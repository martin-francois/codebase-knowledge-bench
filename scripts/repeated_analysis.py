#!/usr/bin/env python3
"""Deterministic matched repeated-treatment inference."""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from typing import Any, Iterable


SCHEMA_VERSION = "repeated-analysis-v1"
DEFAULT_SEED = 20260713
DEFAULT_RESAMPLES = 10_000
TOKEN_WEIGHTS = (0.0, 0.1, 0.25, 1.0)

METRICS = (
    "task_success",
    "issue_contract_pass_fraction",
    "common_regression_pass_fraction",
    "operational_correctness_score",
    "input_tokens",
    "cached_input_tokens",
    "non_cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "modeled_weighted_token_load",
    "solve_wall_seconds",
    "warm_workflow_seconds",
    "setup_seconds",
    "index_seconds",
    "smoke_seconds",
    "execution_calls_started",
    "execution_calls_completed",
    "execution_calls_successful",
    "execution_calls_failed",
    "execution_calls_cancelled",
    "execution_calls_unfinished",
    "intended_tool_successful_calls",
    "intended_tool_failed_calls",
    "intended_tool_unfinished_calls",
    "any_native_search_command_count",
    "native_file_read_count",
    "unique_native_files_opened",
    "native_context_bytes",
    "estimated_native_context_tokens",
    "tool_context_bytes_total",
    "tool_context_estimated_tokens_total",
    "timeouts",
    "infrastructure_retries",
)


def _number(row: dict[str, Any], metric: str) -> float | None:
    value = row.get(metric)
    if metric == "task_success":
        return float(bool(value))
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "minimum": None, "maximum": None,
                "population_standard_deviation": None, "population_variance": None}
    return {
        "count": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "minimum": min(values),
        "maximum": max(values),
        "population_standard_deviation": statistics.pstdev(values) if len(values) > 1 else None,
        "population_variance": statistics.pvariance(values) if len(values) > 1 else None,
    }


def _percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _binomial_two_sided(discordant: int, wins: int) -> float | None:
    if discordant == 0:
        return None
    tail = sum(math.comb(discordant, k) for k in range(0, min(wins, discordant - wins) + 1)) / (2 ** discordant)
    return min(1.0, 2.0 * tail)


def _eligible(row: dict[str, Any]) -> bool:
    return bool(row.get("operational_rank_eligible")) and bool(row.get("trust_valid"))


def _token_load(row: dict[str, Any], weight: float) -> float | None:
    fields = ("non_cached_input_tokens", "output_tokens", "reasoning_output_tokens", "cached_input_tokens")
    if any(_number(row, field) is None for field in fields):
        return None
    return sum(float(row[field]) for field in fields[:3]) + weight * float(row[fields[3]])


def _pareto(points: dict[str, tuple[float, float, float, bool]]) -> set[str]:
    frontier: set[str] = set()
    for name, point in points.items():
        correctness, tokens, seconds, viable = point
        dominated = False
        for other_name, other in points.items():
            if other_name == name:
                continue
            oc, ot, os, ov = other
            if ov and not viable:
                dominated = True
                break
            if ov == viable and oc >= correctness and ot <= tokens and os <= seconds and (
                oc > correctness or ot < tokens or os < seconds
            ):
                dominated = True
                break
        if not dominated:
            frontier.add(name)
    return frontier


def analyze_repeated(
    rows: Iterable[dict[str, Any]],
    policy: dict[str, Any],
    *,
    seed: int = DEFAULT_SEED,
    resamples: int = DEFAULT_RESAMPLES,
) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: (str(row.get("issue_id")), int(row.get("repetition", 0)), str(row.get("variant"))))
    by_block: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in ordered:
        if _eligible(row):
            by_block[(str(row["issue_id"]), int(row.get("repetition", 1)))][str(row["variant"])] = row
    treatments = sorted({name for block in by_block.values() for name in block if name != "baseline-none"})
    minimum_repetitions = int(policy.get("minimum_repetitions_for_inference", 3))
    comparison_policy = policy.get("operational_comparison", policy.get("matched_comparison", policy))
    output: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "seed": seed,
        "resample_count": resamples,
        "minimum_repetitions_for_inference": minimum_repetitions,
        "treatments": {},
        "analysis_mode": "repeated_inference",
        "statistically_supported_operational_winner": None,
        "outcome": "inconclusive",
    }
    rng = random.Random(seed)
    treatment_bootstrap: dict[str, list[dict[str, float]]] = {}
    for treatment in treatments:
        blocks = []
        for (issue, repetition), members in sorted(by_block.items()):
            if treatment not in members or "baseline-none" not in members:
                continue
            treatment_row, baseline = members[treatment], members["baseline-none"]
            metrics: dict[str, Any] = {}
            for metric in METRICS:
                tv, bv = _number(treatment_row, metric), _number(baseline, metric)
                metrics[metric] = {
                    "treatment": tv,
                    "baseline": bv,
                    "delta": None if tv is None or bv is None else tv - bv,
                    "ratio": None if tv is None or bv in {None, 0.0} else tv / bv,
                }
            blocks.append({"issue_id": issue, "repetition": repetition, "metrics": metrics,
                           "timed_out": bool(treatment_row.get("timed_out") or baseline.get("timed_out"))})
        issue_blocks: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for block in blocks:
            issue_blocks[block["issue_id"]].append(block)
        minimum_cell = min((len(items) for items in issue_blocks.values()), default=0)
        if minimum_cell < minimum_repetitions:
            output["analysis_mode"] = "pilot_only"
        within_issue: dict[str, Any] = {}
        for issue, items in sorted(issue_blocks.items()):
            within_issue[issue] = {
                metric: _summary([item["metrics"][metric]["delta"] for item in items if item["metrics"][metric]["delta"] is not None])
                for metric in METRICS
            }
        heterogeneity: dict[str, Any] = {}
        for metric in METRICS:
            issue_means = [
                statistics.fmean(values)
                for issue in sorted(within_issue)
                if (values := [item["metrics"][metric]["delta"] for item in issue_blocks[issue]
                               if item["metrics"][metric]["delta"] is not None])
            ]
            heterogeneity[metric] = {"issue_effects": issue_means, **_summary(issue_means)}
        bootstrap: list[dict[str, float]] = []
        issues = sorted(issue_blocks)
        if issues:
            for _ in range(resamples):
                sampled: list[dict[str, Any]] = []
                for issue in (rng.choice(issues) for _ in issues):
                    candidates = issue_blocks[issue]
                    sampled.extend(rng.choice(candidates) for _ in candidates)
                record: dict[str, float] = {}
                for metric in ("operational_correctness_score", "modeled_weighted_token_load", "solve_wall_seconds"):
                    values = [item["metrics"][metric]["delta"] for item in sampled if item["metrics"][metric]["delta"] is not None]
                    if values:
                        record[metric] = statistics.fmean(values)
                bootstrap.append(record)
        treatment_bootstrap[treatment] = bootstrap
        intervals = {
            metric: {
                "lower_95": _percentile([sample[metric] for sample in bootstrap if metric in sample], 0.025),
                "upper_95": _percentile([sample[metric] for sample in bootstrap if metric in sample], 0.975),
            }
            for metric in ("operational_correctness_score", "modeled_weighted_token_load", "solve_wall_seconds")
        }
        task_pairs = [(int(item["metrics"]["task_success"]["treatment"] or 0),
                       int(item["metrics"]["task_success"]["baseline"] or 0)) for item in blocks]
        wins = sum(t > b for t, b in task_pairs)
        losses = sum(t < b for t, b in task_pairs)
        correctness_deltas = [item["metrics"]["operational_correctness_score"]["delta"] for item in blocks
                              if item["metrics"]["operational_correctness_score"]["delta"] is not None]
        standardized = None
        standardized_reason = None
        if len(correctness_deltas) < 2:
            standardized_reason = "fewer_than_two_matched_blocks"
        elif statistics.pstdev(correctness_deltas) == 0:
            standardized_reason = "zero_delta_variance"
        else:
            standardized = statistics.fmean(correctness_deltas) / statistics.pstdev(correctness_deltas)
        margin = float(comparison_policy.get("correctness_equivalence_margin_points", 2.0))
        correctness_interval = intervals["operational_correctness_score"]
        equivalent = correctness_interval["lower_95"] is not None and correctness_interval["lower_95"] >= -margin
        viable_pairs = [item for item in blocks if item["metrics"]["task_success"]["treatment"] == 1.0
                        and item["metrics"]["task_success"]["baseline"] == 1.0]
        sensitivity = {}
        for weight in TOKEN_WEIGHTS:
            deltas = []
            for item in blocks:
                treatment_row = by_block[(item["issue_id"], item["repetition"])][treatment]
                baseline_row = by_block[(item["issue_id"], item["repetition"])]["baseline-none"]
                tv, bv = _token_load(treatment_row, weight), _token_load(baseline_row, weight)
                if tv is not None and bv is not None:
                    deltas.append(tv - bv)
            sensitivity[str(weight)] = _summary(deltas)
        output["treatments"][treatment] = {
            "matched_blocks": blocks,
            "matched_block_count": len(blocks),
            "minimum_repetitions_per_issue": minimum_cell,
            "within_issue": within_issue,
            "across_issue_heterogeneity": heterogeneity,
            "hierarchical_bootstrap_intervals": intervals,
            "paired_task_success": {"wins": wins, "losses": losses, "ties": len(task_pairs) - wins - losses,
                                    "exact_two_sided_p": _binomial_two_sided(wins + losses, wins)},
            "raw_effects": {metric: _summary([item["metrics"][metric]["delta"] for item in blocks
                                               if item["metrics"][metric]["delta"] is not None]) for metric in METRICS},
            "standardized_correctness_effect": standardized,
            "standardized_effect_unavailable_reason": standardized_reason,
            "correctness_noninferior_within_margin": equivalent,
            "viable_matched_block_count": len(viable_pairs),
            "cached_token_weight_sensitivity": sensitivity,
            "timeout_sensitivity": {
                "with_timeouts": len(blocks),
                "without_timeouts": sum(not item["timed_out"] for item in blocks),
            },
            "robust_outlier_analysis": {
                "method": "median_delta_no_observations_deleted",
                "correctness_median_delta": statistics.median(correctness_deltas) if correctness_deltas else None,
            },
            "inference": "inconclusive",
        }
    # Joint resampling uses identical sample indexes, preserving matched treatment comparisons.
    variants = ["baseline-none", *treatments]
    rank_counts = {name: 0 for name in treatments}
    frontier_counts = {name: 0 for name in variants}
    if treatments:
        samples = min((len(treatment_bootstrap[name]) for name in treatments), default=0)
        for index in range(samples):
            effects = {name: treatment_bootstrap[name][index] for name in treatments}
            ordered_names = sorted(treatments, key=lambda name: (
                -effects[name].get("operational_correctness_score", -math.inf),
                effects[name].get("modeled_weighted_token_load", math.inf),
                effects[name].get("solve_wall_seconds", math.inf), name))
            if ordered_names:
                rank_counts[ordered_names[0]] += 1
            points = {"baseline-none": (0.0, 0.0, 0.0, True)}
            for name in treatments:
                sample = effects[name]
                points[name] = (sample.get("operational_correctness_score", -math.inf),
                                sample.get("modeled_weighted_token_load", math.inf),
                                sample.get("solve_wall_seconds", math.inf),
                                output["treatments"][name]["viable_matched_block_count"] > 0)
            for name in _pareto(points):
                frontier_counts[name] += 1
        for name in treatments:
            output["treatments"][name]["rank_stability_probability"] = rank_counts[name] / samples if samples else None
            output["treatments"][name]["pareto_frontier_probability"] = frontier_counts[name] / samples if samples else None
        output["baseline_pareto_frontier_probability"] = frontier_counts["baseline-none"] / samples if samples else None
    output["tie_bands"] = [sorted(treatments)] if treatments else []
    if output["analysis_mode"] == "pilot_only":
        output["outcome"] = "pilot_only_inconclusive"
    else:
        supported: list[str] = []
        material = float(comparison_policy.get("correctness_material_improvement_points", 5.0))
        token_threshold = 1.0 - float(comparison_policy.get("minimum_practical_token_reduction_fraction", 0.1))
        time_threshold = 1.0 - float(comparison_policy.get("minimum_practical_time_reduction_fraction", 0.1))
        stability_threshold = float(policy.get("repeated_analysis", {}).get("rank_stability_threshold", 0.8))
        for name, record in output["treatments"].items():
            ci = record["hierarchical_bootstrap_intervals"]["operational_correctness_score"]
            blocks = record["matched_blocks"]
            token_ratios = [block["metrics"]["modeled_weighted_token_load"]["ratio"] for block in blocks
                            if block["metrics"]["modeled_weighted_token_load"]["ratio"] is not None]
            time_ratios = [block["metrics"]["solve_wall_seconds"]["ratio"] for block in blocks
                           if block["metrics"]["solve_wall_seconds"]["ratio"] is not None]
            material_better = ci["lower_95"] is not None and ci["lower_95"] >= material
            practical_efficiency = (
                bool(token_ratios) and statistics.fmean(token_ratios) <= token_threshold
            ) or (
                bool(time_ratios) and statistics.fmean(time_ratios) <= time_threshold
            )
            viability_complete = record["viable_matched_block_count"] == record["matched_block_count"] > 0
            stable = (record.get("rank_stability_probability") or 0.0) >= stability_threshold
            if viability_complete and stable and (material_better or (
                record["correctness_noninferior_within_margin"] and practical_efficiency
            )):
                supported.append(name)
        if len(supported) == 1:
            output["statistically_supported_operational_winner"] = supported[0]
            output["outcome"] = "supported_operational_benefit"
            output["tie_bands"] = [[supported[0]], sorted(name for name in treatments if name != supported[0])]
    return output
