#!/usr/bin/env bash
# Stop the all-in-one srsRAN network started by start_srsran_local.sh
# (UEs, broker, DU, CU). Open5GS and the RIC are left running.
#
# Usage: sudo ./run_local/stop_srsran_local.sh [--quiet]
set -uo pipefail

QUIET="${1:-}"
log() { [[ "$QUIET" == --quiet ]] || printf '[%s] %s\n' "$(date +%T)" "$*"; }

[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }

stop() { # pattern, name
    if pgrep -f "$1" >/dev/null; then
        log "stopping $2"
        pkill -INT -f "$1"
        for _ in $(seq 10); do pgrep -f "$1" >/dev/null || return 0; sleep 1; done
        pkill -KILL -f "$1"
    fi
}

stop "srsue/src/srsue" "srsUEs"
stop "multi_ue_scenario_headless" "broker"
pkill -f "sleep infinity" 2>/dev/null || true
stop "apps/du/srsdu" "DU"
stop "apps/cu/srscu" "CU"
log "stopped"
