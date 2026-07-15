#!/usr/bin/env python3
"""Validate and execute the current verification registry."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from jsonschema import Draft202012Validator
from verification_checkers import CHECKERS, run

ROOT = Path(__file__).resolve().parents[1]


def load(repo: Path) -> dict:
    return json.loads((repo / "verification/verification-registry.json").read_text())


def validate(repo: Path) -> list[str]:
    registry = load(repo)
    schema = json.loads((repo / "schemas/verification-registry.schema.json").read_text())
    errors = [error.message for error in Draft202012Validator(schema).iter_errors(registry)]
    entries = registry.get("entries", [])
    ids = [entry.get("id") for entry in entries]
    if len(ids) != len(set(ids)):
        errors.append("verification IDs are not unique")
    automated = {entry["id"] for entry in entries if entry.get("kind") == "automated"}
    if automated != set(CHECKERS):
        errors.append(f"checker coverage mismatch missing={sorted(automated-set(CHECKERS))} extra={sorted(set(CHECKERS)-automated)}")
    for entry in entries:
        for key in ("implementation", "test_files", "fixture_files"):
            for value in entry.get(key, []):
                if not (repo / value).exists():
                    errors.append(f"{entry['id']}: missing path {value}")
    return errors


def execute(repo: Path) -> dict:
    errors = validate(repo)
    rows = []
    for entry in load(repo).get("entries", []):
        if entry.get("kind") != "automated":
            continue
        started = time.monotonic()
        positive = run(entry["id"], repo)
        negative = run(entry["id"], repo, inject_fault=True)
        rows.append({"id": entry["id"], "checker_id": entry["checker_id"], "status": "passed" if positive["status"] == "passed" and negative["status"] == "failed" else "failed", "positive": positive, "negative_fault_injection": negative, "duration_seconds": time.monotonic()-started})
    return {"schema_id": "verification-report-current", "status": "passed" if not errors and all(row["status"] == "passed" for row in rows) else "failed", "registry_errors": errors, "checks": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("validate", "run"))
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    data = {"status": "passed", "errors": validate(args.repo.resolve())} if args.command == "validate" else execute(args.repo.resolve())
    if args.command == "validate" and data["errors"]:
        data["status"] = "failed"
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    args.output.write_text(text) if args.output else print(text, end="")
    return 0 if data["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
