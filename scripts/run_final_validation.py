#!/usr/bin/env python3
"""Run and record the deterministic final source-validation command set."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_archive import safe_extract_tar


ROOT = Path(__file__).resolve().parents[1]


def reconstruct_exact_git_checkout(
    source_repo: Path, checkout: Path
) -> dict[str, str]:
    expected_commit = subprocess.check_output(
        ["git", "-C", str(source_repo), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    expected_tree = subprocess.check_output(
        ["git", "-C", str(source_repo), "rev-parse", "HEAD^{tree}"],
        text=True,
    ).strip()
    commit_object = subprocess.check_output(
        ["git", "-C", str(source_repo), "cat-file", "commit", expected_commit]
    )
    subprocess.run(
        ["git", "-C", str(checkout), "init", "--quiet"], check=True
    )
    subprocess.run(
        ["git", "-C", str(checkout), "add", "--all"], check=True
    )
    actual_tree = subprocess.check_output(
        ["git", "-C", str(checkout), "write-tree"], text=True
    ).strip()
    if actual_tree != expected_tree:
        raise RuntimeError(
            "clean-checkout source tree reconstruction mismatch"
        )
    actual_commit = subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "hash-object",
            "-t",
            "commit",
            "-w",
            "--stdin",
        ],
        input=commit_object,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.decode().strip()
    if actual_commit != expected_commit:
        raise RuntimeError(
            "clean-checkout commit-object reconstruction mismatch"
        )
    subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "update-ref",
            "HEAD",
            expected_commit,
        ],
        check=True,
    )
    status = subprocess.check_output(
        [
            "git",
            "-C",
            str(checkout),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        text=True,
    )
    if status:
        raise RuntimeError("reconstructed clean checkout is dirty")
    return {
        "commit": actual_commit,
        "tree": actual_tree,
        "status": "clean",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--maven-home", type=Path, required=True)
    parser.add_argument(
        "--chromium-executable", type=Path, required=True
    )
    parser.add_argument("--current-preflight-root", type=Path)
    parser.add_argument("--clean-checkout", action="store_true")
    args = parser.parse_args()
    repo = args.repo.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    commands = [
        ["uv", "sync", "--frozen", "--all-extras"],
        ["uv", "run", "python", "-m", "py_compile", *[
            str(path.relative_to(repo))
            for pattern in ("scripts/*.py", "tests/*.py")
            for path in sorted(repo.glob(pattern))
        ]],
        ["uv", "run", "python", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
        ["uv", "run", "python", "scripts/verification_registry.py", "validate"],
        ["uv", "run", "python", "scripts/verification_registry.py", "run"],
        ["uv", "run", "python", "scripts/preflight_status_faults.py"],
        [
            "uv",
            "run",
            "python",
            "-m",
            "unittest",
            "tests.test_final_source_replay",
        ],
        ["npm", "ci", "--prefix", "dashboard"],
        ["npm", "audit", "--prefix", "dashboard", "--package-lock-only"],
        ["npm", "test", "--prefix", "dashboard", "--", "--run"],
        ["npm", "run", "build", "--prefix", "dashboard"],
        ["npm", "run", "test:browser", "--prefix", "dashboard"],
        ["uv", "run", "python", "scripts/private_prerelease_audit.py"],
        ["uv", "run", "python", "scripts/normative_document_audit.py"],
        ["uv", "run", "python", "scripts/execution_field_provenance.py", "validate"],
        ["git", "diff", "--check"],
    ]
    environment = dict(os.environ)
    environment.update({
        "BENCH_TARGET_REPO_PATH": str(args.target.resolve()),
        "BENCH_CHROMIUM_EXECUTABLE": str(
            args.chromium_executable.resolve()
        ),
        "MAVEN_USER_HOME": str(args.maven_home.resolve()),
        "MAVEN_OPTS": (
            f"-Dmaven.repo.local={args.maven_home.resolve()}/repository"
        ),
    })
    if args.current_preflight_root:
        environment["BENCH_CURRENT_PREFLIGHT_CACHE_ROOT"] = str(
            args.current_preflight_root.resolve()
        )
    rows = []
    log_lines = []
    for command in commands:
        started = time.monotonic()
        process = subprocess.run(
            command,
            cwd=repo,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        duration = time.monotonic() - started
        text = process.stdout
        rows.append({
            "command": shlex.join(command),
            "returncode": process.returncode,
            "duration_seconds": duration,
            "output_sha256": __import__("hashlib").sha256(text.encode()).hexdigest(),
        })
        log_lines.extend([
            f"$ {shlex.join(command)}",
            f"returncode={process.returncode} duration_seconds={duration:.6f}",
            text.rstrip(),
            "",
        ])
        if process.returncode:
            break
    if args.clean_checkout and all(row["returncode"] == 0 for row in rows):
        with tempfile.TemporaryDirectory(prefix="final-source-only-") as temporary:
            archive = Path(temporary) / "source.tar"
            checkout = Path(temporary) / "source"
            subprocess.run(
                ["git", "-C", str(repo), "archive", "--format=tar", "-o", str(archive), "HEAD"],
                check=True,
            )
            import tarfile

            with tarfile.open(archive) as source_archive:
                safe_extract_tar(source_archive, checkout)
            reconstructed = reconstruct_exact_git_checkout(repo, checkout)
            (checkout / "dashboard/node_modules").symlink_to(
                repo / "dashboard/node_modules", target_is_directory=True
            )
            command = [
                str(repo / ".venv/bin/python"),
                "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py",
            ]
            started = time.monotonic()
            process = subprocess.run(
                command,
                cwd=checkout,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            duration = time.monotonic() - started
            rows.append({
                "command": "clean-checkout: $SOURCE_PYTHON -m unittest discover -s tests -p 'test_*.py'",
                "returncode": process.returncode,
                "duration_seconds": duration,
                "output_sha256": __import__("hashlib").sha256(process.stdout.encode()).hexdigest(),
                "source_commit": reconstructed["commit"],
                "source_tree": reconstructed["tree"],
                "worktree_status_before_test": reconstructed["status"],
            })
            log_lines.extend([
                "$ clean-checkout source-only test suite",
                f"returncode={process.returncode} duration_seconds={duration:.6f}",
                process.stdout.rstrip(),
                "",
            ])
    passed = bool(rows) and all(row["returncode"] == 0 for row in rows)
    receipt = {
        "schema_id": "final-source-test-results-current",
        "status": "passed" if passed else "failed",
        "effective_release_gates_passed": passed,
        "command_count": len(rows),
        "commands": rows,
        "total_duration_seconds": sum(row["duration_seconds"] for row in rows),
        "model_calls": 0,
        "codex_implementation_children": 0,
    }
    (output / "command-log.txt").write_text("\n".join(log_lines), encoding="utf-8")
    (output / "test-results.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
