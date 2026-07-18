#!/usr/bin/env python3
"""Run and receipt the clean-checkout, source-only CI stratum."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PYTHON_POLICY = ">=3.14,<3.15"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_receipt(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def command_plan(methodology_output: Path) -> list[tuple[str, list[str]]]:
    python_sources = [
        str(path.relative_to(ROOT))
        for directory in ("scripts", "tests")
        for path in sorted((ROOT / directory).glob("*.py"))
    ]
    return [
        (
            "python_dependencies",
            ["uv", "sync", "--frozen", "--all-extras"],
        ),
        (
            "python_compile",
            [
                "uv",
                "run",
                "python",
                "-m",
                "py_compile",
                *python_sources,
            ],
        ),
        (
            "python_unit",
            [
                "uv",
                "run",
                "python",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-p",
                "test_*.py",
            ],
        ),
        (
            "verification_registry",
            [
                "uv",
                "run",
                "python",
                "scripts/verification_registry.py",
                "validate",
            ],
        ),
        (
            "source_only_production_shadow",
            [
                "uv",
                "run",
                "python",
                "scripts/methodology_fixture.py",
                "--stratum",
                "source-only",
                "--output",
                str(methodology_output),
            ],
        ),
        (
            "private_prerelease_audit",
            [
                "uv",
                "run",
                "python",
                "scripts/private_prerelease_audit.py",
                "--output-dir",
                str(methodology_output.parent / "private-prerelease-audit"),
            ],
        ),
        (
            "node_dependencies",
            ["npm", "ci", "--prefix", "dashboard"],
        ),
        (
            "node_audit",
            [
                "npm",
                "audit",
                "--prefix",
                "dashboard",
                "--package-lock-only",
            ],
        ),
        (
            "dashboard_unit",
            ["npm", "test", "--prefix", "dashboard", "--", "--run"],
        ),
        (
            "dashboard_build",
            ["npm", "run", "build", "--prefix", "dashboard"],
        ),
        ("git_diff_check", ["git", "diff", "--check"]),
    ]


def fixture_identity() -> dict[str, Any]:
    fixture = ROOT / "fixtures/source-only-target"
    files = sorted(path for path in fixture.rglob("*") if path.is_file())
    entries = [
        {
            "path": path.relative_to(ROOT).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in files
    ]
    digest = hashlib.sha256(
        json.dumps(
            entries, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return {
        "path": "fixtures/source-only-target",
        "entries": entries,
        "entry_count": len(entries),
        "manifest_root": digest,
    }


def source_identity() -> dict[str, Any]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout
    return {
        "commit": commit,
        "tree": tree,
        "worktree_clean": not status.strip(),
    }


def run(output: Path) -> dict[str, Any]:
    if sys.version_info[:2] != (3, 14):
        raise RuntimeError(
            "source-only CI requires exactly Python 3.14"
        )
    if os.environ.get("BENCH_TARGET_REPO_PATH"):
        raise RuntimeError(
            "source-only CI rejects BENCH_TARGET_REPO_PATH"
        )
    fixture = fixture_identity()
    source = source_identity()
    if not source["worktree_clean"]:
        raise RuntimeError(
            "source-only CI requires a clean plain Git checkout"
        )
    if fixture["entry_count"] < 2:
        raise RuntimeError("checked-in source-only fixture is incomplete")
    logs = output.parent / (output.stem + "-logs")
    logs.mkdir(parents=True, exist_ok=True)
    methodology_output = output.parent / "source-only-methodology.json"
    rows: list[dict[str, Any]] = []
    started = time.monotonic()
    for name, command in command_plan(methodology_output):
        stdout_path = logs / f"{name}.stdout.log"
        stderr_path = logs / f"{name}.stderr.log"
        command_started = time.monotonic()
        with stdout_path.open("wb") as stdout, stderr_path.open(
            "wb"
        ) as stderr:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=os.environ.copy(),
                stdout=stdout,
                stderr=stderr,
                check=False,
            )
        row = {
            "name": name,
            "command": [
                (
                    "$SOURCE_ONLY_CI_OUTPUT/"
                    + Path(value).relative_to(output.parent).as_posix()
                    if Path(value).is_absolute()
                    and (
                        Path(value) == output.parent
                        or output.parent in Path(value).parents
                    )
                    else value
                )
                for value in command
            ],
            "exit_code": completed.returncode,
            "duration_seconds": time.monotonic() - command_started,
            "stdout": {
                "path": stdout_path.relative_to(
                    output.parent
                ).as_posix(),
                "bytes": stdout_path.stat().st_size,
                "sha256": sha256_file(stdout_path),
            },
            "stderr": {
                "path": stderr_path.relative_to(
                    output.parent
                ).as_posix(),
                "bytes": stderr_path.stat().st_size,
                "sha256": sha256_file(stderr_path),
            },
            "status": (
                "passed" if completed.returncode == 0 else "failed"
            ),
        }
        rows.append(row)
        receipt = {
            "schema_id": "source-only-ci-receipt-current",
            "status": (
                "passed"
                if all(item["status"] == "passed" for item in rows)
                and len(rows) == len(command_plan(methodology_output))
                else "failed"
            ),
            "execution_stratum": "source-only",
            "python_support": PYTHON_POLICY,
            "plain_git_checkout_compatible": True,
            "canonical_target_required": False,
            "bench_target_repo_path_present": False,
            "bubblewrap_required": False,
            "privileged_namespaces_required": False,
            "canonical_output_directories_required": False,
            "packaged_replay_runtimes_required": False,
            "external_executable_command_tests_use_injection": True,
            "fixture": fixture,
            "source": source,
            "commands": rows,
            "command_count": len(rows),
            "duration_seconds": time.monotonic() - started,
        }
        write_receipt(output, receipt)
        if completed.returncode != 0:
            return receipt
    final_source = source_identity()
    source_unchanged = final_source == source
    receipt["source"] = final_source
    receipt["source_identity_unchanged"] = source_unchanged
    if not source_unchanged:
        receipt["status"] = "failed"
    write_receipt(output, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.output.resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
