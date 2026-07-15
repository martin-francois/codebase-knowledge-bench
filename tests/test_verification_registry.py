import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from verification_checkers import CHECKERS, run
from verification_registry import execute, load, validate


class VerificationRegistryTests(unittest.TestCase):
    def test_registry_schema_paths_and_checker_coverage(self):
        self.assertEqual([], validate(ROOT))
        automated = {entry["id"] for entry in load(ROOT)["entries"] if entry["kind"] == "automated"}
        self.assertEqual(automated, set(CHECKERS))

    def test_every_automated_checker_has_dedicated_positive_and_negative_result(self):
        failures = []
        for checker_id in sorted(CHECKERS):
            positive = run(checker_id, ROOT)
            negative = run(checker_id, ROOT, inject_fault=True)
            if positive["status"] != "passed" or negative["status"] != "failed":
                failures.append({"id": checker_id, "positive": positive, "negative": negative})
        self.assertEqual([], failures)

    def test_current_report_invokes_every_checker(self):
        report = execute(ROOT)
        self.assertEqual(set(CHECKERS), {row["id"] for row in report["checks"]})
        self.assertEqual("passed", report["status"], json.dumps(report, indent=2))

    def test_missing_checker_is_blocker(self):
        key = next(iter(CHECKERS))
        removed = CHECKERS.pop(key)
        try:
            self.assertTrue(any("checker coverage mismatch" in error for error in validate(ROOT)))
        finally:
            CHECKERS[key] = removed


if __name__ == "__main__":
    unittest.main()
