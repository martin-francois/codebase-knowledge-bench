#!/usr/bin/env python3
"""Bounded, observable supervision for non-solve benchmark processes."""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable


DEFAULT_STAGE_TIMEOUTS = {
    "installation": 1800,
    "setup": 1800,
    "indexing": 1800,
    "smoke": 900,
    "verification": 1800,
    "validation": 600,
    "report": 600,
}
STAGE_ENV_NAMES = {
    stage: f"BENCH_{stage.upper()}_TIMEOUT_SECONDS" for stage in DEFAULT_STAGE_TIMEOUTS
}
MAX_STAGE_RETRIES = 3
NON_RETRYABLE_FAILURE = re.compile(
    r"authentication|unauthori[sz]ed|forbidden|unsupported|assertion|invalid credential|"
    r"permission denied|trust violation|leakage",
    re.IGNORECASE,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class StagePolicy:
    timeouts: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_STAGE_TIMEOUTS))
    retries: int = 1
    monitor_interval_seconds: float = 30.0
    idle_warning_seconds: float = 300.0
    terminate_on_idle: bool = False
    idle_termination_seconds: float = 1800.0

    @classmethod
    def from_environment(cls, env: dict[str, str] | None = None) -> "StagePolicy":
        source = env if env is not None else os.environ
        timeouts = {
            stage: float(source.get(STAGE_ENV_NAMES[stage], default))
            for stage, default in DEFAULT_STAGE_TIMEOUTS.items()
        }
        policy = cls(
            timeouts=timeouts,
            retries=int(source.get("BENCH_STAGE_RETRIES", "1")),
            monitor_interval_seconds=float(source.get("BENCH_STAGE_MONITOR_INTERVAL_SECONDS", "30")),
            idle_warning_seconds=float(source.get("BENCH_STAGE_IDLE_WARNING_SECONDS", "300")),
            terminate_on_idle=source.get("BENCH_STAGE_TERMINATE_ON_IDLE", "false").lower() == "true",
            idle_termination_seconds=float(source.get("BENCH_STAGE_IDLE_TERMINATION_SECONDS", "1800")),
        )
        policy.validate()
        return policy

    def validate(self) -> None:
        missing = sorted(set(DEFAULT_STAGE_TIMEOUTS) - set(self.timeouts))
        if missing or any(float(value) <= 0 for value in self.timeouts.values()):
            raise ValueError(f"stage timeouts must be positive and complete; missing={missing}")
        if not 0 <= self.retries <= MAX_STAGE_RETRIES:
            raise ValueError(f"stage retries must be between 0 and {MAX_STAGE_RETRIES}")
        if self.monitor_interval_seconds <= 0 or self.idle_warning_seconds <= 0:
            raise ValueError("stage monitor and idle warning intervals must be positive")
        if self.idle_termination_seconds < self.idle_warning_seconds:
            raise ValueError("idle termination interval must not be shorter than idle warning interval")

    def timeout_for(self, stage: str) -> float:
        if stage not in self.timeouts:
            raise ValueError(f"unknown supervised stage: {stage}")
        return float(self.timeouts[stage])

    def as_dict(self) -> dict[str, object]:
        return {
            "timeouts_seconds": dict(sorted(self.timeouts.items())),
            "automatic_retries": self.retries,
            "maximum_automatic_retries": MAX_STAGE_RETRIES,
            "maximum_total_attempts": self.retries + 1,
            "monitor_interval_seconds": self.monitor_interval_seconds,
            "idle_warning_seconds": self.idle_warning_seconds,
            "terminate_on_idle": self.terminate_on_idle,
            "idle_termination_seconds": self.idle_termination_seconds,
        }


@dataclass
class StageAttempt:
    attempt: int
    returncode: int
    timed_out: bool
    interrupted: bool
    elapsed_seconds: float
    stdout_path: str
    stderr_path: str
    diagnostics_path: str
    cleanup_signals: list[str]
    remaining_descendants: list[int]
    retry: bool
    retry_rationale: str


@dataclass
class StageResult:
    command: list[str] | str
    cwd: str
    stage: str
    treatment: str
    returncode: int
    stdout: str
    stderr: str
    seconds: float
    timed_out: bool
    attempts: list[StageAttempt]


def _descendants(root_pid: int) -> list[int]:
    children: dict[int, list[int]] = {}
    proc = Path("/proc")
    if not proc.is_dir():
        return []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text(encoding="utf-8").split()
            children.setdefault(int(fields[3]), []).append(int(entry.name))
        except (OSError, ValueError, IndexError):
            continue
    found: list[int] = []
    pending = list(children.get(root_pid, []))
    while pending:
        pid = pending.pop()
        if pid in found:
            continue
        found.append(pid)
        pending.extend(children.get(pid, []))
    return sorted(found)


def _process_sample(root_pid: int) -> dict[str, object]:
    pids = [root_pid, *_descendants(root_pid)]
    cpu_ticks = 0
    rss_kib = 0
    live: list[dict[str, object]] = []
    for pid in pids:
        try:
            stat = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8").split()
            status = (Path("/proc") / str(pid) / "status").read_text(encoding="utf-8")
            cpu_ticks += int(stat[13]) + int(stat[14])
            rss_line = next((line for line in status.splitlines() if line.startswith("VmRSS:")), "")
            rss = int(rss_line.split()[1]) if rss_line else 0
            rss_kib += rss
            live.append({"pid": pid, "ppid": int(stat[3]), "cpu_ticks": int(stat[13]) + int(stat[14]), "rss_kib": rss})
        except (OSError, ValueError, IndexError, StopIteration):
            continue
    return {"processes": live, "cpu_ticks": cpu_ticks, "rss_kib": rss_kib}


def _filesystem_activity(paths: Iterable[Path]) -> dict[str, object]:
    newest_ns = 0
    newest_path = ""
    for root in paths:
        candidates = [root]
        if root.is_dir():
            try:
                candidates.extend(root.rglob("*"))
            except OSError:
                pass
        for path in candidates:
            try:
                stamp = path.stat().st_mtime_ns
            except OSError:
                continue
            if stamp > newest_ns:
                newest_ns, newest_path = stamp, str(path)
    return {"newest_mtime_ns": newest_ns, "newest_path": newest_path}


def _reader(stream, channel: str, events: queue.Queue[tuple[str, str]], sink: Path) -> None:
    with sink.open("w", encoding="utf-8") as output:
        for chunk in iter(lambda: stream.readline(), ""):
            output.write(chunk)
            output.flush()
            events.put((channel, chunk))
    stream.close()


def terminate_process_session(pid: int) -> tuple[list[str], list[int]]:
    sent: list[str] = []
    for sig, wait_seconds in ((signal.SIGINT, 1.0), (signal.SIGTERM, 1.0), (signal.SIGKILL, 1.0)):
        if not Path(f"/proc/{pid}").exists() and not _descendants(pid):
            break
        try:
            os.killpg(pid, sig)
            sent.append(signal.Signals(sig).name)
        except ProcessLookupError:
            break
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline and (Path(f"/proc/{pid}").exists() or _descendants(pid)):
            time.sleep(0.02)
    return sent, _descendants(pid)


def run_stage(
    command: list[str] | str,
    *,
    cwd: Path,
    stage: str,
    evidence_dir: Path,
    treatment: str = "orchestrator",
    policy: StagePolicy | None = None,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    activity_paths: Iterable[Path] = (),
    retryable_returncodes: Iterable[int] = (),
    prepare_attempt: Callable[[int, Path], Path] | None = None,
    sanitize: Callable[[str], str] = lambda value: value,
) -> StageResult:
    policy = policy or StagePolicy.from_environment()
    timeout = policy.timeout_for(stage)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    attempts: list[StageAttempt] = []
    total_started = time.monotonic()
    final_stdout = final_stderr = ""
    final_code = 1
    final_timed_out = False
    retryable_codes = set(retryable_returncodes)
    for attempt_number in range(1, policy.retries + 2):
        attempt_dir = evidence_dir / f"attempt-{attempt_number:03d}"
        workspace = attempt_dir / "workspace"
        workspace.mkdir(parents=True, exist_ok=False)
        attempt_cwd = prepare_attempt(attempt_number, workspace) if prepare_attempt else cwd
        stdout_path, stderr_path = attempt_dir / "stdout.log", attempt_dir / "stderr.log"
        diagnostics_path = attempt_dir / "progress.jsonl"
        attempt_env = dict(env or os.environ)
        attempt_env["BENCH_STAGE_ATTEMPT_WORKSPACE"] = str(workspace)
        started = time.monotonic()
        started_at = utc_now()
        proc = subprocess.Popen(
            command,
            cwd=attempt_cwd,
            env=attempt_env,
            stdin=subprocess.PIPE if input_text is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=isinstance(command, str),
            start_new_session=True,
        )
        if input_text is not None and proc.stdin:
            proc.stdin.write(input_text)
            proc.stdin.close()
        events: queue.Queue[tuple[str, str]] = queue.Queue()
        threads = [
            threading.Thread(target=_reader, args=(proc.stdout, "stdout", events, stdout_path), daemon=True),
            threading.Thread(target=_reader, args=(proc.stderr, "stderr", events, stderr_path), daemon=True),
        ]
        for thread in threads:
            thread.start()
        last_activity = started
        last_cpu = -1
        last_fs_mtime = _filesystem_activity(activity_paths)["newest_mtime_ns"]
        next_sample = started
        output_bytes = {"stdout": 0, "stderr": 0}
        idle_warned_at = 0.0
        cleanup_signals: list[str] = []
        remaining: list[int] = []
        timed_out = interrupted = idle_terminated = False
        try:
            while proc.poll() is None:
                now = time.monotonic()
                if now < next_sample:
                    time.sleep(min(next_sample - now, 0.1))
                    continue
                next_sample = now + policy.monitor_interval_seconds
                while True:
                    try:
                        channel, chunk = events.get_nowait()
                    except queue.Empty:
                        break
                    output_bytes[channel] += len(chunk.encode("utf-8", errors="replace"))
                    last_activity = time.monotonic()
                sample = _process_sample(proc.pid)
                fs = _filesystem_activity(activity_paths)
                if int(fs["newest_mtime_ns"]) > int(last_fs_mtime):
                    last_fs_mtime = int(fs["newest_mtime_ns"])
                    last_activity = time.monotonic()
                cpu_changed = sample["cpu_ticks"] != last_cpu
                if cpu_changed:
                    last_cpu = int(sample["cpu_ticks"])
                    last_activity = time.monotonic()
                elapsed = time.monotonic() - started
                idle = time.monotonic() - last_activity
                event = {
                    "kind": "progress",
                    "at": utc_now(),
                    "stage": stage,
                    "treatment": treatment,
                    "attempt": attempt_number,
                    "elapsed_seconds": elapsed,
                    "configured_timeout_seconds": timeout,
                    "process_session_id": proc.pid,
                    "process_tree": sample["processes"],
                    "cpu_ticks": sample["cpu_ticks"],
                    "rss_kib": sample["rss_kib"],
                    "stdout_bytes": output_bytes["stdout"],
                    "stderr_bytes": output_bytes["stderr"],
                    "last_filesystem_activity": fs,
                    "idle_seconds": idle,
                    "idle_warning": idle >= policy.idle_warning_seconds,
                }
                with diagnostics_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event, sort_keys=True) + "\n")
                if event["idle_warning"] and time.monotonic() - idle_warned_at >= policy.idle_warning_seconds:
                    idle_warned_at = time.monotonic()
                if elapsed >= timeout:
                    timed_out = True
                    cleanup_signals, remaining = terminate_process_session(proc.pid)
                    break
                if policy.terminate_on_idle and idle >= policy.idle_termination_seconds and not cpu_changed:
                    idle_terminated = True
                    cleanup_signals, remaining = terminate_process_session(proc.pid)
                    break
        except BaseException:
            interrupted = True
            cleanup_signals, remaining = terminate_process_session(proc.pid)
            raise
        finally:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                extra, remaining = terminate_process_session(proc.pid)
                cleanup_signals.extend(extra)
                proc.wait()
            for thread in threads:
                thread.join(timeout=1)
        final_stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
        final_stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
        final_code = 124 if timed_out or idle_terminated else int(proc.returncode or 0)
        final_timed_out = timed_out
        explicit_non_retryable = bool(NON_RETRYABLE_FAILURE.search(final_stdout + "\n" + final_stderr))
        transient = (timed_out or idle_terminated or final_code in retryable_codes) and not explicit_non_retryable
        can_retry = transient and attempt_number <= policy.retries and not remaining
        rationale = (
            "authentication, unsupported capability, assertion, or trust failure; no retry"
            if explicit_non_retryable
            else
            "confirmed timeout; retrying in fresh attempt workspace" if timed_out and can_retry
            else "confirmed transient failure; retrying in fresh attempt workspace" if can_retry
            else "retry bound reached" if transient
            else "deterministic/non-transient exit; no retry"
        )
        attempts.append(StageAttempt(
            attempt=attempt_number,
            returncode=final_code,
            timed_out=timed_out,
            interrupted=interrupted,
            elapsed_seconds=time.monotonic() - started,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            diagnostics_path=str(diagnostics_path),
            cleanup_signals=cleanup_signals,
            remaining_descendants=remaining,
            retry=can_retry,
            retry_rationale=rationale,
        ))
        (attempt_dir / "attempt.json").write_text(
            json.dumps({
                **asdict(attempts[-1]),
                "stage": stage,
                "treatment": treatment,
                "command": sanitize(str(command)),
                "started_at": started_at,
                "ended_at": utc_now(),
                "configured_timeout_seconds": timeout,
            }, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if not can_retry:
            break
    result = StageResult(
        command=command,
        cwd=str(cwd),
        stage=stage,
        treatment=treatment,
        returncode=final_code,
        stdout=final_stdout,
        stderr=final_stderr,
        seconds=time.monotonic() - total_started,
        timed_out=final_timed_out,
        attempts=attempts,
    )
    (evidence_dir / "stage-result.json").write_text(
        json.dumps({**asdict(result), "command": sanitize(str(command)), "policy": policy.as_dict()}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def checkpoint_fingerprint(inputs: dict[str, object]) -> str:
    payload = json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def checkpoint_reusable(checkpoint: dict[str, object], expected_inputs: dict[str, object]) -> tuple[bool, str]:
    if checkpoint.get("state") != "smoke_succeeded":
        return False, "checkpoint is incomplete or unsuccessful"
    if checkpoint.get("trust_valid") is not True:
        return False, "checkpoint is trust-invalid"
    expected = checkpoint_fingerprint(expected_inputs)
    if checkpoint.get("fingerprint") != expected or checkpoint.get("inputs") != expected_inputs:
        return False, "checkpoint inputs do not match"
    return True, "all checkpoint inputs match exactly"
