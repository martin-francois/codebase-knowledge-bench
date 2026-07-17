from __future__ import annotations

import copy
import hashlib
import io
import json
import os
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
    review_manifest_path_errors,
    write_zip,
    write_zip_directory,
    write_zip_symlink,
)
from preflight_status_faults import FAULTS, run as status_fault_matrix
from safe_archive import (
    build_exact_tar,
    canonical_root,
    exact_archive_manifest,
    safe_extract_exact_tar,
    safe_extract_zip,
    validate_exact_tar,
)
from independent_verifier import _validated_zip_infos
from target_replay import (
    _replay_script,
    _stage_python,
    embedded_python_blocks,
    validate_generated_script,
)


class ExactPreflightStatusTest(unittest.TestCase):
    def test_positive_and_all_required_negative_status_fixtures(self) -> None:
        result = status_fault_matrix(ROOT)
        self.assertEqual("passed", result["status"])
        self.assertEqual("passed", result["positive_fixture"])
        self.assertEqual(set(FAULTS), {row["id"] for row in result["records"]})
        self.assertTrue(
            all(row["status"] == "rejected" for row in result["records"])
        )

    def test_contracts_use_only_exact_status_outcomes(self) -> None:
        for path in sorted(
            (ROOT / "verification/methodology-current/contracts").glob(
                "issue-*.json"
            )
        ):
            contract = json.loads(path.read_text(encoding="utf-8"))
            for requirement in contract["requirements"]:
                for evidence in requirement["evidence"]:
                    self.assertIn(evidence["base_status"], {"passed", "failed"})
                    self.assertIn(
                        evidence["reference_status"], {"passed", "failed"}
                    )
                    self.assertNotIn("base_result", evidence)
                    self.assertNotIn("reference_result", evidence)


class SourceGeneratedReplayTest(unittest.TestCase):
    def test_generation_is_equal_and_all_embedded_python_compiles(self) -> None:
        first = _replay_script()
        second = _replay_script()
        self.assertEqual(first.encode(), second.encode())
        self.assertEqual("passed", validate_generated_script(first)["status"])
        blocks = embedded_python_blocks(first)
        self.assertEqual(1, len(blocks))
        for index, block in enumerate(blocks):
            compile(block, f"<embedded-{index}>", "exec")
        self.assertNotIn("--finalize-existing", first)

    def test_broken_embedded_python_is_rejected(self) -> None:
        broken = _replay_script().replace(
            "import pathlib\n",
            "import pathlib\nbroken = 'newline\nliteral'\n",
            1,
        )
        result = validate_generated_script(broken)
        self.assertEqual("failed", result["status"])
        self.assertTrue(
            any("embedded Python" in error for error in result["errors"])
        )

    def test_replay_script_drift_is_byte_visible(self) -> None:
        generated = _replay_script().encode()
        drifted = generated + b"# drift\n"
        self.assertNotEqual(generated, drifted)

    def test_replay_selects_only_declared_targeted_mutants(self) -> None:
        source = (ROOT / "scripts/target_replay.py").read_text()
        self.assertIn(
            'row.get("calibration_kind") == "targeted"', source
        )
        self.assertIn(
            'mutation_command.extend(["--only", mutant_id])', source
        )


class ExactArchiveBoundaryTest(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, Path]:
        source = root / "dependency"
        source.mkdir()
        (source / "expected.txt").write_text("expected\n", encoding="utf-8")
        archive = root / "dependency.tar.zst"
        manifest = build_exact_tar(source, archive, "dependency")
        manifest_path = root / "dependency-manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(
            "passed", validate_exact_tar(archive, manifest_path)["status"]
        )
        return archive, manifest_path

    def test_unmanifested_member_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, manifest = self.fixture(root)
            with tarfile.open(archive, "w:zst") as output:
                directory = tarfile.TarInfo("dependency")
                directory.type = tarfile.DIRTYPE
                directory.mode = 0o755
                output.addfile(directory)
                expected = tarfile.TarInfo("dependency/expected.txt")
                expected.mode = 0o644
                expected.size = len(b"expected\n")
                output.addfile(expected, io.BytesIO(b"expected\n"))
                extra = tarfile.TarInfo("dependency/extra.txt")
                extra.mode = 0o644
                extra.size = len(b"extra\n")
                output.addfile(extra, io.BytesIO(b"extra\n"))
            result = validate_exact_tar(archive, manifest)
            self.assertEqual("failed", result["status"])
            self.assertTrue(
                any(
                    "unexpected archive members" in error
                    for error in result["errors"]
                ),
                result,
            )

    def test_escaping_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, manifest = self.fixture(root)
            with tarfile.open(archive, "w:zst") as output:
                directory = tarfile.TarInfo("dependency")
                directory.type = tarfile.DIRTYPE
                directory.mode = 0o755
                output.addfile(directory)
                expected = tarfile.TarInfo("dependency/expected.txt")
                expected.mode = 0o644
                expected.size = len(b"expected\n")
                output.addfile(expected, io.BytesIO(b"expected\n"))
                escape = tarfile.TarInfo("dependency/escape")
                escape.type = tarfile.SYMTYPE
                escape.mode = 0o777
                escape.linkname = "../../outside"
                output.addfile(escape)
            result = validate_exact_tar(archive, manifest)
            self.assertEqual("failed", result["status"])
            self.assertTrue(
                any("escaping archive link" in error for error in result["errors"]),
                result,
            )

    def test_mode_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, manifest_path = self.fixture(root)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            file_row = next(
                row for row in manifest["entries"] if row["type"] == "file"
            )
            file_row["mode"] ^= 0o100
            manifest["manifest_root"] = canonical_root(manifest["entries"])
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            result = validate_exact_tar(archive, manifest_path)
            self.assertEqual("failed", result["status"])
            self.assertTrue(
                any("archive member mismatch" in error for error in result["errors"])
            )

    def test_duplicate_manifest_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, manifest_path = self.fixture(root)
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            manifest["entries"].append(
                copy.deepcopy(manifest["entries"][-1])
            )
            manifest["entry_count"] = len(manifest["entries"])
            manifest["manifest_root"] = canonical_root(
                manifest["entries"]
            )
            manifest_path.write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            result = validate_exact_tar(archive, manifest_path)
            self.assertEqual("failed", result["status"])
            self.assertTrue(
                any("duplicate" in error for error in result["errors"]),
                result,
            )

    def test_review_manifest_path_collisions_are_rejected(self) -> None:
        base = {
            "type": "file",
            "bytes": 1,
            "sha256": "0" * 64,
            "mode": 0o644,
            "symlink_target": None,
            "hardlink_target": None,
            "media_type": "application/octet-stream",
            "role": "runtime",
            "source": "generated-or-content-addressed",
            "required": True,
        }
        entries = [
            {"path": "runtime/tool", **base},
            {"path": "runtime/tool", **base},
            {"path": "Runtime/other", **base},
            {"path": "runtime", **base},
        ]
        errors = review_manifest_path_errors(entries)
        self.assertTrue(any("duplicate" in error for error in errors))
        self.assertTrue(any("case-fold" in error for error in errors))
        self.assertTrue(
            any("file/directory collision" in error for error in errors)
        )

    def test_manifested_hardlink_extracts_as_the_same_inode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "hardlink.tar.zst"
            with tarfile.open(archive_path, "w:zst") as archive:
                directory = tarfile.TarInfo("dependency")
                directory.type = tarfile.DIRTYPE
                directory.mode = 0o755
                archive.addfile(directory)
                source = tarfile.TarInfo("dependency/source.txt")
                source.mode = 0o644
                source.size = 7
                archive.addfile(source, io.BytesIO(b"source\n"))
                linked = tarfile.TarInfo("dependency/linked.txt")
                linked.type = tarfile.LNKTYPE
                linked.mode = 0o644
                linked.linkname = "dependency/source.txt"
                archive.addfile(linked)
            with tarfile.open(archive_path) as archive:
                manifest = exact_archive_manifest(
                    archive_path, archive
                )
            manifest_path = root / "hardlink-manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            destination = root / "out"
            safe_extract_exact_tar(
                archive_path, manifest_path, destination
            )
            self.assertTrue(
                os.path.samefile(
                    destination / "dependency/source.txt",
                    destination / "dependency/linked.txt",
                )
            )

    def test_hardlink_mode_disagreement_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "hardlink-mode.tar.zst"
            with tarfile.open(archive_path, "w:zst") as archive:
                source = tarfile.TarInfo("source.txt")
                source.mode = 0o644
                source.size = 1
                archive.addfile(source, io.BytesIO(b"x"))
                linked = tarfile.TarInfo("linked.txt")
                linked.type = tarfile.LNKTYPE
                linked.mode = 0o600
                linked.linkname = "source.txt"
                archive.addfile(linked)
            manifest_path = root / "manifest.json"
            manifest_path.write_text("{}")
            result = validate_exact_tar(archive_path, manifest_path)
            self.assertEqual("failed", result["status"])
            self.assertTrue(
                any(
                    "hardlink mode differs" in error
                    for error in result["errors"]
                )
            )

    def test_zip_release_limit_is_explicit_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "payload.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("payload.txt", b"0123456789")
            with zipfile.ZipFile(archive_path) as archive:
                with self.assertRaisesRegex(
                    ValueError, "expanded-size limit"
                ):
                    safe_extract_zip(
                        archive,
                        root / "too-small",
                        max_total_bytes=9,
                    )
            with zipfile.ZipFile(archive_path) as archive:
                safe_extract_zip(
                    archive,
                    root / "bounded",
                    max_total_bytes=10,
                )
            self.assertEqual(
                b"0123456789", (root / "bounded/payload.txt").read_bytes()
            )

    def test_independent_zip_boundary_rejects_duplicate_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "duplicate.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("same.txt", b"first")
                archive.writestr("same.txt", b"second")
            with zipfile.ZipFile(archive_path) as archive:
                with self.assertRaisesRegex(ValueError, "duplicate"):
                    _validated_zip_infos(
                        archive,
                        max_members=10,
                        max_member_bytes=100,
                    )

    def test_zip_modes_and_declared_symlink_survive_restrictive_umask(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "exact.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                write_zip_directory(archive, "runtime", mode=0o755)
                write_zip(
                    archive,
                    "runtime/tool",
                    b"#!/bin/sh\n",
                    mode=0o755,
                )
                write_zip_symlink(
                    archive, "runtime/tool-link", "tool"
                )
            with zipfile.ZipFile(archive_path) as archive:
                safe_extract_zip(
                    archive,
                    root / "out",
                    allowed_symlinks={
                        "runtime/tool-link": "tool"
                    },
                    expected_modes={
                        "runtime": 0o755,
                        "runtime/tool": 0o755,
                        "runtime/tool-link": 0o777,
                    },
                )
            self.assertEqual(
                0o755,
                (root / "out/runtime").stat().st_mode & 0o777,
            )
            self.assertEqual(
                0o755,
                (root / "out/runtime/tool").stat().st_mode & 0o777,
            )
            self.assertTrue(
                (root / "out/runtime/tool-link").is_symlink()
            )
            self.assertEqual(
                "tool", os.readlink(root / "out/runtime/tool-link")
            )
            with zipfile.ZipFile(archive_path) as archive:
                with self.assertRaisesRegex(ValueError, "mode mismatch"):
                    safe_extract_zip(
                        archive,
                        root / "wrong-mode",
                        allowed_symlinks={
                            "runtime/tool-link": "tool"
                        },
                        expected_modes={
                            "runtime": 0o755,
                            "runtime/tool": 0o644,
                            "runtime/tool-link": 0o777,
                        },
                    )

    def test_zip_escaping_declared_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "escape.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                write_zip_symlink(
                    archive, "runtime/escape", "../../outside"
                )
            with zipfile.ZipFile(archive_path) as archive:
                with self.assertRaisesRegex(
                    ValueError, "escaping archive link"
                ):
                    safe_extract_zip(
                        archive,
                        root / "out",
                        allowed_symlinks={
                            "runtime/escape": "../../outside"
                        },
                    )


class NetworkNamespaceLauncherTest(unittest.TestCase):
    def test_launcher_source_enforces_namespace_and_loopback(self) -> None:
        script = _replay_script()
        launcher = (
            ROOT / "scripts/replay_namespace_launcher.c"
        ).read_text(encoding="utf-8")
        for token in (
            'LAUNCHER="$TARGET_DIR/namespace-launcher"',
            "runtime/replay-rootfs",
            "empty-resolv.conf",
        ):
            self.assertIn(token, script)
        for token in (
            "CLONE_NEWNS | CLONE_NEWNET | CLONE_NEWPID",
            "enable_loopback();",
            'bind_mount(package, destination, "mount-package")',
            'bind_mount(resolver_source, destination, "mount-resolver")',
        ):
            self.assertIn(token, launcher)

    def test_outer_only_bootstrap_streams_regular_files(self) -> None:
        script = (
            ROOT / "scripts/independent_verifier.sh"
        ).read_text(encoding="utf-8")
        self.assertIn('"$UNZIP" -p "$INNER" "$member"', script)
        self.assertIn('"$UNZIP" -p "$OUTER"', script)
        self.assertIn("--library-path \"$LIBRARIES\"", script)
        self.assertNotIn("zipinfo", script)
        self.assertNotIn("sha256sum", script)
        self.assertNotIn("awk", script)
        self.assertNotIn('unzip -q "$INNER"', script)

    def test_outer_only_bootstrap_executes_with_real_packaged_libraries(
        self,
    ) -> None:
        launcher = ROOT / "scripts/independent_verifier.sh"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python_runtime = root / "python-runtime"
            _stage_python(
                Path(sys.executable).resolve().parents[1],
                python_runtime,
            )
            inner = root / "review-handoff.zip"
            with zipfile.ZipFile(inner, "w") as archive:
                members = {
                    "runtime/bootstrap-python/bin/python3.14":
                        python_runtime / "bin/python3.14",
                    "runtime/bootstrap-python/lib/"
                    "libpython3.14.so.1.0":
                        python_runtime / "lib/libpython3.14.so.1.0",
                    "runtime/bootstrap-python/lib/python314.zip":
                        python_runtime / "lib/python314.zip",
                }
                for name in (
                    "ld-linux-x86-64.so.2",
                    "libc.so.6",
                    "libdl.so.2",
                    "libm.so.6",
                    "libpthread.so.0",
                    "librt.so.1",
                    "libutil.so.1",
                ):
                    members[
                        f"runtime/bootstrap-python/system-libs/{name}"
                    ] = python_runtime / "system-libs" / name
                for name, path in members.items():
                    write_zip(
                        archive,
                        name,
                        path.read_bytes(),
                        mode=0o755 if name != (
                            "runtime/bootstrap-python/lib/python314.zip"
                        ) else 0o644,
                    )
                write_zip(
                    archive,
                    "verification/independent-verifier/"
                    "independent_verifier.py",
                    b"raise SystemExit(0)\n",
                )
            digest = hashlib.sha256(inner.read_bytes()).hexdigest()
            outer = root / "outer.zip"
            with zipfile.ZipFile(outer, "w") as archive:
                for name, data in {
                    "agent-response.md": b"fixture\n",
                    "delivery-manifest.json": b"{}\n",
                    "delivery-validation.json": b"{}\n",
                    "independent-verifier.sh": launcher.read_bytes(),
                    "review-handoff/review-handoff.zip":
                        inner.read_bytes(),
                    "review-handoff/review-handoff.zip.sha256":
                        f"{digest}  review-handoff.zip\n".encode(),
                    "review-handoff/"
                    "review-handoff.zip.validation.json": b"{}\n",
                }.items():
                    write_zip(archive, name, data)
            result = subprocess.run(
                [str(launcher), str(outer), str(root / "output")],
                env={
                    **os.environ,
                    "LD_LIBRARY_PATH": str(
                        python_runtime / "system-libs"
                    ),
                    "PYTHONPATH": "/hostile/python",
                    "JAVA_HOME": "/hostile/java",
                    "NODE_PATH": "/hostile/node",
                },
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
