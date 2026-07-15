from __future__ import annotations

import copy
import csv
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from publication_supplement import publication_contract_errors, resolve_recorded_path, write_csv


def fixture() -> tuple[dict, str, dict, dict, dict]:
    supported = {"correctness_non_inferior_by_tolerance": {"0": [{"variant": "sverklo"}]}}
    summary = {
        "archive": {"sha256": "b4a77687b40bea1ff97117224d08e00b0b66ee0a6fc1875c87d0b95da19e49e0", "embedded_review_manifest_count": 13},
        "descriptive_arithmetic_means": [{
            "treatment": "baseline-none", "task_success_count": 1, "task_count": 1,
            "arithmetic_mean_behavioral_correctness": 100, "arithmetic_mean_modeled_weighted_tokens": 10,
            "arithmetic_mean_solve_seconds": 1, "arithmetic_mean_warm_seconds": 2,
            "arithmetic_mean_calls_started": 1, "successful_intended_tool_calls_total_across_tasks": 0,
            "successful_intended_tool_calls_arithmetic_mean_per_task": 0, "strict_direct_attribution_count": 0,
        }],
        "primary_matched_paired_geometric_effects": [], "observed_findings": {},
        "supported_findings": supported, "direct_attribution": {}, "limitations": [],
        "suite_id": "fixture", "canonical_result": {}, "execution_source": {"commit": "x", "tree": "y"},
        "analysis_source": {"commit": "x", "tree": "y"},
    }
    report = "\n".join([
        "Paired geometric effects are the primary baseline comparison; aggregate arithmetic means are descriptive only.",
        "Supported correctness non-inferiority", "limited_cluster_evidence",
        "Cached-token-weight sensitivity", "Delayed-retry block sensitivity",
        "All observed quality differentiation comes from `issue-498`",
    ])
    required = [
        "fresh-retry-execution-contract.json", "immutable-input-comparison.json", "prompt-equality.json",
        "semantic-fingerprint-comparison.json", "selected-state-restoration-comparison.json",
        "selected-pre-smoke-snapshot-manifest.json", "child-spawn-receipt.json",
    ]
    retry = {"records": [{"artifact": name} for name in required]}
    gaps = {"gaps": []}
    validation = {"embedded_manifests": {"count": 13}, "dashboard": {"status": "passed"}}
    return summary, report, retry, gaps, validation


class PublicationSupplementContractTest(unittest.TestCase):
    def assert_contract_error(self, mutate, phrase: str) -> None:
        values = list(fixture())
        mutate(values)
        errors = publication_contract_errors(*values)
        self.assertTrue(any(phrase in error for error in errors), errors)

    def test_rejects_summary_values_from_another_archive(self):
        self.assert_contract_error(lambda v: v[0]["archive"].update(sha256="0" * 64), "selected archive")

    def test_rejects_arithmetic_aggregate_labeled_as_matched_effect(self):
        self.assert_contract_error(lambda v: v.__setitem__(1, v[1].replace("aggregate arithmetic means are descriptive only", "")), "mislabeled")

    def test_intended_tool_totals_are_not_presented_as_means(self):
        def mutate(v):
            v[0]["descriptive_arithmetic_means"][0].pop("successful_intended_tool_calls_total_across_tasks")
        self.assert_contract_error(mutate, "totals and means")

    def test_rejects_blank_statistical_support(self):
        self.assert_contract_error(lambda v: v.__setitem__(1, v[1].replace("Supported correctness non-inferiority", "")), "support is blank")

    def test_rejects_missing_limited_cluster_status(self):
        self.assert_contract_error(lambda v: v.__setitem__(1, v[1].replace("limited_cluster_evidence", "")), "limited-cluster")

    def test_retry_proof_must_be_packaged_or_declared_missing(self):
        self.assert_contract_error(lambda v: v[2]["records"].pop(), "neither packaged")

    def test_child_spawn_receipt_needs_warning_when_absent(self):
        def mutate(v):
            v[2]["records"] = [row for row in v[2]["records"] if row["artifact"] != "child-spawn-receipt.json"]
        self.assert_contract_error(mutate, "timing receipt")

    def test_all_embedded_manifests_are_required(self):
        self.assert_contract_error(lambda v: v[4]["embedded_manifests"].update(count=12), "fewer embedded")

    def test_dashboard_cannot_be_not_applicable(self):
        self.assert_contract_error(lambda v: v[4]["dashboard"].update(status="not_applicable"), "dashboard validation")

    def test_token_weight_sensitivity_is_required(self):
        self.assert_contract_error(lambda v: v.__setitem__(1, v[1].replace("Cached-token-weight sensitivity", "")), "token-weight")

    def test_delayed_retry_sensitivity_is_required(self):
        self.assert_contract_error(lambda v: v.__setitem__(1, v[1].replace("Delayed-retry block sensitivity", "")), "delayed-retry")

    def test_issue_498_heterogeneity_is_required(self):
        self.assert_contract_error(lambda v: v.__setitem__(1, v[1].replace("All observed quality differentiation comes from `issue-498`", "")), "issue-498")

    def test_csv_uses_union_of_heterogeneous_row_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "table.csv"
            write_csv(path, [{"treatment": "baseline"}, {"treatment": "tool", "interval": 0.5}])
            with path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
        self.assertEqual(["treatment", "interval"], list(rows[0]))
        self.assertEqual("0.5", rows[1]["interval"])

    def test_sanitized_output_root_path_resolves_without_weakening_identity(self):
        self.assertEqual(
            Path("/canonical/child-spawn-receipts/receipt.json"),
            resolve_recorded_path("$OUTPUT_ROOT/child-spawn-receipts/receipt.json", Path("/canonical")),
        )

    def test_unknown_sanitized_path_placeholder_fails_closed(self):
        with self.assertRaises(ValueError):
            resolve_recorded_path("$OTHER/receipt.json", Path("/canonical"))


if __name__ == "__main__":
    unittest.main()
