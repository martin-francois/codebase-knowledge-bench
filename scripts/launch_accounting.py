"""Published orchestration-attempt and implementation-child accounting."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "published-launch-accounting-current"
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


def normalized_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def new_attempt(
    run_key: str,
    sequence: int,
    *,
    invocation_id: str,
    started_at: str | None = None,
) -> dict[str, Any]:
    started = started_at or utc_now()
    attempt_id = sha256_bytes(normalized_bytes({
        "run_key": run_key,
        "orchestration_sequence": sequence,
        "invocation_id": invocation_id,
        "started_at": started,
    }))
    return {
        "attempt_id": attempt_id,
        "invocation_id": invocation_id,
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


def reserve_attempt(ledger: dict[str, Any], run_key: str, *, started_at: str | None = None) -> dict[str, Any]:
    run = ledger["runs"][run_key]
    attempts = run.setdefault("attempts", [])
    invocation_id = str(ledger.get("current_invocation_id") or "")
    if not invocation_id:
        raise ValueError("published-suite ledger has no current invocation")
    attempt = new_attempt(
        run_key,
        len(attempts) + 1,
        invocation_id=invocation_id,
        started_at=started_at,
    )
    attempt["pre_spawn_validation_started"] = True
    attempt["status"] = "pre_spawn_validation_started"
    attempts.append(attempt)
    run["orchestration_attempt_count"] = len(attempts)
    ledger["orchestration_attempts"] = int(ledger.get("orchestration_attempts") or 0) + 1
    return attempt


def mark_pre_spawn_rejected(
    ledger: dict[str, Any], run_key: str, reason: str, *, finished_at: str | None = None,
) -> dict[str, Any]:
    attempt = ledger["runs"][run_key]["attempts"][-1]
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
    run_key: str, attempt: dict[str, Any], pid: int, *, observed_at: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "run_key": run_key,
        "attempt_id": attempt["attempt_id"],
        "child_pid": int(pid),
        "observed_at": observed_at or utc_now(),
        "event": "child_process_spawned",
    }
    payload["receipt_sha256"] = sha256_bytes(normalized_bytes(payload))
    return payload


def record_child_spawn(
    ledger: dict[str, Any], run_key: str, receipt: dict[str, Any],
) -> None:
    run = ledger["runs"][run_key]
    attempt = run["attempts"][-1]
    if attempt["attempt_id"] != receipt["attempt_id"]:
        raise ValueError("child-spawn receipt does not identify the active attempt")
    if attempt["pre_spawn_rejected"]:
        raise ValueError("pre-spawn rejected attempt cannot later spawn a child")
    if attempt["child_process_spawned"]:
        if attempt["child_spawn_receipt"] != receipt["receipt_sha256"]:
            raise ValueError("attempt has conflicting child-spawn receipts")
        return
    invocation_id = str(ledger.get("current_invocation_id") or "")
    invocation = next(
        (
            item
            for item in ledger.get("invocations", [])
            if item.get("invocation_id") == invocation_id
        ),
        None,
    )
    if invocation is None:
        raise ValueError("published-suite current invocation is missing")
    if int(invocation.get("actual_child_spawns") or 0) >= int(ledger["maximum_launches"]):
        raise ValueError("published-suite per-invocation child-spawn budget exhausted")
    invocation_run_spawns = sum(
        bool(item.get("counts_as_implementation_child_launch"))
        and item.get("invocation_id") == invocation_id
        for item in run.get("attempts", [])
    )
    if invocation_run_spawns >= int(ledger["maximum_launches_per_run"]):
        raise ValueError("per-invocation per-run child-spawn budget exhausted")
    attempt.update({
        "child_process_spawned": True,
        "child_pid": int(receipt["child_pid"]),
        "child_spawn_receipt": receipt["receipt_sha256"],
        "counts_as_implementation_child_launch": True,
        "status": "child_process_spawned",
    })
    run["actual_child_spawn_count"] = int(run.get("actual_child_spawn_count") or 0) + 1
    ledger["actual_implementation_child_spawns"] = int(
        ledger.get("actual_implementation_child_spawns") or 0
    ) + 1
    invocation["actual_child_spawns"] = int(
        invocation.get("actual_child_spawns") or 0
    ) + 1


def record_model_event(
    ledger: dict[str, Any], run_key: str, *, response_observed: bool = False,
) -> None:
    attempt = ledger["runs"][run_key]["attempts"][-1]
    if not attempt["child_process_spawned"]:
        raise ValueError("model event cannot precede child spawn")
    attempt["model_request_started"] = True
    attempt["provider_response_observed"] = bool(response_observed)
    attempt["status"] = (
        "model_response_observed" if response_observed else "model_request_started"
    )


def finish_attempt(
    ledger: dict[str, Any], run_key: str, *, terminal: bool, status: str,
    finished_at: str | None = None,
) -> None:
    attempt = ledger["runs"][run_key]["attempts"][-1]
    attempt["finished_at"] = finished_at or utc_now()
    attempt["terminal"] = bool(terminal)
    attempt["status"] = "child_terminal" if terminal else status
    ledger["runs"][run_key]["terminal"] = bool(terminal)
    ledger["runs"][run_key]["status"] = status


def validate_ledger_accounting(ledger: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(ledger, dict):
        return ["execution ledger is not an object"]
    runs = ledger.get("runs")
    planned = ledger.get("planned_run_keys")
    invocations = ledger.get("invocations")
    if not isinstance(runs, dict):
        return ["execution ledger runs are malformed"]
    if (
        not isinstance(planned, list)
        or not all(isinstance(key, str) and key for key in planned)
        or len(planned) != len(set(planned))
        or set(planned) != set(runs)
    ):
        errors.append("planned run keys do not reconcile with ledger runs")
    maximum_unique_runs = ledger.get("maximum_unique_runs")
    maximum_launches = ledger.get("maximum_launches")
    maximum_launches_per_run = ledger.get("maximum_launches_per_run")
    if (
        not isinstance(maximum_unique_runs, int)
        or isinstance(maximum_unique_runs, bool)
        or maximum_unique_runs <= 0
        or maximum_unique_runs != len(runs)
    ):
        errors.append("maximum unique-run budget does not reconcile")
    for label, value in (
        ("maximum child-launch", maximum_launches),
        ("per-run child-launch", maximum_launches_per_run),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            errors.append(f"{label} budget is invalid")
    if not isinstance(invocations, list) or not invocations:
        errors.append("ledger invocation history is malformed")
        invocations = []
    invocation_ids: list[str] = []
    invocation_by_id: dict[str, dict[str, Any]] = {}
    profile = ledger.get("profile")
    for expected_sequence, invocation in enumerate(invocations, 1):
        if not isinstance(invocation, dict):
            errors.append("ledger invocation is malformed")
            continue
        invocation_id = invocation.get("invocation_id")
        started_at = invocation.get("started_at")
        sequence = invocation.get("sequence")
        if (
            not isinstance(invocation_id, str)
            or not re.fullmatch(r"[0-9a-f]{64}", invocation_id)
            or invocation_id in invocation_by_id
        ):
            errors.append("ledger invocation identity is malformed or duplicated")
            continue
        invocation_ids.append(invocation_id)
        invocation_by_id[invocation_id] = invocation
        if sequence != expected_sequence or not isinstance(started_at, str) or not started_at:
            errors.append(f"ledger invocation sequence or timestamp is invalid: {invocation_id}")
        else:
            expected_id = sha256_bytes(
                normalized_bytes(
                    {
                        "sequence": sequence,
                        "started_at": started_at,
                        "profile_sha256": sha256_bytes(normalized_bytes(profile)),
                    }
                )
            )
            if invocation_id != expected_id:
                errors.append(f"ledger invocation content identity does not reconcile: {invocation_id}")
        if (
            invocation.get("maximum_child_spawns") != maximum_launches
            or invocation.get("maximum_child_spawns_per_run")
            != maximum_launches_per_run
            or invocation.get("limit_scope") != "this_coordinator_invocation_only"
        ):
            errors.append(f"ledger invocation budgets do not reconcile: {invocation_id}")
    current_invocation_id = ledger.get("current_invocation_id")
    if (
        not isinstance(current_invocation_id, str)
        or not invocation_ids
        or current_invocation_id != invocation_ids[-1]
    ):
        errors.append("current ledger invocation does not identify the latest invocation")

    attempts = 0
    actual = 0
    seen_attempt_ids: set[str] = set()
    observed_by_invocation = {invocation_id: 0 for invocation_id in invocation_ids}
    for key, run in runs.items():
        if not isinstance(key, str) or not isinstance(run, dict):
            errors.append("ledger run entry is malformed")
            continue
        run_attempts = run.get("attempts")
        if not isinstance(run_attempts, list):
            errors.append(f"attempt list is malformed for {key}")
            continue
        attempts += len(run_attempts)
        if run.get("orchestration_attempt_count") != len(run_attempts):
            errors.append(f"orchestration-attempt count does not reconcile for {key}")
        run_actual = run.get("actual_child_spawn_count")
        if not isinstance(run_actual, int) or isinstance(run_actual, bool) or run_actual < 0:
            errors.append(f"actual child-spawn count is malformed for {key}")
            run_actual = 0
        actual += run_actual
        counted = sum(
            bool(attempt.get("counts_as_implementation_child_launch"))
            for attempt in run_attempts if isinstance(attempt, dict)
        )
        if counted != run_actual:
            errors.append(f"actual child-spawn count does not reconcile for {key}")
        per_invocation: dict[str, int] = {}
        for index, attempt in enumerate(run_attempts, 1):
            if not isinstance(attempt, dict):
                errors.append(f"attempt {index} is malformed for {key}")
                continue
            invocation_id = str(attempt.get("invocation_id") or "")
            attempt_id = attempt.get("attempt_id")
            started_at = attempt.get("started_at")
            if invocation_id not in invocation_by_id:
                errors.append(f"attempt {index} names an unknown invocation for {key}")
            if (
                not isinstance(attempt_id, str)
                or not re.fullmatch(r"[0-9a-f]{64}", attempt_id)
                or attempt_id in seen_attempt_ids
            ):
                errors.append(f"attempt {index} identity is malformed or duplicated for {key}")
            else:
                seen_attempt_ids.add(attempt_id)
                if isinstance(started_at, str) and started_at:
                    expected_attempt_id = sha256_bytes(
                        normalized_bytes(
                            {
                                "run_key": key,
                                "orchestration_sequence": index,
                                "invocation_id": invocation_id,
                                "started_at": started_at,
                            }
                        )
                    )
                    if attempt_id != expected_attempt_id:
                        errors.append(f"attempt {index} content identity does not reconcile for {key}")
            if not isinstance(started_at, str) or not started_at:
                errors.append(f"attempt {index} start timestamp is invalid for {key}")
            boolean_fields = (
                "orchestration_attempt", "pre_spawn_validation_started",
                "pre_spawn_rejected", "child_process_spawned",
                "model_request_started", "provider_response_observed",
                "counts_as_implementation_child_launch", "terminal",
            )
            if any(not isinstance(attempt.get(field), bool) for field in boolean_fields):
                errors.append(f"attempt {index} lifecycle flags are malformed for {key}")
                continue
            if (
                attempt["orchestration_attempt"] is not True
                or attempt["pre_spawn_validation_started"] is not True
            ):
                errors.append(f"attempt {index} lacks its reservation lifecycle for {key}")
            spawned = attempt["child_process_spawned"]
            counted_launch = attempt["counts_as_implementation_child_launch"]
            rejected = attempt["pre_spawn_rejected"]
            model_started = attempt["model_request_started"]
            response_observed = attempt["provider_response_observed"]
            terminal = attempt["terminal"]
            finished_at = attempt.get("finished_at")
            child_pid = attempt.get("child_pid")
            spawn_receipt = attempt.get("child_spawn_receipt")
            adopted = attempt.get("terminal_evidence_adopted") is True
            if rejected and (
                spawned or counted_launch or model_started or response_observed or terminal
            ):
                errors.append(f"pre-spawn rejection conflicts with later lifecycle for {key}")
            if rejected and (
                not isinstance(attempt.get("pre_spawn_rejection_reason"), str)
                or not attempt["pre_spawn_rejection_reason"]
                or not isinstance(finished_at, str)
                or not finished_at
                or attempt.get("status") != "pre_spawn_rejected"
            ):
                errors.append(f"pre-spawn rejection evidence is incomplete for {key}")
            if spawned:
                if (
                    not counted_launch
                    or not isinstance(child_pid, int)
                    or isinstance(child_pid, bool)
                    or child_pid <= 0
                    or not isinstance(spawn_receipt, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", spawn_receipt)
                    or rejected
                ):
                    errors.append(f"child-spawn lifecycle is incomplete for {key}")
            elif (
                counted_launch
                or child_pid is not None
                or spawn_receipt is not None
                or model_started
                or response_observed
            ):
                errors.append(f"unspawned attempt contains child or model evidence for {key}")
            if response_observed and not model_started:
                errors.append(f"provider response precedes model request for {key}")
            if model_started and not spawned:
                errors.append(f"model request precedes child spawn for {key}")
            if terminal and (
                not isinstance(finished_at, str)
                or not finished_at
                or attempt.get("status") != "child_terminal"
            ):
                errors.append(f"terminal attempt evidence is incomplete for {key}")
            if not terminal and attempt.get("status") == "child_terminal":
                errors.append(f"nonterminal attempt has terminal status for {key}")
            if terminal and not spawned and not adopted:
                errors.append(f"unspawned terminal attempt lacks explicit adoption for {key}")
            if adopted and (
                spawned
                or attempt.get("adoption_kind")
                != "terminal_model_evidence_then_deterministic_derivation"
                or not terminal
            ):
                errors.append(f"terminal-evidence adoption is inconsistent for {key}")
            if index < len(run_attempts) and (
                not isinstance(finished_at, str) or not finished_at or terminal
            ):
                errors.append(f"superseded attempt is unfinished or behavioral-terminal for {key}")
            if counted_launch:
                per_invocation[invocation_id] = per_invocation.get(invocation_id, 0) + 1
                if invocation_id in observed_by_invocation:
                    observed_by_invocation[invocation_id] += 1
        if any(
            isinstance(maximum_launches_per_run, int)
            and value > maximum_launches_per_run
            for value in per_invocation.values()
        ):
            errors.append(
                f"per-invocation per-run child-spawn budget exceeded for {key}"
            )
        run_terminal = run.get("terminal")
        if not isinstance(run_terminal, bool):
            errors.append(f"terminal run flag is malformed for {key}")
        elif run_attempts and isinstance(run_attempts[-1], dict):
            if run_terminal != (run_attempts[-1].get("terminal") is True):
                errors.append(f"run and latest-attempt terminal state differ for {key}")
        elif run_terminal:
            errors.append(f"terminal run has no lifecycle attempt for {key}")
    if attempts != ledger.get("orchestration_attempts"):
        errors.append("orchestration-attempt total does not reconcile")
    if actual != ledger.get("actual_implementation_child_spawns"):
        errors.append("actual child-spawn total does not reconcile")
    invocation_total = 0
    for invocation_id, invocation in invocation_by_id.items():
        recorded = invocation.get("actual_child_spawns")
        if not isinstance(recorded, int) or isinstance(recorded, bool) or recorded < 0:
            errors.append(f"invocation child-spawn total is malformed: {invocation_id}")
            continue
        invocation_total += recorded
        if recorded != observed_by_invocation.get(invocation_id, 0):
            errors.append(f"invocation child-spawn total does not reconcile: {invocation_id}")
    if invocation_total != actual:
        errors.append("per-invocation child-spawn totals do not reconcile")
    if any(
        isinstance(maximum_launches, int)
        and isinstance(item.get("actual_child_spawns"), int)
        and item["actual_child_spawns"] > maximum_launches
        for item in invocations if isinstance(item, dict)
    ):
        errors.append("per-invocation child-spawn budget exceeded")
    return errors
