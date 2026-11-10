#!/bin/bash
#
# Installs the srsRAN testbed dashboard as the systemd service "srsran-dashboard"
# (separate from the OAI "testbed-dashboard": other port, units srsdash-*).
#
# Usage: sudo ./install.sh            install (or update) and start it
#        sudo ./install.sh uninstall  stop and remove the service (the testbed
#                                     components it started keep running)

set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
  echo "Run with sudo: sudo $0 $*" >&2
  exit 1
fi

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# runs as root: do not leave root-owned __pycache__ in the repo
export PYTHONDONTWRITEBYTECODE=1
rm -rf "$DIR/tbdash/__pycache__"
UNIT=/etc/systemd/system/srsran-dashboard.service

if [[ "${1:-}" == "uninstall" ]]; then
  systemctl disable --now srsran-dashboard.service 2>/dev/null || true
  rm -f "$UNIT"
  systemctl daemon-reload
  echo "Service removed. Components still running: systemctl list-units 'srsdash-*'"
  exit 0
fi

python3 -c "import yaml, psutil, websocket" 2>/dev/null || {
  echo "Missing Python modules: sudo apt install python3-yaml python3-psutil python3-websocket" >&2
  exit 1
}
python3 - "$DIR" <<'EOF'
import sys
sys.path.insert(0, sys.argv[1])
from tbdash import config
cfg = config.load(sys.argv[1] + "/config.yaml")
print(f"config ok: {len(cfg['components'])} components, {len(cfg['tasks'])} tasks")
EOF

cat > "$UNIT" <<EOF
[Unit]
Description=srsRAN testbed dashboard (status, logs, metrics and control)
Documentation=file://$DIR/README.md
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $DIR/server.py --config $DIR/config.yaml
Restart=on-failure
RestartSec=3
# SIGTERM only to the server, which stops its exporter xApp itself. The
# testbed components run in their own units (srsdash-*) and keep running.
KillMode=mixed
TimeoutStopSec=20
Environment=PYTHONUNBUFFERED=1
# runs as root: do not leave root-owned __pycache__ in the repo
Environment=PYTHONDONTWRITEBYTECODE=1

[Install]
WantedBy=multi-user.target
EOF

mkdir -p /var/log/srsran-dashboard
systemctl daemon-reload
systemctl enable srsran-dashboard.service >/dev/null
systemctl restart srsran-dashboard.service
sleep 2
systemctl --no-pager --lines=5 status srsran-dashboard.service || true

PORT=$(python3 - "$DIR" <<'EOF'
import sys
sys.path.insert(0, sys.argv[1])
from tbdash import config
c = config.load(sys.argv[1] + "/config.yaml")["server"]
print(f"{c['host']}:{c['port']}")
EOF
)
echo
echo "Dashboard at http://$PORT/"
