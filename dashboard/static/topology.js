/* Live topology diagram (inline SVG): core, nearRT-RIC, CU/DU (or gNB), UEs
   and xApps, with the state of every node and interface (NG, F1, E2, E42,
   radio). The SVG is rebuilt only when the set of nodes/links changes;
   otherwise attributes are updated in place, so focus and selection stay. */
"use strict";

(function (global) {
  const NS = "http://www.w3.org/2000/svg";
  const W = 1180;
  const SIZE = { big: [200, 96], ue: [196, 96], xapp: [176, 84] };
  const ROLE_LABEL = { core: "5G CORE", ric: "NEAR-RT RIC", cu: "CU", du: "DU", gnb: "gNB", ue: "UE", xapp: "xAPP", other: "" };
  const STATUS = {
    good: { icon: "i-check", label: "OK" },
    warning: { icon: "i-alert", label: "Degraded" },
    critical: { icon: "i-x", label: "Down" },
    pending: { icon: "i-clock", label: "Waiting" },
    unknown: { icon: "i-help", label: "Unknown" },
    off: { icon: "i-dash", label: "Not running" },
  };

  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v != null) e.setAttribute(k, v);
    if (parent) parent.append(e);
    return e;
  }

  function useIcon(parent, id, x, y, size, cls) {
    const u = el("use", { href: "#" + id, x, y, width: size, height: size, class: "ticon " + (cls || "") }, parent);
    return u;
  }

  function clip(text, max) {
    text = String(text || "");
    return text.length > max ? text.slice(0, max - 1) + "…" : text;
  }

  function sizeOf(role) {
    return role === "ue" ? SIZE.ue : role === "xapp" ? SIZE.xapp : SIZE.big;
  }

  /* positions of every node (top-left corner), from the roles in the topology */
  function layout(topo, comps) {
    const role = (id) => (comps[id] ? comps[id].role : "other");
    const ids = topo.nodes;
    const xapps = ids.filter((id) => role(id) === "xapp");
    const ues = ids.filter((id) => role(id) === "ue");
    const cus = ids.filter((id) => role(id) === "cu");
    const dus = ids.filter((id) => role(id) === "du");
    const gnbs = ids.filter((id) => role(id) === "gnb");
    const pos = {};
    const xRowY = 20, ricY = 170, ranY = 330;

    if (ids.includes("core")) pos.core = [20, ranY];
    let ranCenter;
    if (gnbs.length) {
      gnbs.forEach((id, i) => { pos[id] = [430 + i * 230, ranY]; });
      ranCenter = 430 + (gnbs.length * 230 - 30) / 2;
    } else {
      cus.forEach((id, i) => { pos[id] = [300 + i * 10, ranY + i * 10]; });
      dus.forEach((id, i) => { pos[id] = [600 + i * 10, ranY + i * 10]; });
      ranCenter = (300 + 600 + 200) / 2;
    }
    if (topo.ric) pos[topo.ric] = [ranCenter - 100, ricY];

    const xw = SIZE.xapp[0], gap = 16;
    const rowW = xapps.length * xw + Math.max(0, xapps.length - 1) * gap;
    let x0 = Math.max(20, Math.min(W - 20 - rowW, ranCenter - rowW / 2));
    xapps.forEach((id, i) => { pos[id] = [x0 + i * (xw + gap), xRowY]; });

    const uh = SIZE.ue[1], ugap = 16;
    const colH = ues.length * uh + Math.max(0, ues.length - 1) * ugap;
    let uTop = ranY + SIZE.big[1] / 2 - colH / 2;
    uTop = Math.max(uTop, xRowY + SIZE.xapp[1] + 24);
    ues.forEach((id, i) => { pos[id] = [W - 20 - SIZE.ue[0], uTop + i * (uh + ugap)]; });

    let H = ranY + SIZE.big[1];
    if (ues.length) H = Math.max(H, uTop + colH);
    return { pos, H: H + 24 };
  }

  function anchor(p, size, side) {
    const [x, y] = p, [w, h] = size;
    return side === "left" ? [x, y + h / 2] : side === "right" ? [x + w, y + h / 2]
      : side === "top" ? [x + w / 2, y] : [x + w / 2, y + h];
  }

  function bez(p0, p3, mode) {
    let p1, p2;
    if (mode === "h") { const dx = (p3[0] - p0[0]) * 0.45; p1 = [p0[0] + dx, p0[1]]; p2 = [p3[0] - dx, p3[1]]; }
    else { const dy = (p3[1] - p0[1]) * 0.5; p1 = [p0[0], p0[1] + dy]; p2 = [p3[0], p3[1] - dy]; }
    return [p0, p1, p2, p3];
  }

  function at(b, t) {
    const u = 1 - t;
    const a = u * u * u, c = 3 * u * u * t, d = 3 * u * t * t, e = t * t * t;
    return [a * b[0][0] + c * b[1][0] + d * b[2][0] + e * b[3][0], a * b[0][1] + c * b[1][1] + d * b[2][1] + e * b[3][1]];
  }

  function pathOf(b) {
    return `M${b[0][0]},${b[0][1]} C${b[1][0]},${b[1][1]} ${b[2][0]},${b[2][1]} ${b[3][0]},${b[3][1]}`;
  }

  class Topology {
    constructor(container, opts) {
      this.container = container;
      this.opts = opts;           // { onSelect(id), statusOf(comp) -> {cls,label,icon,spin}, fmt }
      this.sig = "";
      this.nodes = {};
      this.links = {};
      this.selected = null;
    }

    select(id) {
      this.selected = id;
      for (const [nid, n] of Object.entries(this.nodes)) n.g.classList.toggle("selected", nid === id);
    }

    render(topo, comps) {
      if (!topo) return;
      const sig = topo.nodes.join(",") + "|" + topo.links.map((l) => l.id).join(",");
      if (sig !== this.sig) this._build(topo, comps, sig);
      this._update(topo, comps);
    }

    _build(topo, comps, sig) {
      this.sig = sig;
      const { pos, H } = layout(topo, comps);
      this.pos = pos;
      const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "topo", role: "group",
        "aria-label": "Live topology of the testbed" });
      const gLinks = el("g", { class: "links" }, svg);
      const gChips = el("g", { class: "chips" }, svg);
      const gNodes = el("g", { class: "nodes" }, svg);
      this.nodes = {};
      this.links = {};

      // E2/E42 links fan into the RIC: spread their attach points
      const ricP = topo.ric && pos[topo.ric];
      const into = { e2: topo.links.filter((l) => l.kind === "e2"), e42: topo.links.filter((l) => l.kind === "e42") };
      const spread = (kind, l) => {
        const list = into[kind], i = list.indexOf(l), n = list.length;
        const w = SIZE.big[0] - 40;
        return ricP[0] + 20 + (n === 1 ? w / 2 : (w * i) / (n - 1));
      };

      for (const l of topo.links) {
        const pa = pos[l.a], pb = pos[l.b];
        if (!pa || !pb) continue;
        const sa = sizeOf(comps[l.a] && comps[l.a].role), sb = sizeOf(comps[l.b] && comps[l.b].role);
        let b, t = 0.5;
        if (l.kind === "ng" || l.kind === "f1") {
          b = bez(anchor(pa, sa, "right"), anchor(pb, sb, "left"), "h");
        } else if (l.kind === "radio") {
          b = bez(anchor(pa, sa, "right"), anchor(pb, sb, "left"), "h");
          t = 0.62;
        } else if (l.kind === "e2" && ricP) {
          b = bez(anchor(pa, sa, "top"), [spread("e2", l), ricP[1] + SIZE.big[1]], "v");
        } else if (l.kind === "e42" && ricP) {
          b = bez(anchor(pa, sa, "bottom"), [spread("e42", l), ricP[1]], "v");
          t = 0.42;
        } else continue;
        const g = el("g", { class: "link" }, gLinks);
        const title = el("title", null, g);
        const path = el("path", { d: pathOf(b), class: "wire" }, g);
        const flow = el("path", { d: pathOf(b), class: "flow" }, g);
        const m = at(b, t);
        const chip = el("g", { class: "chip", transform: `translate(${m[0]},${m[1]})` }, gChips);
        const chipTitle = el("title", null, chip);
        const rect = el("rect", { y: -11, height: 22, rx: 11 }, chip);
        const icon = useIcon(chip, "i-check", 0, -7, 14);
        const text = el("text", { y: 4, class: "chip-text" }, chip);
        this.links[l.id] = { g, path, flow, title, chip, chipTitle, rect, icon, text, kind: l.kind };
      }

      for (const id of topo.nodes) {
        const c = comps[id];
        if (!c || !pos[id]) continue;
        const [w, h] = sizeOf(c.role);
        const [x, y] = pos[id];
        const g = el("g", { class: "node", transform: `translate(${x},${y})`, tabindex: 0, role: "button",
          "data-id": id }, gNodes);
        el("rect", { width: w, height: h, rx: 10, class: "box" }, g);
        el("rect", { x: 0, y: 10, width: 4, height: h - 20, rx: 2, class: "accent" }, g);
        // rows: role 18, name 37, state 56, detail at the bottom (>= 18 px apart)
        const role = el("text", { x: 16, y: 18, class: "role" }, g);
        role.textContent = ROLE_LABEL[c.role] || "";
        const name = el("text", { x: 16, y: 37, class: "name" }, g);
        const key = el("line", { x1: w - 30, x2: w - 14, y1: 32, y2: 32, class: "series-key" }, g);
        const sIcon = useIcon(g, "i-check", 16, 45, 14);
        const sText = el("text", { x: 35, y: 56, class: "state" }, g);
        const detail = el("text", { x: 16, y: h - 10, class: "detail" }, g);
        const title = el("title", null, g);
        g.addEventListener("click", () => this.opts.onSelect(id));
        g.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); this.opts.onSelect(id); }
        });
        this.nodes[id] = { g, name, key, sIcon, sText, detail, title, w, role: c.role };
      }

      this.container.replaceChildren(svg);
      this.svg = svg;
      if (this.selected) this.select(this.selected);
    }

    _update(topo, comps) {
      const fmt = this.opts.fmt;
      for (const l of topo.links) {
        const e = this.links[l.id];
        if (!e) continue;
        const st = STATUS[l.status] || STATUS.unknown;
        e.g.setAttribute("class", `link s-${l.status}` + (l.flow && l.status === "good" ? " flowing" : ""));
        e.chip.setAttribute("class", `chip s-${l.status}`);
        let label = l.label;
        if (l.kind === "radio" && l.ue && l.status !== "off") {
          label = `↓ ${fmt(l.ue.dl_mbps, 1)}  ↑ ${fmt(l.ue.ul_mbps, 1)} Mbit/s`;
        }
        e.text.textContent = label;
        e.icon.setAttribute("href", "#" + st.icon);
        const tw = Math.ceil(label.length * 6.6) + 34;
        e.rect.setAttribute("x", -tw / 2);
        e.rect.setAttribute("width", tw);
        e.icon.setAttribute("x", -tw / 2 + 8);
        e.text.setAttribute("x", -tw / 2 + 26);
        const tip = `${l.label}: ${st.label}${l.detail ? " — " + l.detail : ""}`;
        e.title.textContent = tip;
        e.chipTitle.textContent = tip;
      }

      const radio = {};
      for (const l of topo.links) if (l.kind === "radio" && l.ue) radio[l.b] = l;

      for (const id of topo.nodes) {
        const n = this.nodes[id], c = comps[id];
        if (!n || !c) continue;
        const st = this.opts.statusOf(c);
        n.g.setAttribute("class", `node h-${st.cls}` + (id === this.selected ? " selected" : ""));
        n.name.textContent = clip(c.name, n.w > 190 ? 22 : 19);
        n.sIcon.setAttribute("href", "#" + st.icon);
        n.sIcon.setAttribute("class", "ticon" + (st.spin ? " spin-svg" : ""));
        n.sText.textContent = clip(st.label + (c.state === "external" ? " · outside dashboard" : ""), n.w > 190 ? 28 : 24);
        n.detail.textContent = clip(this._detail(c, radio[id], topo), n.w > 190 ? 32 : 28);
        n.title.textContent = `${c.name}: ${st.label}. Click for details and controls.`;
        const r = radio[id];
        if (r && r.ue && r.ue.idx != null) {
          n.key.style.display = "";
          n.key.setAttribute("style", `stroke: var(--series-${(r.ue.idx % 8) + 1})`);
        } else {
          n.key.style.display = "none";
        }
        n.g.setAttribute("aria-label", `${c.name}: ${st.label}`);
      }
    }

    _detail(c, radio, topo) {
      const fmt = this.opts.fmt;
      if (c.kind === "core") {
        const n = c.containers.filter((x) => x.state === "running").length;
        const regs = c.amf ? c.amf.ues.filter((u) => /REGISTERED/.test(u.state) && !/DEREG/.test(u.state)).length : 0;
        return `${n}/${c.containers.length} containers · ${regs} UE(s) reg.`;
      }
      if (c.kind === "exporter") {
        return c.state === "running" ? `${(c.nodes || []).length} E2 node(s) · ${topo.mac_ues} UE(s)` : (c.detail || "");
      }
      if (c.state !== "running" && c.state !== "external") return c.state === "failed" ? "exited with an error" : "stopped";
      if (c.role === "ue") {
        const ip = (c.checks.find((x) => x.name === "IP (PDU session)") || {}).detail || "";
        const m = /^(\d+\.\d+\.\d+\.\d+)/.exec(ip);
        if (radio && radio.ue) return `${m ? m[1] + " · " : ""}slice ${radio.ue.slice ?? "—"} · MCS ${radio.ue.dl_mcs ?? "—"}`;
        return m ? m[1] : "attaching";
      }
      if (c.role === "du" || c.role === "gnb") return `${topo.mac_ues} UE(s) · CPU ${fmt(c.cpu, 0)} %`;
      return `up ${global.fmtDur ? global.fmtDur(c.uptime) : ""} · CPU ${fmt(c.cpu, 0)} %`;
    }
  }

  global.Topology = Topology;
  global.TOPO_STATUS = STATUS;
})(window);
