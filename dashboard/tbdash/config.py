"""Loads config.yaml: expands ${vars}, fills defaults and checks the fields
the rest of the dashboard relies on."""

import os
import re
import shlex

import yaml

_VAR = re.compile(r"\$\{([A-Za-z0-9_]+)\}")
_ID = re.compile(r"^[a-z0-9_]+$")
# where a component sits in the live topology diagram
ROLES = ("ric", "cu", "du", "gnb", "ue", "xapp", "other")


class ConfigError(Exception):
    pass


def _expand_vars(variables):
    """Resolves ${name} references between the entries of `vars`."""
    out = dict(variables)
    for _ in range(10):
        changed = False
        for key, val in out.items():
            if isinstance(val, str):
                new = _VAR.sub(lambda m: str(out.get(m.group(1), m.group(0))), val)
                if new != val:
                    out[key] = new
                    changed = True
        if not changed:
            return out
    raise ConfigError("vars: circular ${...} reference")


def _subst(obj, variables):
    if isinstance(obj, str):
        def repl(m):
            if m.group(1) not in variables:
                raise ConfigError(f"unknown variable ${{{m.group(1)}}} in {obj!r}")
            return str(variables[m.group(1)])
        return _VAR.sub(repl, obj)
    if isinstance(obj, list):
        return [_subst(x, variables) for x in obj]
    if isinstance(obj, dict):
        return {k: _subst(v, variables) for k, v in obj.items()}
    return obj


def _argv(value, where):
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, str):
        return shlex.split(value)
    raise ConfigError(f"{where}: cmd must be a string or a list")


def _params(items, where):
    out = []
    for p in items or []:
        if "name" not in p:
            raise ConfigError(f"{where}: every param needs a name")
        kind = p.get("type", "text")
        if kind not in ("text", "flag", "choice"):
            raise ConfigError(f"{where}: param {p['name']}: unknown type {kind}")
        if kind == "choice" and not p.get("choices"):
            raise ConfigError(f"{where}: param {p['name']}: choice needs choices")
        out.append({
            "name": str(p["name"]),
            "label": str(p.get("label", p["name"])),
            "type": kind,
            "default": str(p.get("default", "")),
            "choices": [str(c) for c in p.get("choices", [])],
            "pattern": p.get("pattern", r"^[\w.:/,+=@-]*$"),
            "env": bool(p.get("env", False)),
            "help": str(p.get("help", "")),
        })
    return out


def _runnable(entry, where, defaults):
    """Fields shared by components and tasks (things started with systemd-run)."""
    return {
        "cmd": _argv(entry["cmd"], where),
        "cwd": entry.get("cwd", "/"),
        "user": entry.get("user", "root"),
        "env": {str(k): str(v) for k, v in (entry.get("env") or {}).items()},
        "pre": entry.get("pre"),
        "kill_signal": entry.get("kill_signal", defaults["kill_signal"]),
        "stop_timeout": int(entry.get("stop_timeout", defaults["stop_timeout"])),
        "params": _params(entry.get("params"), where),
    }


def load(path):
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    variables = _expand_vars({str(k): str(v) for k, v in (raw.get("vars") or {}).items()})
    cfg = _subst({k: v for k, v in raw.items() if k != "vars"}, variables)
    cfg["vars"] = variables

    server = cfg.setdefault("server", {})
    server.setdefault("host", "127.0.0.1")
    server["port"] = int(server.get("port", 8090))
    server.setdefault("allowed_hosts", [])
    auth = server.get("auth")
    if auth and not (auth.get("user") and auth.get("password")):
        raise ConfigError("server.auth needs user and password")
    if auth:
        # YAML reads "password: 4553" as a number
        server["auth"] = auth = {"user": str(auth["user"]), "password": str(auth["password"])}
    if server["host"] not in ("127.0.0.1", "localhost", "::1") and not auth:
        raise ConfigError("server.host is not loopback: set server.auth (user/password) "
                          "or keep host 127.0.0.1")

    cfg.setdefault("log_dir", "/var/log/testbed-dashboard")
    cfg["log_max_mb"] = int(cfg.get("log_max_mb", 200))
    defaults = {
        "kill_signal": cfg.get("kill_signal", "SIGINT"),
        "stop_timeout": int(cfg.get("stop_timeout", 15)),
    }

    m = cfg.setdefault("metrics", {})
    m.setdefault("enabled", True)
    m["cmd"] = _argv(m.get("cmd", []), "metrics") if m.get("cmd") else []
    m.setdefault("user", variables.get("user", "root"))
    m.setdefault("env", {})
    m["period_ms"] = int(m.get("period_ms", 100))
    m["tick_s"] = float(m.get("tick_s", 1.0))
    m["history_s"] = int(m.get("history_s", 900))
    m["dl_slots_per_s"] = float(m.get("dl_slots_per_s", 1600))
    m["ul_slots_per_s"] = float(m.get("ul_slots_per_s", 600))
    m["mac_nodes"] = [str(x) for x in m.get("mac_nodes", ["du", "gnb"])]
    # component the exporter needs running ("ric" for an E2 xApp; "du" when
    # the metrics come straight from the DU, e.g. the srsRAN WebSocket)
    m["anchor"] = str(m.get("anchor", "ric"))
    m["name"] = str(m.get("name", "E2 metrics exporter"))
    m["description"] = str(m.get("description", "xApp: subscribes to MAC + RLC and feeds the charts"))
    # draw the exporter as an xApp attached to the RIC (E42) in the topology
    m["in_topology"] = bool(m.get("in_topology", True))
    m["cqi_note"] = str(m.get("cqi_note", "CQI (est.) is estimated from the PUSCH SNR, "
                                          "as OAI reports no CQI with one antenna port."))

    core = cfg.setdefault("core", {})
    if core:
        core.setdefault("type", "compose")
        if core["type"] == "compose":
            for key in ("compose_dir", "project"):
                if key not in core:
                    raise ConfigError(f"core.{key} is required")
            core.setdefault("amf_container", "oai-amf")
            core.setdefault("name", "Core 5G (OAI CN5G)")
        elif core["type"] == "systemd":
            if not core.get("services"):
                raise ConfigError("core.services is required for core.type systemd")
            core["services"] = [str(x) for x in core["services"]]
            core.setdefault("amf_service", core["services"][0])
            core.setdefault("amf_log", "/var/log/open5gs/amf.log")
            core.setdefault("name", "Core 5G (Open5GS)")
        else:
            raise ConfigError("core.type must be compose or systemd")

    # interfaces whose IP shows that a UE has a PDU session
    cfg["ue_tun_prefixes"] = [str(x) for x in cfg.get("ue_tun_prefixes", ["oaitun", "tun_srsue"])]

    seen = set()
    comps = []
    for c in cfg.get("components") or []:
        cid = str(c.get("id", ""))
        where = f"components.{cid or '?'}"
        if not _ID.match(cid) or cid in seen or cid in ("core", "exporter"):
            raise ConfigError(f"{where}: id must be unique, [a-z0-9_]+, not 'core'/'exporter'")
        if "cmd" not in c or "match" not in c:
            raise ConfigError(f"{where}: cmd and match are required")
        seen.add(cid)
        if c.get("role") and c["role"] not in ROLES:
            raise ConfigError(f"{where}: role must be one of {', '.join(ROLES)}")
        match = c["match"]
        if not match.get("exe"):
            raise ConfigError(f"{where}: match.exe is required")
        comp = {
            "id": cid,
            "name": str(c.get("name", cid)),
            "group": str(c.get("group", "Other")),
            "description": str(c.get("description", "")),
            "match": {"exe": str(match["exe"]),
                      "args": [str(a) for a in match.get("args", [])]},
            "markers": [{"name": str(mk["name"]), "ok": str(mk["ok"]),
                         "bad": str(mk["bad"]) if mk.get("bad") else None,
                         "link": str(mk["link"]) if mk.get("link") else None}
                        for mk in c.get("markers", [])],
            "e2_types": [str(t) for t in c.get("e2_types", [])],
            "netns": c.get("netns"),
            "xapp": bool(c.get("xapp", False)),
            "role": str(c.get("role") or ("ric" if cid == "ric" else "xapp" if c.get("xapp")
                                          else "ue" if c.get("netns") else "other")),
            "sst": int(c["sst"]) if c.get("sst") is not None else None,
            # slice index (0 low, 1 medium, 2 high) used to match the UE to its
            # metrics when the UEs share an SST and differ in SD (srsRAN setup)
            "slice": int(c["slice"]) if c.get("slice") is not None else None,
            "imsi": str(c["imsi"]) if c.get("imsi") else None,
            "autostart_order": c.get("autostart_order"),
            "start_wait": c.get("start_wait"),
            # docker container that shows the component running when it has no
            # matching process (e.g. the RIC started with "docker compose up -d"),
            # and the command that stops it in that case
            "container": str(c["container"]) if c.get("container") else None,
            "container_stop": _argv(c["container_stop"], where) if c.get("container_stop") else None,
        }
        comp.update(_runnable(c, where, defaults))
        comps.append(comp)
    cfg["components"] = comps

    seen = set()
    tasks = []
    for t in cfg.get("tasks") or []:
        tid = str(t.get("id", ""))
        where = f"tasks.{tid or '?'}"
        if not _ID.match(tid) or tid in seen:
            raise ConfigError(f"{where}: id must be unique, [a-z0-9_]+")
        if "cmd" not in t:
            raise ConfigError(f"{where}: cmd is required")
        seen.add(tid)
        task = {
            "id": tid,
            "name": str(t.get("name", tid)),
            "group": str(t.get("group", "Tasks")),
            "description": str(t.get("description", "")),
            "confirm": bool(t.get("confirm", False)),
        }
        task.update(_runnable(t, where, defaults))
        task["kill_signal"] = t.get("kill_signal", "SIGINT")
        tasks.append(task)
    cfg["tasks"] = tasks

    cfg["extra_logs"] = [{"id": str(x["id"]), "name": str(x.get("name", x["id"])),
                          "path": str(x["path"]), "group": str(x.get("group", "Files"))}
                         for x in cfg.get("extra_logs") or []]
    for x in cfg["extra_logs"]:
        if not _ID.match(x["id"]):
            raise ConfigError(f"extra_logs.{x['id']}: id must be [a-z0-9_]+")
    return cfg


def find_config(argv_path=None):
    if argv_path:
        return argv_path
    env = os.environ.get("TESTBED_DASHBOARD_CONFIG")
    if env:
        return env
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
