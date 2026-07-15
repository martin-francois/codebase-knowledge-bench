import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from mutation_calibration import protected_provenance


class MutationCalibrationProvenanceTests(unittest.TestCase):
    def test_protected_junit_ownership_and_source_hash_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "ProtectedTest.java"
            source.write_bytes(b"final class ProtectedTest {}\n")
            provenance = protected_provenance({"src/test/ProtectedTest.java": source})

        self.assertIs(provenance["candidate_junit_included"], False)
        self.assertEqual(
            provenance["protected_source_hashes"]["src/test/ProtectedTest.java"],
            hashlib.sha256(b"final class ProtectedTest {}\n").hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
