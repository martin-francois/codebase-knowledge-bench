#!/usr/bin/env python3
"""Machine-local ownership lock preventing overlapping benchmark timing."""
from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator


LOCK_FD_ENV = "BENCH_SEQUENTIAL_LOCK_FD"
LOCK_PATH_ENV = "BENCH_SEQUENTIAL_LOCK_PATH"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_lock_path() -> Path:
    configured = os.environ.get(LOCK_PATH_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    return runtime / f"codebase-knowledge-bench-{os.getuid()}.lock"


@dataclass
class SequentialTimingLock:
    fd: int
    path: Path
    owner: bool
    wait_seconds: float
    acquired_at: str

    def child_environment(self) -> dict[str, str]:
        os.set_inheritable(self.fd, True)
        return {LOCK_FD_ENV: str(self.fd), LOCK_PATH_ENV: str(self.path)}

    def evidence(self) -> dict[str, object]:
        lock_owner_pid = os.getpid()
        if not self.owner:
            try:
                os.lseek(self.fd, 0, os.SEEK_SET)
                lock_owner_pid = int(json.loads(os.read(self.fd, 65536))["owner_pid"])
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                raise RuntimeError("inherited sequential timing lock has invalid owner evidence") from exc
        return {
            "schema_version": 1,
            "lock_path": str(self.path),
            "process_pid": os.getpid(),
            "lock_owner_pid": lock_owner_pid,
            "owner": self.owner,
            "inherited": not self.owner,
            "wait_seconds": self.wait_seconds,
            "acquired_at": self.acquired_at,
            "command": [Path(sys.argv[0]).name, *sys.argv[1:]],
        }

    def write_evidence(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
        temporary.write_text(json.dumps(self.evidence(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)

    def release(self) -> None:
        if self.owner:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def acquire_sequential_timing_lock(
    *, on_waiting: Callable[[], None] | None = None
) -> SequentialTimingLock:
    inherited = os.environ.get(LOCK_FD_ENV, "").strip()
    path = default_lock_path()
    if inherited:
        try:
            fd = int(inherited)
            os.fstat(fd)
        except (ValueError, OSError) as exc:
            raise RuntimeError("invalid inherited sequential timing lock descriptor") from exc
        return SequentialTimingLock(fd, path, False, 0.0, _now())

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    started = time.monotonic()
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"[benchmark] waiting for sequential timing lock: {path}", flush=True)
            if on_waiting is not None:
                on_waiting()
            fcntl.flock(fd, fcntl.LOCK_EX)
        wait_seconds = time.monotonic() - started
        acquired_at = _now()
        payload = {
            "owner_pid": os.getpid(),
            "acquired_at": acquired_at,
            "wait_seconds": wait_seconds,
            "command": [Path(sys.argv[0]).name, *sys.argv[1:]],
        }
        os.ftruncate(fd, 0)
        os.write(fd, (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"))
        os.fsync(fd)
        return SequentialTimingLock(fd, path, True, wait_seconds, acquired_at)
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def sequential_timing_lock(evidence_path: Path) -> Iterator[SequentialTimingLock]:
    lock = acquire_sequential_timing_lock()
    try:
        lock.write_evidence(evidence_path)
        yield lock
    finally:
        lock.release()
