from __future__ import annotations

import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_review_handoff import reconstruct_tree, scan_text


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


class ReviewHandoffTest(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, bytes, str]:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        (repo / "plain.txt").write_text("plain\n")
        script = repo / "tool.sh"
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o755)
        os.symlink("plain.txt", repo / "plain-link")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        tree = git(repo, "write-tree")
        archive = root / "source.tar"
        subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", "--output", str(archive), tree], check=True)
        return repo, archive.read_bytes(), tree

    def test_reconstructs_exact_tree_with_modes_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            _, payload, tree = self.fixture(Path(directory))
            result = reconstruct_tree(payload, tree)
            self.assertTrue(result["exact_match"], result)

    def test_changed_byte_mode_missing_and_extra_change_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, payload, tree = self.fixture(root)
            extracted = root / "extracted"
            tar_path = root / "copy.tar"
            tar_path.write_bytes(payload)
            with tarfile.open(tar_path) as archive:
                from safe_archive import safe_extract_tar
                safe_extract_tar(archive, extracted)
            variants = {
                "byte": lambda: (extracted / "plain.txt").write_text("changed\n"),
                "mode": lambda: (extracted / "tool.sh").chmod(0o644),
                "missing": lambda: (extracted / "plain-link").unlink(),
                "extra": lambda: (extracted / "extra.txt").write_text("extra\n"),
            }
            for name, mutate in variants.items():
                with self.subTest(name=name):
                    subprocess.run(["git", "-C", str(extracted), "init", "-q"], check=True)
                    mutate()
                    subprocess.run(["git", "-C", str(extracted), "add", "-A"], check=True)
                    self.assertNotEqual(tree, git(extracted, "write-tree"))
                    subprocess.run(["rm", "-rf", str(extracted)], check=True)
                    with tarfile.open(tar_path) as archive:
                        from safe_archive import safe_extract_tar
                        safe_extract_tar(archive, extracted)

    def test_secret_and_host_paths_are_scanned(self):
        self.assertTrue(scan_text("source/x", b"api_key=abcdefghijklmnop"))
        self.assertTrue(scan_text("agent-response.md", b"/home/alice/private/file"))
        self.assertEqual([], scan_text("docs/path", b"repo://relative/path"))


if __name__ == "__main__":
    unittest.main()
