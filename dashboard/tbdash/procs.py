"""Finds the processes of each component by their command line, whether the
dashboard started them or they were started from a terminal."""

import os
import signal
import time

import psutil


_INTERPRETERS = {"bash", "sh", "dash", "python", "python3"}


class ProcScanner:
    def __init__(self, components):
        # comp_id -> (exe basename, [substrings the arguments must contain])
        self.rules = {c["id"]: (c["match"]["exe"], c["match"]["args"]) for c in components}
        self._cache = {}  # pid -> psutil.Process, keeps cpu_percent() state

    def _match(self, cmdline):
        if not cmdline:
            return None
        exe = os.path.basename(cmdline[0])
        rest = cmdline[1:]
        if exe in _INTERPRETERS and rest and not rest[0].startswith("-"):
            exe, rest = os.path.basename(rest[0]), rest[1:]   # "bash script.sh args"
        args = " ".join(rest)
        for cid, (want_exe, want_args) in self.rules.items():
            if exe == want_exe and all(a in args for a in want_args):
                return cid
        return None

    def scan(self):
        """Returns {comp_id: [proc dict, ...]}, main processes first."""
        found = {}
        alive = set()
        for p in psutil.process_iter(["pid", "ppid", "cmdline", "create_time", "username"]):
            info = p.info
            cid = self._match(info.get("cmdline"))
            if cid is None:
                continue
            pid = info["pid"]
            alive.add(pid)
            proc = self._cache.get(pid)
            if proc is None or proc.create_time() != info["create_time"]:
                proc = p
                self._cache[pid] = proc
                try:
                    proc.cpu_percent(None)  # first call only primes the counter
                except psutil.Error:
                    pass
            try:
                cpu = proc.cpu_percent(None)
                rss = proc.memory_info().rss
            except psutil.Error:
                continue
            found.setdefault(cid, []).append({
                "pid": pid,
                "ppid": info["ppid"],
                "cmd": " ".join(info["cmdline"])[:400],
                "started": info["create_time"],
                "user": info.get("username") or "?",
                "cpu": cpu,
                "rss": rss,
            })
        for pid in list(self._cache):
            if pid not in alive:
                del self._cache[pid]

        for cid, procs in found.items():
            pids = {p["pid"] for p in procs}
            for p in procs:
                p["main"] = p["ppid"] not in pids
            procs.sort(key=lambda p: (not p["main"], p["started"]))
        return found


def stop_pids(pids, sig=signal.SIGINT, timeout=15):
    """Signals the given processes, waits, then SIGKILLs what is left.
    Returns a short description of what happened."""
    procs = []
    for pid in pids:
        try:
            procs.append(psutil.Process(pid))
        except psutil.Error:
            pass
    if not procs:
        return "nenhum processo"
    for p in procs:
        try:
            p.send_signal(sig)
        except psutil.Error:
            pass
    gone, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass
    if alive:
        psutil.wait_procs(alive, timeout=5)
        return f"{len(gone)} terminado(s), {len(alive)} morto(s) com SIGKILL"
    return f"{len(gone)} terminado(s)"


def boot_time():
    return psutil.boot_time()


def now():
    return time.time()
