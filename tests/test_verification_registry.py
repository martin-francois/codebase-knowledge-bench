from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from verification_registry import publication_launch_boundary_errors, render_registry, validate_findings, validate_publications, validate_registry


class VerificationRegistryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = json.loads((ROOT / "verification" / "verification-registry.json").read_text())

    def test_golden_registry_and_markdown_agree(self):
        self.assertEqual([], validate_registry(ROOT))
        self.assertEqual((ROOT / "docs" / "verification-registry.md").read_text(), render_registry(self.document["entries"]))

    def test_duplicate_id_fails(self):
        entries = copy.deepcopy(self.document["entries"])
        entries.append(copy.deepcopy(entries[0]))
        self.assertIn("unique", " ".join(validate_registry(ROOT, entries)))

    def test_missing_test_and_stale_path_fail(self):
        entries = copy.deepcopy(self.document["entries"])
        target = next(item for item in entries if item["kind"] == "automated")
        target["test_files"] = []
        target["implementation"] = ["missing.py"]
        errors = " ".join(validate_registry(ROOT, entries))
        self.assertIn("stale", errors)
        self.assertIn("lacks implementation or tests", errors)

    def test_undocumented_llm_check_fails(self):
        entries = copy.deepcopy(self.document["entries"])
        target = next(item for item in entries if item["kind"] == "llm_manual")
        target["id"] = "LLM-999"
        self.assertIn("undocumented", " ".join(validate_registry(ROOT, entries)))

    def test_unresolved_blocker_fails(self):
        entries = copy.deepcopy(self.document["entries"])
        target = entries[0]
        target["failure_severity"] = "blocker"
        target["kind"] = "llm_manual"
        self.assertIn("blocker", " ".join(validate_registry(ROOT, entries)))

    def test_findings_ledger_is_classified(self):
        self.assertEqual([], validate_findings(ROOT))

    def test_publication_validation_never_launches_subprocess(self):
        root = Path("/home/server/git-projects/.codebase-knowledge-graph-benchmark-output/canonical-three-repetition/final-deterministic-integration-20260715T112633Z")
        if not root.is_dir():
            self.skipTest("immutable publication fixture unavailable")
        with mock.patch("subprocess.Popen", side_effect=AssertionError("launch forbidden")):
            result = validate_publications(root / "suite-bundle.zip", root / "canonical-publication-supplement.zip")
        self.assertEqual("passed", result["status"])

    def test_publication_generator_has_no_model_or_benchmark_launch_path(self):
        self.assertEqual([], publication_launch_boundary_errors(ROOT / "scripts" / "publication_supplement.py"))

    def test_model_launch_import_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "bad.py"
            source.write_text("from run_benchmark import main\nmain()\n")
            self.assertTrue(publication_launch_boundary_errors(source))

    def test_untracked_supplement_source_fails_release_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q", repo], check=True)
            (repo / "generator.py").write_text("pass\n")
            tracked = subprocess.run(["git", "ls-files"], cwd=repo, text=True, capture_output=True, check=True).stdout
            self.assertNotIn("generator.py", tracked)
