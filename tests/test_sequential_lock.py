#!/usr/bin/env python3
"""Regression tests for machine-local sequential benchmark ownership."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from sequential_lock import (  # noqa: E402
    LOCK_FD_ENV,
    LOCK_PATH_ENV,
    acquire_sequential_timing_lock,
    sequential_timing_lock,
)


class SequentialTimingLockTest(unittest.TestCase):
    def environment(self, lock_path: Path) -> dict[str, str]:
        env = os.environ.copy()
        env.pop(LOCK_FD_ENV, None)
        env[LOCK_PATH_ENV] = str(lock_path)
        env["PYTHONPATH"] = str(SCRIPTS)
        return env

    def test_independent_benchmark_waits_until_owner_releases(self) -> None:
        for repetition in range(10):
            with self.subTest(repetition=repetition), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                lock_path = root / "timing.lock"
                marker = root / "child-acquired.json"
                evidence = root / "owner.json"
                ready_read, ready_write = os.pipe()
                code = (
                    "import json,os,pathlib\n"
                    "from sequential_lock import acquire_sequential_timing_lock\n"
                    f"ready={ready_write}\n"
                    "def waiting():\n"
                    " os.write(ready,b'waiting\\n'); os.close(ready)\n"
                    "lock=acquire_sequential_timing_lock(on_waiting=waiting)\n"
                    f"pathlib.Path({str(marker)!r}).write_text(json.dumps(lock.evidence()))\n"
                    "lock.release()"
                )
                try:
                    with mock.patch.dict(
                        os.environ, self.environment(lock_path), clear=True
                    ):
                        with sequential_timing_lock(evidence):
                            child = subprocess.Popen(
                                [sys.executable, "-c", code],
                                env=self.environment(lock_path),
                                pass_fds=(ready_write,),
                            )
                            os.close(ready_write)
                            ready_write = -1
                            self.assertEqual(b"waiting\n", os.read(ready_read, 8))
                            self.assertIsNone(child.poll())
                            self.assertFalse(marker.exists())
                        self.assertEqual(0, child.wait(timeout=3))
                finally:
                    os.close(ready_read)
                    if ready_write >= 0:
                        os.close(ready_write)
                payload = json.loads(marker.read_text(encoding="utf-8"))
                self.assertGreater(payload["wait_seconds"], 0)
                self.assertTrue(payload["owner"])

    def test_suite_child_adopts_inherited_descriptor_without_waiting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock_path = root / "timing.lock"
            with mock.patch.dict(os.environ, self.environment(lock_path), clear=True):
                owner = acquire_sequential_timing_lock()
                try:
                    env = self.environment(lock_path)
                    env.update(owner.child_environment())
                    code = (
                        "import json\nfrom sequential_lock import acquire_sequential_timing_lock\n"
                        "lock=acquire_sequential_timing_lock(); print(json.dumps(lock.evidence())); lock.release()"
                    )
                    child = subprocess.run(
                        [sys.executable, "-c", code],
                        env=env,
                        pass_fds=(owner.fd,),
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=3,
                    )
                finally:
                    owner.release()
            self.assertEqual(0, child.returncode, child.stderr)
            payload = json.loads(child.stdout)
            self.assertFalse(payload["owner"])
            self.assertTrue(payload["inherited"])
            self.assertEqual(0, payload["wait_seconds"])

    def test_invalid_inherited_descriptor_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self.environment(Path(tmp) / "timing.lock")
            env[LOCK_FD_ENV] = "999999"
            with mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(RuntimeError, "invalid inherited"):
                    acquire_sequential_timing_lock()

    def test_lock_evidence_records_owner_and_wait(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / "evidence.json"
            with mock.patch.dict(
                os.environ, self.environment(root / "timing.lock"), clear=True
            ):
                with sequential_timing_lock(evidence):
                    payload = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertTrue(payload["owner"])
            self.assertFalse(payload["inherited"])
            self.assertIn("wait_seconds", payload)


if __name__ == "__main__":
    unittest.main()
