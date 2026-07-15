from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from calibration_coverage import build


class CalibrationCoverageTests(unittest.TestCase):
    def test_existing_target_code_mutants_remain_executed_and_killed(self):
        result = build(ROOT)
        self.assertEqual(6, result["executed_mutants"])
        self.assertEqual(6, result["killed_mutants"])
        self.assertEqual(0, result["survived_mutants"])

    def test_broad_mutant_cannot_claim_multiple_acceptance_dimensions(self):
        result = build(ROOT)
        row = next(item for item in result["requirements"] if item["requirement_id"] == "no-in-progress-workflow-and-side-effects")
        self.assertEqual("targeted_calibration_incomplete", row["calibration_status"])
        self.assertGreater(len(row["distinct_acceptance_dimensions"]), len(row["targeted_mutants"]))

    def test_missing_targeted_critical_calibration_blocks_readiness(self):
        result = build(ROOT)
        self.assertFalse(result["critical_calibration_complete"])
        self.assertEqual("failed", result["status"])


if __name__ == "__main__":
    unittest.main()
