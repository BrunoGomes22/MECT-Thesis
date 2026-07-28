#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
UE_SLICE_MAP_FILE="${UE_SLICE_MAP_FILE:-${SCRIPT_DIR}/ue_slice_map.json}"

SERVER_IP="${SERVER_IP:-192.168.81.178}"
DURATION="${DURATION:-30}"
PRE_SLEEP="${PRE_SLEEP:-5}"
POST_SLEEP="${POST_SLEEP:-5}"

OUT_BASE="${OUT_BASE:-./results}"
RUN_ID="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="${OUT_BASE}/${RUN_ID}"

mkdir -p "$OUT_DIR"

MARKERS_FILE="${OUT_DIR}/scenario_markers.csv"
echo "scenario,start_ts,end_ts,be_offered,medium_offered,high_offered,ue1_bw,ue1_priority,ue2_bw,ue2_priority,ue3_bw,ue3_priority,notes" > "$MARKERS_FILE"

log() {
    printf '\r\033[K%s\n' "$*"
}

blank() {
    printf '\r\033[K\n'
}

csv_field() {
    local value="${1:-}"
    value="${value//\"/\"\"}"
    printf '"%s"' "$value"
}

write_scenario_marker() {
    local scenario="$1"
    local start_ts="$2"
    local end_ts="$3"
    local ue1_bw="$4"
    local ue2_bw="$5"
    local ue3_bw="$6"
    local notes="$7"

    local be_offered="0"
    local medium_offered="0"
    local high_offered="0"

    case "${UE_PRIORITY[ue1]}" in
        best_effort) be_offered="$ue1_bw" ;;
        medium) medium_offered="$ue1_bw" ;;
        high) high_offered="$ue1_bw" ;;
    esac

    case "${UE_PRIORITY[ue2]}" in
        best_effort) be_offered="$ue2_bw" ;;
        medium) medium_offered="$ue2_bw" ;;
        high) high_offered="$ue2_bw" ;;
    esac

    case "${UE_PRIORITY[ue3]}" in
        best_effort) be_offered="$ue3_bw" ;;
        medium) medium_offered="$ue3_bw" ;;
        high) high_offered="$ue3_bw" ;;
    esac

    {
        csv_field "$scenario"; printf ','
        csv_field "$start_ts"; printf ','
        csv_field "$end_ts"; printf ','
        csv_field "$be_offered"; printf ','
        csv_field "$medium_offered"; printf ','
        csv_field "$high_offered"; printf ','
        csv_field "$ue1_bw"; printf ','
        csv_field "${UE_PRIORITY[ue1]}"; printf ','
        csv_field "$ue2_bw"; printf ','
        csv_field "${UE_PRIORITY[ue2]}"; printf ','
        csv_field "$ue3_bw"; printf ','
        csv_field "${UE_PRIORITY[ue3]}"; printf ','
        csv_field "$notes"; printf '\n'
    } >> "$MARKERS_FILE"
}


reset_terminal() {
    stty sane 2>/dev/null || true
    printf '\r\033[K'
}

cleanup() {
    blank
    log "Stopping test matrix..."

    # Kill background jobs started by this script.
    jobs -pr | xargs -r kill 2>/dev/null || true

    # Extra cleanup: kill iperf3 clients inside UE namespaces.
    sudo -n ip netns exec ue1 pkill -f iperf3 2>/dev/null || true
    sudo -n ip netns exec ue2 pkill -f iperf3 2>/dev/null || true
    sudo -n ip netns exec ue3 pkill -f iperf3 2>/dev/null || true

    reset_terminal
    log "Stopped."
    exit 130
}

trap cleanup INT TERM
trap reset_terminal EXIT

# --------------------------------------------------------------------
# UE priority mapping
# --------------------------------------------------------------------
# The script reads ue_slice_map.json from the same directory as this script.
# Mapping rule:
#   sst=1, sd=ffffff -> best_effort
#   sst=1, sd=000001 -> medium
#   sst=1, sd=000002 -> high
#   any other slice   -> high
#
# Namespace/port convention kept from the original script:
#   gnb_cu_ue_f1ap_id=0 -> ue1 / 5201
#   gnb_cu_ue_f1ap_id=1 -> ue2 / 5202
#   gnb_cu_ue_f1ap_id=2 -> ue3 / 5203
# --------------------------------------------------------------------

declare -A UE_PRIORITY=()
declare -A UE_PORT=(
    [ue1]=5201
    [ue2]=5202
    [ue3]=5203
)

load_ue_slice_map() {
    if [[ ! -f "$UE_SLICE_MAP_FILE" ]]; then
        log "ERROR: UE slice mapping file not found: $UE_SLICE_MAP_FILE"
        log "Place ue_slice_map.json in the same directory as this script, or set UE_SLICE_MAP_FILE=/path/to/file.json"
        exit 1
    fi

    if ! command -v python3 >/dev/null 2>&1; then
        log "ERROR: python3 is required to parse $UE_SLICE_MAP_FILE"
        exit 1
    fi

    while IFS=, read -r ue_id priority; do
        local ue_ns="ue$((ue_id + 1))"

        if [[ -z "${UE_PORT[$ue_ns]:-}" ]]; then
            log "ERROR: $UE_SLICE_MAP_FILE contains UE ID $ue_id, mapped to namespace $ue_ns, but this script only knows ue1, ue2, ue3."
            exit 1
        fi

        UE_PRIORITY["$ue_ns"]="$priority"
    done < <(python3 - "$UE_SLICE_MAP_FILE" <<'PY'
import json
import sys

path = sys.argv[1]

try:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
except Exception as exc:
    raise SystemExit(f"Failed to read JSON file {path}: {exc}")

ues = data.get("ues")
if not isinstance(ues, list):
    raise SystemExit("Invalid JSON: expected top-level key 'ues' containing a list")

for ue in ues:
    try:
        ue_id = int(ue["gnb_cu_ue_f1ap_id"])
        sst = int(ue["sst"])
        sd = str(ue["sd"]).lower()
    except Exception as exc:
        raise SystemExit(f"Invalid UE entry {ue!r}: {exc}")

    if sst == 1 and sd == "ffffff":
        priority = "best_effort"
    elif sst == 1 and sd == "000001":
        priority = "medium"
    elif sst == 1 and sd == "000002":
        priority = "high"
    else:
        priority = "high"

    print(f"{ue_id},{priority}")
PY
    )

    for ue_ns in ue1 ue2 ue3; do
        if [[ -z "${UE_PRIORITY[$ue_ns]:-}" ]]; then
            log "ERROR: No slice mapping found for $ue_ns in $UE_SLICE_MAP_FILE"
            log "Expected entries for gnb_cu_ue_f1ap_id values 0, 1, and 2."
            exit 1
        fi
    done
}

bw_for_ue_priority() {
    local ue_ns="$1"
    local best_effort_bw="$2"
    local medium_bw="$3"
    local high_bw="$4"

    case "${UE_PRIORITY[$ue_ns]}" in
        best_effort)
            printf '%s\n' "$best_effort_bw"
            ;;
        medium)
            printf '%s\n' "$medium_bw"
            ;;
        high)
            printf '%s\n' "$high_bw"
            ;;
        *)
            log "ERROR: Unknown priority '${UE_PRIORITY[$ue_ns]}' for $ue_ns"
            exit 1
            ;;
    esac
}

log_ue_priority_mapping() {
    log "UE slice/priority mapping from: $UE_SLICE_MAP_FILE"
    log "  ue1 / port 5201 -> ${UE_PRIORITY[ue1]}"
    log "  ue2 / port 5202 -> ${UE_PRIORITY[ue2]}"
    log "  ue3 / port 5203 -> ${UE_PRIORITY[ue3]}"
    blank
}

print_scenario_header() {
    local scenario="$1"
    local ue1_bw="$2"
    local ue2_bw="$3"
    local ue3_bw="$4"
    local notes="$5"

    reset_terminal
    blank
    log "============================================================"
    log "Scenario: $scenario"
    log "UE1=$ue1_bw (${UE_PRIORITY[ue1]}) UE2=$ue2_bw (${UE_PRIORITY[ue2]}) UE3=$ue3_bw (${UE_PRIORITY[ue3]})"
    log "Notes: $notes"
    log "============================================================"
}

run_ue_dl() {
    local ue_ns="$1"
    local port="$2"
    local bw="$3"
    local scenario="$4"
    local logfile="${OUT_DIR}/${scenario}_${ue_ns}.log"

    if [[ "$bw" == "0" || "$bw" == "none" ]]; then
        return 0
    fi

    log "Starting ${ue_ns} (${UE_PRIORITY[$ue_ns]}): ${bw} on port ${port}"

    sudo -n ip netns exec "$ue_ns" iperf3 \
        -c "$SERVER_IP" \
        -p "$port" \
        -u \
        -b "$bw" \
        -t "$DURATION" \
        -R \
        --json \
        > "$logfile" 2>&1 < /dev/null &
}

wait_for_jobs() {
    local failed=0

    for pid in "$@"; do
        if ! wait "$pid"; then
            failed=1
        fi
    done

    if [[ "$failed" -ne 0 ]]; then
        log "WARNING: one or more iperf3 clients failed. Check logs in $OUT_DIR"
    fi
}

run_scenario_parallel() {
    local scenario="$1"
    local ue1_bw="$2"
    local ue2_bw="$3"
    local ue3_bw="$4"
    local notes="$5"

    print_scenario_header "$scenario" "$ue1_bw" "$ue2_bw" "$ue3_bw" "$notes"

    log "Pre-sleep ${PRE_SLEEP}s..."
    sleep "$PRE_SLEEP"

    local start_ts
    start_ts="$(date +%s.%N)"

    local pids=()

    run_ue_dl "ue1" "${UE_PORT[ue1]}" "$ue1_bw" "$scenario"
    [[ "$ue1_bw" != "0" && "$ue1_bw" != "none" ]] && pids+=("$!")

    run_ue_dl "ue2" "${UE_PORT[ue2]}" "$ue2_bw" "$scenario"
    [[ "$ue2_bw" != "0" && "$ue2_bw" != "none" ]] && pids+=("$!")

    run_ue_dl "ue3" "${UE_PORT[ue3]}" "$ue3_bw" "$scenario"
    [[ "$ue3_bw" != "0" && "$ue3_bw" != "none" ]] && pids+=("$!")

    wait_for_jobs "${pids[@]}"

    local end_ts
    end_ts="$(date +%s.%N)"

    write_scenario_marker "$scenario" "$start_ts" "$end_ts" "$ue1_bw" "$ue2_bw" "$ue3_bw" "$notes"

    reset_terminal
    log "Post-sleep ${POST_SLEEP}s..."
    sleep "$POST_SLEEP"
}

run_priority_scenario_parallel() {
    local scenario="$1"
    local best_effort_bw="$2"
    local medium_bw="$3"
    local high_bw="$4"
    local notes="$5"

    local ue1_bw
    local ue2_bw
    local ue3_bw

    ue1_bw="$(bw_for_ue_priority "ue1" "$best_effort_bw" "$medium_bw" "$high_bw")"
    ue2_bw="$(bw_for_ue_priority "ue2" "$best_effort_bw" "$medium_bw" "$high_bw")"
    ue3_bw="$(bw_for_ue_priority "ue3" "$best_effort_bw" "$medium_bw" "$high_bw")"

    run_scenario_parallel "$scenario" "$ue1_bw" "$ue2_bw" "$ue3_bw" "$notes"
}

run_scenario_staggered() {
    local scenario="$1"
    local ue1_bw="$2"
    local ue2_bw="$3"
    local ue3_bw="$4"
    local stagger="$5"
    local notes="$6"

    print_scenario_header "$scenario" "$ue1_bw" "$ue2_bw" "$ue3_bw" "$notes"
    log "Staggered starts every ${stagger}s"

    log "Pre-sleep ${PRE_SLEEP}s..."
    sleep "$PRE_SLEEP"

    local start_ts
    start_ts="$(date +%s.%N)"

    local pids=()

    run_ue_dl "ue1" "${UE_PORT[ue1]}" "$ue1_bw" "$scenario"
    [[ "$ue1_bw" != "0" && "$ue1_bw" != "none" ]] && pids+=("$!")

    sleep "$stagger"

    run_ue_dl "ue2" "${UE_PORT[ue2]}" "$ue2_bw" "$scenario"
    [[ "$ue2_bw" != "0" && "$ue2_bw" != "none" ]] && pids+=("$!")

    sleep "$stagger"

    run_ue_dl "ue3" "${UE_PORT[ue3]}" "$ue3_bw" "$scenario"
    [[ "$ue3_bw" != "0" && "$ue3_bw" != "none" ]] && pids+=("$!")

    wait_for_jobs "${pids[@]}"

    local end_ts
    end_ts="$(date +%s.%N)"

    write_scenario_marker "$scenario" "$start_ts" "$end_ts" "$ue1_bw" "$ue2_bw" "$ue3_bw" "$notes"

    reset_terminal
    log "Post-sleep ${POST_SLEEP}s..."
    sleep "$POST_SLEEP"
}

load_ue_slice_map

log "Checking sudo access..."
sudo -v
log "Sudo OK."
blank

log "Results directory: $OUT_DIR"
log "Server IP: $SERVER_IP"
log "Duration per test: ${DURATION}s"
blank

log_ue_priority_mapping

log "Make sure these iperf3 servers are running on $SERVER_IP:"
log "  iperf3 -s -p 5201"
log "  iperf3 -s -p 5202"
log "  iperf3 -s -p 5203"
blank

log "Also make sure your xApp is already running and logging CSV."
blank

read -rp "Press ENTER to start the test matrix..."

# ------------------------------------------------------------
# A. Single-UE calibration and one-active-UE behaviour
# ------------------------------------------------------------

run_priority_scenario_parallel \
    "00a_single_best_effort" \
    "35M" "0" "0" \
    "Single best-effort UE. Expected: active_count=1, cap=100%; measure standalone capacity."

run_priority_scenario_parallel \
    "00b_single_medium" \
    "0" "35M" "0" \
    "Single medium UE. Expected: active_count=1, cap=100%; measure standalone capacity."

run_priority_scenario_parallel \
    "00c_single_high" \
    "0" "0" "35M" \
    "Single high UE. Expected: active_count=1, cap=100%; measure standalone capacity."

# ------------------------------------------------------------
# B. Equal-demand load sweep
# ------------------------------------------------------------

run_priority_scenario_parallel \
    "01_underload_equal" \
    "5M" "5M" "5M" \
    "15M total. Expected: all offered traffic delivered; caps need not be 100%."

run_priority_scenario_parallel \
    "02_near_capacity_equal" \
    "9M" "9M" "9M" \
    "27M total. Expected: demands mostly satisfied; higher PRB pressure than underload."

run_priority_scenario_parallel \
    "03_moderate_overload_equal" \
    "15M" "15M" "15M" \
    "45M total. Expected: cell saturation, increased delay/loss and stronger caps."

run_priority_scenario_parallel \
    "04_saturated_equal" \
    "35M" "35M" "35M" \
    "All UEs backlogged. Expected: high cap >= medium cap >= best-effort cap."

# ------------------------------------------------------------
# C. Same total offered load, only heavy-demand priority changes
# ------------------------------------------------------------

run_priority_scenario_parallel \
    "05_best_effort_heavy" \
    "35M" "5M" "5M" \
    "Best-effort is the heavy source. Tests strongest throttling and possible utilization tradeoff."

run_priority_scenario_parallel \
    "06_medium_heavy" \
    "5M" "35M" "5M" \
    "Medium is the heavy source. Expected behaviour between best-effort-heavy and high-heavy."

run_priority_scenario_parallel \
    "07_high_heavy" \
    "5M" "5M" "35M" \
    "High is the heavy source. Expected: no high-priority ceiling and highest aggregate utilization."

# ------------------------------------------------------------
# D. Pairwise competition
# ------------------------------------------------------------

run_priority_scenario_parallel \
    "08_pair_best_effort_high" \
    "35M" "0" "35M" \
    "Two backlogged UEs. Most important pairwise priority-protection test."

run_priority_scenario_parallel \
    "09_pair_best_effort_medium" \
    "35M" "35M" "0" \
    "Tests whether the best-effort ceiling releases resources to medium."

run_priority_scenario_parallel \
    "10_pair_medium_high_optional" \
    "0" "35M" "35M" \
    "Negative/control case: medium cap may not bind because its natural share is below 60%."

# --------------------------------------------------------------------
# Staggered dynamic scenario
# --------------------------------------------------------------------

#run_scenario_staggered \
#    "10_staggered_equal_30M" \
#    "30M" "30M" "30M" \
#    "10" \
#    "UE1 starts first, then UE2 after 10s, then UE3 after 10s."

blank
log "All scenarios completed."
log "Results saved in: $OUT_DIR"
log "Scenario markers: $MARKERS_FILE"
