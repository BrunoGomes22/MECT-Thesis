#!/usr/bin/env python3
"""Summarise a traffic_phases.sh run, one row per phase and UE.

Reads <run_dir>/phases.csv, iperf_<phase>_ueN.log (receiver side, 1 s
intervals) and, when present, du_sched.csv / du_rlc.csv from the DU metrics
logger. The first SKIP_S seconds of each phase are left out (transient).

Prints a table and writes <run_dir>/summary.csv.

Usage: analyze_phases.py <run_dir> [skip_s]
"""
import csv
import os
import re
import statistics as st
import sys

RUN = sys.argv[1].rstrip("/")
if RUN == "latest":   # newest run in test_results/local_traffic
    _base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "test_results", "local_traffic")
    _runs = sorted(d for d in os.listdir(_base) if os.path.isfile(os.path.join(_base, d, "phases.csv")))
    if not _runs:
        sys.exit(f"no runs in {_base}")
    RUN = os.path.join(_base, _runs[-1])
    print(f"run: {RUN}")
SKIP_S = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
UES = [("ue1", "low"), ("ue2", "medium"), ("ue3", "high")]
UNIT = {"": 1e-6, "K": 1e-3, "M": 1.0, "G": 1e3}
LINE = re.compile(r"\]\s+([\d.]+)-([\d.]+)\s+sec\s+[\d.]+ \w?Bytes\s+([\d.]+) (\w?)bits/sec"
                  r"\s+[\d.]+ ms\s+(-?\d+)/(\d+)")


def read_phases():
    with open(os.path.join(RUN, "phases.csv")) as f:
        rows = list(csv.DictReader(f))
    phases = []
    for cur, nxt in zip(rows, rows[1:]):
        if cur["phase"] in ("end", "stop"):
            continue
        phases.append({
            "name": cur["phase"], "start": float(cur["time_s"]), "end": float(nxt["time_s"]),
            "offered": [float(cur["low_mbps"]), float(cur["med_mbps"]), float(cur["high_mbps"])],
        })
    return phases


def iperf(phase, ue):
    path = os.path.join(RUN, f"iperf_{phase}_{ue}.log")
    if not os.path.exists(path):
        return None
    thp, lost, total = [], 0, 0
    with open(path, errors="ignore") as f:
        for line in f:
            m = LINE.search(line)
            if not m:
                continue
            t0, t1 = float(m.group(1)), float(m.group(2))
            if t1 - t0 > 1.01 or t0 < SKIP_S:
                continue
            thp.append(float(m.group(3)) * UNIT[m.group(4)])
            lost += max(int(m.group(5)), 0)
            total += int(m.group(6))
    if not thp:
        return {"error": True}
    return {"thp": st.mean(thp), "thp_min": min(thp), "thp_max": max(thp),
            "loss_pct": 100.0 * lost / total if total else 0.0, "error": False}


def load_csv(name):
    path = os.path.join(RUN, name)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


def mean_of(rows, key):
    vals = [float(r[key]) for r in rows if r.get(key) not in ("", None, "None")]
    return st.mean(vals) if vals else None


def main():
    phases = read_phases()
    sched = load_csv("du_sched.csv")
    rlc = load_csv("du_rlc.csv")
    out = []

    hdr = (f"{'phase':8s} {'UE':4s} {'prio':6s} {'offered':>7s} {'served':>7s} {'%':>6s} "
           f"{'loss%':>6s} {'mcs':>5s} {'cqi':>4s} {'dl_bs kB':>9s} {'rlc ms':>7s}")
    print(hdr)
    print("-" * len(hdr))
    for ph in phases:
        lo, hi = ph["start"] + SKIP_S, ph["end"]
        total_off = total_srv = 0.0
        for (ue, prio), offered in zip(UES, ph["offered"]):
            if offered == 0:
                continue
            r = iperf(ph["name"], ue)
            s_rows = [x for x in sched if x["ue"] == ue and lo <= float(x["wall_time"]) <= hi]
            r_rows = [x for x in rlc if x["ue"] == ue and lo <= float(x["wall_time"]) <= hi]
            mcs, cqi = mean_of(s_rows, "dl_mcs"), mean_of(s_rows, "cqi")
            bs = mean_of(s_rows, "dl_bs_bytes")
            n = sum(float(x["tx_num_sdus"] or 0) for x in r_rows)
            lat = sum(float(x["tx_sum_sdu_latency_us"] or 0) for x in r_rows)
            delay = lat / n / 1000 if n else None
            row = {"phase": ph["name"], "ue": ue, "priority": prio, "offered_mbps": offered,
                   "served_mbps": None, "served_pct": None, "loss_pct": None,
                   "thp_min": None, "thp_max": None, "dl_mcs": mcs, "cqi": cqi,
                   "dl_bs_kB": bs / 1000 if bs is not None else None, "rlc_delay_ms": delay,
                   "iperf_error": r is None or r["error"]}
            if r and not r["error"]:
                row.update(served_mbps=r["thp"], served_pct=100 * r["thp"] / offered,
                           loss_pct=r["loss_pct"], thp_min=r["thp_min"], thp_max=r["thp_max"])
                total_off += offered
                total_srv += r["thp"]
            out.append(row)

            def fmt(v, w, p=1):
                return f"{v:{w}.{p}f}" if isinstance(v, (int, float)) else f"{'-':>{w}s}"
            srv = fmt(row["served_mbps"], 7) if not row["iperf_error"] else f"{'FAIL':>7s}"
            print(f"{ph['name']:8s} {ue:4s} {prio:6s} {offered:7.1f} {srv} {fmt(row['served_pct'], 6)} "
                  f"{fmt(row['loss_pct'], 6)} {fmt(mcs, 5)} {fmt(cqi, 4)} {fmt(row['dl_bs_kB'], 9)} "
                  f"{fmt(delay, 7)}")
        if total_off:
            print(f"{ph['name']:8s} {'all':4s} {'':6s} {total_off:7.1f} {total_srv:7.1f} "
                  f"{100 * total_srv / total_off:6.1f}")
        print()

    with open(os.path.join(RUN, "summary.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    print(f"summary: {os.path.join(RUN, 'summary.csv')}")


if __name__ == "__main__":
    main()
