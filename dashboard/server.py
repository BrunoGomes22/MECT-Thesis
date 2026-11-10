#!/usr/bin/env python3
"""Testbed dashboard: status, logs, metrics and control of the 5G testbed
(core, nearRT-RIC, CU/DU, UEs, xApps). See README.md.

Usage: sudo python3 server.py [--config config.yaml]
"""

import argparse
import logging
import logging.handlers
import os
import signal
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tbdash import config as config_mod  # noqa: E402
from tbdash.manager import Manager       # noqa: E402
from tbdash.web import serve             # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", help="config file (default: config.yaml next to server.py)")
    ap.add_argument("--port", type=int, help="override server.port")
    ap.add_argument("--log-dir", help="override log_dir")
    args = ap.parse_args()

    path = config_mod.find_config(args.config)
    try:
        cfg = config_mod.load(path)
    except (OSError, config_mod.ConfigError) as e:
        print(f"config {path}: {e}", file=sys.stderr)
        return 2
    if args.port:
        cfg["server"]["port"] = args.port
    if args.log_dir:
        cfg["log_dir"] = args.log_dir
    os.makedirs(cfg["log_dir"], exist_ok=True)

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(threadName)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.handlers.RotatingFileHandler(os.path.join(cfg["log_dir"], "dashboard.log"),
                                              maxBytes=5 * 1024 * 1024, backupCount=2)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)

    if os.geteuid() != 0:
        logging.warning("not running as root: starting/stopping components will fail")

    mgr = Manager(cfg)
    mgr.start()
    httpd = serve(cfg, mgr)

    done = threading.Event()

    def stop(signum, frame):
        logging.info("signal %d: stopping (the testbed components keep running)", signum)
        done.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    done.wait()
    httpd.shutdown()
    mgr.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
