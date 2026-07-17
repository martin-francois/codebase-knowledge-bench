import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from mutation_calibration import classify_calibration


class MutationCalibrationProvenanceTests(unittest.TestCase):
    def test_clean_targeted_mutant_requires_common_process_and_isolation(self) -> None:
        definition = {"calibration_kind": "targeted"}
        positive = classify_calibration(
            definition,
            intended_failure=True,
            unexpected_requested_collateral=set(),
            regression_gates_pass=True,
            common_pass=True,
            overlap_pass=True,
            process_valid=True,
        )
        self.assertEqual("killed", positive["status"])
        self.assertTrue(positive["calibrated"])
        for field in (
            "common_pass", "overlap_pass", "process_valid", "regression_gates_pass"
        ):
            arguments = {
                "intended_failure": True,
                "unexpected_requested_collateral": set(),
                "regression_gates_pass": True,
                "common_pass": True,
                "overlap_pass": True,
                "process_valid": True,
            }
            arguments[field] = False
            with self.subTest(field=field):
                observed = classify_calibration(definition, **arguments)
                self.assertFalse(observed["calibrated"])

    def test_published_mutant_receipt_includes_source_and_process_provenance(self) -> None:
        source = (ROOT / "scripts/mutation_calibration.py").read_text(encoding="utf-8")
        for field in (
            '"protected_source_hashes"',
            '"channel_process_audit"',
            '"configured_common_skip_count"',
            '"selector_overlap_empty"',
        ):
            self.assertIn(field, source)

    def test_mutants_are_serialized_to_prevent_common_suite_interference(self) -> None:
        source = (ROOT / "scripts/mutation_calibration.py").read_text(encoding="utf-8")
        self.assertNotIn("ThreadPoolExecutor", source)
        self.assertIn("calibration must execute mutants serially", source)


if __name__ == "__main__":
    unittest.main()
