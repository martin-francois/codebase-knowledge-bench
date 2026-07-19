#!/usr/bin/env python3
"""Run and receipt the digest-pinned, clean-checkout source-only CI stratum."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
PYTHON_POLICY = ">=3.14,<3.15"
TASK_ID = "final-source-only-ci-browser-and-image-pin"
ROUTING_NONCE = "FMCB-20260719-9D4E2A7B"
BASE_COMMIT = "86e1658f48539a8cd3e737d740f498ee649d214c"
EXPECTED_PYTHON_VERSION = "3.14.3"
EXPECTED_NODE_VERSION = "v22.22.0"
EXPECTED_NPM_VERSION = "10.9.4"
EXPECTED_CHROMIUM_VERSION = "Google Chrome for Testing 149.0.7827.55"
EXPECTED_CHROMIUM_EXECUTABLE = (
    "/ms-playwright/chromium-1228/chrome-linux64/chrome"
)
EXPECTED_CHROMIUM_SHA256 = (
    "2d18db9d8608b052b6a552ee00ec1e830f93692e928b65ecc67d693bd33fe801"
)
SOURCE_ONLY_USERSPACE_IMAGE_DIGEST = (
    "sha256:5b8f294aff9041b7191c34a4bab3ac270157a28774d4b0660e9743297b697e48"
)
SOURCE_ONLY_USERSPACE_IMAGE = (
    "mcr.microsoft.com/playwright:v1.61.1-noble@"
    + SOURCE_ONLY_USERSPACE_IMAGE_DIGEST
)
WORKFLOW_PATH = ROOT / ".github/workflows/ci.yml"
BROWSER_SPEC = ROOT / "dashboard/tests/browser.spec.ts"
BROWSER_SPEC_RELATIVE = "dashboard/tests/browser.spec.ts"
BROWSER_COMMAND = [
    "npm",
    "run",
    "test:browser",
    "--prefix",
    "dashboard",
]
REQUIRED_COMMAND_NAMES = (
    "python_dependencies",
    "python_compile",
    "python_unit",
    "verification_registry",
    "source_only_production_shadow",
    "private_prerelease_audit",
    "node_dependencies",
    "node_audit",
    "dashboard_unit",
    "dashboard_build",
    "dashboard_browser",
    "git_diff_check",
)
HEX_64 = re.compile(r"[0-9a-f]{64}")
INDEPENDENCE_CONTRACT = {
    "plain_git_checkout_compatible": True,
    "canonical_target_required": False,
    "bench_target_repo_path_present": False,
    "bubblewrap_required": False,
    "privileged_namespaces_required": False,
    "canonical_output_directories_required": False,
    "builder_home_required": False,
    "builder_caches_required": False,
    "packaged_replay_runtimes_required": False,
    "artifact_backed_target_evidence_imported": False,
    "external_executable_command_tests_use_injection": True,
}


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_identity(path: Path, *, root: Path | None = None) -> dict[str, Any]:
    resolved = path.resolve()
    if root is not None:
        try:
            rendered = resolved.relative_to(root.resolve()).as_posix()
        except ValueError:
            rendered = str(resolved)
    else:
        rendered = str(resolved)
    return {
        "path": rendered,
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def write_receipt(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical_bytes(value))
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
        ("dashboard_browser", list(BROWSER_COMMAND)),
        ("git_diff_check", ["git", "diff", "--check"]),
    ]


def _portable_value(value: str, output_root: Path) -> str:
    path = Path(value)
    if not path.is_absolute():
        return value
    try:
        relative = path.relative_to(output_root)
    except ValueError:
        return value
    return "$SOURCE_ONLY_CI_OUTPUT/" + relative.as_posix()


def portable_command_plan(
    plan: Sequence[tuple[str, Sequence[str]]],
    output_root: Path,
) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "command": [
                _portable_value(value, output_root) for value in command
            ],
        }
        for name, command in plan
    ]


def command_plan_identity(
    plan: Sequence[tuple[str, Sequence[str]]],
    output_root: Path,
) -> dict[str, Any]:
    portable = portable_command_plan(plan, output_root)
    payload = canonical_bytes(portable)
    return {
        "command_count": len(portable),
        "commands": portable,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def command_plan_errors(
    plan: Sequence[tuple[str, Sequence[str]]],
) -> list[str]:
    errors: list[str] = []
    names = [name for name, _ in plan]
    if tuple(names) != REQUIRED_COMMAND_NAMES:
        errors.append("source-only command names or order differ")
    if len(names) != len(set(names)):
        errors.append("source-only command names are not unique")
    by_name = {name: list(command) for name, command in plan}
    if by_name.get("dashboard_browser") != BROWSER_COMMAND:
        errors.append(
            "dashboard_browser must execute npm run test:browser "
            "--prefix dashboard"
        )
    package = json.loads(
        (ROOT / "dashboard/package.json").read_text(encoding="utf-8")
    )
    if package.get("scripts", {}).get("test:browser") != "playwright test":
        errors.append("dashboard test:browser must execute Playwright")
    if not BROWSER_SPEC.is_file():
        errors.append("dashboard browser.spec.ts is missing")
    return errors


def workflow_userspace_images() -> list[str]:
    source = WORKFLOW_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        r"mcr\.microsoft\.com/playwright:"
        r"[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}"
    )
    return pattern.findall(source)


def workflow_image_errors() -> list[str]:
    images = workflow_userspace_images()
    errors: list[str] = []
    if len(images) != 3:
        errors.append(
            "workflow must bind its container and two receipt environment "
            "values to exactly one full userspace digest"
        )
    if not images or set(images) != {SOURCE_ONLY_USERSPACE_IMAGE}:
        errors.append("workflow and source-only userspace image differ")
    source = WORKFLOW_PATH.read_text(encoding="utf-8")
    expected_container = (
        "    container:\n"
        f'      image: "{SOURCE_ONLY_USERSPACE_IMAGE}"'
    )
    if expected_container not in source:
        errors.append("source-only job container differs from source pin")
    if "runs-on: ubuntu-latest" in source:
        errors.append("mutable ubuntu-latest source-only runner")
    return errors


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


def _version(command: Sequence[str]) -> str:
    completed = subprocess.run(
        list(command),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    return completed.stdout.strip().splitlines()[0]


def _executable(name: str) -> Path:
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"source-only CI executable is missing: {name}")
    return Path(found).resolve()


def _os_release() -> dict[str, str]:
    result: dict[str, str] = {}
    for line in Path("/etc/os-release").read_text(
        encoding="utf-8"
    ).splitlines():
        if "=" not in line:
            continue
        key, raw = line.split("=", 1)
        result[key] = raw.strip().strip('"')
    return result


def environment_identity() -> dict[str, Any]:
    requested_image = os.environ.get(
        "SOURCE_ONLY_USERSPACE_IMAGE", ""
    ).strip()
    executed_image = os.environ.get(
        "SOURCE_ONLY_EXECUTED_IMAGE", ""
    ).strip()
    chromium_raw = os.environ.get(
        "BENCH_CHROMIUM_EXECUTABLE", ""
    ).strip()
    if not chromium_raw:
        raise RuntimeError("BENCH_CHROMIUM_EXECUTABLE is required")
    chromium = Path(chromium_raw).resolve()
    if not chromium.is_file():
        raise RuntimeError(
            "BENCH_CHROMIUM_EXECUTABLE is not a regular file"
        )
    python = Path(sys.executable).resolve()
    node = _executable("node")
    npm = _executable("npm")
    release = _os_release()
    glibc = _version(["ldd", "--version"])
    return {
        "source_only_userspace_image": requested_image,
        "source_only_userspace_image_digest":
            requested_image.rsplit("@", 1)[-1]
            if "@" in requested_image
            else "",
        "source_only_executed_image": executed_image,
        "source_only_distribution": release.get(
            "PRETTY_NAME", release.get("ID", "")
        ),
        "source_only_distribution_id": release.get("ID", ""),
        "source_only_distribution_version": release.get(
            "VERSION_ID", ""
        ),
        "source_only_glibc": glibc,
        "python_version": platform.python_version(),
        "python_executable": str(python),
        "python_executable_sha256": sha256_file(python),
        "node_version": _version([str(node), "--version"]),
        "node_executable": str(node),
        "node_executable_sha256": sha256_file(node),
        "npm_version": _version([str(npm), "--version"]),
        "npm_entrypoint": str(npm),
        "npm_entrypoint_sha256": sha256_file(npm),
        "chromium_version": _version([str(chromium), "--version"]),
        "chromium_executable": str(chromium),
        "chromium_executable_sha256": sha256_file(chromium),
    }


def environment_identity_errors(identity: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if (
        identity.get("source_only_userspace_image")
        != SOURCE_ONLY_USERSPACE_IMAGE
    ):
        errors.append("source-only userspace image differs from source pin")
    if (
        identity.get("source_only_executed_image")
        != SOURCE_ONLY_USERSPACE_IMAGE
    ):
        errors.append("workflow and executed image differ")
    if (
        identity.get("source_only_userspace_image_digest")
        != SOURCE_ONLY_USERSPACE_IMAGE_DIGEST
    ):
        errors.append("source-only userspace image digest is missing or stale")
    if identity.get("python_version") != EXPECTED_PYTHON_VERSION:
        errors.append("source-only Python version differs from exact pin")
    if identity.get("node_version") != EXPECTED_NODE_VERSION:
        errors.append("source-only Node version differs from exact pin")
    if identity.get("npm_version") != EXPECTED_NPM_VERSION:
        errors.append("source-only npm version differs from exact pin")
    if (
        identity.get("chromium_version")
        != EXPECTED_CHROMIUM_VERSION
    ):
        errors.append("source-only Chromium version differs from image pin")
    if (
        identity.get("chromium_executable")
        != EXPECTED_CHROMIUM_EXECUTABLE
    ):
        errors.append(
            "source-only Chromium executable differs from image pin"
        )
    if (
        identity.get("chromium_executable_sha256")
        != EXPECTED_CHROMIUM_SHA256
    ):
        errors.append("source-only Chromium SHA-256 differs from image pin")
    for field in (
        "python_executable_sha256",
        "node_executable_sha256",
        "npm_entrypoint_sha256",
        "chromium_executable_sha256",
    ):
        if not HEX_64.fullmatch(str(identity.get(field, ""))):
            errors.append(f"{field} is not a SHA-256")
    for field in (
        "source_only_distribution",
        "source_only_glibc",
        "chromium_version",
        "chromium_executable",
    ):
        if not str(identity.get(field, "")).strip():
            errors.append(f"{field} is missing")
    return errors


def _normalize_test_file(raw: str) -> str:
    path = Path(raw)
    candidates = (
        [path]
        if path.is_absolute()
        else [
            ROOT / "dashboard/tests" / path,
            ROOT / "dashboard" / path,
            ROOT / path,
        ]
    )
    fallbacks: list[str] = []
    for candidate in candidates:
        try:
            relative = (
                candidate.resolve().relative_to(ROOT).as_posix()
            )
        except ValueError:
            continue
        if candidate.resolve().is_file():
            return relative
        fallbacks.append(relative)
    return fallbacks[0] if fallbacks else str(path)


def _suite_specs(value: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    for spec in value.get("specs", []):
        if isinstance(spec, Mapping):
            result.append(spec)
    for suite in value.get("suites", []):
        if isinstance(suite, Mapping):
            result.extend(_suite_specs(suite))
    return result


def playwright_result_summary(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    stats = value.get("stats", {})
    counts = {
        name: int(stats.get(name, 0))
        for name in ("expected", "unexpected", "flaky", "skipped")
    }
    files = sorted(
        {
            _normalize_test_file(str(spec.get("file", "")))
            for spec in _suite_specs(value)
            if spec.get("tests")
        }
    )
    test_count = sum(counts.values())
    errors: list[str] = []
    if files != [BROWSER_SPEC_RELATIVE]:
        errors.append(
            "Playwright did not execute only dashboard/tests/browser.spec.ts"
        )
    if test_count < 1:
        errors.append("Playwright executed zero browser tests")
    if counts["unexpected"] or value.get("errors"):
        errors.append("Playwright browser test did not pass")
    return {
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "browser_test_count": test_count,
        "passed_test_count": counts["expected"],
        "failed_test_count": counts["unexpected"],
        "flaky_test_count": counts["flaky"],
        "skipped_test_count": counts["skipped"],
        "executed_test_files": files,
        "result": file_identity(path, root=path.parent),
    }


def browser_receipt_errors(receipt: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if receipt.get("task_id") != TASK_ID:
        errors.append("browser receipt task ID differs")
    if receipt.get("routing_nonce") != ROUTING_NONCE:
        errors.append("browser receipt routing nonce differs")
    if receipt.get("status") != "passed":
        errors.append("browser receipt status is not passed")
    if receipt.get("command") != BROWSER_COMMAND:
        errors.append("browser receipt command is not real Playwright")
    if receipt.get("command_exit_code") != 0:
        errors.append("browser receipt command did not exit zero")
    if receipt.get("executed_test_files") != [BROWSER_SPEC_RELATIVE]:
        errors.append("browser receipt does not bind browser.spec.ts")
    try:
        browser_test_count = int(receipt.get("browser_test_count", 0))
        passed_test_count = int(receipt.get("passed_test_count", 0))
        failed_test_count = int(receipt.get("failed_test_count", -1))
        flaky_test_count = int(receipt.get("flaky_test_count", 0))
        skipped_test_count = int(receipt.get("skipped_test_count", 0))
    except (TypeError, ValueError):
        browser_test_count = 0
        passed_test_count = 0
        failed_test_count = -1
        flaky_test_count = 0
        skipped_test_count = 0
        errors.append("browser receipt test counts are invalid")
    if browser_test_count < 1:
        errors.append("browser receipt test count is zero")
    if (
        passed_test_count < 1
        or failed_test_count != 0
        or browser_test_count
        != (
            passed_test_count
            + failed_test_count
            + flaky_test_count
            + skipped_test_count
        )
    ):
        errors.append("browser receipt test counts do not describe a pass")
    source = receipt.get("source", {})
    if not isinstance(source, Mapping):
        errors.append("browser receipt source identity is missing")
    else:
        commit = str(source.get("commit", ""))
        tree = str(source.get("tree", ""))
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            errors.append("browser receipt source commit is invalid")
        if commit == BASE_COMMIT:
            errors.append("browser receipt uses stale base source")
        if not re.fullmatch(r"[0-9a-f]{40}", tree):
            errors.append("browser receipt source tree is invalid")
        if source.get("worktree_clean") is not True:
            errors.append("browser receipt source is not clean")
    browser_spec = receipt.get("browser_spec", {})
    if (
        not isinstance(browser_spec, Mapping)
        or browser_spec.get("path") != BROWSER_SPEC_RELATIVE
        or browser_spec.get("bytes") != BROWSER_SPEC.stat().st_size
        or browser_spec.get("sha256") != sha256_file(BROWSER_SPEC)
    ):
        errors.append("browser receipt browser.spec.ts identity differs")
    if receipt.get("source_only_userspace_image") != (
        SOURCE_ONLY_USERSPACE_IMAGE
    ):
        errors.append("browser receipt userspace image differs")
    if (
        receipt.get("source_only_userspace_image_digest")
        != SOURCE_ONLY_USERSPACE_IMAGE_DIGEST
    ):
        errors.append("browser receipt userspace digest differs")
    for field in (
        "source_only_distribution",
        "source_only_glibc",
        "chromium_version",
        "chromium_executable",
    ):
        if not str(receipt.get(field, "")).strip():
            errors.append(f"browser receipt {field} is missing")
    if not HEX_64.fullmatch(
        str(receipt.get("chromium_executable_sha256", ""))
    ):
        errors.append("browser receipt Chromium SHA-256 is missing")
    result = receipt.get("result", {})
    if (
        not isinstance(result, Mapping)
        or not str(result.get("path", "")).strip()
        or not isinstance(result.get("bytes"), int)
        or result.get("bytes", 0) < 1
        or not HEX_64.fullmatch(str(result.get("sha256", "")))
    ):
        errors.append("browser receipt Playwright result identity is invalid")
    if receipt.get("errors") != []:
        errors.append("browser receipt contains Playwright errors")
    if receipt.get("validation_errors") not in (None, []):
        errors.append("browser receipt contains validation errors")
    return errors


def build_browser_receipt(
    *,
    result_path: Path,
    source: Mapping[str, Any],
    environment: Mapping[str, Any],
    command_row: Mapping[str, Any],
) -> dict[str, Any]:
    summary = playwright_result_summary(result_path)
    receipt = {
        "schema_id": "source-only-browser-receipt-current",
        "task_id": TASK_ID,
        "routing_nonce": ROUTING_NONCE,
        "status": (
            "passed"
            if command_row.get("exit_code") == 0
            and summary["status"] == "passed"
            else "failed"
        ),
        "source": dict(source),
        "command": list(BROWSER_COMMAND),
        "command_exit_code": command_row.get("exit_code"),
        "browser_spec": {
            "path": BROWSER_SPEC_RELATIVE,
            "bytes": BROWSER_SPEC.stat().st_size,
            "sha256": sha256_file(BROWSER_SPEC),
        },
        **{
            field: environment[field]
            for field in (
                "source_only_userspace_image",
                "source_only_userspace_image_digest",
                "source_only_distribution",
                "source_only_glibc",
                "chromium_version",
                "chromium_executable",
                "chromium_executable_sha256",
            )
        },
        **summary,
    }
    validation_errors = browser_receipt_errors(receipt)
    receipt["validation_errors"] = validation_errors
    if validation_errors:
        receipt["status"] = "failed"
    return receipt


def _read_logs(row: Mapping[str, Any], output_root: Path) -> str:
    chunks = []
    for stream in ("stdout", "stderr"):
        path = output_root / row[stream]["path"]
        chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def _test_count(name: str, row: Mapping[str, Any], output_root: Path) -> int:
    text = _read_logs(row, output_root)
    if name == "python_unit":
        match = re.search(r"Ran\s+(\d+)\s+tests?", text)
        return int(match.group(1)) if match else 0
    if name == "dashboard_unit":
        match = re.search(r"Tests\s+(\d+)\s+passed", text)
        return int(match.group(1)) if match else 0
    return 0


def source_only_receipt_errors(
    receipt: Mapping[str, Any],
    browser_receipt: Mapping[str, Any] | None,
) -> list[str]:
    errors: list[str] = []
    if receipt.get("task_id") != TASK_ID:
        errors.append("source-only receipt task ID differs")
    if receipt.get("routing_nonce") != ROUTING_NONCE:
        errors.append("source-only receipt routing nonce differs")
    if receipt.get("status") != "passed":
        errors.append("source-only receipt status is not passed")
    errors.extend(environment_identity_errors(receipt))
    source = receipt.get("source", {})
    if not isinstance(source, Mapping):
        source = {}
        errors.append("source-only receipt source identity is missing")
    commit = str(source.get("commit", ""))
    tree = str(source.get("tree", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        errors.append("source-only receipt source commit is invalid")
    if source.get("commit") == BASE_COMMIT:
        errors.append("source-only receipt uses stale base source")
    if not re.fullmatch(r"[0-9a-f]{40}", tree):
        errors.append("source-only receipt source tree is invalid")
    if source.get("worktree_clean") is not True:
        errors.append("source-only receipt source is not clean")
    if receipt.get("source_identity_unchanged") is not True:
        errors.append("source identity changed during source-only CI")
    if receipt.get("workflow_definition_sha256") != sha256_file(
        WORKFLOW_PATH
    ):
        errors.append("source-only workflow SHA-256 differs")
    errors.extend(workflow_image_errors())
    plan = receipt.get("command_plan", {})
    expected_output = Path("/source-only-ci-output")
    expected_plan = command_plan_identity(
        command_plan(expected_output / "source-only-methodology.json"),
        expected_output,
    )
    if not isinstance(plan, Mapping):
        plan = {}
        errors.append("source-only command-plan receipt is missing")
    if plan.get("command_count") != len(REQUIRED_COMMAND_NAMES):
        errors.append("source-only command-plan count differs")
    if plan.get("commands") != expected_plan["commands"]:
        errors.append("source-only command-plan commands differ")
    embedded_plan_hash = hashlib.sha256(
        canonical_bytes(plan.get("commands", []))
    ).hexdigest()
    if (
        plan.get("sha256") != expected_plan["sha256"]
        or plan.get("sha256") != embedded_plan_hash
    ):
        errors.append("source-only command-plan SHA-256 differs")
    rows = receipt.get("commands", [])
    if not isinstance(rows, list) or not all(
        isinstance(row, Mapping) for row in rows
    ):
        rows = []
        errors.append("executed source-only commands are invalid")
    if [row.get("name") for row in rows] != list(
        REQUIRED_COMMAND_NAMES
    ):
        errors.append("executed source-only command plan differs")
    if [
        {"name": row.get("name"), "command": row.get("command")}
        for row in rows
    ] != expected_plan["commands"]:
        errors.append("executed source-only commands differ")
    if not all(row.get("status") == "passed" for row in rows):
        errors.append("not every source-only command passed")
    if not all(row.get("exit_code") == 0 for row in rows):
        errors.append("not every source-only command exited zero")
    if receipt.get("command_count") != len(rows):
        errors.append("source-only executed command count differs")
    for field, expected in INDEPENDENCE_CONTRACT.items():
        if receipt.get(field) is not expected:
            if field == "artifact_backed_target_evidence_imported":
                errors.append(
                    "source-only proof imported artifact-backed evidence"
                )
            else:
                errors.append(
                    f"source-only independence field {field} differs"
                )
    if browser_receipt is None:
        errors.append("source-only browser receipt is missing")
    else:
        errors.extend(browser_receipt_errors(browser_receipt))
        if browser_receipt.get("source") != source:
            errors.append("source-only CI/browser source identities differ")
        for field in (
            "source_only_userspace_image",
            "source_only_userspace_image_digest",
            "source_only_distribution",
            "source_only_glibc",
            "chromium_version",
            "chromium_executable",
            "chromium_executable_sha256",
        ):
            if browser_receipt.get(field) != receipt.get(field):
                errors.append(
                    f"source-only CI/browser {field} identities differ"
                )
        declared = receipt.get("source_only_browser_receipt", {})
        browser_bytes = canonical_bytes(browser_receipt)
        if (
            not str(declared.get("path", "")).strip()
            or declared.get("bytes") != len(browser_bytes)
            or declared.get("sha256")
            != hashlib.sha256(browser_bytes).hexdigest()
        ):
            errors.append("source-only browser receipt hash differs")
        if declared.get("status") != "passed":
            errors.append("source-only browser receipt status differs")
        if receipt.get("test_counts", {}).get(
            "playwright"
        ) != browser_receipt.get("browser_test_count"):
            errors.append("source-only Playwright test count differs")
    counts = receipt.get("test_counts", {})
    if not isinstance(counts, Mapping):
        counts = {}
        errors.append("source-only test counts are invalid")
    for name in ("python_unit", "vitest", "playwright"):
        try:
            count = int(counts.get(name, 0))
        except (TypeError, ValueError):
            count = 0
        if count < 1:
            errors.append(f"source-only {name} test count is zero")
    if receipt.get("validation_errors") not in (None, []):
        errors.append("source-only receipt contains validation errors")
    return errors


def _isolated_environment(
    output_root: Path, browser_result: Path
) -> dict[str, str]:
    home = output_root / "source-only-home"
    cache = output_root / "source-only-tool-cache"
    home.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "XDG_CACHE_HOME": str(cache / "xdg"),
            "UV_CACHE_DIR": str(cache / "uv"),
            "npm_config_cache": str(cache / "npm"),
            "BENCH_PLAYWRIGHT_JSON_OUTPUT": str(browser_result),
            "CI": "1",
        }
    )
    return environment


def run(output: Path, browser_output: Path | None = None) -> dict[str, Any]:
    if platform.python_version() != EXPECTED_PYTHON_VERSION:
        raise RuntimeError(
            "source-only CI requires exactly Python "
            + EXPECTED_PYTHON_VERSION
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
    if source["commit"] == BASE_COMMIT:
        raise RuntimeError(
            "source-only CI rejects the stale task base commit"
        )
    if fixture["entry_count"] < 2:
        raise RuntimeError("checked-in source-only fixture is incomplete")
    environment = environment_identity()
    environment_errors = environment_identity_errors(environment)
    if environment_errors:
        raise RuntimeError("; ".join(environment_errors))
    workflow_errors = workflow_image_errors()
    if workflow_errors:
        raise RuntimeError("; ".join(workflow_errors))
    logs = output.parent / (output.stem + "-logs")
    logs.mkdir(parents=True, exist_ok=True)
    methodology_output = output.parent / "source-only-methodology.json"
    browser_result = output.parent / "source-only-browser-result.json"
    browser_output = (
        browser_output
        if browser_output is not None
        else output.parent / "source-only-browser-receipt.json"
    )
    plan = command_plan(methodology_output)
    plan_errors = command_plan_errors(plan)
    if plan_errors:
        raise RuntimeError("; ".join(plan_errors))
    plan_identity = command_plan_identity(plan, output.parent)
    rows: list[dict[str, Any]] = []
    browser_receipt: dict[str, Any] | None = None
    started = time.monotonic()
    command_environment = _isolated_environment(
        output.parent, browser_result
    )
    receipt: dict[str, Any] = {
        "schema_id": "source-only-ci-receipt-current",
        "task_id": TASK_ID,
        "routing_nonce": ROUTING_NONCE,
        "status": "failed",
        "execution_stratum": "source-only",
        "python_support": PYTHON_POLICY,
        **INDEPENDENCE_CONTRACT,
        "fixture": fixture,
        "source": source,
        "workflow_definition": ".github/workflows/ci.yml",
        "workflow_definition_sha256": sha256_file(WORKFLOW_PATH),
        "command_plan": plan_identity,
        "commands": rows,
        "command_count": 0,
        "test_counts": {
            "python_unit": 0,
            "vitest": 0,
            "playwright": 0,
        },
        **environment,
    }
    write_receipt(output, receipt)
    for name, command in plan:
        stdout_path = logs / f"{name}.stdout.log"
        stderr_path = logs / f"{name}.stderr.log"
        command_started = time.monotonic()
        with stdout_path.open("wb") as stdout, stderr_path.open(
            "wb"
        ) as stderr:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=command_environment,
                stdout=stdout,
                stderr=stderr,
                check=False,
            )
        row = {
            "name": name,
            "command": [
                _portable_value(value, output.parent)
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
        if name == "python_unit":
            receipt["test_counts"]["python_unit"] = _test_count(
                name, row, output.parent
            )
        elif name == "dashboard_unit":
            receipt["test_counts"]["vitest"] = _test_count(
                name, row, output.parent
            )
        elif name == "dashboard_browser" and browser_result.is_file():
            browser_receipt = build_browser_receipt(
                result_path=browser_result,
                source=source,
                environment=environment,
                command_row=row,
            )
            write_receipt(browser_output, browser_receipt)
            receipt["test_counts"]["playwright"] = browser_receipt[
                "browser_test_count"
            ]
            receipt["source_only_browser_receipt"] = {
                "path": browser_output.name,
                "bytes": browser_output.stat().st_size,
                "sha256": sha256_file(browser_output),
                "status": browser_receipt["status"],
            }
        receipt["commands"] = rows
        receipt["command_count"] = len(rows)
        receipt["duration_seconds"] = time.monotonic() - started
        write_receipt(output, receipt)
        if completed.returncode != 0:
            return receipt
    final_source = source_identity()
    source_unchanged = final_source == source
    receipt["source"] = final_source
    receipt["source_identity_unchanged"] = source_unchanged
    receipt["status"] = (
        "passed"
        if source_unchanged
        and browser_receipt is not None
        and browser_receipt.get("status") == "passed"
        and all(row["status"] == "passed" for row in rows)
        else "failed"
    )
    validation_errors = source_only_receipt_errors(
        receipt, browser_receipt
    )
    receipt["validation_errors"] = validation_errors
    if validation_errors:
        receipt["status"] = "failed"
    write_receipt(output, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser-output", type=Path)
    args = parser.parse_args()
    result = run(
        args.output.resolve(),
        (
            args.browser_output.resolve()
            if args.browser_output is not None
            else None
        ),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
