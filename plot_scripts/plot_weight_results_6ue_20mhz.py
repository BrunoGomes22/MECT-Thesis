#!/usr/bin/env python3
"""Plot the repeated 6-UE, 20-MHz congestion-weight experiment.

The input is the directory produced by the short weight-selection matrix. Each
configuration is first averaged across the scenarios marked
``included_in_aggregate=True`` in one analyzer ``*_results.csv`` file. The
figures then show the mean and sample standard deviation across repeated runs.

Throughput, satisfaction, loss, and utilization metrics come from receiver-side
iPerf results. The script writes PNG figures only and requires Times New Roman
so thesis figures cannot silently use a substitute font.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from pathlib import Path
from typing import TypeAlias

import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import PercentFormatter


CONFIG_ORDER = ["default", "balanced", "prb", "competition", "delay"]
CONFIG_ALIASES = {
    "default": "default",
    "balanced": "balanced",
    "prb": "prb",
    "comp": "competition",
    "competition": "competition",
    "delay": "delay",
}
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
    "iperf_total_loss_percent",
    "controls_per_minute",
]

RunMetrics: TypeAlias = dict[str, float | str]
RunsByConfig: TypeAlias = dict[str, list[RunMetrics]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot repeated 6-UE, 20-MHz weight-selection results."
    )
    parser.add_argument(
        "--input-root",
        type=Path,
        default=Path(
            "/home/pca/weight_set_optimization/weight_selection_results/"
            "weight_selection_6ue_20mhz"
        ),
        help="Directory containing run_1, run_2, and run_3.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=(
            Path(__file__).resolve().parent
            / "weight_results_plots_6ue_20mhz"
        ),
        help="Directory in which PNG figures and summary CSV files are written.",
    )
    parser.add_argument(
        "--expected-runs",
        type=int,
        default=3,
        help="Expected repeated runs per configuration (default: 3).",
    )
    parser.add_argument(
        "--expected-cell-capacity",
        type=float,
        default=54.4,
        help="Expected analyzer capacity reference in Mbit/s (default: 54.4).",
    )
    return parser.parse_args()


def finite_values(rows: list[dict[str, str]], field: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        raw = row.get(field, "").strip()
        if not raw:
            continue
        value = float(raw)
        if math.isfinite(value):
            values.append(value)
    return values


def numeric_mean(rows: list[dict[str, str]], field: str) -> float:
    values = finite_values(rows, field)
    return statistics.mean(values) if values else math.nan


def normalize_config(raw: str, source: Path) -> str:
    key = raw.strip().lower()
    config = CONFIG_ALIASES.get(key)
    if config is None:
        raise ValueError(
            f"Unknown weight_set {raw!r} in {source}. Expected one of: "
            f"{', '.join(sorted(CONFIG_ALIASES))}."
        )
    return config


def discover_result_files(input_root: Path) -> list[Path]:
    files = sorted(
        path
        for path in input_root.glob("run_*/*/*_results.csv")
        if not path.name.endswith("_per_ue_results.csv")
    )
    if not files:
        raise FileNotFoundError(
            f"No analyzer *_results.csv files found below {input_root}"
        )
    return files


def load_runs(
    input_root: Path,
    expected_capacity: float,
) -> RunsByConfig:
    runs: RunsByConfig = {}

    for result_file in discover_result_files(input_root):
        with result_file.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            raise ValueError(f"Empty analyzer result file: {result_file}")

        included = [
            row
            for row in rows
            if row.get("included_in_aggregate", "").strip().lower() == "true"
        ]
        if not included:
            raise ValueError(f"No included scenarios in {result_file}")

        capacities = finite_values(rows, "cell_capacity_mbps")
        if not capacities:
            raise ValueError(f"Missing cell_capacity_mbps in {result_file}")
        if any(
            not math.isclose(value, expected_capacity, abs_tol=1e-6)
            for value in capacities
        ):
            unique = sorted(set(capacities))
            raise ValueError(
                f"Unexpected cell-capacity reference in {result_file}: "
                f"{unique}; expected {expected_capacity:g} Mbit/s."
            )

        config = normalize_config(rows[0].get("weight_set", ""), result_file)
        run: RunMetrics = {
            metric: numeric_mean(included, metric) for metric in METRICS
        }
        run["source_file"] = str(result_file)
        runs.setdefault(config, []).append(run)

    missing = [config for config in CONFIG_ORDER if config not in runs]
    if missing:
        raise ValueError(
            "Missing weight configurations: " + ", ".join(missing)
        )
    return runs


def metric_values(
    runs: RunsByConfig,
    config: str,
    metric: str,
) -> list[float]:
    values: list[float] = []
    for run in runs[config]:
        value = run.get(metric, math.nan)
        if isinstance(value, (int, float)) and math.isfinite(value):
            values.append(float(value))
    return values


def mean_std(
    runs: RunsByConfig,
    config: str,
    metric: str,
) -> tuple[float, float]:
    values = metric_values(runs, config, metric)
    if not values:
        return math.nan, math.nan
    deviation = statistics.stdev(values) if len(values) > 1 else 0.0
    return statistics.mean(values), deviation


def padded_limits(
    means: list[float],
    deviations: list[float],
    *,
    lower_bound: float = 0.0,
    upper_bound: float = 1.02,
    minimum_span: float = 0.10,
) -> tuple[float, float]:
    lows = [mean - deviation for mean, deviation in zip(means, deviations)]
    highs = [mean + deviation for mean, deviation in zip(means, deviations)]
    low = min(lows)
    high = max(highs)
    data_span = max(high - low, 0.01)
    padding = max(0.015, data_span * 0.16)
    low = max(lower_bound, low - padding)
    high = min(upper_bound, high + padding)

    if high - low < minimum_span:
        midpoint = (low + high) / 2.0
        low = max(lower_bound, midpoint - minimum_span / 2.0)
        high = min(upper_bound, midpoint + minimum_span / 2.0)
    return low, high


def save_figure(fig: plt.Figure, output_dir: Path, stem: str) -> None:
    fig.savefig(output_dir / f"{stem}.png", dpi=220, bbox_inches="tight")
    plt.close(fig)


def plot_priority_satisfaction(
    runs: RunsByConfig,
    output_dir: Path,
) -> None:
    series = [
        ("High priority", "high_iperf_satisfaction", "#2563eb"),
        ("Medium priority", "medium_iperf_satisfaction", "#eab308"),
        ("Best effort", "best_effort_iperf_satisfaction", "#16a34a"),
    ]
    x = list(range(len(CONFIG_ORDER)))
    width = 0.24
    fig, ax = plt.subplots(figsize=(12.0, 6.2))
    all_means: list[float] = []
    all_deviations: list[float] = []

    for series_index, (label, metric, color) in enumerate(series):
        offset = (series_index - 1) * width
        statistics_by_config = [
            mean_std(runs, config, metric) for config in CONFIG_ORDER
        ]
        means = [item[0] for item in statistics_by_config]
        deviations = [item[1] for item in statistics_by_config]
        all_means.extend(means)
        all_deviations.extend(deviations)
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

    lower, upper = padded_limits(all_means, all_deviations, minimum_span=0.18)
    ax.set_title(
        "Per-priority satisfaction by congestion-score weights\n"
        "6 UEs, 20 MHz"
    )
    ax.set_ylabel("Mean satisfaction (achieved / offered)")
    ax.set_xticks(x, [CONFIG_LABELS[config] for config in CONFIG_ORDER])
    ax.set_ylim(lower, upper)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax.grid(axis="y", alpha=0.25)
    ax.legend(ncol=3, loc="upper center")
    fig.text(
        0.5,
        0.015,
        "Mean ± sample standard deviation across 3 runs. High and best "
        "effort include all four scenarios; medium includes the three "
        "scenarios in which medium traffic is active.",
        ha="center",
        fontsize=9,
    )
    fig.tight_layout(rect=(0.02, 0.07, 0.995, 0.98))
    save_figure(fig, output_dir, "01_priority_satisfaction")


def plot_system_metrics(runs: RunsByConfig, output_dir: Path) -> None:
    series = [
        ("Weighted satisfaction", "iperf_weighted_satisfaction", "#7c3aed"),
        ("Protected satisfaction", "iperf_protected_satisfaction", "#2563eb"),
        ("Cell utilization", "iperf_cell_capacity_utilization", "#ea580c"),
        (
            "Demand-capacity efficiency",
            "iperf_demand_capacity_efficiency",
            "#059669",
        ),
    ]
    x = list(range(len(CONFIG_ORDER)))
    width = 0.19
    fig, ax = plt.subplots(figsize=(12.4, 6.4))
    all_means: list[float] = []
    all_deviations: list[float] = []

    for series_index, (label, metric, color) in enumerate(series):
        offset = (series_index - 1.5) * width
        statistics_by_config = [
            mean_std(runs, config, metric) for config in CONFIG_ORDER
        ]
        means = [item[0] for item in statistics_by_config]
        deviations = [item[1] for item in statistics_by_config]
        all_means.extend(means)
        all_deviations.extend(deviations)
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

    lower, upper = padded_limits(all_means, all_deviations, minimum_span=0.18)
    ax.set_title(
        "Aggregate performance by congestion-score weights\n"
        "6 UEs, 20 MHz",
        pad=72,
    )
    ax.set_ylabel("Mean metric value")
    ax.set_xticks(x, [CONFIG_LABELS[config] for config in CONFIG_ORDER])
    ax.set_ylim(lower, upper)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax.grid(axis="y", alpha=0.25)
    ax.legend(ncol=2, loc="lower center", bbox_to_anchor=(0.5, 1.01))
    fig.text(
        0.5,
        0.015,
        "Mean ± sample standard deviation across 3 runs. Metrics are first "
        "averaged equally across the four included scenarios in each run.",
        ha="center",
        fontsize=9,
    )
    fig.tight_layout(rect=(0.02, 0.07, 0.995, 0.91))
    save_figure(fig, output_dir, "02_aggregate_performance")


def plot_tradeoff(runs: RunsByConfig, output_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(9.0, 7.0))
    annotations = {
        "default": (-8, -14, "right", "top"),
        "balanced": (10, -12, "left", "top"),
        "prb": (10, 8, "left", "bottom"),
        "competition": (10, 10, "left", "bottom"),
        "delay": (10, 8, "left", "bottom"),
    }
    x_means: list[float] = []
    x_deviations: list[float] = []
    y_means: list[float] = []
    y_deviations: list[float] = []

    for config in CONFIG_ORDER:
        x_mean, x_std = mean_std(
            runs, config, "best_effort_iperf_satisfaction"
        )
        y_mean, y_std = mean_std(
            runs, config, "iperf_protected_satisfaction"
        )
        x_means.append(x_mean)
        x_deviations.append(x_std)
        y_means.append(y_mean)
        y_deviations.append(y_std)
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
        offset_x, offset_y, horizontal_alignment, vertical_alignment = (
            annotations[config]
        )
        ax.annotate(
            CONFIG_LABELS[config].split("\n")[0],
            (x_mean, y_mean),
            xytext=(offset_x, offset_y),
            textcoords="offset points",
            fontsize=9,
            ha=horizontal_alignment,
            va=vertical_alignment,
        )

    x_lower, x_upper = padded_limits(
        x_means,
        x_deviations,
        upper_bound=1.0,
        minimum_span=0.10,
    )
    y_lower, y_upper = padded_limits(
        y_means,
        y_deviations,
        upper_bound=1.0,
        minimum_span=0.08,
    )
    ax.set_title("Protection versus best-effort trade-off\n6 UEs, 20 MHz")
    ax.set_xlabel("Best-effort satisfaction")
    ax.set_ylabel("Protected-priority satisfaction (high + medium)")
    ax.set_xlim(x_lower, x_upper)
    ax.set_ylim(y_lower, y_upper)
    ax.xaxis.set_major_formatter(PercentFormatter(1.0))
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax.grid(alpha=0.25)
    ax.legend(loc="lower left", fontsize=8.5)
    fig.text(
        0.5,
        0.015,
        "Moving upward improves protected traffic; moving right improves "
        "best-effort traffic. Error bars show ±1 sample SD across 3 runs.",
        ha="center",
        fontsize=9,
    )
    fig.tight_layout(rect=(0.02, 0.07, 0.995, 0.98))
    save_figure(fig, output_dir, "03_protection_tradeoff")


def write_summary_csv(runs: RunsByConfig, output_dir: Path) -> None:
    path = output_dir / "weight_results_6ue_20mhz_mean_sd.csv"
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


def write_per_run_csv(runs: RunsByConfig, output_dir: Path) -> None:
    path = output_dir / "weight_results_6ue_20mhz_per_run.csv"
    fields = ["configuration", "run_index", "source_file", *METRICS]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for config in CONFIG_ORDER:
            for run_index, run in enumerate(runs[config], start=1):
                row: dict[str, object] = {
                    "configuration": config,
                    "run_index": run_index,
                    "source_file": run["source_file"],
                }
                row.update({metric: run[metric] for metric in METRICS})
                writer.writerow(row)


def configure_matplotlib() -> None:
    requested_font = "Times New Roman"
    installed = {font.name for font in font_manager.fontManager.ttflist}
    if requested_font not in installed:
        raise RuntimeError(
            "Times New Roman is required but is not registered with "
            "Matplotlib. Rebuild the Matplotlib font cache after installing "
            "the font."
        )

    plt.style.use("default")
    plt.rcParams.update(
        {
            "font.family": requested_font,
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.titleweight": "bold",
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
        }
    )


def main() -> None:
    args = parse_args()
    if args.expected_runs <= 0:
        raise SystemExit("--expected-runs must be positive.")
    if args.expected_cell_capacity <= 0:
        raise SystemExit("--expected-cell-capacity must be positive.")

    configure_matplotlib()
    runs = load_runs(args.input_root, args.expected_cell_capacity)
    counts = {config: len(config_runs) for config, config_runs in runs.items()}
    unexpected = {
        config: count
        for config, count in counts.items()
        if count != args.expected_runs
    }
    if unexpected:
        raise ValueError(
            f"Expected {args.expected_runs} runs per configuration; "
            f"found {counts}."
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    plot_priority_satisfaction(runs, args.output_dir)
    plot_system_metrics(runs, args.output_dir)
    plot_tradeoff(runs, args.output_dir)
    write_summary_csv(runs, args.output_dir)
    write_per_run_csv(runs, args.output_dir)

    print(f"Loaded {sum(counts.values())} analyzer result files: {counts}")
    print(f"Plots and summary tables written to {args.output_dir}")


if __name__ == "__main__":
    main()
