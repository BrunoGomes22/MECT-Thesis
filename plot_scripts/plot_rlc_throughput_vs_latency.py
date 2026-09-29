#!/usr/bin/env python3
"""Plot the relationship between UE DL throughput and DL RLC SDU delay.

The script is intentionally limited to Python's standard library, NumPy, and
Matplotlib so it can run with the packages already installed on this system.
"""

from __future__ import annotations

import argparse
import csv
import glob
import math
import os
from collections import defaultdict
from statistics import median

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import FuncFormatter


UE_STYLES = {
    0: ("Best effort (UE 0)", "#0072B2", "o"),
    1: ("Medium (UE 1)", "#E69F00", "s"),
    2: ("High (UE 2)", "#009E73", "^"),
}

REGIME_BANDS = {
    "Lower-throughput regime": (4.5, 6.0),
    "Near-capacity regime": (9.0, 10.5),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "input_dir",
        nargs="?",
        default=".",
        help="Directory containing rlc_testing_*.csv files.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory (defaults to the input directory).",
    )
    return parser.parse_args()


def as_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def as_bool(value: str | None) -> bool:
    return str(value).strip().lower() == "true"


def load_observations(input_dir: str):
    paths = sorted(glob.glob(os.path.join(input_dir, "rlc_testing_*.csv")))
    if not paths:
        raise SystemExit(f"No rlc_testing_*.csv files found in {input_dir}")

    valid = []
    rejected_delay = 0
    active_with_delay = 0
    control_enabled_rows = 0
    control_sent_rows = 0

    for run_number, path in enumerate(paths, start=1):
        with open(path, newline="") as handle:
            for row in csv.DictReader(handle):
                control_enabled_rows += int(as_bool(row.get("control_enabled")))
                control_sent_rows += int(as_bool(row.get("control_sent")))

                if row.get("trigger") != "style4_ue" or not as_bool(row.get("is_active")):
                    continue

                throughput_kbps = as_float(row.get("ue_dl_thp"))
                delay_ms = as_float(row.get("ue_dl_delay_ms"))
                max_delay_ms = as_float(row.get("max_valid_delay_ms")) or 6000.0
                if throughput_kbps is None or delay_ms is None:
                    continue

                active_with_delay += 1
                if delay_ms < 0 or delay_ms > max_delay_ms:
                    rejected_delay += 1
                    continue

                valid.append(
                    {
                        "run": run_number,
                        "source": os.path.basename(path),
                        "ue_id": int(row["ue_id"]),
                        "priority": row["priority"],
                        "throughput_mbps": throughput_kbps / 1000.0,
                        "delay_ms": delay_ms,
                    }
                )

    if control_enabled_rows or control_sent_rows:
        raise SystemExit(
            "The input contains control-enabled/control-sent rows; "
            "this plot is intended for the no-control characterization."
        )

    return paths, valid, active_with_delay, rejected_delay


def summarize_regimes(observations):
    groups = defaultdict(list)
    for obs in observations:
        for regime, (lower, upper) in REGIME_BANDS.items():
            if lower <= obs["throughput_mbps"] <= upper:
                groups[(obs["run"], obs["ue_id"], obs["priority"], regime)].append(obs)
                break

    summaries = []
    for (run, ue_id, priority, regime), rows in sorted(groups.items()):
        summaries.append(
            {
                "run": run,
                "ue_id": ue_id,
                "priority": priority,
                "regime": regime,
                "n": len(rows),
                "throughput_mbps_median": median(r["throughput_mbps"] for r in rows),
                "delay_ms_median": median(r["delay_ms"] for r in rows),
            }
        )
    return summaries


def write_summary(path: str, summaries) -> None:
    fieldnames = [
        "run",
        "ue_id",
        "priority",
        "regime",
        "n",
        "throughput_mbps_median",
        "delay_ms_median",
    ]
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summaries)


def format_delay_tick(value, _position):
    if value >= 1000:
        return f"{value / 1000:g}k"
    return f"{value:g}"


def make_plot(observations, summaries, output_png: str, output_pdf: str) -> None:
    plt.rcParams.update(
        {
            "font.size": 10.5,
            "axes.titlesize": 12,
            "axes.labelsize": 11,
            "legend.fontsize": 9,
            "figure.titlesize": 16,
        }
    )
    fig, axes = plt.subplots(1, 2, figsize=(13.2, 5.8), sharey=True)
    raw_ax, paired_ax = axes

    for ue_id in sorted({row["ue_id"] for row in observations}):
        label, color, marker = UE_STYLES.get(
            ue_id, (f"UE {ue_id}", "#777777", "o")
        )
        rows = [row for row in observations if row["ue_id"] == ue_id]
        raw_ax.scatter(
            [row["throughput_mbps"] for row in rows],
            [row["delay_ms"] for row in rows],
            s=12,
            marker=marker,
            color=color,
            alpha=0.14,
            linewidths=0,
            label=label,
            rasterized=True,
        )

    raw_ax.axvspan(4.5, 6.0, color="#56B4E9", alpha=0.08)
    raw_ax.axvspan(9.0, 10.5, color="#D55E00", alpha=0.06)
    raw_ax.set_title("(a) All valid 1-second observations", loc="left")
    raw_ax.set_xlabel("UE downlink throughput (Mbit/s)")
    raw_ax.set_ylabel("DL RLC SDU delay (ms, log scale)")
    raw_ax.legend(loc="upper left", frameon=True, markerscale=1.7)

    summary_by_key = {
        (row["run"], row["ue_id"], row["regime"]): row for row in summaries
    }
    regimes = list(REGIME_BANDS)
    run_numbers = sorted({row["run"] for row in summaries})
    ue_ids = sorted({row["ue_id"] for row in summaries})

    for ue_id in ue_ids:
        label, color, marker = UE_STYLES.get(
            ue_id, (f"UE {ue_id}", "#777777", "o")
        )
        for run in run_numbers:
            pair = [summary_by_key.get((run, ue_id, regime)) for regime in regimes]
            if any(item is None for item in pair):
                continue
            paired_ax.plot(
                [item["throughput_mbps_median"] for item in pair],
                [item["delay_ms_median"] for item in pair],
                color=color,
                alpha=0.28,
                linewidth=1.2,
                zorder=2,
            )
            paired_ax.scatter(
                [item["throughput_mbps_median"] for item in pair],
                [item["delay_ms_median"] for item in pair],
                color=color,
                marker=marker,
                s=31,
                alpha=0.48,
                linewidths=0,
                zorder=3,
            )

        across_run = []
        for regime in regimes:
            rows = [
                row
                for row in summaries
                if row["ue_id"] == ue_id and row["regime"] == regime
            ]
            across_run.append(
                (
                    median(row["throughput_mbps_median"] for row in rows),
                    median(row["delay_ms_median"] for row in rows),
                )
            )
        paired_ax.plot(
            [point[0] for point in across_run],
            [point[1] for point in across_run],
            color=color,
            linewidth=2.8,
            zorder=4,
        )
        paired_ax.scatter(
            [point[0] for point in across_run],
            [point[1] for point in across_run],
            color=color,
            edgecolors="white",
            linewidths=1.1,
            marker=marker,
            s=105,
            zorder=5,
            label=label,
        )

    low_rows = [row for row in summaries if row["regime"] == regimes[0]]
    high_rows = [row for row in summaries if row["regime"] == regimes[1]]
    low_delay = median(row["delay_ms_median"] for row in low_rows)
    high_delay = median(row["delay_ms_median"] for row in high_rows)
    low_thp = median(row["throughput_mbps_median"] for row in low_rows)
    high_thp = median(row["throughput_mbps_median"] for row in high_rows)

    paired_ax.annotate(
        f"Across 5 runs and 3 UEs\n{low_thp:.2f} Mbit/s: {low_delay:.2f} ms\n"
        f"{high_thp:.2f} Mbit/s: {high_delay / 1000:.2f} s",
        xy=(high_thp, high_delay),
        xytext=(6.0, 150),
        arrowprops={
            "arrowstyle": "->",
            "color": "#333333",
            "lw": 1.2,
            "connectionstyle": "arc3,rad=-0.12",
        },
        bbox={"boxstyle": "round,pad=0.35", "fc": "white", "ec": "#BBBBBB"},
        ha="left",
        va="center",
        zorder=10,
    )
    paired_ax.set_title("(b) Paired median of each run", loc="left")
    paired_ax.set_xlabel("Median UE downlink throughput (Mbit/s)")
    paired_ax.legend(loc="upper left", frameon=True)

    for ax in axes:
        ax.set_yscale("log")
        ax.set_xlim(0.5, 10.7)
        ax.set_ylim(0.8, 8000)
        ax.set_yticks([1, 10, 100, 1000, 6000])
        ax.yaxis.set_major_formatter(FuncFormatter(format_delay_tick))
        ax.grid(True, which="major", color="#D8D8D8", linewidth=0.7)
        ax.grid(True, which="minor", axis="y", color="#EEEEEE", linewidth=0.45)
        ax.set_axisbelow(True)

    fig.suptitle("RLC latency rises sharply at higher UE downlink throughput")
    fig.text(
        0.5,
        0.012,
        "No-control characterization; five repetitions. Raw delays outside the xApp-valid "
        "0–6,000 ms range are excluded.",
        ha="center",
        color="#444444",
        fontsize=9.5,
    )
    fig.tight_layout(rect=(0, 0.05, 1, 0.93))
    fig.savefig(output_png, dpi=300, bbox_inches="tight")
    fig.savefig(output_pdf, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    args = parse_args()
    input_dir = os.path.abspath(args.input_dir)
    output_dir = os.path.abspath(args.output_dir or input_dir)
    os.makedirs(output_dir, exist_ok=True)

    paths, observations, active_count, rejected_count = load_observations(input_dir)
    summaries = summarize_regimes(observations)

    stem = os.path.join(output_dir, "rlc_throughput_vs_latency")
    write_summary(stem + "_summary.csv", summaries)
    make_plot(observations, summaries, stem + ".png", stem + ".pdf")

    low = [row for row in summaries if row["regime"] == "Lower-throughput regime"]
    high = [row for row in summaries if row["regime"] == "Near-capacity regime"]
    print(f"Loaded {len(paths)} runs and {len(observations)} valid observations.")
    print(f"Excluded {rejected_count}/{active_count} active observations outside 0–6,000 ms.")
    print(
        "Across-run/UE medians: "
        f"{median(row['throughput_mbps_median'] for row in low):.2f} Mbit/s -> "
        f"{median(row['delay_ms_median'] for row in low):.2f} ms; "
        f"{median(row['throughput_mbps_median'] for row in high):.2f} Mbit/s -> "
        f"{median(row['delay_ms_median'] for row in high):.2f} ms."
    )
    print(f"Wrote {stem}.png, {stem}.pdf, and {stem}_summary.csv")


if __name__ == "__main__":
    main()
