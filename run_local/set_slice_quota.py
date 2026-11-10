#!/usr/bin/env python3
"""Changes the PRB quota of a slice at run time through the DU's WebSocket
command "rrm_policy_ratio_set" (srsRAN 25.10). The slice must be declared in
the DU config (cell_cfg.slicing); min/max are % of the 106 PRBs, per slot.
min is a guarantee (unused PRBs go to the other slices), max a hard cap.

Usage: set_slice_quota.py <low|medium|high|all> <min %> <max %> [ws_url]
       set_slice_quota.py all 0 100        # back to no slicing
"""
import json
import sys

import websocket

SLICES = {  # name -> rRMPolicyMember (PLMN 00101, SST 1)
    "low": {"plmn": "00101", "sst": 1},             # no SD
    "medium": {"plmn": "00101", "sst": 1, "sd": 1},
    "high": {"plmn": "00101", "sst": 1, "sd": 2},
}


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 1
    which, lo, hi = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    url = sys.argv[4] if len(sys.argv) > 4 else "ws://127.0.0.1:8001"
    if which not in list(SLICES) + ["all"]:
        print(f"slice must be one of {', '.join(SLICES)}, all")
        return 1
    if not (0 <= lo <= hi <= 100 and hi >= 1):
        print("need 0 <= min <= max <= 100 and max >= 1")
        return 1
    ws = websocket.create_connection(url, timeout=5)
    ok = True
    for name in (SLICES if which == "all" else [which]):
        cmd = {"cmd": "rrm_policy_ratio_set",
               "policies": {"resourceType": "PRB", "rRMPolicyMemberList": [SLICES[name]],
                            "min_prb_policy_ratio": lo, "max_prb_policy_ratio": hi}}
        ws.send(json.dumps(cmd))
        # skip metric messages until the command's reply
        while True:
            reply = json.loads(ws.recv())
            if reply.get("cmd") == "rrm_policy_ratio_set":
                break
        good = "error" not in reply
        ok &= good
        print(f"{name:6s} min {lo:3d}% max {hi:3d}% -> {'ok' if good else reply.get('error', reply)}")
    ws.close()
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
