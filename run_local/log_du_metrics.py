#!/usr/bin/env python3
"""Log the srsRAN DU JSON metrics (WebSocket, remote_control) to CSV.

Writes two files in <out_dir>:
  du_sched.csv  one row per UE per scheduler report (1 s): throughput, buffer,
                MCS, CQI, HARQ counters
  du_rlc.csv    one row per DRB per RLC report: SDUs, bytes, summed SDU latency
                (mean queueing delay = sum_sdu_latency_us / num_sdus, which
                avoids the per-UE DRB.RlcSduDelayDl bug of the KPM provider)

UEs are named ue1..ue3 from the c-rnti printed in /tmp/srsran_local/ueN.out
and get their slice from gnb_confs/local/ue_slice_map.json (DU UE index =
F1AP ID with the staggered attach of start_srsran_local.sh).

Usage: log_du_metrics.py <out_dir> [ws_url]
"""
import csv
import json
import os
import re
import signal
import sys
import time

import websocket

OUT_DIR = sys.argv[1]
URL = sys.argv[2] if len(sys.argv) > 2 else "ws://127.0.0.1:8001"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UE_OUT = "/tmp/srsran_local/ue{}.out"
SLICE_MAP = os.path.join(REPO, "gnb_confs/local/ue_slice_map.json")
PRIORITY = {"ffffff": "low", "000001": "medium", "000002": "high"}

SCHED_FIELDS = ["wall_time", "du_timestamp", "ue", "slice", "priority", "rnti", "cqi", "dl_mcs",
                "dl_brate_bps", "dl_bs_bytes", "dl_nof_ok", "dl_nof_nok", "ul_brate_bps", "ul_mcs", "bsr"]
RLC_FIELDS = ["wall_time", "du_timestamp", "ue_index", "ue", "slice", "priority", "drb_id",
              "tx_num_sdus", "tx_num_sdu_bytes", "tx_sum_sdu_latency_us", "tx_mean_sdu_delay_ms",
              "tx_num_dropped_sdus", "tx_num_discarded_sdus"]


def rnti_to_ue():
    """Map c-rnti -> ueN from the srsUE output files."""
    result = {}
    for i in (1, 2, 3):
        try:
            with open(UE_OUT.format(i), errors="ignore") as f:
                m = re.findall(r"c-rnti=0x([0-9a-fA-F]+)", f.read())
            if m:
                result[int(m[-1], 16)] = f"ue{i}"
        except OSError:
            pass
    return result


def index_to_slice():
    """Map DU UE index (F1AP ID) -> SD from the static slice map."""
    try:
        with open(SLICE_MAP) as f:
            return {u["gnb_cu_ue_f1ap_id"]: u["sd"] for u in json.load(f)["ues"]}
    except (OSError, ValueError, KeyError):
        return {}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    sched_f = open(os.path.join(OUT_DIR, "du_sched.csv"), "w", newline="", buffering=1)
    rlc_f = open(os.path.join(OUT_DIR, "du_rlc.csv"), "w", newline="", buffering=1)
    sched_w = csv.DictWriter(sched_f, SCHED_FIELDS)
    rlc_w = csv.DictWriter(rlc_f, RLC_FIELDS)
    sched_w.writeheader()
    rlc_w.writeheader()

    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    rnti_map = rnti_to_ue()
    slice_map = index_to_slice()
    # ueN -> DU index: ue1/ue2/ue3 attach in that order (F1AP IDs 0/1/2).
    ue_index = {f"ue{i + 1}": i for i in range(3)}
    index_ue = {v: k for k, v in ue_index.items()}

    ws = websocket.create_connection(URL, timeout=5)
    ws.send(json.dumps({"cmd": "metrics_subscribe"}))
    print(f"logging {URL} -> {OUT_DIR}", flush=True)

    while running:
        try:
            msg = json.loads(ws.recv())
        except websocket.WebSocketTimeoutException:
            continue
        except (websocket.WebSocketConnectionClosedException, ConnectionError, OSError):
            print("websocket closed", flush=True)
            break
        now = time.time()
        ts = msg.get("timestamp", "")

        if "cells" in msg:
            for cell in msg["cells"]:
                for ue in cell.get("ue_list", []):
                    rnti = ue.get("rnti")
                    if rnti not in rnti_map:
                        rnti_map = rnti_to_ue()
                    name = rnti_map.get(rnti, f"rnti_{rnti:#x}" if rnti is not None else "?")
                    sd = slice_map.get(ue_index.get(name), "")
                    sched_w.writerow({
                        "wall_time": f"{now:.3f}", "du_timestamp": ts, "ue": name, "slice": sd,
                        "priority": PRIORITY.get(sd, ""), "rnti": rnti, "cqi": ue.get("cqi"),
                        "dl_mcs": ue.get("dl_mcs"), "dl_brate_bps": ue.get("dl_brate"),
                        "dl_bs_bytes": ue.get("dl_bs"), "dl_nof_ok": ue.get("dl_nof_ok"),
                        "dl_nof_nok": ue.get("dl_nof_nok"), "ul_brate_bps": ue.get("ul_brate"),
                        "ul_mcs": ue.get("ul_mcs"), "bsr": ue.get("bsr"),
                    })

        if "rlc_metrics" in msg:
            r = msg["rlc_metrics"]
            tx = r.get("tx", {})
            idx = r.get("ue_id")
            name = index_ue.get(idx, f"index_{idx}")
            sd = slice_map.get(idx, "")
            n = tx.get("num_sdus", 0) or 0
            lat = tx.get("sum_sdu_latency_us", 0) or 0
            rlc_w.writerow({
                "wall_time": f"{now:.3f}", "du_timestamp": ts, "ue_index": idx, "ue": name, "slice": sd,
                "priority": PRIORITY.get(sd, ""), "drb_id": r.get("drb_id"),
                "tx_num_sdus": n, "tx_num_sdu_bytes": tx.get("num_sdu_bytes"),
                "tx_sum_sdu_latency_us": lat,
                "tx_mean_sdu_delay_ms": f"{lat / n / 1000:.3f}" if n else "",
                "tx_num_dropped_sdus": tx.get("num_dropped_sdus"),
                "tx_num_discarded_sdus": tx.get("num_discarded_sdus"),
            })

    ws.close()
    sched_f.close()
    rlc_f.close()


if __name__ == "__main__":
    main()
