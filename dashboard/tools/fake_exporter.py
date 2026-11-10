#!/usr/bin/env python3
"""Stand-in for xapp_dashboard_exporter: prints the same JSON lines with
made-up but plausible numbers (3 UEs in slices 0/1/2), to develop or demo
the dashboard without a RIC. Point metrics.cmd at it in a test config."""

import json
import math
import random
import sys
import time

PERIOD = 0.1
UES = [  # rnti, slice, sst, mean DL Mbit/s, mean UL Mbit/s
    (0x4601, 0, 1, 20.0, 2.0),
    (0x8a3c, 1, 2, 31.0, 1.0),
    (0xc117, 2, 3, 10.0, 0.5),
]


def out(obj):
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main():
    start = time.time()
    out({"t": "nodes", "rx": int(start * 1e6), "nodes": [
        {"type": 7, "type_name": "gNB-DU", "nb_id": 3584, "mcc": 1, "mnc": 1, "cu_du_id": 3584,
         "ran_funcs": [2, 3, 142, 143, 144, 145, 148]},
        {"type": 5, "type_name": "gNB-CU", "nb_id": 3584, "mcc": 1, "mnc": 1,
         "ran_funcs": [2, 3, 144, 148]}]})
    state = {r: {"dl": 0, "ul": 0, "dprb": 0, "uprb": 0, "dd": 0} for r, *_ in UES}
    n = 0
    last_hb = 0.0
    while True:
        now = time.time()
        t = now - start
        ues, rbs = [], []
        for rnti, sl, sst, dl_mean, ul_mean in UES:
            s = state[rnti]
            phase = int(t // 60) % 3
            boost = 1.5 if phase == sl else 1.0
            dl = max(0.0, dl_mean * boost * (1 + 0.15 * math.sin(t / 7 + sl)) + random.gauss(0, 1.5))
            ul = max(0.0, ul_mean * (1 + 0.2 * math.sin(t / 11 + sl)) + random.gauss(0, 0.1))
            s["dl"] += int(dl * 1e6 / 8 * PERIOD)
            s["ul"] += int(ul * 1e6 / 8 * PERIOD)
            s["dprb"] += int(min(106, dl * 1.2) * 1600 * PERIOD)
            s["uprb"] += int(min(106, ul * 4) * 600 * PERIOD)
            s["dd"] += int(max(0.0, dl - 30) * 1e3 * PERIOD)
            snr = 22 + 3 * math.sin(t / 20 + sl) + random.gauss(0, 0.4)
            ues.append({
                "rnti": rnti, "frame": n % 1024, "dl_tbs": s["dl"], "ul_tbs": s["ul"],
                "dl_sdu": 0, "ul_sdu": 0, "dl_prb": s["dprb"], "ul_prb": s["uprb"],
                "dl_retx_prb": 0, "ul_retx_prb": 0, "dl_sched_rb": 30, "ul_sched_rb": 5,
                "dl_mcs": max(0, min(28, int(snr + random.gauss(0, 0.6)))),
                "ul_mcs": max(0, min(28, int(snr - 4))), "wb_cqi": 0,
                "pusch_snr": round(snr, 1), "pucch_snr": round(snr + 2, 1),
                "dl_bler": round(max(0.0, 0.05 + random.gauss(0, 0.01)), 4),
                "ul_bler": round(max(0.0, 0.03 + random.gauss(0, 0.01)), 4),
                "bsr": random.randint(0, 3000), "phr": 40,
                "rsrp": -44 - sl, "sinr": round(snr + 1.5, 1),
                "slice": sl, "sst": sst,
                "dl_harq": [n * 10, n, 0, 0, 0], "ul_harq": [n * 5, 0, 0, 0, 0]})
            buf = max(0, int((dl - 25) * 4000)) if dl > 25 else random.randint(0, 2000)
            rbs.append({"rnti": rnti, "rb": 1, "mode": 0, "tx_buf": buf, "tx_buf_pkts": buf // 1400,
                        "rx_buf": 0, "tx_sdu": s["dl"], "rx_sdu": s["ul"], "tx_pdu": s["dl"],
                        "rx_pdu": s["ul"], "tx_dd": s["dd"], "rx_dd": 0, "tx_retx": 0,
                        "sojourn_us": round(500 + buf / 20 + random.random() * 300, 1),
                        "hol_us": int(buf / 10)})
        ts = int(now * 1e6)
        out({"t": "mac", "ts": ts, "rx": ts, "ues": ues})
        out({"t": "rlc", "ts": ts, "rx": ts, "rbs": rbs})
        if now - last_hb >= 2:
            out({"t": "hb", "rx": ts, "mac_subs": 1, "rlc_subs": 1, "mac_ind": n * 10, "rlc_ind": n * 10})
            last_hb = now
        n += 1
        time.sleep(PERIOD)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, BrokenPipeError):
        pass
