#!/usr/bin/env python3
"""Focused source tests for final release compliance."""

from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from methodology_fixture import run_fixture  # noqa: E402


class StaticVerifierBootstrapTest(unittest.TestCase):
    def test_checked_in_binary_hash_and_elf_static_shape(self) -> None:
        binary = SCRIPTS / "independent-verifier-bootstrap"
        expected = (
            SCRIPTS / "independent-verifier-bootstrap.sha256"
        ).read_text(encoding="utf-8").split()[0]
        self.assertEqual(
            expected, hashlib.sha256(binary.read_bytes()).hexdigest()
        )
        payload = binary.read_bytes()
        self.assertEqual(b"\x7fELF", payload[:4])
        self.assertEqual(2, payload[4], "bootstrap must be ELF64")
        self.assertEqual(1, payload[5], "bootstrap must be little-endian")
        program_offset = struct.unpack_from("<Q", payload, 32)[0]
        entry_size = struct.unpack_from("<H", payload, 54)[0]
        entry_count = struct.unpack_from("<H", payload, 56)[0]
        program_types = [
            struct.unpack_from(
                "<I", payload, program_offset + index * entry_size
            )[0]
            for index in range(entry_count)
        ]
        self.assertNotIn(3, program_types, "PT_INTERP is forbidden")

    def test_bad_arguments_are_structured_under_hostile_environment(
        self,
    ) -> None:
        binary = SCRIPTS / "independent-verifier-bootstrap"
        completed = subprocess.run(
            [str(binary)],
            env={
                **os.environ,
                "LD_LIBRARY_PATH": "/missing/hostile",
                "PYTHONPATH": "/missing/python",
                "JAVA_HOME": "/missing/java",
                "NODE_PATH": "/missing/node",
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(64, completed.returncode)
        error = json.loads(completed.stderr)
        self.assertEqual("bad_arguments", error["code"])
        self.assertEqual("failed", error["status"])

    def test_official_shell_has_no_proc_exe_dependency(self) -> None:
        shell = (SCRIPTS / "independent_verifier.sh").read_text(
            encoding="utf-8"
        )
        source = (
            SCRIPTS / "independent_verifier_bootstrap.c"
        ).read_text(encoding="utf-8")
        self.assertNotIn("/proc/", shell)
        self.assertNotIn("/proc/", source)
        self.assertIn("INDEPENDENT_VERIFIER_SHELL_PATH", shell)
        self.assertIn("clearenv()", source)
        official = (
            "independent-verifier-bootstrap independent-verifier.sh "
            "OUTER_ZIP OUTPUT_ROOT"
        )
        self.assertIn(official, shell)
        self.assertIn(
            official, (ROOT / "README.md").read_text(encoding="utf-8")
        )
        self.assertIn(
            official,
            (ROOT / "docs/review-handoff.md").read_text(
                encoding="utf-8"
            ),
        )


class SourceOnlyStratumTest(unittest.TestCase):
    def test_python_policy_and_workflow_are_314_only(self) -> None:
        project = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(
            ">=3.14,<3.15", project["project"]["requires-python"]
        )
        lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
        self.assertIn('requires-python = "==3.14.*"', lock)
        workflow = (
            ROOT / ".github/workflows/ci.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('python-version: "3.14"', workflow)
        self.assertNotIn('python-version: "3.11"', workflow)
        self.assertNotIn('python-version: "3.13"', workflow)
        self.assertIn("scripts/source_only_ci.py", workflow)
        source_ci = (
            SCRIPTS / "source_only_ci.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"node_audit"', source_ci)
        self.assertIn('"--package-lock-only"', source_ci)
        self.assertIn(
            "source-only CI requires a clean plain Git checkout",
            source_ci,
        )
        self.assertNotIn('"test:browser"', source_ci)

    def test_source_only_fixture_ignores_external_target_discovery(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ,
            {
                key: value
                for key, value in os.environ.items()
                if key != "BENCH_TARGET_REPO_PATH"
            },
            clear=True,
        ):
            result = run_fixture(
                ROOT,
                artifact_root=Path(temporary) / "artifacts",
                build_browser=False,
                stratum="source-only",
            )
        self.assertEqual("passed", result["status"], result)
        self.assertEqual("source-only", result["execution_stratum"])
        self.assertTrue(
            result["stages"][
                "source_only_target_is_checked_in_fixture"
            ]
        )
        self.assertTrue(
            result["stages"][
                "source_only_injected_protected_commands"
            ]
        )


if __name__ == "__main__":
    unittest.main()
