"""E2 metrics: runs the exporter xApp (MAC + RLC SMs -> JSON lines) and turns
its cumulative counters into per-UE and per-slice time series."""

import collections
import json
import os
import pwd
import signal
import subprocess
import threading
import time

from .util import log

STALE_S = 5.0          # a UE with no MAC report for this long has left
SLICE_NAMES = {0: "low", 1: "medium", 2: "high"}

# PUSCH SNR -> CQI, TS 38.214 Table 5.2.2.1-2 thresholds (same estimate as
# xapp_mac_stats_moni.c; wb_cqi is always 0 with one antenna port).
_CQI_THR = [-6.0, -4.3, -2.6, -0.8, 0.9, 2.6, 4.4, 6.2, 8.0, 9.9, 11.8, 13.8, 16.0, 18.2]


def snr_to_cqi(snr):
    if snr is None:
        return None
    for i, thr in enumerate(_CQI_THR):
        if snr < thr:
            return i + 1
    return 15


def _r(v, nd=3):
    return None if v is None else round(v, nd)


class MetricsStore:
    def __init__(self, mcfg):
        self.lock = threading.Lock()
        self.tick_s = mcfg["tick_s"]
        self.history_s = mcfg["history_s"]
        self.dl_slots = mcfg["dl_slots_per_s"]
        self.ul_slots = mcfg["ul_slots_per_s"]
        self.history = collections.deque(maxlen=int(self.history_s / self.tick_s) + 2)
        self.mac = {}        # rnti -> latest MAC UE dict (+ _ts agent time, _rx dashboard time)
        self.rlc = {}        # rnti -> aggregated DRB stats of the latest RLC report
        self.prev = {}       # rnti -> (mac, rlc) used for the last computed rates
        self.last_rec = {}   # rnti -> last computed record (reused when no new report)
        self.meta = {}       # rnti -> {"idx", "first", "last"}
        self.nodes = []
        self.hb = None
        self.last_hb = 0.0
        self.last_mac = 0.0
        self.last_rlc = 0.0

    def reset_link(self):
        """Exporter restarted: node list and heartbeat start over."""
        with self.lock:
            self.nodes = []
            self.hb = None
            self.last_hb = 0.0

    def ingest(self, obj):
        kind = obj.get("t")
        now = time.time()
        with self.lock:
            if kind == "mac":
                for ue in obj.get("ues", []):
                    ue["_ts"] = obj.get("ts", 0)
                    ue["_rx"] = now
                    self.mac[ue["rnti"]] = ue
                self.last_mac = now
            elif kind == "rlc":
                per = {}
                for rb in obj.get("rbs", []):
                    a = per.setdefault(rb["rnti"], {"tx_buf": 0, "rx_buf": 0, "tx_dd": 0, "tx_sdu": 0,
                                                    "rx_sdu": 0, "tx_retx": 0, "sojourn_us": None,
                                                    "hol_us": 0})
                    for key in ("tx_buf", "rx_buf", "tx_dd", "tx_sdu", "rx_sdu", "tx_retx"):
                        a[key] += rb.get(key) or 0
                    soj = rb.get("sojourn_us")
                    if soj is not None:
                        a["sojourn_us"] = max(a["sojourn_us"] or 0.0, soj)
                    a["hol_us"] = max(a["hol_us"], rb.get("hol_us") or 0)
                for rnti, a in per.items():
                    a["_ts"] = obj.get("ts", 0)
                    self.rlc[rnti] = a
                self.last_rlc = now
            elif kind == "nodes":
                self.nodes = obj.get("nodes", [])
            elif kind == "hb":
                self.hb = obj
                self.last_hb = now

    def _ue_index(self, rnti, now):
        meta = self.meta.get(rnti)
        if meta is None:
            # lowest colour index not used by a UE still visible in the history
            used = {m["idx"] for m in self.meta.values() if m["last"] > now - self.history_s}
            idx = 0
            while idx in used:
                idx += 1
            meta = {"idx": idx, "first": now, "last": now}
            self.meta[rnti] = meta
        meta["last"] = now
        return meta["idx"]

    def _record(self, rnti, cur, cur_rlc, now):
        prev = self.prev.get(rnti)
        rec = {
            "idx": self._ue_index(rnti, now),
            "slice": cur.get("slice"),
            "sst": cur.get("sst") or None,
            "dl_mcs": cur.get("dl_mcs"),
            "ul_mcs": cur.get("ul_mcs"),
            "cqi": cur.get("wb_cqi") or snr_to_cqi(cur.get("pusch_snr")),
            "cqi_est": not cur.get("wb_cqi"),
            "pusch_snr": _r(cur.get("pusch_snr"), 1),
            "pucch_snr": _r(cur.get("pucch_snr"), 1),
            "rsrp": cur.get("rsrp"),
            "sinr": _r(cur.get("sinr"), 1),
            "dl_bler": _r(cur.get("dl_bler"), 4),
            "ul_bler": _r(cur.get("ul_bler"), 4),
            "bsr": cur.get("bsr"),
            "phr": cur.get("phr"),
            "dl_total_mb": _r(cur.get("dl_tbs", 0) / 1e6, 2),
            "ul_total_mb": _r(cur.get("ul_tbs", 0) / 1e6, 2),
            "dl_mbps": None, "ul_mbps": None, "dl_prb": None, "ul_prb": None,
            "rlc_buf_kb": None, "rlc_delay_ms": None, "rlc_hol_ms": None, "rlc_drop_kbps": None,
        }
        if cur_rlc is not None:
            rec["rlc_buf_kb"] = _r(cur_rlc["tx_buf"] / 1e3, 2)
            soj = cur_rlc.get("sojourn_us")
            rec["rlc_delay_ms"] = _r(soj / 1e3, 2) if soj is not None else None
            rec["rlc_hol_ms"] = _r(cur_rlc["hol_us"] / 1e3, 2)

        if prev is not None:
            pmac, prlc = prev
            dt = (cur["_ts"] - pmac["_ts"]) / 1e6
            if dt > 0.05:
                def delta(key):
                    a, b = cur.get(key), pmac.get(key)
                    if a is None or b is None or a < b:
                        return None
                    return a - b
                dl, ul = delta("dl_tbs"), delta("ul_tbs")
                dprb, uprb = delta("dl_prb"), delta("ul_prb")
                rec["dl_mbps"] = _r(dl * 8 / dt / 1e6) if dl is not None else None
                rec["ul_mbps"] = _r(ul * 8 / dt / 1e6) if ul is not None else None
                rec["dl_prb"] = _r(dprb / dt / self.dl_slots, 2) if dprb is not None else None
                rec["ul_prb"] = _r(uprb / dt / self.ul_slots, 2) if uprb is not None else None
            if cur_rlc is not None and prlc is not None:
                dtr = (cur_rlc["_ts"] - prlc["_ts"]) / 1e6
                dd = cur_rlc["tx_dd"] - prlc["tx_dd"]
                if dtr > 0.05 and dd >= 0:
                    rec["rlc_drop_kbps"] = _r(dd * 8 / dtr / 1e3, 1)
        return rec

    def tick(self):
        """Computes one sample from the latest reports and appends it to the history."""
        now = time.time()
        with self.lock:
            ues = {}
            for rnti, cur in list(self.mac.items()):
                if now - cur["_rx"] > STALE_S:
                    del self.mac[rnti]
                    self.prev.pop(rnti, None)
                    self.last_rec.pop(rnti, None)
                    self.rlc.pop(rnti, None)
                    continue
                cur_rlc = self.rlc.get(rnti)
                prev = self.prev.get(rnti)
                if prev is not None and prev[0]["_ts"] == cur["_ts"] and rnti in self.last_rec:
                    rec = dict(self.last_rec[rnti])   # no new report since the last tick
                else:
                    rec = self._record(rnti, cur, cur_rlc, now)
                    self.prev[rnti] = (cur, cur_rlc)
                    self.last_rec[rnti] = rec
                ues[f"{rnti:04x}"] = rec

            # Sums stay None until at least one UE has a rate (a UE's first
            # report has no previous one to diff against).
            def add(acc, key, val):
                if val is not None:
                    acc[key] = (acc[key] or 0.0) + val

            slices = {}
            tot = {"dl_mbps": None, "ul_mbps": None, "ues": len(ues)}
            for rec in ues.values():
                key = str(rec["slice"]) if rec["slice"] is not None else "none"
                s = slices.setdefault(key, {"dl_mbps": None, "ul_mbps": None, "dl_prb": None,
                                            "ul_prb": None, "ues": 0})
                s["ues"] += 1
                for k in ("dl_mbps", "ul_mbps", "dl_prb", "ul_prb"):
                    add(s, k, rec[k])
                add(tot, "dl_mbps", rec["dl_mbps"])
                add(tot, "ul_mbps", rec["ul_mbps"])
            for s in slices.values():
                for k in ("dl_mbps", "ul_mbps", "dl_prb", "ul_prb"):
                    s[k] = _r(s[k])
            tot["dl_mbps"] = _r(tot["dl_mbps"])
            tot["ul_mbps"] = _r(tot["ul_mbps"])
            sample = {"t": round(now, 3), "ues": ues, "slices": slices, "tot": tot}
            self.history.append(sample)
            return sample

    def history_since(self, seconds):
        cutoff = time.time() - seconds
        with self.lock:
            return [s for s in self.history if s["t"] >= cutoff]

    def link_status(self):
        now = time.time()
        with self.lock:
            return {
                "nodes": self.nodes,
                "hb": self.hb,
                "hb_age": now - self.last_hb if self.last_hb else None,
                "mac_age": now - self.last_mac if self.last_mac else None,
                "rlc_age": now - self.last_rlc if self.last_rlc else None,
                "ues": len(self.mac),
            }


class Exporter(threading.Thread):
    """Keeps the exporter xApp running while the nearRT-RIC is up, and
    restarts it when the RIC or the DU behind it restarts (an xApp only
    learns the E2 nodes once, at E42 setup, and its subscriptions die with
    the DU)."""

    def __init__(self, mcfg, store, log_path, get_procs, e2_ids=()):
        super().__init__(name="exporter", daemon=True)
        self.cfg = mcfg
        # components that are E2 nodes: when one restarts, the exporter must
        # restart too, to see the new node list and resubscribe
        self.e2_ids = list(e2_ids) or list(mcfg["mac_nodes"])
        self.store = store
        self.log_path = log_path
        self.get_procs = get_procs   # -> latest {comp_id: [procs]} from the monitor
        self.proc = None
        self.started = 0.0
        self.anchor = None           # (ric pid, mac node pids) when this exporter started
        self.state = "off"
        self.detail = ""
        self.restarts = 0
        self.next_start = 0.0
        self.force_restart = False
        self._halt = threading.Event()

    def status(self):
        st = {"state": self.state, "detail": self.detail, "restarts": self.restarts,
              "pid": self.proc.pid if self.proc and self.proc.poll() is None else None,
              "uptime": time.time() - self.started if self.proc and self.proc.poll() is None else None,
              "enabled": bool(self.cfg.get("enabled") and self.cfg["cmd"])}
        st.update(self.store.link_status())
        return st

    def request_restart(self):
        self.force_restart = True

    def shutdown(self):
        self._halt.set()
        self._kill(clean=True)

    def _anchor_now(self, procs):
        ric = procs.get(self.cfg["anchor"]) or []
        ric_key = tuple(p["pid"] for p in ric if p["main"])
        nodes = tuple(sorted(p["pid"] for cid in self.e2_ids for p in procs.get(cid, []) if p["main"]))
        return ric_key, nodes

    @staticmethod
    def _youngest(procs, ids):
        """Uptime of the youngest running main process of these components, or None."""
        ages = [time.time() - p["started"] for cid in ids for p in procs.get(cid, []) if p["main"]]
        return min(ages) if ages else None

    def _spawn(self):
        argv = self.cfg["cmd"]
        if not os.path.exists(argv[0]):
            self.state, self.detail = "error", f"binary not found: {argv[0]}"
            self.next_start = time.time() + 30
            return
        env = dict(os.environ)
        env.update({"FLEXRIC_XAPP_NO_DB": "1", "EXPORTER_PERIOD_MS": str(self.cfg["period_ms"])})
        env.update({k: str(v) for k, v in self.cfg.get("env", {}).items()})
        kwargs = {}
        user = self.cfg.get("user")
        if user and user != "root" and os.geteuid() == 0:
            pw = pwd.getpwnam(user)
            kwargs.update(user=pw.pw_uid, group=pw.pw_gid, extra_groups=[])
            env.update({"HOME": pw.pw_dir, "USER": user, "LOGNAME": user})
        errlog = open(self.log_path, "a", encoding="utf-8")
        errlog.write(f"# {time.strftime('%Y-%m-%d %H:%M:%S')} start: {' '.join(argv)}\n")
        errlog.flush()
        try:
            self.proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=errlog,
                                         stdin=subprocess.DEVNULL, env=env,
                                         cwd=os.path.dirname(argv[0]) or "/",
                                         text=True, bufsize=1, start_new_session=True, **kwargs)
        except OSError as e:
            self.state, self.detail = "error", str(e)
            self.next_start = time.time() + 30
            return
        finally:
            errlog.close()
        self.started = time.time()
        log.info("exporter started, pid %d", self.proc.pid)
        self.store.reset_link()
        self.state, self.detail = "waiting", "waiting for E2 nodes in the RIC"
        threading.Thread(target=self._reader, args=(self.proc,), name="exporter-reader",
                         daemon=True).start()

    def _reader(self, proc):
        for line in proc.stdout:
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            self.store.ingest(obj)
            if obj.get("t") == "hb" and self.proc is proc:
                if obj.get("mac_subs"):
                    self.state, self.detail = "running", ""
                else:
                    self.state, self.detail = "running", "no DU/gNB with the MAC SM"

    def _kill(self, clean):
        """clean: SIGINT, so the xApp deletes its subscriptions first. Only
        while the RIC it subscribed through is still the running one: an xApp
        that sends subscription deletes to a restarted nearRT-RIC makes it
        abort (FlexRIC asserts on unknown request ids), so then SIGKILL."""
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        log.info("exporter pid %d: %s", proc.pid, "SIGINT (clean stop)" if clean else "SIGKILL")
        try:
            if clean:
                os.kill(proc.pid, signal.SIGINT)
                try:
                    proc.wait(timeout=8)
                    return
                except subprocess.TimeoutExpired:
                    pass
            proc.kill()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _restart(self, why, clean=True):
        log.info("exporter restart: %s", why)
        # before its first heartbeat the xApp has no subscriptions to delete
        # (it may still be blocked in E42 setup, where SIGINT does nothing)
        self._kill(clean and self.store.hb is not None)
        self.restarts += 1
        self.proc = None
        self.next_start = time.time() + 2

    def run(self):
        while not self._halt.wait(1.0):
            try:
                self._step()
            except Exception:   # keep supervising whatever happens
                log.exception("exporter supervisor")

    def _step(self):
        procs = self.get_procs()
        if procs is None:
            return
        enabled = bool(self.cfg.get("enabled") and self.cfg["cmd"])
        anchor_id = self.cfg["anchor"]
        ric_up = bool(procs.get(anchor_id))
        if not enabled or not ric_up:
            if self.proc is not None:
                self._kill(clean=ric_up)
                self.proc = None
            self.state = "off" if not enabled else "idle"
            self.detail = "" if not enabled else (
                "nearRT-RIC stopped" if anchor_id == "ric" else f"{anchor_id} stopped")
            return

        now = time.time()
        if self.proc is not None and self.proc.poll() is not None:
            code = self.proc.returncode
            log.info("exporter pid %d exited with code %s", self.proc.pid, code)
            self.proc = None
            self.state, self.detail = "error", f"exited (code {code})"
            self.restarts += 1
            self.next_start = now + min(30, 5 * self.restarts)
            return

        if self.proc is None:
            if now >= self.next_start:
                self.anchor = self._anchor_now(procs)
                self._spawn()
            return

        anchor = self._anchor_now(procs)
        uptime = now - self.started
        node_age = self._youngest(procs, self.e2_ids)
        mac_age = self._youngest(procs, self.cfg["mac_nodes"])
        hb = self.store.hb
        if self.force_restart:
            self.force_restart = False
            self._restart("requested from the dashboard")
        elif anchor[0] != self.anchor[0]:
            self._restart(f"{self.cfg['anchor']} restarted", clean=False)
        elif anchor[1] != self.anchor[1] and (node_age is None or node_age > 8):
            self._restart("an E2 node (CU/DU) started or stopped")
        elif hb is not None and self.store.last_hb and now - self.store.last_hb > 10:
            self._restart("no heartbeat for 10 s", clean=False)
        elif (hb is not None and not hb.get("mac_subs") and mac_age is not None
              and mac_age > 10 and uptime > 20):
            self._restart("DU/gNB running but no MAC subscription")
