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
from build_review_handoff import (
    MANDATORY_FILES,
    portable_generated_text,
    reconstruct_tree,
    scan_source_text,
    scan_text,
    validate_handoff_symlink,
    write_zip,
)


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


class ReviewHandoffTest(unittest.TestCase):
    def test_final_handoff_requires_both_pre_fix_audits(self) -> None:
        self.assertIn("audit/pre-fix-audit.json", MANDATORY_FILES)
        self.assertIn("audit/pre-fix-audit.md", MANDATORY_FILES)
        self.assertIn(
            "audit/pre-fix-portability-audit.json", MANDATORY_FILES
        )
        self.assertIn(
            "audit/pre-fix-portability-audit.md", MANDATORY_FILES
        )

    def test_dangling_in_root_symlink_is_safe_but_escape_is_rejected(
        self,
    ) -> None:
        name = "runtime/replay-rootfs/etc/alternatives/awk.1.gz"
        target = "../../usr/share/man/man1/gawk.1.gz"
        self.assertEqual(
            "runtime/replay-rootfs/usr/share/man/man1/gawk.1.gz",
            validate_handoff_symlink(name, target),
        )
        with self.assertRaisesRegex(
            ValueError, "escaping handoff evidence symlink"
        ):
            validate_handoff_symlink(
                name, "../../../../../../outside"
            )

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
            tools = {
                "byte": lambda: (extracted / "plain.txt").write_text("changed\n"),
                "mode": lambda: (extracted / "tool.sh").chmod(0o644),
                "missing": lambda: (extracted / "plain-link").unlink(),
                "extra": lambda: (extracted / "extra.txt").write_text("extra\n"),
            }
            for name, mutate in tools.items():
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

    def test_raw_maven_junit_host_path_exception_is_narrowly_scoped(self):
        payload = b'<property name="java.class.path" value="/home/server/work/target/classes"/>'
        for member in (
            "source/source.tar!/verification/methodology-current/mutation-calibration/m1/test-results/protected-common/TEST-C.xml",
            "methodology/mutation-calibration/process-evidence/m1/test-results/protected-common/TEST-C.xml",
            "channel/junit/issue-486/protected-common/TEST-C.xml",
            "preflight/current-preflight/issue-486/base/test-results/protected-common/TEST-C.xml",
            "replay/preflight/issue-486/base/test-results/protected-common/TEST-C.xml",
            "replay/mutation-calibration/m1/test-results/protected-common/TEST-C.xml",
            "replay/production-shadow/preflight/issue-486/base/test-results/protected-common/TEST-C.xml",
        ):
            with self.subTest(member=member):
                findings, exceptions = scan_source_text(member, payload)
                self.assertEqual([], findings)
                self.assertEqual(
                    "raw Maven JUnit environment-property provenance",
                    exceptions[0]["reason"],
                )
        findings, exceptions = scan_source_text("source/runtime.xml", payload)
        self.assertTrue(findings)
        self.assertEqual([], exceptions)
        findings, exceptions = scan_source_text(
            "replay/unrelated/TEST-C.xml", payload
        )
        self.assertTrue(findings)
        self.assertEqual([], exceptions)

    def test_final_replay_provenance_exceptions_are_narrowly_scoped(self):
        host_path = b"/home/server/work/replay"
        for member in (
            "source/source.tar!/scripts/target_replay.py",
            "replay/command-logs/current-preflight-issue-486.stdout.log",
            "replay/preflight/issue-486/base/maven-logs/protected-direct.log",
            "replay/runtime-resolution.json",
            "target/replay.sh",
            "tests/command-log.txt",
            "verification/independent-verifier/stdout.log",
            "runtime/replay-rootfs/usr/share/perl/5.36.0/CPAN.pm",
        ):
            with self.subTest(member=member):
                findings, exceptions = scan_source_text(member, host_path)
                self.assertEqual([], findings)
                self.assertTrue(exceptions)
        findings, exceptions = scan_source_text(
            "replay/unrelated/result.json", host_path
        )
        self.assertTrue(findings)
        self.assertEqual([], exceptions)

    def test_protected_and_runtime_secret_exceptions_are_narrowly_scoped(self):
        secret = b'api_key="abcdefghijklmnop"'
        for member in (
            "preflight/current-preflight/issue-486/base/protected-requirement-evidence-inputs/protected-sources/direct/src/test/Fixture.java",
            "replay/mutation-calibration/m1/protected-requirement-evidence-inputs/protected-sources/direct/src/test/Fixture.java",
            "runtime/bootstrap-python/lib/python3.14/http/server.py",
            "runtime/replay-rootfs/usr/share/perl/5.36.0/CPAN.pm",
        ):
            with self.subTest(member=member):
                findings, exceptions = scan_source_text(member, secret)
                self.assertEqual([], findings)
                self.assertTrue(exceptions)
        for member in (
            "preflight/current-preflight/issue-486/base/result.json",
            "runtime/runtime-lock.json",
        ):
            with self.subTest(member=member):
                findings, exceptions = scan_source_text(member, secret)
                self.assertTrue(findings)
                self.assertEqual([], exceptions)

    def test_generated_text_is_portable_and_secret_redacted(self):
        data, notes = portable_generated_text(
            b"/home/server/git-projects/codebase-knowledge-bench /home/alice/private api_key=abcdefghijklmnop"
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
        commit_stores = [node for node in ast.walk(function) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id == "commit"]
        self.assertEqual(1, len(commit_stores))

    def test_target_fixture_scanner_exceptions_are_provenance_scoped(self):
        fixture = b"api_key=ATTAsecretsecretsecret path=/home/Jane/private"
        target_name = "methodology/mutation-calibration/target-snapshots/issue.tar!/src/test/Fixture.java"
        errors, exceptions = scan_source_text(target_name, fixture)
        self.assertEqual([], errors)
        self.assertEqual({"host-only path", "secret-shaped value"}, {row["category"] for row in exceptions})
        errors, _ = scan_source_text("source/git-archive.tar!/src/main/Production.java", fixture)
        self.assertEqual(2, len(errors))

    def test_delivery_detached_receipt_binding_positive_and_negative(self):
        from external_review_delivery import sha256_bytes, validate_detached_binding

        payload = b"inner review fixture"
        digest = sha256_bytes(payload)
        receipt = {"review_zip_path": "review.zip", "review_zip_sha256": digest, "review_zip_bytes": len(payload)}
        self.assertEqual("passed", validate_detached_binding("review.zip", payload, digest, receipt)["status"])
        receipt["review_zip_path"] = "another.zip"
        self.assertEqual("failed", validate_detached_binding("review.zip", payload, digest, receipt)["status"])

    def test_outer_delivery_rejects_missing_sidecars(self):
        from external_review_delivery import validate
        from build_review_handoff import write_zip

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "delivery.zip"
            with zipfile.ZipFile(path, "w") as archive:
                write_zip(archive, "review-handoff/review.zip", b"not a zip")
                write_zip(archive, "agent-response.md", b"response")
                write_zip(archive, "delivery-manifest.json", b"{}")
                write_zip(archive, "delivery-validation.json", b"{}")
            with self.assertRaisesRegex(ValueError, "member mismatch"):
                validate(path)


if __name__ == "__main__":
    unittest.main()
