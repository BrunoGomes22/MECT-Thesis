/* Testbed dashboard client: live topology, component controls, charts, log
   viewer and tasks, fed by server-sent events. Plain DOM, no framework. */
"use strict";

const SVGNS = "http://www.w3.org/2000/svg";
const $ = (sel, root = document) => root.querySelector(sel);

function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) {
    if (c == null || c === false) continue;
    e.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return e;
}

function icon(id, extra) {
  const s = document.createElementNS(SVGNS, "svg");
  s.setAttribute("class", "icon" + (extra ? " " + extra : ""));
  s.setAttribute("aria-hidden", "true");
  const u = document.createElementNS(SVGNS, "use");
  u.setAttribute("href", "#" + id);
  s.append(u);
  return s;
}

const _nf = {};
function fmt(v, d = 1) {
  if (v == null || !isFinite(v)) return "—";
  _nf[d] = _nf[d] || new Intl.NumberFormat("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d });
  return _nf[d].format(v);
}

function fmtDur(s) {
  if (s == null) return "—";
  s = Math.max(0, Math.floor(s));
  const d = Math.floor(s / 86400), hh = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return `${d} d ${hh} h`;
  if (hh) return `${hh} h ${m} min`;
  if (m) return `${m} min ${s % 60} s`;
  return `${s % 60} s`;
}

function fmtBytes(b) {
  if (b == null) return "—";
  if (b >= 1e9) return fmt(b / 1e9, 1) + " GB";
  if (b >= 1e6) return fmt(b / 1e6, 0) + " MB";
  return fmt(b / 1e3, 0) + " kB";
}

/* ------------------------------------------------------------------ API */

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

async function post(url, body) {
  let r, data = {};
  try {
    r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Testbed-Dashboard": "1" },
      body: JSON.stringify(body),
    });
    data = await r.json();
  } catch (e) {
    toast("critical", "No answer from the dashboard: " + e.message);
    return false;
  }
  if (!r.ok || !data.ok) {
    toast("critical", data.msg || data.error || `Error ${r.status}`);
    return false;
  }
  return true;
}

/* ---------------------------------------------------------------- state */

const S = {
  cfg: null,
  state: null,
  byId: {},
  samples: [],
  range: 300,
  paused: false,
  view: "live",
  sliceNames: { 0: "low", 1: "medium", 2: "high" },
  cards: new Map(),
  charts: {},
  sliceCharts: new Map(),
  ovCharts: {},
  selected: null,
};

const HEALTH = {
  good: { label: "Healthy", icon: "i-check" },
  warning: { label: "Degraded", icon: "i-alert" },
  critical: { label: "Failing", icon: "i-x" },
  pending: { label: "Starting", icon: "i-clock" },
  neutral: { label: "Stopped", icon: "i-stop" },
};

const ROLE_NAME = { core: "5G core", ric: "near-RT RIC", cu: "CU", du: "DU", gnb: "gNB", ue: "UE", xapp: "xApp", other: "" };

function statusOf(c) {
  if (c.busy) return { cls: "pending", label: c.busy.replace(/^.*?: /, ""), icon: "i-spinner", spin: true };
  if (c.state === "stopping") return { cls: "pending", label: "Stopping", icon: "i-spinner", spin: true };
  if (c.state === "stopped") return { cls: "neutral", label: "Stopped", icon: "i-stop" };
  if (c.state === "failed") {
    const code = c.unit && c.unit.exit && c.unit.exit !== "0" ? ` (exit ${c.unit.exit})` : "";
    return { cls: "critical", label: "Failed" + code, icon: "i-x" };
  }
  if (c.state === "partial") return { cls: "critical", label: "Partly running", icon: "i-x" };
  if (c.state === "unknown") return { cls: "warning", label: "Unknown", icon: "i-alert" };
  const hm = HEALTH[c.health] || HEALTH.good;
  return { cls: c.health, label: hm.label, icon: hm.icon };
}

function badge(st) {
  return h("span", { class: "badge " + st.cls }, icon(st.icon, st.spin ? "spin" : ""), st.label);
}

function isRunning(c) {
  return ["running", "external", "partial", "unknown"].includes(c.state);
}

/* --------------------------------------------------------------- toasts */

function toast(level, msg) {
  const lv = ["good", "warning", "critical"].includes(level) ? level : "pending";
  const ic = { good: "i-check", warning: "i-alert", critical: "i-x", pending: "i-clock" }[lv];
  const prefix = { good: "OK: ", warning: "Warning: ", critical: "Error: ", pending: "" }[lv];
  const close = h("button", { class: "close", type: "button", "aria-label": "Close" }, "×");
  const t = h("div", { class: "toast " + lv, role: lv === "critical" ? "alert" : "status" },
    icon(ic), h("div", null, h("span", { class: "muted" }, prefix), msg), close);
  close.addEventListener("click", () => t.remove());
  $("#toasts").append(t);
  setTimeout(() => t.remove(), lv === "critical" ? 15000 : 7000);
  while ($("#toasts").children.length > 5) $("#toasts").firstChild.remove();
}

/* --------------------------------------------------------------- dialog */

function paramFields(params, values) {
  return params.map((p) => {
    const id = "f-" + p.name;
    const val = values && values[p.name] != null ? values[p.name] : p.default;
    if (p.type === "flag") {
      return h("label", { class: "field check" },
        h("input", { type: "checkbox", id, "data-name": p.name, checked: val === "1" || val === true }), p.label);
    }
    let input;
    if (p.type === "choice") {
      input = h("select", { id, "data-name": p.name }, p.choices.map((c) => h("option", { value: c, selected: c === val }, c)));
    } else {
      input = h("input", { type: "text", id, "data-name": p.name, value: val || "", spellcheck: "false", autocomplete: "off" });
    }
    return h("label", { class: "field", for: id }, p.label, input, p.help ? h("span", { class: "note" }, p.help) : null);
  });
}

function readFields(root) {
  const out = {};
  for (const e of root.querySelectorAll("[data-name]")) {
    out[e.dataset.name] = e.type === "checkbox" ? (e.checked ? "1" : "") : e.value.trim();
  }
  return out;
}

function ask({ title, text, params, ok = "Confirm", danger = false }) {
  const dlg = $("#dialog");
  $("#dlg-title").textContent = title;
  $("#dlg-text").textContent = text || "";
  $("#dlg-text").hidden = !text;
  const fields = $("#dlg-fields");
  fields.replaceChildren(...paramFields(params || []));
  fields.hidden = !(params && params.length);
  const okBtn = $("#dlg-ok");
  okBtn.textContent = ok;
  okBtn.classList.toggle("danger", danger);
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => {
      resolve(dlg.returnValue === "ok" ? readFields(fields) : null);
    }, { once: true });
    dlg.returnValue = "";
    dlg.showModal();
  });
}

/* ------------------------------------------------------------ top bar */

function renderSummary(state) {
  const counts = { good: 0, warning: 0, critical: 0, pending: 0, neutral: 0 };
  for (const c of state.components) counts[statusOf(c).cls] = (counts[statusOf(c).cls] || 0) + 1;
  const items = [
    ["good", "i-check", `${counts.good} healthy`],
    ["warning", "i-alert", `${counts.warning} degraded`],
    ["critical", "i-x", `${counts.critical} failing`],
    ["pending", "i-clock", `${counts.pending} changing`],
    ["neutral", "i-stop", `${counts.neutral} stopped`],
  ].filter(([cls]) => counts[cls] > 0 || cls === "good");
  $("#summary").replaceChildren(...items.map(([cls, ic, label]) => h("span", { class: "badge " + cls }, icon(ic), label)));
  $("#orch").replaceChildren(...(state.orchestration ? [icon("i-spinner", "spin"), state.orchestration] : []));
  const busy = !!state.orchestration;
  $("#btn-start-all").disabled = busy;
  $("#btn-stop-ran").disabled = busy;
  $("#host").textContent = state.host || "";
}

function setConn(live) {
  const c = $("#conn");
  c.className = "conn " + (live ? "live" : "down");
  c.lastElementChild.textContent = live ? "live" : "disconnected, retrying…";
}

/* -------------------------------------------------------------- actions */

async function compAction(c, action) {
  if (c.kind === "core") {
    if (action !== "start") {
      const ok = await ask({
        title: action === "stop" ? "Stop the 5G core?" : "Restart the 5G core?",
        text: `${S.cfg && S.cfg.core_kind === "systemd" ? (action === "stop" ? "systemctl stop" : "systemctl stop + start") + " of the core services" : "docker compose " + (action === "stop" ? "down" : "down + up -d")}. The UEs lose their sessions and the CU loses its NG link (the CU/DU/UEs usually need a restart afterwards).`,
        ok: action === "stop" ? "Stop core" : "Restart core", danger: true,
      });
      if (!ok) return;
    }
  } else if (c.kind === "process") {
    if ((action === "start" || action === "restart") && c.params && c.params.length) {
      const params = await ask({ title: `${action === "start" ? "Start" : "Restart"} ${c.name}`, params: c.params, ok: action === "start" ? "Start" : "Restart" });
      if (!params) return;
      return post("/api/component", { id: c.id, action, params });
    }
    if (action === "stop" || action === "restart") {
      const ext = c.state === "external";
      const pids = c.procs.filter((p) => p.main).map((p) => p.pid).join(", ");
      const ok = await ask({
        title: `${action === "stop" ? "Stop" : "Restart"} ${c.name}?`,
        text: (ext ? `This process was started outside the dashboard (PID ${pids}). ` : "")
          + `It gets SIGINT, like Ctrl+C${action === "restart" ? ", and is then started by the dashboard" : ""}.`,
        ok: action === "stop" ? "Stop" : "Restart", danger: true,
      });
      if (!ok) return;
    }
  }
  return post("/api/component", { id: c.id, action });
}

function openLogFor(source) {
  location.hash = "#logs";
  setTimeout(() => selectLog(source), 0);
}

function actionButtons(c, small = true) {
  const sz = small ? "btn small" : "btn";
  const btns = [];
  const dis = !!c.busy;
  const running = isRunning(c);
  if (c.kind === "exporter") {
    btns.push(h("button", { class: sz, type: "button", disabled: dis || c.state === "stopped", onclick: () => compAction(c, "restart") }, icon("i-restart"), "Restart"));
  } else {
    btns.push(h("button", { class: sz, type: "button", disabled: dis || (running && c.kind !== "core") || c.state === "stopping", onclick: () => compAction(c, "start") }, icon("i-play"), "Start"));
    btns.push(h("button", { class: sz + " danger", type: "button", disabled: dis || !(running || c.state === "stopping"), onclick: () => compAction(c, "stop") }, icon("i-square"), "Stop"));
    btns.push(h("button", { class: sz, type: "button", disabled: dis || !running, onclick: () => compAction(c, "restart") }, icon("i-restart"), "Restart"));
  }
  const logLabel = c.kind === "core" ? "Core actions log (docker compose)" : "Logs of " + c.name;
  btns.push(h("button", { class: sz + " ghost push", type: "button", title: logLabel, "aria-label": logLabel, onclick: () => openLogFor(c.log) }, icon("i-log"), "Logs"));
  return btns;
}

/* ------------------------------------------------------- live: controls */

function stripOrder(comps) {
  const exp = comps.filter((c) => c.id === "exporter");
  return comps.filter((c) => c.id !== "exporter").concat(exp);
}

async function powerAction(c) {
  if (c.kind === "exporter") return compAction(c, "restart");
  return compAction(c, isRunning(c) || c.state === "stopping" ? "stop" : "start");
}

function renderControlStrip() {
  const box = $("#comp-buttons");
  const comps = stripOrder(S.state.components);
  const sig = JSON.stringify(comps.map((c) => [c.id, c.name, statusOf(c), c.state, !!c.busy, S.selected === c.id]));
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  const focused = document.activeElement && box.contains(document.activeElement) ? document.activeElement.dataset.key : null;
  box.replaceChildren(...comps.map((c) => {
    const st = statusOf(c);
    const running = isRunning(c);
    const powerLabel = c.kind === "exporter" ? `Restart ${c.name}` : `${running ? "Stop" : "Start"} ${c.name}`;
    return h("div", { class: `cbtn h-${st.cls}` + (S.selected === c.id ? " selected" : "") },
      h("button", { class: "cbtn-main", type: "button", "data-key": "m-" + c.id, "aria-pressed": String(S.selected === c.id),
        title: `${c.name}: ${st.label}. Click to show it in the diagram.`, onclick: () => selectNode(c.id) },
        icon(st.icon, st.spin ? "spin" : ""), h("span", { class: "cname", text: c.name }), h("span", { class: "cstate", text: st.label })),
      h("button", { class: "cbtn-power" + (running ? " on" : ""), type: "button", "data-key": "p-" + c.id,
        title: powerLabel, "aria-label": powerLabel, disabled: !!c.busy || c.state === "stopping",
        onclick: () => powerAction(c) }, icon(c.kind === "exporter" ? "i-restart" : "i-power")));
  }));
  if (focused) {
    const again = box.querySelector(`[data-key="${focused}"]`);
    if (again) again.focus();
  }
}

/* -------------------------------------------------------- live: diagram */

let TOPO = null;

function selectNode(id) {
  S.selected = id;
  if (TOPO) TOPO.select(id);
  renderControlStrip();
  renderDetail(true);
}

function renderLegend() {
  const box = $("#topo-legend");
  if (box.childElementCount) return;
  const items = [["good", "i-check", "OK"], ["warning", "i-alert", "Degraded"], ["critical", "i-x", "Down"],
    ["pending", "i-clock", "Waiting"], ["unknown", "i-help", "Unknown"], ["off", "i-dash", "Not running"]];
  box.replaceChildren(...items.map(([cls, ic, label]) =>
    h("span", { class: "lg-item s-" + cls }, h("span", { class: "lg-line" }), icon(ic), label)));
}

function renderLive() {
  if (!S.state) return;
  renderLegend();
  renderControlStrip();
  if (!S.state.topology) {
    $("#diagram").replaceChildren(h("div", { class: "log-empty", text: "This dashboard service is still running the old code: run  sudo systemctl restart srsran-dashboard  to get the live topology." }));
    renderDetail();
    return;
  }
  if (!TOPO) TOPO = new Topology($("#diagram"), { onSelect: selectNode, statusOf, fmt });
  TOPO.render(S.state.topology, S.byId);
  renderDetail();
}

function linkLine(l) {
  const st = TOPO_STATUS[l.status] || TOPO_STATUS.unknown;
  const other = l.a === S.selected ? l.b : l.a;
  const name = (S.byId[other] || {}).name || other;
  return h("li", { class: "s-" + l.status }, icon(st.icon),
    h("span", { class: "name" }, `${l.label} ↔ ${name}`),
    h("span", { class: "detail" }, st.label + (l.detail ? " — " + l.detail : "")));
}

function kv(rows) {
  return h("dl", { class: "kv" }, rows.filter(Boolean).flatMap(([k, v]) => [h("dt", { text: k }), h("dd", { text: v })]));
}

function detailExtras(c, topo) {
  const out = [];
  const links = topo ? topo.links.filter((l) => l.a === c.id || l.b === c.id) : [];
  if (c.role === "ue") {
    const r = links.find((l) => l.kind === "radio" && l.ue);
    if (r) {
      const u = r.ue;
      out.push(h("h4", { text: "Radio (E2 MAC/RLC)" }), kv([
        ["RNTI", "0x" + r.rnti],
        ["Slice", u.slice != null ? `${u.slice} · ${S.sliceNames[u.slice] || "?"}` : "—"],
        ["DL / UL", `${fmt(u.dl_mbps, 2)} / ${fmt(u.ul_mbps, 2)} Mbit/s`],
        ["PRB per slot DL / UL", `${fmt(u.dl_prb, 1)} / ${fmt(u.ul_prb, 1)}`],
        ["MCS DL / UL", `${fmt(u.dl_mcs, 0)} / ${fmt(u.ul_mcs, 0)}`],
        ["SINR · RSRP", `${fmt(u.sinr, 1)} dB · ${fmt(u.rsrp, 0)} dBm`],
        ["BLER DL / UL", `${fmt(u.dl_bler == null ? null : u.dl_bler * 100, 1)} / ${fmt(u.ul_bler == null ? null : u.ul_bler * 100, 1)} %`],
        ["RLC buffer · delay", `${fmt(u.rlc_buf_kb, 1)} kB · ${fmt(u.rlc_delay_ms, 1)} ms`],
      ]));
    } else if (isRunning(c)) {
      out.push(h("p", { class: "note", text: "No metrics matched to this UE yet (matched by its slice)." }));
    }
    out.push(kv([["IMSI", c.imsi || "—"], ["Namespace", c.netns || "—"], ["SST", c.sst != null ? String(c.sst) : "—"],
      ["Slice", c.slice != null ? `${c.slice} · ${S.sliceNames[c.slice] || "?"}` : "—"]]));
  } else if (c.kind === "core") {
    out.push(h("h4", { text: S.cfg && S.cfg.core_kind === "systemd" ? "Services" : "Containers" }), h("div", { class: "mini-containers" }, c.containers.map((ct) => {
      const ok = ct.state === "running" && (ct.health === "healthy" || ct.health === "");
      const bad = ct.state !== "running" || ct.health === "unhealthy";
      return h("span", { class: "badge plain " + (ok ? "good" : bad ? "critical" : "pending"), title: ct.status },
        icon(ok ? "i-check" : bad ? "i-x" : "i-clock"), ct.name);
    })));
    if (c.amf) {
      out.push(kv([
        ["gNBs at the AMF", c.amf.gnbs.map((g) => `${g.name} (${g.status})`).join(", ") || "none"],
        ["UEs at the AMF", c.amf.ues.map((u) => `${u.imsi} ${u.state}`).join(", ") || "none"],
      ]));
    }
  } else if (c.role === "ric") {
    const exp = S.byId.exporter;
    const xapps = S.state.components.filter((x) => x.role === "xapp");
    out.push(kv([
      ["E2 nodes", exp && exp.nodes && exp.nodes.length ? exp.nodes.map((n) => n.id || n.type_name).join(", ") : "unknown (metrics exporter not running)"],
      ["xApps", xapps.map((x) => `${x.name}: ${statusOf(x).label}`).join(" · ")],
    ]));
  } else if ((c.role === "du" || c.role === "gnb") && S.samples.length) {
    const last = S.samples[S.samples.length - 1];
    out.push(kv([
      ["UEs with MAC reports", String(last.tot.ues)],
      ["Cell DL / UL", `${fmt(last.tot.dl_mbps, 1)} / ${fmt(last.tot.ul_mbps, 2)} Mbit/s`],
    ]));
  }
  if (links.length) out.push(h("h4", { text: "Interfaces" }), h("ul", { class: "checks links-list" }, links.map(linkLine)));
  return out;
}

function renderDetail(force) {
  const box = $("#detail");
  const c = S.selected && S.byId[S.selected];
  if (!c) {
    if (box.dataset.sig !== "none") {
      box.dataset.sig = "none";
      box.replaceChildren(
        h("h3", { text: "Details" }),
        h("p", { class: "note", text: "Click a component in the diagram, or one of the buttons above it, to see its health checks and interfaces and to start, stop or restart it." }),
        h("p", { class: "note", text: "Boxes show the component state; lines show the interfaces between them (NG to the core, F1 between CU and DU, E2 to the RIC, E42 between xApps and the RIC, radio to the UEs, with live DL/UL throughput). Moving dots mean live traffic or E2 reports." }));
    }
    return;
  }
  const st = statusOf(c);
  // header + buttons: rebuilt only when they change, so keyboard focus survives
  const headSig = JSON.stringify([c.id, c.name, st, c.state, !!c.busy, c.description]);
  if (force || box.dataset.sig !== headSig) {
    box.dataset.sig = headSig;
    box.replaceChildren(...[
      h("div", { class: "detail-head" },
        h("div", null, h("div", { class: "role-tag", text: ROLE_NAME[c.role] || "" }), h("h3", { text: c.name })),
        badge(st)),
      c.description ? h("p", { class: "desc", text: c.description }) : null,
      c.state === "external" ? h("p", { class: "note", text: "Started outside the dashboard (in a terminal): its log is in that terminal." }) : null,
      h("div", { class: "card-actions" }, actionButtons(c)),
      h("div", { class: "detail-body" }),
    ].filter(Boolean));
  }
  const body = box.querySelector(".detail-body");
  const meta = [];
  if (c.kind === "process" && isRunning(c)) {
    const main = c.procs.filter((p) => p.main);
    meta.push(["PID", main.map((p) => p.pid).join(", ") || "—"], ["Up", fmtDur(c.uptime)],
      ["CPU · RAM", `${fmt(c.cpu, 0)} % · ${fmtBytes(c.rss)}`]);
    if (main[0] && main[0].user) meta.push(["User", main[0].user]);
  } else if (c.kind === "exporter") {
    meta.push(["PID", c.pid ? String(c.pid) : "—"], ["Up", fmtDur(c.uptime)], ["Restarts", String(c.restarts)]);
    if (c.detail) meta.push(["Note", c.detail]);
  } else if (c.kind === "process" && c.state === "failed" && c.unit) {
    meta.push(["Exited", `${fmtDur(c.unit.exited_ago)} ago`], ["Result", c.unit.result || "?"]);
  }
  const topo = S.state.topology;
  body.replaceChildren(...[
    meta.length ? kv(meta) : null,
    c.checks.length ? h("h4", { text: "Health checks" }) : null,
    c.checks.length ? checksList(c.checks) : null,
    ...detailExtras(c, topo),
  ].filter(Boolean));
}

/* ------------------------------------------------- components (cards) */

const GROUP_ORDER = ["Core", "RIC", "RAN", "UEs", "xApps"];

function groupBox(name) {
  let box = document.getElementById("grp-" + name);
  if (!box) {
    box = h("section", { class: "group", id: "grp-" + name }, h("h2", { text: name }), h("div", { class: "cards" }));
    const groups = $("#groups");
    const order = (n) => { const i = GROUP_ORDER.indexOf(n); return i < 0 ? 99 : i; };
    const after = [...groups.children].find((g) => order(g.id.slice(4)) > order(name));
    groups.insertBefore(box, after || null);
  }
  return box.querySelector(".cards");
}

function checksList(checks) {
  return h("ul", { class: "checks" }, checks.map((ch) => {
    const cls = ch.ok === true ? "ok" : ch.ok === false ? "bad" : "unk";
    const ic = ch.ok === true ? "i-check" : ch.ok === false ? "i-x" : "i-dash";
    return h("li", { class: cls }, icon(ic), h("span", { class: "name" }, ch.name), h("span", { class: "detail" }, ch.detail || ""));
  }));
}

function cardFor(c) {
  let card = S.cards.get(c.id);
  if (!card) {
    const el = h("article", { class: "card", "aria-label": c.name });
    card = { el, sig: {} };
    S.cards.set(c.id, card);
    groupBox(c.group).append(el);
  }
  return card;
}

function part(card, key, data, build) {
  const sig = JSON.stringify(data);
  if (card.sig[key] === sig) return null;
  card.sig[key] = sig;
  return build();
}

function renderCard(c) {
  const card = cardFor(c);
  const el = card.el;
  const st = statusOf(c);
  el.className = "card health-" + st.cls;

  const head = part(card, "head", [c.name, c.description, st, c.state === "external"], () =>
    h("div", { class: "card-head" },
      h("div", { class: "card-title" }, h("h3", { text: c.name }), c.description ? h("div", { class: "desc", text: c.description }) : null),
      h("div", { style: "display:flex;flex-direction:column;align-items:flex-end;gap:4px" },
        badge(st), c.state === "external" ? h("span", { class: "tag", title: "Started in a terminal, not by the dashboard: no log file here" }, "outside dashboard") : null)));

  const metaItems = [];
  if (c.kind === "process" && (c.state === "running" || c.state === "external")) {
    const main = c.procs.filter((p) => p.main);
    metaItems.push(`PID ${main.map((p) => p.pid).join(", ") || "—"}`, `up ${fmtDur(c.uptime)}`,
      `CPU ${fmt(c.cpu, 0)} %`, `RAM ${fmtBytes(c.rss)}`);
    if (main[0] && main[0].user && main[0].user !== "root") metaItems.push(`user ${main[0].user}`);
  } else if (c.kind === "exporter" && c.pid) {
    metaItems.push(`PID ${c.pid}`, `up ${fmtDur(c.uptime)}`, `restarts ${c.restarts}`);
    if (c.detail) metaItems.push(c.detail);
  } else if (c.kind === "exporter" && c.detail) {
    metaItems.push(c.detail);
  } else if (c.kind === "process" && c.state === "failed" && c.unit) {
    metaItems.push(`exited ${fmtDur(c.unit.exited_ago)} ago`, `result ${c.unit.result || "?"}`);
  }
  const meta = part(card, "meta", metaItems, () =>
    metaItems.length ? h("div", { class: "card-meta" }, metaItems.map((m) => h("span", null, m))) : h("div", { hidden: true }));

  const checks = part(card, "checks", c.checks, () => (c.checks.length ? checksList(c.checks) : h("div", { hidden: true })));

  let cont = null;
  if (c.kind === "core") {
    cont = part(card, "containers", [c.containers, !!c.busy], () =>
      h("div", { class: "containers" }, c.containers.map((ct) => {
        const ok = ct.state === "running" && (ct.health === "healthy" || ct.health === "");
        const bad = ct.state !== "running" || ct.health === "unhealthy";
        const label = ct.state === "running" ? (ct.health || "running") : ct.state;
        return h("div", { class: "row" },
          h("span", { class: "cname mono", title: ct.status }, ct.name),
          h("span", { class: "badge plain " + (ok ? "good" : bad ? "critical" : "pending") }, icon(ok ? "i-check" : bad ? "i-x" : "i-clock"), label),
          h("span", null,
            h("button", { class: "btn small ghost", type: "button", title: "Logs of " + ct.name, "aria-label": "Logs of " + ct.name, onclick: () => openLogFor("docker:" + ct.name) }, icon("i-log")),
            h("button", { class: "btn small ghost", type: "button", title: "Restart " + ct.name, "aria-label": "Restart " + ct.name, disabled: ct.state === "missing" || !!c.busy,
              onclick: async () => {
                const ok = await ask({ title: `Restart ${ct.name}?`, text: "docker restart " + ct.name, ok: "Restart", danger: true });
                if (ok) post("/api/core/container", { name: ct.name });
              } }, icon("i-restart"))));
      })));
  }

  const actions = part(card, "actions", [c.state, !!c.busy, c.kind], () => h("div", { class: "card-actions" }, actionButtons(c)));

  if (!el.firstChild) {
    el.append(head || h("div"), meta || h("div"), checks || h("div"));
    if (c.kind === "core") el.append(cont || h("div"));
    el.append(actions || h("div"));
    return;
  }
  const kids = el.children;
  let i = 0;
  const swap = (node) => { if (node) el.replaceChild(node, kids[i]); i++; };
  swap(head); swap(meta); swap(checks);
  if (c.kind === "core") swap(cont);
  swap(actions);
}

function renderKpis() {
  const state = S.state;
  const last = S.samples[S.samples.length - 1];
  const fresh = last && Date.now() / 1000 - last.t < 5;
  const comps = state ? state.components : [];
  const core = comps.find((c) => c.id === "core");
  const exp = comps.find((c) => c.id === "exporter");
  const running = comps.filter((c) => ["running", "external"].includes(c.state));
  const warn = comps.filter((c) => ["warning", "critical"].includes(statusOf(c).cls));
  const regs = core && core.amf ? core.amf.ues.filter((u) => /REGISTERED/.test(u.state) && !/DEREG/.test(u.state)) : [];
  const nodesCheck = exp && exp.checks.find((ch) => ch.name === "E2 nodes");
  const tiles = [
    ["Cell DL throughput", fresh ? fmt(last.tot.dl_mbps, 1) : "—", "Mbit/s", fresh ? `${last.tot.ues} UE(s) with MAC reports` : "no recent MAC reports"],
    ["Cell UL throughput", fresh ? fmt(last.tot.ul_mbps, 2) : "—", "Mbit/s", "MAC, sum over the UEs"],
    ["UEs registered at the AMF", core && core.amf ? String(regs.length) : "—", "", regs.map((u) => u.imsi).join(", ") || "none"],
    ["Components running", `${running.length}`, `of ${comps.length}`, warn.length ? `${warn.length} degraded or failing` : "no warnings"],
    ["E2 nodes at the RIC", nodesCheck && nodesCheck.ok ? String(nodesCheck.detail.split(",").length) : "—", "", nodesCheck ? nodesCheck.detail : "exporter not running"],
  ];
  $("#kpis").replaceChildren(...tiles.map(([label, value, unit, sub]) =>
    h("div", { class: "tile" }, h("div", { class: "label", text: label }),
      h("div", { class: "value" }, value, unit ? h("small", null, unit) : null),
      h("div", { class: "sub", text: sub, title: sub }))));
}

function onState(state) {
  S.state = state;
  S.byId = Object.fromEntries(state.components.map((c) => [c.id, c]));
  if (state.slice_names) S.sliceNames = state.slice_names;
  renderSummary(state);
  const seen = new Set();
  for (const c of state.components) { renderCard(c); seen.add(c.id); }
  for (const [id, card] of S.cards) if (!seen.has(id)) { card.el.remove(); S.cards.delete(id); }
  renderKpis();
  if (S.view === "live") renderLive();
  if (S.view === "tasks") renderTasks();
  renderMetricsNote();
}

/* -------------------------------------------------------------- metrics */

const UE_CHARTS = [
  { key: "dl_mbps", title: "DL throughput", unit: "Mbit/s (MAC)", d: 2 },
  { key: "ul_mbps", title: "UL throughput", unit: "Mbit/s (MAC)", d: 2 },
  { key: "dl_prb", title: "DL PRBs", unit: "PRBs per DL slot (mean)", d: 1 },
  { key: "ul_prb", title: "UL PRBs", unit: "PRBs per UL slot (mean)", d: 1 },
  { key: "dl_mcs", title: "DL MCS", unit: "index", d: 0 },
  { key: "ul_mcs", title: "UL MCS", unit: "index", d: 0 },
  { key: "sinr", title: "SINR", unit: "dB (SSB-SINR report)", d: 1, zero: false },
  { key: "rsrp", title: "RSRP", unit: "dBm", d: 0, zero: false },
  { key: "pusch_snr", title: "PUSCH SNR", unit: "dB", d: 1, zero: false },
  { key: "dl_bler", title: "DL BLER", unit: "%", d: 1, scale: 100 },
  { key: "ul_bler", title: "UL BLER", unit: "%", d: 1, scale: 100 },
  { key: "rlc_buf_kb", title: "DL RLC buffer", unit: "kB queued", d: 1 },
  { key: "rlc_delay_ms", title: "DL RLC delay", unit: "ms (mean sojourn)", d: 1 },
  { key: "rlc_drop_kbps", title: "DL RLC drops", unit: "kbit/s", d: 1 },
];

function ueLabel(rnti, rec) {
  const sl = rec.slice != null ? `slice ${rec.slice} (${S.sliceNames[rec.slice] || "?"})` : "no slice";
  return `0x${rnti} · ${sl}`;
}

function windowSamples(range) {
  const t1 = (S.samples.length ? S.samples[S.samples.length - 1].t : Date.now() / 1000);
  const t0 = t1 - range;
  let i = S.samples.length;
  while (i > 0 && S.samples[i - 1].t >= t0) i--;
  return { samples: S.samples.slice(i), t0, t1 };
}

function ueSeries(win, key, scale = 1) {
  const meta = new Map();
  for (const s of win.samples) for (const [r, rec] of Object.entries(s.ues)) meta.set(r, rec);
  return [...meta.entries()].sort((a, b) => a[1].idx - b[1].idx).map(([r, rec]) => ({
    key: r,
    label: ueLabel(r, rec),
    color: `--series-${(rec.idx % 8) + 1}`,
    points: win.samples.map((s) => { const v = s.ues[r] && s.ues[r][key]; return v == null ? null : v * scale; }),
  }));
}

function ensureUeCharts() {
  if (S.charts.dl_mbps) return;
  const grid = $("#ue-charts");
  for (const def of UE_CHARTS) {
    S.charts[def.key] = new LineChart(grid, {
      title: def.title, unit: def.unit, zero: def.zero !== false, height: 170,
      fmt: (v) => fmt(v, def.d),
    });
  }
}

function ensureOverviewCharts() {
  if (S.ovCharts.dl) return;
  const box = $("#ov-charts");
  S.ovCharts.dl = new LineChart(box, { title: "DL throughput per UE", unit: "Mbit/s · last 5 min", height: 190, fmt: (v) => fmt(v, 2) });
  S.ovCharts.ul = new LineChart(box, { title: "UL throughput per UE", unit: "Mbit/s · last 5 min", height: 190, fmt: (v) => fmt(v, 2) });
}

function setSingleUnit(chart, base, series) {
  chart.setUnit(series.length === 1 ? `${base} · ${series[0].label}` : base);
}

function updateOverviewCharts() {
  ensureOverviewCharts();
  const win = windowSamples(300);
  const times = win.samples.map((s) => s.t);
  for (const [k, key] of [["dl", "dl_mbps"], ["ul", "ul_mbps"]]) {
    const series = ueSeries(win, key);
    setSingleUnit(S.ovCharts[k], "Mbit/s · last 5 min", series);
    S.ovCharts[k].setData(times, series, win.t0, win.t1);
  }
}

function sliceChart(row, key, title, unit, d) {
  const id = row + ":" + key;
  if (!S.sliceCharts.has(id)) {
    const grid = document.getElementById("slice-" + row);
    S.sliceCharts.set(id, new LineChart(grid, { title, unit, height: 140, fmt: (v) => fmt(v, d) }));
  }
  return S.sliceCharts.get(id);
}

function ensureSliceRows() {
  if (document.getElementById("slice-dl")) return;
  const box = $("#slice-charts");
  box.className = "";
  box.append(
    h("p", { class: "note", text: "One series per chart, same vertical scale along each row, to compare the slices." }),
    h("h3", { class: "chart-unit", style: "margin:8px 0 6px;font-size:13px", text: "DL throughput (Mbit/s)" }),
    h("div", { class: "slice-grid", id: "slice-dl" }),
    h("h3", { class: "chart-unit", style: "margin:16px 0 6px;font-size:13px", text: "DL PRBs per slot (mean)" }),
    h("div", { class: "slice-grid", id: "slice-prb" }));
}

function updateSliceCharts(win, times) {
  ensureSliceRows();
  const keys = new Set();
  for (const s of win.samples) for (const k of Object.keys(s.slices)) keys.add(k);
  const order = [...keys].sort((a, b) => (a === "none") - (b === "none") || a.localeCompare(b));
  if (!order.length) {
    if (!$("#slice-empty")) $("#slice-charts").append(h("div", { class: "empty-state", id: "slice-empty", text: "No UEs with MAC reports in the selected window." }));
    return;
  }
  const empty = $("#slice-empty");
  if (empty) empty.remove();
  for (const [row, key, unitBase, d] of [["dl", "dl_mbps", "Mbit/s", 2], ["prb", "dl_prb", "PRB/slot", 1]]) {
    let maxV = 0;
    const per = order.map((k) => {
      const pts = win.samples.map((s) => (s.slices[k] ? s.slices[k][key] : null));
      for (const v of pts) if (v != null && v > maxV) maxV = v;
      return [k, pts];
    });
    for (const [k, pts] of per) {
      const name = k === "none" ? "No slice" : `Slice ${k} · ${S.sliceNames[k] || "?"}`;
      const ch = sliceChart(row, k, name, unitBase, d);
      ch.setData(times, [{ key: k, label: name, color: "--series-1", points: pts }], win.t0, win.t1, { yMax: maxV });
    }
  }
}

function updateUeTable(last) {
  const table = $("#ue-table");
  const cols = [
    ["UE", null], ["Slice", null], ["SST", null],
    ["DL Mbit/s", (r) => fmt(r.dl_mbps, 2)], ["UL Mbit/s", (r) => fmt(r.ul_mbps, 2)],
    ["DL PRB/slot", (r) => fmt(r.dl_prb, 1)], ["UL PRB/slot", (r) => fmt(r.ul_prb, 1)],
    ["DL MCS", (r) => fmt(r.dl_mcs, 0)], ["UL MCS", (r) => fmt(r.ul_mcs, 0)],
    ["CQI", (r) => (r.cqi == null ? "—" : r.cqi + (r.cqi_est ? " (est.)" : ""))],
    ["SINR dB", (r) => fmt(r.sinr, 1)], ["RSRP dBm", (r) => fmt(r.rsrp, 0)],
    ["PUSCH SNR dB", (r) => fmt(r.pusch_snr, 1)], ["PUCCH SNR dB", (r) => fmt(r.pucch_snr, 1)],
    ["DL BLER %", (r) => fmt(r.dl_bler == null ? null : r.dl_bler * 100, 1)],
    ["UL BLER %", (r) => fmt(r.ul_bler == null ? null : r.ul_bler * 100, 1)],
    ["RLC buffer kB", (r) => fmt(r.rlc_buf_kb, 1)], ["RLC delay ms", (r) => fmt(r.rlc_delay_ms, 1)],
    ["Drops kbit/s", (r) => fmt(r.rlc_drop_kbps, 1)],
    ["BSR B", (r) => fmt(r.bsr, 0)], ["PHR dB", (r) => fmt(r.phr, 0)],
    ["DL total MB", (r) => fmt(r.dl_total_mb, 1)], ["UL total MB", (r) => fmt(r.ul_total_mb, 1)],
  ];
  const rows = last ? Object.entries(last.ues).sort((a, b) =>
    ((a[1].slice ?? 99) - (b[1].slice ?? 99)) || a[1].idx - b[1].idx) : [];
  const thead = h("thead", null, h("tr", null, cols.map(([name], i) => h("th", { class: i < 3 ? "left" : null, scope: "col" }, name))));
  const tbody = h("tbody", null, rows.map(([rnti, r]) => h("tr", null,
    h("td", { class: "left" }, h("span", { class: "line-key", style: `background:var(--series-${(r.idx % 8) + 1});margin-right:8px;vertical-align:middle` }), "0x" + rnti),
    h("td", { class: "left" }, r.slice != null ? `${r.slice} · ${S.sliceNames[r.slice] || "?"}` : "—"),
    h("td", { class: "left" }, r.sst != null ? String(r.sst) : "—"),
    cols.slice(3).map(([, f]) => { const v = f(r); return h("td", { class: v === "—" ? "na" : null }, v); }))));
  table.replaceChildren(thead, tbody);
  if (!rows.length) table.append(h("tbody", null, h("tr", null, h("td", { colspan: cols.length, class: "left na" }, "No UEs with MAC reports."))));
  $("#ue-table-note").textContent = "PRB per slot = PRBs scheduled per second ÷ DL (or UL) slots per second"
    + (S.cfg ? ` (${S.cfg.metrics.dl_slots_per_s} DL, ${S.cfg.metrics.ul_slots_per_s} UL; config.yaml)` : "")
    + ". " + (S.cfg && S.cfg.metrics.cqi_note ? S.cfg.metrics.cqi_note : "");
}

function renderMetricsNote() {
  const exp = S.byId.exporter;
  const el = $("#metrics-note");
  if (!exp) { el.textContent = ""; return; }
  const last = S.samples[S.samples.length - 1];
  const age = last ? Date.now() / 1000 - last.t : null;
  let txt;
  const nm = exp.name || "E2 exporter";
  if (exp.state === "stopped") txt = nm + " stopped: " + (exp.detail || "the nearRT-RIC is not running") + ".";
  else if (exp.state === "failed") txt = nm + " error: " + (exp.detail || "") + ` (see the '${nm}' log).`;
  else if (exp.detail) txt = nm + ": " + exp.detail + ".";
  else txt = `${nm} running · ${last ? Object.keys(last.ues).length : 0} UE(s)` + (age != null && age > 5 ? ` · last sample ${fmtDur(age)} ago` : "");
  el.textContent = txt + (S.paused ? " · Charts paused." : "");
}

function updateMetrics() {
  if (S.view === "live") updateOverviewCharts();
  if (S.view !== "metrics") return;
  ensureUeCharts();
  const win = windowSamples(S.range);
  const times = win.samples.map((s) => s.t);
  for (const def of UE_CHARTS) {
    const series = ueSeries(win, def.key, def.scale || 1);
    setSingleUnit(S.charts[def.key], def.unit, series);
    S.charts[def.key].setData(times, series, win.t0, win.t1);
  }
  updateSliceCharts(win, times);
  updateUeTable(S.samples[S.samples.length - 1]);
}

function onSample(sample) {
  S.samples.push(sample);
  const max = S.cfg ? S.cfg.metrics.history_s / S.cfg.metrics.tick_s + 5 : 2000;
  if (S.samples.length > max) S.samples.splice(0, S.samples.length - max);
  if (!S.paused) updateMetrics();
  renderKpis();
}

async function loadHistory() {
  try {
    const d = await getJSON("/api/metrics/history?seconds=1800");
    S.samples = d.samples || [];
  } catch (e) {
    S.samples = [];
  }
}

/* ----------------------------------------------------------------- logs */

const LOG = { source: null, es: null, lines: [], max: 5000, paused: false, held: [], first: false, sources: [] };

async function loadLogSources() {
  try {
    const d = await getJSON("/api/logs/sources");
    LOG.sources = d.sources;
  } catch (e) {
    return;
  }
  const nav = $("#log-sources");
  const cur = LOG.sources.find((s) => s.id === LOG.source);
  if (cur) $("#log-title").textContent = cur.name;
  const groups = new Map();
  for (const s of LOG.sources) {
    if (!groups.has(s.group)) groups.set(s.group, []);
    groups.get(s.group).push(s);
  }
  nav.replaceChildren(...[...groups.entries()].flatMap(([g, items]) => [
    h("h4", { text: g }),
    ...items.map((s) => h("button", {
      type: "button", class: s.exists ? null : "missing", "data-src": s.id,
      "aria-current": s.id === LOG.source ? "true" : "false",
      title: s.exists ? s.name : s.name + " (no log yet)",
      onclick: () => selectLog(s.id),
    }, icon("i-log"), h("span", { class: "name", text: s.name }))),
  ]));
}

function lineEl(l) {
  const [lvl, text] = l;
  const d = h("div", { class: "ll" + (lvl ? " " + lvl : "") + (text.startsWith("— ") ? " sys" : "") });
  if (lvl) d.append(h("span", { class: "lvl", text: lvl === "E" ? "ERROR" : "WARN" }));
  d.append(text);
  return d;
}

function logMatches(l) {
  const lvl = $("#log-level").value;
  if (lvl === "E" && l[0] !== "E") return false;
  if (lvl === "W" && !l[0]) return false;
  const f = $("#log-filter").value.trim().toLowerCase();
  return !f || l[1].toLowerCase().includes(f);
}

function renderLogAll() {
  if (!LOG.source) return;
  const view = $("#log-view");
  const shown = LOG.lines.filter(logMatches).slice(-3000);
  let emptyText = LOG.lines.length ? "No line matches the filter." : "Empty log (no lines yet).";
  const comp = LOG.source.startsWith("comp:") && S.byId[LOG.source.slice(5)];
  if (!LOG.lines.length && comp && comp.state === "external") {
    emptyText = "This component was started in a terminal, so its log is in that terminal. Start it from the dashboard (Stop + Start) to see it here.";
  }
  view.replaceChildren(...(shown.length ? shown.map(lineEl) : [h("div", { class: "log-empty", text: emptyText })]));
  if ($("#log-follow").checked) view.scrollTop = view.scrollHeight;
  updateLogStatus();
}

function appendLog(lines) {
  LOG.lines.push(...lines);
  if (LOG.lines.length > LOG.max) LOG.lines.splice(0, LOG.lines.length - LOG.max);
  const view = $("#log-view");
  if (view.firstChild && view.firstChild.classList && view.firstChild.classList.contains("log-empty")) view.replaceChildren();
  const frag = document.createDocumentFragment();
  for (const l of lines) if (logMatches(l)) frag.append(lineEl(l));
  view.append(frag);
  while (view.childElementCount > 3000) view.firstChild.remove();
  if ($("#log-follow").checked) view.scrollTop = view.scrollHeight;
  updateLogStatus();
}

function updateLogStatus() {
  const errs = LOG.lines.filter((l) => l[0] === "E").length;
  const warns = LOG.lines.filter((l) => l[0] === "W").length;
  const items = [`${LOG.lines.length} lines in memory (max ${LOG.max})`, `${errs} errors`, `${warns} warnings`];
  if (LOG.paused) items.push(`paused (${LOG.held.length} lines waiting)`);
  $("#log-status").replaceChildren(...items.map((t) => h("span", { text: t })));
}

function selectLog(source) {
  if (LOG.es) { LOG.es.close(); LOG.es = null; }
  LOG.source = source;
  LOG.lines = [];
  LOG.held = [];
  const meta = LOG.sources.find((s) => s.id === source);
  $("#log-title").textContent = meta ? meta.name : source;
  for (const b of document.querySelectorAll("#log-sources button")) b.setAttribute("aria-current", String(b.dataset.src === source));
  $("#log-view").replaceChildren(h("div", { class: "log-empty", text: "Loading…" }));
  const es = new EventSource(`/api/logs/stream?source=${encodeURIComponent(source)}&lines=1000`);
  LOG.es = es;
  es.addEventListener("open", () => { LOG.first = true; });
  es.addEventListener("lines", (ev) => {
    const d = JSON.parse(ev.data);
    if (LOG.first) { LOG.first = false; LOG.lines = []; LOG.held = []; LOG.lines.push(...d.lines); renderLogAll(); return; }
    if (LOG.paused) { LOG.held.push(...d.lines); if (LOG.held.length > LOG.max) LOG.held.splice(0, LOG.held.length - LOG.max); updateLogStatus(); return; }
    appendLog(d.lines);
  });
  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) $("#log-status").replaceChildren(h("span", { text: "Source unavailable." }));
  };
}

function setupLogs() {
  $("#log-filter").addEventListener("input", renderLogAll);
  $("#log-level").addEventListener("change", renderLogAll);
  $("#log-clear").addEventListener("click", () => { LOG.lines = []; renderLogAll(); });
  $("#log-pause").addEventListener("click", (e) => {
    LOG.paused = !LOG.paused;
    e.currentTarget.setAttribute("aria-pressed", String(LOG.paused));
    e.currentTarget.textContent = LOG.paused ? "Resume" : "Pause";
    if (!LOG.paused && LOG.held.length) { const held = LOG.held; LOG.held = []; appendLog(held); }
    updateLogStatus();
  });
  $("#log-download").addEventListener("click", () => {
    if (!LOG.source) return;
    const blob = new Blob([LOG.lines.map((l) => l[1]).join("\n") + "\n"], { type: "text/plain" });
    const a = h("a", { href: URL.createObjectURL(blob), download: LOG.source.replace(/[^a-z0-9_-]+/gi, "_") + ".log" });
    document.body.append(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  });
  const view = $("#log-view");
  view.addEventListener("scroll", () => {
    $("#log-follow").checked = view.scrollHeight - view.scrollTop - view.clientHeight < 40;
  });
}

/* ---------------------------------------------------------------- tasks */

const taskForms = new Map();

function taskStatus(t) {
  if (t.busy) return { cls: "pending", label: t.busy.replace(/^.*?: /, ""), icon: "i-spinner", spin: true };
  if (t.state === "running") return { cls: "pending", label: `Running for ${fmtDur(t.unit.age)}`, icon: "i-spinner", spin: true };
  if (t.state === "failed") return { cls: "critical", label: `Failed${t.unit.exit && t.unit.exit !== "0" ? " (exit " + t.unit.exit + ")" : ""}`, icon: "i-x" };
  return { cls: "neutral", label: "Idle", icon: "i-dash" };
}

function renderTasks() {
  if (!S.state) return;
  const root = $("#task-groups");
  const groups = new Map();
  for (const t of S.state.tasks) {
    if (!groups.has(t.group)) groups.set(t.group, []);
    groups.get(t.group).push(t);
  }
  for (const [g, tasks] of groups) {
    let box = document.getElementById("tgrp-" + g);
    if (!box) {
      box = h("section", { class: "group", id: "tgrp-" + g }, h("h2", { text: g }), h("div", { class: "cards" }));
      root.append(box);
    }
    const cards = box.querySelector(".cards");
    for (const t of tasks) {
      let entry = taskForms.get(t.id);
      if (!entry) {
        const form = h("div", { class: "task-form" }, paramFields(t.params));
        const status = h("div");
        const actions = h("div", { class: "card-actions" });
        const el = h("article", { class: "card", "aria-label": t.name },
          h("div", { class: "card-head" },
            h("div", { class: "card-title" }, h("h3", { text: t.name }), h("div", { class: "desc", text: t.description })),
            status),
          t.params.length ? form : null, actions);
        entry = { el, form, status, actions, sig: "" };
        taskForms.set(t.id, entry);
        cards.append(el);
      }
      const st = taskStatus(t);
      const sig = JSON.stringify([st, t.state, !!t.busy]);
      if (sig !== entry.sig) {
        entry.sig = sig;
        entry.status.replaceChildren(badge(st));
        const running = t.state === "running";
        entry.actions.replaceChildren(
          h("button", { class: "btn small primary", type: "button", disabled: running || !!t.busy, onclick: () => runTask(t, entry.form) }, icon("i-play"), "Run"),
          h("button", { class: "btn small danger", type: "button", disabled: !running || !!t.busy, onclick: () => post("/api/task", { id: t.id, action: "stop" }) }, icon("i-square"), "Stop"),
          h("button", { class: "btn small ghost push", type: "button", onclick: () => openLogFor(t.log) }, icon("i-log"), "Log"));
      }
    }
  }
}

async function runTask(t, form) {
  const params = readFields(form);
  if (t.confirm) {
    const ok = await ask({ title: `Run "${t.name}"?`, text: t.description, ok: "Run", danger: true });
    if (!ok) return;
  }
  if (await post("/api/task", { id: t.id, action: "run", params })) openLogFor(t.log);
}

/* ---------------------------------------------------------------- views */

const VIEWS = ["live", "components", "metrics", "logs", "tasks"];

function showView() {
  const name = (location.hash || "#live").slice(1);
  S.view = VIEWS.includes(name) ? name : "live";
  for (const v of VIEWS) {
    $("#view-" + v).hidden = v !== S.view;
    $("#tab-" + v).setAttribute("aria-selected", String(v === S.view));
  }
  if (S.view === "logs") loadLogSources();
  if (S.view === "tasks") renderTasks();
  if (S.view === "live") renderLive();
  if (S.view !== "logs" && LOG.es) { LOG.es.close(); LOG.es = null; LOG.source = null; }
  updateMetrics();
  renderMetricsNote();
}

/* ---------------------------------------------------------------- theme */

function applyTheme(theme) {
  if (theme) document.documentElement.dataset.theme = theme;
  const dark = theme ? theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  $("#theme").replaceChildren(icon(dark ? "i-sun" : "i-moon"));
  for (const c of [...Object.values(S.charts), ...Object.values(S.ovCharts), ...S.sliceCharts.values()]) c.render();
}

function setupTheme() {
  let saved = null;
  try { saved = localStorage.getItem("tbdash-theme"); } catch (e) { /* storage blocked */ }
  applyTheme(saved);
  $("#theme").addEventListener("click", () => {
    const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    try { localStorage.setItem("tbdash-theme", next); } catch (e) { /* ignore */ }
    applyTheme(next);
  });
}

/* ----------------------------------------------------------------- init */

function connect() {
  const es = new EventSource("/api/events");
  let wasDown = false;
  es.addEventListener("open", async () => {
    setConn(true);
    if (wasDown) { wasDown = false; await loadHistory(); updateMetrics(); }
  });
  es.addEventListener("state", (e) => onState(JSON.parse(e.data)));
  es.addEventListener("metrics", (e) => onSample(JSON.parse(e.data)));
  es.addEventListener("toast", (e) => { const d = JSON.parse(e.data); toast(d.level, d.msg); });
  es.onerror = () => { setConn(false); wasDown = true; };
}

async function init() {
  setupTheme();
  setupLogs();
  for (const b of document.querySelectorAll("#range button")) {
    b.setAttribute("aria-pressed", String(Number(b.dataset.s) === S.range));
    b.addEventListener("click", () => {
      S.range = Number(b.dataset.s);
      for (const x of document.querySelectorAll("#range button")) x.setAttribute("aria-pressed", String(x === b));
      updateMetrics();
    });
  }
  $("#pause").addEventListener("click", (e) => {
    S.paused = !S.paused;
    e.currentTarget.setAttribute("aria-pressed", String(S.paused));
    e.currentTarget.textContent = S.paused ? "Resume" : "Pause";
    if (!S.paused) updateMetrics();
    renderMetricsNote();
  });
  $("#btn-start-all").addEventListener("click", async () => {
    const ok = await ask({ title: "Start everything?", text: "Starts, in order, the core (if stopped), the nearRT-RIC, the CU, the DU (rfsim) and the UEs, waiting for each step. What is already running is left alone. xApps are not started.", ok: "Start all" });
    if (ok) post("/api/orchestrate", { action: "start_all" });
  });
  $("#btn-stop-ran").addEventListener("click", async () => {
    const ok = await ask({ title: "Stop the RAN?", text: "Stops (SIGINT) the xApps first, then the UEs, the DU, the CU and the nearRT-RIC, including those started in terminals. The core keeps running.", ok: "Stop RAN", danger: true });
    if (ok) post("/api/orchestrate", { action: "stop_ran" });
  });
  window.addEventListener("hashchange", showView);

  try { S.cfg = await getJSON("/api/config"); S.sliceNames = S.cfg.slice_names || S.sliceNames; } catch (e) { /* retried by SSE state */ }
  await loadHistory();
  showView();
  connect();
  setInterval(() => { renderKpis(); renderMetricsNote(); }, 5000);
}

init();
