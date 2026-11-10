#!/usr/bin/env bash
# Downlink UDP traffic in phases for the all-in-one srsRAN network (one UE per
# slice), the srsRAN counterpart of ~/xApp-Development/qlearning_traffic.sh.
#
# Each UE runs "iperf3 -u -R" in its namespace against an iperf3 server on
# 10.45.0.1 (ogstun): ue1 = low (port 5201), ue2 = medium (5202),
# ue3 = high (5203). Rates are Mbit/s of UDP payload.
#
# Default phases: the OAI phases scaled by 0.96 (56.7 / 59 Mbit/s of cell
# capacity; 56.7 measured here with 3 UEs, 30 Mbit/s each, 2026-11-10), so each
# phase loads the cell by the same fraction as on OAI:
#   A  19/30/10 (59, 104%)   B  43/10/11 (64, 113%)
#   C  19/19/38 (76, 134%)   A2 = A
#
# Usage:
#   sudo ./run_local/traffic_phases.sh                      # A, B, C, A2, 300 s each
#   sudo PHASE_SECS=60 ./run_local/traffic_phases.sh        # shorter phases
#   sudo PHASES="CAP 30 30 30" PHASE_SECS=60 ./run_local/traffic_phases.sh   # capacity test
#
# Output in $OUT_BASE/<date>_<time>/:
#   phases.csv     phase start times (time_s,phase,low_mbps,med_mbps,high_mbps)
#   du_sched.csv   per-UE DU scheduler metrics every second (run_local/log_du_metrics.py)
#   du_rlc.csv     per-DRB RLC metrics (queueing delay)
#   iperf_<phase>_ueN.log  iperf3 client reports (1 s intervals, receiver side)
set -euo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE_SECS="${PHASE_SECS:-300}"
SERVER_IP="${SERVER_IP:-10.45.0.1}"
OUT_BASE="${OUT_BASE:-$REPO/test_results/local_traffic}"
PHASES="${PHASES:-A 19 30 10;B 43 10 11;C 19 19 38;A2 19 30 10}"

log() { printf '[%s] %s\n' "$(date +%T)" "$*"; }
die() { log "ERROR: $*"; exit 1; }

[[ $EUID -eq 0 ]] || die "run with sudo (iperf3 runs inside the UE namespaces)"
for i in 1 2 3; do
    ip netns exec "ue$i" ip -4 -o addr show tun_srsue 2>/dev/null | grep -q inet \
        || die "ue$i has no IP: start the network first (sudo ./run_local/start_srsran_local.sh)"
done

OUT_DIR="$OUT_BASE/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUT_DIR"
OWNER="${SUDO_USER:-root}"
# The metrics logger runs as $OWNER and writes here.
chown -R "$OWNER" "$OUT_BASE"
PHASE_LOG="$OUT_DIR/phases.csv"
echo "time_s,phase,low_mbps,med_mbps,high_mbps" >"$PHASE_LOG"
mark() { echo "$(date +%s.%3N),$1,$2,$3,$4" >>"$PHASE_LOG"; }

SERVER_PIDS=()
LOGGER_PID=""
stop_clients() {
    for i in 1 2 3; do ip netns exec "ue$i" pkill -x iperf3 2>/dev/null || true; done
}
cleanup() {
    stop_clients
    stop_servers
    [[ -n "$LOGGER_PID" ]] && kill "$LOGGER_PID" 2>/dev/null || true
    mark stop 0 0 0
    chown -R "$OWNER" "$OUT_DIR" 2>/dev/null || true
    log "stopped; results in $OUT_DIR"
}
trap 'cleanup; exit 0' INT TERM

# iperf3 servers on the core side (host), one per UE, started fresh for every
# phase: iperf3 3.9 refuses the next client ("unable to send control message:
# Bad file descriptor") after a UDP -R test, so a server is never reused.
start_servers() { # phase name
    SERVER_PIDS=()
    for port in 5201 5202 5203; do
        iperf3 -s -1 -B "$SERVER_IP" -p "$port" >"$OUT_DIR/iperf_server_${1}_$port.log" 2>&1 &
        SERVER_PIDS+=($!)
    done
    sleep 1
}
stop_servers() {
    for p in "${SERVER_PIDS[@]}"; do kill "$p" 2>/dev/null || true; done
    SERVER_PIDS=()
}

# DU metrics logger (runs as the normal user).
sudo -u "$OWNER" python3 "$REPO/run_local/log_du_metrics.py" "$OUT_DIR" >"$OUT_DIR/logger.out" 2>&1 &
LOGGER_PID=$!
sleep 2

IFS=';' read -ra PHASE_LIST <<<"$PHASES"
TOTAL=$(( ${#PHASE_LIST[@]} * PHASE_SECS ))
log "results: $OUT_DIR"
log "${#PHASE_LIST[@]} phases x ${PHASE_SECS} s = $((TOTAL / 60)) min (Ctrl+C stops)"

for p in "${PHASE_LIST[@]}"; do
    read -r name low med high <<<"$p"
    log "phase $name: low=${low}M medium=${med}M high=${high}M for ${PHASE_SECS}s"
    start_servers "$name"
    mark "$name" "$low" "$med" "$high"
    i=0
    for rate in "$low" "$med" "$high"; do
        i=$((i + 1))
        if [[ "$rate" != 0 ]]; then
            ip netns exec "ue$i" iperf3 -c "$SERVER_IP" -p "520$i" -u -R -b "${rate}M" \
                -t "$PHASE_SECS" -i 1 --forceflush >"$OUT_DIR/iperf_${name}_ue$i.log" 2>&1 &
        fi
    done
    sleep "$PHASE_SECS"
    stop_clients
    stop_servers
    sleep 1
    for i in 1 2 3; do
        grep -q "error" "$OUT_DIR/iperf_${name}_ue$i.log" 2>/dev/null \
            && log "WARNING: ue$i iperf3 failed in phase $name: $(grep -m1 -o 'error.*' "$OUT_DIR/iperf_${name}_ue$i.log")"
    done
done

mark end 0 0 0
cleanup
