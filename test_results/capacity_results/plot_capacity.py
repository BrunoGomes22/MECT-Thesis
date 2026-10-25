#!/usr/bin/env python3
"""
Cell capacity analysis for the srsRAN testbed.

Run it from the directory that contains the test folders:

    capacity_test_3ue_10mhz/   capacity_test_6ue_10mhz/   capacity_test_6ue_20mhz/

Each test folder holds one sub-folder per run, each with a capacity_summary.csv.

For every configuration the script:
  1. takes, in each run, the mean total received throughput over the stages whose
     per-UE offered rate lies in the configured saturation range (stages with a
     failed iperf3 client are excluded);
  2. averages these per-run means over all runs (the capacity of the cell);
  3. writes one PDF figure with received vs. offered load.

It also prints a summary and writes capacity_results.csv.

Usage:  python3 plot_capacity.py [directory]     (default: current directory)
Needs:  matplotlib  (pip install matplotlib)
"""

import csv
import glob
import os
import statistics
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# ----------------------------------------------------------------------------
# Configurations: folder name -> number of UEs, bandwidth, per-UE averaging range
# ----------------------------------------------------------------------------
CONFIGS = [
    {"folder": "capacity_test_3ue_10mhz", "ues": 3, "bw": 10, "range": (9.6667, 11.3333),
     "pdf": "saturation_3ue_10mhz.pdf"},
    {"folder": "capacity_test_6ue_10mhz", "ues": 6, "bw": 10, "range": (4.7, 5.2),
     "pdf": "saturation_6ue_10mhz.pdf"},
    {"folder": "capacity_test_6ue_20mhz", "ues": 6, "bw": 20, "range": (9.5, 11.0),
     "pdf": "saturation_6ue_20mhz.pdf"},
]
TOL = 1e-3  # tolerance when comparing per-UE rates such as 9.6667

# ----------------------------------------------------------------------------
# Figure style (print-oriented, recessive axes, one accent colour)
# ----------------------------------------------------------------------------
ACCENT = "#2a78d6"     # mean across runs
RUN_GREY = "#8a8984"   # individual runs (context)
REF_GREY = "#b8b7b1"   # reference lines and band
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"

plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Latin Modern Roman", "CMU Serif", "DejaVu Serif"],
    "mathtext.fontset": "cm",
    "font.size": 10,
    "axes.labelsize": 10,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "legend.fontsize": 9,
    "axes.edgecolor": TEXT_2,
    "axes.labelcolor": TEXT,
    "xtick.color": TEXT_2,
    "ytick.color": TEXT_2,
    "axes.spines.top": False,
    "axes.spines.right": False,
})


# ----------------------------------------------------------------------------
# Reading the CSV files (both column layouts are supported)
# ----------------------------------------------------------------------------
def first_key(row, names):
    for n in names:
        if n in row and row[n] not in ("", None):
            return n
    raise KeyError(f"none of the columns {names} found")


def read_run(path):
    """Return a list of stages: dict(per_ue, offered, received, failed)."""
    stages = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            per_ue = float(row[first_key(row, ["per_ue_offered_mbps"])])
            offered = float(row[first_key(row, ["total_offered_mbps", "offered_mbps"])])
            received = float(row[first_key(row, ["total_rx_mbps", "aggregate_rx_mbps"])])
            failed = int(float(row.get("failed_clients", 0) or 0))
            failed += int(float(row.get("parse_failures", 0) or 0))
            ue_rx = []
            k = 1
            while f"ue{k}_rx" in row or f"ue{k}_rx_mbps" in row:
                ue_rx.append(float(row.get(f"ue{k}_rx_mbps", row.get(f"ue{k}_rx"))))
                k += 1
            stages.append({"per_ue": per_ue, "offered": offered, "received": received,
                           "ue_rx": ue_rx, "failed": failed > 0})
    return stages


def in_range(value, lo, hi):
    return lo - TOL <= value <= hi + TOL


# ----------------------------------------------------------------------------
# Analysis
# ----------------------------------------------------------------------------
def analyse(base, cfg):
    files = sorted(glob.glob(os.path.join(base, cfg["folder"], "*", "capacity_summary.csv")))
    if not files:
        print(f"[skip] {cfg['folder']}: no capacity_summary.csv found")
        return None

    runs = [read_run(f) for f in files]
    lo, hi = cfg["range"]

    per_run_means = []
    excluded = 0
    for stages in runs:
        pts = [s["received"] for s in stages if in_range(s["per_ue"], lo, hi) and not s["failed"]]
        excluded += sum(1 for s in stages if in_range(s["per_ue"], lo, hi) and s["failed"])
        if pts:
            per_run_means.append(statistics.mean(pts))

    # Per-UE received throughput in the averaging range (same two-step average)
    n_ue = len(runs[0][0]["ue_rx"])
    per_ue = []
    for u in range(n_ue):
        run_means = []
        for stages in runs:
            pts = [s["ue_rx"][u] for s in stages if in_range(s["per_ue"], lo, hi) and not s["failed"]]
            if pts:
                run_means.append(statistics.mean(pts))
        per_ue.append(statistics.mean(run_means))

    capacity = statistics.mean(per_run_means)
    sd = statistics.stdev(per_run_means) if len(per_run_means) > 1 else 0.0
    return {"cfg": cfg, "runs": runs, "per_run_means": per_run_means,
            "capacity": capacity, "sd": sd, "excluded": excluded, "per_ue": per_ue}


# ----------------------------------------------------------------------------
# Figure
# ----------------------------------------------------------------------------
def plot(res, out_dir):
    cfg, runs = res["cfg"], res["runs"]
    lo, hi = cfg["range"]
    n = cfg["ues"]

    fig, ax = plt.subplots(figsize=(5.2, 3.9))

    # Axis limits: same range on both axes so that "received = offered" is a 45 deg line
    values = [s["offered"] for r in runs for s in r] + [s["received"] for r in runs for s in r]
    vmin, vmax = min(values), max(values)
    pad = 0.06 * (vmax - vmin)
    lim = (vmin - pad, vmax + pad)
    ax.set_xlim(lim)
    ax.set_ylim(lim)

    # Averaging range (shaded band)
    ax.axvspan(lo * n - TOL, hi * n + TOL, color=REF_GREY, alpha=0.18, lw=0, zorder=0)
    ax.text((lo + hi) / 2 * n, lim[1] - 0.02 * (lim[1] - lim[0]), "averaging range",
            ha="center", va="top", fontsize=8.5, color=TEXT_2)

    # Reference line: received = offered
    ax.plot(lim, lim, ls=(0, (4, 3)), lw=1.0, color=REF_GREY, zorder=1)

    # Individual runs (grey) and failed stages (crosses)
    for i, stages in enumerate(runs):
        ok = [s for s in stages if not s["failed"]]
        ax.plot([s["offered"] for s in ok], [s["received"] for s in ok],
                color=RUN_GREY, lw=0.8, alpha=0.6, marker="o", ms=3, zorder=2,
                label="Individual runs" if i == 0 else None)
    failed = [s for r in runs for s in r if s["failed"]]
    if failed:
        ax.scatter([s["offered"] for s in failed], [s["received"] for s in failed],
                   marker="x", s=30, color=TEXT_2, lw=1.0, zorder=3,
                   label="iperf3 client failure (excluded)")

    # Mean across runs at each offered load
    levels = sorted({round(s["offered"], 3) for r in runs for s in r if not s["failed"]})
    means = []
    for lv in levels:
        vals = [s["received"] for r in runs for s in r
                if not s["failed"] and abs(s["offered"] - lv) < TOL]
        means.append(statistics.mean(vals))
    ax.plot(levels, means, color=ACCENT, lw=2.0, marker="o", ms=5.5, zorder=4,
            markeredgecolor="white", markeredgewidth=0.8, label="Mean of runs")

    # Capacity line with a direct label
    cap = res["capacity"]
    ax.axhline(cap, color=ACCENT, lw=1.0, ls=(0, (1.5, 2)), zorder=3)
    ax.text(lim[1] + 0.015 * (lim[1] - lim[0]), cap,
            f"capacity\n$\\approx${cap:.2f} Mbit/s", ha="left", va="center",
            fontsize=9, color=TEXT, clip_on=False)
    ax.text(lim[1] - 0.02 * (lim[1] - lim[0]), lim[1] - 0.04 * (lim[1] - lim[0]),
            "received = offered", ha="right", va="top", fontsize=8.5, color=TEXT_2,
            rotation=45, rotation_mode="anchor", transform_rotates_text=True)

    ax.set_xlabel("Total offered load (Mbit/s)")
    ax.set_ylabel("Total received throughput (Mbit/s)")
    ax.grid(axis="y", color="#e6e5e0", lw=0.6, zorder=0)
    ax.set_axisbelow(True)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.01), ncol=3, frameon=False,
              borderaxespad=0, handlelength=2.2, columnspacing=1.4)

    out = os.path.join(out_dir, cfg["pdf"])
    fig.savefig(out, bbox_inches="tight", pad_inches=0.03)
    plt.close(fig)
    return out


# ----------------------------------------------------------------------------
def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "."
    results = [r for r in (analyse(base, c) for c in CONFIGS) if r]
    if not results:
        sys.exit("No test folders found. Run the script inside the directory that contains them.")

    print(f"\n{'Configuration':<22}{'Runs':>5}{'Per-run means (Mbit/s)':>44}{'Capacity':>11}{'SD':>7}")
    print("-" * 89)
    rows = []
    for r in results:
        c = r["cfg"]
        name = f"{c['ues']} UEs, {c['bw']} MHz"
        pr = ", ".join(f"{m:.2f}" for m in r["per_run_means"])
        print(f"{name:<22}{len(r['per_run_means']):>5}{pr:>44}{r['capacity']:>11.2f}{r['sd']:>7.2f}")
        ues = ", ".join(f"UE{i+1} {v:.2f}" for i, v in enumerate(r["per_ue"]))
        print(f"{'':<22}  per UE in range: {ues}")
        if r["excluded"]:
            print(f"{'':<22}  ({r['excluded']} stage(s) excluded because an iperf3 client failed)")
        out = plot(r, base)
        print(f"{'':<22}  figure: {out}")
        rows.append([c["ues"], c["bw"], f"{c['range'][0]}-{c['range'][1]}", len(r["per_run_means"]),
                     *[f"{m:.3f}" for m in r["per_run_means"]], f"{r['capacity']:.3f}", f"{r['sd']:.3f}"])

    with open(os.path.join(base, "capacity_results.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ues", "bandwidth_mhz", "per_ue_range_mbps", "runs",
                    "run1", "run2", "run3", "run4", "run5", "capacity_mbps", "sd_across_runs"])
        w.writerows(rows)
    print("\nSummary written to capacity_results.csv\n")


if __name__ == "__main__":
    main()
