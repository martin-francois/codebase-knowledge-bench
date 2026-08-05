from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from methodology_revision import (
    derive_rule_correction_proof,
    methodology_revision_record,
)


def row(
    tool: str,
    issue: str,
    repetition: int,
    *,
    success: bool,
    correctness: float,
    cost: int,
    seconds: float,
) -> dict:
    return {
        "tool": tool,
        "issue_id": issue,
        "repetition": repetition,
        "implementation_evaluated": True,
        "operational_rank_eligible": True,
        "trust_valid": True,
        "task_success": success,
        "correctness_score": correctness,
        "active_solve_seconds": seconds,
        "equivalent_cost": {
            "status": "exact",
            "exact_usd_nanos": cost,
            "lower_bound_usd_nanos": cost,
            "upper_bound_usd_nanos": cost,
        },
        "prohibited_attempt_blocked_count": 0,
        "prohibited_access_invalidating_count": 0,
        "anti_leak_confidence": "high",
        "anti_leak_incidents": [],
    }


def cohort(tool_profiles: dict[str, dict]) -> list[dict]:
    rows = []
    for issue in ("issue-a", "issue-b", "issue-c"):
        for repetition in (1, 2, 3, 4):
            rows.append(
                row(
                    "baseline-none",
                    issue,
                    repetition,
                    success=repetition == 1,
                    correctness=83,
                    cost=100,
                    seconds=100,
                )
            )
            for tool, profile in tool_profiles.items():
                rows.append(
                    row(
                        tool,
                        issue,
                        repetition,
                        success=repetition == 1 and profile.get("success", True),
                        correctness=profile["correctness"],
                        cost=profile["cost"],
                        seconds=profile["seconds"],
                    )
                )
    return rows


class MethodologyRevisionTest(unittest.TestCase):
    def test_published_cohort_pattern_findings_stay_unchanged(self) -> None:
        rows = cohort(
            {
                "lower-cost-longer-time": {
                    "correctness": 83,
                    "cost": 95,
                    "seconds": 111,
                },
                "less-time-higher-cost": {
                    "correctness": 83,
                    "cost": 107,
                    "seconds": 97,
                },
                "similar-no-savings": {
                    "correctness": 81.5,
                    "cost": 120,
                    "seconds": 130,
                },
                "worse-score": {
                    "correctness": 78,
                    "cost": 90,
                    "seconds": 90,
                },
                "fewer-solves": {
                    "success": False,
                    "correctness": 82.5,
                    "cost": 90,
                    "seconds": 90,
                },
            }
        )
        proof = derive_rule_correction_proof(rows)
        self.assertTrue(proof["findings_unchanged"])
        self.assertEqual(
            ["less-time-higher-cost", "lower-cost-longer-time"],
            proof["tools_that_helped_original"],
        )
        self.assertEqual(
            proof["tools_that_helped_original"],
            proof["tools_that_helped_revised"],
        )
        by_tool = {item["tool"]: item for item in proof["per_tool"]}
        self.assertEqual(
            "similar",
            by_tool["lower-cost-longer-time"]["revised_result_classification"],
        )
        self.assertEqual(
            "worse", by_tool["fewer-solves"]["revised_result_classification"]
        )
        self.assertEqual(
            "worse", by_tool["worse-score"]["revised_result_classification"]
        )
        for item in proof["per_tool"]:
            self.assertTrue(item["finding_unchanged"], item["tool"])

    def test_proof_detects_a_finding_change(self) -> None:
        rows = cohort(
            {
                "small-score-gain": {
                    "correctness": 84,
                    "cost": 95,
                    "seconds": 90,
                },
            }
        )
        proof = derive_rule_correction_proof(rows)
        by_tool = {item["tool"]: item for item in proof["per_tool"]}
        record = by_tool["small-score-gain"]
        self.assertEqual("similar", record["revised_result_classification"])
        self.assertIn("observed_better_quality", record["original_categories"])
        self.assertNotIn("observed_better_quality", record["revised_categories"])
        self.assertFalse(record["finding_unchanged"])
        self.assertFalse(proof["findings_unchanged"])

    def test_revision_record_is_result_neutral_and_machine_readable(self) -> None:
        record = methodology_revision_record()
        self.assertEqual(
            "post-run-methodology-revision-v1", record["schema_version"]
        )
        self.assertFalse(record["raw_results_changed"])
        self.assertFalse(record["reruns_performed"])
        kinds = {revision["kind"] for revision in record["revisions"]}
        self.assertEqual(
            {"uncertainty_display_correction", "result_comparison_correction"},
            kinds,
        )
        uncertainty = next(
            revision
            for revision in record["revisions"]
            if revision["kind"] == "uncertainty_display_correction"
        )
        self.assertEqual(
            "Observed range across four repetitions",
            uncertainty["reader_facing_label"],
        )
        self.assertEqual(
            "observed-min-max-repetition-means-v1",
            uncertainty["revised_method_id"],
        )


if __name__ == "__main__":
    unittest.main()
