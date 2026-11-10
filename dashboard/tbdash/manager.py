"""Ties everything together: periodic status of components, core, tasks and
E2 metrics, health checks, the live topology, and the actions behind the
dashboard's buttons."""

import os
import queue
import re
import signal
import threading
import time

from . import units
from .core import Core, SystemdCore
from .logs import LogSources
from .metrics import Exporter, MetricsStore, SLICE_NAMES
from .procs import ProcScanner, stop_pids
from .util import log, run

MARKER_READ_LIMIT = 4 * 1024 * 1024
RUNNING = ("running", "external")


class Broadcaster:
    """Fan-out of server-sent events to every connected browser."""

    def __init__(self):
        self.lock = threading.Lock()
        self.clients = set()

    def subscribe(self):
        q = queue.Queue(maxsize=500)
        with self.lock:
            self.clients.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.clients.discard(q)

    def publish(self, event, data):
        with self.lock:
            clients = list(self.clients)
        for q in clients:
            try:
                q.put_nowait((event, data))
            except queue.Full:
                pass   # slow client: it resyncs from the next state event


class MarkerWatcher:
    """Reads a managed unit's log incrementally and remembers which health
    markers (regexes from the config) appeared since the unit started."""

    def __init__(self, path, markers):
        self.path = path
        self.markers = [(m["name"], re.compile(m["ok"]), re.compile(m["bad"]) if m["bad"] else None)
                        for m in markers]
        self.inode = None
        self.pos = 0
        self.seen = {}   # name -> True (ok seen last) / False (bad seen last)

    def update(self):
        if not self.markers:
            return
        try:
            st = os.stat(self.path)
        except OSError:
            self.inode, self.pos, self.seen = None, 0, {}
            return
        if st.st_ino != self.inode:      # a new file: the unit was (re)started
            self.inode, self.pos, self.seen = st.st_ino, 0, {}
        elif st.st_size < self.pos:      # trimmed in place (log_max_mb): same run, keep what was seen
            self.pos = 0
        if st.st_size == self.pos:
            return
        start = max(self.pos, st.st_size - MARKER_READ_LIMIT)
        try:
            with open(self.path, "rb") as f:
                f.seek(start)
                data = f.read(st.st_size - start)
        except OSError:
            return
        cut = data.rfind(b"\n")           # only consume complete lines
        if cut < 0:
            return
        self.pos = start + cut + 1
        text = data[:cut + 1].decode("utf-8", errors="replace")
        for name, ok, bad in self.markers:
            last_ok = max((m.start() for m in ok.finditer(text)), default=-1)
            last_bad = max((m.start() for m in bad.finditer(text)), default=-1) if bad else -1
            if last_ok >= 0 or last_bad >= 0:
                self.seen[name] = last_ok > last_bad


class Manager:
    def __init__(self, cfg):
        self.cfg = cfg
        self.log_dir = cfg["log_dir"]
        os.makedirs(self.log_dir, exist_ok=True)
        self.comps = {c["id"]: c for c in cfg["components"]}
        self.tasks = {t["id"]: t for t in cfg["tasks"]}
        self.events = Broadcaster()
        self.scanner = ProcScanner(cfg["components"])
        core_cls = SystemdCore if (cfg.get("core") or {}).get("type") == "systemd" else Core
        self.core = core_cls(cfg["core"], os.path.join(self.log_dir, "core.log")) if cfg.get("core") else None
        self.logs = LogSources(cfg, lambda: self.core.expected_containers() if self.core else [])
        self.store = MetricsStore(cfg["metrics"])
        self.exporter = Exporter(cfg["metrics"], self.store,
                                 os.path.join(self.log_dir, "exporter.log"), lambda: self.procs,
                                 [c["id"] for c in cfg["components"] if c["e2_types"]])
        self.markers = {cid: MarkerWatcher(self.logs.comp_path(cid), c["markers"])
                        for cid, c in self.comps.items()}
        self.procs = None
        self.unit_props = {}
        self.netns_ips = {}
        self.containers_up = set()   # running docker containers named by components
        self.core_state = None
        self.state = {}
        self.busy = {}          # target -> description of the running action
        self.busy_lock = threading.Lock()
        self.orch = None        # running orchestration description
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._netns_ts = 0.0
        self.refresh_lock = threading.Lock()
        self.scan_lock = threading.Lock()

    # ---------------------------------------------------------------- loops

    def start(self):
        threading.Thread(target=self._monitor_loop, name="monitor", daemon=True).start()
        threading.Thread(target=self._metrics_loop, name="metrics", daemon=True).start()
        self.exporter.start()

    def shutdown(self):
        self._stop.set()
        self.exporter.shutdown()

    def _metrics_loop(self):
        tick = self.cfg["metrics"]["tick_s"]
        nxt = time.time()
        while not self._stop.is_set():
            nxt += tick
            try:
                sample = self.store.tick()
                self.events.publish("metrics", sample)
            except Exception:
                log.exception("metrics tick")
            self._stop.wait(max(0.0, nxt - time.time()))

    def _monitor_loop(self):
        last_trim = 0.0
        while not self._stop.is_set():
            try:
                self.refresh()
                if time.time() - last_trim > 60:
                    last_trim = time.time()
                    self._trim_logs()
            except Exception:
                log.exception("monitor")
            self._wake.wait(2.0)
            self._wake.clear()

    def _trim_logs(self):
        limit = self.cfg["log_max_mb"] * 1024 * 1024
        paths = [self.logs.comp_path(cid) for cid in self.comps]
        paths += [self.logs.task_path(tid) for tid in self.tasks]
        paths += [os.path.join(self.log_dir, n) for n in ("core.log", "exporter.log")]
        for path in paths:
            if os.path.exists(path) and units.trim_log(path, limit):
                log.info("trimmed %s", path)

    def poke(self):
        self._wake.set()

    # --------------------------------------------------------------- status

    def _unit_list(self):
        return ([units.unit_name("comp", cid) for cid in self.comps]
                + [units.unit_name("task", tid) for tid in self.tasks])

    def _refresh_netns(self):
        now = time.time()
        if now - self._netns_ts < 5:
            return
        self._netns_ts = now
        ips = {}
        for c in self.comps.values():
            ns = c.get("netns")
            if not ns:
                continue
            if not os.path.exists(f"/run/netns/{ns}"):
                ips[ns] = None
                continue
            rc, out = run(["ip", "-n", ns, "-4", "-o", "addr", "show"], timeout=5)
            if rc != 0:
                ips[ns] = "?"     # e.g. not root: cannot enter the namespace
                continue
            found = []
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 4 and parts[1].startswith(tuple(self.cfg["ue_tun_prefixes"])):
                    found.append(f"{parts[3].split('/')[0]} ({parts[1]})")
            ips[ns] = found
        self.netns_ips = ips

    def _scan(self):
        with self.scan_lock:
            return self.scanner.scan()

    def _refresh_containers(self):
        names = [c["container"] for c in self.comps.values() if c.get("container")]
        if not names:
            return
        rc, out = run(["docker", "ps", "--format", "{{.Names}}"], timeout=10)
        self.containers_up = set(out.split()) & set(names) if rc == 0 else set()

    def refresh(self):
        with self.refresh_lock:
            self.procs = self._scan()
            self._refresh_containers()
            self.unit_props = units.show(self._unit_list())
            self._refresh_netns()
            for w in self.markers.values():
                w.update()
            if self.core:
                containers, err = self.core.containers()
                amf = self.core.amf_info() if containers and any(
                    c["name"] == self.core.amf and c["state"] == "running" for c in containers) else None
                self.core_state = {"containers": containers or [], "error": err, "amf": amf}
            self.state = self._build_state()
        self.events.publish("state", self.state)

    def _unit_info(self, unit):
        p = self.unit_props.get(unit) or {}
        mono = time.monotonic()
        return {
            "active": p.get("ActiveState", "inactive"),
            "sub": p.get("SubState", ""),
            "result": p.get("Result", ""),
            "exit": p.get("ExecMainStatus", ""),
            "main_pid": int(p.get("MainPID") or 0),
            "age": units.monotonic_to_age(p.get("ExecMainStartTimestampMonotonic"), mono),
            "exited_ago": units.monotonic_to_age(p.get("ExecMainExitTimestampMonotonic"), mono),
        }

    def _e2_nodes(self):
        st = self.store.link_status()
        if self.exporter.state not in ("running",):
            return None
        return [n.get("type_name") for n in st["nodes"]]

    def _ric_started(self, procs=None):
        """Start time of the running nearRT-RIC (main process), or None."""
        procs = procs if procs is not None else (self.procs or {})
        mains = [p["started"] for p in procs.get("ric", []) if p["main"]]
        return min(mains) if mains else None

    def _component_state(self, c):
        cid = c["id"]
        unit = self._unit_info(units.unit_name("comp", cid))
        procs = (self.procs or {}).get(cid, [])
        managed = unit["active"] in ("active", "activating", "deactivating", "reloading")
        if unit["active"] == "deactivating":
            state = "stopping"
        elif managed:
            state = "running"
        elif procs or (c.get("container") and c["container"] in self.containers_up):
            state = "external"
        elif unit["active"] == "failed":
            state = "failed"
        else:
            state = "stopped"

        main = [p for p in procs if p["main"]]
        uptime = (time.time() - main[0]["started"]) if main else (unit["age"] if managed else None)
        checks = []
        if state in RUNNING:
            if main:
                checks.append({"name": "Process", "ok": True,
                               "detail": ", ".join(str(p["pid"]) for p in main)})
            else:
                checks.append({"name": "Container", "ok": True,
                               "detail": f"{c['container']} running (started outside the dashboard)"})
            watcher = self.markers[cid]
            for mk in c["markers"]:
                if state == "external":
                    checks.append({"name": mk["name"], "ok": None, "link": mk["link"],
                                   "detail": "no log (started outside the dashboard)"})
                    continue
                seen = watcher.seen.get(mk["name"])
                if seen is None:
                    waiting = uptime is not None and uptime < 45
                    checks.append({"name": mk["name"], "ok": None if waiting else False, "link": mk["link"],
                                   "detail": "waiting" if waiting else "not seen in the log"})
                else:
                    checks.append({"name": mk["name"], "ok": seen, "link": mk["link"],
                                   "detail": "ok" if seen else "error in the log"})
            if c["e2_types"]:
                nodes = self._e2_nodes()
                if nodes is None:
                    checks.append({"name": "E2 (RIC)", "ok": None, "link": "e2",
                                   "detail": "no data from the E2 exporter"})
                else:
                    have = [t for t in c["e2_types"] if t in nodes]
                    young = uptime is not None and uptime < 30
                    ok = len(have) == len(c["e2_types"])
                    checks.append({"name": "E2 (RIC)", "ok": True if ok else (None if young else False),
                                   "link": "e2",
                                   "detail": ", ".join(have) if ok else
                                   ("waiting" if young else "node not connected to the RIC")})
            if c.get("xapp"):
                ric = self._ric_started()
                main_start = main[0]["started"] if main else None
                if ric is None:
                    checks.append({"name": "nearRT-RIC", "ok": False, "link": "e42",
                                   "detail": "the RIC is not running"})
                elif main_start is not None and main_start < ric:
                    checks.append({"name": "nearRT-RIC", "ok": False, "link": "e42",
                                   "detail": "started before the running RIC: its subscriptions are gone, restart it"})
                else:
                    checks.append({"name": "nearRT-RIC", "ok": True, "link": "e42",
                                   "detail": "attached to the running RIC"})
            if c.get("netns"):
                ips = self.netns_ips.get(c["netns"])
                young = uptime is not None and uptime < 40
                if ips == "?":
                    checks.append({"name": "IP (PDU session)", "ok": None, "link": "radio",
                                   "detail": "cannot enter the namespace (dashboard not running as root)"})
                else:
                    checks.append({"name": "IP (PDU session)", "ok": bool(ips) if ips or not young else None,
                                   "link": "radio",
                                   "detail": ", ".join(ips) if ips else
                                   ("waiting" if young else f"no UE tunnel interface in {c['netns']}")})
            if c.get("imsi") and self.core_state and self.core_state.get("amf"):
                reg = [u for u in self.core_state["amf"]["ues"] if u["imsi"] == c["imsi"]]
                ok = bool(reg) and "REGISTERED" in reg[0]["state"] and "DEREG" not in reg[0]["state"]
                checks.append({"name": "AMF registration",
                               "ok": ok if ok or not (uptime and uptime < 40) else None,
                               "detail": reg[0]["state"] if reg else "IMSI not in the AMF's UE table"})
        elif c.get("netns") and c["netns"] in self.netns_ips and self.netns_ips[c["netns"]] is None:
            checks.append({"name": "Namespace", "ok": None,
                           "detail": f"{c['netns']} does not exist (created on start)"})

        return {
            "id": cid, "kind": "process", "name": c["name"], "group": c["group"], "role": c["role"],
            "description": c["description"], "state": state, "health": self._health(state, checks, uptime),
            "uptime": uptime, "unit": unit, "procs": procs[:8],
            "cpu": round(sum(p["cpu"] for p in procs), 1),
            "rss": sum(p["rss"] for p in procs),
            "checks": checks, "busy": self.busy.get(f"comp:{cid}"),
            "params": c["params"], "log": f"comp:{cid}",
            "imsi": c.get("imsi"), "sst": c.get("sst"), "slice": c.get("slice"), "netns": c.get("netns"),
        }

    @staticmethod
    def _health(state, checks, uptime):
        if state == "failed":
            return "critical"
        if state == "stopped":
            return "neutral"
        if state == "stopping":
            return "pending"
        if any(ch["ok"] is False for ch in checks):
            return "warning"
        if any(ch["ok"] is None for ch in checks) and uptime is not None and uptime < 60:
            return "pending"
        return "good"

    def _core_component(self):
        cs = self.core_state or {"containers": [], "error": "loading", "amf": None}
        cont = cs["containers"]
        total = len(cont)
        running = [c for c in cont if c["state"] == "running"]
        unhealthy = [c for c in running if c["health"] == "unhealthy"]
        starting = [c for c in running if c["health"] == "starting"]
        if cs.get("error"):
            state, health = "unknown", "warning"
        elif not running:
            state, health = "stopped", "neutral"
        elif len(running) < total:
            state, health = "partial", "critical"
        else:
            state = "running"
            health = "warning" if unhealthy else ("pending" if starting else "good")
        checks = [{"name": "Containers", "ok": (len(running) == total) if running else None,
                   "detail": f"{len(running)}/{total} running"
                   + (f", {len(unhealthy)} unhealthy" if unhealthy else "")}]
        amf = cs.get("amf")
        if amf and running:
            gnbs = [g for g in amf["gnbs"] if g["status"].lower() == "connected"]
            regs = [u for u in amf["ues"] if "REGISTERED" in u["state"] and "DEREG" not in u["state"]]
            checks.append({"name": "gNB connected to the AMF", "ok": True if gnbs else None,
                           "detail": ", ".join(g["name"] for g in gnbs) or "none"})
            checks.append({"name": "Registered UEs", "ok": True if regs else None,
                           "detail": f"{len(regs)}: " + ", ".join(u["imsi"] for u in regs) if regs else "none"})
        return {
            "id": "core", "kind": "core", "name": self.cfg["core"]["name"], "group": "Core", "role": "core",
            "description": self.core.describe(), "state": state, "health": health,
            "containers": cont, "error": cs.get("error"), "amf": amf, "checks": checks,
            "busy": self.busy.get("core"), "log": "core", "params": [],
        }

    def _exporter_component(self):
        st = self.exporter.status()
        state = {"running": "running", "waiting": "running", "idle": "stopped",
                 "off": "stopped", "error": "failed"}.get(st["state"], "stopped")
        checks = []
        if st["state"] in ("running", "waiting"):
            types = [n.get("type_name") for n in st["nodes"]]
            checks.append({"name": "E2 nodes", "ok": bool(types) if st["state"] == "running" else None,
                           "detail": ", ".join(types) or st["detail"] or "—"})
            if st["state"] == "running":
                fresh = st["mac_age"] is not None and st["mac_age"] < 5
                checks.append({"name": "MAC indications", "ok": True if fresh else None,
                               "detail": f"{st['ues']} UE(s)" if fresh else "no UEs connected"})
        health = {"running": "good", "waiting": "pending", "error": "critical"}.get(st["state"], "neutral")
        if st["state"] == "running" and st["detail"]:
            health = "warning"
        return {
            "id": "exporter", "kind": "exporter", "name": self.cfg["metrics"]["name"], "group": "RIC",
            "role": "xapp", "description": self.cfg["metrics"]["description"],
            "state": state, "health": health, "detail": st["detail"], "checks": checks,
            "uptime": st["uptime"], "restarts": st["restarts"], "pid": st["pid"],
            "nodes": st["nodes"], "busy": self.busy.get("exporter"), "log": "exporter", "params": [],
        }

    def _task_state(self, t):
        unit = self._unit_info(units.unit_name("task", t["id"]))
        if unit["active"] in ("active", "activating", "deactivating"):
            state = "running"
        elif unit["active"] == "failed":
            state = "failed"
        else:
            state = "idle"
        return {"id": t["id"], "name": t["name"], "group": t["group"],
                "description": t["description"], "state": state, "unit": unit,
                "params": t["params"], "confirm": t["confirm"],
                "busy": self.busy.get(f"task:{t['id']}"), "log": f"task:{t['id']}"}

    # ------------------------------------------------------------- topology

    @staticmethod
    def _check_status(comp, link):
        """Status of a link from the component's checks that support it:
        True -> good, False -> warning, None -> None (not known)."""
        found = [ch["ok"] for ch in comp["checks"] if ch.get("link") == link]
        if not found:
            return None
        if any(v is False for v in found):
            return "warning"
        if any(v is True for v in found):
            return "good"
        return None

    def _topology(self, by_id):
        """Nodes to draw and the status of each interface between them. The
        RAN is drawn as CU + DU, or as the monolithic gNB when one runs."""
        roles = {}
        for cid, c in self.comps.items():
            roles.setdefault(c["role"], []).append(cid)
        up = lambda cid: cid in by_id and by_id[cid]["state"] in RUNNING  # noqa: E731

        gnbs_up = [g for g in roles.get("gnb", []) if up(g)]
        if gnbs_up:
            cus, dus, gnbs = [], [], gnbs_up
        else:
            cus = roles.get("cu", [])
            dus = [d for d in roles.get("du", []) if up(d)] or roles.get("du", [])[:1]
            gnbs = []
        ran = cus + dus + gnbs
        ues = roles.get("ue", [])
        xapps = roles.get("xapp", []) + (["exporter"] if self.cfg["metrics"]["in_topology"] else [])
        rics = roles.get("ric", [])
        ric = rics[0] if rics else None
        radio_parent = (gnbs or dus or [None])[0]
        core = "core" if self.core else None

        last = self.store.history[-1] if self.store.history else None
        fresh = last is not None and time.time() - last["t"] < 5
        mac_ues = len(last["ues"]) if fresh else 0
        amf = (by_id.get("core") or {}).get("amf") or {}
        amf_gnb = any(g["status"].lower() == "connected" for g in amf.get("gnbs", []))

        links = []

        def add(kind, a, b, label, status, detail, **extra):
            if a is None or b is None:
                return
            if not (up(a) or a in ("core",)) or not up(b):
                status, detail = "off", "not running"
            links.append(dict(id=f"{kind}:{a}:{b}", kind=kind, a=a, b=b, label=label,
                              status=status, detail=detail, **extra))

        # NG: CU (or gNB) <-> core
        for n in cus + gnbs:
            if not core:
                continue
            core_state = by_id["core"]["state"]
            st = self._check_status(by_id[n], "ng")
            if core_state != "running":
                status, detail = "critical", "core not running"
            elif st == "good" or (st is None and amf_gnb):
                status, detail = "good", "NG Setup done, gNB connected to the AMF"
            elif st == "warning":
                status, detail = "warning", "no NG Setup in the log"
            else:
                status, detail = "unknown", "waiting for NG Setup"
            add("ng", core, n, "NG", status, detail)

        # F1: CU <-> DU
        for cu in cus:
            for du in dus:
                st_du, st_cu = self._check_status(by_id[du], "f1"), self._check_status(by_id[cu], "f1")
                if "good" in (st_du, st_cu) or (mac_ues and up(du)):
                    status, detail = "good", "F1 Setup done" if "good" in (st_du, st_cu) else "UEs attached through the DU"
                elif "warning" in (st_du, st_cu):
                    status, detail = "warning", "no F1 Setup in the log"
                else:
                    status, detail = "unknown", "no log to check F1"
                add("f1", cu, du, "F1", status, detail)

        # E2: RAN nodes <-> RIC
        for n in ran:
            if not self.comps[n]["e2_types"]:
                continue
            st = self._check_status(by_id[n], "e2")
            status = st or "unknown"
            detail = {"good": "E2 node registered in the RIC", "warning": "E2 node not registered"}.get(
                st, "unknown (E2 exporter not running)")
            add("e2", n, ric, "E2", status, detail, flow=bool(st == "good" and fresh and n in dus + gnbs))

        # E42: xApps <-> RIC
        for x in xapps:
            comp = by_id.get(x)
            if comp is None:
                continue
            if x == "exporter":
                es = self.exporter.state
                status = {"running": "good", "waiting": "pending", "error": "critical"}.get(es, "off")
                detail = comp["detail"] or {"running": "subscribed to MAC + RLC"}.get(es, es)
                if es == "running" and comp["detail"]:
                    status = "warning"
            else:
                st = self._check_status(comp, "e42")
                status = st or "unknown"
                detail = {"good": "attached to the running RIC",
                          "warning": "attached to an older RIC instance: restart the xApp"}.get(st, "")
            add("e42", x, ric, "E42", status, detail, flow=bool(x == "exporter" and status == "good" and fresh))

        # Radio: UEs <-> DU/gNB, with the UE's live throughput when it can be
        # matched to a MAC report: by slice index when the component has one
        # (UEs that share an SST, srsRAN setup), else by S-NSSAI SST
        by_sst, by_slice = {}, {}
        if fresh:
            for rnti, rec in last["ues"].items():
                if rec.get("sst"):
                    by_sst.setdefault(rec["sst"], []).append((rnti, rec))
                if rec.get("slice") is not None:
                    by_slice.setdefault(rec["slice"], []).append((rnti, rec))
        for ue in ues:
            comp = by_id[ue]
            st = self._check_status(comp, "radio")
            status = st or ("pending" if comp["state"] in RUNNING else "off")
            if st == "warning":
                detail = "no PDU session IP"
            elif st == "good":
                detail = "PDU session up"
            else:
                detail = "attaching" if comp["state"] in RUNNING else ""
            extra = {}
            sst, sl = self.comps[ue].get("sst"), self.comps[ue].get("slice")
            match = by_slice.get(sl) if sl is not None else (by_sst.get(sst) if sst else None)
            if match and len(match) == 1 and comp["state"] in RUNNING:
                rnti, rec = match[0]
                extra = {"rnti": rnti, "ue": rec, "flow": bool((rec.get("dl_mbps") or 0) > 0.05
                                                               or (rec.get("ul_mbps") or 0) > 0.05)}
            add("radio", radio_parent, ue, "Radio", status, detail, **extra)

        nodes = ([core] if core else []) + ([ric] if ric else []) + ran + ues + xapps
        return {"nodes": [n for n in nodes if n in by_id],
                "ran": "gnb" if gnbs else "split", "ric": ric, "links": links,
                "mac_ues": mac_ues}

    def _build_state(self):
        comps = []
        if self.core:
            comps.append(self._core_component())
        comps.append(self._exporter_component())
        comps.extend(self._component_state(c) for c in self.comps.values())
        by_id = {c["id"]: c for c in comps}
        return {
            "t": time.time(),
            "host": os.uname().nodename,
            "components": comps,
            "topology": self._topology(by_id),
            "tasks": [self._task_state(t) for t in self.tasks.values()],
            "orchestration": self.orch,
            "slice_names": SLICE_NAMES,
        }

    # -------------------------------------------------------------- actions

    def _toast(self, level, msg):
        log.info("%s: %s", level, msg)
        self.events.publish("toast", {"level": level, "msg": msg, "t": time.time()})

    def _run_action(self, target, desc, fn):
        """Runs fn() in the background; one action per target at a time."""
        with self.busy_lock:
            if target in self.busy:
                return False, f"another action is in progress: {self.busy[target]}"
            self.busy[target] = desc

        def worker():
            try:
                ok, msg = fn()
                self._toast("good" if ok else "critical", msg)
            except Exception as e:
                log.exception("action %s", target)
                self._toast("critical", f"{desc}: internal error: {e}")
            finally:
                with self.busy_lock:
                    self.busy.pop(target, None)
                self.poke()

        threading.Thread(target=worker, name=f"action-{target}", daemon=True).start()
        self.poke()
        return True, desc

    @staticmethod
    def _check_params(spec_params, given):
        """Validates the values posted for a component/task against its params.
        Returns (values, error)."""
        values = {}
        given = given or {}
        for p in spec_params:
            raw = given.get(p["name"], p["default"])
            if p["type"] == "flag":
                values[p["name"]] = "1" if str(raw).lower() in ("1", "true", "on", "yes") else ""
                continue
            raw = str(raw).strip()
            if p["type"] == "choice":
                if raw not in p["choices"]:
                    return None, f"{p['label']}: invalid value"
            elif not re.fullmatch(p["pattern"], raw):
                return None, f"{p['label']}: invalid value"
            values[p["name"]] = raw
        return values, None

    @staticmethod
    def _apply_params(spec, values):
        """Fills {name} in the command (non-env params) and builds the env."""
        env = dict(spec["env"])
        argv = list(spec["cmd"])
        for p in spec["params"]:
            val = values.get(p["name"], "")
            if p["env"]:
                if val:
                    env[p["name"]] = val
                continue
            token = "{" + p["name"] + "}"
            argv = [a.replace(token, val) for a in argv]
        return argv, env

    def _run_pre(self, spec, desc):
        if not spec.get("pre"):
            return True, ""
        rc, out = run(["bash", "-c", spec["pre"]], timeout=60, cwd=spec["cwd"])
        if rc != 0:
            return False, f"{desc}: pre-start step failed: {out.strip()[-300:]}"
        return True, ""

    def _start_unit(self, kind, spec, values):
        argv, env = self._apply_params(spec, values)
        unit = units.unit_name(kind, spec["id"])
        path = self.logs.comp_path(spec["id"]) if kind == "comp" else self.logs.task_path(spec["id"])
        ok, msg = self._run_pre(spec, spec["name"])
        if not ok:
            return False, msg
        if spec["user"] not in ("root", "0"):
            env.setdefault("HOME", self.cfg["vars"].get("home", "/"))
        ok, msg = units.start(unit, spec, argv, env, path, f"testbed-dashboard: {spec['name']}")
        if not ok:
            return False, f"{spec['name']}: did not start: {msg}"
        return True, f"{spec['name']}: started"

    def _stale_xapp(self, c, procs):
        """True if this xApp started before the running nearRT-RIC, or the
        RIC is gone: its subscriptions died with the old RIC, and on SIGINT
        it would send subscription deletes for ids the running RIC never
        gave out (an unpatched FlexRIC RIC aborts on them, and they can even
        match another xApp's subscription). Such an xApp gets SIGKILL."""
        if not c.get("xapp"):
            return False
        mine = [p for p in procs.get(c["id"], []) if p["main"]]
        ric = self._ric_started(procs)
        return bool(mine) and (ric is None or min(p["started"] for p in mine) < ric)

    def _stop_component(self, c):
        cid = c["id"]
        unit = units.unit_name("comp", cid)
        msgs = []
        stale = self._stale_xapp(c, self._scan())
        if stale:
            msgs.append("SIGKILL: xApp older than the running nearRT-RIC")
        if units.is_active(unit):
            if stale:
                units.kill(unit, "SIGKILL")
            ok, msg = units.stop(unit, c["stop_timeout"])
            if not ok:
                return False, f"{c['name']}: failed to stop: {msg}"
            msgs.append("unit stopped")
        procs = self._scan().get(cid, [])
        if procs:
            sig = signal.SIGKILL if stale else getattr(signal, c["kill_signal"], signal.SIGINT)
            msgs.append(stop_pids([p["pid"] for p in procs], sig, c["stop_timeout"]))
        elif c.get("container_stop"):
            self._refresh_containers()
            if c["container"] in self.containers_up:
                rc, out = run(c["container_stop"], timeout=c["stop_timeout"] + 60, cwd=c["cwd"])
                if rc != 0:
                    return False, f"{c['name']}: failed to stop: {out.strip()[-200:]}"
                msgs.append("containers stopped")
        if not msgs:
            return True, f"{c['name']}: was already stopped"
        return True, f"{c['name']}: stopped ({'; '.join(msgs)})"

    def component_action(self, cid, action, params=None):
        if cid == "core":
            return self.core_action(action)
        if cid == "exporter":
            if action != "restart":
                return False, "invalid action"
            self.exporter.request_restart()
            return True, "the E2 exporter will restart"
        c = self.comps.get(cid)
        if c is None:
            return False, "unknown component"
        if action not in ("start", "stop", "restart"):
            return False, "invalid action"
        values = {}
        if action in ("start", "restart"):
            values, err = self._check_params(c["params"], params)
            if err:
                return False, err
        if action == "start" and (units.is_active(units.unit_name("comp", cid)) or self._scan().get(cid)):
            return False, f"{c['name']}: already running"
        if action == "start" and c.get("container") and not c.get("pre"):
            self._refresh_containers()
            if c["container"] in self.containers_up:
                return False, f"{c['name']}: already running (container {c['container']})"

        def fn():
            if action in ("stop", "restart"):
                ok, msg = self._stop_component(c)
                if not ok or action == "stop":
                    return ok, msg
                time.sleep(1)
            if action == "start" and self._scan().get(cid):
                return False, f"{c['name']}: already running"
            return self._start_unit("comp", c, values)

        verb = {"start": "starting", "stop": "stopping", "restart": "restarting"}[action]
        return self._run_action(f"comp:{cid}", f"{c['name']}: {verb}", fn)

    def core_action(self, action, container=None):
        if not self.core:
            return False, "no core configured"
        if action == "restart_container":
            return self._run_action("core", f"restarting {container}",
                                    lambda: self.core.restart_container(container))
        actions = {
            "start": ("Core: starting", "Core started", "start", lambda: self.core.compose("up", "-d")),
            "stop": ("Core: stopping", "Core stopped", "stop", lambda: self.core.compose("down")),
            "restart": ("Core: restarting", "Core restarted", "restart", self._core_restart),
        }
        if action not in actions:
            return False, "invalid action"
        desc, done, verb, fn = actions[action]

        def wrapped():
            ok, last = fn()
            if ok:
                return True, done
            return False, f"Core: failed to {verb}" + (f" ({last})" if last else "")
        return self._run_action("core", desc, wrapped)

    def _core_restart(self):
        ok, last = self.core.compose("down")
        if not ok:
            return ok, last
        return self.core.compose("up", "-d")

    def task_action(self, tid, action, params=None):
        t = self.tasks.get(tid)
        if t is None:
            return False, "unknown task"
        unit = units.unit_name("task", tid)
        if action == "run":
            values, err = self._check_params(t["params"], params)
            if err:
                return False, err
            if units.is_active(unit):
                return False, f"{t['name']}: already running"
            return self._run_action(f"task:{tid}", f"{t['name']}: starting",
                                    lambda: self._start_unit("task", t, values))
        if action == "stop":
            if not units.is_active(unit):
                return False, f"{t['name']}: not running"

            def stop_task():
                ok, msg = units.stop(unit, t["stop_timeout"])
                return ok, f"{t['name']}: stopped" if ok else f"{t['name']}: failed to stop: {msg}"
            return self._run_action(f"task:{tid}", f"{t['name']}: stopping", stop_task)
        return False, "invalid action"

    # -------------------------------------------------------- orchestration

    def _wait(self, cond, timeout, what):
        end = time.time() + timeout
        while time.time() < end:
            self.refresh()
            if cond():
                return True
            time.sleep(1.5)
        self._toast("warning", f"{what}: timed out, carrying on")
        return False

    def _comp(self, cid):
        return next((c for c in self.state.get("components", []) if c["id"] == cid), None)

    def orchestrate(self, action):
        if action not in ("start_all", "stop_ran"):
            return False, "invalid action"
        if self.orch:
            return False, f"already in progress: {self.orch}"

        order = sorted((c for c in self.comps.values() if c.get("autostart_order") is not None),
                       key=lambda c: c["autostart_order"])

        def step(msg):
            self.orch = msg
            self.events.publish("state", self._build_state())

        def start_all():
            if self.core:
                core = self._comp("core")
                if not core or core["state"] != "running":
                    step("starting the core")
                    self.core.compose("up", "-d")
                step("waiting for the core (healthy)")
                self._wait(lambda: (self._comp("core") or {}).get("health") == "good", 120, "core")
            for c in order:
                cur = self._comp(c["id"])
                if cur and cur["state"] in RUNNING:
                    continue
                step(f"starting {c['name']}")
                ok, msg = self._start_unit("comp", c, self._check_params(c["params"], {})[0] or {})
                if not ok:
                    return False, msg
                wait = c.get("start_wait") or {}
                if wait.get("marker"):
                    name = wait["marker"]
                    self._wait(lambda cid=c["id"], name=name:
                               self.markers[cid].seen.get(name) is True,
                               float(wait.get("timeout", 40)), f"{c['name']}: {name}")
                else:
                    time.sleep(float(wait.get("seconds", 3)))
            return True, "Start-up complete"

        def stop_ran():
            # xApps first, while their RIC is still up to take the
            # subscription deletes; then the rest in reverse start order.
            procs = self._scan()
            first = [c for c in self.comps.values() if c.get("autostart_order") is None and procs.get(c["id"])]
            first.sort(key=lambda c: not c.get("xapp"))
            for c in first + list(reversed(order)):
                step(f"stopping {c['name']}")
                self._stop_component(c)
            return True, "RAN, RIC and xApps stopped (the core keeps running)"

        def fn():
            try:
                return (start_all if action == "start_all" else stop_ran)()
            finally:
                self.orch = None

        desc = "Start all" if action == "start_all" else "Stop RAN"
        return self._run_action("orchestration", desc, fn)

    def ui_config(self):
        return {
            "metrics": {k: self.cfg["metrics"][k] for k in ("tick_s", "history_s", "dl_slots_per_s",
                                                             "ul_slots_per_s", "name", "cqi_note")},
            "core_kind": (self.cfg.get("core") or {}).get("type", "compose"),
            "slice_names": SLICE_NAMES,
            "host": os.uname().nodename,
            "user": self.cfg["vars"].get("user", ""),
        }
