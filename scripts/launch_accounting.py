"""Canonical orchestration-attempt and implementation-child accounting."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "canonical-launch-accounting-current"
LIFECYCLE_STATES = (
    "orchestration_attempt_reserved",
    "pre_spawn_validation_started",
    "pre_spawn_rejected",
    "child_process_spawned",
    "model_request_started",
    "model_response_observed",
    "child_terminal",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def new_attempt(arm_key: str, sequence: int, *, started_at: str | None = None) -> dict[str, Any]:
    started = started_at or utc_now()
    attempt_id = sha256_bytes(canonical_bytes({
        "arm_key": arm_key,
        "orchestration_sequence": sequence,
        "started_at": started,
    }))
    return {
        "attempt_id": attempt_id,
        "started_at": started,
        "finished_at": None,
        "orchestration_attempt": True,
        "pre_spawn_validation_started": False,
        "pre_spawn_rejected": False,
        "pre_spawn_rejection_reason": None,
        "child_process_spawned": False,
        "child_pid": None,
        "child_spawn_receipt": None,
        "model_request_started": False,
        "provider_response_observed": False,
        "counts_as_implementation_child_launch": False,
        "terminal": False,
        "status": "orchestration_attempt_reserved",
    }


def reserve_attempt(ledger: dict[str, Any], arm_key: str, *, started_at: str | None = None) -> dict[str, Any]:
    arm = ledger["arms"][arm_key]
    attempts = arm.setdefault("attempts", [])
    attempt = new_attempt(arm_key, len(attempts) + 1, started_at=started_at)
    attempt["pre_spawn_validation_started"] = True
    attempt["status"] = "pre_spawn_validation_started"
    attempts.append(attempt)
    arm["orchestration_attempt_count"] = len(attempts)
    ledger["orchestration_attempts"] = int(ledger.get("orchestration_attempts") or 0) + 1
    return attempt


def mark_pre_spawn_rejected(
    ledger: dict[str, Any], arm_key: str, reason: str, *, finished_at: str | None = None,
) -> dict[str, Any]:
    attempt = ledger["arms"][arm_key]["attempts"][-1]
    if attempt["child_process_spawned"]:
        raise ValueError("cannot classify a spawned child as a pre-spawn rejection")
    attempt.update({
        "finished_at": finished_at or utc_now(),
        "pre_spawn_rejected": True,
        "pre_spawn_rejection_reason": reason,
        "status": "pre_spawn_rejected",
    })
    return attempt


def child_spawn_receipt(
    arm_key: str, attempt: dict[str, Any], pid: int, *, observed_at: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "arm_key": arm_key,
        "attempt_id": attempt["attempt_id"],
        "child_pid": int(pid),
        "observed_at": observed_at or utc_now(),
        "event": "child_process_spawned",
    }
    payload["receipt_sha256"] = sha256_bytes(canonical_bytes(payload))
    return payload


def record_child_spawn(
    ledger: dict[str, Any], arm_key: str, receipt: dict[str, Any],
) -> None:
    arm = ledger["arms"][arm_key]
    attempt = arm["attempts"][-1]
    if attempt["attempt_id"] != receipt["attempt_id"]:
        raise ValueError("child-spawn receipt does not identify the active attempt")
    if attempt["pre_spawn_rejected"]:
        raise ValueError("pre-spawn rejected attempt cannot later spawn a child")
    if attempt["child_process_spawned"]:
        if attempt["child_spawn_receipt"] != receipt["receipt_sha256"]:
            raise ValueError("attempt has conflicting child-spawn receipts")
        return
    if int(ledger.get("actual_implementation_child_spawns") or 0) >= int(ledger["maximum_launches"]):
        raise ValueError("published-suite child-spawn budget exhausted")
    if int(arm.get("actual_child_spawn_count") or 0) >= int(ledger["maximum_launches_per_arm"]):
        raise ValueError("per-run actual child-spawn budget exhausted")
    attempt.update({
        "child_process_spawned": True,
        "child_pid": int(receipt["child_pid"]),
        "child_spawn_receipt": receipt["receipt_sha256"],
        "counts_as_implementation_child_launch": True,
        "status": "child_process_spawned",
    })
    arm["actual_child_spawn_count"] = int(arm.get("actual_child_spawn_count") or 0) + 1
    ledger["actual_implementation_child_spawns"] = int(
        ledger.get("actual_implementation_child_spawns") or 0
    ) + 1


def record_model_event(
    ledger: dict[str, Any], arm_key: str, *, response_observed: bool = False,
) -> None:
    attempt = ledger["arms"][arm_key]["attempts"][-1]
    if not attempt["child_process_spawned"]:
        raise ValueError("model event cannot precede child spawn")
    attempt["model_request_started"] = True
    attempt["provider_response_observed"] = bool(response_observed)
    attempt["status"] = (
        "model_response_observed" if response_observed else "model_request_started"
    )


def finish_attempt(
    ledger: dict[str, Any], arm_key: str, *, terminal: bool, status: str,
    finished_at: str | None = None,
) -> None:
    attempt = ledger["arms"][arm_key]["attempts"][-1]
    attempt["finished_at"] = finished_at or utc_now()
    attempt["terminal"] = bool(terminal)
    attempt["status"] = "child_terminal" if terminal else status
    ledger["arms"][arm_key]["terminal"] = bool(terminal)
    ledger["arms"][arm_key]["status"] = status


def validate_ledger_accounting(ledger: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    attempts = sum(len(arm.get("attempts") or []) for arm in ledger["arms"].values())
    actual = sum(int(arm.get("actual_child_spawn_count") or 0) for arm in ledger["arms"].values())
    if attempts != int(ledger.get("orchestration_attempts") or 0):
        errors.append("orchestration-attempt total does not reconcile")
    if actual != int(ledger.get("actual_implementation_child_spawns") or 0):
        errors.append("actual child-spawn total does not reconcile")
    for key, arm in ledger["arms"].items():
        counted = sum(
            bool(attempt.get("counts_as_implementation_child_launch"))
            for attempt in arm.get("attempts") or []
        )
        if counted != int(arm.get("actual_child_spawn_count") or 0):
            errors.append(f"actual child-spawn count does not reconcile for {key}")
        if counted > int(ledger["maximum_launches_per_arm"]):
            errors.append(f"per-run actual child-spawn budget exceeded for {key}")
    return errors
