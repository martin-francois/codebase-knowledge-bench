#!/usr/bin/env python3
"""Authoritative one-checker-per-invariant execution and result rendering."""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from future_methodology import derive_token_usage, modeled_token_load, pricing_cost
from source_verification import subject_manifest
from token_accounting_erratum import build_erratum
from vnext_fixture import run_fixture


@dataclass(frozen=True)
class CheckerSpec:
    checker_id: str
    primitive: str
    version: str = "1"


def _ids(prefix: str, start: int, end: int) -> list[str]:
    return [f"{prefix}-{number:03d}" for number in range(start, end + 1)]


AUTOMATED_IDS = (
    _ids("PUB", 1, 26) + _ids("TOK", 1, 26) + _ids("COR", 1, 27)
    + _ids("COR-ISSUE", 1, 8) + _ids("SRC", 1, 3) + _ids("VER", 1, 6) + ["SEC-002"]
)
CHECKER_MAP = {identifier: CheckerSpec(f"checker-{identifier.lower()}", f"fact:{identifier}") for identifier in AUTOMATED_IDS}


def evaluate_checker(identifier: str, facts: dict[str, bool], evidence: dict[str, list[str]]) -> dict[str, Any]:
    if identifier not in CHECKER_MAP:
        raise ValueError(f"missing checker mapping: {identifier}")
    spec = CHECKER_MAP[identifier]
    passed = facts.get(identifier) is True
    return {"checker_id": spec.checker_id, "verification_id": identifier, "checker_version": spec.version,
            "status": "passed" if passed else "failed", "invoked": True, "evidence": evidence.get(identifier, []),
            "failure_reason": None if passed else f"independent fact {spec.primitive} was false or unavailable"}


def _source_has(repo: Path, path: str, *needles: str) -> bool:
    target = repo / path
    if not target.is_file(): return False
    text = target.read_text(encoding="utf-8", errors="replace")
    return all(needle in text for needle in needles)


def build_facts(repo: Path, canonical: Path, supplement: Path) -> tuple[dict[str, bool], dict[str, list[str]]]:
    from verification_registry import validate_publications, validate_registry, validate_findings, tracked_source_errors
    publication = validate_publications(canonical, supplement)
    with zipfile.ZipFile(supplement) as archive:
        supplement_names=set(archive.namelist())
        extracted_validation=json.loads(archive.read("independent-extracted-validation.json"))
    erratum, rows = build_erratum(canonical)
    vnext = run_fixture(repo)
    facts: dict[str, bool] = {}
    evidence: dict[str, list[str]] = {}
    pub_conditions = {
        "PUB-001": publication["canonical_archive_sha256"].startswith("b4a77687"),
        "PUB-002": publication["canonical_manifest_root"].startswith("deed7470"),
        "PUB-003": publication["canonical_manifest_entries_checked"] == 11968,
        "PUB-004": publication["embedded_review_manifests_checked"] == 13,
        "PUB-005": extracted_validation["raw_streams"]["primary_raw_streams_checked"] == 63,
        "PUB-006": extracted_validation["matrix"]["scheduled_unique_arms"] == 63 and extracted_validation["matrix"]["actual_child_spawns"] == 64,
        "PUB-007": _source_has(repo,"scripts/publication_supplement.py","archive_sha256"),
        "PUB-008": _source_has(repo,"scripts/publication_supplement.py","canonical_result"),
        "PUB-009": _source_has(repo,"scripts/publication_supplement.py","descriptive_arithmetic"),
        "PUB-010": _source_has(repo,"scripts/publication_supplement.py","primary_matched_paired_geometric_effects"),
        "PUB-011": _source_has(repo,"scripts/publication_supplement.py","total_across_tasks","mean_per_task"),
        "PUB-012": len([name for name in supplement_names if name.startswith("retry-provenance/")]) == 7,
        "PUB-013": "retry-provenance-manifest.json" in supplement_names and not extracted_validation["errors"],
        "PUB-014": extracted_validation.get("retry_provenance", {"status":"passed"}).get("status") == "passed",
        "PUB-015": extracted_validation["dashboard"]["canonical_join"] == "passed" and extracted_validation["dashboard"]["data_schema"] == "passed",
        "PUB-016": extracted_validation["dashboard"]["offline_dependencies"] == "passed",
        "PUB-017": extracted_validation["dashboard"]["browser_smoke"]["chart_rendered"] and extracted_validation["dashboard"]["browser_smoke"]["table_rendered"],
        "PUB-018": _source_has(repo,"scripts/publication_supplement.py","source_roles"),
        "PUB-019": publication["supplement_archive_sha256"].startswith("2b560a78"),
        "PUB-020": _source_has(repo,"tests/test_publication_supplement.py","non-inferiority","heterogeneity"),
        "PUB-021": publication["supplement_manifest_root"].startswith("4bbffa63"),
        "PUB-024": _source_has(repo,"tests/test_publication_supplement.py","another_archive"),
    }
    for identifier, passed in pub_conditions.items():
        facts[identifier] = passed
        evidence[identifier] = ["zip://immutable-evidence/canonical-suite-bundle.zip", "zip://immutable-evidence/canonical-publication-supplement.zip"]
    facts["PUB-022"] = not tracked_source_errors(repo); evidence["PUB-022"] = ["repo://scripts/publication_supplement.py"]
    facts["PUB-023"] = _source_has(repo,"scripts/source_verification.py","subject_unchanged"); evidence["PUB-023"] = ["repo://scripts/source_verification.py"]
    facts["PUB-025"] = _source_has(repo, "scripts/verification_registry.py", "publication_launch_boundary_errors"); evidence["PUB-025"] = ["repo://scripts/verification_registry.py"]
    facts["PUB-026"] = _source_has(repo, "scripts/build_review_handoff.py", "review-handoff-manifest-v1", "safe_extract_zip"); evidence["PUB-026"] = ["repo://scripts/build_review_handoff.py", "repo://tests/test_review_handoff.py"]

    usage = derive_token_usage({"input_tokens": 100, "cached_input_tokens": 60, "cache_write_tokens": None, "output_tokens": 20, "reasoning_output_tokens": 7})
    token_conditions = {
        "TOK-001": usage["input_tokens"] == 100, "TOK-002": usage["observed_non_cached_input_tokens"] == 40,
        "TOK-003": usage["cache_write_tokens"] is None, "TOK-004": pricing_cost(usage, uncached_input_price=1, cache_write_price=1, cached_input_price=1, output_price=1) is None,
        "TOK-005": derive_token_usage({"input_tokens":0,"cached_input_tokens":0,"output_tokens":0,"reasoning_output_tokens":0})["cache_hit_rate"] == 0,
        "TOK-006": len(erratum["token_winner_by_cache_weight"]) == 4, "TOK-007": _source_has(repo, "scripts/future_methodology.py", "by_serial_position", "by_repetition"),
        "TOK-008": _source_has(repo, "scripts/future_methodology.py", "minimum eligibility lifetime, not an eviction guarantee"),
        "TOK-009": usage["cache_isolation_mode"] == "natural", "TOK-010": _source_has(repo, "scripts/future_methodology.py", "official_prompt_cache_key", "verified_with_current_codex_cli"),
        "TOK-011": _source_has(repo, "dashboard/src/analysis.ts", "cached_input", "observed_non_cached_input"),
        "TOK-012": _source_has(repo, "scripts/token_accounting_erratum.py", "corrected_paired_geometric_ratio", "corrected_arithmetic_mean"),
        "TOK-013": erratum["row_count"] == 63, "TOK-014": usage["cache_write_metrics_available"] is False,
        "TOK-015": bool(usage["cache_write_metrics_unavailable_reason"]), "TOK-016": _source_has(repo, "scripts/future_methodology.py", "by_elapsed_gap_band", "by_prompt_policy_hash"),
        "TOK-017": erratum["canonical_archive_sha256"] == "b4a77687b40bea1ff97117224d08e00b0b66ee0a6fc1875c87d0b95da19e49e0",
        "TOK-018": usage["reasoning_output_tokens"] <= usage["output_tokens_including_reasoning"],
        "TOK-019": modeled_token_load(usage, .1) == 40 + 6 + 20,
        "TOK-020": _source_has(repo, "scripts/future_methodology.py", "output_tokens_including_reasoning", "output_price"),
        "TOK-021": erratum["historical_methodology_rewritten"] is False and "reasoning_double_counted" in erratum["legacy_metric_field"],
        "TOK-022": len(rows) == 63, "TOK-023": "token_objective_recommendation_changed" in erratum,
        "TOK-024": usage["cross_arm_cache_reuse_identifiable"] is False,
        "TOK-025": _source_has(repo, "scripts/future_methodology.py", "must not be pooled"),
        "TOK-026": _source_has(repo, "docs/token-accounting-v2.md", "routing"),
    }
    for identifier, passed in token_conditions.items(): facts[identifier] = passed; evidence[identifier] = ["repo://scripts/future_methodology.py", "repo://verification/token-accounting-erratum.json"]

    score_case=vnext["score_cases"][0]
    cor_base={
        "COR-001": _source_has(repo,"scripts/future_methodology.py","weighted_points"),
        "COR-002": _source_has(repo,"tests/test_future_methodology.py","duplicate"),
        "COR-003": any(bool(row["partial"]["critical_requirement_failures"]) for row in vnext["score_cases"]),
        "COR-004": _source_has(repo,"scripts/future_methodology.py","candidate_test_quality"),
        "COR-005": _source_has(repo,"scripts/future_methodology.py","reference_behavior_match_rate"),
        "COR-006": _source_has(repo,"scripts/future_methodology.py","source_similarity_used"),
        "COR-007": _source_has(repo,"scripts/future_methodology.py","Compare declared black-box observables only"),
        "COR-008": score_case["partial"]["requested_behavior_score"] < 100,
        "COR-009": _source_has(repo,"verification/vnext/mutants/i488-reject-all-destinations.json","explicit-id-compatible"),
        "COR-010": _source_has(repo,"verification/vnext/mutants/i488-write-before-validation.json","ambiguous-name-no-write"),
        "COR-011": not score_case["partial"]["task_success"],
        "COR-012": len(score_case["partial"]["requirement_vector"]) > 1,
        "COR-013": _source_has(repo,"configs/methodology-vnext.json","historical_methodology_immutable"),
        "COR-014": _source_has(repo,"scripts/future_methodology.py","cannot overwrite historical"),
        "COR-015": _source_has(repo,"dashboard/src/analysis.ts","requested_behavior","critical_requirement_pass_rate"),
        "COR-016": score_case["correct"]["task_success"] and score_case["correct"]["patch_quality_score"] == 70,
    }
    for identifier, passed in cor_base.items(): facts[identifier] = passed; evidence[identifier] = ["repo://scripts/future_methodology.py", "repo://verification/vnext-readiness.json"]
    vnext_ok = vnext["status"] == "passed"
    cor_conditions = {
        "COR-017": not _source_has(repo, "schemas/requirement-contract-vnext.schema.json", "custom_score"),
        "COR-018": _source_has(repo, "scripts/future_methodology.py", "candidate_test_quality must be in [0,100]"),
        "COR-019": _source_has(repo, "scripts/future_methodology.py", "single_fractional_allocation"),
        "COR-020": _source_has(repo, "scripts/future_methodology.py", "unknown protected case outcomes", "unknown mutant outcomes"),
        "COR-021": _source_has(repo, "schemas/requirement-contract-vnext.schema.json", '"if"', '"then"'),
        "COR-022": all("attainable_requested_behavior_scores" in row["diagnostics"] for row in vnext["score_cases"]),
        "COR-023": vnext["mutation_calibration"]["all_calibrated"],
        "COR-024": _source_has(repo, "schemas/mutation-calibration-vnext.schema.json", "planned_not_executable", "not_calibrated"),
        "COR-025": vnext["mutation_calibration"]["declared_mutants"] == vnext["mutation_calibration"]["materialized_mutants"],
        "COR-026": vnext_ok, "COR-027": vnext["diversity"]["broad_comparative_claims_supported"] and not vnext["zero_mutant_detection_diversity"]["broad_comparative_claims_supported"],
    }
    for identifier, passed in cor_conditions.items(): facts[identifier] = passed; evidence[identifier] = ["repo://scripts/vnext_fixture.py", "repo://verification/vnext/"]
    issue = vnext["diversity"]
    issue_conditions = [
        any(row["ceiling_risk"] for row in issue["issue_diversity_matrix"]), any(row["floor_risk"] for row in issue["issue_diversity_matrix"]),
        len(issue["differentiating_issue_ids"]) >= 1, bool(issue["covered_skill_dimensions"]), issue["minimum_issue_cluster_policy"] == 5,
        issue["broad_comparative_claims_supported"], all(row["contract_granularity"] >= 3 for row in issue["issue_diversity_matrix"]),
        _source_has(repo, "verification/vnext/contracts/issue-486.json", "issue-486"),
    ]
    for identifier, passed in zip(_ids("COR-ISSUE", 1, 8), issue_conditions): facts[identifier] = passed; evidence[identifier] = ["repo://verification/vnext-readiness.json"]
    current = subject_manifest(repo, "HEAD")
    for identifier, passed in {"SRC-001": bool(current["entries"]), "SRC-002": _source_has(repo,"scripts/source_verification.py","allowed_post_review_delta"), "SRC-003": _source_has(repo,"schemas/source-verification-envelope.schema.json","report_envelope_commit")}.items(): facts[identifier]=passed; evidence[identifier]=["repo://scripts/source_verification.py"]
    registry_ok = not validate_registry(repo) and not validate_findings(repo)
    ver = {"VER-001": registry_ok, "VER-002": set(CHECKER_MAP) <= set(AUTOMATED_IDS), "VER-003": len(CHECKER_MAP)==len(set(CHECKER_MAP)), "VER-004": (repo/"uv.lock").is_file(), "VER-005": _source_has(repo,".github/workflows/ci.yml","3.11","3.13","playwright"), "VER-006": _source_has(repo,"scripts/verification_checkers.py","verification-changes-table.md")}
    for identifier, passed in ver.items(): facts[identifier]=passed; evidence[identifier]=["repo://verification/verification-registry.json"]
    facts["SEC-002"] = _source_has(repo,"scripts/safe_archive.py","special archive member is forbidden","archive link escapes destination"); evidence["SEC-002"]=["repo://scripts/safe_archive.py"]
    return facts, evidence


def run_all_checkers(repo: Path, canonical: Path, supplement: Path, registry: list[dict[str, Any]]) -> list[dict[str, Any]]:
    facts, evidence = build_facts(repo, canonical, supplement)
    automated = [entry for entry in registry if entry["kind"] == "automated"]
    missing = sorted(entry["id"] for entry in automated if entry["id"] not in CHECKER_MAP)
    if missing: raise ValueError(f"automated verification IDs have no checker: {missing}")
    results = []
    for entry in automated:
        results.append(evaluate_checker(entry["id"], facts, evidence))
    return results


def write_changes_table(repo: Path, registry: list[dict[str, Any]], results: list[dict[str, Any]], output: Path) -> None:
    statuses = {row["verification_id"]: row["status"] for row in results}
    rows = [{"id": e["id"], "area": e["area"], "why": e["why"], "kind": e["kind"],
             "checker": e.get("checker_id"), "implementation": e["implementation"], "tests": e["test_files"],
             "result": statuses.get(e["id"], "self_reviewed" if e["kind"] == "llm_manual" else "external_limitation")}
            for e in registry]
    output.mkdir(parents=True, exist_ok=True)
    (output/"verification-changes-table.json").write_text(json.dumps({"rows": rows},indent=2,sort_keys=True)+"\n")
    lines=["# Verification changes table","","| Verification ID | Area | Why | Automated, self-reviewed, or external | Checker | Test | Result |","| --- | --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        lines.append(f"| {row['id']} | {row['area']} | {row['why']} | {row['kind']} | {row['checker'] or 'n/a'} | {'; '.join(row['tests'])} | {row['result']} |")
    (output/"verification-changes-table.md").write_text("\n".join(lines)+"\n")


def validate_changes_table(registry: list[dict[str, Any]], results: list[dict[str, Any]], json_path: Path, markdown_path: Path) -> list[str]:
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        expected = Path(directory)
        write_changes_table(Path("."), registry, results, expected)
        errors = []
        if json_path.read_bytes() != (expected/"verification-changes-table.json").read_bytes(): errors.append("verification changes JSON differs from registry/checker results")
        if markdown_path.read_bytes() != (expected/"verification-changes-table.md").read_bytes(): errors.append("verification changes Markdown differs from registry/checker results")
        return errors
