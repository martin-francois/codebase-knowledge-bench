from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from publication_findings import derive_publication_findings


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
        "approval_request_count": 2,
        "approval_accept_count": 1,
        "approval_reject_count": 1,
        "approval_cache_hit_count": 1,
        "approval_cache_miss_count": 1,
        "native_default_approval_request_count": 1,
        "benchmark_stricter_approval_request_count": 1,
        "approve_once_burden_count": 2,
        "approve_for_session_burden_count": 1,
        "prohibited_attempt_blocked_count": 3,
        "prohibited_access_invalidating_count": 0,
        "anti_leak_confidence": "medium",
        "anti_leak_incidents": [],
    }


class PublicationFindingsTest(unittest.TestCase):
    def rows(self) -> list[dict]:
        rows = []
        for issue in ("issue-a", "issue-b", "issue-c"):
            for repetition in (1, 2, 3, 4):
                rows.extend(
                    [
                        row(
                            "baseline-none",
                            issue,
                            repetition,
                            success=repetition == 1,
                            correctness=80,
                            cost=100,
                            seconds=100,
                        ),
                        row(
                            "tool",
                            issue,
                            repetition,
                            success=repetition == 1,
                            correctness=78,
                            cost=80,
                            seconds=90,
                        ),
                    ]
                )
        return rows

    def test_frozen_similar_quality_rule_uses_success_then_two_points(self) -> None:
        result = derive_publication_findings(self.rows())
        comparison = result["comparisons"][0]
        self.assertTrue(comparison["quality"]["similar_quality"])
        self.assertEqual(
            [
                "observed_similar_quality_lower_exact_cost",
                "observed_similar_quality_less_solve_time",
                "mixed_trade_off",
            ],
            comparison["categories"],
        )
        self.assertTrue(comparison["helps"])
        self.assertEqual(12, comparison["matched_block_count"])
        self.assertEqual(3, len(comparison["by_issue"]))
        self.assertEqual(12, len(comparison["matched_blocks"]))

    def test_one_fewer_task_success_is_not_similar_despite_correctness(self) -> None:
        rows = self.rows()
        tool_success = next(row for row in rows if row["tool"] == "tool" and row["task_success"])
        tool_success["task_success"] = False
        tool_success["correctness_score"] = 100
        comparison = derive_publication_findings(rows)["comparisons"][0]
        self.assertFalse(comparison["quality"]["similar_quality"])
        self.assertNotIn(
            "observed_similar_quality_lower_exact_cost", comparison["categories"]
        )

    def test_non_exact_cost_cannot_become_a_lower_cost_finding(self) -> None:
        rows = self.rows()
        rows[-1]["equivalent_cost"] = {
            "status": "bounded",
            "exact_usd_nanos": None,
            "lower_bound_usd_nanos": 1,
            "upper_bound_usd_nanos": 2,
        }
        comparison = derive_publication_findings(rows)["comparisons"][0]
        self.assertEqual(
            "unavailable", comparison["exact_equivalent_cost_usd_nanos"]["status"]
        )
        self.assertNotIn(
            "observed_similar_quality_lower_exact_cost", comparison["categories"]
        )

    def test_missing_and_invalid_blocks_fail_closed(self) -> None:
        rows = self.rows()
        missing = derive_publication_findings(rows[:-1])["comparisons"][0]
        self.assertEqual("incomplete", missing["status"])
        self.assertEqual(["incomplete_comparison"], missing["categories"])
        invalid_rows = copy.deepcopy(rows)
        invalid_rows[-1]["trust_valid"] = False
        invalid = derive_publication_findings(invalid_rows)["comparisons"][0]
        self.assertEqual("invalid", invalid["status"])
        self.assertEqual(["invalid_comparison"], invalid["categories"])

    def test_approval_and_anti_leak_totals_rederive_from_rows(self) -> None:
        rows = self.rows()
        result = derive_publication_findings(rows)
        self.assertEqual(24, result["measured_totals"]["valid_run_count"])
        self.assertEqual(2_160, result["measured_totals"]["exact_equivalent_cost"]["total_usd_nanos"])
        self.assertEqual(48, result["approval_burden"]["approval_request_count"])
        self.assertEqual(72, result["anti_leak"]["prohibited_attempt_blocked_count"])
        self.assertEqual(0, result["anti_leak"]["prohibited_access_invalidating_count"])
        self.assertTrue(result["anti_leak"]["positive_finding_supported"])


if __name__ == "__main__":
    unittest.main()
