#!/usr/bin/env python3
"""Local regression tests for bounded non-solve stage supervision."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from stage_process import (  # noqa: E402
    DEFAULT_STAGE_TIMEOUTS,
    StagePolicy,
    checkpoint_fingerprint,
    checkpoint_reusable,
    run_stage,
    terminate_process_session,
)


class StageProcessTest(unittest.TestCase):
    def policy(self, *, timeout: float = 0.8, retries: int = 0) -> StagePolicy:
        return StagePolicy(
            timeouts={stage: timeout for stage in DEFAULT_STAGE_TIMEOUTS},
            retries=retries,
            monitor_interval_seconds=0.03,
            idle_warning_seconds=0.08,
            idle_termination_seconds=0.25,
        )

    def run_local(self, root: Path, code: str, *, policy: StagePolicy, activity=()):
        return run_stage(
            [sys.executable, "-c", code],
            cwd=root,
            stage="indexing",
            treatment="fake-tool",
            evidence_dir=root / "evidence",
            policy=policy,
            activity_paths=activity,
        )

    def test_slow_progressing_command_completes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.run_local(
                root,
                "import time\nfor i in range(6): print(i, flush=True); time.sleep(.06)",
                policy=self.policy(),
            )
            self.assertEqual(0, result.returncode)
            self.assertFalse(result.timed_out)
            self.assertIn("5", result.stdout)

    def test_quiet_valid_command_is_not_killed_for_idleness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_local(
                Path(tmp), "import time; time.sleep(.18)", policy=self.policy(timeout=.5)
            )
            self.assertEqual(0, result.returncode)
            events = [json.loads(line) for line in Path(result.attempts[0].diagnostics_path).read_text().splitlines()]
            self.assertTrue(any(event["idle_warning"] for event in events))

    def test_filesystem_progress_is_observed_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = root / "index"
            index.mkdir()
            code = (
                "import pathlib,time\np=pathlib.Path('index/progress')\n"
                "for i in range(5): p.write_text(str(i)); time.sleep(.06)"
            )
            result = self.run_local(root, code, policy=self.policy(), activity=(index,))
            self.assertEqual(0, result.returncode)
            events = [json.loads(line) for line in Path(result.attempts[0].diagnostics_path).read_text().splitlines()]
            self.assertTrue(any(event["last_filesystem_activity"]["newest_path"].endswith("progress") for event in events))

    def test_timeout_then_success_retries_once_in_fresh_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            counter = root / "counter"
            code = (
                "import os,pathlib,time\n"
                f"p=pathlib.Path({str(counter)!r}); n=int(p.read_text())+1 if p.exists() else 1; p.write_text(str(n))\n"
                "assert pathlib.Path(os.environ['BENCH_STAGE_ATTEMPT_WORKSPACE']).is_dir()\n"
                "time.sleep(.5) if n == 1 else None"
            )
            result = self.run_local(root, code, policy=self.policy(timeout=.15, retries=1))
            self.assertEqual(0, result.returncode)
            self.assertEqual(2, len(result.attempts))
            self.assertTrue(result.attempts[0].retry)
            self.assertNotEqual(
                Path(result.attempts[0].stdout_path).parent,
                Path(result.attempts[1].stdout_path).parent,
            )

    def test_retry_bound_prevents_third_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_local(
                Path(tmp), "import time; time.sleep(2)", policy=self.policy(timeout=.12, retries=1)
            )
            self.assertEqual(124, result.returncode)
            self.assertEqual(2, len(result.attempts))
            self.assertEqual("retry bound reached", result.attempts[-1].retry_rationale)

    def test_deterministic_failure_is_not_retried(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_local(
                Path(tmp), "import sys; print('deterministic configuration failure', file=sys.stderr); raise SystemExit(7)",
                policy=self.policy(retries=3),
            )
            self.assertEqual(7, result.returncode)
            self.assertEqual(1, len(result.attempts))
            self.assertIn("non-transient", result.attempts[0].retry_rationale)

    def test_authentication_or_unsupported_failure_is_not_retried(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_local(
                Path(tmp),
                "import sys,time; print('authentication required: unsupported account', file=sys.stderr, flush=True); time.sleep(2)",
                policy=self.policy(timeout=.12, retries=3),
            )
            self.assertEqual(124, result.returncode)
            self.assertEqual(1, len(result.attempts))
            self.assertIn("no retry", result.attempts[0].retry_rationale)

    def test_timeout_cleans_forked_descendants_and_records_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_local(
                Path(tmp),
                "import subprocess,time; subprocess.Popen(['sleep','20']); time.sleep(20)",
                policy=self.policy(timeout=.15),
            )
            self.assertEqual(124, result.returncode)
            self.assertTrue(result.attempts[0].cleanup_signals)
            self.assertEqual([], result.attempts[0].remaining_descendants)

    def test_explicit_interruption_cleanup_removes_process_session(self) -> None:
        proc = subprocess.Popen(
            [sys.executable, "-c", "import subprocess,time; subprocess.Popen(['sleep','20']); time.sleep(20)"],
            start_new_session=True,
        )
        try:
            time.sleep(.08)
            signals, remaining = terminate_process_session(proc.pid)
            proc.wait(timeout=3)
            self.assertTrue(signals)
            self.assertEqual([], remaining)
        finally:
            if proc.poll() is None:
                terminate_process_session(proc.pid)

    def test_attempt_logs_and_retry_rationale_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.run_local(
                root, "print('kept-output'); raise SystemExit(4)", policy=self.policy(retries=2)
            )
            attempt = result.attempts[0]
            self.assertIn("kept-output", Path(attempt.stdout_path).read_text())
            self.assertTrue(Path(attempt.diagnostics_path).is_file())
            payload = json.loads((root / "evidence" / "attempt-001" / "attempt.json").read_text())
            self.assertEqual("deterministic/non-transient exit; no retry", payload["retry_rationale"])
            self.assertTrue((root / "evidence" / "stage-result.json").is_file())

    def test_checkpoint_requires_complete_trust_valid_exact_match(self) -> None:
        inputs = {
            "repository": "abc",
            "issue": "5",
            "adapter": "fake",
            "tool_version": "1",
            "configuration": "x",
            "model": "m",
            "harness": "h",
        }
        checkpoint = {
            "state": "smoke_succeeded",
            "trust_valid": True,
            "inputs": inputs,
            "fingerprint": checkpoint_fingerprint(inputs),
        }
        self.assertEqual((True, "all checkpoint inputs match exactly"), checkpoint_reusable(checkpoint, inputs))
        changed = {**inputs, "model": "different"}
        self.assertEqual((False, "checkpoint inputs do not match"), checkpoint_reusable(checkpoint, changed))
        self.assertEqual((False, "checkpoint is incomplete or unsuccessful"), checkpoint_reusable({**checkpoint, "state": "setup_succeeded"}, inputs))
        self.assertEqual((False, "checkpoint is trust-invalid"), checkpoint_reusable({**checkpoint, "trust_valid": False}, inputs))

    def test_environment_policy_is_configurable_and_bounded(self) -> None:
        env = {
            **{f"BENCH_{stage.upper()}_TIMEOUT_SECONDS": "42" for stage in DEFAULT_STAGE_TIMEOUTS},
            "BENCH_STAGE_RETRIES": "3",
        }
        policy = StagePolicy.from_environment(env)
        self.assertEqual(42, policy.timeout_for("smoke"))
        self.assertEqual(3, policy.retries)
        with self.assertRaises(ValueError):
            StagePolicy.from_environment({**env, "BENCH_STAGE_RETRIES": "4"})


if __name__ == "__main__":
    unittest.main()
