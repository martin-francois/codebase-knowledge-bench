from __future__ import annotations

import sys
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from calibration_coverage import build


class CalibrationCoverageTests(unittest.TestCase):
    def test_existing_target_code_mutants_remain_executed_and_killed(self):
        result = build(ROOT)
        definitions = json.loads((ROOT / "verification/methodology-current/mutations/mutants.json").read_text())
        self.assertEqual(len(definitions["mutants"]), result["executed_mutants"])
        self.assertEqual(len(definitions["mutants"]), result["killed_mutants"])
        self.assertEqual(0, result["survived_mutants"])

    def test_every_critical_dimension_has_targeted_calibration(self):
        result = build(ROOT)
        critical = [row for row in result["requirements"] if row["critical"]]
        self.assertTrue(all(row["calibration_status"] == "calibrated" for row in critical))
        self.assertTrue(all(row["targeted_mutants"] for row in critical))

    def test_targeted_critical_calibration_completes_readiness_gate(self):
        result = build(ROOT)
        self.assertTrue(result["critical_calibration_complete"])
        self.assertEqual("passed", result["status"])


if __name__ == "__main__":
    unittest.main()
