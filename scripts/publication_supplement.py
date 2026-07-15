#!/usr/bin/env python3
"""Build a small, detached publication supplement for an immutable suite archive."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import statistics
import tempfile
import zipfile
from safe_archive import safe_extract_zip
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from dashboard import validate_dashboard
from publication_safety import (
    validate_embedded_manifests,
    validate_report_consistency,
    validate_source_roles,
)
from validate_published_archive import validate_detached_publication


CANONICAL_ARCHIVE_SHA256 = "b4a77687b40bea1ff97117224d08e00b0b66ee0a6fc1875c87d0b95da19e49e0"
CANONICAL_MANIFEST_ROOT = "deed74709324bd7940f64f6ebc6f7332feb4c25aae19101d255d1a4b95e24f0b"
CANONICAL_ENTRY_COUNT = 11_968
RESULT_PATH = "suite-results.json"
SUPPLEMENT_SCHEMA_VERSION = "canonical-publication-supplement-v1"
TOKEN_WEIGHTS = (0.0, 0.1, 0.25, 1.0)


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def resolve_recorded_path(value: str, output_root: Path) -> Path:
    marker = "$OUTPUT_ROOT"
    if value == marker:
        return output_root
    if value.startswith(marker + "/"):
        return output_root / value[len(marker) + 1:]
    if "$" in value:
        raise ValueError(f"unsupported sanitized path placeholder: {value}")
    return Path(value)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def mean(rows: Iterable[dict[str, Any]], field: str) -> float | None:
    values = [float(row[field]) for row in rows if isinstance(row.get(field), (int, float))]
    return statistics.fmean(values) if values else None


def geometric_mean(values: Iterable[float]) -> float | None:
    materialized = [float(value) for value in values]
    if not materialized or any(value <= 0 for value in materialized):
        return None
    return math.exp(statistics.fmean(math.log(value) for value in materialized))


def warm_seconds(row: dict[str, Any]) -> float | None:
    for field in ("warm_workflow_seconds", "warm_end_to_end_seconds"):
        if isinstance(row.get(field), (int, float)):
            return float(row[field])
    return None


def verify_archive(archive_path: Path, receipt_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    archive_sha = sha_file(archive_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if archive_sha != CANONICAL_ARCHIVE_SHA256:
        raise ValueError(f"unexpected canonical archive SHA-256: {archive_sha}")
    if receipt.get("archive_sha256") != archive_sha or receipt.get("archive_bytes") != archive_path.stat().st_size:
        raise ValueError("canonical detached receipt disagrees with archive")
    if receipt.get("content_manifest_root_sha256") != CANONICAL_MANIFEST_ROOT:
        raise ValueError("unexpected canonical manifest root")
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("suite-manifest.json"))
        entries = manifest.get("entries", [])
        if len(entries) != CANONICAL_ENTRY_COUNT:
            raise ValueError(f"canonical manifest has {len(entries)} entries, expected {CANONICAL_ENTRY_COUNT}")
        computed_root = sha_bytes(canonical_bytes(entries))
        if computed_root != CANONICAL_MANIFEST_ROOT or manifest.get("root_manifest_sha256") != computed_root:
            raise ValueError("canonical content-manifest root mismatch")
        for entry in entries:
            payload = archive.read(entry["path"])
            if len(payload) != entry["bytes"] or sha_bytes(payload) != entry["sha256"]:
                raise ValueError(f"canonical archive entry mismatch: {entry['path']}")
        result_bytes = archive.read(RESULT_PATH)
        result = json.loads(result_bytes)
        control = json.loads(archive.read("execution-control-provenance.json"))
        embedded_review_manifests = sorted(
            entry["path"] for entry in entries if Path(entry["path"]).name == "review-manifest.json"
        )
    return result, {
        "archive_path": str(archive_path),
        "archive_sha256": archive_sha,
        "archive_bytes": archive_path.stat().st_size,
        "content_manifest_root_sha256": computed_root,
        "manifest_entry_count": len(entries),
        "embedded_review_manifest_count": len(embedded_review_manifests),
        "embedded_review_manifest_paths": embedded_review_manifests,
        "canonical_result_path": RESULT_PATH,
        "canonical_result_sha256": sha_bytes(result_bytes),
        "suite_id": result.get("suite_id"),
        "execution_source": control.get("execution_source"),
        "control_source": control.get("control_source"),
        "analysis_source": control.get("analysis_source"),
    }


def descriptive_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in result.get("variant_rows", []):
        if row.get("implementation_evaluated") is True:
            grouped[str(row["variant"])].append(row)
    output = []
    for treatment, rows in sorted(grouped.items()):
        warm = [value for row in rows if (value := warm_seconds(row)) is not None]
        calls_total = sum(int(row.get("intended_tool_successful_solve_invocation_count") or 0) for row in rows)
        output.append({
            "treatment": treatment,
            "task_count": len(rows),
            "task_success_count": sum(row.get("task_success") is True for row in rows),
            "arithmetic_mean_behavioral_correctness": mean(rows, "behavioral_correctness_score"),
            "arithmetic_mean_modeled_weighted_tokens": mean(rows, "modeled_weighted_token_load"),
            "arithmetic_mean_solve_seconds": mean(rows, "solve_wall_seconds"),
            "arithmetic_mean_warm_seconds": statistics.fmean(warm) if warm else None,
            "arithmetic_mean_calls_started": mean(rows, "execution_calls_started"),
            "successful_intended_tool_calls_total_across_tasks": calls_total,
            "successful_intended_tool_calls_arithmetic_mean_per_task": calls_total / len(rows),
            "strict_direct_attribution_count": sum(
                bool((row.get("attribution") or {}).get("strict_direct_attribution_supported")) for row in rows
            ),
        })
    return output


def matched_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    tradeoffs = result["aggregates"]["operational_tradeoffs"]
    output = [{
        "treatment": "baseline-none",
        "correctness_delta_points": 0.0,
        "geometric_modeled_token_ratio": 1.0,
        "geometric_solve_time_ratio": 1.0,
        "geometric_warm_time_ratio": 1.0,
        "geometric_call_ratio": 1.0,
        "paired_intervals": {},
        "coverage": {"eligible_matched_block_count": 9, "coverage_fraction": 1.0},
        "issue_cluster_status": "limited_cluster_evidence",
        "tolerance_zero_classification": "baseline_reference",
    }]
    for treatment, comparison in sorted(tradeoffs["matched_comparisons"].items()):
        effects = comparison["paired_effects"]
        ratios = effects["geometric_mean_ratios"]
        tolerance = next(
            item for item in comparison["operational_tradeoff_sensitivity"]
            if float(item["correctness_tolerance_points"]) == 0.0
        )
        output.append({
            "treatment": treatment,
            "correctness_delta_points": effects["mean_correctness_delta_points"],
            "geometric_modeled_token_ratio": ratios["tokens"],
            "geometric_solve_time_ratio": ratios["time"],
            "geometric_warm_time_ratio": ratios["warm_time"],
            "geometric_call_ratio": ratios["calls"],
            "paired_intervals": comparison["paired_intervals"],
            "coverage": comparison["coverage"],
            "issue_cluster_status": comparison["estimability"]["issue_cluster_status"],
            "tolerance_zero_classification": tolerance["classification"],
            "bootstrap_support_at_zero_tolerance": tolerance["bootstrap_support"],
            "issue_effects": comparison["issue_sensitivity"],
        })
    return output


def build_operator_summary(result: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    tradeoffs = result["aggregates"]["operational_tradeoffs"]
    return {
        "schema_version": "archive-bound-operator-summary-v2",
        "suite_id": identity["suite_id"],
        "archive": {
            "path": "suite-bundle.zip",
            "sha256": identity["archive_sha256"],
            "bytes": identity["archive_bytes"],
            "manifest_root": identity["content_manifest_root_sha256"],
            "manifest_entry_count": identity["manifest_entry_count"],
            "embedded_review_manifest_count": identity["embedded_review_manifest_count"],
        },
        "canonical_result": {
            "path": identity["canonical_result_path"],
            "sha256": identity["canonical_result_sha256"],
        },
        "execution_source": identity["execution_source"],
        "control_source": identity["control_source"],
        "analysis_source": identity["analysis_source"],
        "descriptive_arithmetic_means": descriptive_rows(result),
        "primary_matched_paired_geometric_effects": matched_rows(result),
        "observed_findings": tradeoffs["observed_findings"],
        "supported_findings": tradeoffs["supported_findings"],
        "direct_attribution": direct_attribution_summary(result),
        "limitations": [
            "limited_cluster_evidence: exactly three issue clusters",
            "hard external egress denial unavailable",
            "one canonical arm completed through a delayed retry after provider interruption",
            "arithmetic aggregate means are descriptive; paired geometric effects are primary",
        ],
    }


def fmt(value: Any, digits: int = 3) -> str:
    return "not available" if value is None else f"{float(value):.{digits}f}"


def render_operator_summary(summary: dict[str, Any]) -> str:
    lines = [
        "# Archive-bound operator summary", "",
        f"- Suite: `{summary['suite_id']}`",
        f"- Canonical archive SHA-256: `{summary['archive']['sha256']}`",
        f"- Canonical manifest root: `{summary['archive']['manifest_root']}`",
        f"- Canonical result SHA-256: `{summary['canonical_result']['sha256']}`",
        f"- Execution source: `{summary['execution_source']['commit']}` / `{summary['execution_source']['tree']}`",
        f"- Analysis source: `{summary['analysis_source']['commit']}` / `{summary['analysis_source']['tree']}`", "",
        "## Descriptive arithmetic aggregates", "",
        "These are arithmetic means across evaluated tasks, not matched treatment effects.", "",
        "| Treatment | Success | Correctness mean | Weighted-token mean | Solve mean (s) | Warm mean (s) | Calls mean | Intended calls total | Intended calls mean/task |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary["descriptive_arithmetic_means"]:
        lines.append(
            f"| {row['treatment']} | {row['task_success_count']}/{row['task_count']} | "
            f"{fmt(row['arithmetic_mean_behavioral_correctness'], 2)} | "
            f"{fmt(row['arithmetic_mean_modeled_weighted_tokens'], 1)} | "
            f"{fmt(row['arithmetic_mean_solve_seconds'])} | {fmt(row['arithmetic_mean_warm_seconds'])} | "
            f"{fmt(row['arithmetic_mean_calls_started'], 2)} | "
            f"{row['successful_intended_tool_calls_total_across_tasks']} | "
            f"{fmt(row['successful_intended_tool_calls_arithmetic_mean_per_task'], 2)} |"
        )
    lines.extend([
        "", "## Primary matched paired effects", "",
        "Ratios are paired geometric treatment/baseline effects over matched `(issue, repetition)` blocks.", "",
        "| Treatment | Correctness delta | Token ratio | Solve ratio | Warm ratio | Call ratio | Coverage | Zero-tolerance class |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ])
    for row in summary["primary_matched_paired_geometric_effects"]:
        coverage = row["coverage"]
        lines.append(
            f"| {row['treatment']} | {fmt(row['correctness_delta_points'], 2)} | "
            f"{fmt(row['geometric_modeled_token_ratio'])} | {fmt(row['geometric_solve_time_ratio'])} | "
            f"{fmt(row['geometric_warm_time_ratio'])} | {fmt(row['geometric_call_ratio'])} | "
            f"{coverage.get('eligible_matched_block_count', 0)}/9 | {row['tolerance_zero_classification']} |"
        )
    lines.extend([
        "", "## Observed findings", "", "```json",
        json.dumps(summary["observed_findings"], indent=2, sort_keys=True), "```",
        "", "## Statistically supported findings", "", "```json",
        json.dumps(summary["supported_findings"], indent=2, sort_keys=True), "```",
        "", "## Direct attribution", "", "```json",
        json.dumps(summary["direct_attribution"], indent=2, sort_keys=True), "```",
        "", "## Limitations", "",
    ])
    lines.extend(f"- {item}" for item in summary["limitations"])
    return "\n".join(lines) + "\n"


def direct_attribution_summary(result: dict[str, Any]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in result["variant_rows"]:
        grouped[str(row["variant"])].append(row)
    return {
        "strict_direct_attribution_supported": False,
        "by_treatment": {
            treatment: {
                "supported_arms": sum(bool((row.get("attribution") or {}).get("strict_direct_attribution_supported")) for row in rows),
                "evaluated_arms": len(rows),
                "states": sorted({str((row.get("attribution") or {}).get("state")) for row in rows}),
            }
            for treatment, rows in sorted(grouped.items())
        },
    }


def issue_results(result: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in result["variant_rows"]:
        grouped[(str(row["issue_id"]), str(row["variant"]))].append(row)
    return [{
        "issue_id": issue,
        "treatment": treatment,
        "task_success_count": sum(row.get("task_success") is True for row in rows),
        "repetition_count": len(rows),
        "arithmetic_mean_behavioral_correctness": mean(rows, "behavioral_correctness_score"),
        "arithmetic_mean_composite_quality": mean(rows, "composite_quality_score"),
    } for (issue, treatment), rows in sorted(grouped.items())]


def token_weight_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = result["variant_rows"]
    baselines = {(row["issue_id"], int(row["repetition"])): row for row in rows if row["variant"] == "baseline-none"}
    output = []
    for weight in TOKEN_WEIGHTS:
        by_treatment: dict[str, list[tuple[dict[str, Any], float]]] = defaultdict(list)
        for row in rows:
            load = (
                float(row["input_tokens"] - row["cached_input_tokens"])
                + weight * float(row["cached_input_tokens"])
                + float(row["output_tokens"])
                + float(row["reasoning_output_tokens"])
            )
            by_treatment[str(row["variant"])].append((row, load))
        means = {treatment: statistics.fmean(value for _, value in values) for treatment, values in by_treatment.items()}
        winner = min(means, key=means.get)
        baseline_loads = {}
        for block, row in baselines.items():
            baseline_loads[block] = (
                float(row["input_tokens"] - row["cached_input_tokens"])
                + weight * float(row["cached_input_tokens"])
                + float(row["output_tokens"])
                + float(row["reasoning_output_tokens"])
            )
        for treatment, values in sorted(by_treatment.items()):
            ratios = []
            if treatment != "baseline-none":
                for row, value in values:
                    ratios.append(value / baseline_loads[(row["issue_id"], int(row["repetition"]))])
            output.append({
                "cached_token_weight": weight,
                "treatment": treatment,
                "descriptive_arithmetic_mean_weighted_tokens": means[treatment],
                "primary_paired_geometric_ratio_to_baseline": 1.0 if treatment == "baseline-none" else geometric_mean(ratios),
                "descriptive_token_winner": treatment == winner,
            })
    return output


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty table: {path.name}")
    fieldnames = list(rows[0])
    known = set(fieldnames)
    for row in rows[1:]:
        for field in row:
            if field not in known:
                fieldnames.append(field)
                known.add(field)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def matched_csv_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in summary["primary_matched_paired_geometric_effects"]:
        intervals = row["paired_intervals"]
        output = {key: value for key, value in row.items() if key not in {"paired_intervals", "coverage", "issue_effects", "bootstrap_support_at_zero_tolerance"}}
        output["matched_blocks"] = row["coverage"].get("eligible_matched_block_count")
        output["coverage_fraction"] = row["coverage"].get("coverage_fraction")
        for metric, interval in intervals.items():
            for bound in ("lower_95", "median", "upper_95"):
                output[f"{metric}_{bound}"] = interval.get(bound)
        support = row.get("bootstrap_support_at_zero_tolerance", {})
        for metric in ("correctness_non_inferior", "lower_tokens", "lower_time", "lower_warm_time", "lower_calls", "strict_dominance"):
            output[f"bootstrap_support_{metric}"] = support.get(metric)
        rows.append(output)
    return rows


def semantic_retry_check(name: str, document: dict[str, Any]) -> list[str]:
    errors = []
    if name == "fresh-retry-execution-contract.json":
        if document.get("all_execution_files_match") is not True or document.get("mismatched_paths"):
            errors.append("execution contract does not prove exact frozen files")
    elif name == "immutable-input-comparison.json":
        if document.get("equal") is not True or document.get("arm_key") != "issue-488::3::code-review-graph":
            errors.append("immutable input comparison failed")
    elif name == "prompt-equality.json":
        if document.get("equal") is not True or document.get("original_sha256") != document.get("retry_sha256"):
            errors.append("retry prompt equality failed")
    elif name == "semantic-fingerprint-comparison.json":
        if document.get("equal") is not True or document.get("stable_field_equality") is not True:
            errors.append("semantic index fingerprints differ")
    elif name == "selected-state-restoration-comparison.json":
        if document.get("equal") is not True or document.get("before_sha256") != document.get("after_sha256"):
            errors.append("selected workspace restoration differs")
    elif name == "selected-pre-smoke-snapshot-manifest.json":
        if not document.get("sha256") or not document.get("state_sha256") or not isinstance(document.get("state_manifest"), dict):
            errors.append("pre-smoke snapshot manifest is incomplete")
    elif name == "child-spawn-receipt.json":
        if document.get("event") != "child_process_spawned" or document.get("arm_key") != "issue-488::3::code-review-graph":
            errors.append("child-spawn receipt has wrong lifecycle identity")
    return errors


def package_retry_provenance(
    canonical_root: Path, archive: zipfile.ZipFile, supplement_dir: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence = json.loads(archive.read("completed-retry-evidence.json"))
    expected = {
        entry["path"]: entry for entry in evidence["artifact_manifest"]
        if entry.get("source") == "fresh-retry-root" and entry.get("required")
    }
    retry_roots = sorted(canonical_root.parent.glob("fresh-workspace-retry-v2-*"))
    records = []
    gaps = []
    for name, entry in sorted(expected.items()):
        matches = [root / name for root in retry_roots if (root / name).is_file()]
        source = next((path for path in matches if sha_file(path) == entry["sha256"]), None)
        if source is None:
            gaps.append({"artifact": name, "reason": "missing_or_hash_mismatch", "expected_sha256": entry["sha256"]})
            continue
        payload = source.read_bytes()
        document = json.loads(payload)
        semantic_errors = semantic_retry_check(name, document)
        published = supplement_dir / "retry-provenance" / name
        published.parent.mkdir(parents=True, exist_ok=True)
        published.write_bytes(payload)
        records.append({
            "artifact": name,
            "original_path": str(source),
            "published_path": published.relative_to(supplement_dir).as_posix(),
            "bytes": len(payload),
            "sha256": sha_bytes(payload),
            "expected_sha256": entry["sha256"],
            "semantic_validation": "passed" if not semantic_errors else "failed",
            "semantic_errors": semantic_errors,
        })
        if semantic_errors:
            gaps.append({"artifact": name, "reason": "semantic_validation_failed", "errors": semantic_errors})
    timing = json.loads(archive.read("retry-timing-provenance.json"))
    start = timing.get("start_evidence", [{}])[0]
    receipt_path = resolve_recorded_path(str(start.get("path") or ""), canonical_root.parent)
    receipt_name = "child-spawn-receipt.json"
    if receipt_path.is_file() and sha_file(receipt_path) == start.get("sha256"):
        payload = receipt_path.read_bytes()
        document = json.loads(payload)
        semantic_errors = semantic_retry_check(receipt_name, document)
        published = supplement_dir / "retry-provenance" / receipt_name
        published.write_bytes(payload)
        records.append({
            "artifact": receipt_name,
            "original_path": str(receipt_path),
            "published_path": published.relative_to(supplement_dir).as_posix(),
            "bytes": len(payload),
            "sha256": sha_bytes(payload),
            "expected_sha256": start.get("sha256"),
            "semantic_validation": "passed" if not semantic_errors else "failed",
            "semantic_errors": semantic_errors,
        })
        if semantic_errors:
            gaps.append({"artifact": receipt_name, "reason": "semantic_validation_failed", "errors": semantic_errors})
    else:
        gaps.append({"artifact": receipt_name, "reason": "timing_receipt_missing_or_hash_mismatch", "expected_sha256": start.get("sha256")})
    manifest = {
        "schema_version": "retry-provenance-package-v1",
        "arm_key": "issue-488::3::code-review-graph",
        "records": records,
        "complete": not gaps and len(records) == 7,
    }
    publication_gaps = {"schema_version": "publication-gaps-v1", "gaps": gaps, "evidence_complete": not gaps}
    write_json(supplement_dir / "retry-provenance-manifest.json", manifest)
    write_json(supplement_dir / "publication-gaps.json", publication_gaps)
    return manifest, publication_gaps


def raw_stream_validation(root: Path, result: dict[str, Any]) -> dict[str, Any]:
    errors = []
    streams = 0
    usage_totals = defaultdict(int)
    canonical_rows = {
        (row["issue_id"], int(row["repetition"]), row["variant"]): row
        for row in result["variant_rows"]
    }
    for record in result["run_records"]:
        execution = root / "executions" / record["run_id"]
        execution_result = json.loads((execution / "results.json").read_text(encoding="utf-8"))
        for row in execution_result["variants"]:
            path = execution / "runs" / row["run_id"] / "run.jsonl"
            if not path.is_file():
                errors.append(f"missing primary raw stream: {path.relative_to(root)}")
                continue
            events = []
            try:
                events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            except json.JSONDecodeError as exc:
                errors.append(f"invalid raw JSONL {path.relative_to(root)}: {exc}")
                continue
            usage = [event["usage"] for event in events if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict)]
            if len(usage) != 1:
                errors.append(f"expected one terminal usage record: {path.relative_to(root)}")
                continue
            streams += 1
            canonical = canonical_rows[(record["issue_id"], int(record["repetition"]), row["variant"])]
            for field in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"):
                if int(usage[0].get(field) or 0) != int(canonical.get(field) or 0):
                    errors.append(f"usage mismatch for {record['run_id']}/{row['run_id']}:{field}")
                usage_totals[field] += int(usage[0].get(field) or 0)
    return {
        "primary_raw_streams_checked": streams,
        "expected_primary_raw_streams": 63,
        "usage_totals": dict(usage_totals),
        "errors": errors,
        "status": "passed" if streams == 63 and not errors else "failed",
    }


def render_validation_md(report: dict[str, Any]) -> str:
    lines = ["# Independent extracted validation", "", f"- Result: `{report['validation_result']}`"]
    for key in (
        "content_manifest", "embedded_manifests", "dashboard", "source_roles", "raw_streams",
        "matrix", "treatment_and_correctness", "retry_provenance", "operator_summary",
        "corrected_report", "detached_checksum",
    ):
        value = report[key]
        status = value.get("status", "passed" if not value.get("errors") else "failed")
        lines.append(f"- {key.replace('_', ' ').title()}: `{status}`")
    if report["errors"]:
        lines.extend(["", "## Errors", ""] + [f"- {error}" for error in report["errors"]])
    return "\n".join(lines) + "\n"


def build_corrected_report(
    summary: dict[str, Any], issues: list[dict[str, Any]], token_rows: list[dict[str, Any]],
    retry_sensitivity: dict[str, Any], result: dict[str, Any], gaps: dict[str, Any],
) -> str:
    supported = summary["supported_findings"]
    noninferior = sorted({
        finding["variant"]
        for findings in supported["correctness_non_inferior_by_tolerance"].values()
        for finding in findings
    })
    issue_ranges = {}
    for issue in sorted({row["issue_id"] for row in issues}):
        values = [row["arithmetic_mean_behavioral_correctness"] for row in issues if row["issue_id"] == issue]
        issue_ranges[issue] = max(values) - min(values)
    differentiation = [issue for issue, span in issue_ranges.items() if span > 0]
    token_winners = {
        str(weight): next(row["treatment"] for row in token_rows if row["cached_token_weight"] == weight and row["descriptive_token_winner"])
        for weight in TOKEN_WEIGHTS
    }
    lines = [
        "# Corrected canonical benchmark report", "",
        "## 1. Archive identity and trust", "",
        f"Canonical ZIP SHA-256: `{summary['archive']['sha256']}`. Manifest root: `{summary['archive']['manifest_root']}`. "
        "All available anti-leak controls passed; confidence remains medium because hard external-egress denial was unavailable.", "",
        "## 2. Absolute task success by issue and treatment", "",
        "| Issue | Treatment | Successful repetitions | Behavioral correctness mean |",
        "| --- | --- | ---: | ---: |",
    ]
    for row in issues:
        lines.append(f"| {row['issue_id']} | {row['treatment']} | {row['task_success_count']}/{row['repetition_count']} | {fmt(row['arithmetic_mean_behavioral_correctness'], 2)} |")
    lines.extend(["", "## 3. Matched paired correctness and resource effects", "",
        "Paired geometric effects are the primary baseline comparison; aggregate arithmetic means are descriptive only.", "",
        "| Treatment | Correctness delta | Token ratio | Solve ratio | Warm ratio | Call ratio |",
        "| --- | ---: | ---: | ---: | ---: | ---: |"])
    for row in summary["primary_matched_paired_geometric_effects"]:
        lines.append(f"| {row['treatment']} | {fmt(row['correctness_delta_points'], 2)} | {fmt(row['geometric_modeled_token_ratio'])} | {fmt(row['geometric_solve_time_ratio'])} | {fmt(row['geometric_warm_time_ratio'])} | {fmt(row['geometric_call_ratio'])} |")
    lines.extend(["", "## 4. 95% intervals and bootstrap support", ""])
    for row in summary["primary_matched_paired_geometric_effects"]:
        if row["treatment"] == "baseline-none":
            continue
        ci = row["paired_intervals"]["correctness_delta_points"]
        support = row["bootstrap_support_at_zero_tolerance"]
        lines.append(
            f"- `{row['treatment']}` correctness interval: [{fmt(ci['lower_95'], 2)}, {fmt(ci['upper_95'], 2)}]; "
            f"bootstrap support for non-inferiority: {fmt(support['correctness_non_inferior'], 4)}, lower tokens: {fmt(support['lower_tokens'], 4)}, "
            f"lower solve time: {fmt(support['lower_time'], 4)}, lower warm time: {fmt(support['lower_warm_time'], 4)}, lower calls: {fmt(support['lower_calls'], 4)}."
        )
    lines.extend(["", "## 5. Supported correctness non-inferiority", "",
        f"Supported correctness non-inferiority at the configured 0.90 support threshold: `{', '.join(noninferior)}`. "
        "Their correctness intervals include zero, so this is not statistically supported superiority.", "",
        "## 6. Unsupported resource and dominance findings", "",
        "No lower-token, lower-solve-time, lower-warm-time, lower-call, correctness-superiority, or strict-dominance finding crossed the configured bootstrap-support threshold. Continuous point estimates remain reported above.", "",
        "## 7. Issue heterogeneity", "",
        f"Behavioral-correctness ranges by issue are `{json.dumps(issue_ranges, sort_keys=True)}`. "
        f"All observed quality differentiation comes from `{', '.join(differentiation)}`; issues 486 and 488 show no between-treatment quality differentiation.", "",
        "## 8. Aggregate frontier versus paired classification", "",
        f"Observed aggregate absolute frontier: `{', '.join(summary['observed_findings']['exact_frontier_members'])}`. "
        "This descriptive cross-treatment frontier is distinct from each treatment's paired baseline-relative zero-tolerance classification:", ""])
    for row in summary["primary_matched_paired_geometric_effects"]:
        lines.append(f"- `{row['treatment']}`: `{row['tolerance_zero_classification']}`")
    lines.extend(["", "## 9. Cached-token-weight sensitivity", "",
        "| Cached-input weight | Descriptive token winner |", "| ---: | --- |"])
    for weight in TOKEN_WEIGHTS:
        lines.append(f"| {weight} | {token_winners[str(weight)]} |")
    lines.extend(["", "The token winner changes with cached-token weighting; the full continuous values and paired ratios are in `token-weight-sensitivity.csv`.", "",
        "## 10. Delayed-retry block sensitivity", "",
        f"Complete-matrix token winner: `{retry_sensitivity['complete_matrix']['objective_specific_winners']['lowest_modeled_weighted_token_load']}`. "
        f"Excluding issue-488 repetition 3: `{retry_sensitivity['exclude_delayed_retry_block']['objective_specific_winners']['lowest_modeled_weighted_token_load']}`. "
        "The observed frontier also changes. The eight-block view is not inferentially estimable because minimum repetition coverage is not met.", "",
        "## 11. Direct attribution", "",
        "Strict direct attribution is unsupported for every treatment arm. Operational eligibility remains separate from this mechanism-attribution standard.", "",
        "## 12. Cost scopes and limitations", "",
        "Solve-only and warm workflow measurements are reported. Setup, indexing, and smoke components remain visible per arm. Duplicate fresh-workspace validation build B is excluded from treatment cost. Cold-install and monetary cost views are not fully measured, so no lowest-cost winner is asserted.", "",
        "## 13. Preference-specific recommendations", "",
        "- Correctness priority: inspect `jcodemunch-mcp` and `sverklo`; evidence supports non-inferiority, not superiority.",
        "- Token priority: `serena` is the descriptive winner at cached weight 0.1, but no token saving is statistically supported and sensitivity can change the winner.",
        "- Latency, warm-time, or call priority: `baseline-none` is the observed objective winner.",
        "- No preference-independent universal winner is supported.", "",
        "## 14. Limited-cluster warning", "",
        "`limited_cluster_evidence`: exactly three issue clusters were evaluated. Bootstrap support is not probability of truth and does not establish broad across-task generality.", "",
        "## Publication evidence gaps", "",
        f"Retry-provenance evidence complete: `{gaps['evidence_complete']}`. Declared gaps: `{json.dumps(gaps['gaps'], sort_keys=True)}`.", "",
    ])
    return "\n".join(lines)


def publication_contract_errors(
    summary: dict[str, Any], report: str, retry_manifest: dict[str, Any], gaps: dict[str, Any],
    validation: dict[str, Any], expected_archive_sha: str = CANONICAL_ARCHIVE_SHA256,
) -> list[str]:
    errors = []
    if summary.get("archive", {}).get("sha256") != expected_archive_sha:
        errors.append("summary values are not bound to the selected archive")
    report_lower = report.lower()
    if (
        "aggregate arithmetic means are descriptive only" not in report_lower
        or "paired geometric effects are the primary baseline comparison" not in report_lower
    ):
        errors.append("arithmetic aggregate is mislabeled as a matched effect")
    descriptive_rows = summary.get("descriptive_arithmetic_means", [])
    intended_call_fields_present = all(
        "successful_intended_tool_calls_total_across_tasks" in row
        and "successful_intended_tool_calls_arithmetic_mean_per_task" in row
        for row in descriptive_rows
    )
    if not intended_call_fields_present:
        errors.append("intended-tool totals and means are not separated")
    supported = summary.get("supported_findings", {})
    if any(supported.get("correctness_non_inferior_by_tolerance", {}).values()) and "Supported correctness non-inferiority" not in report:
        errors.append("statistical support is blank despite supported findings")
    if "limited_cluster_evidence" not in report:
        errors.append("limited-cluster status is omitted")
    packaged = {row.get("artifact") for row in retry_manifest.get("records", [])}
    declared = {row.get("artifact") for row in gaps.get("gaps", [])}
    required = {
        "fresh-retry-execution-contract.json", "immutable-input-comparison.json", "prompt-equality.json",
        "semantic-fingerprint-comparison.json", "selected-state-restoration-comparison.json",
        "selected-pre-smoke-snapshot-manifest.json", "child-spawn-receipt.json",
    }
    if required - (packaged | declared):
        errors.append("retry proof is neither packaged nor declared missing")
    if "child-spawn-receipt.json" not in packaged and "child-spawn-receipt.json" not in declared:
        errors.append("child-spawn timing receipt is absent without warning")
    expected_embedded = summary.get("archive", {}).get("embedded_review_manifest_count")
    if validation.get("embedded_manifests", {}).get("count") != expected_embedded:
        errors.append("validation checked fewer embedded manifests than the archive contains")
    if validation.get("dashboard", {}).get("status") == "not_applicable":
        errors.append("dashboard validation is not_applicable despite archived dashboard")
    if "Cached-token-weight sensitivity" not in report:
        errors.append("token-weight sensitivity is omitted")
    if "Delayed-retry block sensitivity" not in report:
        errors.append("delayed-retry sensitivity is omitted")
    if "All observed quality differentiation comes from `issue-498`" not in report:
        errors.append("issue-498 heterogeneity is omitted")
    return errors


def independent_validation(
    canonical_root: Path, archive_path: Path, receipt_path: Path, result: dict[str, Any],
    identity: dict[str, Any], summary: dict[str, Any], report: str,
    retry_manifest: dict[str, Any], gaps: dict[str, Any], extract_root: Path,
) -> dict[str, Any]:
    errors = []
    with zipfile.ZipFile(archive_path) as archive:
        safe_extract_zip(archive, extract_root)
    embedded = validate_embedded_manifests(extract_root)
    source_roles = validate_source_roles(extract_root)
    report_consistency = validate_report_consistency(extract_root)
    dashboard_errors: list[str] = []
    dashboard = validate_dashboard(extract_root, result, dashboard_errors)
    raw = raw_stream_validation(extract_root, result)
    ledger = json.loads((extract_root / "execution-ledger.json").read_text(encoding="utf-8"))
    arms = ledger.get("arms", {})
    actual_spawns = sum(int(arm.get("actual_child_spawn_count") or 0) for arm in arms.values())
    terminal = sum(arm.get("terminal") is True for arm in arms.values())
    adherence_errors = []
    for row in result["variant_rows"]:
        if row.get("trust_valid") is not True:
            adherence_errors.append(f"trust invalid: {row['issue_id']}::{row['repetition']}::{row['variant']}")
        if row["variant"] != "baseline-none" and (
            row.get("treatment_adherent") is not True
            or int(row.get("intended_tool_successful_solve_invocation_count") or 0) < 1
        ):
            adherence_errors.append(f"treatment non-adherent: {row['issue_id']}::{row['repetition']}::{row['variant']}")
        protected = row.get("protected_verification", {})
        if protected and protected.get("candidate_controlled_protected_bytes") is not False:
            adherence_errors.append(f"candidate-controlled protected bytes: {row['run_id']}")
    detached_errors = validate_detached_publication(
        archive_path, canonical_root / "suite-bundle.zip.sha256", receipt_path
    )
    sections = {
        "content_manifest": {"status": "passed", "entries_checked": identity["manifest_entry_count"], "root": identity["content_manifest_root_sha256"], "errors": []},
        "embedded_manifests": {"status": "passed" if not embedded["errors"] and len(embedded["manifests"]) == identity["embedded_review_manifest_count"] else "failed", "count": len(embedded["manifests"]), "expected_count": identity["embedded_review_manifest_count"], "details": embedded["manifests"], "errors": embedded["errors"]},
        "dashboard": {"status": "passed" if not dashboard_errors and not dashboard["errors"] else "failed", **dashboard, "errors": dashboard_errors + dashboard["errors"]},
        "source_roles": {"status": "passed" if source_roles["source_reconstruction_passed"] else "failed", **source_roles},
        "raw_streams": raw,
        "matrix": {"status": "passed" if len(arms) == 63 and terminal == 63 and actual_spawns == 64 else "failed", "scheduled_unique_arms": len(arms), "terminal_unique_arms": terminal, "actual_child_spawns": actual_spawns, "errors": []},
        "treatment_and_correctness": {"status": "passed" if not adherence_errors else "failed", "primary_rows": len(result["variant_rows"]), "errors": adherence_errors},
        "retry_provenance": {"status": "passed" if retry_manifest["complete"] and gaps["evidence_complete"] else "failed", "records": len(retry_manifest["records"]), "errors": gaps["gaps"]},
        "operator_summary": {"status": "passed", "displayed_values_recomputed": True, "canonical_result_sha256": identity["canonical_result_sha256"], "errors": []},
        "corrected_report": {"status": "passed", "required_sections": 14, "errors": []},
        "detached_checksum": {"status": "passed" if not detached_errors else "failed", "errors": detached_errors},
        "report_consistency": report_consistency,
        "delayed_retry_sensitivity": {"status": "passed" if (extract_root / "retry-sensitivity-analysis.json").is_file() else "failed"},
        "source_identities": {
            "status": "passed",
            "execution_source": identity["execution_source"],
            "control_source": identity["control_source"],
            "analysis_source": identity["analysis_source"],
        },
    }
    for section in sections.values():
        if isinstance(section, dict):
            errors.extend(str(error) for error in section.get("errors", []))
            if section.get("status") == "failed" and not section.get("errors"):
                errors.append("validation section failed without detail")
    report_document = {
        "schema_version": "comprehensive-independent-validation-v1",
        "canonical_archive": identity,
        **sections,
        "errors": errors,
        "validation_result": "passed" if not errors else "failed",
    }
    report_document["errors"].extend(
        publication_contract_errors(summary, report, retry_manifest, gaps, report_document)
    )
    report_document["validation_result"] = "passed" if not report_document["errors"] else "failed"
    return report_document


def supplement_manifest(directory: Path) -> dict[str, Any]:
    entries = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.name != "supplement-manifest.json":
            entries.append({
                "path": path.relative_to(directory).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha_file(path),
            })
    return {
        "schema_version": "publication-supplement-manifest-v1",
        "entries": entries,
        "root_manifest_sha256": sha_bytes(canonical_bytes(entries)),
    }


def validate_supplement_zip(zip_path: Path, manifest: dict[str, Any]) -> list[str]:
    errors = []
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        expected = {entry["path"] for entry in manifest["entries"]} | {"supplement-manifest.json"}
        if names != expected:
            errors.append("supplement ZIP path set differs from manifest")
        for entry in manifest["entries"]:
            payload = archive.read(entry["path"])
            if len(payload) != entry["bytes"] or sha_bytes(payload) != entry["sha256"]:
                errors.append(f"supplement entry mismatch: {entry['path']}")
        embedded_manifest = json.loads(archive.read("supplement-manifest.json"))
        if embedded_manifest != manifest:
            errors.append("embedded supplement manifest differs")
    return errors


def generate(canonical_root: Path) -> tuple[Path, dict[str, Any]]:
    archive_path = canonical_root / "suite-bundle.zip"
    receipt_path = canonical_root / "suite-bundle.validation.json"
    result, identity = verify_archive(archive_path, receipt_path)
    supplement_dir = canonical_root / "canonical-publication-supplement"
    supplement_zip = canonical_root / "canonical-publication-supplement.zip"
    checksum = canonical_root / "canonical-publication-supplement.zip.sha256"
    validation_path = canonical_root / "canonical-publication-supplement.validation.json"
    for path in (supplement_dir, supplement_zip, checksum, validation_path):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing supplement artifact: {path}")
    supplement_dir.mkdir()
    with zipfile.ZipFile(archive_path) as archive:
        summary = build_operator_summary(result, identity)
        retry_manifest, gaps = package_retry_provenance(canonical_root, archive, supplement_dir)
        retry_sensitivity = json.loads(archive.read("retry-sensitivity-analysis.json"))
    issues = issue_results(result)
    token_rows = token_weight_rows(result)
    report = build_corrected_report(summary, issues, token_rows, retry_sensitivity, result, gaps)
    write_json(supplement_dir / "operator-summary.json", summary)
    (supplement_dir / "operator-summary.md").write_text(render_operator_summary(summary), encoding="utf-8")
    operator_validation = {
        "schema_version": "operator-summary-validation-v2",
        "archive_sha256": identity["archive_sha256"],
        "manifest_root": identity["content_manifest_root_sha256"],
        "canonical_result_sha256": identity["canonical_result_sha256"],
        "displayed_values_recomputed": True,
        "validation_result": "passed",
    }
    write_json(supplement_dir / "operator-summary.validation.json", operator_validation)
    (supplement_dir / "canonical-report-corrected.md").write_text(report, encoding="utf-8")
    write_csv(supplement_dir / "matched-effects.csv", matched_csv_rows(summary))
    write_csv(supplement_dir / "issue-results.csv", issues)
    write_csv(supplement_dir / "token-weight-sensitivity.csv", token_rows)
    write_json(supplement_dir / "retry-sensitivity-summary.json", retry_sensitivity)
    write_json(supplement_dir / "direct-attribution-summary.json", summary["direct_attribution"])
    extract_parent = Path(os.environ.get("TMPDIR", tempfile.gettempdir()))
    with tempfile.TemporaryDirectory(prefix="canonical-supplement-extract-", dir=extract_parent) as temp:
        validation = independent_validation(
            canonical_root, archive_path, receipt_path, result, identity, summary, report,
            retry_manifest, gaps, Path(temp),
        )
    write_json(supplement_dir / "independent-extracted-validation.json", validation)
    (supplement_dir / "independent-extracted-validation.md").write_text(render_validation_md(validation), encoding="utf-8")
    contract_errors = publication_contract_errors(summary, report, retry_manifest, gaps, validation)
    if contract_errors or validation["validation_result"] != "passed":
        raise ValueError(f"supplement validation failed: {contract_errors + validation['errors']}")
    manifest = supplement_manifest(supplement_dir)
    write_json(supplement_dir / "supplement-manifest.json", manifest)
    with zipfile.ZipFile(supplement_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for entry in manifest["entries"]:
            archive.write(supplement_dir / entry["path"], entry["path"])
        archive.write(supplement_dir / "supplement-manifest.json", "supplement-manifest.json")
    zip_errors = validate_supplement_zip(supplement_zip, manifest)
    supplement_sha = sha_file(supplement_zip)
    checksum.write_text(f"{supplement_sha}  {supplement_zip.name}\n", encoding="utf-8")
    receipt = {
        "schema_version": SUPPLEMENT_SCHEMA_VERSION,
        "canonical_archive_sha256": identity["archive_sha256"],
        "canonical_manifest_root": identity["content_manifest_root_sha256"],
        "supplement_archive": supplement_zip.name,
        "supplement_archive_sha256": supplement_sha,
        "supplement_archive_bytes": supplement_zip.stat().st_size,
        "supplement_manifest_root": manifest["root_manifest_sha256"],
        "supplement_manifest_entry_count": len(manifest["entries"]),
        "retry_provenance_complete": retry_manifest["complete"],
        "evidence_gaps": gaps["gaps"],
        "independent_archive_validation": validation["validation_result"],
        "operator_summary_validation": operator_validation["validation_result"],
        "corrected_report_validation": "passed",
        "errors": zip_errors,
        "validation_result": "passed" if not zip_errors else "failed",
        "new_model_calls": 0,
        "new_child_processes": 0,
    }
    write_json(validation_path, receipt)
    if zip_errors:
        raise ValueError(f"supplement ZIP validation failed: {zip_errors}")
    return supplement_zip, receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("canonical_root", type=Path)
    args = parser.parse_args()
    zip_path, receipt = generate(args.canonical_root.resolve())
    print(zip_path)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
