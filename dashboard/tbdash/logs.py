"""Log sources: files written by the units the dashboard starts, the
dashboard's own logs, extra files from the config, and docker containers."""

import os
import queue
import subprocess
import threading
import time

from .util import classify_line, run

MAX_BATCH = 1500


def tail_file(path, lines):
    """Last `lines` lines of a file, read backwards in blocks."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            block = 64 * 1024
            data = b""
            pos = end
            while pos > 0 and data.count(b"\n") <= lines:
                step = min(block, pos)
                pos -= step
                f.seek(pos)
                data = f.read(step) + data
                if len(data) > 64 * 1024 * 1024:
                    break
            text = data.decode("utf-8", errors="replace").splitlines()
            return text[-lines:], end
    except OSError:
        return [], 0


class FileFollower:
    """Yields batches of new lines appended to a file; copes with truncation
    and with the file being replaced (a unit restart rotates its log)."""

    def __init__(self, path, start_pos):
        self.path = path
        self.pos = start_pos
        self.inode = self._inode()
        self.partial = b""

    def _inode(self):
        try:
            return os.stat(self.path).st_ino
        except OSError:
            return None

    def poll(self):
        inode = self._inode()
        if inode is None:
            return [], False
        reset = False
        if inode != self.inode:
            self.inode, self.pos, self.partial, reset = inode, 0, b"", True
        try:
            size = os.path.getsize(self.path)
            if size < self.pos:
                self.pos, self.partial, reset = 0, b"", True
            if size == self.pos:
                return [], reset
            with open(self.path, "rb") as f:
                f.seek(self.pos)
                data = f.read(min(size - self.pos, 8 * 1024 * 1024))
                self.pos = f.tell()
        except OSError:
            return [], reset
        data = self.partial + data
        parts = data.split(b"\n")
        self.partial = parts.pop()
        return [p.decode("utf-8", errors="replace") for p in parts], reset

    def close(self):
        pass


class DockerFollower:
    def __init__(self, container):
        self.q = queue.Queue(maxsize=20000)
        self.proc = subprocess.Popen(["docker", "logs", "-f", "--tail", "0", container],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, text=True, errors="replace")
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            try:
                self.q.put_nowait(line)
            except queue.Full:
                pass

    def poll(self):
        out = []
        try:
            while len(out) < 20000:
                out.append(self.q.get_nowait())
        except queue.Empty:
            pass
        return out, False

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


class LogSources:
    def __init__(self, cfg, core_containers):
        self.cfg = cfg
        self.log_dir = cfg["log_dir"]
        self.core_containers = core_containers   # callable -> list of names

    def comp_path(self, cid):
        return os.path.join(self.log_dir, f"{cid}.log")

    def task_path(self, tid):
        return os.path.join(self.log_dir, f"task-{tid}.log")

    def listing(self):
        out = []
        if self.cfg.get("core"):
            systemd_core = self.cfg["core"].get("type") == "systemd"
            out.append({"id": "core", "group": "Core",
                        "name": "Core actions (systemctl)" if systemd_core else "Core actions (docker compose)",
                        "exists": os.path.exists(os.path.join(self.log_dir, "core.log"))})
            # a systemd core has no container logs: its log files go in extra_logs
            if not systemd_core:
                for name in self.core_containers():
                    out.append({"id": f"docker:{name}", "name": name, "group": "Core", "exists": True})
        for c in self.cfg["components"]:
            out.append({"id": f"comp:{c['id']}", "name": c["name"], "group": c["group"],
                        "exists": os.path.exists(self.comp_path(c["id"]))})
        for t in self.cfg["tasks"]:
            out.append({"id": f"task:{t['id']}", "name": t["name"], "group": "Tasks",
                        "exists": os.path.exists(self.task_path(t["id"]))})
        out.append({"id": "exporter", "name": self.cfg["metrics"]["name"], "group": "Dashboard",
                    "exists": os.path.exists(os.path.join(self.log_dir, "exporter.log"))})
        out.append({"id": "dashboard", "name": "Dashboard (this service)", "group": "Dashboard",
                    "exists": os.path.exists(os.path.join(self.log_dir, "dashboard.log"))})
        for x in self.cfg["extra_logs"]:
            out.append({"id": f"file:{x['id']}", "name": x["name"], "group": x.get("group", "Files"),
                        "exists": os.path.exists(x["path"])})
        return out

    def resolve(self, source):
        """Returns ('file', path) or ('docker', name); raises KeyError if unknown.
        Only sources from listing() are allowed: never a path from the request."""
        kind, _, key = source.partition(":")
        if kind == "comp" and any(c["id"] == key for c in self.cfg["components"]):
            return "file", self.comp_path(key)
        if kind == "task" and any(t["id"] == key for t in self.cfg["tasks"]):
            return "file", self.task_path(key)
        if source in ("core", "exporter", "dashboard"):
            return "file", os.path.join(self.log_dir, f"{source}.log")
        if (kind == "docker" and key in self.core_containers()
                and (self.cfg.get("core") or {}).get("type") != "systemd"):
            return "docker", key
        if kind == "file":
            for x in self.cfg["extra_logs"]:
                if x["id"] == key:
                    return "file", x["path"]
        raise KeyError(source)

    @staticmethod
    def render(lines):
        return [list(classify_line(l)) for l in lines]

    def tail(self, source, lines):
        kind, target = self.resolve(source)
        if kind == "file":
            text, _ = tail_file(target, lines)
            return self.render(text)
        rc, out = run(["docker", "logs", "--tail", str(lines), target], timeout=20)
        return self.render(out.splitlines()[-lines:])

    def stream(self, source, lines, alive):
        """Generator of ('batch', rendered lines, reset) for SSE; stops when alive() is False."""
        kind, target = self.resolve(source)
        if kind == "file":
            text, pos = tail_file(target, lines)
            yield self.render(text), True
            follower = FileFollower(target, pos)
        else:
            rc, out = run(["docker", "logs", "--tail", str(lines), target], timeout=20)
            yield self.render(out.splitlines()[-lines:]), True
            follower = DockerFollower(target)
        try:
            while alive():
                new, reset = follower.poll()
                if reset and kind == "file":
                    yield [["", "— log restarted (new run) —"]], True
                if new:
                    if len(new) > MAX_BATCH:
                        skipped = len(new) - MAX_BATCH
                        new = new[-MAX_BATCH:]
                        yield [["W", f"… {skipped} lines skipped (too fast) …"]], False
                    yield self.render(new), False
                else:
                    yield None, False
                time.sleep(0.4)
        finally:
            follower.close()
