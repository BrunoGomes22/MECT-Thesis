#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
UE_SLICE_MAP_FILE="${UE_SLICE_MAP_FILE:-${SCRIPT_DIR}/ue_slice_map.json}"

SERVER_IP="${SERVER_IP:-10.45.0.1}"
DURATION="${DURATION:-60}"
PRE_SLEEP="${PRE_SLEEP:-5}"
POST_SLEEP="${POST_SLEEP:-5}"

OUT_BASE="${OUT_BASE:-./results}"
RUN_ID="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="${OUT_BASE}/${RUN_ID}"

readonly -a UE_NAMES=(ue1 ue2 ue3 ue4 ue5 ue6)
readonly -a PRIORITIES=(best_effort medium high)

mkdir -p "$OUT_DIR"

MARKERS_FILE="${OUT_DIR}/scenario_markers.csv"
{
    printf 'scenario,start_ts,end_ts,be_offered,medium_offered,high_offered'
    for ue_ns in "${UE_NAMES[@]}"; do
        printf ',%s_id,%s_bw,%s_priority' "$ue_ns" "$ue_ns" "$ue_ns"
    done
    printf ',notes\n'
} > "$MARKERS_FILE"

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

rate_token() {
    local mbps="$1"

    if [[ "$mbps" == "0" || "$mbps" == "0.0" ]]; then
        printf '0'
    else
        printf '%sM' "$mbps"
    fi
}

write_scenario_marker() {
    local scenario="$1"
    local start_ts="$2"
    local end_ts="$3"
    local be_total_mbps="$4"
    local medium_total_mbps="$5"
    local high_total_mbps="$6"
    local notes="$7"

    {
        csv_field "$scenario"; printf ','
        csv_field "$start_ts"; printf ','
        csv_field "$end_ts"; printf ','
        csv_field "$(rate_token "$be_total_mbps")"; printf ','
        csv_field "$(rate_token "$medium_total_mbps")"; printf ','
        csv_field "$(rate_token "$high_total_mbps")"; printf ','

        local ue_ns
        for ue_ns in "${UE_NAMES[@]}"; do
            csv_field "${UE_ID[$ue_ns]}"; printf ','
            csv_field "${UE_BW[$ue_ns]}"; printf ','
            csv_field "${UE_PRIORITY[$ue_ns]}"; printf ','
        done

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

    # Extra cleanup: kill iperf3 clients inside all six UE namespaces.
    local ue_ns
    for ue_ns in "${UE_NAMES[@]}"; do
        sudo -n ip netns exec "$ue_ns" pkill -f iperf3 2>/dev/null || true
    done

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
# Namespace/port convention:
#   gnb_cu_ue_f1ap_id=0 -> ue1 / 5201
#   gnb_cu_ue_f1ap_id=1 -> ue2 / 5202
#   ...
#   gnb_cu_ue_f1ap_id=5 -> ue6 / 5206
#
# The population may be balanced (2/2/2) or skewed. Each priority must have
# at least one mapped UE. Scenario demands below are aggregate per-priority
# demands and are divided among however many UEs belong to that priority.
# --------------------------------------------------------------------

declare -A UE_PRIORITY=()
declare -A UE_ID=()
declare -A UE_BW=()
declare -A UE_PORT=(
    [ue1]=5201
    [ue2]=5202
    [ue3]=5203
    [ue4]=5204
    [ue5]=5205
    [ue6]=5206
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

    local parsed_map
    if ! parsed_map="$(python3 - "$UE_SLICE_MAP_FILE" <<'PY'
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

expected_ids = set(range(6))
parsed = {}

for ue in ues:
    try:
        ue_id = int(ue["gnb_cu_ue_f1ap_id"])
        sst = int(ue["sst"])
        sd = str(ue["sd"]).strip().lower()
    except Exception as exc:
        raise SystemExit(f"Invalid UE entry {ue!r}: {exc}")

    if ue_id in parsed:
        raise SystemExit(f"Duplicate gnb_cu_ue_f1ap_id in {path}: {ue_id}")

    if sst == 1 and sd == "ffffff":
        priority = "best_effort"
    elif sst == 1 and sd == "000001":
        priority = "medium"
    elif sst == 1 and sd == "000002":
        priority = "high"
    else:
        priority = "high"

    parsed[ue_id] = priority

actual_ids = set(parsed)
if actual_ids != expected_ids:
    missing = sorted(expected_ids - actual_ids)
    extra = sorted(actual_ids - expected_ids)
    details = []
    if missing:
        details.append(f"missing IDs {missing}")
    if extra:
        details.append(f"unexpected IDs {extra}")
    raise SystemExit(
        "Expected exactly six UEs with gnb_cu_ue_f1ap_id values 0 through 5; "
        + ", ".join(details)
    )

for ue_id in sorted(parsed):
    print(f"{ue_id},{parsed[ue_id]}")
PY
    )"; then
        log "ERROR: Failed to load six-UE slice mapping from $UE_SLICE_MAP_FILE"
        exit 1
    fi

    local ue_id
    local priority
    while IFS=, read -r ue_id priority; do
        local ue_ns="ue$((ue_id + 1))"
        UE_ID["$ue_ns"]="$ue_id"
        UE_PRIORITY["$ue_ns"]="$priority"
    done <<< "$parsed_map"

    local ue_ns
    for ue_ns in "${UE_NAMES[@]}"; do
        if [[ -z "${UE_PRIORITY[$ue_ns]:-}" || -z "${UE_ID[$ue_ns]:-}" ]]; then
            log "ERROR: No complete mapping found for $ue_ns in $UE_SLICE_MAP_FILE"
            exit 1
        fi
    done

    local required_priority
    for required_priority in "${PRIORITIES[@]}"; do
        local found=0
        for ue_ns in "${UE_NAMES[@]}"; do
            if [[ "${UE_PRIORITY[$ue_ns]}" == "$required_priority" ]]; then
                found=1
                break
            fi
        done

        if [[ "$found" -ne 1 ]]; then
            log "ERROR: No UE is mapped to priority '$required_priority'."
            log "The six-UE matrix requires at least one best_effort, one medium, and one high UE."
            exit 1
        fi
    done
}

priority_count() {
    local wanted_priority="$1"
    local count=0
    local ue_ns

    for ue_ns in "${UE_NAMES[@]}"; do
        if [[ "${UE_PRIORITY[$ue_ns]}" == "$wanted_priority" ]]; then
            count=$((count + 1))
        fi
    done

    printf '%d\n' "$count"
}

log_ue_priority_mapping() {
    log "UE slice/priority mapping from: $UE_SLICE_MAP_FILE"

    local ue_ns
    for ue_ns in "${UE_NAMES[@]}"; do
        log "  $ue_ns / F1AP ID ${UE_ID[$ue_ns]} / port ${UE_PORT[$ue_ns]} -> ${UE_PRIORITY[$ue_ns]}"
    done

    log "Priority population: best_effort=$(priority_count best_effort), medium=$(priority_count medium), high=$(priority_count high)"
    blank
}

reset_scenario_bandwidths() {
    local ue_ns
    for ue_ns in "${UE_NAMES[@]}"; do
        UE_BW["$ue_ns"]="0"
    done
}

allocate_priority_total() {
    local priority="$1"
    local total_mbps="$2"
    local distribution="$3"
    local -a members=()
    local ue_ns

    for ue_ns in "${UE_NAMES[@]}"; do
        if [[ "${UE_PRIORITY[$ue_ns]}" == "$priority" ]]; then
            members+=("$ue_ns")
        fi
    done

    if [[ "${#members[@]}" -eq 0 ]]; then
        log "ERROR: Cannot allocate ${total_mbps} Mbit/s: priority '$priority' has no UEs."
        exit 1
    fi

    local allocation_output
    if ! allocation_output="$(python3 - "$total_mbps" "${#members[@]}" "$distribution" <<'PY'
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import sys

try:
    total = Decimal(sys.argv[1])
    count = int(sys.argv[2])
except (InvalidOperation, ValueError) as exc:
    raise SystemExit(f"Invalid allocation input: {exc}")

distribution = sys.argv[3]
if total < 0:
    raise SystemExit("Offered bitrate cannot be negative")
if count <= 0:
    raise SystemExit("UE count must be positive")

if distribution == "equal":
    weights = [Decimal(1)] * count
elif distribution == "rank":
    weights = [Decimal(i) for i in range(1, count + 1)]
else:
    raise SystemExit(f"Unknown distribution: {distribution}")

weight_sum = sum(weights)
quantum = Decimal("0.000001")
allocations = []

# Round down every allocation except the last. Give the exact remainder to the
# last UE so the per-UE rates add up to the requested aggregate rate.
for weight in weights[:-1]:
    value = (total * weight / weight_sum).quantize(quantum, rounding=ROUND_DOWN)
    allocations.append(value)
allocations.append(total - sum(allocations))

for value in allocations:
    if value == 0:
        print("0")
    else:
        print(f"{format(value.normalize(), 'f')}M")
PY
    )"; then
        log "ERROR: Failed to allocate $total_mbps Mbit/s across priority '$priority'."
        exit 1
    fi

    local -a allocations=()
    mapfile -t allocations <<< "$allocation_output"

    if [[ "${#allocations[@]}" -ne "${#members[@]}" ]]; then
        log "ERROR: Allocation count mismatch for priority '$priority'."
        exit 1
    fi

    local index
    for ((index = 0; index < ${#members[@]}; index++)); do
        UE_BW["${members[$index]}"]="${allocations[$index]}"
    done
}

build_priority_scenario_bandwidths() {
    local be_total_mbps="$1"
    local medium_total_mbps="$2"
    local high_total_mbps="$3"
    local distribution="$4"

    reset_scenario_bandwidths
    allocate_priority_total best_effort "$be_total_mbps" "$distribution"
    allocate_priority_total medium "$medium_total_mbps" "$distribution"
    allocate_priority_total high "$high_total_mbps" "$distribution"
}

print_scenario_header() {
    local scenario="$1"
    local be_total_mbps="$2"
    local medium_total_mbps="$3"
    local high_total_mbps="$4"
    local notes="$5"

    reset_terminal
    blank
    log "============================================================"
    log "Scenario: $scenario"
    log "Aggregate offered load: best_effort=$(rate_token "$be_total_mbps"), medium=$(rate_token "$medium_total_mbps"), high=$(rate_token "$high_total_mbps")"

    local ue_ns
    for ue_ns in "${UE_NAMES[@]}"; do
        log "  ${ue_ns}=${UE_BW[$ue_ns]} (${UE_PRIORITY[$ue_ns]}, F1AP ID ${UE_ID[$ue_ns]})"
    done

    log "Notes: $notes"
    log "============================================================"
}

bandwidth_is_disabled() {
    local bw="${1,,}"
    [[ "$bw" == "0" || "$bw" == "0m" || "$bw" == "none" ]]
}

run_ue_dl() {
    local ue_ns="$1"
    local port="$2"
    local bw="$3"
    local scenario="$4"
    local logfile="${OUT_DIR}/${scenario}_${ue_ns}.log"

    if bandwidth_is_disabled "$bw"; then
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

    local pid
    for pid in "$@"; do
        if ! wait "$pid"; then
            failed=1
        fi
    done

    if [[ "$failed" -ne 0 ]]; then
        log "WARNING: one or more iperf3 clients failed. Check logs in $OUT_DIR"
    fi
}

run_priority_scenario_parallel() {
    local scenario="$1"
    local be_total_mbps="$2"
    local medium_total_mbps="$3"
    local high_total_mbps="$4"
    local distribution="$5"
    local notes="${6:-}"

    build_priority_scenario_bandwidths \
        "$be_total_mbps" \
        "$medium_total_mbps" \
        "$high_total_mbps" \
        "$distribution"

    print_scenario_header \
        "$scenario" \
        "$be_total_mbps" \
        "$medium_total_mbps" \
        "$high_total_mbps" \
        "$notes"

    log "Pre-sleep ${PRE_SLEEP}s..."
    sleep "$PRE_SLEEP"

    local start_ts
    start_ts="$(date +%s.%N)"

    local -a pids=()
    local ue_ns
    for ue_ns in "${UE_NAMES[@]}"; do
        run_ue_dl "$ue_ns" "${UE_PORT[$ue_ns]}" "${UE_BW[$ue_ns]}" "$scenario"
        if ! bandwidth_is_disabled "${UE_BW[$ue_ns]}"; then
            pids+=("$!")
        fi
    done

    wait_for_jobs "${pids[@]}"

    local end_ts
    end_ts="$(date +%s.%N)"

    write_scenario_marker \
        "$scenario" \
        "$start_ts" \
        "$end_ts" \
        "$be_total_mbps" \
        "$medium_total_mbps" \
        "$high_total_mbps" \
        "$notes"

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
for ue_ns in "${UE_NAMES[@]}"; do
    log "  iperf3 -s -p ${UE_PORT[$ue_ns]}"
done
blank

log "Also make sure your xApp is already running and logging CSV."
blank

read -rp "Press ENTER to start the test matrix..."

# --------------------------------------------------------------------
# A. Equal-demand load sweep
#
# These are per-priority aggregate demands. With a balanced 2/2/2 mapping,
# each mapped UE receives half of the displayed priority demand.
# --------------------------------------------------------------------

run_priority_scenario_parallel \
    "01_underload_equal" \
    "5" "5" "5" \
    "equal" \
    "Equal priority totals; aggregate offered load 15 Mbit/s."

run_priority_scenario_parallel \
    "02_near_capacity_equal" \
    "9" "9" "9" \
    "equal" \
    "Equal priority totals near cell capacity; aggregate offered load 27 Mbit/s."

run_priority_scenario_parallel \
    "03_moderate_overload_equal" \
    "10" "10" "10" \
    "equal" \
    "Equal priority totals above measured cell capacity; aggregate offered load 30 Mbit/s."

# --------------------------------------------------------------------
# B. Same total offered load, only heavy-demand priority changes
# --------------------------------------------------------------------

run_priority_scenario_parallel \
    "04_best_effort_heavy" \
    "14" "8" "8" \
    "equal" \
    "Best-effort-heavy demand at constant 30 Mbit/s aggregate offered load."

run_priority_scenario_parallel \
    "05_medium_heavy" \
    "8" "14" "8" \
    "equal" \
    "Medium-heavy demand at constant 30 Mbit/s aggregate offered load."

run_priority_scenario_parallel \
    "06_high_heavy" \
    "8" "8" "14" \
    "equal" \
    "High-priority-heavy demand at constant 30 Mbit/s aggregate offered load."

# --------------------------------------------------------------------
# C. Pairwise competition
# --------------------------------------------------------------------

run_priority_scenario_parallel \
    "07_pair_best_effort_high" \
    "15" "0" "15" \
    "equal" \
    "Pairwise competition: best effort versus high; total 30 Mbit/s."

run_priority_scenario_parallel \
    "08_pair_best_effort_medium" \
    "15" "15" "0" \
    "equal" \
    "Pairwise competition: best effort versus medium; total 30 Mbit/s."

run_priority_scenario_parallel \
    "09_pair_medium_high_optional" \
    "0" "15" "15" \
    "equal" \
    "Pairwise competition: medium versus high; total 30 Mbit/s."

# --------------------------------------------------------------------
# D. Asymmetric overload with all priorities active
# --------------------------------------------------------------------

run_priority_scenario_parallel \
    "10_overload_best_effort" \
    "12" "10" "8" \
    "equal" \
    "Best effort has the largest class demand; total 30 Mbit/s."

run_priority_scenario_parallel \
    "11_overload_medium" \
    "8" "12" "10" \
    "equal" \
    "Medium has the largest class demand; total 30 Mbit/s."

run_priority_scenario_parallel \
    "12_overload_high" \
    "8" "10" "12" \
    "equal" \
    "High has the largest class demand; total 30 Mbit/s."

# --------------------------------------------------------------------
# E. Heterogeneous per-UE demand with equal priority totals
#
# Within each priority, UEs ordered by namespace receive demand proportional
# to ranks 1..N. For a balanced 2/2/2 population this is approximately 3.33
# and 6.67 Mbit/s per pair, while every priority still offers 10 Mbit/s in
# aggregate.
# --------------------------------------------------------------------

run_priority_scenario_parallel \
    "13_heterogeneous_equal_aggregate" \
    "10" "10" "10" \
    "rank" \
    "Unequal per-UE demands by namespace rank (3.33/6.67 for 2 UEs); equal 10 Mbit/s priority totals and 30 Mbit/s overall."

blank
log "All scenarios completed."
log "Results saved in: $OUT_DIR"
log "Scenario markers: $MARKERS_FILE"
