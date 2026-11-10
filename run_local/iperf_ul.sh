#!/usr/bin/env bash
# Uplink UDP traffic from one UE to an iperf3 server on 10.45.0.1 (ogstun).
# Port 5301-5303 (ue1-ue3), so it does not clash with the downlink scripts.
#
# Usage: sudo ./run_local/iperf_ul.sh <ue1|ue2|ue3> <Mbit/s> <seconds>
set -euo pipefail
[[ $# -eq 3 ]] || { echo "usage: $0 <ue1|ue2|ue3> <Mbit/s> <seconds>"; exit 1; }
ns="$1" rate="$2" secs="$3"
[[ "$ns" =~ ^ue[1-3]$ ]] || { echo "UE must be ue1, ue2 or ue3"; exit 1; }
[[ "$rate" =~ ^[0-9]+(\.[0-9]+)?$ && "$secs" =~ ^[0-9]+$ ]] || { echo "rate and seconds must be numbers"; exit 1; }
port="530${ns#ue}"

iperf3 -s -1 -B 10.45.0.1 -p "$port" >/dev/null 2>&1 &
server=$!
trap 'kill $server 2>/dev/null || true' EXIT
sleep 1
ip netns exec "$ns" iperf3 -c 10.45.0.1 -p "$port" -u -b "${rate}M" -t "$secs" -i 1 --forceflush
