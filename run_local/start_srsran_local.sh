#!/usr/bin/env bash
# Start the all-in-one simulated srsRAN network on this host:
#   Open5GS (systemd, must already be configured) + OSC near-RT RIC (docker)
#   + srsRAN CU + DU (ZMQ, 20 MHz, 106 PRBs) + GNU Radio broker + 3 srsUEs.
# One UE per slice: ue1 low (SST 1), ue2 medium (SST 1/SD 1), ue3 high (SST 1/SD 2).
#
# Usage:  sudo ./run_local/start_srsran_local.sh
# Stop:   sudo ./run_local/stop_srsran_local.sh
#
# The 3 UEs start before the broker (it needs all of them to move samples).
# srsUE always sends preamble 0 and the 3 UEs see the cell at the same
# instant, so their Msg3 collide. The broker gives them 0/10/20 dB of path
# loss while they attach (the strongest Msg3 still decodes, so they attach in
# order ue1, ue2, ue3 = F1AP IDs 0/1/2, as gnb_confs/local/ue_slice_map.json
# assumes) and puts all back to 0 dB after 45 s.
#
# Process output goes to $OUT_DIR/<name>.out; the DU log is /tmp/du.log.
set -euo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SRSRAN_PROJECT="${SRSRAN_PROJECT:-/home/testbedpc-a/srsRAN_Project/build}"
SRSUE="${SRSUE:-/home/testbedpc-a/srsRAN_4G/build/srsue/src/srsue}"
RIC_DIR="${RIC_DIR:-/home/testbedpc-a/oran-sc-ric}"
OUT_DIR="${OUT_DIR:-/tmp/srsran_local}"
UE_ATTACH_TIMEOUT="${UE_ATTACH_TIMEOUT:-90}"
STOP_OAI="${STOP_OAI:-1}"   # 1: stop the OAI RAN units of the testbed dashboard (CPU)

CU_CFG="$REPO/gnb_confs/local/cu_local.yml"
DU_CFG="$REPO/gnb_confs/local/du_local_zmq_3ue_20mhz.yml"
BROKER="$REPO/multi-ue-broker_scripts/multi_ue_scenario_headless_3_20mhz.py"
UE_CFG_DIR="$REPO/srsue_confs/srsue_20mhz"

log() { printf '[%s] %s\n' "$(date +%T)" "$*"; }
die() { log "ERROR: $*"; exit 1; }

[[ $EUID -eq 0 ]] || die "run with sudo"
mkdir -p "$OUT_DIR"

# --- clean start -------------------------------------------------------------
if [[ "$STOP_OAI" == 1 ]]; then
    for u in tbdash-ue1 tbdash-ue2 tbdash-ue3 tbdash-du tbdash-cu; do
        if systemctl is-active --quiet "$u"; then
            log "stopping OAI unit $u (STOP_OAI=0 to keep it)"
            systemctl stop "$u"
        fi
    done
fi
"$REPO/run_local/stop_srsran_local.sh" --quiet || true

# --- prerequisites ------------------------------------------------------------
for s in open5gs-amfd open5gs-smfd open5gs-upfd open5gs-nssfd open5gs-nrfd; do
    systemctl is-active --quiet "$s" || die "$s is not active (systemctl status $s)"
done
log "Open5GS active"

# Restart the RIC on every run: after the DU restarts, e2mgr still holds the
# old E2 node and answers the new E2 Setup Request (same node ID) with an
# E2 Setup Failure. RIC_RESTART=0 keeps a running RIC (only if the DU did not
# connect to it before).
if [[ "${RIC_RESTART:-1}" == 1 ]] || ! docker ps --format '{{.Names}}' | grep -qx ric_e2term; then
    log "(re)starting OSC near-RT RIC"
    (cd "$RIC_DIR" && docker compose down >/dev/null 2>&1; docker compose up -d >/dev/null 2>&1) \
        || die "RIC did not start (cd $RIC_DIR && docker compose up)"
    sleep 10
fi
ip -4 addr show | grep -q "10.0.2.1/" || die "RIC bridge 10.0.2.1 missing (docker compose ps in $RIC_DIR)"
log "RIC up"

for i in 1 2 3; do
    ip netns list | grep -qw "ue$i" || ip netns add "ue$i"
done

# --- helpers ------------------------------------------------------------------
start_bg() { # name, command...
    local name="$1"; shift
    setsid nohup "$@" >"$OUT_DIR/$name.out" 2>&1 </dev/null &
    echo $! >"$OUT_DIR/$name.pid"
}

wait_for() { # timeout_s, description, test command...
    local t="$1" what="$2"; shift 2
    for ((s = 0; s < t; s++)); do
        if "$@" >/dev/null 2>&1; then return 0; fi
        sleep 1
    done
    die "timeout waiting for $what"
}

alive() { kill -0 "$(cat "$OUT_DIR/$1.pid")" 2>/dev/null; }

# --- CU -----------------------------------------------------------------------
# srscu buffers stdout when it goes to a file, so check the SCTP association
# to the AMF (127.0.0.5:38412) instead of waiting for its "N2: ... completed" line.
cu_amf_up() { ss -Hn --sctp state established | grep -q " 127.0.0.5:38412"; }
log "starting CU"
start_bg cu stdbuf -oL "$SRSRAN_PROJECT/apps/cu/srscu" -c "$CU_CFG"
wait_for 20 "CU to connect to the AMF (see $OUT_DIR/cu.out)" cu_amf_up
alive cu || die "CU exited, see $OUT_DIR/cu.out"
log "CU connected to AMF"

# --- DU -----------------------------------------------------------------------
: >/tmp/du.log
log "starting DU"
start_bg du stdbuf -oL "$SRSRAN_PROJECT/apps/du/srsdu" -c "$DU_CFG"
wait_for 30 "F1 Setup" grep -q "F1 Setup: Procedure completed successfully" /tmp/du.log
wait_for 30 "E2 Setup" grep -q "E2 Setup procedure successful" /tmp/du.log
log "DU: F1 Setup and E2 Setup done"

# --- UEs, then broker ------------------------------------------------------------
# The broker sums the uplink of all 3 UEs and only moves samples when every UE
# socket delivers, so the 3 UEs must be running before it starts (a UE started
# later would leave the cell without samples until it appears).
ue_ip() { ip netns exec "ue$1" ip -4 -o addr show tun_srsue 2>/dev/null | awk '{print $4}'; }
ue_has_ip() { [[ -n "$(ue_ip "$1")" ]]; }
all_ues_have_ip() { ue_has_ip 1 && ue_has_ip 2 && ue_has_ip 3; }

for i in 1 2 3; do
    log "starting ue$i"
    start_bg "ue$i" stdbuf -oL "$SRSUE" "$UE_CFG_DIR/ue${i}_zmq.conf"
done
sleep 3

# The broker waits for Enter on stdin; feed it a pipe that never closes.
log "starting broker (attach path loss 0/10/20 dB, back to 0 dB after 45 s)"
setsid nohup bash -c "sleep infinity | exec python3 '$BROKER'" >"$OUT_DIR/broker.out" 2>&1 </dev/null &
echo $! >"$OUT_DIR/broker.pid"
sleep 3
alive broker || die "broker exited, see $OUT_DIR/broker.out"

log "waiting for the 3 UEs to attach (up to ${UE_ATTACH_TIMEOUT}s)"
wait_for "$UE_ATTACH_TIMEOUT" "all UEs to get an IP (see $OUT_DIR/ue*.out)" all_ues_have_ip
for i in 1 2 3; do log "ue$i attached: $(ue_ip "$i")"; done

log "network up. DU log: /tmp/du.log, process output: $OUT_DIR/"
log "metrics: websocket ws://127.0.0.1:8001 ({\"cmd\":\"metrics_subscribe\"})"
