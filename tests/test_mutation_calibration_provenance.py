import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

class MutationCalibrationProvenanceTests(unittest.TestCase):
    def test_protected_junit_ownership_and_source_hash_are_explicit(self) -> None:
        root = Path(__file__).resolve().parents[1]
        evidence_root = root / "verification/methodology-current/mutation-calibration/i486-import-active-drop"
        provenance = json.loads((evidence_root / "protected-verification.json").read_text())
        result = json.loads((evidence_root / "result.json").read_text())

        self.assertIs(provenance["candidate_junit_included"], False)
        self.assertEqual([], provenance["candidate_owned_cases"])
        self.assertTrue(result["selector_overlap_empty"])
        self.assertTrue(result["protected_source_hashes"]["common"])
        self.assertTrue(result["protected_source_hashes"]["direct"])


if __name__ == "__main__":
    unittest.main()
