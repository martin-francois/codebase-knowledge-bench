import hashlib
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


if __name__ == "__main__":
    unittest.main()
