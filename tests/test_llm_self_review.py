import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from generate_llm_self_review import verification_subject_tree_sha256


class LlmSelfReviewBindingTests(unittest.TestCase):
    def test_subject_binding_is_sha256_of_full_git_tree_manifest(self) -> None:
        manifest = subprocess.check_output(
            ["git", "-C", str(ROOT), "ls-tree", "-r", "-z", "HEAD"]
        )
        actual = verification_subject_tree_sha256(ROOT)
        self.assertEqual(hashlib.sha256(manifest).hexdigest(), actual)
        self.assertEqual(64, len(actual))

    def test_current_llm_report_schema_covers_all_23_checks(self) -> None:
        schema = json.loads((ROOT / "schemas/llm-verification-report.schema.json").read_text())
        checks = schema["properties"]["checks"]
        self.assertEqual(23, checks["minItems"])
        self.assertEqual(23, checks["maxItems"])
        self.assertIn("02[0-3]", checks["items"]["properties"]["id"]["pattern"])


if __name__ == "__main__":
    unittest.main()
