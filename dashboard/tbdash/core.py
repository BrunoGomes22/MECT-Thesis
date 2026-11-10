"""The 5G core: docker compose project status, actions and the AMF's
periodic gNB/UE tables."""

import json
import os
import re
import time

import yaml

from .util import run

_ROW = re.compile(r"^\s*\|(.*)\|\s*$")


class Core:
    def __init__(self, cfg, log_path):
        self.cfg = cfg
        self.dir = cfg["compose_dir"]
        self.project = cfg["project"]
        self.amf = cfg["amf_container"]
        self.log_path = log_path
        self._expected = None
        self._amf_cache = (0.0, {"gnbs": [], "ues": [], "ok": False})

    def expected_containers(self):
        """Container names declared in the compose file."""
        if self._expected is None:
            names = []
            for fn in ("docker-compose.yaml", "docker-compose.yml", "compose.yaml", "compose.yml"):
                path = os.path.join(self.dir, fn)
                if os.path.exists(path):
                    try:
                        with open(path, encoding="utf-8") as f:
                            services = (yaml.safe_load(f) or {}).get("services") or {}
                        for svc, spec in services.items():
                            names.append((spec or {}).get("container_name") or f"{self.project}-{svc}-1")
                    except (OSError, yaml.YAMLError):
                        pass
                    break
            self._expected = names
        return self._expected

    def containers(self):
        rc, out = run(["docker", "ps", "-a", "--no-trunc",
                       "--filter", f"label=com.docker.compose.project={self.project}",
                       "--format", "{{json .}}"], timeout=15)
        if rc != 0:
            return None, out.strip()
        found = {}
        for line in out.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                c = json.loads(line)
            except ValueError:
                continue
            status = c.get("Status", "")
            health = c.get("HealthStatus") or ""
            if not health:
                m = re.search(r"\((healthy|unhealthy|health: starting)\)", status)
                health = m.group(1).replace("health: ", "") if m else ""
            found[c.get("Names", "?")] = {
                "name": c.get("Names", "?"),
                "state": c.get("State", "?"),
                "status": status,
                "health": health,
                "image": c.get("Image", ""),
            }
        for name in self.expected_containers():
            found.setdefault(name, {"name": name, "state": "missing", "status": "missing",
                                    "health": "", "image": ""})
        return sorted(found.values(), key=lambda c: c["name"]), None

    def amf_info(self, max_age=8.0):
        """Last gNB and UE tables the AMF printed (it prints them every few s)."""
        ts, cached = self._amf_cache
        if time.time() - ts < max_age:
            return cached
        rc, out = run(["docker", "logs", "--tail", "400", self.amf], timeout=15)
        info = {"gnbs": [], "ues": [], "ok": rc == 0}
        if rc == 0:
            lines = out.splitlines()
            gnb_at = max((i for i, l in enumerate(lines) if "gNBs' Information" in l), default=-1)
            ue_at = max((i for i, l in enumerate(lines) if "UEs' Information" in l), default=-1)
            if gnb_at >= 0:
                info["gnbs"] = [{"status": r[1], "id": r[2], "name": r[3], "plmn": r[4]}
                                for r in self._table(lines, gnb_at) if len(r) >= 5]
            if ue_at >= 0:
                info["ues"] = [{"state": r[1], "imsi": r[2], "plmn": r[6] if len(r) > 6 else ""}
                               for r in self._table(lines, ue_at) if len(r) >= 3]
        self._amf_cache = (time.time(), info)
        return info

    @staticmethod
    def _table(lines, start):
        """Rows after a table title: skips the header row and stops at the first
        non-row line or closing rule."""
        rows = []
        header = True
        for line in lines[start + 1:]:
            m = _ROW.match(line)
            if not m:
                break
            body = m.group(1)
            if "Information" in body:
                break
            if set(body.strip()) <= {"-", "|"}:
                if rows:
                    break
                continue
            cells = [c.strip() for c in body.split("|")]
            if header:
                header = False
                continue
            rows.append(cells)
        return rows

    def _log(self, text):
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(text if text.endswith("\n") else text + "\n")

    def compose(self, *args, timeout=300):
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self._log(f"# {stamp} docker compose {' '.join(args)}")
        rc, out = run(["docker", "compose"] + list(args), cwd=self.dir, timeout=timeout)
        self._log(out)
        self._log(f"# exit {rc}")
        self._amf_cache = (0.0, self._amf_cache[1])
        return rc == 0, out.strip().splitlines()[-1] if out.strip() else ""

    def restart_container(self, name):
        if name not in self.expected_containers():
            return False, f"unknown container: {name}"
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self._log(f"# {stamp} docker restart {name}")
        rc, out = run(["docker", "restart", name], timeout=120)
        self._log(out)
        return rc == 0, out.strip()

    def describe(self):
        return f"docker compose in {self.dir}"


# Open5GS AMF log lines (v2.7/2.8)
_O5GS_GNBS = re.compile(r"Number of gNBs is now (\d+)")
_O5GS_GNB_ADDR = re.compile(r"gNB-N2 accepted\[([^\]]+)\] in master_sm")
_O5GS_REG = re.compile(r"\[(imsi-\d+)\] Registration complete")
_O5GS_DEREG = re.compile(r"\[(imsi-\d+)\].*(De-register|Deregistration|Implicit De-registered)", re.I)
_O5GS_UES = re.compile(r"Number of AMF-UEs is now (\d+)")
AMF_LOG_READ = 2 * 1024 * 1024


class SystemdCore:
    """The 5G core as a set of systemd services (native Open5GS). Same
    interface as Core: each service is shown as a "container", start/stop act
    on all of them, and the AMF state comes from its log file."""

    def __init__(self, cfg, log_path):
        self.cfg = cfg
        self.services = list(cfg["services"])
        self.amf = cfg["amf_service"]
        self.amf_log = cfg["amf_log"]
        self.dir = ""
        self.log_path = log_path
        self._amf_cache = (0.0, {"gnbs": [], "ues": [], "ok": False})

    def describe(self):
        return f"systemd services: {', '.join(self.services)}"

    def expected_containers(self):
        return list(self.services)

    def containers(self):
        rc, out = run(["systemctl", "show", "--no-pager", "-p", "Id,ActiveState,SubState,Result"]
                      + [s + ".service" for s in self.services], timeout=15)
        if rc != 0:
            return None, out.strip()
        found = {}
        for block in out.strip().split("\n\n"):
            props = dict(line.partition("=")[::2] for line in block.splitlines())
            name = props.get("Id", "").removesuffix(".service")
            if not name:
                continue
            active = props.get("ActiveState", "")
            found[name] = {
                "name": name,
                "state": "running" if active == "active" else
                         ("exited" if active in ("inactive", "failed") else active),
                "status": f"{active} ({props.get('SubState', '')})",
                "health": "unhealthy" if active == "failed" else "",
                "image": "systemd",
            }
        return [found.get(s, {"name": s, "state": "missing", "status": "missing", "health": "",
                              "image": ""}) for s in self.services], None

    def amf_info(self, max_age=4.0):
        """gNBs and UE registrations, rebuilt from the tail of the AMF log."""
        ts, cached = self._amf_cache
        if time.time() - ts < max_age:
            return cached
        info = {"gnbs": [], "ues": [], "ok": False}
        try:
            with open(self.amf_log, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - AMF_LOG_READ))
                text = f.read().decode("utf-8", errors="replace")
            info["ok"] = True
        except OSError:
            text = ""
        n_gnbs, addrs, ues = 0, [], {}
        for line in text.splitlines():
            m = _O5GS_GNB_ADDR.search(line)
            if m:
                addrs.append(m.group(1))
            m = _O5GS_GNBS.search(line)
            if m:
                n_gnbs = int(m.group(1))
                if n_gnbs == 0:
                    addrs, ues = [], {}
            m = _O5GS_UES.search(line)
            if m and int(m.group(1)) == 0:
                ues = {}
            m = _O5GS_REG.search(line)
            if m:
                ues[m.group(1)] = "5GMM-REGISTERED"
            m = _O5GS_DEREG.search(line)
            if m:
                ues[m.group(1)] = "5GMM-DEREGISTERED"
        addrs = addrs[-n_gnbs:] if n_gnbs else []
        info["gnbs"] = [{"status": "connected", "id": str(i), "name": f"gNB-N2[{a}]", "plmn": ""}
                        for i, a in enumerate(addrs)]
        info["ues"] = [{"state": st, "imsi": imsi.removeprefix("imsi-"), "plmn": ""}
                       for imsi, st in sorted(ues.items())]
        self._amf_cache = (time.time(), info)
        return info

    def _log(self, text):
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(text if text.endswith("\n") else text + "\n")

    def _systemctl(self, verb, names):
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self._log(f"# {stamp} systemctl {verb} {' '.join(names)}")
        rc, out = run(["systemctl", verb] + [n + ".service" for n in names], timeout=120)
        self._log(out)
        self._log(f"# exit {rc}")
        self._amf_cache = (0.0, self._amf_cache[1])
        return rc == 0, out.strip().splitlines()[-1] if out.strip() else ""

    def compose(self, *args, timeout=300):
        """Kept for the Core interface: "up" starts, "down" stops every service."""
        verb = "stop" if args and args[0] == "down" else "start"
        return self._systemctl(verb, self.services)

    def restart_container(self, name):
        if name not in self.services:
            return False, f"unknown service: {name}"
        return self._systemctl("restart", [name])
