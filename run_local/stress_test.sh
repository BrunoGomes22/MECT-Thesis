#!/usr/bin/env bash
# Cell stress test for the all-in-one srsRAN network: raises the offered
# downlink load in steps until the cell saturates, then analyses the run.
#
#   1. equal load on the 3 UEs: 10, 13, 16, 17, 18, 19, 20, 22, 25, 30 Mbit/s
#      each (30 to 90 Mbit/s in total; capacity measured before: ~56.7)
#   2. one UE alone at 70 Mbit/s: maximum for a single UE (all PRBs to one UE)
#
# Usage:  sudo ./run_local/stress_test.sh            # 30 s per step, ~6 min
#         sudo STEP_SECS=60 ./run_local/stress_test.sh
#
# Results: test_results/local_traffic/<date>_<time>/ (same files as
# traffic_phases.sh) plus summary.csv from analyze_phases.py.
set -euo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
STEP_SECS="${STEP_SECS:-30}"

[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }

PHASES=""
for r in 10 13 16 17 18 19 20 22 25 30; do
    PHASES+="EQ$((3 * r)) $r $r $r;"
done
PHASES+="SINGLE 70 0 0"

export PHASES PHASE_SECS="$STEP_SECS" OUT_BASE="$REPO/test_results/local_traffic"
before=$(ls -1d "$OUT_BASE"/*/ 2>/dev/null | sort | tail -1 || true)
"$REPO/run_local/traffic_phases.sh"
run_dir=$(ls -1d "$OUT_BASE"/*/ | sort | tail -1)
[[ "$run_dir" != "$before" ]] || { echo "no new run directory"; exit 1; }

sudo -u "${SUDO_USER:-root}" python3 "$REPO/run_local/analyze_phases.py" "$run_dir"
