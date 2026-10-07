#!/usr/bin/env python3
"""Plot the three-run QoS weight-selection experiment.

The script reads the per-scenario ``*_results.csv`` files produced by
``analyze_qos_weight_scores_v2.py``.  For each experimental run, it first
averages the scenarios marked ``included_in_aggregate=True``.  It then plots
the mean and sample standard deviation across repeated runs.

Only iPerf receiver-side performance metrics are used for throughput and
satisfaction.  Control-message frequency comes from the xApp log analysis.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter


CONFIG_ORDER = ["default", "balanced", "prb", "competition", "delay"]
CONFIG_LABELS = {
    "default": "Default\n(40/35/25)",
    "balanced": "Balanced\n(34/33/33)",
    "prb": "PRB-heavy\n(60/20/20)",
    "competition": "Competition-heavy\n(20/60/20)",
    "delay": "Delay-heavy\n(20/20/60)",
}
CONFIG_COLORS = {
    "default": "#6b7280",
    "balanced": "#2563eb",
    "prb": "#7c3aed",
    "competition": "#059669",
    "delay": "#dc2626",
}

METRICS = [
    "iperf_weighted_satisfaction",
    "iperf_protected_satisfaction",
    "high_iperf_satisfaction",
    "medium_iperf_satisfaction",
    "best_effort_iperf_satisfaction",
    "iperf_cell_capacity_utilization",
    "iperf_demand_capacity_efficiency",
    "total_iperf_goodput_mbps",
    "controls_per_minute",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot repeated QoS weight-selection results."
    )
    parser.add_argument(
        "--input-root",
        type=Path,
        default=Path("/home/pca/weight_set_optimization"),
        help="Directory containing weight_selection_run_* folders.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "weight_results_plots",
        help="Directory in which PNG, PDF, and summary CSV files are written.",
    )
    return parser.parse_args()


def numeric_mean(rows: list[dict[str, str]], field: str) -> float:
    values = []
    for row in rows:
        raw = row.get(field, "").strip()
        if not raw:
            continue
        value = float(raw)
        if math.isfinite(value):
            values.append(value)
    return statistics.mean(values) if values else math.nan


def load_runs(input_root: Path) -> dict[str, list[dict[str, float]]]:
    result_files = sorted(
        input_root.glob("weight_selection_run_*/*/*_results.csv")
    )
    if not result_files:
        raise FileNotFoundError(
            f"No *_results.csv files found below {input_root}"
        )

    runs: dict[str, list[dict[str, float]]] = {}
    for result_file in result_files:
        with result_file.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            continue

        included = [
            row
            for row in rows
            if row.get("included_in_aggregate", "").strip().lower() == "true"
        ]
        if not included:
            raise ValueError(f"No included scenarios in {result_file}")

        config = rows[0]["weight_set"].strip().lower()
        run = {metric: numeric_mean(included, metric) for metric in METRICS}
        run["source_file"] = str(result_file)  # type: ignore[assignment]
        runs.setdefault(config, []).append(run)

    missing = [config for config in CONFIG_ORDER if config not in runs]
    if missing:
        raise ValueError(f"Missing configurations: {', '.join(missing)}")
    return runs


def mean_std(
    runs: dict[str, list[dict[str, float]]], config: str, metric: str
) -> tuple[float, float]:
    values = [run[metric] for run in runs[config] if math.isfinite(run[metric])]
    if not values:
        return math.nan, math.nan
    deviation = statistics.stdev(values) if len(values) > 1 else 0.0
    return statistics.mean(values), deviation


def save_figure(fig: plt.Figure, output_dir: Path, stem: str) -> None:
    fig.savefig(output_dir / f"{stem}.png", dpi=220, bbox_inches="tight")
    fig.savefig(output_dir / f"{stem}.pdf", bbox_inches="tight")
    plt.close(fig)


def plot_priority_satisfaction(
    runs: dict[str, list[dict[str, float]]], output_dir: Path
) -> None:
    series = [
        ("High priority", "high_iperf_satisfaction", "#2563eb"),
        ("Medium priority", "medium_iperf_satisfaction", "#eab308"),
        ("Best effort", "best_effort_iperf_satisfaction", "#16a34a"),
    ]
    x = list(range(len(CONFIG_ORDER)))
    width = 0.24
    fig, ax = plt.subplots(figsize=(11.5, 5.8))

    for series_index, (label, metric, color) in enumerate(series):
        offset = (series_index - 1) * width
        means, deviations = zip(
            *(mean_std(runs, config, metric) for config in CONFIG_ORDER)
        )
        ax.bar(
            [position + offset for position in x],
            means,
            width,
            label=label,
            color=color,
            yerr=deviations,
            capsize=3,
            error_kw={"elinewidth": 1.2},
        )

    ax.set_title("Per-priority satisfaction by congestion-score weights")
    ax.set_ylabel("Mean satisfaction (achieved / offered)")
    ax.set_xticks(x, [CONFIG_LABELS[config] for config in CONFIG_ORDER])
    ax.set_ylim(0.72, 0.92)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax.grid(axis="y", alpha=0.25)
    ax.legend(ncol=3, loc="upper center")
    ax.text(
        0.01,
        -0.22,
        "Bars show mean ± sample standard deviation across 3 runs; "
        "only scenarios marked for aggregate analysis are included.",
        transform=ax.transAxes,
        fontsize=9,
    )
    fig.subplots_adjust(bottom=0.25)
    save_figure(fig, output_dir, "01_priority_satisfaction")


def plot_system_metrics(
    runs: dict[str, list[dict[str, float]]], output_dir: Path
) -> None:
    series = [
        ("Weighted satisfaction", "iperf_weighted_satisfaction", "#7c3aed"),
        ("Protected satisfaction", "iperf_protected_satisfaction", "#2563eb"),
        ("Cell utilization", "iperf_cell_capacity_utilization", "#ea580c"),
        ("Demand-capacity efficiency", "iperf_demand_capacity_efficiency", "#059669"),
    ]
    x = list(range(len(CONFIG_ORDER)))
    width = 0.19
    fig, ax = plt.subplots(figsize=(12, 5.8))

    for series_index, (label, metric, color) in enumerate(series):
        offset = (series_index - 1.5) * width
        means, deviations = zip(
            *(mean_std(runs, config, metric) for config in CONFIG_ORDER)
        )
        ax.bar(
            [position + offset for position in x],
            means,
            width,
            label=label,
            color=color,
            yerr=deviations,
            capsize=2.5,
            error_kw={"elinewidth": 1.1},
        )

    ax.set_title("Aggregate performance by congestion-score weights", pad=70)
    ax.set_ylabel("Mean metric value")
    ax.set_xticks(x, [CONFIG_LABELS[config] for config in CONFIG_ORDER])
    ax.set_ylim(0.78, 0.98)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax.grid(axis="y", alpha=0.25)
    ax.legend(
        ncol=2,
        loc="lower center",
        bbox_to_anchor=(0.5, 1.01),
    )
    ax.text(
        0.01,
        -0.22,
        "Mean ± sample standard deviation across 3 runs. Throughput-based "
        "metrics use iPerf receiver results.",
        transform=ax.transAxes,
        fontsize=9,
    )
    fig.subplots_adjust(bottom=0.25)
    save_figure(fig, output_dir, "02_aggregate_performance")


def plot_tradeoff(
    runs: dict[str, list[dict[str, float]]], output_dir: Path
) -> None:
    fig, ax = plt.subplots(figsize=(8.5, 6.5))
    annotations = {
        "default": (7, 8),
        "balanced": (7, 6),
        "prb": (7, 5),
        "competition": (7, -16),
        "delay": (7, 7),
    }

    for config in CONFIG_ORDER:
        x_mean, x_std = mean_std(runs, config, "best_effort_iperf_satisfaction")
        y_mean, y_std = mean_std(runs, config, "iperf_protected_satisfaction")
        ax.errorbar(
            x_mean,
            y_mean,
            xerr=x_std,
            yerr=y_std,
            fmt="o",
            markersize=9,
            capsize=4,
            color=CONFIG_COLORS[config],
            label=CONFIG_LABELS[config].replace("\n", " "),
        )
        ax.annotate(
            CONFIG_LABELS[config].split("\n")[0],
            (x_mean, y_mean),
            xytext=annotations[config],
            textcoords="offset points",
            fontsize=9,
        )

    ax.set_title("Protection versus best-effort trade-off")
    ax.set_xlabel("Best-effort satisfaction")
    ax.set_ylabel("Protected-priority satisfaction (high + medium)")
    ax.set_xlim(0.75, 0.80)
    ax.set_ylim(0.82, 0.86)
    ax.xaxis.set_major_formatter(PercentFormatter(1.0))
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax.grid(alpha=0.25)
    ax.text(
        0.01,
        -0.16,
        "Moving upward improves protected traffic; moving right improves "
        "best-effort traffic. Error bars show ±1 SD across 3 runs.",
        transform=ax.transAxes,
        fontsize=9,
    )
    fig.subplots_adjust(bottom=0.18)
    save_figure(fig, output_dir, "03_protection_tradeoff")


def write_summary_csv(
    runs: dict[str, list[dict[str, float]]], output_dir: Path
) -> None:
    path = output_dir / "weight_results_mean_std.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        fields = ["configuration", "runs"]
        for metric in METRICS:
            fields.extend([f"{metric}_mean", f"{metric}_sd"])
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for config in CONFIG_ORDER:
            row: dict[str, object] = {
                "configuration": config,
                "runs": len(runs[config]),
            }
            for metric in METRICS:
                mean, deviation = mean_std(runs, config, metric)
                row[f"{metric}_mean"] = mean
                row[f"{metric}_sd"] = deviation
            writer.writerow(row)


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # Keep styling independent of optional seaborn styles and Matplotlib version.
    plt.style.use("default")
    plt.rcParams.update(
        {
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.titleweight": "bold",
            "font.size": 10,
        }
    )

    runs = load_runs(args.input_root)
    counts = {config: len(config_runs) for config, config_runs in runs.items()}
    if any(counts[config] != 3 for config in CONFIG_ORDER):
        print(f"Warning: expected 3 runs per configuration, found {counts}")

    plot_priority_satisfaction(runs, args.output_dir)
    plot_system_metrics(runs, args.output_dir)
    plot_tradeoff(runs, args.output_dir)
    write_summary_csv(runs, args.output_dir)
    print(f"Plots and aggregate table written to {args.output_dir}")


if __name__ == "__main__":
    main()
