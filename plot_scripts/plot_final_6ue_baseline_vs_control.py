#!/usr/bin/env python3
"""Plot matched baseline-versus-control experiments for the six-UE matrix.

The script consumes the scenario-level and long per-UE CSV files produced by
``analyze_qos_6ue_results.py`` (or an analyzer with the same output contract),
plus the raw xApp CSV and scenario markers belonging to every experiment.
Endpoint goodput, satisfaction and loss come from receiver-side iPerf results.
RLC delay and within-priority fairness come from the analyzer's per-UE CSV.

Input layout mirrors the established three-UE workflow::

    testing_results_6ues/
      run1/
        baseline_1/
          baseline_1.csv
          baseline_results_1.csv
          baseline_results_1_per_ue.csv
          <timestamp>/scenario_markers.csv
        control_1/
          control_enabled_1.csv
          control_results_1.csv
          control_results_1_per_ue.csv
          <timestamp>/scenario_markers.csv

Any number of complete paired runs is accepted, with a minimum of two.
Figures are written as PNG only. Genuine Times New Roman is required so that
missing-font substitution cannot silently change thesis figures.
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
from typing import Iterable, Optional, Sequence

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-final-comparison-6ues")

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
MODE_OFFSETS = {"baseline": -0.15, "control": 0.15}

PRIORITIES = ("high", "medium", "best_effort")
PRIORITY_LABELS = {
    "high": "High priority",
    "medium": "Medium priority",
    "best_effort": "Best effort",
}
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

SCENARIO_ORDER = (
    "01_underload_equal",
    "02_near_capacity_equal",
    "03_moderate_overload_equal",
    "04_best_effort_heavy",
    "05_medium_heavy",
    "06_high_heavy",
    "07_pair_best_effort_high",
    "08_pair_best_effort_medium",
    "09_pair_medium_high_optional",
    "10_overload_best_effort",
    "11_overload_medium",
    "12_overload_high",
    "13_heterogeneous_equal_aggregate",
)
SCENARIO_LABELS = {
    "01_underload_equal": "01 Equal\n5/5/5",
    "02_near_capacity_equal": "02 Equal\n9/9/9",
    "03_moderate_overload_equal": "03 Equal overload\n10/10/10",
    "04_best_effort_heavy": "04 BE-heavy\n14/8/8",
    "05_medium_heavy": "05 M-heavy\n8/14/8",
    "06_high_heavy": "06 H-heavy\n8/8/14",
    "07_pair_best_effort_high": "07 BE–H\n15/0/15",
    "08_pair_best_effort_medium": "08 BE–M\n15/15/0",
    "09_pair_medium_high_optional": "09 M–H\n0/15/15",
    "10_overload_best_effort": "10 BE-led\n12/10/8",
    "11_overload_medium": "11 M-led\n8/12/10",
    "12_overload_high": "12 H-led\n8/10/12",
    "13_heterogeneous_equal_aggregate": (
        "13 Heterogeneous\n3.33/6.67 pairs"
    ),
}
REPRESENTATIVE_SCENARIOS = (
    "01_underload_equal",
    "03_moderate_overload_equal",
    "07_pair_best_effort_high",
    "08_pair_best_effort_medium",
    "09_pair_medium_high_optional",
    "10_overload_best_effort",
    "11_overload_medium",
    "12_overload_high",
    "13_heterogeneous_equal_aggregate",
)
PER_UE_SCENARIOS = (
    "03_moderate_overload_equal",
    "13_heterogeneous_equal_aggregate",
)
DYNAMICS_SCENARIO = "03_moderate_overload_equal"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Plot matched baseline/control results for the six-UE test matrix."
        )
    )
    parser.add_argument(
        "--input-root",
        type=Path,
        default=Path("/home/pca/weight_set_optimization/testing_results_6ues"),
        help="Directory containing paired runN/baseline_N and control_N results.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "final_comparison_plots_6ues",
        help="Distinct directory for PNG, CSV, and methodology-note output.",
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


def required_float(row: dict[str, str], field: str, context: str) -> float:
    value = to_float(row.get(field))
    if value is None:
        raise ValueError(f"Missing or invalid {field!r} in {context}")
    return value


def to_bool(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def read_dicts(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {path}")
        return list(reader)


def require_columns(
    rows: list[dict[str, str]], required: set[str], path: Path
) -> None:
    columns = set(rows[0]) if rows else set()
    missing = required - columns
    if missing:
        raise ValueError(f"{path} is missing columns: {sorted(missing)}")


def one_match(paths: Iterable[Path], description: str) -> Path:
    matches = list(paths)
    if len(matches) != 1:
        rendered = ", ".join(str(path) for path in matches) or "none"
        raise ValueError(
            f"Expected exactly one {description}; found {len(matches)}: {rendered}"
        )
    return matches[0]


def natural_ue_key(value: object) -> tuple[int, str]:
    text = str(value)
    match = re.search(r"(\d+)$", text)
    return (int(match.group(1)) if match else 10**9, text)


def discover_experiments(
    input_root: Path,
) -> tuple[list[dict[str, object]], list[int]]:
    if not input_root.is_dir():
        raise FileNotFoundError(f"Input root does not exist: {input_root}")

    run_dirs: list[tuple[int, Path]] = []
    for path in input_root.glob("run*"):
        match = re.fullmatch(r"run(\d+)", path.name)
        if match and path.is_dir():
            run_dirs.append((int(match.group(1)), path))
    run_dirs.sort()
    if len(run_dirs) < 2:
        raise ValueError(
            f"Expected at least two paired runN directories below {input_root}; "
            f"found {len(run_dirs)}"
        )

    experiments: list[dict[str, object]] = []
    for run, run_dir in run_dirs:
        for mode in MODES:
            experiment_dir = run_dir / f"{mode}_{run}"
            if not experiment_dir.is_dir():
                raise FileNotFoundError(
                    f"Run {run} is not a complete pair; missing {experiment_dir}"
                )

            results_path = experiment_dir / f"{mode}_results_{run}.csv"
            per_ue_path = experiment_dir / f"{mode}_results_{run}_per_ue.csv"
            raw_path = experiment_dir / (
                f"baseline_{run}.csv"
                if mode == "baseline"
                else f"control_enabled_{run}.csv"
            )
            marker_path = one_match(
                experiment_dir.glob("*/scenario_markers.csv"),
                f"scenario_markers.csv below {experiment_dir}",
            )
            for path in (results_path, per_ue_path, raw_path, marker_path):
                if not path.is_file():
                    raise FileNotFoundError(path)

            experiments.append(
                {
                    "run": run,
                    "mode": mode,
                    "experiment_dir": experiment_dir,
                    "results_path": results_path,
                    "per_ue_path": per_ue_path,
                    "raw_path": raw_path,
                    "marker_path": marker_path,
                }
            )

    runs = [run for run, _ in run_dirs]
    return experiments, runs


def mean_sd(values: Iterable[object]) -> tuple[float, float]:
    numeric = [float(value) for value in values if value is not None]
    if not numeric:
        return math.nan, math.nan
    return (
        statistics.mean(numeric),
        statistics.stdev(numeric) if len(numeric) > 1 else 0.0,
    )


def mean_or_none(values: Iterable[Optional[float]]) -> Optional[float]:
    numeric = [float(value) for value in values if value is not None]
    return statistics.mean(numeric) if numeric else None


def jain_fairness(values: Sequence[float]) -> Optional[float]:
    """Return Jain's index, or None when fairness is not assessable."""
    if len(values) < 2:
        return None
    nonnegative = [max(0.0, float(value)) for value in values]
    squared_sum = sum(value * value for value in nonnegative)
    if squared_sum <= 0.0:
        return None
    return (sum(nonnegative) ** 2) / (len(nonnegative) * squared_sum)


def load_data(
    experiments: list[dict[str, object]],
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[str],
]:
    priority_rows: list[dict[str, object]] = []
    aggregate_rows: list[dict[str, object]] = []
    per_ue_rows: list[dict[str, object]] = []
    fairness_rows: list[dict[str, object]] = []
    warnings: list[str] = []

    result_required = {
        "scenario",
        "included_in_aggregate",
        "be_offered_mbps",
        "medium_offered_mbps",
        "high_offered_mbps",
        "cell_capacity_mbps",
        "iperf_complete",
        "total_iperf_goodput_mbps",
        "iperf_cell_capacity_utilization",
        "iperf_demand_capacity_efficiency",
        "iperf_total_loss_percent",
        "iperf_test_duration_seconds",
    }
    for priority in PRIORITIES:
        result_required.update(
            {
                f"{priority}_iperf_goodput_mbps",
                f"{priority}_iperf_satisfaction",
                f"{priority}_iperf_loss_percent",
            }
        )

    per_ue_required = {
        "scenario",
        "included_in_aggregate",
        "ue_namespace",
        "ue_id",
        "priority",
        "traffic_active",
        "offered_mbps",
        "kpm_actual_mbps",
        "kpm_satisfaction",
        "delay_median_ms",
        "delay_p95_ms",
        "delay_samples",
        "avg_target_cap",
        "iperf_status",
        "iperf_goodput_mbps",
        "iperf_satisfaction",
        "iperf_loss_percent",
        "iperf_jitter_ms",
        "iperf_packets",
        "iperf_lost_packets",
    }

    capacity_values: list[float] = []
    for experiment in experiments:
        run = int(experiment["run"])
        mode = str(experiment["mode"])
        results_path = Path(experiment["results_path"])
        per_ue_path = Path(experiment["per_ue_path"])
        marker_path = Path(experiment["marker_path"])

        result_rows = read_dicts(results_path)
        ue_source_rows = read_dicts(per_ue_path)
        marker_rows = read_dicts(marker_path)
        require_columns(result_rows, result_required, results_path)
        require_columns(ue_source_rows, per_ue_required, per_ue_path)

        results = {row["scenario"]: row for row in result_rows}
        markers = {row["scenario"]: row for row in marker_rows}
        ue_by_scenario: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in ue_source_rows:
            ue_by_scenario[row["scenario"]].append(row)

        missing_results = [s for s in SCENARIO_ORDER if s not in results]
        missing_markers = [s for s in SCENARIO_ORDER if s not in markers]
        missing_ue = [s for s in SCENARIO_ORDER if s not in ue_by_scenario]
        if missing_results or missing_markers or missing_ue:
            raise ValueError(
                f"Incomplete six-UE matrix in {experiment['experiment_dir']}: "
                f"results={missing_results}, markers={missing_markers}, "
                f"per_ue={missing_ue}"
            )

        for scenario in SCENARIO_ORDER:
            result = results[scenario]
            context = f"{results_path}, scenario {scenario}"
            if not to_bool(result.get("included_in_aggregate")):
                raise ValueError(f"Primary scenario excluded in {context}")
            if not to_bool(result.get("iperf_complete")):
                raise ValueError(f"Incomplete iPerf endpoint results in {context}")

            normalized_ues: list[dict[str, object]] = []
            seen_names: set[str] = set()
            for ue_row in ue_by_scenario[scenario]:
                ue_name = ue_row.get("ue_namespace", "").strip()
                priority = ue_row.get("priority", "").strip()
                if not ue_name or ue_name in seen_names:
                    raise ValueError(
                        f"Missing or duplicate UE namespace in {per_ue_path}, "
                        f"scenario {scenario}: {ue_name!r}"
                    )
                if priority not in PRIORITIES:
                    raise ValueError(
                        f"Unknown priority {priority!r} for {ue_name} in {per_ue_path}"
                    )
                seen_names.add(ue_name)
                active = to_bool(ue_row.get("traffic_active"))
                offered = required_float(
                    ue_row, "offered_mbps", f"{per_ue_path}, {scenario}, {ue_name}"
                )
                status = ue_row.get("iperf_status", "").strip()
                if active and offered > 0.0 and status != "ok":
                    raise ValueError(
                        f"Expected valid iPerf data for {scenario}/{ue_name}; "
                        f"status is {status!r}"
                    )
                normalized = {
                    "run": run,
                    "mode": mode,
                    "scenario": scenario,
                    "ue_namespace": ue_name,
                    "ue_id": ue_row.get("ue_id", ""),
                    "priority": priority,
                    "traffic_active": active,
                    "offered_mbps": offered,
                    "kpm_actual_mbps": to_float(ue_row.get("kpm_actual_mbps")),
                    "kpm_satisfaction_pct": (
                        None
                        if to_float(ue_row.get("kpm_satisfaction")) is None
                        else 100.0 * float(ue_row["kpm_satisfaction"])
                    ),
                    "delay_median_ms": to_float(ue_row.get("delay_median_ms")),
                    "delay_p95_ms": to_float(ue_row.get("delay_p95_ms")),
                    "delay_samples": int(
                        to_float(ue_row.get("delay_samples")) or 0
                    ),
                    "avg_target_cap": to_float(ue_row.get("avg_target_cap")),
                    "iperf_status": status,
                    "goodput_mbps": to_float(ue_row.get("iperf_goodput_mbps")),
                    "satisfaction_pct": (
                        None
                        if to_float(ue_row.get("iperf_satisfaction")) is None
                        else 100.0 * float(ue_row["iperf_satisfaction"])
                    ),
                    "loss_pct": to_float(ue_row.get("iperf_loss_percent")),
                    "jitter_ms": to_float(ue_row.get("iperf_jitter_ms")),
                    "packets": to_float(ue_row.get("iperf_packets")),
                    "lost_packets": to_float(ue_row.get("iperf_lost_packets")),
                }
                normalized_ues.append(normalized)
                per_ue_rows.append(normalized)

            if len(normalized_ues) != 6:
                raise ValueError(
                    f"Expected six UE rows for {scenario} in {per_ue_path}; "
                    f"found {len(normalized_ues)}"
                )

            offered_fields = {
                "high": "high_offered_mbps",
                "medium": "medium_offered_mbps",
                "best_effort": "be_offered_mbps",
            }
            for priority in PRIORITIES:
                offered_total = required_float(
                    result, offered_fields[priority], context
                )
                ue_total = sum(
                    float(row["offered_mbps"])
                    for row in normalized_ues
                    if row["priority"] == priority
                )
                if not math.isclose(offered_total, ue_total, abs_tol=0.02):
                    raise ValueError(
                        f"Priority offered-load mismatch for {scenario}/{priority}: "
                        f"summary={offered_total}, per-UE sum={ue_total}"
                    )

                active_ues = [
                    row
                    for row in normalized_ues
                    if row["priority"] == priority
                    and bool(row["traffic_active"])
                    and float(row["offered_mbps"]) > 0.0
                ]
                delay_medians = [
                    row["delay_median_ms"]
                    for row in active_ues
                    if row["delay_median_ms"] is not None
                ]
                delay_p95s = [
                    row["delay_p95_ms"]
                    for row in active_ues
                    if row["delay_p95_ms"] is not None
                ]
                priority_rows.append(
                    {
                        "run": run,
                        "mode": mode,
                        "scenario": scenario,
                        "priority": priority,
                        "offered_mbps": offered_total,
                        "active_ue_count": len(active_ues),
                        "goodput_mbps": to_float(
                            result.get(f"{priority}_iperf_goodput_mbps")
                        ),
                        "satisfaction_pct": (
                            None
                            if to_float(
                                result.get(f"{priority}_iperf_satisfaction")
                            )
                            is None
                            else 100.0
                            * float(result[f"{priority}_iperf_satisfaction"])
                        ),
                        "loss_pct": to_float(
                            result.get(f"{priority}_iperf_loss_percent")
                        ),
                        # Equal UE weighting prevents a UE with more KPM reports
                        # from dominating the priority-level delay result.
                        "delay_median_ms": mean_or_none(delay_medians),
                        "delay_p95_ms": mean_or_none(delay_p95s),
                        "delay_samples": sum(
                            int(row["delay_samples"]) for row in active_ues
                        ),
                        "delay_ue_count": len(delay_medians),
                    }
                )

                fairness_values = [
                    float(row["satisfaction_pct"]) / 100.0
                    for row in active_ues
                    if row["iperf_status"] == "ok"
                    and row["satisfaction_pct"] is not None
                ]
                fairness = jain_fairness(fairness_values)
                fairness_rows.append(
                    {
                        "run": run,
                        "mode": mode,
                        "scenario": scenario,
                        "priority": priority,
                        "active_ue_count": len(fairness_values),
                        "jain_satisfaction": fairness,
                        "mean_ue_satisfaction_pct": (
                            100.0 * statistics.mean(fairness_values)
                            if fairness_values
                            else None
                        ),
                        "min_ue_satisfaction_pct": (
                            100.0 * min(fairness_values)
                            if fairness_values
                            else None
                        ),
                    }
                )

                analyzer_fairness = to_float(
                    result.get(f"{priority}_iperf_jain_satisfaction")
                )
                if (
                    analyzer_fairness is not None
                    and fairness is not None
                    and not math.isclose(
                        analyzer_fairness, fairness, rel_tol=1e-6, abs_tol=1e-6
                    )
                ):
                    warnings.append(
                        f"{results_path}: recomputed Jain index differs for "
                        f"{scenario}/{priority}; plotter used per-UE value."
                    )

            capacity = required_float(result, "cell_capacity_mbps", context)
            capacity_values.append(capacity)
            aggregate_rows.append(
                {
                    "run": run,
                    "mode": mode,
                    "scenario": scenario,
                    "total_goodput_mbps": required_float(
                        result, "total_iperf_goodput_mbps", context
                    ),
                    "cell_utilization_pct": 100.0
                    * required_float(
                        result, "iperf_cell_capacity_utilization", context
                    ),
                    "demand_efficiency_pct": 100.0
                    * required_float(
                        result, "iperf_demand_capacity_efficiency", context
                    ),
                    "aggregate_loss_pct": to_float(
                        result.get("iperf_total_loss_percent")
                    ),
                    "total_offered_mbps": sum(
                        required_float(result, field, context)
                        for field in offered_fields.values()
                    ),
                    "cell_capacity_mbps": capacity,
                    "iperf_test_duration_seconds": required_float(
                        result, "iperf_test_duration_seconds", context
                    ),
                }
            )

    if capacity_values and max(capacity_values) - min(capacity_values) > 1e-6:
        raise ValueError(
            "Analyzer outputs use inconsistent cell-capacity references: "
            f"{sorted(set(capacity_values))}"
        )
    return priority_rows, aggregate_rows, per_ue_rows, fairness_rows, warnings


def load_control_dynamics(
    experiments: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Load full scenario-03 raw KPM rows from every controlled run."""
    dynamics: list[dict[str, object]] = []
    controls = [exp for exp in experiments if exp["mode"] == "control"]
    for experiment in controls:
        run = int(experiment["run"])
        result_rows = {
            row["scenario"]: row
            for row in read_dicts(Path(experiment["results_path"]))
        }
        marker_rows = {
            row["scenario"]: row
            for row in read_dicts(Path(experiment["marker_path"]))
        }
        if DYNAMICS_SCENARIO not in result_rows or DYNAMICS_SCENARIO not in marker_rows:
            raise ValueError(
                f"Missing {DYNAMICS_SCENARIO} for dynamics in "
                f"{experiment['experiment_dir']}"
            )
        marker = marker_rows[DYNAMICS_SCENARIO]
        result = result_rows[DYNAMICS_SCENARIO]
        start = required_float(marker, "start_ts", str(experiment["marker_path"]))
        marker_end = required_float(
            marker, "end_ts", str(experiment["marker_path"])
        )
        duration = required_float(
            result,
            "iperf_test_duration_seconds",
            str(experiment["results_path"]),
        )
        end = min(marker_end, start + duration)

        raw_path = Path(experiment["raw_path"])
        raw_rows = read_dicts(raw_path)
        require_columns(
            raw_rows,
            {
                "timestamp",
                "trigger",
                "ue_id",
                "priority",
                "ue_dl_thp",
                "congestion_score",
                "target_cap",
            },
            raw_path,
        )
        for row in raw_rows:
            if row.get("trigger") != "style4_ue":
                continue
            timestamp = to_float(row.get("timestamp"))
            throughput = to_float(row.get("ue_dl_thp"))
            score = to_float(row.get("congestion_score"))
            cap = to_float(row.get("target_cap"))
            priority = row.get("priority", "")
            ue_id = row.get("ue_id", "").strip()
            if (
                timestamp is None
                or throughput is None
                or score is None
                or cap is None
                or priority not in PRIORITIES
                or not ue_id
                or timestamp < start
                or timestamp > end
            ):
                continue
            dynamics.append(
                {
                    "run": run,
                    "second": int(math.floor(timestamp - start)),
                    "ue_id": ue_id,
                    "priority": priority,
                    "throughput_mbps": throughput / 1000.0,
                    "congestion_score": score,
                    "target_cap_pct": cap,
                }
            )
    expected_runs = sorted(int(exp["run"]) for exp in controls)
    found_runs = sorted({int(row["run"]) for row in dynamics})
    if found_runs != expected_runs:
        raise ValueError(
            f"Incomplete scenario-03 control dynamics: expected runs "
            f"{expected_runs}, found {found_runs}"
        )
    return dynamics


def configure_matplotlib() -> str:
    requested = "Times New Roman"
    try:
        resolved = font_manager.findfont(
            font_manager.FontProperties(family=requested),
            fallback_to_default=False,
        )
    except ValueError as exc:
        raise RuntimeError(
            "Times New Roman is required for the thesis plots but Matplotlib "
            "cannot find it. Install the font, run 'fc-cache -f', remove the "
            "Matplotlib font cache, and rerun this script."
        ) from exc
    if requested not in {font.name for font in font_manager.fontManager.ttflist}:
        raise RuntimeError(
            "Times New Roman is not registered in Matplotlib's font list; "
            "refusing to use a silent substitute."
        )

    plt.style.use("default")
    plt.rcParams.update(
        {
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.titleweight": "bold",
            "font.family": requested,
            "font.size": 10,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
        }
    )
    return f"{requested} ({resolved})"


def run_jitter(run: int, runs: Sequence[int]) -> float:
    if len(runs) <= 1:
        return 0.0
    index = list(runs).index(run)
    return -0.055 + 0.11 * index / (len(runs) - 1)


def select_values(
    rows: list[dict[str, object]],
    scenario: str,
    mode: str,
    field: str,
    *,
    priority: Optional[str] = None,
    ue_namespace: Optional[str] = None,
    positive_only: bool = False,
) -> dict[int, float]:
    selected: dict[int, float] = {}
    for row in rows:
        if row["scenario"] != scenario or row["mode"] != mode:
            continue
        if priority is not None and row.get("priority") != priority:
            continue
        if ue_namespace is not None and row.get("ue_namespace") != ue_namespace:
            continue
        value = row.get(field)
        if value is None:
            continue
        numeric = float(value)
        if positive_only and numeric <= 0.0:
            continue
        selected[int(row["run"])] = numeric
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
    runs: Sequence[int],
) -> None:
    for run in sorted(set(baseline) & set(control)):
        jitter = run_jitter(run, runs)
        ax.plot(
            [x + MODE_OFFSETS["baseline"] + jitter,
             x + MODE_OFFSETS["control"] + jitter],
            [baseline[run], control[run]],
            color="#9ca3af",
            linewidth=0.75,
            alpha=0.42,
            zorder=1,
        )

    for mode, values in (("baseline", baseline), ("control", control)):
        if not values:
            continue
        ordered = sorted(values.items())
        xs = [
            x + MODE_OFFSETS[mode] + run_jitter(run, runs)
            for run, _ in ordered
        ]
        ys = [value for _, value in ordered]
        ax.scatter(
            xs,
            ys,
            s=20,
            color=MODE_COLORS[mode],
            alpha=0.50,
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
    positions = list(range(len(SCENARIO_ORDER)))
    ax.set_xlim(-0.55, len(SCENARIO_ORDER) - 0.45)
    ax.set_xticks(positions)
    if show_xlabels:
        ax.set_xticklabels(
            [SCENARIO_LABELS[scenario] for scenario in SCENARIO_ORDER],
            fontsize=8.2,
        )
    else:
        ax.tick_params(axis="x", labelbottom=False)
    ax.grid(axis="y", alpha=0.24)


def save_figure(fig: plt.Figure, output_dir: Path, stem: str) -> None:
    fig.savefig(output_dir / f"{stem}.png", dpi=220, bbox_inches="tight")
    plt.close(fig)


def finite_values(rows: list[dict[str, object]], field: str) -> list[float]:
    return [
        float(row[field])
        for row in rows
        if row.get(field) is not None and math.isfinite(float(row[field]))
    ]


def linear_upper(values: Iterable[float], floor: float = 1.0) -> float:
    numeric = [float(value) for value in values if math.isfinite(float(value))]
    maximum = max(numeric, default=floor)
    padded = max(floor, maximum * 1.12)
    magnitude = 10 ** math.floor(math.log10(padded)) if padded > 0 else 1.0
    step = magnitude / 2.0
    return math.ceil(padded / step) * step


def plot_priority_metric(
    rows: list[dict[str, object]],
    output_dir: Path,
    runs: Sequence[int],
    field: str,
    ylabel: str,
    title: str,
    stem: str,
    *,
    percent_axis: bool = False,
    log_axis: bool = False,
    include_offered: bool = False,
) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(17.0, 10.8), sharex=True)
    all_metric_values = finite_values(rows, field)
    offered_values = finite_values(rows, "offered_mbps") if include_offered else []
    upper = linear_upper(all_metric_values + offered_values, floor=1.0)

    for panel, priority in enumerate(PRIORITIES):
        ax = axes[panel]
        for x, scenario in enumerate(SCENARIO_ORDER):
            baseline = select_values(
                rows,
                scenario,
                "baseline",
                field,
                priority=priority,
                positive_only=log_axis,
            )
            control = select_values(
                rows,
                scenario,
                "control",
                field,
                priority=priority,
                positive_only=log_axis,
            )
            draw_paired_group(ax, x, baseline, control, runs)

            if include_offered:
                offered = next(
                    (
                        float(row["offered_mbps"])
                        for row in rows
                        if row["scenario"] == scenario
                        and row["priority"] == priority
                        and float(row["offered_mbps"]) > 0.0
                    ),
                    None,
                )
                if offered is not None:
                    ax.scatter(
                        x,
                        offered,
                        marker="x",
                        s=43,
                        linewidths=1.5,
                        color="#111827",
                        zorder=5,
                    )

        ax.set_ylabel(f"{PRIORITY_LABELS[priority]}\n{ylabel}")
        if log_axis:
            positive = [value for value in all_metric_values if value > 0.0]
            log_upper = max(10.0, linear_upper(positive, floor=10.0))
            log_upper = min(10000.0, max(100.0, log_upper))
            ax.set_yscale("log")
            ax.set_ylim(0.5, log_upper)
            ticks = [1, 10, 100, 1000, 6000, 10000]
            ax.yaxis.set_major_locator(
                FixedLocator([tick for tick in ticks if tick <= log_upper])
            )
            ax.yaxis.set_major_formatter(
                FuncFormatter(lambda value, _: f"{value:g}")
            )
        elif percent_axis:
            ax.set_ylim(0.0, max(105.0, upper))
            ax.yaxis.set_major_formatter(PercentFormatter(100.0))
        else:
            ax.set_ylim(0.0, upper)
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
        f"Thin lines join {len(runs)} matched baseline/control runs. Diamonds "
        "show mean ± 1 SD; small points are individual runs. Loads are "
        "aggregate BE/M/H Mbit/s."
    )
    if log_axis:
        note += (
            " Each run-level value is the equal-weight mean of active UE-level "
            "delay summaries; the axis is logarithmic."
        )
    fig.text(0.5, 0.016, note, ha="center", fontsize=9)
    fig.tight_layout(rect=(0.025, 0.06, 0.995, 0.915))
    save_figure(fig, output_dir, stem)


def plot_cell_performance(
    rows: list[dict[str, object]],
    output_dir: Path,
    runs: Sequence[int],
) -> None:
    panels = (
        ("total_goodput_mbps", "Aggregate goodput (Mbit/s)"),
        ("cell_utilization_pct", "Cell-capacity utilization"),
        ("demand_efficiency_pct", "Demand-capacity efficiency"),
    )
    fig, axes = plt.subplots(3, 1, figsize=(17.0, 10.8), sharex=True)
    capacity = float(rows[0]["cell_capacity_mbps"])

    for panel, (field, ylabel) in enumerate(panels):
        ax = axes[panel]
        for x, scenario in enumerate(SCENARIO_ORDER):
            baseline = select_values(rows, scenario, "baseline", field)
            control = select_values(rows, scenario, "control", field)
            draw_paired_group(ax, x, baseline, control, runs)
        values = finite_values(rows, field)
        if field == "total_goodput_mbps":
            ax.set_ylim(0.0, linear_upper(values + [capacity], floor=capacity))
            ax.axhline(
                capacity,
                linestyle="--",
                linewidth=1.2,
                color="#111827",
                alpha=0.75,
            )
        else:
            ax.set_ylim(0.0, max(105.0, linear_upper(values, floor=100.0)))
            ax.yaxis.set_major_formatter(PercentFormatter(100.0))
        ax.set_ylabel(ylabel)
        style_scenario_axis(ax, panel == len(panels) - 1)

    handles = mode_handles()
    handles.append(
        Line2D(
            [0],
            [0],
            linestyle="--",
            color="#111827",
            label=f"Capacity reference ({capacity:g} Mbit/s)",
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
        0.016,
        f"Receiver-side iPerf results across {len(runs)} matched run pairs. "
        "Diamonds show mean ± 1 SD; points show individual runs.",
        ha="center",
        fontsize=9,
    )
    fig.tight_layout(rect=(0.025, 0.06, 0.995, 0.915))
    save_figure(fig, output_dir, "06_aggregate_cell_performance")


def plot_satisfaction_delta_heatmap(
    rows: list[dict[str, object]], output_dir: Path, runs: Sequence[int]
) -> None:
    matrix: list[list[float]] = []
    finite: list[float] = []
    for scenario in SCENARIO_ORDER:
        scenario_values: list[float] = []
        for priority in PRIORITIES:
            baseline = select_values(
                rows, scenario, "baseline", "satisfaction_pct", priority=priority
            )
            control = select_values(
                rows, scenario, "control", "satisfaction_pct", priority=priority
            )
            paired = sorted(set(baseline) & set(control))
            if paired:
                delta = statistics.mean(
                    control[run] - baseline[run] for run in paired
                )
                scenario_values.append(delta)
                finite.append(delta)
            else:
                scenario_values.append(math.nan)
        matrix.append(scenario_values)

    limit = max(5.0, math.ceil(max((abs(value) for value in finite), default=5.0)))
    rendered = [
        [0.0 if math.isnan(value) else value for value in scenario]
        for scenario in matrix
    ]
    fig, ax = plt.subplots(figsize=(8.5, 7.7))
    image = ax.imshow(
        rendered,
        cmap=plt.get_cmap("RdBu"),
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
                label, color = "N/A", "#6b7280"
            else:
                label = f"{value:+.1f}"
                color = "white" if abs(value) > 0.48 * limit else "#111827"
            ax.text(x, y, label, ha="center", va="center", color=color, fontsize=10)

    ax.set_xticks(range(len(PRIORITIES)))
    ax.set_xticklabels([PRIORITY_LABELS[p] for p in PRIORITIES])
    ax.set_yticks(range(len(SCENARIO_ORDER)))
    ax.set_yticklabels(
        [SCENARIO_LABELS[s].replace("\n", " ") for s in SCENARIO_ORDER]
    )
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
        0.016,
        f"Mean paired percentage-point change across {len(runs)} runs. "
        "Positive values indicate higher satisfaction with control enabled.",
        ha="center",
        fontsize=9,
    )
    fig.tight_layout(rect=(0.02, 0.05, 0.98, 0.98))
    save_figure(fig, output_dir, "07_satisfaction_change_heatmap")


def draw_bar_with_points(
    ax: plt.Axes,
    center: float,
    values: dict[int, float],
    color: str,
    width: float,
    runs: Sequence[int],
    *,
    annotate: bool = False,
) -> None:
    if not values:
        return
    ordered = sorted(values.items())
    numeric = [value for _, value in ordered]
    mean, deviation = mean_sd(numeric)
    ax.bar(
        center,
        mean,
        width=width,
        color=color,
        alpha=0.86,
        yerr=deviation,
        capsize=4,
        error_kw={"elinewidth": 1.2, "ecolor": "#111827"},
        zorder=2,
    )
    ax.scatter(
        [center + 0.65 * run_jitter(run, runs) for run, _ in ordered],
        numeric,
        s=20,
        color=color,
        edgecolors="white",
        linewidths=0.45,
        zorder=4,
    )
    if annotate:
        ax.annotate(
            f"{mean:.2f}",
            (center, mean + deviation),
            xytext=(0, 6),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8.5,
        )


def plot_representative_aggregate_bars(
    rows: list[dict[str, object]],
    output_dir: Path,
    runs: Sequence[int],
) -> None:
    fig, ax = plt.subplots(figsize=(16.2, 6.4))
    width = 0.34
    for mode_index, mode in enumerate(MODES):
        offset = (-0.19, 0.19)[mode_index]
        for x, scenario in enumerate(REPRESENTATIVE_SCENARIOS):
            values = select_values(rows, scenario, mode, "total_goodput_mbps")
            draw_bar_with_points(
                ax,
                x + offset,
                values,
                MODE_COLORS[mode],
                width,
                runs,
                annotate=True,
            )

    capacity = float(rows[0]["cell_capacity_mbps"])
    all_values = finite_values(rows, "total_goodput_mbps")
    ax.axhline(capacity, color="#111827", linestyle="--", linewidth=1.3)
    ax.set_ylim(0.0, linear_upper(all_values + [capacity], floor=capacity))
    ax.set_ylabel("Aggregate receiver goodput (Mbit/s)")
    ax.set_xticks(
        range(len(REPRESENTATIVE_SCENARIOS)),
        [SCENARIO_LABELS[s] for s in REPRESENTATIVE_SCENARIOS],
        fontsize=8.8,
    )
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
    ax.set_title(
        "Aggregate downlink goodput in representative scenarios",
        fontsize=16,
        fontweight="bold",
        pad=64,
    )
    fig.text(
        0.5,
        0.016,
        f"Bars show mean ± 1 SD across {len(runs)} runs; points show individual runs.",
        ha="center",
        fontsize=9,
    )
    fig.tight_layout(rect=(0.02, 0.06, 0.995, 0.98))
    save_figure(fig, output_dir, "08_representative_aggregate_goodput")


def collapse_across_runs(
    values: dict[tuple[int, int], float]
) -> tuple[list[int], list[float], list[float]]:
    seconds = sorted({second for _, second in values})
    means: list[float] = []
    deviations: list[float] = []
    for second in seconds:
        second_values = [
            value
            for (run, row_second), value in values.items()
            if row_second == second
        ]
        mean, deviation = mean_sd(second_values)
        means.append(mean)
        deviations.append(deviation)
    return seconds, means, deviations


def dynamics_priority_series(
    rows: list[dict[str, object]], field: str, priority: str, reduction: str
) -> tuple[list[int], list[float], list[float]]:
    per_ue: dict[tuple[int, int, str], list[float]] = defaultdict(list)
    for row in rows:
        if row["priority"] != priority:
            continue
        per_ue[(int(row["run"]), int(row["second"]), str(row["ue_id"]))].append(
            float(row[field])
        )
    ue_means = {key: statistics.mean(values) for key, values in per_ue.items()}
    grouped: dict[tuple[int, int], list[float]] = defaultdict(list)
    for (run, second, _), value in ue_means.items():
        grouped[(run, second)].append(value)
    if reduction == "sum":
        collapsed = {key: sum(values) for key, values in grouped.items()}
    elif reduction == "mean":
        collapsed = {key: statistics.mean(values) for key, values in grouped.items()}
    else:
        raise ValueError(f"Unknown dynamics reduction: {reduction}")
    return collapse_across_runs(collapsed)


def dynamics_congestion_series(
    rows: list[dict[str, object]],
) -> tuple[list[int], list[float], list[float]]:
    # The congestion score is global and repeated on each UE decision row.
    # Selecting one stable representative UE per run prevents sixfold weighting.
    representative: dict[int, str] = {}
    for run in sorted({int(row["run"]) for row in rows}):
        ids = sorted(
            {str(row["ue_id"]) for row in rows if int(row["run"]) == run},
            key=natural_ue_key,
        )
        if ids:
            representative[run] = ids[0]
    per_second: dict[tuple[int, int], list[float]] = defaultdict(list)
    for row in rows:
        run = int(row["run"])
        if str(row["ue_id"]) != representative.get(run):
            continue
        per_second[(run, int(row["second"]))].append(
            float(row["congestion_score"])
        )
    collapsed = {
        key: statistics.mean(values) for key, values in per_second.items()
    }
    return collapse_across_runs(collapsed)


def plot_control_dynamics(
    rows: list[dict[str, object]], output_dir: Path, runs: Sequence[int]
) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(13.2, 9.4), sharex=True)
    throughput_ax, score_ax, cap_ax = axes
    max_throughput = 0.0

    for priority in PRIORITIES:
        seconds, means, deviations = dynamics_priority_series(
            rows, "throughput_mbps", priority, "sum"
        )
        lower = [max(0.0, m - d) for m, d in zip(means, deviations)]
        upper = [m + d for m, d in zip(means, deviations)]
        max_throughput = max(max_throughput, max(upper, default=0.0))
        color = PRIORITY_COLORS[priority]
        throughput_ax.plot(
            seconds,
            means,
            color=color,
            linewidth=2.0,
            label=PRIORITY_LABELS[priority],
        )
        throughput_ax.fill_between(
            seconds, lower, upper, color=color, alpha=0.14, linewidth=0
        )

    seconds, score_means, score_deviations = dynamics_congestion_series(rows)
    score_ax.plot(
        seconds,
        score_means,
        color="#dc2626",
        linewidth=2.2,
        label="Congestion score",
    )
    score_ax.fill_between(
        seconds,
        [max(0.0, m - d) for m, d in zip(score_means, score_deviations)],
        [min(1.0, m + d) for m, d in zip(score_means, score_deviations)],
        color="#dc2626",
        alpha=0.15,
        linewidth=0,
    )

    for priority in PRIORITIES:
        seconds, means, deviations = dynamics_priority_series(
            rows, "target_cap_pct", priority, "mean"
        )
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
            [max(0.0, m - d) for m, d in zip(means, deviations)],
            [min(100.0, m + d) for m, d in zip(means, deviations)],
            step="mid",
            color=color,
            alpha=0.14,
            linewidth=0,
        )

    duration = max(int(row["second"]) for row in rows)
    for ax in axes:
        ax.set_xlim(0.0, max(1.0, float(duration)))
        ax.grid(alpha=0.24)
    throughput_ax.set_ylabel("Aggregate priority\nKPM DL bitrate (Mbit/s)")
    throughput_ax.set_ylim(
        0.0, linear_upper([max_throughput], floor=max(1.0, max_throughput))
    )
    throughput_ax.legend(loc="lower right", ncol=3, frameon=True)
    score_ax.set_ylabel("Congestion score")
    score_ax.set_ylim(0.0, 1.02)
    score_ax.legend(loc="lower right", frameon=True)
    cap_ax.set_ylabel("Mean UE target\nPRB cap (%)")
    cap_ax.set_ylim(0.0, 105.0)
    cap_ax.set_xlabel("Time since traffic start (s)")
    cap_ax.legend(loc="lower right", ncol=3, frameon=True)

    fig.suptitle(
        "Heuristic control dynamics — scenario 03 (aggregate 10/10/10 Mbit/s)",
        fontsize=16,
        fontweight="bold",
        y=0.985,
    )
    fig.text(
        0.5,
        0.014,
        f"Lines are means across {len(runs)} controlled runs; shading is ±1 SD. "
        "UE KPM bitrate is summed within each priority, caps are averaged within "
        "priority, and the global score is deduplicated using one representative UE.",
        ha="center",
        fontsize=9,
    )
    fig.tight_layout(rect=(0.035, 0.06, 0.995, 0.95))
    save_figure(fig, output_dir, "09_control_dynamics_scenario_03")


def plot_fairness(
    rows: list[dict[str, object]],
    output_dir: Path,
    runs: Sequence[int],
) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(17.0, 10.8), sharex=True)
    for panel, priority in enumerate(PRIORITIES):
        ax = axes[panel]
        for x, scenario in enumerate(SCENARIO_ORDER):
            baseline = select_values(
                rows,
                scenario,
                "baseline",
                "jain_satisfaction",
                priority=priority,
            )
            control = select_values(
                rows,
                scenario,
                "control",
                "jain_satisfaction",
                priority=priority,
            )
            draw_paired_group(ax, x, baseline, control, runs)
        ax.axhline(1.0, color="#111827", linewidth=0.9, linestyle="--", alpha=0.6)
        ax.set_ylabel(f"{PRIORITY_LABELS[priority]}\nJain index")
        ax.set_ylim(0.0, 1.04)
        style_scenario_axis(ax, panel == len(PRIORITIES) - 1)

    fig.suptitle(
        "Within-priority fairness of per-UE demand satisfaction",
        fontsize=16,
        fontweight="bold",
        y=0.985,
    )
    fig.legend(
        handles=mode_handles(),
        loc="upper center",
        bbox_to_anchor=(0.5, 0.955),
        ncol=2,
        frameon=True,
    )
    fig.text(
        0.5,
        0.014,
        "Jain index is computed from receiver-side per-UE satisfaction within "
        "each priority (1 means equal satisfaction). It is omitted when fewer "
        "than two active UEs have valid results.",
        ha="center",
        fontsize=9,
    )
    fig.tight_layout(rect=(0.025, 0.06, 0.995, 0.915))
    save_figure(fig, output_dir, "10_within_priority_satisfaction_fairness")


def active_ue_names(
    rows: list[dict[str, object]], scenario: str
) -> list[str]:
    return sorted(
        {
            str(row["ue_namespace"])
            for row in rows
            if row["scenario"] == scenario
            and bool(row["traffic_active"])
            and float(row["offered_mbps"]) > 0.0
        },
        key=natural_ue_key,
    )


def ue_priority(
    rows: list[dict[str, object]], scenario: str, ue_namespace: str
) -> str:
    priorities = {
        str(row["priority"])
        for row in rows
        if row["scenario"] == scenario and row["ue_namespace"] == ue_namespace
    }
    if len(priorities) != 1:
        raise ValueError(
            f"Expected one priority for {scenario}/{ue_namespace}; found {priorities}"
        )
    return next(iter(priorities))


def plot_per_ue_comparison(
    rows: list[dict[str, object]],
    output_dir: Path,
    runs: Sequence[int],
    *,
    field: str,
    ylabel: str,
    title: str,
    stem: str,
    percent_axis: bool = False,
    offered_markers: bool = False,
) -> None:
    fig, axes = plt.subplots(1, len(PER_UE_SCENARIOS), figsize=(16.0, 6.0))
    axes_list = list(axes) if isinstance(axes, Iterable) else [axes]
    all_values = finite_values(rows, field)
    offered_values = finite_values(rows, "offered_mbps") if offered_markers else []
    upper = (
        max(105.0, linear_upper(all_values, floor=100.0))
        if percent_axis
        else linear_upper(all_values + offered_values, floor=1.0)
    )

    for ax, scenario in zip(axes_list, PER_UE_SCENARIOS):
        ue_names = active_ue_names(rows, scenario)
        for mode_index, mode in enumerate(MODES):
            offset = (-0.19, 0.19)[mode_index]
            for x, ue_name in enumerate(ue_names):
                values = select_values(
                    rows,
                    scenario,
                    mode,
                    field,
                    ue_namespace=ue_name,
                )
                draw_bar_with_points(
                    ax,
                    x + offset,
                    values,
                    MODE_COLORS[mode],
                    0.34,
                    runs,
                )
        if offered_markers:
            for x, ue_name in enumerate(ue_names):
                offered = next(
                    float(row["offered_mbps"])
                    for row in rows
                    if row["scenario"] == scenario
                    and row["ue_namespace"] == ue_name
                    and float(row["offered_mbps"]) > 0.0
                )
                ax.scatter(
                    x,
                    offered,
                    marker="x",
                    s=48,
                    linewidths=1.6,
                    color="#111827",
                    zorder=6,
                )
        tick_labels = [
            f"{name}\n{SHORT_PRIORITY_LABELS[ue_priority(rows, scenario, name)]}"
            for name in ue_names
        ]
        ax.set_xticks(range(len(ue_names)), tick_labels)
        ax.set_ylim(0.0, upper)
        ax.set_ylabel(ylabel)
        if percent_axis:
            ax.yaxis.set_major_formatter(PercentFormatter(100.0))
        ax.grid(axis="y", alpha=0.24)
        ax.set_title(
            "Scenario 03: homogeneous demand within priorities"
            if scenario == "03_moderate_overload_equal"
            else "Scenario 13: heterogeneous demand within priorities",
            fontsize=12,
        )

    handles = mode_handles(offered_markers)
    fig.suptitle(title, fontsize=16, fontweight="bold", y=0.985)
    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.935),
        ncol=len(handles),
        frameon=True,
    )
    fig.text(
        0.5,
        0.016,
        f"Bars show mean ± 1 SD across {len(runs)} runs; points show individual "
        "runs. Priority membership is printed below each UE namespace.",
        ha="center",
        fontsize=9,
    )
    fig.tight_layout(rect=(0.025, 0.07, 0.995, 0.88))
    save_figure(fig, output_dir, stem)


def write_csv(path: Path, fields: Sequence[str], rows: Iterable[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def grouped_mean_sd_rows(
    rows: list[dict[str, object]],
    group_fields: Sequence[str],
    metric_fields: Sequence[str],
) -> list[dict[str, object]]:
    groups: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row.get(field) for field in group_fields)].append(row)
    output: list[dict[str, object]] = []
    for key, group in groups.items():
        result = {field: value for field, value in zip(group_fields, key)}
        result["runs"] = len({int(row["run"]) for row in group})
        for metric in metric_fields:
            mean, deviation = mean_sd(row.get(metric) for row in group)
            result[f"{metric}_mean"] = mean
            result[f"{metric}_sd"] = deviation
        output.append(result)
    scenario_index = {scenario: i for i, scenario in enumerate(SCENARIO_ORDER)}
    mode_index = {mode: i for i, mode in enumerate(MODES)}
    priority_index = {priority: i for i, priority in enumerate(PRIORITIES)}
    output.sort(
        key=lambda row: (
            scenario_index.get(str(row.get("scenario")), 10**9),
            mode_index.get(str(row.get("mode")), 10**9),
            priority_index.get(str(row.get("priority")), 10**9),
            natural_ue_key(row.get("ue_namespace", "")),
        )
    )
    return output


def write_output_csvs(
    output_dir: Path,
    priority_rows: list[dict[str, object]],
    aggregate_rows: list[dict[str, object]],
    per_ue_rows: list[dict[str, object]],
    fairness_rows: list[dict[str, object]],
) -> None:
    aggregate_fields = (
        "run",
        "mode",
        "scenario",
        "total_offered_mbps",
        "total_goodput_mbps",
        "cell_utilization_pct",
        "demand_efficiency_pct",
        "aggregate_loss_pct",
        "cell_capacity_mbps",
        "iperf_test_duration_seconds",
    )
    priority_fields = (
        "run",
        "mode",
        "scenario",
        "priority",
        "offered_mbps",
        "active_ue_count",
        "goodput_mbps",
        "satisfaction_pct",
        "loss_pct",
        "delay_median_ms",
        "delay_p95_ms",
        "delay_samples",
        "delay_ue_count",
    )
    per_ue_fields = (
        "run",
        "mode",
        "scenario",
        "ue_namespace",
        "ue_id",
        "priority",
        "traffic_active",
        "offered_mbps",
        "goodput_mbps",
        "satisfaction_pct",
        "loss_pct",
        "jitter_ms",
        "packets",
        "lost_packets",
        "kpm_actual_mbps",
        "kpm_satisfaction_pct",
        "delay_median_ms",
        "delay_p95_ms",
        "delay_samples",
        "avg_target_cap",
        "iperf_status",
    )
    fairness_fields = (
        "run",
        "mode",
        "scenario",
        "priority",
        "active_ue_count",
        "jain_satisfaction",
        "mean_ue_satisfaction_pct",
        "min_ue_satisfaction_pct",
    )
    write_csv(
        output_dir / "final_6ue_scenario_metrics_per_run.csv",
        aggregate_fields,
        aggregate_rows,
    )
    write_csv(
        output_dir / "final_6ue_priority_metrics_per_run.csv",
        priority_fields,
        priority_rows,
    )
    write_csv(
        output_dir / "final_6ue_per_ue_metrics_per_run.csv",
        per_ue_fields,
        per_ue_rows,
    )
    write_csv(
        output_dir / "final_6ue_fairness_per_run.csv",
        fairness_fields,
        fairness_rows,
    )

    summary_specs = (
        (
            "final_6ue_scenario_metrics_mean_sd.csv",
            aggregate_rows,
            ("mode", "scenario"),
            (
                "total_goodput_mbps",
                "cell_utilization_pct",
                "demand_efficiency_pct",
                "aggregate_loss_pct",
            ),
        ),
        (
            "final_6ue_priority_metrics_mean_sd.csv",
            priority_rows,
            ("mode", "scenario", "priority"),
            (
                "offered_mbps",
                "active_ue_count",
                "goodput_mbps",
                "satisfaction_pct",
                "loss_pct",
                "delay_median_ms",
                "delay_p95_ms",
            ),
        ),
        (
            "final_6ue_per_ue_metrics_mean_sd.csv",
            per_ue_rows,
            ("mode", "scenario", "ue_namespace", "priority"),
            (
                "offered_mbps",
                "goodput_mbps",
                "satisfaction_pct",
                "loss_pct",
                "delay_median_ms",
                "delay_p95_ms",
                "avg_target_cap",
            ),
        ),
        (
            "final_6ue_fairness_mean_sd.csv",
            fairness_rows,
            ("mode", "scenario", "priority"),
            (
                "active_ue_count",
                "jain_satisfaction",
                "mean_ue_satisfaction_pct",
                "min_ue_satisfaction_pct",
            ),
        ),
    )
    for filename, source, group_fields, metric_fields in summary_specs:
        summary = grouped_mean_sd_rows(source, group_fields, metric_fields)
        fields = list(group_fields) + ["runs"]
        for metric in metric_fields:
            fields.extend([f"{metric}_mean", f"{metric}_sd"])
        write_csv(output_dir / filename, fields, summary)


def write_notes(
    output_dir: Path,
    input_root: Path,
    runs: Sequence[int],
    font_description: str,
    warnings: list[str],
) -> None:
    lines = [
        "Six-UE baseline-versus-control plot methodology",
        "================================================",
        f"Input root: {input_root}",
        f"Matched run pairs: {len(runs)} ({', '.join(map(str, runs))})",
        "Primary scenarios: 01-13",
        "Scenario loads shown as aggregate best-effort/medium/high Mbit/s",
        "Goodput, satisfaction, loss, and fairness source: receiver-side iPerf",
        "Priority satisfaction: aggregate priority goodput / aggregate priority demand",
        "Per-UE satisfaction: UE goodput / UE demand, bounded at 1",
        "Within-priority fairness: Jain index over active UEs' iPerf satisfaction",
        "Fairness is undefined and omitted when fewer than two valid active UEs exist",
        "Priority delay: equal-weight mean of active UE-level analyzer summaries",
        "Dynamics throughput: per-UE KPM values summed within each priority/second",
        "Dynamics caps: per-UE target caps averaged within each priority/second",
        "Dynamics congestion: global score deduplicated using one representative UE",
        "Error bars and shaded bands: sample standard deviation across runs",
        f"Plot font: {font_description}",
        "Image format: PNG only",
        "",
        "Warnings:",
    ]
    lines.extend(f"- {warning}" for warning in warnings)
    if not warnings:
        lines.append("- None")
    (output_dir / "README_plot_method.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main() -> None:
    args = parse_args()
    font_description = configure_matplotlib()
    experiments, runs = discover_experiments(args.input_root)
    (
        priority_rows,
        aggregate_rows,
        per_ue_rows,
        fairness_rows,
        warnings,
    ) = load_data(experiments)
    dynamics_rows = load_control_dynamics(experiments)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    plot_priority_metric(
        priority_rows,
        args.output_dir,
        runs,
        "goodput_mbps",
        "Goodput (Mbit/s)",
        "Per-priority aggregate downlink goodput",
        "01_priority_aggregate_goodput",
        include_offered=True,
    )
    plot_priority_metric(
        priority_rows,
        args.output_dir,
        runs,
        "satisfaction_pct",
        "Satisfaction",
        "Per-priority aggregate demand satisfaction",
        "02_priority_aggregate_satisfaction",
        percent_axis=True,
    )
    plot_priority_metric(
        priority_rows,
        args.output_dir,
        runs,
        "loss_pct",
        "Packet loss",
        "Per-priority downlink packet loss",
        "03_priority_packet_loss",
        percent_axis=True,
    )
    plot_priority_metric(
        priority_rows,
        args.output_dir,
        runs,
        "delay_median_ms",
        "Mean UE median RLC delay (ms)",
        "Per-priority RLC downlink delay: mean of UE medians",
        "04_rlc_delay_median",
        log_axis=True,
    )
    plot_priority_metric(
        priority_rows,
        args.output_dir,
        runs,
        "delay_p95_ms",
        "Mean UE p95 RLC delay (ms)",
        "Per-priority RLC downlink delay: mean of UE 95th percentiles",
        "05_rlc_delay_p95",
        log_axis=True,
    )
    plot_cell_performance(aggregate_rows, args.output_dir, runs)
    plot_satisfaction_delta_heatmap(priority_rows, args.output_dir, runs)
    plot_representative_aggregate_bars(aggregate_rows, args.output_dir, runs)
    plot_control_dynamics(dynamics_rows, args.output_dir, runs)
    plot_fairness(fairness_rows, args.output_dir, runs)
    plot_per_ue_comparison(
        per_ue_rows,
        args.output_dir,
        runs,
        field="satisfaction_pct",
        ylabel="Demand satisfaction",
        title=(
            "Per-UE demand satisfaction under homogeneous and heterogeneous "
            "within-priority demand"
        ),
        stem="11_per_ue_satisfaction_scenarios_03_13",
        percent_axis=True,
    )
    plot_per_ue_comparison(
        per_ue_rows,
        args.output_dir,
        runs,
        field="goodput_mbps",
        ylabel="Receiver goodput (Mbit/s)",
        title=(
            "Per-UE goodput under homogeneous and heterogeneous "
            "within-priority demand"
        ),
        stem="12_per_ue_goodput_scenarios_03_13",
        offered_markers=True,
    )
    write_output_csvs(
        args.output_dir,
        priority_rows,
        aggregate_rows,
        per_ue_rows,
        fairness_rows,
    )
    write_notes(
        args.output_dir,
        args.input_root,
        runs,
        font_description,
        warnings,
    )

    print(
        f"Generated six-UE comparison plots from {len(runs)} matched run pairs "
        f"in {args.output_dir}"
    )
    if warnings:
        print(f"Recorded {len(warnings)} warning(s) in README_plot_method.txt")


if __name__ == "__main__":
    main()
