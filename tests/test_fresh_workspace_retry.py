import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from fresh_workspace_retry import (  # noqa: E402
    HISTORICAL_DIGEST, compare_fingerprints, create_snapshot,
    normalize_path_text, restore_snapshot, state_manifest,
    validate_pre_model_artifacts,
)


class FreshWorkspaceRetryTests(unittest.TestCase):
    def test_historical_digest_is_not_a_reconstruction_mechanism(self):
        self.assertEqual(len(HISTORICAL_DIGEST), 64)

    def test_path_normalization_preserves_relative_paths(self):
        root = Path("/tmp/build-a")
        self.assertEqual(normalize_path_text("/tmp/build-a/repo/A.java", [root]), "$WORKSPACE_ROOT/repo/A.java")
        self.assertEqual(normalize_path_text("scripts/run_benchmark.py", [root]), "scripts/run_benchmark.py")

    def test_path_normalization_makes_independent_query_rows_equal(self):
        left = normalize_path_text("/tmp/build-a/repo/src/A.java", [Path("/tmp/build-a")])
        right = normalize_path_text("/tmp/build-b/repo/src/A.java", [Path("/tmp/build-b")])
        self.assertEqual(left, right)

    def test_fingerprint_comparison(self):
        left = {"semantic_sha256": "a", "tables": {"nodes": {"row_count": 2}}}
        right = {"semantic_sha256": "b", "tables": {"nodes": {"row_count": 2}}}
        self.assertTrue(compare_fingerprints(left, right)["equal"])
        right["tables"]["nodes"]["row_count"] = 3
        self.assertFalse(compare_fingerprints(left, right)["equal"])

    def test_optional_absent_tables_are_explicitly_comparable(self):
        absent = {"present": False, "columns": [], "row_count": 0}
        self.assertTrue(compare_fingerprints(
            {"semantic_sha256": "a", "embeddings": absent},
            {"semantic_sha256": "b", "embeddings": dict(absent)},
        )["equal"])

    def test_snapshot_restores_selected_own_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "workspace"
            for name in ("repo", "home", "xdg-cache", "xdg-config", "xdg-data", "codex-template"):
                (root / name).mkdir(parents=True)
            (root / "repo/file").write_text("before")
            archive = Path(temporary) / "state.tar.zst"
            receipt = create_snapshot(root, archive)
            (root / "repo/file").write_text("after")
            restored = restore_snapshot(archive, root)
            self.assertEqual(receipt["state_sha256"], restored["state_sha256"])
            self.assertEqual((root / "repo/file").read_text(), "before")

    def test_pre_model_gate_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = validate_pre_model_artifacts(Path(temporary))
            self.assertEqual(result["decision"], "NO_GO")
            self.assertTrue(result["missing_artifacts"])

    def test_candidate_change_changes_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("repo", "home", "xdg-cache", "xdg-config", "xdg-data", "codex-template"):
                (root / name).mkdir()
            before = state_manifest(root)["state_sha256"]
            (root / "repo/candidate.java").write_text("candidate")
            self.assertNotEqual(before, state_manifest(root)["state_sha256"])


if __name__ == "__main__":
    unittest.main()
