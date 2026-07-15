from __future__ import annotations

import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_review_handoff import portable_generated_text, reconstruct_tree, scan_source_text, scan_text, write_zip


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

    def test_source_scan_exceptions_are_path_scoped_and_recorded(self):
        findings, exceptions = scan_source_text(
            "tests/test_review_handoff.py", b"api_key=abcdefghijklmnop"
        )
        self.assertEqual([], findings)
        self.assertEqual("negative scanner fixture", exceptions[0]["reason"])
        findings, exceptions = scan_source_text(
            "src/runtime.py", b"api_key=abcdefghijklmnop"
        )
        self.assertTrue(findings)
        self.assertEqual([], exceptions)

    def test_generated_text_is_portable_and_secret_redacted(self):
        data, notes = portable_generated_text(
            b"/home/server/git-projects/codebase-knowledge-graph-benchmark /home/alice/private api_key=abcdefghijklmnop"
        )
        self.assertIn(b"$REPO", data)
        self.assertIn(b"$REDACTED_TEST_SECRET", data)
        self.assertEqual([], scan_text("source/full-diff.patch", data))
        self.assertGreaterEqual(len(notes), 3)
        self.assertNotIn("/home/", " ".join(notes))
        self.assertNotIn("abcdefghijklmnop", " ".join(notes))

    def test_deterministic_zip_member_uses_complete_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "member.zip"
            with zipfile.ZipFile(path, "w") as archive:
                write_zip(archive, "member.txt", b"content")
            with zipfile.ZipFile(path) as archive:
                self.assertEqual((1980, 1, 1, 0, 0, 0), archive.getinfo("member.txt").date_time)

    def test_explicit_directories_are_not_file_collisions(self):
        from safe_archive import safe_extract_tar

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "directories.tar"
            with tarfile.open(archive_path, "w") as archive:
                folder = tarfile.TarInfo("folder/")
                folder.type = tarfile.DIRTYPE
                archive.addfile(folder)
                child = tarfile.TarInfo("folder/child.txt")
                child.size = 1
                import io
                archive.addfile(child, io.BytesIO(b"x"))
            with tarfile.open(archive_path) as archive:
                safe_extract_tar(archive, root / "out")
            self.assertEqual("x", (root / "out/folder/child.txt").read_text())

    def test_file_directory_collision_is_rejected(self):
        from safe_archive import safe_extract_tar

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "collision.tar"
            with tarfile.open(archive_path, "w") as archive:
                import io
                parent = tarfile.TarInfo("folder")
                parent.size = 1
                archive.addfile(parent, io.BytesIO(b"x"))
                child = tarfile.TarInfo("folder/child")
                child.size = 1
                archive.addfile(child, io.BytesIO(b"y"))
            with tarfile.open(archive_path) as archive:
                with self.assertRaisesRegex(ValueError, "file/directory collision"):
                    safe_extract_tar(archive, root / "out")

    def test_optional_target_repository_parameter_is_not_shadowed(self):
        import ast

        source = (ROOT / "scripts/build_review_handoff.py").read_text()
        function = next(node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.FunctionDef) and node.name == "build")
        stores = [node for node in ast.walk(function) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id == "target"]
        self.assertEqual([], stores)


if __name__ == "__main__":
    unittest.main()
