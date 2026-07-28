#!/usr/bin/env python3
"""
Analyze priority-PRB xApp CSV logs using test-matrix scenario markers.

The analyzer deliberately keeps network outcomes separate from controller
diagnostics. Its primary outcomes are:

  * per-priority demand satisfaction;
  * priority-weighted demand satisfaction (high:medium:best-effort = 3:2:1);
  * total throughput and cell-capacity utilization.

Single-UE calibration scenarios are reported but excluded from aggregate
weight-set comparisons by default.
"""

from __future__ import annotations

import argparse
import math
import re
from pathlib import Path
from typing import Dict, Optional

import pandas as pd


PRIORITY_ORDER = ["high", "medium", "best_effort"]
SATISFACTION_WEIGHTS = {
    "high": 3.0,
    "medium": 2.0,
    "best_effort": 1.0,
}
PROTECTED_WEIGHTS = {
    "high": 3.0,
    "medium": 2.0,
}


def normalize_priority(value: object) -> Optional[str]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None

    text = str(value).strip().lower().replace("-", "_")
    aliases = {
        "hi": "high",
        "h": "high",
        "high": "high",
        "me": "medium",
        "med": "medium",
        "medium": "medium",
        "be": "best_effort",
        "best_effort": "best_effort",
        "best effort": "best_effort",
        "besteffort": "best_effort",
    }
    return aliases.get(text, text if text in PRIORITY_ORDER else None)


def parse_rate_to_mbps(value: object) -> float:
    """Parse iperf-style rates such as 30M, 500K, 0 or none."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 0.0

    text = str(value).strip().lower()
    if text in {"", "0", "none", "off", "false", "nan"}:
        return 0.0

    match = re.fullmatch(
        r"([0-9]*\.?[0-9]+)\s*([kmgb]?)(?:bit/s|bits/s|bps|b/s)?",
        text,
    )
    if not match:
        raise ValueError(f"Could not parse bitrate value: {value!r}")

    number = float(match.group(1))
    unit = match.group(2)
    if unit == "g":
        return number * 1000.0
    if unit in {"m", ""}:
        return number
    if unit == "k":
        return number / 1000.0
    if unit == "b":
        return number / 1_000_000.0
    return number


def parse_bool_series(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().isin(
        {"true", "1", "yes", "y"}
    )


def throughput_to_mbps(series: pd.Series, unit: str) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    unit = unit.lower()
    if unit == "mbps":
        return values
    if unit == "kbps":
        return values / 1000.0
    if unit == "bps":
        return values / 1_000_000.0
    raise ValueError("--throughput_unit must be one of: kbps, mbps, bps")


def offered_from_marker(
    row: pd.Series,
    args: argparse.Namespace,
) -> Dict[str, float]:
    """Return the offered bitrate for each priority in Mbit/s."""
    columns = set(row.index)
    priority_column_sets = {
        "best_effort": [
            "be_offered",
            "best_effort_offered",
            "best_effort_bw",
            "be_bw",
        ],
        "medium": ["medium_offered", "me_offered", "medium_bw", "me_bw"],
        "high": ["high_offered", "hi_offered", "high_bw", "hi_bw"],
    }

    offered = {"best_effort": 0.0, "medium": 0.0, "high": 0.0}
    found_priority_columns = False

    for priority, possible_names in priority_column_sets.items():
        for name in possible_names:
            if name in columns:
                offered[priority] = parse_rate_to_mbps(row[name])
                found_priority_columns = True
                break

    if found_priority_columns:
        return offered

    cli_priorities = {
        "ue1": normalize_priority(args.ue1_priority),
        "ue2": normalize_priority(args.ue2_priority),
        "ue3": normalize_priority(args.ue3_priority),
    }

    for ue in ["ue1", "ue2", "ue3"]:
        bw_col = f"{ue}_bw"
        if bw_col not in columns:
            continue

        priority_col = f"{ue}_priority"
        priority = (
            normalize_priority(row[priority_col])
            if priority_col in columns
            else cli_priorities[ue]
        )
        if priority is None:
            raise ValueError(
                f"Marker file does not contain {priority_col}, and "
                f"--{ue}_priority was not provided."
            )
        offered[priority] += parse_rate_to_mbps(row[bw_col])

    return offered


def satisfaction(
    actual_mbps: float,
    offered_mbps: float,
) -> Optional[float]:
    if offered_mbps <= 0:
        return None
    return max(0.0, min(actual_mbps / offered_mbps, 1.0))


def mean_or_none(series: pd.Series) -> Optional[float]:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return None
    return float(numeric.mean())


def weighted_present_mean(
    values: Dict[str, Optional[float]],
    weights: Dict[str, float],
) -> Optional[float]:
    terms = []
    present_weights = []
    for priority, weight in weights.items():
        value = values.get(priority)
        if value is not None:
            terms.append(weight * value)
            present_weights.append(weight)
    if not present_weights:
        return None
    return sum(terms) / sum(present_weights)


def one_row_per_global_update(sdf: pd.DataFrame) -> pd.DataFrame:
    """
    Global pressure values are repeated once for every known UE during a
    policy application. Selecting one consistently present UE avoids weighting
    a global value by the number of UE decision rows.
    """
    ue_ids = pd.to_numeric(sdf["ue_id"], errors="coerce").dropna()
    if ue_ids.empty:
        return sdf
    representative_ue = ue_ids.min()
    numeric_ids = pd.to_numeric(sdf["ue_id"], errors="coerce")
    return sdf[numeric_ids == representative_ue].copy()


def analyze_scenario(
    xapp: pd.DataFrame,
    marker: pd.Series,
    args: argparse.Namespace,
) -> Dict[str, object]:
    scenario = str(marker[args.scenario_col])
    start_ts = float(marker[args.start_col]) + args.time_offset + args.warmup
    end_ts = float(marker[args.end_col]) + args.time_offset - args.cooldown
    offered = offered_from_marker(marker, args)

    if end_ts <= start_ts:
        raise ValueError(
            f"Scenario {scenario}: analysis end is not after analysis start."
        )

    included = not bool(
        args.exclude_scenario_regex
        and re.search(args.exclude_scenario_regex, scenario)
    )
    sdf = xapp[
        (xapp["timestamp"] >= start_ts) & (xapp["timestamp"] <= end_ts)
    ].copy()

    result: Dict[str, object] = {
        "weight_set": args.weight_set,
        "w_prb": args.w_prb,
        "w_competition": args.w_competition,
        "w_delay": args.w_delay,
        "w_prb_normalized": args.w_prb / args.qos_weight_sum,
        "w_competition_normalized": args.w_competition / args.qos_weight_sum,
        "w_delay_normalized": args.w_delay / args.qos_weight_sum,
        "scenario": scenario,
        "included_in_aggregate": included,
        "analysis_start_ts": start_ts,
        "analysis_end_ts": end_ts,
        "analysis_duration_seconds": end_ts - start_ts,
        "n_rows": int(len(sdf)),
        "be_offered_mbps": offered["best_effort"],
        "medium_offered_mbps": offered["medium"],
        "high_offered_mbps": offered["high"],
        "total_offered_mbps": sum(offered.values()),
        "cell_capacity_mbps": args.cell_capacity_mbps,
    }

    for priority in PRIORITY_ORDER:
        result[f"{priority}_actual_mbps"] = 0.0
        result[f"{priority}_satisfaction"] = None
        result[f"{priority}_avg_target_cap"] = None

    result.update(
        {
            "weighted_satisfaction": None,
            "protected_satisfaction": None,
            "total_actual_mbps": 0.0,
            "aggregate_demand_satisfaction": None,
            "cell_capacity_utilization": 0.0,
            "demand_capacity_efficiency": None,
            "avg_congestion_score": None,
            "avg_prb_pressure": None,
            "avg_competition_pressure": None,
            "avg_delay_pressure": None,
            "avg_valid_delay_count": None,
            "controls_sent": 0,
            "controls_per_minute": 0.0,
            "n_style4_rows": 0,
        }
    )

    if sdf.empty:
        return result

    sdf["priority"] = sdf["priority"].apply(normalize_priority)
    sdf = sdf[sdf["priority"].isin(PRIORITY_ORDER)].copy()
    sdf["is_active_bool"] = parse_bool_series(sdf["is_active"])

    # Only Style 4 contains a newly received per-UE throughput measurement.
    ue_rows = sdf[sdf["trigger"] == "style4_ue"].copy()
    ue_rows["ue_dl_thp_mbps"] = throughput_to_mbps(
        ue_rows["ue_dl_thp"],
        args.throughput_unit,
    )
    result["n_style4_rows"] = int(len(ue_rows))

    per_ue = (
        ue_rows.dropna(subset=["ue_id", "priority", "ue_dl_thp_mbps"])
        .groupby(["priority", "ue_id"], as_index=False)["ue_dl_thp_mbps"]
        .mean()
    )

    actual = {priority: 0.0 for priority in PRIORITY_ORDER}
    for priority, group in per_ue.groupby("priority"):
        actual[priority] = float(group["ue_dl_thp_mbps"].sum())

    sats: Dict[str, Optional[float]] = {}
    for priority in PRIORITY_ORDER:
        sats[priority] = satisfaction(actual[priority], offered[priority])
        result[f"{priority}_actual_mbps"] = actual[priority]
        result[f"{priority}_satisfaction"] = sats[priority]

    total_actual = sum(
        actual[priority]
        for priority in PRIORITY_ORDER
        if offered[priority] > 0
    )
    total_offered = sum(offered.values())
    demand_capacity = min(total_offered, args.cell_capacity_mbps)

    result["weighted_satisfaction"] = weighted_present_mean(
        sats,
        SATISFACTION_WEIGHTS,
    )
    result["protected_satisfaction"] = weighted_present_mean(
        sats,
        PROTECTED_WEIGHTS,
    )
    result["total_actual_mbps"] = total_actual
    result["aggregate_demand_satisfaction"] = (
        min(total_actual / total_offered, 1.0)
        if total_offered > 0
        else None
    )
    result["cell_capacity_utilization"] = min(
        total_actual / args.cell_capacity_mbps,
        1.0,
    )
    result["demand_capacity_efficiency"] = (
        min(total_actual / demand_capacity, 1.0)
        if demand_capacity > 0
        else None
    )

    # Controller diagnostics are not folded into a hidden composite score.
    global_rows = one_row_per_global_update(sdf)
    diagnostic_columns = {
        "congestion_score": "avg_congestion_score",
        "prb_pressure": "avg_prb_pressure",
        "competition_pressure": "avg_competition_pressure",
        "delay_pressure": "avg_delay_pressure",
        "valid_delay_count": "avg_valid_delay_count",
    }
    for source, destination in diagnostic_columns.items():
        if source in global_rows.columns:
            result[destination] = mean_or_none(global_rows[source])

    # Inactive UEs are restored to 100%; including those rows would inflate
    # average caps in pairwise and single-UE scenarios.
    active_rows = sdf[sdf["is_active_bool"]].copy()
    if "target_cap" in active_rows.columns:
        active_rows["target_cap"] = pd.to_numeric(
            active_rows["target_cap"],
            errors="coerce",
        )
        caps = active_rows.groupby("priority")["target_cap"].mean()
        for priority in PRIORITY_ORDER:
            cap = caps.get(priority, float("nan"))
            result[f"{priority}_avg_target_cap"] = (
                None if pd.isna(cap) else float(cap)
            )

    if "control_sent" in sdf.columns:
        controls_sent = int(parse_bool_series(sdf["control_sent"]).sum())
        result["controls_sent"] = controls_sent
        result["controls_per_minute"] = controls_sent / (
            (end_ts - start_ts) / 60.0
        )

    return result


def nullable_mean(
    frame: pd.DataFrame,
    column: str,
) -> float:
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return float(values.mean()) if not values.empty else float("nan")


def format_optional(value: float) -> str:
    return "NaN" if math.isnan(value) else f"{value:.4f}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize priority-PRB xApp experiments without collapsing "
            "network outcomes and controller diagnostics into one score."
        )
    )
    parser.add_argument("--xapp_csv", required=True, help="Path to xApp CSV.")
    parser.add_argument(
        "--markers",
        required=True,
        help="Path to scenario_markers.csv.",
    )
    parser.add_argument(
        "--weight_set",
        default="unknown",
        help="Human-readable label for this QoS-weight configuration.",
    )
    parser.add_argument("--w_prb", type=float, required=True)
    parser.add_argument("--w_competition", type=float, required=True)
    parser.add_argument("--w_delay", type=float, required=True)
    parser.add_argument(
        "--cell_capacity_mbps",
        type=float,
        default=30.0,
        help="Measured reference DL capacity. Default: 30 Mbit/s.",
    )
    parser.add_argument("--out_csv", default="")
    parser.add_argument("--summary_txt", default="")
    parser.add_argument(
        "--throughput_unit",
        default="kbps",
        choices=["kbps", "mbps", "bps"],
    )
    parser.add_argument(
        "--warmup",
        type=float,
        default=5.0,
        help="Seconds ignored after scenario start.",
    )
    parser.add_argument(
        "--cooldown",
        type=float,
        default=2.0,
        help="Seconds ignored before scenario end.",
    )
    parser.add_argument("--time_offset", type=float, default=0.0)
    parser.add_argument(
        "--exclude_scenario_regex",
        default=r"^00",
        help=(
            "Scenario-name regex excluded from aggregate comparison. "
            "Default excludes 00* calibration scenarios; use an empty "
            "string to include everything."
        ),
    )
    parser.add_argument("--scenario_col", default="scenario")
    parser.add_argument("--start_col", default="start_ts")
    parser.add_argument("--end_col", default="end_ts")
    parser.add_argument("--ue1_priority", default="")
    parser.add_argument("--ue2_priority", default="")
    parser.add_argument("--ue3_priority", default="")
    args = parser.parse_args()

    qos_weights = [args.w_prb, args.w_competition, args.w_delay]
    if any(weight < 0 for weight in qos_weights):
        raise SystemExit("QoS-score weights must be non-negative.")
    args.qos_weight_sum = sum(qos_weights)
    if args.qos_weight_sum <= 0:
        raise SystemExit("At least one QoS-score weight must be positive.")
    if args.cell_capacity_mbps <= 0:
        raise SystemExit("--cell_capacity_mbps must be positive.")

    xapp_path = Path(args.xapp_csv)
    markers_path = Path(args.markers)
    xapp = pd.read_csv(xapp_path)
    markers = pd.read_csv(markers_path)

    required_xapp_cols = {
        "timestamp",
        "trigger",
        "ue_id",
        "priority",
        "is_active",
        "ue_dl_thp",
    }
    missing = required_xapp_cols - set(xapp.columns)
    if missing:
        raise SystemExit(
            f"xApp CSV is missing required columns: {sorted(missing)}"
        )

    for column in [args.scenario_col, args.start_col, args.end_col]:
        if column not in markers.columns:
            raise SystemExit(f"Marker CSV is missing required column: {column}")

    xapp["timestamp"] = pd.to_numeric(xapp["timestamp"], errors="coerce")
    xapp = xapp.dropna(subset=["timestamp"])

    summaries = [
        analyze_scenario(xapp, marker, args)
        for _, marker in markers.iterrows()
    ]
    summary = pd.DataFrame(summaries)

    out_csv = (
        Path(args.out_csv)
        if args.out_csv
        else markers_path.with_name("qos_weight_metrics_summary.csv")
    )
    summary.to_csv(out_csv, index=False)

    include_mask = parse_bool_series(summary["included_in_aggregate"])
    included = summary[include_mask].copy()

    aggregate_metrics = {
        "weighted satisfaction": nullable_mean(
            included,
            "weighted_satisfaction",
        ),
        "protected satisfaction": nullable_mean(
            included,
            "protected_satisfaction",
        ),
        "best-effort satisfaction": nullable_mean(
            included,
            "best_effort_satisfaction",
        ),
        "cell-capacity utilization": nullable_mean(
            included,
            "cell_capacity_utilization",
        ),
        "demand/capacity efficiency": nullable_mean(
            included,
            "demand_capacity_efficiency",
        ),
        "controls per minute": nullable_mean(
            included,
            "controls_per_minute",
        ),
    }

    display_cols = [
        "scenario",
        "included_in_aggregate",
        "high_satisfaction",
        "medium_satisfaction",
        "best_effort_satisfaction",
        "weighted_satisfaction",
        "protected_satisfaction",
        "total_actual_mbps",
        "cell_capacity_utilization",
        "demand_capacity_efficiency",
        "avg_delay_pressure",
        "controls_per_minute",
    ]
    summary_table = summary[display_cols].to_string(
        index=False,
        float_format=lambda value: f"{value:.3f}",
    )

    normalized = [
        args.w_prb / args.qos_weight_sum,
        args.w_competition / args.qos_weight_sum,
        args.w_delay / args.qos_weight_sum,
    ]
    aggregate_lines = "\n".join(
        f"{label}: {format_optional(value)}"
        for label, value in aggregate_metrics.items()
    )
    summary_text = (
        "\n=== QoS weight experiment metrics ===\n"
        f"Weight set: {args.weight_set}\n"
        f"Configured weights: PRB={args.w_prb}, "
        f"competition={args.w_competition}, delay={args.w_delay}\n"
        f"Normalized weights: PRB={normalized[0]:.4f}, "
        f"competition={normalized[1]:.4f}, delay={normalized[2]:.4f}\n"
        f"Cell capacity reference: {args.cell_capacity_mbps:.3f} Mbit/s\n"
        f"Included scenarios: {len(included)}/{len(summary)}\n"
        f"xApp CSV: {xapp_path}\n"
        f"Markers: {markers_path}\n"
        f"Output CSV: {out_csv}\n\n"
        "Aggregate comparison metrics (no composite score):\n"
        f"{aggregate_lines}\n\n"
        f"{summary_table}\n"
    )
    print(summary_text, end="")

    summary_txt_path = (
        Path(args.summary_txt)
        if args.summary_txt
        else out_csv.with_name(out_csv.stem + "_summary.txt")
    )
    summary_txt_path.write_text(summary_text, encoding="utf-8")
    print(f"Summary TXT: {summary_txt_path}")


if __name__ == "__main__":
    main()
