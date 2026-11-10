#!/usr/bin/env bash
# One-off downlink UDP traffic to the 3 UEs (one phase of traffic_phases.sh),
# with the same logs: test_results/local_traffic/<date>_<time>/.
#
# Usage: sudo ./run_local/iperf_manual.sh <low Mbit/s> <medium Mbit/s> <high Mbit/s> <seconds>
#        (0 Mbit/s = no traffic to that UE)
set -euo pipefail
REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
[[ $# -eq 4 ]] || { echo "usage: $0 <low> <medium> <high> <seconds>"; exit 1; }
for v in "$@"; do [[ "$v" =~ ^[0-9]+(\.[0-9]+)?$ ]] || { echo "not a number: $v"; exit 1; }; done
PHASES="MANUAL $1 $2 $3" PHASE_SECS="$4" exec "$REPO/run_local/traffic_phases.sh"
