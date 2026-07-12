#!/usr/bin/env python3
"""Preference-sensitive matched operational tradeoff analysis."""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from typing import Any, Iterable


SCHEMA_VERSION = "operational-tradeoffs-v1"
CORE_METRICS = (
    "operational_correctness_score",
    "modeled_weighted_token_load",
    "solve_wall_seconds",
    "execution_calls_started",
)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _mean(values: Iterable[float | None]) -> float | None:
    selected = [value for value in values if value is not None]
    return statistics.fmean(selected) if selected else None


def _median(values: Iterable[float | None]) -> float | None:
    selected = [value for value in values if value is not None]
    return statistics.median(selected) if selected else None


def _percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def absolute_quality(row: dict[str, Any]) -> dict[str, Any]:
    direct_fraction = row.get("issue_contract_pass_fraction")
    common_fraction = row.get("common_regression_pass_fraction")
    failures = []
    if row.get("issue_contract_full_pass") is not True:
        failures.append("direct_issue_contract")
    if row.get("common_regression_full_pass") is not True:
        failures.append("common_regression")
    if not row.get("implementation_evaluated"):
        failures.append("implementation_not_evaluated")
    score = float(row.get("operational_correctness_score") or 0.0)
    viability = "successful" if row.get("task_success") else "partial" if score > 0 else "unsuccessful"
    return {
        "correctness_score": score,
        "direct_issue_contract_pass_fraction": direct_fraction,
        "direct_issue_contract_full_pass": row.get("issue_contract_full_pass"),
        "common_regression_pass_fraction": common_fraction,
        "task_success": bool(row.get("task_success")),
        "viability_class": viability,
        "failed_requirements": failures,
    }


def _relative(treatment: dict[str, Any], baseline: dict[str, Any], tolerance: float) -> dict[str, Any]:
    correctness = float(treatment.get("operational_correctness_score") or 0.0)
    baseline_correctness = float(baseline.get("operational_correctness_score") or 0.0)
    delta = correctness - baseline_correctness

    def ratio(field: str) -> float | None:
        left, right = _number(treatment.get(field)), _number(baseline.get(field))
        return left / right if left is not None and right not in {None, 0.0} else None

    if delta > 0:
        relation = "better"
    elif delta == 0:
        relation = "equivalent"
    elif delta >= -tolerance:
        relation = "non_inferior_with_tolerance"
    else:
        relation = "worse"
    return {
        "correctness_delta_points": delta,
        "correctness_relation": relation,
        "token_ratio": ratio("modeled_weighted_token_load"),
        "time_ratio": ratio("solve_wall_seconds"),
        "call_ratio": ratio("execution_calls_started"),
    }


def enrich_rows(rows: list[dict[str, Any]], default_tolerance: float) -> None:
    baselines = {
        (str(row.get("issue_id")), int(row.get("repetition") or 0)): row
        for row in rows if row.get("variant") == "baseline-none" and row.get("operational_rank_eligible")
    }
    for row in rows:
        row["absolute_quality"] = absolute_quality(row)
        if not row.get("operational_rank_eligible"):
            row["relative_to_matched_baseline"] = None
            continue
        if row.get("variant") == "baseline-none":
            row["relative_to_matched_baseline"] = {
                "correctness_delta_points": 0.0, "correctness_relation": "equivalent",
                "token_ratio": 1.0, "time_ratio": 1.0, "call_ratio": 1.0,
            }
            continue
        baseline = baselines.get((str(row.get("issue_id")), int(row.get("repetition") or 0)))
        row["relative_to_matched_baseline"] = (
            _relative(row, baseline, default_tolerance) if baseline else {
                "correctness_delta_points": None, "correctness_relation": "inconclusive",
                "token_ratio": None, "time_ratio": None, "call_ratio": None,
            }
        )


def _dominates(left: dict[str, Any], right: dict[str, Any]) -> bool:
    dimensions = (
        left["correctness"] >= right["correctness"],
        left["tokens"] <= right["tokens"],
        left["time"] <= right["time"],
    )
    strict = (
        left["correctness"] > right["correctness"]
        or left["tokens"] < right["tokens"]
        or left["time"] < right["time"]
    )
    return all(dimensions) and strict


def _frontier(points: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(name for name, point in points.items() if not any(
        other_name != name and _dominates(other, point)
        for other_name, other in points.items()
    ))


def _tolerance_dominates(
    left: dict[str, Any], right: dict[str, Any], tolerance: float
) -> bool:
    return (
        left["correctness"] >= right["correctness"] - tolerance
        and left["tokens"] <= right["tokens"]
        and left["time"] <= right["time"]
        and (
            left["correctness"] > right["correctness"]
            or left["tokens"] < right["tokens"]
            or left["time"] < right["time"]
        )
    )


def _tolerance_frontier(
    points: dict[str, dict[str, Any]], tolerance: float
) -> list[str]:
    return sorted(name for name, point in points.items() if not any(
        other_name != name and _tolerance_dominates(other, point, tolerance)
        for other_name, other in points.items()
    ))


def _resource_saving(ratio: float | None) -> float | None:
    return None if ratio is None else 100.0 * (1.0 - ratio)


def _loss_per_ten_percent(correctness_delta: float, saving: float | None) -> float | None:
    loss = max(0.0, -correctness_delta)
    return loss / (saving / 10.0) if saving is not None and saving > 0 else None


def _classify_tradeoff(delta: float, token_ratio: float | None, time_ratio: float | None,
                       tolerance: float, baseline_dominates: bool, treatment_dominates: bool) -> str:
    if treatment_dominates:
        return "strictly_dominates"
    if baseline_dominates:
        return "dominated"
    acceptable = delta >= -tolerance
    resource_better = (token_ratio is not None and token_ratio < 1) or (time_ratio is not None and time_ratio < 1)
    if acceptable and resource_better:
        return "tolerance_aware_efficiency_preferred"
    if delta < -tolerance:
        return "dominated" if not resource_better else "pareto_tradeoff"
    return "pareto_tradeoff"


def analyze_operational_tradeoffs(rows: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    config = policy["operational_tradeoffs"]
    tolerances = [float(value) for value in config["correctness_loss_tolerance_grid_points"]]
    default_tolerance = float(policy["operational_comparison"]["correctness_equivalence_margin_points"])
    enrich_rows(rows, default_tolerance)
    eligible = [row for row in rows if row.get("operational_rank_eligible")]
    by_variant_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        by_variant_rows[str(row["variant"])].append(row)
    points: dict[str, dict[str, Any]] = {}
    aggregates: dict[str, Any] = {}
    for variant, selected in sorted(by_variant_rows.items()):
        point = {
            "correctness": _mean(_number(row.get("operational_correctness_score")) for row in selected),
            "tokens": _mean(_number(row.get("modeled_weighted_token_load")) for row in selected),
            "time": _mean(_number(row.get("solve_wall_seconds")) for row in selected),
            "calls": _mean(_number(row.get("execution_calls_started")) for row in selected),
            "warm_time": _mean(_number(row.get("warm_workflow_seconds")) for row in selected),
            "cost": _mean(_number(row.get("estimated_monetary_cost")) for row in selected),
        }
        if all(point[key] is not None for key in ("correctness", "tokens", "time")):
            points[variant] = point
        aggregates[variant] = {
            "variant": variant,
            "matched_run_count": len(selected),
            "issue_count": len({str(row.get("issue_id")) for row in selected}),
            "repetition_count": len({int(row.get("repetition") or 0) for row in selected}),
            "task_success": {"numerator": sum(bool(row.get("task_success")) for row in selected),
                             "denominator": len(selected), "eligibility": "operational_rank_eligible"},
            "mean": point,
            "median": {
                "correctness": _median(_number(row.get("operational_correctness_score")) for row in selected),
                "tokens": _median(_number(row.get("modeled_weighted_token_load")) for row in selected),
                "time": _median(_number(row.get("solve_wall_seconds")) for row in selected),
                "calls": _median(_number(row.get("execution_calls_started")) for row in selected),
                "warm_time": _median(_number(row.get("warm_workflow_seconds")) for row in selected),
            },
            "absolute_quality": {
                "all_tasks_successful": all(bool(row.get("task_success")) for row in selected),
                "any_task_successful": any(bool(row.get("task_success")) for row in selected),
                "correctness_score": point["correctness"],
            },
        }
    baseline_rows = {
        (str(row.get("issue_id")), int(row.get("repetition") or 0)): row
        for row in eligible if row.get("variant") == "baseline-none"
    }
    matched: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        variant = str(row.get("variant"))
        if variant == "baseline-none":
            continue
        block = (str(row.get("issue_id")), int(row.get("repetition") or 0))
        baseline = baseline_rows.get(block)
        if not baseline:
            continue
        relative = _relative(row, baseline, default_tolerance)
        matched[variant].append({
            "issue_id": block[0], "repetition": block[1],
            "treatment": variant, "baseline": "baseline-none",
            "absolute_quality": row["absolute_quality"],
            "relative_to_matched_baseline": relative,
            "timed_out": bool(row.get("timed_out") or baseline.get("timed_out")),
            "infrastructure_retry": bool(row.get("infrastructure_retries") or baseline.get("infrastructure_retries")),
        })
    exact_frontier = _frontier(points)
    baseline_point = points.get("baseline-none")
    comparisons: dict[str, Any] = {}
    rng = random.Random(int(config["bootstrap_seed"]))
    resamples = int(config["bootstrap_resamples"])
    for variant, blocks in sorted(matched.items()):
        treatment_point = points.get(variant)
        if treatment_point is None or baseline_point is None:
            continue
        delta = treatment_point["correctness"] - baseline_point["correctness"]
        token_ratio = treatment_point["tokens"] / baseline_point["tokens"] if baseline_point["tokens"] else None
        time_ratio = treatment_point["time"] / baseline_point["time"] if baseline_point["time"] else None
        call_ratio = (
            treatment_point["calls"] / baseline_point["calls"]
            if baseline_point.get("calls") not in {None, 0.0} and treatment_point.get("calls") is not None else None
        )
        treatment_dominates = _dominates(treatment_point, baseline_point)
        baseline_dominates = _dominates(baseline_point, treatment_point)
        issues: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for block in blocks:
            issues[block["issue_id"]].append(block)
        minimum_repetitions = min((len(items) for items in issues.values()), default=0)
        inferential = minimum_repetitions >= int(policy["analysis"]["minimum_matched_repetitions"])
        bootstrap_samples: list[dict[str, float]] = []
        issue_names = sorted(issues)
        if inferential and issue_names:
            for _ in range(resamples):
                sample_blocks = []
                for issue in (rng.choice(issue_names) for _ in issue_names):
                    candidates = issues[issue]
                    sample_blocks.extend(rng.choice(candidates) for _ in candidates)
                deltas = [block["relative_to_matched_baseline"]["correctness_delta_points"] for block in sample_blocks]
                token_logs = [math.log(block["relative_to_matched_baseline"]["token_ratio"])
                              for block in sample_blocks if block["relative_to_matched_baseline"]["token_ratio"] not in {None, 0.0}]
                time_logs = [math.log(block["relative_to_matched_baseline"]["time_ratio"])
                             for block in sample_blocks if block["relative_to_matched_baseline"]["time_ratio"] not in {None, 0.0}]
                call_logs = [math.log(block["relative_to_matched_baseline"]["call_ratio"])
                             for block in sample_blocks if block["relative_to_matched_baseline"]["call_ratio"] not in {None, 0.0}]
                bootstrap_samples.append({
                    "correctness_delta": statistics.fmean(deltas),
                    "log_token_ratio": statistics.fmean(token_logs) if token_logs else math.nan,
                    "log_time_ratio": statistics.fmean(time_logs) if time_logs else math.nan,
                    "log_call_ratio": statistics.fmean(call_logs) if call_logs else math.nan,
                })
        def interval(field: str) -> dict[str, Any]:
            values = [sample[field] for sample in bootstrap_samples if math.isfinite(sample[field])]
            return {"estimable": bool(values), "lower_95": _percentile(values, 0.025),
                    "median": _percentile(values, 0.5), "upper_95": _percentile(values, 0.975)}
        sensitivity = []
        for tolerance in tolerances:
            acceptable = delta >= -tolerance
            classification = _classify_tradeoff(
                delta, token_ratio, time_ratio, tolerance, baseline_dominates, treatment_dominates
            )
            support = None
            if bootstrap_samples:
                support = {
                    "correctness_tolerance_points": tolerance,
                    "probability_correctness_non_inferior": statistics.fmean(
                        sample["correctness_delta"] >= -tolerance for sample in bootstrap_samples),
                    "probability_tokens_lower": statistics.fmean(
                        sample["log_token_ratio"] < 0 for sample in bootstrap_samples),
                    "probability_time_lower": statistics.fmean(
                        sample["log_time_ratio"] < 0 for sample in bootstrap_samples),
                    "probability_non_inferior_and_tokens_lower": statistics.fmean(
                        sample["correctness_delta"] >= -tolerance and sample["log_token_ratio"] < 0
                        for sample in bootstrap_samples),
                    "probability_non_inferior_and_time_lower": statistics.fmean(
                        sample["correctness_delta"] >= -tolerance and sample["log_time_ratio"] < 0
                        for sample in bootstrap_samples),
                    "probability_strictly_dominates_baseline": statistics.fmean(
                        sample["correctness_delta"] > 0 and sample["log_token_ratio"] < 0
                        and sample["log_time_ratio"] < 0 for sample in bootstrap_samples),
                }
            sensitivity.append({
                "correctness_tolerance_points": tolerance,
                "correctness_acceptable": acceptable,
                "token_savings_percent": _resource_saving(token_ratio),
                "time_savings_percent": _resource_saving(time_ratio),
                "call_savings_percent": _resource_saving(call_ratio),
                "exact_pareto_optimal": variant in exact_frontier,
                "tolerance_aware_pareto_optimal": variant in _tolerance_frontier(
                    points, tolerance
                ),
                "dominates_baseline": treatment_dominates,
                "baseline_dominates": baseline_dominates,
                "classification": classification,
                "inconclusive": not inferential,
                "bootstrap_support": support,
            })
        token_saving = _resource_saving(token_ratio)
        time_saving = _resource_saving(time_ratio)
        call_saving = _resource_saving(call_ratio)
        if token_saving and token_saving > 0 and time_saving and time_saving > 0:
            tradeoff_class = "cheaper_and_faster"
        elif token_saving and token_saving > 0:
            tradeoff_class = "cheaper_but_slower"
        elif time_saving and time_saving > 0:
            tradeoff_class = "faster_but_more_expensive"
        elif baseline_dominates:
            tradeoff_class = "dominated"
        else:
            tradeoff_class = "mixed_tradeoff"
        comparisons[variant] = {
            "variant": variant,
            "matched_block_count": len(blocks),
            "absolute_quality": aggregates[variant]["absolute_quality"],
            "relative_to_matched_baseline": {
                "correctness_delta_points": delta,
                "correctness_relation": "better" if delta > 0 else "equivalent" if delta == 0 else "worse",
                "token_ratio": token_ratio, "time_ratio": time_ratio, "call_ratio": call_ratio,
            },
            "break_even": {
                "correctness_points_gained_or_lost": delta,
                "tokens_saved_percent": token_saving,
                "time_saved_percent": time_saving,
                "calls_saved_percent": call_saving,
                "correctness_points_lost_per_10_percent_token_saving": _loss_per_ten_percent(delta, token_saving),
                "correctness_points_lost_per_10_percent_time_saving": _loss_per_ten_percent(delta, time_saving),
                "minimum_correctness_loss_tolerance_points": max(0.0, -delta),
                "tradeoff_class": tradeoff_class,
            },
            "empirical_sign_counts": {
                "better": sum(block["relative_to_matched_baseline"]["correctness_delta_points"] > 0 for block in blocks),
                "equal": sum(block["relative_to_matched_baseline"]["correctness_delta_points"] == 0 for block in blocks),
                "worse": sum(block["relative_to_matched_baseline"]["correctness_delta_points"] < 0 for block in blocks),
            },
            "paired_intervals": {
                "correctness_delta": interval("correctness_delta"),
                "log_token_ratio": interval("log_token_ratio"),
                "log_time_ratio": interval("log_time_ratio"),
                "log_call_ratio": interval("log_call_ratio"),
            },
            "within_issue_dispersion": {
                issue: {
                    "count": len(items),
                    "correctness_delta_pstdev": statistics.pstdev(
                        [item["relative_to_matched_baseline"]["correctness_delta_points"] for item in items]
                    ) if len(items) > 1 else None,
                } for issue, items in sorted(issues.items())
            },
            "across_issue_heterogeneity": {
                "issue_count": len(issues),
                "limited_issue_clusters": len(issues) < int(config["limited_issue_cluster_threshold"]),
                "issue_correctness_deltas": {
                    issue: statistics.fmean(item["relative_to_matched_baseline"]["correctness_delta_points"] for item in items)
                    for issue, items in sorted(issues.items())
                },
            },
            "issue_sensitivity": {
                issue: statistics.fmean(item["relative_to_matched_baseline"]["correctness_delta_points"] for item in items)
                for issue, items in sorted(issues.items())
            },
            "repetition_sensitivity": {
                str(repetition): statistics.fmean(
                    item["relative_to_matched_baseline"]["correctness_delta_points"]
                    for item in blocks if item["repetition"] == repetition
                ) for repetition in sorted({item["repetition"] for item in blocks})
            },
            "timeout_blocks": sum(block["timed_out"] for block in blocks),
            "infrastructure_retry_blocks": sum(block["infrastructure_retry"] for block in blocks),
            "uncertainty_status": "estimable_limited_issue_clusters" if inferential else "not_estimable_pilot",
            "operational_tradeoff_sensitivity": sensitivity,
        }
    objective_winners = {}
    for label, field, maximize in (
        ("highest_correctness", "correctness", True), ("lowest_modeled_weighted_token_load", "tokens", False),
        ("lowest_solve_time", "time", False), ("fewest_execution_calls", "calls", False),
        ("lowest_estimated_cost", "cost", False), ("lowest_warm_end_to_end_time", "warm_time", False),
    ):
        values = {name: point[field] for name, point in points.items() if point.get(field) is not None}
        if not values:
            objective_winners[label] = []
        else:
            best = max(values.values()) if maximize else min(values.values())
            objective_winners[label] = sorted(name for name, value in values.items() if value == best)
    profiles = {
        name: {
            "maximum_correctness_loss_points": float(tolerance),
            "candidate_treatments": sorted(
                variant for variant, comparison in comparisons.items()
                if comparison["relative_to_matched_baseline"]["correctness_delta_points"] >= -float(tolerance)
                and comparison["break_even"]["tradeoff_class"] != "dominated"
            ),
        } for name, tolerance in config["preference_profiles"].items()
    }
    all_incomplete = bool(aggregates) and all(not item["absolute_quality"]["all_tasks_successful"] for item in aggregates.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "baseline": "baseline-none",
        "correctness_loss_tolerance_grid_points": tolerances,
        "bootstrap": {"method": "issue_then_repetition_matched_hierarchical_resampling",
                      "seed": int(config["bootstrap_seed"]), "resamples": resamples,
                      "probability_label": "bootstrap_support"},
        "absolute_quality": aggregates,
        "matched_comparisons": comparisons,
        "exact_pareto_frontier": exact_frontier,
        "tolerance_aware_pareto_frontiers": {
            f"{tolerance:g}": _tolerance_frontier(points, tolerance)
            for tolerance in tolerances
        },
        "objective_specific_winners": objective_winners,
        "preference_profiles": profiles,
        "decision_summary": {
            "all_implementations_incomplete": all_incomplete,
            "absolute_quality_statement": (
                "No workflow fully solved this issue. Relative comparisons concern incomplete implementations."
                if all_incomplete else "At least one workflow met the configured absolute task-success contract."
            ),
            "preference_independent_overall_winner": None,
            "statistically_supported_winner": None,
            "pilot_only": any(item["uncertainty_status"] == "not_estimable_pilot" for item in comparisons.values()),
        },
    }
