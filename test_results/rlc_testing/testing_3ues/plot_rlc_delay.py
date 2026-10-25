#!/usr/bin/env python3
"""
DL RLC SDU delay against per-UE DL throughput (no-control runs, 3 UEs, 10 MHz).

Run it from the directory that contains rlc_testing_*.csv (one file per run):

    python3 plot_rlc_delay.py [directory]

Both quantities come from the xApp log (E2SM-KPM, 1 s period):
  * ue_dl_thp      DRB.UEThpDl, RLC PDU bytes sent by the DU (kbit/s, headers included)
  * ue_dl_delay_ms per-UE DL RLC SDU delay (ms)

Only UE-level rows (trigger style4_ue) of active UEs are used. Delays outside
0 to max_valid_delay_ms (6000 ms) are excluded, as the xApp does.

For each run and UE the median delay is taken inside two throughput windows
(low load and overload); the figure labels the median of those medians.

Outputs: rlc_delay_vs_throughput.pdf and rlc_delay_summary.csv
Needs:   matplotlib
"""

import csv
import glob
import math
import os
import sys
from statistics import median

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FixedLocator, FuncFormatter, NullFormatter  # noqa: E402

# Throughput windows (Mbit/s per UE) used for the medians
WINDOWS = {"low": (4.5, 6.0), "overload": (9.0, 10.5)}

# One colour per priority class (fixed order, reference categorical palette)
UES = {
    "best_effort": ("Best effort", "#2a78d6", "o"),
    "medium": ("Medium", "#eb6834", "s"),
    "high": ("High", "#1baf7a", "^"),
}

TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
REF_GREY = "#b8b7b1"

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


def as_float(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def load(base):
    paths = sorted(glob.glob(os.path.join(base, "rlc_testing_*.csv")))
    if not paths:
        sys.exit(f"No rlc_testing_*.csv files found in {base}")
    obs, active, rejected, limit = [], 0, 0, 6000.0
    for run, path in enumerate(paths, start=1):
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                if row.get("control_enabled") == "True" or row.get("control_sent") == "True":
                    sys.exit(f"{path} contains rows with control enabled; this figure is for no-control runs")
                if row.get("trigger") != "style4_ue" or row.get("is_active") != "True":
                    continue
                thp = as_float(row.get("ue_dl_thp"))
                delay = as_float(row.get("ue_dl_delay_ms"))
                if thp is None or delay is None:
                    continue
                limit = as_float(row.get("max_valid_delay_ms")) or 6000.0
                active += 1
                if delay < 0 or delay > limit:
                    rejected += 1
                    continue
                obs.append({"run": run, "prio": row["priority"], "ue": int(row["ue_id"]),
                            "thp": thp / 1000.0, "delay": delay})
    return paths, obs, active, rejected, limit


def window_medians(obs):
    """Median throughput and delay per (run, UE, window)."""
    groups = {}
    for o in obs:
        for name, (lo, hi) in WINDOWS.items():
            if lo <= o["thp"] <= hi:
                groups.setdefault((o["run"], o["ue"], o["prio"], name), []).append(o)
    return [{"run": r, "ue_id": u, "priority": p, "window": w, "n": len(v),
             "thp_median_mbps": median(x["thp"] for x in v),
             "delay_median_ms": median(x["delay"] for x in v)}
            for (r, u, p, w), v in sorted(groups.items())]


def fmt_delay(v, _):
    return f"{v / 1000:g} s" if v >= 1000 else f"{v:g} ms"


def plot(obs, summ, limit, out):
    fig, ax = plt.subplots(figsize=(5.2, 3.9))
    ax.set_yscale("log")
    ax.set_xlim(0, 11)
    ax.set_ylim(0.5, 2e4)

    # Windows used for the medians
    for name, (lo, hi) in WINDOWS.items():
        ax.axvspan(lo, hi, color=REF_GREY, alpha=0.18, lw=0, zorder=0)
        ax.text((lo + hi) / 2, 1.4e4, "low-load window" if name == "low" else "overload window",
                ha="center", va="top", fontsize=8.5, color=TEXT_2)

    # Exclusion limit
    ax.axhline(limit, color=TEXT_2, lw=0.8, ls=(0, (1.5, 2)), zorder=1)
    ax.text(0.15, limit * 1.12, f"exclusion limit ({limit / 1000:g} s)", ha="left", va="bottom",
            fontsize=8.5, color=TEXT_2)

    # All valid 1 s observations
    for prio, (label, colour, marker) in UES.items():
        pts = [o for o in obs if o["prio"] == prio]
        ax.scatter([o["thp"] for o in pts], [o["delay"] for o in pts], s=9, marker=marker,
                   color=colour, alpha=0.18, linewidths=0, zorder=2, rasterized=True, label=label)

    # Median of the per-run medians, per UE and window, with one direct label per window
    for name in WINDOWS:
        rows = [s for s in summ if s["window"] == name]
        for prio, (_, colour, marker) in UES.items():
            r = [s for s in rows if s["priority"] == prio]
            if r:
                ax.scatter(median(s["thp_median_mbps"] for s in r), median(s["delay_median_ms"] for s in r),
                           s=60, marker=marker, color=colour, edgecolors="white", linewidths=1.0, zorder=4)
        thp = median(s["thp_median_mbps"] for s in rows)
        d = median(s["delay_median_ms"] for s in rows)
        txt = f"median {d:.1f} ms" if d < 1000 else f"median {d / 1000:.1f} s"
        if name == "low":
            ax.annotate(txt, (thp, d), xytext=(thp - 0.9, d * 4), ha="right", va="bottom",
                        fontsize=9, color=TEXT,
                        arrowprops={"arrowstyle": "-", "color": TEXT_2, "lw": 0.7})
        else:
            ax.annotate(txt, (thp, d), xytext=(thp - 1.1, d / 6), ha="right", va="top",
                        fontsize=9, color=TEXT,
                        arrowprops={"arrowstyle": "-", "color": TEXT_2, "lw": 0.7})

    ax.yaxis.set_major_locator(FixedLocator([1, 10, 100, 1000, 10000]))
    ax.yaxis.set_major_formatter(FuncFormatter(fmt_delay))
    ax.yaxis.set_minor_formatter(NullFormatter())
    ax.set_xlabel("Per-UE DL throughput at the DU, DRB.UEThpDl (Mbit/s)")
    ax.set_ylabel("DL RLC SDU delay (log scale)")
    ax.grid(axis="y", which="major", color="#e6e5e0", lw=0.6, zorder=0)
    ax.set_axisbelow(True)
    leg = ax.legend(loc="lower left", bbox_to_anchor=(0, 1.01), ncol=3, frameon=False,
                    borderaxespad=0, markerscale=2.2, handletextpad=0.3, columnspacing=1.6)
    for h in leg.legend_handles:
        h.set_alpha(1)

    fig.savefig(out, bbox_inches="tight", pad_inches=0.03, dpi=300)
    plt.close(fig)


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "."
    paths, obs, active, rejected, limit = load(base)
    summ = window_medians(obs)

    out = os.path.join(base, "rlc_delay_vs_throughput.pdf")
    plot(obs, summ, limit, out)
    with open(os.path.join(base, "rlc_delay_summary.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summ[0]))
        w.writeheader()
        w.writerows(summ)

    print(f"{len(paths)} runs, {len(obs)} valid observations, "
          f"{rejected}/{active} excluded ({100 * rejected / active:.1f} %) for delay outside 0-{limit:g} ms")
    for name in WINDOWS:
        rows = [s for s in summ if s["window"] == name]
        print(f"{name:>9} window: median throughput {median(s['thp_median_mbps'] for s in rows):.2f} Mbit/s, "
              f"median delay {median(s['delay_median_ms'] for s in rows):.2f} ms")
        for prio, (label, _, _) in UES.items():
            r = [s for s in rows if s["priority"] == prio]
            print(f"{'':>18}{label:<12} {median(s['thp_median_mbps'] for s in r):.2f} Mbit/s, "
                  f"{median(s['delay_median_ms'] for s in r):.2f} ms")
    print(f"Figure: {out}")


if __name__ == "__main__":
    main()
