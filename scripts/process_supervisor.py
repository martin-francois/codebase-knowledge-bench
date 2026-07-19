#!/usr/bin/env python3
"""Own, terminate, and reap one subprocess tree in an isolated process."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PR_SET_CHILD_SUBREAPER = 36
TERM_GRACE_SECONDS = 0.5
KILL_GRACE_SECONDS = 2.0


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start_time: int
    parent_pid: int
    process_group: int
    session_id: int
    state: str


class SupervisorInterrupted(Exception):
    def __init__(self, signum: int):
        super().__init__(signal.Signals(signum).name)
        self.signum = signum


def _process_identity(pid: int) -> ProcessIdentity | None:
    try:
        raw = (Path("/proc") / str(pid) / "stat").read_text(
            encoding="utf-8"
        )
        fields = raw[raw.rfind(") ") + 2 :].split()
        return ProcessIdentity(
            pid=pid,
            state=fields[0],
            parent_pid=int(fields[1]),
            process_group=int(fields[2]),
            session_id=int(fields[3]),
            start_time=int(fields[19]),
        )
    except (OSError, ValueError, IndexError):
        return None


def _process_table() -> dict[int, ProcessIdentity]:
    table: dict[int, ProcessIdentity] = {}
    proc = Path("/proc")
    if not proc.is_dir():
        return table
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        identity = _process_identity(int(entry.name))
        if identity is not None:
            table[identity.pid] = identity
    return table


def _owned_processes(owner_pid: int) -> dict[int, ProcessIdentity]:
    table = _process_table()
    children: dict[int, list[int]] = {}
    for identity in table.values():
        children.setdefault(identity.parent_pid, []).append(identity.pid)
    owned: dict[int, ProcessIdentity] = {}
    pending = list(children.get(owner_pid, []))
    while pending:
        pid = pending.pop()
        if pid in owned or pid == owner_pid:
            continue
        identity = table.get(pid)
        if identity is None:
            continue
        owned[pid] = identity
        pending.extend(children.get(pid, []))
    return owned


def _signal_identity(
    identity: ProcessIdentity, signum: int
) -> bool:
    """Signal only the exact observed process, never a reused PID."""
    current = _process_identity(identity.pid)
    if (
        current is None
        or current.start_time != identity.start_time
        or current.state == "Z"
    ):
        return False
    try:
        os.kill(identity.pid, signum)
        return True
    except ProcessLookupError:
        return False


def _signal_tree(
    owner_pid: int,
    command_identity: ProcessIdentity,
    signum: int,
) -> list[dict[str, Any]]:
    signaled: list[dict[str, Any]] = []
    current_command = _process_identity(command_identity.pid)
    owned = _owned_processes(owner_pid)
    command_group_owned = any(
        identity.process_group == command_identity.pid
        and identity.session_id == command_identity.session_id
        for identity in owned.values()
    )
    if (
        (
            current_command is not None
            and current_command.start_time == command_identity.start_time
            and current_command.process_group == command_identity.pid
        )
        or command_group_owned
    ):
        try:
            os.killpg(command_identity.pid, signum)
            signaled.append(
                {
                    "scope": "command_session",
                    "signal": signal.Signals(signum).name,
                    "process_group": command_identity.pid,
                }
            )
        except ProcessLookupError:
            pass
    for identity in sorted(
        owned.values(),
        key=lambda value: value.pid,
        reverse=True,
    ):
        if _signal_identity(identity, signum):
            signaled.append(
                {
                    "scope": (
                        "command_descendant"
                        if identity.session_id
                        == command_identity.session_id
                        else "escaped_descendant"
                    ),
                    "signal": signal.Signals(signum).name,
                    "pid": identity.pid,
                    "start_time": identity.start_time,
                }
            )
    return signaled


def _live_owned(owner_pid: int) -> dict[int, ProcessIdentity]:
    return {
        pid: identity
        for pid, identity in _owned_processes(owner_pid).items()
        if identity.state != "Z"
    }


def _wait_for_live_exit(owner_pid: int, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while _live_owned(owner_pid) and time.monotonic() < deadline:
        time.sleep(0.01)


def _reap_adopted(owner_pid: int) -> list[dict[str, int]]:
    """Reap only children adopted by this command-specific supervisor."""
    reaped: list[dict[str, int]] = []
    deadline = time.monotonic() + KILL_GRACE_SECONDS
    while True:
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                pid = 0
                break
            if pid == 0:
                break
            reaped.append({"pid": pid, "status": status})
        owned = _owned_processes(owner_pid)
        if not owned:
            return reaped
        if time.monotonic() >= deadline:
            return reaped
        for identity in owned.values():
            _signal_identity(identity, signal.SIGKILL)
        time.sleep(0.01)


def _enable_subreaper() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _write_receipt(fd: int, value: dict[str, Any]) -> None:
    payload = (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")
    while payload:
        written = os.write(fd, payload)
        payload = payload[written:]
    os.close(fd)


def supervise(config: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    _enable_subreaper()
    owner_pid = os.getpid()
    command = config["command"]
    process = subprocess.Popen(
        command,
        cwd=config["cwd"],
        env=config.get("env"),
        stdin=(
            subprocess.PIPE
            if config.get("input_text") is not None
            else None
        ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=isinstance(command, str),
        start_new_session=True,
    )
    command_identity = _process_identity(process.pid)
    if command_identity is None:
        process.kill()
        process.wait()
        raise RuntimeError("command process identity unavailable")
    cleanup: list[dict[str, Any]] = []
    timed_out = False
    interrupted_signal: int | None = None
    try:
        stdout, stderr = process.communicate(
            input=config.get("input_text"),
            timeout=config.get("timeout"),
        )
    except subprocess.TimeoutExpired:
        timed_out = True
        cleanup.extend(
            _signal_tree(owner_pid, command_identity, signal.SIGTERM)
        )
        _wait_for_live_exit(owner_pid, TERM_GRACE_SECONDS)
        if _live_owned(owner_pid):
            cleanup.extend(
                _signal_tree(owner_pid, command_identity, signal.SIGKILL)
            )
        stdout, stderr = process.communicate()
    except SupervisorInterrupted as exc:
        interrupted_signal = exc.signum
        cleanup.extend(
            _signal_tree(owner_pid, command_identity, signal.SIGTERM)
        )
        _wait_for_live_exit(owner_pid, TERM_GRACE_SECONDS)
        if _live_owned(owner_pid):
            cleanup.extend(
                _signal_tree(owner_pid, command_identity, signal.SIGKILL)
            )
        stdout, stderr = process.communicate()
    residual = _owned_processes(owner_pid)
    if residual:
        cleanup.extend(
            _signal_tree(owner_pid, command_identity, signal.SIGTERM)
        )
        _wait_for_live_exit(owner_pid, TERM_GRACE_SECONDS)
        if _live_owned(owner_pid):
            cleanup.extend(
                _signal_tree(owner_pid, command_identity, signal.SIGKILL)
            )
    reaped = _reap_adopted(owner_pid)
    remaining = [
        asdict(identity)
        for identity in _owned_processes(owner_pid).values()
    ]
    returncode = (
        124
        if timed_out
        else 128 + interrupted_signal
        if interrupted_signal is not None
        else int(process.returncode)
    )
    return (
        stdout or "",
        stderr or "",
        {
            "returncode": returncode,
            "timed_out": timed_out,
            "cleanup": cleanup,
            "reaped": reaped,
            "remaining_descendants": remaining,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt-fd", type=int, required=True)
    args = parser.parse_args()

    def interrupted(signum: int, _frame: Any) -> None:
        raise SupervisorInterrupted(signum)

    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    try:
        config = json.load(sys.stdin)
        stdout, stderr, receipt = supervise(config)
        sys.stdout.write(stdout)
        sys.stderr.write(stderr)
        _write_receipt(args.receipt_fd, receipt)
        return 0
    except BaseException as exc:
        try:
            _write_receipt(
                args.receipt_fd,
                {
                    "supervisor_error": (
                        f"{type(exc).__name__}: {exc}"
                    )
                },
            )
        except OSError:
            pass
        print(f"process supervisor failed: {exc}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
