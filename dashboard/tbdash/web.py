"""HTTP server: static UI, JSON API and server-sent events.

The service runs as root and its buttons start and kill processes, so every
request is checked:
  - Host header must be a known name of this machine (blocks DNS rebinding);
  - POSTs need the X-Testbed-Dashboard header and JSON, which a foreign web
    page cannot send without a CORS preflight that is never granted;
  - optional HTTP basic auth (required by config.py when not on loopback)."""

import base64
import gzip
import hmac
import json
import mimetypes
import os
import queue
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .util import log

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def _local_names(extra):
    names = {"localhost", "127.0.0.1", "::1", "[::1]", socket.gethostname(), socket.getfqdn()}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            names.add(info[4][0])
    except OSError:
        pass
    try:
        import psutil
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family in (socket.AF_INET, socket.AF_INET6):
                    names.add(a.address.split("%")[0])
    except Exception:
        pass
    names.update(extra)
    return {n.lower() for n in names}


class Handler(BaseHTTPRequestHandler):
    server_version = "testbed-dashboard"
    protocol_version = "HTTP/1.0"

    # set by serve()
    mgr = None
    cfg = None
    allowed_hosts = set()
    auth = None

    def log_message(self, fmt, *args):
        pass

    # ------------------------------------------------------------ checks

    def _host_ok(self):
        host = (self.headers.get("Host") or "").strip().lower()
        if host.startswith("["):
            host = host[1:host.find("]")] if "]" in host else host
        else:
            host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        return host in self.allowed_hosts

    def _auth_ok(self):
        if not self.auth:
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            user, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
        except (ValueError, UnicodeDecodeError):
            return False
        return (hmac.compare_digest(user, self.auth["user"])
                and hmac.compare_digest(pw, self.auth["password"]))

    def _guard(self, write=False):
        if not self._host_ok():
            self._send(421, {"error": "host not allowed"})
            return False
        if not self._auth_ok():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="testbed-dashboard"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        if write:
            if self.headers.get("X-Testbed-Dashboard") != "1":
                self._send(403, {"error": "missing header"})
                return False
            origin = self.headers.get("Origin")
            if origin:
                o = urlparse(origin)
                if (o.hostname or "").lower() not in self.allowed_hosts:
                    self._send(403, {"error": "origin not allowed"})
                    return False
        return True

    # ----------------------------------------------------------- helpers

    def _send(self, code, obj, gz=False):
        body = json.dumps(obj, separators=(",", ":"), allow_nan=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        if gz and len(body) > 4096 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            body = gzip.compress(body, 5)
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, rel):
        path = os.path.realpath(os.path.join(STATIC_DIR, rel))
        if not path.startswith(STATIC_DIR + os.sep) or not os.path.isfile(path):
            self._send(404, {"error": "not found"})
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _sse_start(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _sse(self, event, data):
        payload = json.dumps(data, separators=(",", ":"), allow_nan=False, default=str)
        self.wfile.write(f"event: {event}\ndata: {payload}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0 or length > 64 * 1024:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    # ------------------------------------------------------------ routes

    def do_GET(self):
        if not self._guard():
            return
        url = urlparse(self.path)
        q = parse_qs(url.query)
        path = url.path
        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/api/state":
                return self._send(200, self.mgr.state)
            if path == "/api/config":
                return self._send(200, self.mgr.ui_config())
            if path == "/api/metrics/history":
                secs = min(int(q.get("seconds", ["600"])[0]), self.cfg["metrics"]["history_s"])
                return self._send(200, {"samples": self.mgr.store.history_since(secs)}, gz=True)
            if path == "/api/logs/sources":
                return self._send(200, {"sources": self.mgr.logs.listing()})
            if path == "/api/logs/tail":
                lines = max(1, min(int(q.get("lines", ["500"])[0]), 20000))
                return self._send(200, {"lines": self.mgr.logs.tail(q.get("source", [""])[0], lines)},
                                  gz=True)
            if path == "/api/events":
                return self._events()
            if path == "/api/logs/stream":
                lines = max(1, min(int(q.get("lines", ["500"])[0]), 5000))
                return self._log_stream(q.get("source", [""])[0], lines)
            self._send(404, {"error": "not found"})
        except KeyError:
            self._send(404, {"error": "unknown source"})
        except ValueError:
            self._send(400, {"error": "bad request"})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        if not self._guard(write=True):
            return
        body = self._body()
        if not isinstance(body, dict):
            return self._send(400, {"error": "invalid JSON"})
        path = urlparse(self.path).path
        mgr = self.mgr
        if path == "/api/component":
            ok, msg = mgr.component_action(str(body.get("id", "")), str(body.get("action", "")),
                                           body.get("params"))
        elif path == "/api/core/container":
            ok, msg = mgr.core_action("restart_container", str(body.get("name", "")))
        elif path == "/api/task":
            ok, msg = mgr.task_action(str(body.get("id", "")), str(body.get("action", "")),
                                      body.get("params"))
        elif path == "/api/orchestrate":
            ok, msg = mgr.orchestrate(str(body.get("action", "")))
        else:
            return self._send(404, {"error": "not found"})
        log.info("POST %s %s -> %s %s", path, json.dumps(body)[:200], ok, msg)
        self._send(200 if ok else 409, {"ok": ok, "msg": msg})

    # --------------------------------------------------------------- SSE

    def _events(self):
        q = self.mgr.events.subscribe()
        try:
            self._sse_start()
            self._sse("state", self.mgr.state)
            while True:
                try:
                    event, data = q.get(timeout=15)
                    self._sse(event, data)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.mgr.events.unsubscribe(q)

    def _log_stream(self, source, lines):
        self.mgr.logs.resolve(source)          # KeyError -> 404 before headers
        self._sse_start()
        alive = [True]
        last_ping = time.time()
        gen = self.mgr.logs.stream(source, lines, lambda: alive[0])
        try:
            for batch, reset in gen:
                if batch is not None:   # the first batch may be empty: send it anyway
                    self._sse("lines", {"lines": batch, "reset": reset})
                elif time.time() - last_ping > 15:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_ping = time.time()
        except (BrokenPipeError, ConnectionResetError, OSError):
            alive[0] = False
        finally:
            gen.close()   # stops the docker logs follower, if any


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler):
        if ":" in addr[0]:
            self.address_family = socket.AF_INET6
        super().__init__(addr, handler)


def serve(cfg, mgr):
    srv_cfg = cfg["server"]
    Handler.mgr = mgr
    Handler.cfg = cfg
    Handler.auth = srv_cfg.get("auth")
    Handler.allowed_hosts = _local_names([h.lower() for h in srv_cfg["allowed_hosts"]])
    httpd = Server((srv_cfg["host"], srv_cfg["port"]), Handler)
    log.info("listening on http://%s:%d", srv_cfg["host"], srv_cfg["port"])
    thread = threading.Thread(target=httpd.serve_forever, name="http", daemon=True)
    thread.start()
    return httpd
