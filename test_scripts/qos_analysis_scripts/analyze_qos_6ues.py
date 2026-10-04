#!/usr/bin/env python3
"""Analyze six-UE priority-PRB experiments from xApp, marker and iPerf data.

The scenario CSV preserves the field names used by the three-UE analyzer so
that priority-level baseline/control plots remain comparable.  A companion
long-form CSV stores one row per scenario and UE; this is where within-priority
fairness and individual UE behaviour can be inspected.
"""

from __future__ import annotations

import argparse
import json
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
PROTECTED_WEIGHTS = {"high": 3.0, "medium": 2.0}
UE_BW_PATTERN = re.compile(r"^ue([1-9][0-9]*)_bw$")


def normalize_priority(value: object) -> Optional[str]:
    if value is None or pd.isna(value):
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
    """Parse iperf-style rates such as 7.5M, 500K, 0 or none."""
    if value is None or pd.isna(value):
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
    if unit == "mbps":
        return values
    if unit == "kbps":
        return values / 1000.0
    if unit == "bps":
        return values / 1_000_000.0
    raise ValueError("--throughput_unit must be one of: kbps, mbps, bps")


def discover_ue_names(columns: object, expected_ues: int) -> list[str]:
    numbered = []
    for column in columns:
        match = UE_BW_PATTERN.fullmatch(str(column))
        if match:
            numbered.append((int(match.group(1)), str(column)[:-3]))
    numbered.sort()
    names = [name for _, name in numbered]
    numbers = [number for number, _ in numbered]
    if not names:
        raise ValueError("Marker CSV has no ueN_bw columns.")
    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError(
            "Marker UE columns must be contiguous from ue1; found "
            f"{names}."
        )
    if expected_ues > 0 and len(names) != expected_ues:
        raise ValueError(
            f"Expected {expected_ues} UE marker entries; found {len(names)} "
            f"({', '.join(names)})."
        )
    return names


def parse_ue_id(value: object, fallback: int, column: str) -> int:
    if value is None or pd.isna(value) or str(value).strip() == "":
        return fallback
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {column}: {value!r}") from exc
    if not numeric.is_integer() or numeric < 0:
        raise ValueError(f"Invalid {column}: {value!r}")
    return int(numeric)


def ue_details_from_marker(
    row: pd.Series,
    ue_names: list[str],
) -> list[Dict[str, object]]:
    """Return namespace, xApp UE ID, priority and offered load for every UE."""
    details: list[Dict[str, object]] = []
    for ue in ue_names:
        suffix = int(ue[2:])
        priority_column = f"{ue}_priority"
        if priority_column not in row.index:
            raise ValueError(f"Marker file is missing {priority_column}.")
        priority = normalize_priority(row[priority_column])
        if priority is None:
            raise ValueError(
                f"Marker contains invalid {priority_column}: "
                f"{row[priority_column]!r}."
            )
        id_column = f"{ue}_id"
        ue_id = parse_ue_id(
            row[id_column] if id_column in row.index else None,
            suffix - 1,
            id_column,
        )
        details.append(
            {
                "ue_namespace": ue,
                "ue_id": ue_id,
                "priority": priority,
                "offered_mbps": parse_rate_to_mbps(row[f"{ue}_bw"]),
            }
        )

    ids = [int(item["ue_id"]) for item in details]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Marker contains duplicate UE IDs: {ids}.")
    return details


def offered_from_details(
    row: pd.Series,
    details: list[Dict[str, object]],
) -> Dict[str, float]:
    """Derive class totals from UEs and verify redundant marker totals."""
    offered = {priority: 0.0 for priority in PRIORITY_ORDER}
    for item in details:
        offered[str(item["priority"])] += float(item["offered_mbps"])

    aliases = {
        "best_effort": [
            "be_offered",
            "best_effort_offered",
            "best_effort_bw",
            "be_bw",
        ],
        "medium": ["medium_offered", "me_offered", "medium_bw", "me_bw"],
        "high": ["high_offered", "hi_offered", "high_bw", "hi_bw"],
    }
    for priority, columns in aliases.items():
        for column in columns:
            if column not in row.index:
                continue
            marker_total = parse_rate_to_mbps(row[column])
            if not math.isclose(
                marker_total,
                offered[priority],
                rel_tol=1e-9,
                abs_tol=1e-6,
            ):
                raise ValueError(
                    f"Marker {column}={marker_total:g} Mbit/s does not match "
                    f"the {priority} sum from per-UE fields "
                    f"({offered[priority]:g} Mbit/s)."
                )
    return offered


def read_iperf_interval_metrics(
    path: Path,
    warmup: float,
    cooldown: float,
    boundary_tolerance: float,
) -> Dict[str, object]:
    """Read steady-state UDP receiver metrics from an iperf3 JSON log."""
    result: Dict[str, object] = {
        "status": "ok",
        "goodput_mbps": None,
        "loss_percent": None,
        "jitter_ms": None,
        "packets": None,
        "lost_packets": None,
        "selected_intervals": 0,
        "test_duration_seconds": None,
    }
    if not path.is_file():
        result["status"] = "missing"
        return result
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        result["status"] = "invalid_json"
        return result
    if data.get("error"):
        result["status"] = "iperf_error"
        return result
    intervals = data.get("intervals")
    if not isinstance(intervals, list):
        result["status"] = "no_intervals"
        return result

    test_start = data.get("start", {}).get("test_start", {})
    try:
        duration = float(test_start.get("duration"))
    except (TypeError, ValueError):
        ends = []
        for interval in intervals:
            try:
                ends.append(float(interval.get("sum", {}).get("end")))
            except (TypeError, ValueError):
                continue
        duration = max(ends) if ends else 0.0
    result["test_duration_seconds"] = duration
    analysis_end = duration - cooldown
    if analysis_end <= warmup:
        result["status"] = "invalid_window"
        return result

    selected = []
    for interval in intervals:
        summary = interval.get("sum")
        if not isinstance(summary, dict) or summary.get("omitted"):
            continue
        try:
            start = float(summary["start"])
            end = float(summary["end"])
            seconds = float(summary["seconds"])
            received_bytes = int(summary["bytes"])
            packets = int(summary["packets"])
            lost_packets = int(summary["lost_packets"])
        except (KeyError, TypeError, ValueError):
            continue
        if start + boundary_tolerance < warmup:
            continue
        if end - boundary_tolerance > analysis_end or seconds <= 0:
            continue
        try:
            jitter_ms = float(summary["jitter_ms"])
        except (KeyError, TypeError, ValueError):
            jitter_ms = None
        selected.append(
            {
                "seconds": seconds,
                "bytes": max(0, received_bytes),
                "packets": max(0, packets),
                "lost_packets": max(0, lost_packets),
                "jitter_ms": jitter_ms,
            }
        )
    if not selected:
        result["status"] = "no_usable_intervals"
        return result

    total_seconds = sum(item["seconds"] for item in selected)
    total_bytes = sum(item["bytes"] for item in selected)
    total_packets = sum(item["packets"] for item in selected)
    total_lost = sum(item["lost_packets"] for item in selected)
    jitter_terms = []
    jitter_weights = []
    for item in selected:
        if item["jitter_ms"] is None:
            continue
        received_packets = max(0, item["packets"] - item["lost_packets"])
        weight = received_packets if received_packets > 0 else item["seconds"]
        jitter_terms.append(item["jitter_ms"] * weight)
        jitter_weights.append(weight)
    result.update(
        {
            "goodput_mbps": (
                total_bytes * 8.0 / total_seconds / 1_000_000.0
                if total_seconds > 0
                else None
            ),
            "loss_percent": (
                100.0 * total_lost / total_packets
                if total_packets > 0
                else None
            ),
            "jitter_ms": (
                sum(jitter_terms) / sum(jitter_weights)
                if jitter_weights
                else None
            ),
            "packets": total_packets,
            "lost_packets": total_lost,
            "selected_intervals": len(selected),
        }
    )
    return result


def satisfaction(actual_mbps: float, offered_mbps: float) -> Optional[float]:
    if offered_mbps <= 0:
        return None
    return max(0.0, min(actual_mbps / offered_mbps, 1.0))


def jain_fairness(values: list[float]) -> Optional[float]:
    """Return Jain fairness, or None when fewer than two UEs are present."""
    if len(values) < 2:
        return None
    denominator = len(values) * sum(value * value for value in values)
    if denominator <= 0:
        return None
    return sum(values) ** 2 / denominator


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


def analyze_scenario_iperf(
    scenario: str,
    details: list[Dict[str, object]],
    offered: Dict[str, float],
    args: argparse.Namespace,
) -> tuple[Dict[str, object], Dict[str, Dict[str, object]]]:
    """Load all UE endpoint logs and aggregate receiver metrics by class."""
    result: Dict[str, object] = {}
    endpoints: Dict[str, Dict[str, object]] = {}
    priority_totals = {
        priority: {
            "goodput_mbps": 0.0,
            "packets": 0,
            "lost_packets": 0,
            "jitter_weighted_sum": 0.0,
            "jitter_weight": 0.0,
            "expected_logs": 0,
            "valid_logs": 0,
            "satisfactions": [],
        }
        for priority in PRIORITY_ORDER
    }
    test_durations = []

    for item in details:
        ue = str(item["ue_namespace"])
        priority = str(item["priority"])
        offered_mbps = float(item["offered_mbps"])
        path = args.iperf_dir_path / f"{scenario}_{ue}.log"
        result[f"{ue}_iperf_file"] = str(path)
        if offered_mbps <= 0:
            metrics: Dict[str, object] = {
                "status": "not_expected",
                "goodput_mbps": None,
                "loss_percent": None,
                "jitter_ms": None,
                "packets": None,
                "lost_packets": None,
                "selected_intervals": 0,
                "test_duration_seconds": None,
                "satisfaction": None,
                "file": str(path),
            }
            endpoints[ue] = metrics
            result[f"{ue}_iperf_status"] = "not_expected"
            result[f"{ue}_iperf_selected_intervals"] = 0
            continue

        totals = priority_totals[priority]
        totals["expected_logs"] += 1
        metrics = read_iperf_interval_metrics(
            path,
            args.warmup,
            args.cooldown,
            args.iperf_boundary_tolerance,
        )
        metrics["file"] = str(path)
        status = str(metrics["status"])
        result[f"{ue}_iperf_status"] = status
        result[f"{ue}_iperf_selected_intervals"] = metrics[
            "selected_intervals"
        ]
        duration = metrics.get("test_duration_seconds")
        if duration is not None:
            test_durations.append(float(duration))
        if status != "ok":
            metrics["satisfaction"] = None
            endpoints[ue] = metrics
            if args.require_iperf:
                raise ValueError(
                    f"Scenario {scenario}: expected iperf log {path} has "
                    f"status {status}."
                )
            continue

        goodput = float(metrics["goodput_mbps"])
        packets = int(metrics["packets"])
        lost_packets = int(metrics["lost_packets"])
        ue_satisfaction = satisfaction(goodput, offered_mbps)
        metrics["satisfaction"] = ue_satisfaction
        endpoints[ue] = metrics
        totals["goodput_mbps"] += goodput
        totals["packets"] += packets
        totals["lost_packets"] += lost_packets
        totals["valid_logs"] += 1
        if ue_satisfaction is not None:
            totals["satisfactions"].append(ue_satisfaction)
        jitter = metrics["jitter_ms"]
        received_packets = max(0, packets - lost_packets)
        if jitter is not None:
            weight = received_packets if received_packets > 0 else 1
            totals["jitter_weighted_sum"] += float(jitter) * weight
            totals["jitter_weight"] += weight

    total_goodput = 0.0
    total_packets = 0
    total_lost = 0
    iperf_sats: Dict[str, Optional[float]] = {}
    expected_logs = sum(
        int(totals["expected_logs"]) for totals in priority_totals.values()
    )
    valid_logs = sum(
        int(totals["valid_logs"]) for totals in priority_totals.values()
    )
    iperf_complete = expected_logs > 0 and valid_logs == expected_logs

    for priority in PRIORITY_ORDER:
        totals = priority_totals[priority]
        expected = int(totals["expected_logs"])
        valid = int(totals["valid_logs"])
        complete = expected > 0 and valid == expected
        goodput = float(totals["goodput_mbps"]) if complete else None
        packets = int(totals["packets"]) if complete else None
        lost_packets = int(totals["lost_packets"]) if complete else None
        loss_percent = (
            100.0 * lost_packets / packets
            if packets is not None and packets > 0
            else None
        )
        jitter_ms = (
            float(totals["jitter_weighted_sum"])
            / float(totals["jitter_weight"])
            if totals["jitter_weight"] > 0
            else None
        )
        iperf_sats[priority] = (
            satisfaction(goodput, offered[priority])
            if goodput is not None and offered[priority] > 0
            else None
        )
        per_ue_sats = [float(value) for value in totals["satisfactions"]]
        result[f"{priority}_iperf_goodput_mbps"] = goodput
        result[f"{priority}_iperf_satisfaction"] = iperf_sats[priority]
        result[f"{priority}_iperf_loss_percent"] = loss_percent
        result[f"{priority}_iperf_jitter_ms"] = jitter_ms
        result[f"{priority}_iperf_packets"] = packets
        result[f"{priority}_iperf_lost_packets"] = lost_packets
        result[f"{priority}_iperf_min_ue_satisfaction"] = (
            min(per_ue_sats) if complete and per_ue_sats else None
        )
        result[f"{priority}_iperf_jain_satisfaction"] = (
            jain_fairness(per_ue_sats) if complete else None
        )
        if goodput is not None:
            total_goodput += goodput
        if packets is not None:
            total_packets += packets
        if lost_packets is not None:
            total_lost += lost_packets

    total_offered = sum(offered.values())
    demand_capacity = min(total_offered, args.cell_capacity_mbps)
    result.update(
        {
            "iperf_expected_logs": expected_logs,
            "iperf_valid_logs": valid_logs,
            "iperf_complete": iperf_complete,
            "total_iperf_goodput_mbps": (
                total_goodput if iperf_complete else None
            ),
            "iperf_weighted_satisfaction": (
                weighted_present_mean(iperf_sats, SATISFACTION_WEIGHTS)
                if iperf_complete
                else None
            ),
            "iperf_protected_satisfaction": (
                weighted_present_mean(iperf_sats, PROTECTED_WEIGHTS)
                if iperf_complete
                else None
            ),
            "iperf_aggregate_demand_satisfaction": (
                min(total_goodput / total_offered, 1.0)
                if iperf_complete and total_offered > 0
                else None
            ),
            "iperf_cell_capacity_utilization": (
                min(total_goodput / args.cell_capacity_mbps, 1.0)
                if iperf_complete
                else None
            ),
            "iperf_demand_capacity_efficiency": (
                min(total_goodput / demand_capacity, 1.0)
                if iperf_complete and demand_capacity > 0
                else None
            ),
            "iperf_total_packets": total_packets if iperf_complete else None,
            "iperf_total_lost_packets": (
                total_lost if iperf_complete else None
            ),
            "iperf_total_loss_percent": (
                100.0 * total_lost / total_packets
                if iperf_complete and total_packets > 0
                else None
            ),
            "iperf_test_duration_seconds": (
                min(test_durations) if test_durations else None
            ),
        }
    )
    return result, endpoints


def mean_or_none(series: pd.Series) -> Optional[float]:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    return float(numeric.mean()) if not numeric.empty else None


def one_row_per_global_update(sdf: pd.DataFrame) -> pd.DataFrame:
    """Use one UE copy of xApp-global diagnostic fields per update stream."""
    ue_ids = pd.to_numeric(sdf["ue_id"], errors="coerce").dropna()
    if ue_ids.empty:
        return sdf
    representative = ue_ids.min()
    numeric_ids = pd.to_numeric(sdf["ue_id"], errors="coerce")
    return sdf[numeric_ids == representative].copy()


def numeric_values(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").dropna()


def empty_endpoint(path: Optional[Path] = None) -> Dict[str, object]:
    return {
        "file": str(path) if path is not None else None,
        "status": "not_analyzed",
        "selected_intervals": 0,
        "goodput_mbps": None,
        "satisfaction": None,
        "loss_percent": None,
        "jitter_ms": None,
        "packets": None,
        "lost_packets": None,
        "test_duration_seconds": None,
    }


def analyze_scenario(
    xapp: pd.DataFrame,
    marker: pd.Series,
    ue_names: list[str],
    args: argparse.Namespace,
) -> tuple[Dict[str, object], list[Dict[str, object]]]:
    scenario = str(marker[args.scenario_col])
    details = ue_details_from_marker(marker, ue_names)
    offered = offered_from_details(marker, details)
    endpoint_metrics: Dict[str, Dict[str, object]] = {}
    if args.iperf_dir_path is not None:
        iperf_result, endpoint_metrics = analyze_scenario_iperf(
            scenario,
            details,
            offered,
            args,
        )
    else:
        iperf_result = {}
        for item in details:
            ue = str(item["ue_namespace"])
            endpoint_metrics[ue] = empty_endpoint()

    marker_start_ts = float(marker[args.start_col]) + args.time_offset
    marker_end_ts = float(marker[args.end_col]) + args.time_offset
    traffic_end_ts = marker_end_ts
    analysis_window_source = "marker"
    if args.traffic_duration_seconds > 0:
        traffic_end_ts = min(
            traffic_end_ts,
            marker_start_ts + args.traffic_duration_seconds,
        )
        analysis_window_source = "cli_traffic_duration"
    elif iperf_result.get("iperf_test_duration_seconds") is not None:
        traffic_end_ts = min(
            traffic_end_ts,
            marker_start_ts
            + float(iperf_result["iperf_test_duration_seconds"]),
        )
        analysis_window_source = "iperf_test_duration"
    start_ts = marker_start_ts + args.warmup
    end_ts = traffic_end_ts - args.cooldown
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
    active_details = [
        item for item in details if float(item["offered_mbps"]) > 0
    ]
    priority_counts = {
        priority: sum(
            1
            for item in active_details
            if str(item["priority"]) == priority
        )
        for priority in PRIORITY_ORDER
    }

    result: Dict[str, object] = {
        "weight_set": args.weight_set,
        "w_prb": args.w_prb,
        "w_competition": args.w_competition,
        "w_delay": args.w_delay,
        "w_prb_normalized": args.w_prb / args.qos_weight_sum,
        "w_competition_normalized": (
            args.w_competition / args.qos_weight_sum
        ),
        "w_delay_normalized": args.w_delay / args.qos_weight_sum,
        "scenario": scenario,
        "included_in_aggregate": included,
        "analysis_start_ts": start_ts,
        "analysis_end_ts": end_ts,
        "analysis_duration_seconds": end_ts - start_ts,
        "analysis_window_source": analysis_window_source,
        "n_rows": int(len(sdf)),
        "be_offered_mbps": offered["best_effort"],
        "medium_offered_mbps": offered["medium"],
        "high_offered_mbps": offered["high"],
        "total_offered_mbps": sum(offered.values()),
        "cell_capacity_mbps": args.cell_capacity_mbps,
        "expected_traffic_ue_count": len(active_details),
    }
    for priority in PRIORITY_ORDER:
        result[f"{priority}_active_ue_count"] = priority_counts[priority]
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
            "controls_per_active_ue_per_minute": 0.0,
            "n_style4_rows": 0,
        }
    )
    result.update(iperf_result)

    long_rows: list[Dict[str, object]] = []
    if not sdf.empty:
        sdf["priority"] = sdf["priority"].apply(normalize_priority)
        sdf = sdf[sdf["priority"].isin(PRIORITY_ORDER)].copy()
        sdf["ue_id_numeric"] = pd.to_numeric(sdf["ue_id"], errors="coerce")
        sdf["is_active_bool"] = parse_bool_series(sdf["is_active"])

    ue_rows = (
        sdf[sdf["trigger"] == "style4_ue"].copy()
        if not sdf.empty
        else sdf.copy()
    )
    if not ue_rows.empty:
        ue_rows["ue_dl_thp_mbps"] = throughput_to_mbps(
            ue_rows["ue_dl_thp"],
            args.throughput_unit,
        )
    result["n_style4_rows"] = int(len(ue_rows))

    per_ue_kpm: Dict[int, Dict[str, object]] = {}
    per_ue_delay: Dict[int, Dict[str, object]] = {}
    per_ue_cap: Dict[int, Optional[float]] = {}
    for item in details:
        ue_id = int(item["ue_id"])
        priority = str(item["priority"])
        observed = sdf[sdf["ue_id_numeric"] == ue_id] if not sdf.empty else sdf
        observed_priorities = set(observed["priority"].dropna().astype(str))
        if observed_priorities and observed_priorities != {priority}:
            raise ValueError(
                f"Scenario {scenario}: marker maps {item['ue_namespace']} "
                f"(ID {ue_id}) to {priority}, but xApp rows contain "
                f"{sorted(observed_priorities)}."
            )

        samples = (
            ue_rows[ue_rows["ue_id_numeric"] == ue_id]
            if not ue_rows.empty
            else ue_rows
        )
        throughputs = (
            numeric_values(samples["ue_dl_thp_mbps"])
            if "ue_dl_thp_mbps" in samples.columns
            else pd.Series(dtype=float)
        )
        kpm_actual = float(throughputs.mean()) if not throughputs.empty else 0.0
        per_ue_kpm[ue_id] = {
            "actual_mbps": kpm_actual,
            "samples": int(len(throughputs)),
        }

        delay_rows = (
            samples[samples["is_active_bool"]].copy()
            if not samples.empty
            else samples.copy()
        )
        delays = (
            numeric_values(delay_rows["ue_dl_delay_ms"])
            if "ue_dl_delay_ms" in delay_rows.columns
            else pd.Series(dtype=float)
        )
        delays = delays[
            (delays >= 0.0) & (delays <= args.max_valid_delay_ms)
        ]
        per_ue_delay[ue_id] = {
            "median_ms": float(delays.median()) if not delays.empty else None,
            "p95_ms": (
                float(delays.quantile(0.95)) if not delays.empty else None
            ),
            "samples": int(len(delays)),
        }

        cap_rows = (
            observed[observed["is_active_bool"]]
            if not observed.empty
            else observed
        )
        caps = (
            numeric_values(cap_rows["target_cap"])
            if "target_cap" in cap_rows.columns
            else pd.Series(dtype=float)
        )
        per_ue_cap[ue_id] = float(caps.mean()) if not caps.empty else None

    actual = {priority: 0.0 for priority in PRIORITY_ORDER}
    for item in active_details:
        actual[str(item["priority"])] += float(
            per_ue_kpm[int(item["ue_id"])]["actual_mbps"]
        )
    sats: Dict[str, Optional[float]] = {}
    for priority in PRIORITY_ORDER:
        sats[priority] = satisfaction(actual[priority], offered[priority])
        result[f"{priority}_actual_mbps"] = actual[priority]
        result[f"{priority}_satisfaction"] = sats[priority]
        iperf_goodput = result.get(f"{priority}_iperf_goodput_mbps")
        result[f"{priority}_kpm_iperf_gap_percent"] = (
            100.0 * (actual[priority] - float(iperf_goodput))
            / float(iperf_goodput)
            if iperf_goodput is not None and float(iperf_goodput) > 0
            else None
        )

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

    if not sdf.empty:
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
            controls_per_minute = controls_sent / ((end_ts - start_ts) / 60.0)
            result["controls_sent"] = controls_sent
            result["controls_per_minute"] = controls_per_minute
            if active_details:
                result["controls_per_active_ue_per_minute"] = (
                    controls_per_minute / len(active_details)
                )

    for item in details:
        ue = str(item["ue_namespace"])
        ue_id = int(item["ue_id"])
        offered_mbps = float(item["offered_mbps"])
        endpoint = endpoint_metrics.get(ue, empty_endpoint())
        kpm_actual = float(per_ue_kpm[ue_id]["actual_mbps"])
        delay = per_ue_delay[ue_id]
        long_rows.append(
            {
                "weight_set": args.weight_set,
                "w_prb": args.w_prb,
                "w_competition": args.w_competition,
                "w_delay": args.w_delay,
                "w_prb_normalized": args.w_prb / args.qos_weight_sum,
                "w_competition_normalized": (
                    args.w_competition / args.qos_weight_sum
                ),
                "w_delay_normalized": args.w_delay / args.qos_weight_sum,
                "scenario": scenario,
                "included_in_aggregate": included,
                "analysis_start_ts": start_ts,
                "analysis_end_ts": end_ts,
                "analysis_duration_seconds": end_ts - start_ts,
                "ue_namespace": ue,
                "ue_id": ue_id,
                "priority": item["priority"],
                "traffic_active": offered_mbps > 0,
                "offered_mbps": offered_mbps,
                "kpm_actual_mbps": kpm_actual,
                "kpm_satisfaction": satisfaction(kpm_actual, offered_mbps),
                "kpm_samples": per_ue_kpm[ue_id]["samples"],
                "delay_median_ms": delay["median_ms"],
                "delay_p95_ms": delay["p95_ms"],
                "delay_samples": delay["samples"],
                "avg_target_cap": per_ue_cap[ue_id],
                "iperf_file": endpoint.get("file"),
                "iperf_status": endpoint.get("status"),
                "iperf_selected_intervals": endpoint.get(
                    "selected_intervals"
                ),
                "iperf_goodput_mbps": endpoint.get("goodput_mbps"),
                "iperf_satisfaction": endpoint.get("satisfaction"),
                "iperf_loss_percent": endpoint.get("loss_percent"),
                "iperf_jitter_ms": endpoint.get("jitter_ms"),
                "iperf_packets": endpoint.get("packets"),
                "iperf_lost_packets": endpoint.get("lost_packets"),
            }
        )
    return result, long_rows


def nullable_mean(frame: pd.DataFrame, column: str) -> float:
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return float(values.mean()) if not values.empty else float("nan")


def format_optional(value: float) -> str:
    return "NaN" if math.isnan(value) else f"{value:.4f}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize six-UE priority-PRB experiments, including "
            "within-priority per-UE fairness."
        )
    )
    parser.add_argument("--xapp_csv", required=True, help="Path to xApp CSV.")
    parser.add_argument(
        "--markers",
        required=True,
        help="Path to scenario_markers.csv.",
    )
    parser.add_argument("--weight_set", default="unknown")
    parser.add_argument("--w_prb", type=float, required=True)
    parser.add_argument("--w_competition", type=float, required=True)
    parser.add_argument("--w_delay", type=float, required=True)
    parser.add_argument(
        "--expected_ues",
        type=int,
        default=6,
        help="Required number of ueN marker entries (default: 6; 0 disables).",
    )
    parser.add_argument(
        "--cell_capacity_mbps",
        type=float,
        default=28.0,
        help="Measured six-UE reference DL capacity (default: 28.0).",
    )
    parser.add_argument(
        "--iperf_dir",
        default="",
        help="Directory containing <scenario>_<ueN>.log iPerf JSON files.",
    )
    parser.add_argument(
        "--require_iperf",
        action="store_true",
        help="Fail if any expected active-UE iPerf log is missing or invalid.",
    )
    parser.add_argument(
        "--iperf_boundary_tolerance",
        type=float,
        default=0.05,
    )
    parser.add_argument(
        "--traffic_duration_seconds",
        type=float,
        default=0.0,
    )
    parser.add_argument("--out_csv", default="")
    parser.add_argument(
        "--out_ue_csv",
        default="",
        help="Per-UE long CSV (default: <out_csv stem>_per_ue.csv).",
    )
    parser.add_argument("--summary_txt", default="")
    parser.add_argument(
        "--throughput_unit",
        default="kbps",
        choices=["kbps", "mbps", "bps"],
    )
    parser.add_argument("--warmup", type=float, default=5.0)
    parser.add_argument("--cooldown", type=float, default=2.0)
    parser.add_argument("--time_offset", type=float, default=0.0)
    parser.add_argument(
        "--max_valid_delay_ms",
        type=float,
        default=6000.0,
        help="Maximum raw per-UE RLC delay retained (default: 6000).",
    )
    parser.add_argument(
        "--exclude_scenario_regex",
        default=r"^(00)",
        help="Scenario regex excluded from aggregate metrics.",
    )
    parser.add_argument("--scenario_col", default="scenario")
    parser.add_argument("--start_col", default="start_ts")
    parser.add_argument("--end_col", default="end_ts")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    weights = [args.w_prb, args.w_competition, args.w_delay]
    if any(weight < 0 for weight in weights):
        raise SystemExit("QoS-score weights must be non-negative.")
    args.qos_weight_sum = sum(weights)
    if args.qos_weight_sum <= 0:
        raise SystemExit("At least one QoS-score weight must be positive.")
    if args.expected_ues < 0:
        raise SystemExit("--expected_ues must be non-negative.")
    if args.cell_capacity_mbps <= 0:
        raise SystemExit("--cell_capacity_mbps must be positive.")
    if args.iperf_boundary_tolerance < 0:
        raise SystemExit("--iperf_boundary_tolerance must be non-negative.")
    if args.traffic_duration_seconds < 0:
        raise SystemExit("--traffic_duration_seconds must be non-negative.")
    if args.warmup < 0 or args.cooldown < 0:
        raise SystemExit("--warmup and --cooldown must be non-negative.")
    if args.max_valid_delay_ms <= 0:
        raise SystemExit("--max_valid_delay_ms must be positive.")
    if args.require_iperf and not args.iperf_dir:
        raise SystemExit("--require_iperf requires --iperf_dir.")


def main() -> None:
    args = build_parser().parse_args()
    validate_args(args)
    args.iperf_dir_path = Path(args.iperf_dir) if args.iperf_dir else None
    if args.iperf_dir_path is not None and not args.iperf_dir_path.is_dir():
        raise SystemExit(
            f"--iperf_dir is not a directory: {args.iperf_dir_path}"
        )

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
        "ue_dl_delay_ms",
        "target_cap",
    }
    missing = required_xapp_cols - set(xapp.columns)
    if missing:
        raise SystemExit(
            f"xApp CSV is missing required columns: {sorted(missing)}"
        )
    for column in [args.scenario_col, args.start_col, args.end_col]:
        if column not in markers.columns:
            raise SystemExit(f"Marker CSV is missing required column: {column}")
    try:
        ue_names = discover_ue_names(markers.columns, args.expected_ues)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    for ue in ue_names:
        if f"{ue}_priority" not in markers.columns:
            raise SystemExit(
                f"Marker CSV is missing required column: {ue}_priority"
            )

    xapp["timestamp"] = pd.to_numeric(xapp["timestamp"], errors="coerce")
    xapp = xapp.dropna(subset=["timestamp"])
    summaries = []
    all_ue_rows: list[Dict[str, object]] = []
    for _, marker in markers.iterrows():
        try:
            summary, ue_rows = analyze_scenario(xapp, marker, ue_names, args)
        except ValueError as exc:
            scenario = marker.get(args.scenario_col, "unknown")
            raise SystemExit(f"Scenario {scenario}: {exc}") from exc
        summaries.append(summary)
        all_ue_rows.extend(ue_rows)
    summary = pd.DataFrame(summaries)
    per_ue = pd.DataFrame(all_ue_rows)

    out_csv = (
        Path(args.out_csv)
        if args.out_csv
        else markers_path.with_name("qos_6ue_metrics_summary.csv")
    )
    out_ue_csv = (
        Path(args.out_ue_csv)
        if args.out_ue_csv
        else out_csv.with_name(out_csv.stem + "_per_ue.csv")
    )
    summary_txt_path = (
        Path(args.summary_txt)
        if args.summary_txt
        else out_csv.with_name(out_csv.stem + "_summary.txt")
    )
    for path in (out_csv, out_ue_csv, summary_txt_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(out_csv, index=False)
    per_ue.to_csv(out_ue_csv, index=False)

    include_mask = parse_bool_series(summary["included_in_aggregate"])
    included = summary[include_mask].copy()
    aggregate_metrics = {
        "weighted satisfaction": nullable_mean(
            included, "weighted_satisfaction"
        ),
        "protected satisfaction": nullable_mean(
            included, "protected_satisfaction"
        ),
        "best-effort satisfaction": nullable_mean(
            included, "best_effort_satisfaction"
        ),
        "cell-capacity utilization": nullable_mean(
            included, "cell_capacity_utilization"
        ),
        "demand/capacity efficiency": nullable_mean(
            included, "demand_capacity_efficiency"
        ),
        "controls per minute": nullable_mean(
            included, "controls_per_minute"
        ),
        "controls per active UE per minute": nullable_mean(
            included, "controls_per_active_ue_per_minute"
        ),
    }
    if args.iperf_dir_path is not None:
        aggregate_metrics.update(
            {
                "iperf weighted satisfaction": nullable_mean(
                    included, "iperf_weighted_satisfaction"
                ),
                "iperf protected satisfaction": nullable_mean(
                    included, "iperf_protected_satisfaction"
                ),
                "iperf cell-capacity utilization": nullable_mean(
                    included, "iperf_cell_capacity_utilization"
                ),
                "iperf demand/capacity efficiency": nullable_mean(
                    included, "iperf_demand_capacity_efficiency"
                ),
                "iperf packet loss": nullable_mean(
                    included, "iperf_total_loss_percent"
                ),
            }
        )

    network_columns = [
        "scenario",
        "included_in_aggregate",
        "expected_traffic_ue_count",
        "high_satisfaction",
        "medium_satisfaction",
        "best_effort_satisfaction",
        "weighted_satisfaction",
        "protected_satisfaction",
        "total_actual_mbps",
        "cell_capacity_utilization",
        "demand_capacity_efficiency",
    ]
    network_table = summary[network_columns].rename(
        columns={
            "scenario": "Scenario",
            "included_in_aggregate": "Included",
            "expected_traffic_ue_count": "UEs",
            "high_satisfaction": "H_sat",
            "medium_satisfaction": "M_sat",
            "best_effort_satisfaction": "BE_sat",
            "weighted_satisfaction": "W_sat",
            "protected_satisfaction": "P_sat",
            "total_actual_mbps": "DL_Mbps",
            "cell_capacity_utilization": "Cell_util",
            "demand_capacity_efficiency": "Demand_eff",
        }
    ).to_string(index=False, float_format=lambda value: f"{value:.3f}")

    controller_table = summary[
        [
            "scenario",
            "avg_delay_pressure",
            "controls_per_minute",
            "controls_per_active_ue_per_minute",
        ]
    ].rename(
        columns={
            "scenario": "Scenario",
            "avg_delay_pressure": "Delay_pressure",
            "controls_per_minute": "Controls_min",
            "controls_per_active_ue_per_minute": "Controls_UE_min",
        }
    ).to_string(index=False, float_format=lambda value: f"{value:.3f}")

    if args.iperf_dir_path is not None:
        iperf_table = summary[
            [
                "scenario",
                "total_iperf_goodput_mbps",
                "iperf_weighted_satisfaction",
                "iperf_cell_capacity_utilization",
                "high_iperf_loss_percent",
                "medium_iperf_loss_percent",
                "best_effort_iperf_loss_percent",
            ]
        ].rename(
            columns={
                "scenario": "Scenario",
                "total_iperf_goodput_mbps": "Goodput_Mbps",
                "iperf_weighted_satisfaction": "W_sat",
                "iperf_cell_capacity_utilization": "Cell_util",
                "high_iperf_loss_percent": "H_loss_%",
                "medium_iperf_loss_percent": "M_loss_%",
                "best_effort_iperf_loss_percent": "BE_loss_%",
            }
        ).to_string(index=False, float_format=lambda value: f"{value:.3f}")
        iperf_section = f"Iperf receiver results:\n{iperf_table}\n\n"

        fairness_columns = ["scenario"]
        for priority in PRIORITY_ORDER:
            fairness_columns.extend(
                [
                    f"{priority}_active_ue_count",
                    f"{priority}_iperf_min_ue_satisfaction",
                    f"{priority}_iperf_jain_satisfaction",
                ]
            )
        fairness_table = summary[fairness_columns].rename(
            columns={
                "scenario": "Scenario",
                "high_active_ue_count": "H_n",
                "high_iperf_min_ue_satisfaction": "H_min",
                "high_iperf_jain_satisfaction": "H_Jain",
                "medium_active_ue_count": "M_n",
                "medium_iperf_min_ue_satisfaction": "M_min",
                "medium_iperf_jain_satisfaction": "M_Jain",
                "best_effort_active_ue_count": "BE_n",
                "best_effort_iperf_min_ue_satisfaction": "BE_min",
                "best_effort_iperf_jain_satisfaction": "BE_Jain",
            }
        ).to_string(index=False, float_format=lambda value: f"{value:.3f}")
        fairness_section = (
            "Within-priority endpoint satisfaction fairness:\n"
            f"{fairness_table}\n\n"
        )
    else:
        iperf_section = "Iperf receiver results: not available.\n\n"
        fairness_section = (
            "Within-priority endpoint satisfaction fairness: "
            "not available without iPerf.\n\n"
        )

    normalized = [weight / args.qos_weight_sum for weight in (
        args.w_prb,
        args.w_competition,
        args.w_delay,
    )]
    aggregate_lines = "\n".join(
        f"{label}: {format_optional(value)}"
        for label, value in aggregate_metrics.items()
    )
    iperf_line = (
        f"Iperf directory: {args.iperf_dir_path}\n"
        if args.iperf_dir_path is not None
        else "Iperf directory: not provided\n"
    )
    summary_text = (
        "\n=== Six-UE QoS experiment metrics ===\n"
        f"Weight set: {args.weight_set}\n"
        f"Discovered UEs: {', '.join(ue_names)}\n"
        f"Configured weights: PRB={args.w_prb}, "
        f"competition={args.w_competition}, delay={args.w_delay}\n"
        f"Normalized weights: PRB={normalized[0]:.4f}, "
        f"competition={normalized[1]:.4f}, delay={normalized[2]:.4f}\n"
        f"Cell capacity reference: {args.cell_capacity_mbps:.3f} Mbit/s\n"
        f"Included scenarios: {len(included)}/{len(summary)}\n"
        f"xApp CSV: {xapp_path}\n"
        f"Markers: {markers_path}\n"
        f"{iperf_line}"
        f"Scenario CSV: {out_csv}\n"
        f"Per-UE CSV: {out_ue_csv}\n\n"
        "Aggregate comparison metrics (no composite score):\n"
        f"{aggregate_lines}\n\n"
        "Network outcomes from xApp/KPM data:\n"
        f"{network_table}\n\n"
        f"{iperf_section}"
        f"{fairness_section}"
        "Controller diagnostics:\n"
        f"{controller_table}\n"
    )
    print(summary_text, end="")
    summary_txt_path.write_text(summary_text, encoding="utf-8")
    print(f"Summary TXT: {summary_txt_path}")


if __name__ == "__main__":
    main()
