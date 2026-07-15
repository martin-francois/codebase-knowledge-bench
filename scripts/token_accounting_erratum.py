#!/usr/bin/env python3
"""Archive-bound token-accounting-v2 sensitivity for immutable historical results."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from future_methodology import CACHE_WEIGHTS, derive_token_usage, modeled_token_load

CANONICAL_SHA256 = "b4a77687b40bea1ff97117224d08e00b0b66ee0a6fc1875c87d0b95da19e49e0"
LEGACY_FIELD = "legacy_modeled_weighted_token_load_v1_reasoning_double_counted"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def geometric_mean(values: list[float]) -> float:
    if not values or any(value <= 0 for value in values):
        raise ValueError("geometric means require positive values")
    return math.exp(statistics.fmean(math.log(value) for value in values))


def _frontier(rows: list[dict[str, Any]], load_field: str) -> list[str]:
    aggregate = {}
    for treatment in sorted({row["treatment"] for row in rows}):
        selected = [row for row in rows if row["treatment"] == treatment]
        aggregate[treatment] = (
            statistics.fmean(row["behavioral_correctness_score"] for row in selected),
            statistics.fmean(row[load_field] for row in selected),
            statistics.fmean(row["solve_wall_seconds"] for row in selected),
        )
    frontier = []
    for candidate, point in aggregate.items():
        dominated = any(
            other != candidate
            and aggregate[other][0] >= point[0]
            and aggregate[other][1] <= point[1]
            and aggregate[other][2] <= point[2]
            and aggregate[other] != point
            for other in aggregate
        )
        if not dominated:
            frontier.append(candidate)
    return sorted(frontier)


def build_erratum(archive_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    archive_sha = sha256_file(archive_path)
    if archive_sha != CANONICAL_SHA256:
        raise ValueError("canonical archive SHA-256 mismatch")
    with zipfile.ZipFile(archive_path) as archive:
        payload = archive.read("suite-results.json")
        result = json.loads(payload)
    canonical_result_sha = hashlib.sha256(payload).hexdigest()
    corrected_rows = []
    for row in sorted(result["variant_rows"], key=lambda item: (item["issue_id"], item["repetition"], item["variant"])):
        usage = derive_token_usage(row)
        corrected = {str(weight): modeled_token_load(usage, weight) for weight in CACHE_WEIGHTS}
        legacy = float(row["modeled_weighted_token_load"])
        expected_legacy = corrected["0.1"] + float(row["reasoning_output_tokens"])
        if not math.isclose(legacy, expected_legacy, rel_tol=0, abs_tol=1e-6):
            raise ValueError(f"historical load formula mismatch: {row['issue_id']}::{row['repetition']}::{row['variant']}")
        corrected_rows.append({
            "arm_key": f"{row['issue_id']}::{row['repetition']}::{row['variant']}",
            "issue_id": row["issue_id"], "repetition": row["repetition"], "treatment": row["variant"],
            "input_tokens": row["input_tokens"], "cached_input_tokens": row["cached_input_tokens"],
            "observed_non_cached_input_tokens": usage["observed_non_cached_input_tokens"],
            "output_tokens_including_reasoning": row["output_tokens"],
            "reasoning_output_tokens": row["reasoning_output_tokens"],
            "non_reasoning_output_tokens_observed": usage["non_reasoning_output_tokens_observed"],
            LEGACY_FIELD: legacy,
            "corrected_modeled_weighted_token_load_v2_w0": corrected["0.0"],
            "corrected_modeled_weighted_token_load_v2_w0_1": corrected["0.1"],
            "corrected_modeled_weighted_token_load_v2_w0_25": corrected["0.25"],
            "corrected_modeled_weighted_token_load_v2_w1": corrected["1.0"],
            "behavioral_correctness_score": row["behavioral_correctness_score"],
            "solve_wall_seconds": row["solve_wall_seconds"],
        })
    by_treatment: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in corrected_rows:
        by_treatment[row["treatment"]].append(row)
    baseline = {(row["issue_id"], row["repetition"]): row for row in corrected_rows if row["treatment"] == "baseline-none"}
    summaries = []
    winner_by_weight = {}
    for weight, field in ((0.0, "corrected_modeled_weighted_token_load_v2_w0"), (0.1, "corrected_modeled_weighted_token_load_v2_w0_1"), (0.25, "corrected_modeled_weighted_token_load_v2_w0_25"), (1.0, "corrected_modeled_weighted_token_load_v2_w1")):
        means = {name: statistics.fmean(row[field] for row in rows) for name, rows in by_treatment.items()}
        winner_by_weight[str(weight)] = min(means, key=means.get)
    for treatment, rows in sorted(by_treatment.items()):
        ratios = [] if treatment == "baseline-none" else [
            row["corrected_modeled_weighted_token_load_v2_w0_1"] / baseline[(row["issue_id"], row["repetition"])]["corrected_modeled_weighted_token_load_v2_w0_1"]
            for row in rows
        ]
        summaries.append({
            "treatment": treatment,
            "legacy_arithmetic_mean_w0_1": statistics.fmean(row[LEGACY_FIELD] for row in rows),
            "corrected_arithmetic_mean_w0_1": statistics.fmean(row["corrected_modeled_weighted_token_load_v2_w0_1"] for row in rows),
            "corrected_paired_geometric_ratio_w0_1": None if not ratios else geometric_mean(ratios),
            "corrected_paired_percent_change_w0_1": None if not ratios else 100 * (geometric_mean(ratios) - 1),
        })
    old_means = {name: statistics.fmean(row[LEGACY_FIELD] for row in rows) for name, rows in by_treatment.items()}
    old_winner = min(old_means, key=old_means.get)
    report = {
        "schema_version": "token-accounting-erratum-v1",
        "canonical_archive_sha256": archive_sha,
        "canonical_suite_results_sha256": canonical_result_sha,
        "historical_methodology_rewritten": False,
        "legacy_metric_field": LEGACY_FIELD,
        "legacy_formula": "observed_non_cached_input + 0.1*cached_input + output_including_reasoning + reasoning_output",
        "corrected_methodology_version": "token-accounting-v2",
        "corrected_formula": "observed_non_cached_input + cache_weight*cached_input + output_including_reasoning",
        "row_count": len(corrected_rows),
        "aggregate_summaries": summaries,
        "corrected_mean_order_w0_1": [item["treatment"] for item in sorted(summaries, key=lambda item: item["corrected_arithmetic_mean_w0_1"])],
        "token_winner_by_cache_weight": winner_by_weight,
        "legacy_token_winner_w0_1": old_winner,
        "corrected_token_winner_w0_1": winner_by_weight["0.1"],
        "token_objective_recommendation_changed": old_winner != winner_by_weight["0.1"],
        "legacy_frontier_w0_1": _frontier(corrected_rows, LEGACY_FIELD),
        "corrected_frontier_w0_1": _frontier(corrected_rows, "corrected_modeled_weighted_token_load_v2_w0_1"),
        "limitations": [
            "cache-write telemetry is unavailable in the canonical Codex JSONL",
            "turn-aggregate cache telemetry cannot identify cross-arm cache reuse",
            "the immutable canonical archive retains the legacy v1 field",
        ],
    }
    expected_order = ["serena", "graphify", "code-review-graph", "baseline-none", "sverklo", "jcodemunch-mcp", "gitnexus"]
    if report["corrected_mean_order_w0_1"] != expected_order:
        raise ValueError("corrected canonical treatment order does not match the independently reviewed acceptance order")
    return report, corrected_rows


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Token-accounting erratum", "",
        f"- Canonical archive: `{report['canonical_archive_sha256']}`",
        "- Historical results rewritten: `false`",
        f"- Legacy field: `{report['legacy_metric_field']}`",
        "- Corrected version: `token-accounting-v2`", "",
        "Reasoning tokens are a subset of output tokens. The historical v1 resource field added them twice; v2 does not.", "",
        "## Corrected cache-weight 0.1 effects", "", "| Treatment | Arithmetic mean | Paired geometric change |", "| --- | ---: | ---: |",
    ]
    for row in report["aggregate_summaries"]:
        change = "baseline" if row["corrected_paired_percent_change_w0_1"] is None else f"{row['corrected_paired_percent_change_w0_1']:+.6f}%"
        lines.append(f"| {row['treatment']} | {row['corrected_arithmetic_mean_w0_1']:.6f} | {change} |")
    lines.extend(["", f"Token-objective recommendation changed: `{str(report['token_objective_recommendation_changed']).lower()}`.", "",
                  "Cache correlations remain descriptive because turn aggregates cannot identify cross-arm reuse.", ""])
    return "\n".join(lines)


def write_outputs(archive: Path, output: Path) -> dict[str, Any]:
    report, rows = build_erratum(archive)
    output.mkdir(parents=True, exist_ok=True)
    (output / "token-accounting-erratum.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "token-accounting-erratum.md").write_text(render_markdown(report), encoding="utf-8")
    with (output / "token-accounting-corrected-effects.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("canonical_archive", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    write_outputs(args.canonical_archive, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
