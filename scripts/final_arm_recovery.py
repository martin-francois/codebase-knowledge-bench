#!/usr/bin/env python3
"""Fail-closed deterministic preparation for one interrupted canonical arm."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from safe_archive import safe_extract_zip
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from canonical_suite import atomic_json, sha256_file
from launch_accounting import (
    MIGRATION_VERSION,
    canonical_bytes,
    migrate_legacy_ledger,
    validate_ledger_accounting,
)

def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as handle:
        handle.write(value)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def git(repo: Path, *arguments: str, binary: bool = False) -> bytes | str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments], check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=not binary,
    )
    return result.stdout


def virtual_smoke_state_digest(
    roots: dict[str, Path], *, replacement_files: dict[str, bytes],
    excluded_repo_prefixes: tuple[str, ...],
) -> str:
    digest = hashlib.sha256()
    for name, root in sorted(roots.items()):
        digest.update(f"ROOT\0{name}\0{root.exists()}\0".encode())
        if not root.exists():
            continue
        paths: list[Path] = []
        for path in root.rglob("*"):
            relative = path.relative_to(root).as_posix()
            if name == "repo" and any(
                relative == prefix or relative.startswith(prefix + "/")
                for prefix in excluded_repo_prefixes
            ):
                continue
            paths.append(path)
        for path in sorted(paths, key=lambda value: value.relative_to(root).as_posix()):
            relative = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode & 0o7777
            if path.is_symlink():
                digest.update(f"L\0{relative}\0{mode:o}\0{os.readlink(path)}\0".encode())
            elif path.is_dir():
                digest.update(f"D\0{relative}\0{mode:o}\0".encode())
            elif path.is_file():
                replacement = replacement_files.get(relative) if name == "repo" else None
                size = len(replacement) if replacement is not None else path.stat().st_size
                digest.update(f"F\0{relative}\0{mode:o}\0{size}\0".encode())
                if replacement is not None:
                    digest.update(replacement)
                else:
                    with path.open("rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
            else:
                digest.update(f"O\0{relative}\0{mode:o}\0".encode())
    return digest.hexdigest()


def assess_restoration(
    execution_root: Path, run_id: str, *, expected_digest: str,
) -> dict[str, Any]:
    repo = execution_root / "sealed-repos" / run_id / "repo"
    changed = sorted(
        line for line in str(git(repo, "diff", "--name-only", "--no-ext-diff")).splitlines()
        if line
    )
    replacements = {
        relative: git(repo, "show", f"HEAD:{relative}", binary=True)
        for relative in changed
    }
    tool = execution_root / "tool-cache" / run_id
    roots = {
        "repo": repo,
        "home": tool / "home",
        "xdg-cache": tool / "xdg-cache",
        "xdg-config": tool / "xdg-config",
        "xdg-data": tool / "xdg-data",
    }
    prospective = virtual_smoke_state_digest(
        roots, replacement_files=replacements, excluded_repo_prefixes=("target",)
    )
    return {
        "schema_version": "pre-retry-state-restoration-v1",
        "expected_digest": expected_digest,
        "prospective_digest": prospective,
        "exact_digest_match": prospective == expected_digest,
        "repository_modified": False,
        "restoration_applied": False,
        "candidate_paths_to_restore": changed,
        "solve_generated_paths_to_remove": ["target"],
        "failure_reason": (
            None if prospective == expected_digest else
            "immutable evidence cannot reconstruct the recorded pre-solve smoke-state digest"
        ),
    }


def copy_interrupted_attempt(
    execution_root: Path, destination: Path, run_id: str,
) -> dict[str, Any]:
    source = execution_root / "runs" / run_id
    if destination.exists():
        raise SystemExit(f"interrupted-attempt package already exists: {destination}")
    shutil.copytree(source, destination / run_id, copy_function=shutil.copy2)
    repo = execution_root / "sealed-repos" / run_id / "repo"
    status = str(git(repo, "status", "--porcelain=v2", "--branch", "--untracked-files=all"))
    patch = git(repo, "diff", "--binary", "--no-ext-diff", binary=True)
    atomic_text(destination / "dirty-git-status.txt", status)
    (destination / "dirty-worktree.patch").write_bytes(patch)
    manifest = []
    for path in sorted(destination.rglob("*")):
        if path.is_file() and path.name != "attempt-manifest.json":
            manifest.append({
                "path": path.relative_to(destination).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            })
    payload = {
        "schema_version": "infrastructure-attempt-v1",
        "classification": "provider_interruption_after_partial_implementation",
        "primary_treatment_result_status": "excluded_from_primary_treatment_result",
        "token_usage_available": False,
        "token_usage_reason": "turn.failed before turn.completed.usage",
        "files": manifest,
    }
    atomic_json(destination / "attempt-manifest.json", payload)
    return payload


def migrate_accounting(suite_root: Path, audit: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    ledger_path = suite_root / "execution-ledger.json"
    legacy_path = suite_root / "execution-ledger.legacy.json"
    if legacy_path.exists():
        raise SystemExit(f"legacy ledger preservation already exists: {legacy_path}")
    legacy_bytes = ledger_path.read_bytes()
    legacy = json.loads(legacy_bytes)
    terminal = [key for key, arm in legacy["arms"].items() if arm.get("terminal")]
    pending = audit["arm_key"]
    spawned = {key: [0] for key in terminal}
    spawned[pending] = [0]
    rejected = {pending: {1: "sealed repository is not clean"}}
    migrated = migrate_legacy_ledger(
        legacy, spawned_attempt_indexes=spawned, pre_spawn_rejections=rejected
    )
    errors = validate_ledger_accounting(migrated)
    if errors:
        raise SystemExit("launch-accounting migration is invalid: " + "; ".join(errors))
    legacy_path.write_bytes(legacy_bytes)
    atomic_json(ledger_path, migrated)
    migration = {
        "schema_version": "launch-accounting-migration-v1",
        "migration_algorithm": MIGRATION_VERSION,
        "original_ledger_sha256": hashlib.sha256(legacy_bytes).hexdigest(),
        "migrated_ledger_sha256": sha256_file(ledger_path),
        "evidence_files": [
            "final-arm-recovery-audit.json",
            "independent-final-arm-diagnostics/05-retry-attempt/retry-event-timeline.json",
            "independent-final-arm-diagnostics/10-process-and-model-evidence/model-call-assessment.json",
        ],
        "legacy": {
            "implementation_child_launches": legacy["implementation_child_launches"],
            "missing_arm_launch_count": legacy["arms"][pending]["launch_count"],
        },
        "corrected": {
            "actual_implementation_child_spawns": migrated["actual_implementation_child_spawns"],
            "missing_arm_actual_child_spawns": migrated["arms"][pending]["actual_child_spawn_count"],
            "orchestration_attempts": migrated["orchestration_attempts"],
            "missing_arm_orchestration_attempts": migrated["arms"][pending]["orchestration_attempt_count"],
        },
        "validator_result": "pass",
    }
    atomic_json(suite_root / "launch-accounting-migration.json", migration)
    atomic_text(
        suite_root / "launch-accounting-migration.md",
        "# Launch-accounting migration\n\n"
        f"- Algorithm: `{MIGRATION_VERSION}`\n"
        f"- Legacy reservations recorded as child launches: `{legacy['implementation_child_launches']}`\n"
        f"- Evidence-derived actual child spawns: `{migrated['actual_implementation_child_spawns']}`\n"
        f"- Missing-arm orchestration attempts: `{migrated['arms'][pending]['orchestration_attempt_count']}`\n"
        f"- Missing-arm actual child spawns: `{migrated['arms'][pending]['actual_child_spawn_count']}`\n",
    )
    return migrated, migration


def write_no_go_bundle(
    suite_root: Path, audit: dict[str, Any], migration: dict[str, Any],
    restoration: dict[str, Any],
) -> Path:
    destination = suite_root / "final-arm-recovery-no-go"
    if destination.exists():
        raise SystemExit(f"versioned recovery output already exists: {destination}")
    destination.mkdir()
    for name in (
        "final-arm-recovery-audit.json", "final-arm-recovery-audit.md",
        "execution-ledger.json", "execution-ledger.legacy.json",
        "launch-accounting-migration.json", "launch-accounting-migration.md",
        "pre-retry-state-restoration.json", "pre-retry-state-restoration.md",
    ):
        shutil.copy2(suite_root / name, destination / name)
    readiness = {
        "schema_version": "final-arm-recovery-readiness-v1",
        "decision": "NO_GO",
        "canonical_matrix_complete": False,
        "scheduled_unique_arms": 63,
        "terminal_unique_arms": 62,
        "actual_implementation_child_spawns": migration["corrected"]["actual_implementation_child_spawns"],
        "new_model_probes": 0,
        "new_implementation_child_spawns": 0,
        "completed_children_rerun": False,
        "legacy_launch_accounting_preserved": True,
        "original_62_arm_root_unchanged": True,
        "remaining_blockers": [restoration["failure_reason"]],
        "remaining_limitations": ["hard external-egress denial unavailable"],
    }
    atomic_json(destination / "full-suite-readiness.json", readiness)
    atomic_text(
        destination / "full-suite-readiness.md",
        "# Final-arm recovery readiness\n\n"
        "- Decision: **NO_GO**\n"
        "- Matrix: `62/63` terminal arms\n"
        "- New model probes: `0/3`\n"
        "- New implementation child spawns: `0/1`\n"
        f"- Blocker: {restoration['failure_reason']}\n",
    )
    manifest = []
    for path in sorted(destination.rglob("*")):
        if path.is_file() and path.name != "content-manifest.json":
            manifest.append({
                "path": path.relative_to(destination).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            })
    root_hash = hashlib.sha256(canonical_bytes(manifest)).hexdigest()
    atomic_json(destination / "content-manifest.json", {
        "schema_version": "final-arm-recovery-manifest-v1",
        "entries": manifest,
        "root_sha256": root_hash,
    })
    archive = suite_root / "final-arm-recovery-no-go.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(destination.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(destination).as_posix())
    archive_hash = sha256_file(archive)
    atomic_text(archive.with_suffix(".zip.sha256"), f"{archive_hash}  {archive.name}\n")
    with tempfile.TemporaryDirectory() as temporary:
        extracted = Path(temporary)
        with zipfile.ZipFile(archive) as bundle:
            safe_extract_zip(bundle, extracted)
        extracted_manifest = json.loads((extracted / "content-manifest.json").read_text())
        errors = []
        for entry in extracted_manifest["entries"]:
            path = extracted / entry["path"]
            if not path.is_file() or path.stat().st_size != entry["bytes"] or sha256_file(path) != entry["sha256"]:
                errors.append(entry["path"])
        if errors:
            raise SystemExit("recovery NO_GO archive validation failed: " + ", ".join(errors))
    atomic_json(archive.with_suffix(".zip.validation.json"), {
        "result": "pass",
        "archive_sha256": archive_hash,
        "archive_bytes": archive.stat().st_size,
        "manifest_entry_count": len(manifest),
        "manifest_root_sha256": root_hash,
    })
    return archive


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("suite_root", type=Path)
    parser.add_argument("execution_root", type=Path)
    arguments = parser.parse_args()
    suite_root = arguments.suite_root.resolve()
    execution_root = arguments.execution_root.resolve()
    audit = json.loads((suite_root / "final-arm-recovery-audit.json").read_text())
    if not audit.get("passed"):
        raise SystemExit("final-arm recovery audit did not pass")
    dirty_repo = Path(audit["dirty_repository"]["path"])
    run_id = dirty_repo.parent.name
    package = suite_root / "infrastructure-attempts" / f"{run_id}-attempt-001-provider-interruption"
    copy_interrupted_attempt(execution_root, package, run_id)
    _, migration = migrate_accounting(suite_root, audit)
    restoration = assess_restoration(
        execution_root, run_id, expected_digest=audit["expected_smoke_state_digest"]
    )
    atomic_json(suite_root / "pre-retry-state-restoration.json", restoration)
    atomic_text(
        suite_root / "pre-retry-state-restoration.md",
        "# Pre-retry state restoration\n\n"
        f"- Expected smoke-state digest: `{restoration['expected_digest']}`\n"
        f"- Prospective restored digest: `{restoration['prospective_digest']}`\n"
        f"- Exact match: `{restoration['exact_digest_match']}`\n"
        f"- Restoration applied: `{restoration['restoration_applied']}`\n"
        f"- Blocker: {restoration['failure_reason'] or 'none'}\n",
    )
    if not restoration["exact_digest_match"]:
        archive = write_no_go_bundle(suite_root, audit, migration, restoration)
        print(json.dumps({
            "decision": "NO_GO",
            "archive": str(archive),
            "reason": restoration["failure_reason"],
        }, indent=2))
        return 2
    raise SystemExit("exact restoration is proven but application requires explicit recovery coordinator")


if __name__ == "__main__":
    raise SystemExit(main())
