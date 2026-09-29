#!/usr/bin/env python3
"""Plot the final five-pair baseline versus heuristic experiment.

Inputs are the per-run analyzer CSVs, raw xApp CSVs, and scenario markers
below ``testing_results_3ues``.  Throughput, satisfaction, and packet loss use
iPerf receiver-side results.  RLC delay is independently extracted from the
raw xApp CSV using each run's correct marker file.

The primary figures honor the analyzer's ``included_in_aggregate`` flag, so
calibration scenarios 00* and the deliberately extreme scenario 04 are not
mixed into the main comparison.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Optional

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-final-comparison")

import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import Normalize
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator, FuncFormatter, PercentFormatter


MODES = ("baseline", "control")
MODE_LABELS = {
    "baseline": "Control disabled",
    "control": "Heuristic control",
}
MODE_COLORS = {
    "baseline": "#6b7280",
    "control": "#2563eb",
}
MODE_OFFSETS = {"baseline": -0.14, "control": 0.14}

PRIORITIES = ("high", "medium", "best_effort")
PRIORITY_LABELS = {
    "high": "High priority",
    "medium": "Medium priority",
    "best_effort": "Best effort",
}

SCENARIO_ORDER = (
    "01_underload_equal",
    "02_near_capacity_equal",
    "03_moderate_overload_equal",
    "05_best_effort_heavy",
    "06_medium_heavy",
    "07_high_heavy",
    "08_pair_best_effort_high",
    "09_pair_best_effort_medium",
    "10_pair_medium_high_optional",
)
SCENARIO_LABELS = {
    "01_underload_equal": "01 Equal\n5/5/5",
    "02_near_capacity_equal": "02 Equal\n9/9/9",
    "03_moderate_overload_equal": "03 Equal overload\n15/15/15",
    "05_best_effort_heavy": "05 BE-heavy\n25/5/5",
    "06_medium_heavy": "06 M-heavy\n5/25/5",
    "07_high_heavy": "07 H-heavy\n5/5/25",
    "08_pair_best_effort_high": "08 BE–H\n20/0/20",
    "09_pair_best_effort_medium": "09 BE–M\n20/20/0",
    "10_pair_medium_high_optional": "10 M–H\n0/20/20",
}

PRESENTATION_SCENARIOS = (
    "01_underload_equal",
    "03_moderate_overload_equal",
    "08_pair_best_effort_high",
    "09_pair_best_effort_medium",
    "10_pair_medium_high_optional",
)
SHORT_PRIORITY_LABELS = {
    "high": "High",
    "medium": "Medium",
    "best_effort": "Best effort",
}
PRIORITY_COLORS = {
    "high": "#2563eb",
    "medium": "#eab308",
    "best_effort": "#16a34a",
}
DYNAMICS_SCENARIO = "03_moderate_overload_equal"

JITTERS = (-0.050, -0.025, 0.0, 0.025, 0.050)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot the final five-run baseline/control comparison."
    )
    parser.add_argument(
        "--input-root",
        type=Path,
        default=Path(
            "/home/pca/weight_set_optimization/testing_results_3ues"
        ),
        help="Directory containing run1 ... run5.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "final_comparison_plots",
        help="Directory for PNG, CSV, and notes output.",
    )
    parser.add_argument(
        "--warmup",
        type=float,
        default=5.0,
        help="Seconds removed from the start of each delay window.",
    )
    parser.add_argument(
        "--cooldown",
        type=float,
        default=2.0,
        help="Seconds removed from the end of each delay window.",
    )
    parser.add_argument(
        "--max-delay-ms",
        type=float,
        default=6000.0,
        help="Maximum valid raw RLC delay, matching the xApp filter.",
    )
    return parser.parse_args()


def to_float(value: object) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def percentile(values: list[float], probability: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def one_match(paths: Iterable[Path], description: str) -> Path:
    matches = list(paths)
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one {description}; found {len(matches)}: "
            + ", ".join(str(path) for path in matches)
        )
    return matches[0]


def read_dicts(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def discover_experiments(input_root: Path) -> list[dict[str, object]]:
    experiments: list[dict[str, object]] = []
    for run_dir in sorted(input_root.glob("run*")):
        match = re.fullmatch(r"run(\d+)", run_dir.name)
        if not match:
            continue
        run = int(match.group(1))
        for mode in MODES:
            experiment_dir = run_dir / f"{mode}_{run}"
            if not experiment_dir.is_dir():
                raise FileNotFoundError(f"Missing experiment: {experiment_dir}")

            results_path = experiment_dir / f"{mode}_results_{run}.csv"
            raw_name = (
                f"baseline_{run}.csv"
                if mode == "baseline"
                else f"control_enabled_{run}.csv"
            )
            raw_path = experiment_dir / raw_name
            marker_path = one_match(
                experiment_dir.glob("*/scenario_markers.csv"),
                f"marker file below {experiment_dir}",
            )
            for path in (results_path, raw_path, marker_path):
                if not path.is_file():
                    raise FileNotFoundError(path)

            experiments.append(
                {
                    "run": run,
                    "mode": mode,
                    "experiment_dir": experiment_dir,
                    "results_path": results_path,
                    "raw_path": raw_path,
                    "marker_path": marker_path,
                }
            )

    found_runs = sorted(
        {int(experiment["run"]) for experiment in experiments}
    )
    if found_runs != [1, 2, 3, 4, 5]:
        raise ValueError(f"Expected runs 1-5; found {found_runs}")
    if len(experiments) != 10:
        raise ValueError(f"Expected 10 experiments; found {len(experiments)}")
    return experiments


def analysis_window(
    marker: dict[str, str],
    result: dict[str, str],
    warmup: float,
    cooldown: float,
) -> tuple[float, float]:
    marker_start = float(marker["start_ts"])
    marker_end = float(marker["end_ts"])
    traffic_end = marker_end
    duration = to_float(result.get("iperf_test_duration_seconds"))
    if duration is not None and duration > 0:
        traffic_end = min(traffic_end, marker_start + duration)
    start = marker_start + warmup
    end = traffic_end - cooldown
    if end <= start:
        raise ValueError(
            f"Invalid analysis window for {marker.get('scenario', 'unknown')}"
        )
    return start, end


def load_data(
    experiments: list[dict[str, object]],
    warmup: float,
    cooldown: float,
    max_delay_ms: float,
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[str]]:
    priority_rows: list[dict[str, object]] = []
    aggregate_rows: list[dict[str, object]] = []
    warnings: list[str] = []

    for experiment in experiments:
        run = int(experiment["run"])
        mode = str(experiment["mode"])
        results_path = Path(experiment["results_path"])
        raw_path = Path(experiment["raw_path"])
        marker_path = Path(experiment["marker_path"])

        result_rows = read_dicts(results_path)
        marker_rows = read_dicts(marker_path)
        raw_rows = read_dicts(raw_path)
        results = {row["scenario"]: row for row in result_rows}
        markers = {row["scenario"]: row for row in marker_rows}

        if set(results) != set(markers):
            raise ValueError(
                f"Scenario mismatch between {results_path} and {marker_path}"
            )
        incomplete = [
            row["scenario"]
            for row in result_rows
            if row.get("iperf_complete", "").lower() != "true"
        ]
        if incomplete:
            raise ValueError(
                f"Incomplete iPerf results in {results_path}: {incomplete}"
            )

        parsed_delay_rows: list[tuple[float, str, float]] = []
        for row in raw_rows:
            if row.get("trigger") != "style4_ue":
                continue
            if row.get("is_active", "").lower() != "true":
                continue
            timestamp = to_float(row.get("timestamp"))
            delay = to_float(row.get("ue_dl_delay_ms"))
            priority = row.get("priority", "")
            if timestamp is None or delay is None or priority not in PRIORITIES:
                continue
            if 0.0 <= delay <= max_delay_ms:
                parsed_delay_rows.append((timestamp, priority, delay))

        raw_start = to_float(raw_rows[0].get("timestamp")) if raw_rows else None
        raw_end = to_float(raw_rows[-1].get("timestamp")) if raw_rows else None
        window_mismatches: list[str] = []

        for scenario in SCENARIO_ORDER:
            if scenario not in results:
                raise ValueError(f"Missing scenario {scenario} in {results_path}")
            result = results[scenario]
            marker = markers[scenario]
            if result.get("included_in_aggregate", "").lower() != "true":
                raise ValueError(
                    f"Primary scenario unexpectedly excluded: {scenario}"
                )

            start, end = analysis_window(marker, result, warmup, cooldown)
            if raw_start is None or raw_end is None or start < raw_start or end > raw_end:
                raise ValueError(
                    f"Raw xApp CSV does not cover {scenario}: {raw_path}"
                )

            existing_start = to_float(result.get("analysis_start_ts"))
            existing_end = to_float(result.get("analysis_end_ts"))
            if (
                existing_start is not None
                and existing_end is not None
                and (
                    abs(existing_start - start) > 0.5
                    or abs(existing_end - end) > 0.5
                )
            ):
                window_mismatches.append(scenario)

            aggregate_rows.append(
                {
                    "run": run,
                    "mode": mode,
                    "scenario": scenario,
                    "total_goodput_mbps": to_float(
                        result.get("total_iperf_goodput_mbps")
                    ),
                    "cell_utilization_pct": (
                        100.0 * float(result["iperf_cell_capacity_utilization"])
                    ),
                    "demand_efficiency_pct": (
                        100.0 * float(result["iperf_demand_capacity_efficiency"])
                    ),
                    "aggregate_loss_pct": to_float(
                        result.get("iperf_total_loss_percent")
                    ),
                    "cell_capacity_mbps": float(result["cell_capacity_mbps"]),
                }
            )

            offered_fields = {
                "high": "high_offered_mbps",
                "medium": "medium_offered_mbps",
                "best_effort": "be_offered_mbps",
            }
            for priority in PRIORITIES:
                offered = float(result[offered_fields[priority]])
                delay_values = [
                    delay
                    for timestamp, row_priority, delay in parsed_delay_rows
                    if row_priority == priority and start <= timestamp <= end
                ]
                prefix = priority
                priority_rows.append(
                    {
                        "run": run,
                        "mode": mode,
                        "scenario": scenario,
                        "priority": priority,
                        "offered_mbps": offered,
                        "goodput_mbps": to_float(
                            result.get(f"{prefix}_iperf_goodput_mbps")
                        ),
                        "satisfaction_pct": (
                            None
                            if to_float(
                                result.get(f"{prefix}_iperf_satisfaction")
                            )
                            is None
                            else 100.0
                            * float(result[f"{prefix}_iperf_satisfaction"])
                        ),
                        "loss_pct": to_float(
                            result.get(f"{prefix}_iperf_loss_percent")
                        ),
                        "delay_median_ms": (
                            statistics.median(delay_values)
                            if delay_values
                            else None
                        ),
                        "delay_p95_ms": (
                            percentile(delay_values, 0.95)
                            if delay_values
                            else None
                        ),
                        "delay_samples": len(delay_values),
                    }
                )

        if window_mismatches:
            warnings.append(
                f"{results_path}: stored KPM windows do not match its own "
                f"marker for {len(window_mismatches)} primary scenarios "
                f"({', '.join(window_mismatches)}); delay was extracted "
                "from the correct marker instead."
            )

    return priority_rows, aggregate_rows, warnings


def load_control_dynamics(
    experiments: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Load full-traffic-window KPM dynamics for controlled scenario 03."""
    dynamics: list[dict[str, object]] = []
    control_experiments = [
        experiment
        for experiment in experiments
        if experiment["mode"] == "control"
    ]
    if len(control_experiments) != 5:
        raise ValueError(
            f"Expected 5 controlled runs; found {len(control_experiments)}"
        )

    for experiment in control_experiments:
        run = int(experiment["run"])
        raw_path = Path(experiment["raw_path"])
        marker_path = Path(experiment["marker_path"])
        markers = {
            row["scenario"]: row for row in read_dicts(marker_path)
        }
        marker = markers.get(DYNAMICS_SCENARIO)
        if marker is None:
            raise ValueError(
                f"Missing {DYNAMICS_SCENARIO} in {marker_path}"
            )

        start = float(marker["start_ts"])
        end = min(float(marker["end_ts"]), start + 60.0)
        for row in read_dicts(raw_path):
            if row.get("trigger") != "style4_ue":
                continue
            timestamp = to_float(row.get("timestamp"))
            throughput_kbps = to_float(row.get("ue_dl_thp"))
            congestion_score = to_float(row.get("congestion_score"))
            target_cap = to_float(row.get("target_cap"))
            priority = row.get("priority", "")
            if (
                timestamp is None
                or throughput_kbps is None
                or congestion_score is None
                or target_cap is None
                or priority not in PRIORITIES
                or timestamp < start
                or timestamp > end
            ):
                continue
            dynamics.append(
                {
                    "run": run,
                    "second": int(math.floor(timestamp - start)),
                    "priority": priority,
                    "throughput_mbps": throughput_kbps / 1000.0,
                    "congestion_score": congestion_score,
                    "target_cap_pct": target_cap,
                }
            )

    found_runs = sorted({int(row["run"]) for row in dynamics})
    if found_runs != [1, 2, 3, 4, 5]:
        raise ValueError(
            f"Incomplete controlled dynamics; found runs {found_runs}"
        )
    return dynamics


def mean_sd(values: Iterable[object]) -> tuple[float, float]:
    numeric = [float(value) for value in values if value is not None]
    if not numeric:
        return math.nan, math.nan
    return (
        statistics.mean(numeric),
        statistics.stdev(numeric) if len(numeric) > 1 else 0.0,
    )


def select_values(
    rows: list[dict[str, object]],
    scenario: str,
    mode: str,
    field: str,
    priority: Optional[str] = None,
) -> dict[int, float]:
    selected: dict[int, float] = {}
    for row in rows:
        if row["scenario"] != scenario or row["mode"] != mode:
            continue
        if priority is not None and row.get("priority") != priority:
            continue
        value = row.get(field)
        if value is not None:
            selected[int(row["run"])] = float(value)
    return selected


def mode_handles(include_offered: bool = False) -> list[Line2D]:
    handles = [
        Line2D(
            [0],
            [0],
            marker="D",
            linestyle="none",
            markersize=7,
            markerfacecolor=MODE_COLORS[mode],
            markeredgecolor=MODE_COLORS[mode],
            label=MODE_LABELS[mode],
        )
        for mode in MODES
    ]
    if include_offered:
        handles.append(
            Line2D(
                [0],
                [0],
                marker="x",
                linestyle="none",
                markersize=8,
                color="#111827",
                label="Offered bitrate",
            )
        )
    return handles


def draw_paired_group(
    ax: plt.Axes,
    x: float,
    baseline: dict[int, float],
    control: dict[int, float],
) -> None:
    for run in sorted(set(baseline) & set(control)):
        jitter = JITTERS[run - 1] if 1 <= run <= len(JITTERS) else 0.0
        ax.plot(
            [x + MODE_OFFSETS["baseline"] + jitter, x + MODE_OFFSETS["control"] + jitter],
            [baseline[run], control[run]],
            color="#9ca3af",
            linewidth=0.7,
            alpha=0.40,
            zorder=1,
        )

    for mode, values in (("baseline", baseline), ("control", control)):
        if not values:
            continue
        ordered = sorted(values.items())
        xs = [
            x
            + MODE_OFFSETS[mode]
            + (JITTERS[run - 1] if 1 <= run <= len(JITTERS) else 0.0)
            for run, _ in ordered
        ]
        ys = [value for _, value in ordered]
        ax.scatter(
            xs,
            ys,
            s=18,
            color=MODE_COLORS[mode],
            alpha=0.48,
            edgecolors="none",
            zorder=2,
        )
        mean, deviation = mean_sd(ys)
        ax.errorbar(
            x + MODE_OFFSETS[mode],
            mean,
            yerr=deviation,
            fmt="D",
            markersize=6.5,
            color=MODE_COLORS[mode],
            markeredgecolor="white",
            markeredgewidth=0.5,
            capsize=4,
            elinewidth=1.4,
            zorder=4,
        )


def style_scenario_axis(ax: plt.Axes, show_xlabels: bool) -> None:
    x = list(range(len(SCENARIO_ORDER)))
    ax.set_xlim(-0.55, len(SCENARIO_ORDER) - 0.45)
    ax.set_xticks(x)
    if show_xlabels:
        ax.set_xticklabels(
            [SCENARIO_LABELS[scenario] for scenario in SCENARIO_ORDER],
            fontsize=8.5,
        )
    else:
        ax.tick_params(axis="x", labelbottom=False)
    ax.grid(axis="y", alpha=0.24)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def save_figure(fig: plt.Figure, output_dir: Path, stem: str) -> None:
    fig.savefig(output_dir / f"{stem}.png", dpi=220, bbox_inches="tight")
    plt.close(fig)


def plot_priority_metric(
    rows: list[dict[str, object]],
    output_dir: Path,
    field: str,
    ylabel: str,
    title: str,
    stem: str,
    *,
    ylim: Optional[tuple[float, float]] = None,
    percent_axis: bool = False,
    log_axis: bool = False,
    include_offered: bool = False,
) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(15.5, 10.5), sharex=True)
    x_positions = list(range(len(SCENARIO_ORDER)))
    low_delay_sample_marker_used = False

    for panel, priority in enumerate(PRIORITIES):
        ax = axes[panel]
        for x, scenario in zip(x_positions, SCENARIO_ORDER):
            baseline = select_values(rows, scenario, "baseline", field, priority)
            control = select_values(rows, scenario, "control", field, priority)
            draw_paired_group(ax, x, baseline, control)

            if log_axis:
                for mode, values in (("baseline", baseline), ("control", control)):
                    sample_counts = [
                        int(row["delay_samples"])
                        for row in rows
                        if row["scenario"] == scenario
                        and row["priority"] == priority
                        and row["mode"] == mode
                        and float(row["offered_mbps"]) > 0
                    ]
                    if values and sample_counts and min(sample_counts) < 20:
                        ax.annotate(
                            "*",
                            (
                                x + MODE_OFFSETS[mode],
                                statistics.mean(values.values()),
                            ),
                            xytext=(0, 8),
                            textcoords="offset points",
                            ha="center",
                            color=MODE_COLORS[mode],
                            fontsize=14,
                            fontweight="bold",
                            zorder=6,
                        )
                        low_delay_sample_marker_used = True

            if include_offered:
                offered = next(
                    (
                        float(row["offered_mbps"])
                        for row in rows
                        if row["scenario"] == scenario
                        and row["priority"] == priority
                        and float(row["offered_mbps"]) > 0
                    ),
                    None,
                )
                if offered is not None:
                    ax.scatter(
                        x,
                        offered,
                        marker="x",
                        s=42,
                        linewidths=1.5,
                        color="#111827",
                        zorder=5,
                    )

        ax.set_ylabel(f"{PRIORITY_LABELS[priority]}\n{ylabel}")
        if ylim is not None:
            ax.set_ylim(*ylim)
        if percent_axis:
            ax.yaxis.set_major_formatter(PercentFormatter(100.0))
        if log_axis:
            ax.set_yscale("log")
            ax.set_ylim(0.75, 7000.0)
            ticks = [1, 10, 100, 1000, 6000]
            ax.yaxis.set_major_locator(FixedLocator(ticks))
            ax.yaxis.set_major_formatter(
                FuncFormatter(lambda value, _: f"{value:g}")
            )
        style_scenario_axis(ax, panel == len(PRIORITIES) - 1)

    fig.suptitle(title, fontsize=16, fontweight="bold", y=0.985)
    fig.legend(
        handles=mode_handles(include_offered),
        loc="upper center",
        bbox_to_anchor=(0.5, 0.955),
        ncol=3 if include_offered else 2,
        frameon=True,
    )
    note = (
        "Thin lines join matched baseline/control runs. Diamonds show mean ± 1 SD; "
        "small points are the 5 runs. Scenario loads are BE/M/H Mbit/s."
    )
    if log_axis:
        note += " Delay axis is logarithmic; raw samples are filtered to 0–6000 ms."
        if low_delay_sample_marker_used:
            note += " * Fewer than 20 valid delay samples in at least one run."
    fig.text(0.5, 0.018, note, ha="center", fontsize=9)
    fig.tight_layout(rect=(0.025, 0.06, 0.995, 0.915))
    save_figure(fig, output_dir, stem)


def plot_cell_performance(
    rows: list[dict[str, object]], output_dir: Path
) -> None:
    panels = (
        ("total_goodput_mbps", "Aggregate goodput (Mbit/s)", (0.0, 31.0)),
        ("cell_utilization_pct", "Cell-capacity utilization", (45.0, 105.0)),
        ("demand_efficiency_pct", "Demand-capacity efficiency", (75.0, 105.0)),
    )
    fig, axes = plt.subplots(3, 1, figsize=(15.5, 10.5), sharex=True)
    x_positions = list(range(len(SCENARIO_ORDER)))

    for panel, (field, ylabel, ylim) in enumerate(panels):
        ax = axes[panel]
        for x, scenario in zip(x_positions, SCENARIO_ORDER):
            baseline = select_values(rows, scenario, "baseline", field)
            control = select_values(rows, scenario, "control", field)
            draw_paired_group(ax, x, baseline, control)
        ax.set_ylabel(ylabel)
        ax.set_ylim(*ylim)
        if field.endswith("_pct"):
            ax.yaxis.set_major_formatter(PercentFormatter(100.0))
        if field == "total_goodput_mbps":
            capacity = float(rows[0]["cell_capacity_mbps"])
            ax.axhline(
                capacity,
                linestyle="--",
                linewidth=1.2,
                color="#111827",
                alpha=0.7,
                label=f"Capacity reference ({capacity:g} Mbit/s)",
            )
        style_scenario_axis(ax, panel == len(panels) - 1)

    handles = mode_handles()
    handles.append(
        Line2D(
            [0],
            [0],
            linestyle="--",
            color="#111827",
            label=f"Capacity reference ({float(rows[0]['cell_capacity_mbps']):g} Mbit/s)",
        )
    )
    fig.suptitle(
        "Aggregate cell performance: control disabled versus heuristic",
        fontsize=16,
        fontweight="bold",
        y=0.985,
    )
    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.955),
        ncol=3,
        frameon=True,
    )
    fig.text(
        0.5,
        0.018,
        "Receiver-side iPerf results. Thin lines join matched runs; diamonds show "
        "mean ± 1 SD. Scenario loads are BE/M/H Mbit/s.",
        ha="center",
        fontsize=9,
    )
    fig.tight_layout(rect=(0.025, 0.06, 0.995, 0.915))
    save_figure(fig, output_dir, "06_aggregate_cell_performance")


def plot_satisfaction_delta_heatmap(
    rows: list[dict[str, object]], output_dir: Path
) -> None:
    matrix: list[list[float]] = []
    finite_values: list[float] = []
    for scenario in SCENARIO_ORDER:
        row_values: list[float] = []
        for priority in PRIORITIES:
            baseline = select_values(
                rows, scenario, "baseline", "satisfaction_pct", priority
            )
            control = select_values(
                rows, scenario, "control", "satisfaction_pct", priority
            )
            if baseline and control:
                delta = statistics.mean(control.values()) - statistics.mean(
                    baseline.values()
                )
                row_values.append(delta)
                finite_values.append(delta)
            else:
                row_values.append(math.nan)
        matrix.append(row_values)

    limit = max(5.0, math.ceil(max(abs(value) for value in finite_values)))
    masked = [
        [0.0 if math.isnan(value) else value for value in row]
        for row in matrix
    ]
    fig, ax = plt.subplots(figsize=(8.2, 7.1))
    cmap = plt.get_cmap("RdBu").copy()
    image = ax.imshow(
        masked,
        cmap=cmap,
        norm=Normalize(vmin=-limit, vmax=limit),
        aspect="auto",
    )

    for y, row in enumerate(matrix):
        for x, value in enumerate(row):
            if math.isnan(value):
                ax.add_patch(
                    plt.Rectangle(
                        (x - 0.5, y - 0.5),
                        1,
                        1,
                        facecolor="#e5e7eb",
                        edgecolor="white",
                    )
                )
                label = "N/A"
                color = "#6b7280"
            else:
                label = f"{value:+.1f}"
                color = "white" if abs(value) > 0.48 * limit else "#111827"
            ax.text(x, y, label, ha="center", va="center", color=color, fontsize=10)

    ax.set_xticks(range(len(PRIORITIES)))
    ax.set_xticklabels([PRIORITY_LABELS[p] for p in PRIORITIES])
    ax.set_yticks(range(len(SCENARIO_ORDER)))
    ax.set_yticklabels([SCENARIO_LABELS[s].replace("\n", " ") for s in SCENARIO_ORDER])
    ax.set_title(
        "Satisfaction change caused by heuristic control",
        fontsize=15,
        fontweight="bold",
        pad=14,
    )
    colorbar = fig.colorbar(image, ax=ax, shrink=0.86)
    colorbar.set_label("Control minus baseline (percentage points)")
    ax.set_xticks(
        [position - 0.5 for position in range(1, len(PRIORITIES))], minor=True
    )
    ax.set_yticks(
        [position - 0.5 for position in range(1, len(SCENARIO_ORDER))],
        minor=True,
    )
    ax.grid(which="minor", color="white", linewidth=1.5)
    ax.tick_params(which="minor", bottom=False, left=False)
    fig.text(
        0.5,
        0.018,
        "Values are mean percentage-point changes across 5 matched runs. "
        "Positive values indicate greater satisfaction with control enabled.",
        ha="center",
        fontsize=9,
    )
    fig.tight_layout(rect=(0.02, 0.05, 0.98, 0.98))
    save_figure(fig, output_dir, "07_satisfaction_change_heatmap")


def active_priorities_for_scenario(
    rows: list[dict[str, object]], scenario: str
) -> list[str]:
    active = []
    for priority in PRIORITIES:
        if any(
            row["scenario"] == scenario
            and row["priority"] == priority
            and float(row["offered_mbps"]) > 0
            for row in rows
        ):
            active.append(priority)
    return active


def presentation_scenario_title(scenario: str) -> str:
    label, offered = SCENARIO_LABELS[scenario].split("\n", maxsplit=1)
    return f"{label}\nOffered BE/M/H: {offered} Mbit/s"


def draw_bar_run_points(
    ax: plt.Axes,
    center: float,
    values: dict[int, float],
    color: str,
    bar_width: float,
    value_format: str,
) -> None:
    if not values:
        return
    ordered = sorted(values.items())
    numeric = [value for _, value in ordered]
    mean, deviation = mean_sd(numeric)
    ax.bar(
        center,
        mean,
        width=bar_width,
        color=color,
        alpha=0.86,
        yerr=deviation,
        capsize=4,
        error_kw={"elinewidth": 1.3, "ecolor": "#111827"},
        zorder=2,
    )
    point_jitters = [jitter * 0.70 for jitter in JITTERS]
    ax.scatter(
        [
            center
            + (point_jitters[run - 1] if 1 <= run <= len(point_jitters) else 0.0)
            for run, _ in ordered
        ],
        numeric,
        s=20,
        color=color,
        edgecolors="white",
        linewidths=0.45,
        alpha=0.95,
        zorder=4,
    )
    label_y = mean + deviation
    ax.annotate(
        format(mean, value_format),
        (center, label_y),
        xytext=(0, 7),
        textcoords="offset points",
        ha="center",
        va="bottom",
        fontsize=8.5,
        color="#111827",
        zorder=5,
    )


def plot_selected_priority_bars(
    rows: list[dict[str, object]],
    output_dir: Path,
    field: str,
    ylabel: str,
    title: str,
    stem: str,
    ylim: tuple[float, float],
    value_format: str,
    percent_axis: bool = False,
) -> None:
    fig, axes = plt.subplots(2, 3, figsize=(14.5, 8.4))
    axes_flat = list(axes.flat)
    bar_width = 0.34

    for panel, scenario in enumerate(PRESENTATION_SCENARIOS):
        ax = axes_flat[panel]
        priorities = active_priorities_for_scenario(rows, scenario)
        x_positions = list(range(len(priorities)))
        for mode_index, mode in enumerate(MODES):
            offset = (-0.19, 0.19)[mode_index]
            for x, priority in zip(x_positions, priorities):
                values = select_values(rows, scenario, mode, field, priority)
                draw_bar_run_points(
                    ax,
                    x + offset,
                    values,
                    MODE_COLORS[mode],
                    bar_width,
                    value_format,
                )

        ax.set_title(presentation_scenario_title(scenario), fontsize=11)
        ax.set_xticks(
            x_positions,
            [SHORT_PRIORITY_LABELS[priority] for priority in priorities],
        )
        ax.set_ylabel(ylabel)
        ax.set_ylim(*ylim)
        if percent_axis:
            ax.yaxis.set_major_formatter(PercentFormatter(100.0))
        ax.grid(axis="y", alpha=0.24)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    axes_flat[-1].axis("off")
    fig.suptitle(title, fontsize=16, fontweight="bold", y=0.985)
    fig.legend(
        handles=mode_handles(),
        loc="upper center",
        bbox_to_anchor=(0.5, 0.945),
        ncol=2,
        frameon=True,
    )
    fig.text(
        0.5,
        0.018,
        "Bars show the mean; error bars show ±1 SD; points show all 5 runs. "
        "Only representative scenarios are shown.",
        ha="center",
        fontsize=9,
    )
    fig.tight_layout(rect=(0.025, 0.055, 0.995, 0.90))
    save_figure(fig, output_dir, stem)


def plot_selected_aggregate_goodput_bars(
    rows: list[dict[str, object]], output_dir: Path
) -> None:
    fig, ax = plt.subplots(figsize=(11.8, 6.0))
    bar_width = 0.34
    x_positions = list(range(len(PRESENTATION_SCENARIOS)))

    for mode_index, mode in enumerate(MODES):
        offset = (-0.19, 0.19)[mode_index]
        for x, scenario in zip(x_positions, PRESENTATION_SCENARIOS):
            values = select_values(rows, scenario, mode, "total_goodput_mbps")
            draw_bar_run_points(
                ax,
                x + offset,
                values,
                MODE_COLORS[mode],
                bar_width,
                ".2f",
            )

    capacity = float(rows[0]["cell_capacity_mbps"])
    ax.axhline(
        capacity,
        color="#111827",
        linestyle="--",
        linewidth=1.3,
        alpha=0.75,
    )
    ax.set_title(
        "Aggregate downlink goodput in representative scenarios",
        fontsize=16,
        fontweight="bold",
        pad=64,
    )
    ax.set_ylabel("Aggregate receiver goodput (Mbit/s)")
    ax.set_xticks(
        x_positions,
        [SCENARIO_LABELS[scenario] for scenario in PRESENTATION_SCENARIOS],
    )
    ax.set_ylim(0.0, 31.5)
    ax.grid(axis="y", alpha=0.24)
    handles = mode_handles()
    handles.append(
        Line2D(
            [0],
            [0],
            color="#111827",
            linestyle="--",
            label=f"Capacity reference ({capacity:g} Mbit/s)",
        )
    )
    ax.legend(
        handles=handles,
        loc="lower center",
        bbox_to_anchor=(0.5, 1.01),
        ncol=3,
        frameon=True,
    )
    fig.text(
        0.5,
        0.018,
        "Bars show mean ± 1 SD; points show all 5 runs. Receiver-side iPerf results.",
        ha="center",
        fontsize=9,
    )
    fig.tight_layout(rect=(0.025, 0.07, 0.995, 0.98))
    save_figure(fig, output_dir, "11_simple_aggregate_goodput_bars")


def plot_selected_delay_points(
    rows: list[dict[str, object]], output_dir: Path
) -> None:
    fig, axes = plt.subplots(2, 3, figsize=(14.5, 8.4))
    axes_flat = list(axes.flat)
    low_sample_marker_used = False

    for panel, scenario in enumerate(PRESENTATION_SCENARIOS):
        ax = axes_flat[panel]
        priorities = active_priorities_for_scenario(rows, scenario)
        x_positions = list(range(len(priorities)))
        for mode in MODES:
            for x, priority in zip(x_positions, priorities):
                values = select_values(
                    rows, scenario, mode, "delay_median_ms", priority
                )
                if not values:
                    continue
                ordered = sorted(values.items())
                numeric = [value for _, value in ordered]
                mean, deviation = mean_sd(numeric)
                center = x + MODE_OFFSETS[mode]
                ax.scatter(
                    [
                        center
                        + (JITTERS[run - 1] if 1 <= run <= len(JITTERS) else 0.0)
                        for run, _ in ordered
                    ],
                    numeric,
                    s=20,
                    color=MODE_COLORS[mode],
                    alpha=0.55,
                    edgecolors="none",
                    zorder=2,
                )
                ax.errorbar(
                    center,
                    mean,
                    yerr=deviation,
                    fmt="D",
                    markersize=7,
                    color=MODE_COLORS[mode],
                    markeredgecolor="white",
                    markeredgewidth=0.5,
                    capsize=4,
                    elinewidth=1.3,
                    zorder=4,
                )
                sample_counts = [
                    int(row["delay_samples"])
                    for row in rows
                    if row["scenario"] == scenario
                    and row["priority"] == priority
                    and row["mode"] == mode
                    and float(row["offered_mbps"]) > 0
                ]
                if sample_counts and min(sample_counts) < 20:
                    ax.annotate(
                        "*",
                        (center, mean),
                        xytext=(0, 8),
                        textcoords="offset points",
                        ha="center",
                        color=MODE_COLORS[mode],
                        fontsize=14,
                        fontweight="bold",
                    )
                    low_sample_marker_used = True

        ax.set_title(presentation_scenario_title(scenario), fontsize=11)
        ax.set_xticks(
            x_positions,
            [SHORT_PRIORITY_LABELS[priority] for priority in priorities],
        )
        ax.set_ylabel("Median RLC delay (ms)")
        ax.set_yscale("log")
        ax.set_ylim(0.75, 7000.0)
        ticks = [1, 10, 100, 1000, 6000]
        ax.yaxis.set_major_locator(FixedLocator(ticks))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
        ax.grid(axis="y", alpha=0.24)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    axes_flat[-1].axis("off")
    fig.suptitle(
        "Median RLC downlink delay in representative scenarios",
        fontsize=16,
        fontweight="bold",
        y=0.985,
    )
    fig.legend(
        handles=mode_handles(),
        loc="upper center",
        bbox_to_anchor=(0.5, 0.945),
        ncol=2,
        frameon=True,
    )
    note = (
        "Logarithmic delay axis. Diamonds show mean ± 1 SD; points show all "
        "5 run-level medians. Samples are filtered to 0–6000 ms."
    )
    if low_sample_marker_used:
        note += " * Fewer than 20 valid samples in at least one run."
    fig.text(0.5, 0.018, note, ha="center", fontsize=9)
    fig.tight_layout(rect=(0.025, 0.055, 0.995, 0.90))
    save_figure(fig, output_dir, "12_simple_median_rlc_delay_points")


def dynamics_series(
    rows: list[dict[str, object]],
    field: str,
    priority: Optional[str] = None,
) -> tuple[list[int], list[float], list[float]]:
    per_run_second: dict[tuple[int, int], list[float]] = defaultdict(list)
    for row in rows:
        if priority is not None and row["priority"] != priority:
            continue
        per_run_second[(int(row["run"]), int(row["second"]))].append(
            float(row[field])
        )

    collapsed = {
        key: statistics.mean(values)
        for key, values in per_run_second.items()
    }
    seconds = sorted({second for _, second in collapsed})
    means: list[float] = []
    deviations: list[float] = []
    for second in seconds:
        values = [
            value
            for (run, row_second), value in collapsed.items()
            if row_second == second
        ]
        means.append(statistics.mean(values))
        deviations.append(
            statistics.stdev(values) if len(values) > 1 else 0.0
        )
    return seconds, means, deviations


def plot_control_dynamics(
    rows: list[dict[str, object]], output_dir: Path, warmup: float
) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(12.8, 9.2), sharex=True)
    throughput_ax, score_ax, cap_ax = axes

    for priority in PRIORITIES:
        seconds, means, deviations = dynamics_series(
            rows, "throughput_mbps", priority
        )
        lower = [max(0.0, mean - deviation) for mean, deviation in zip(means, deviations)]
        upper = [mean + deviation for mean, deviation in zip(means, deviations)]
        color = PRIORITY_COLORS[priority]
        throughput_ax.plot(
            seconds,
            means,
            color=color,
            linewidth=2.0,
            label=PRIORITY_LABELS[priority],
        )
        throughput_ax.fill_between(
            seconds,
            lower,
            upper,
            color=color,
            alpha=0.14,
            linewidth=0,
        )

    seconds, score_means, score_deviations = dynamics_series(
        rows, "congestion_score"
    )
    score_lower = [
        max(0.0, mean - deviation)
        for mean, deviation in zip(score_means, score_deviations)
    ]
    score_upper = [
        min(1.0, mean + deviation)
        for mean, deviation in zip(score_means, score_deviations)
    ]
    score_ax.plot(
        seconds,
        score_means,
        color="#dc2626",
        linewidth=2.2,
        label="Congestion score",
    )
    score_ax.fill_between(
        seconds,
        score_lower,
        score_upper,
        color="#dc2626",
        alpha=0.15,
        linewidth=0,
    )

    for priority in PRIORITIES:
        seconds, means, deviations = dynamics_series(
            rows, "target_cap_pct", priority
        )
        lower = [max(0.0, mean - deviation) for mean, deviation in zip(means, deviations)]
        upper = [min(100.0, mean + deviation) for mean, deviation in zip(means, deviations)]
        color = PRIORITY_COLORS[priority]
        cap_ax.step(
            seconds,
            means,
            where="mid",
            color=color,
            linewidth=2.0,
            label=PRIORITY_LABELS[priority],
        )
        cap_ax.fill_between(
            seconds,
            lower,
            upper,
            step="mid",
            color=color,
            alpha=0.14,
            linewidth=0,
        )

    for ax in axes:
        ax.axvline(
            warmup,
            color="#111827",
            linestyle="--",
            linewidth=1.1,
            alpha=0.75,
        )
        ax.set_xlim(0.0, 60.0)
        ax.grid(alpha=0.24)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    throughput_ax.set_ylabel("KPM DL bitrate\n(Mbit/s)")
    throughput_ax.set_ylim(0.0, 16.5)
    throughput_ax.legend(loc="lower right", ncol=3, frameon=True)

    score_ax.set_ylabel("Congestion score")
    score_ax.set_ylim(0.0, 1.02)
    score_ax.legend(loc="lower right", frameon=True)

    cap_ax.set_ylabel("Target PRB cap (%)")
    cap_ax.set_ylim(0.0, 105.0)
    cap_ax.set_xlabel("Time since traffic start (s)")
    cap_ax.legend(loc="lower right", ncol=3, frameon=True)
    cap_ax.text(
        warmup + 0.6,
        4.0,
        "End of analysis warm-up",
        fontsize=9,
        color="#111827",
        ha="left",
        va="bottom",
    )

    fig.suptitle(
        "Heuristic control dynamics — scenario 03 equal overload (15/15/15 Mbit/s)",
        fontsize=16,
        fontweight="bold",
        y=0.985,
    )
    fig.text(
        0.5,
        0.018,
        "Lines show the mean across 5 controlled runs; shaded regions show ±1 SD. "
        "The full traffic interval is shown. Bitrate is the xApp's KPM measurement.",
        ha="center",
        fontsize=9,
    )
    fig.tight_layout(rect=(0.035, 0.055, 0.995, 0.95))
    save_figure(fig, output_dir, "13_control_dynamics_scenario_03")


def write_long_csv(
    rows: list[dict[str, object]], output_dir: Path
) -> None:
    fields = [
        "run",
        "mode",
        "scenario",
        "priority",
        "offered_mbps",
        "goodput_mbps",
        "satisfaction_pct",
        "loss_pct",
        "delay_median_ms",
        "delay_p95_ms",
        "delay_samples",
    ]
    with (output_dir / "final_priority_metrics_per_run.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field) for field in fields} for row in rows)


def write_summary_csv(
    rows: list[dict[str, object]], output_dir: Path
) -> None:
    metrics = (
        "goodput_mbps",
        "satisfaction_pct",
        "loss_pct",
        "delay_median_ms",
        "delay_p95_ms",
    )
    fields = ["mode", "scenario", "priority", "runs"]
    for metric in metrics:
        fields.extend([f"{metric}_mean", f"{metric}_sd"])
    fields.extend(["delay_samples_min", "delay_samples_total"])

    with (output_dir / "final_priority_metrics_mean_sd.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for scenario in SCENARIO_ORDER:
            for priority in PRIORITIES:
                for mode in MODES:
                    group = [
                        row
                        for row in rows
                        if row["scenario"] == scenario
                        and row["priority"] == priority
                        and row["mode"] == mode
                    ]
                    output: dict[str, object] = {
                        "mode": mode,
                        "scenario": scenario,
                        "priority": priority,
                        "runs": len(group),
                    }
                    for metric in metrics:
                        mean, deviation = mean_sd(row.get(metric) for row in group)
                        output[f"{metric}_mean"] = mean
                        output[f"{metric}_sd"] = deviation
                    sample_counts = [int(row["delay_samples"]) for row in group]
                    output["delay_samples_min"] = min(sample_counts)
                    output["delay_samples_total"] = sum(sample_counts)
                    writer.writerow(output)


def write_notes(
    output_dir: Path,
    input_root: Path,
    warnings: list[str],
    priority_rows: list[dict[str, object]],
    plot_font: str,
) -> None:
    low_sample_groups: list[str] = []
    groups: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for row in priority_rows:
        if float(row["offered_mbps"]) > 0:
            groups[(str(row["mode"]), str(row["scenario"]), str(row["priority"]))].append(
                int(row["delay_samples"])
            )
    for key, counts in groups.items():
        if counts and min(counts) < 20:
            low_sample_groups.append(
                f"{key[0]} / {key[1]} / {key[2]}: per-run counts {counts}"
            )

    lines = [
        "Final baseline-versus-control plot notes",
        "==========================================",
        f"Input root: {input_root}",
        "Runs: 5 matched baseline/control pairs",
        "Primary scenarios: 01, 02, 03, and 05-10 (9 scenarios)",
        "Excluded from primary figures: 00* calibration and 04 extreme stress",
        "Goodput/satisfaction/loss source: receiver-side iPerf analyzer fields",
        "Delay source: raw xApp ue_dl_delay_ms, style4_ue active rows only",
        "Delay window: correct per-run marker, warmup=5 s, cooldown=2 s",
        "Delay validity filter: 0 <= delay <= 6000 ms",
        "Error bars: sample standard deviation across the 5 runs",
        "Small points: individual runs; thin lines: matched run pairs",
        f"Plot font: {plot_font}",
        "Dynamics figure: full scenario-03 traffic interval; KPM bitrate, congestion score, and target caps averaged across 5 controlled runs",
        "",
        "Warnings:",
    ]
    lines.extend(f"- {warning}" for warning in warnings)
    if not warnings:
        lines.append("- None")
    lines.extend(["", "Low valid-delay-sample groups (<20 in any run):"])
    lines.extend(f"- {entry}" for entry in low_sample_groups)
    if not low_sample_groups:
        lines.append("- None")
    (output_dir / "README_plot_method.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def configure_matplotlib() -> str:
    plt.style.use("default")
    requested_font = "Times New Roman"
    available_fonts = {
        font.name for font in font_manager.fontManager.ttflist
    }
    if requested_font in available_fonts:
        selected_font = requested_font
    elif "Liberation Serif" in available_fonts:
        selected_font = "Liberation Serif"
        print(
            "Warning: Times New Roman is not installed; using "
            "Liberation Serif as the metrically compatible fallback."
        )
    else:
        selected_font = "serif"
        print(
            "Warning: Times New Roman is not installed and Liberation Serif "
            "is unavailable; using the default serif font."
        )
    plt.rcParams.update(
        {
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.titleweight": "bold",
            "font.family": selected_font,
            "font.size": 10,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
        }
    )
    return selected_font


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    plot_font = configure_matplotlib()

    experiments = discover_experiments(args.input_root)
    priority_rows, aggregate_rows, warnings = load_data(
        experiments,
        warmup=args.warmup,
        cooldown=args.cooldown,
        max_delay_ms=args.max_delay_ms,
    )
    dynamics_rows = load_control_dynamics(experiments)

    plot_priority_metric(
        priority_rows,
        args.output_dir,
        "goodput_mbps",
        "Goodput (Mbit/s)",
        "Per-priority downlink goodput",
        "01_priority_goodput",
        ylim=(0.0, 27.0),
        include_offered=True,
    )
    plot_priority_metric(
        priority_rows,
        args.output_dir,
        "satisfaction_pct",
        "Satisfaction",
        "Per-priority demand satisfaction",
        "02_priority_satisfaction",
        ylim=(0.0, 105.0),
        percent_axis=True,
    )
    plot_priority_metric(
        priority_rows,
        args.output_dir,
        "loss_pct",
        "Packet loss",
        "Per-priority downlink packet loss",
        "03_priority_packet_loss",
        ylim=(0.0, 62.0),
        percent_axis=True,
    )
    plot_priority_metric(
        priority_rows,
        args.output_dir,
        "delay_median_ms",
        "Median RLC delay (ms)",
        "Per-priority median RLC downlink delay",
        "04_rlc_delay_median",
        log_axis=True,
    )
    plot_priority_metric(
        priority_rows,
        args.output_dir,
        "delay_p95_ms",
        "95th-percentile RLC delay (ms)",
        "Per-priority 95th-percentile RLC downlink delay",
        "05_rlc_delay_p95",
        log_axis=True,
    )
    plot_cell_performance(aggregate_rows, args.output_dir)
    plot_satisfaction_delta_heatmap(priority_rows, args.output_dir)
    plot_selected_priority_bars(
        priority_rows,
        args.output_dir,
        "satisfaction_pct",
        "Satisfaction",
        "Demand satisfaction: representative scenarios",
        "08_simple_satisfaction_bars",
        ylim=(0.0, 112.0),
        value_format=".1f",
        percent_axis=True,
    )
    plot_selected_priority_bars(
        priority_rows,
        args.output_dir,
        "goodput_mbps",
        "Goodput (Mbit/s)",
        "Per-priority goodput: representative scenarios",
        "09_simple_priority_goodput_bars",
        ylim=(0.0, 18.5),
        value_format=".2f",
    )
    plot_selected_priority_bars(
        priority_rows,
        args.output_dir,
        "loss_pct",
        "Packet loss",
        "Packet loss: representative scenarios",
        "10_simple_packet_loss_bars",
        ylim=(0.0, 64.0),
        value_format=".1f",
        percent_axis=True,
    )
    plot_selected_aggregate_goodput_bars(aggregate_rows, args.output_dir)
    plot_selected_delay_points(priority_rows, args.output_dir)
    plot_control_dynamics(dynamics_rows, args.output_dir, args.warmup)
    write_long_csv(priority_rows, args.output_dir)
    write_summary_csv(priority_rows, args.output_dir)
    write_notes(
        args.output_dir,
        args.input_root,
        warnings,
        priority_rows,
        plot_font,
    )

    print(f"Generated final comparison plots in {args.output_dir}")
    if warnings:
        print(f"Recorded {len(warnings)} analyzer-window warning(s) in README_plot_method.txt")


if __name__ == "__main__":
    main()
