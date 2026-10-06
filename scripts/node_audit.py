#!/usr/bin/env python3
"""Fail on every dashboard npm advisory except a recorded, still-unfixable allowance."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
ALLOWANCES_PATH = ROOT / "dashboard/audit-allowances.json"
LOCKFILE_PATH = ROOT / "dashboard/package-lock.json"
AUDIT_COMMAND = [
    "npm",
    "audit",
    "--prefix",
    "dashboard",
    "--package-lock-only",
    "--json",
]


def load_allowances(path: Path = ALLOWANCES_PATH) -> list[dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    return sorted(document["allowances"], key=lambda entry: entry["ghsa"])


def locked_versions(path: Path = LOCKFILE_PATH) -> dict[str, list[str]]:
    """Map each package name in the npm lockfile to every version it resolves to."""
    lockfile = json.loads(path.read_text(encoding="utf-8"))
    versions: dict[str, set[str]] = {}
    for key, entry in lockfile["packages"].items():
        if "node_modules/" not in key or "version" not in entry:
            continue
        name = key.rsplit("node_modules/", 1)[-1]
        versions.setdefault(name, set()).add(entry["version"])
    return {name: sorted(found) for name, found in sorted(versions.items())}


def release(version: str) -> tuple[int, int, int]:
    major, minor, patch = re.match(r"(\d+)\.(\d+)\.(\d+)", version).groups()
    return int(major), int(minor), int(patch)


def reported_advisories(
    report: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Map each GHSA in an npm audit JSON report to its package, range and fix state."""
    advisories: dict[str, dict[str, Any]] = {}
    for name, vulnerability in sorted(
        report.get("vulnerabilities", {}).items()
    ):
        for via in vulnerability["via"]:
            # A string names the vulnerable dependency this package reaches it through.
            if isinstance(via, str):
                continue
            ghsa = via["url"].rstrip("/").rsplit("/", 1)[-1]
            advisories[ghsa] = {
                "package": name,
                "range": via["range"],
                "severity": via["severity"],
                "url": via["url"],
                "fix_available": vulnerability["fixAvailable"],
            }
    return advisories


def applicable_fix(
    fix_available: Any, locked: Mapping[str, list[str]]
) -> bool:
    if not isinstance(fix_available, Mapping):
        return fix_available is True
    # npm also offers a downgrade to a release that predates the vulnerable dependency.
    # Only an upgrade, semver-major or not, means a patched release exists.
    current = locked.get(fix_available["name"], [])
    return any(
        release(fix_available["version"]) > release(version) for version in current
    )


def audit_errors(
    report: Mapping[str, Any],
    allowances: list[Mapping[str, Any]],
    locked: Mapping[str, list[str]],
) -> list[str]:
    advisories = reported_advisories(report)
    allowed = {entry["ghsa"]: entry for entry in allowances}
    errors: list[str] = []
    for ghsa, advisory in sorted(advisories.items()):
        if ghsa not in allowed:
            errors.append(
                f"{advisory['severity']} advisory {ghsa} in "
                f"{advisory['package']} {advisory['range']}: "
                f"{advisory['url']}"
            )
    for ghsa, entry in sorted(allowed.items()):
        advisory = advisories.get(ghsa)
        if advisory is None:
            errors.append(
                f"allowance {ghsa} no longer matches any advisory: "
                "remove it from dashboard/audit-allowances.json"
            )
            continue
        if (
            advisory["package"] != entry["package"]
            or advisory["range"] != entry["vulnerable_range"]
        ):
            errors.append(
                f"advisory {ghsa} now reports {advisory['package']} "
                f"{advisory['range']}, the allowance recorded "
                f"{entry['package']} {entry['vulnerable_range']}: "
                "check for a patched release before renewing the allowance"
            )
        # The proof reads the code of these exact versions, so any other version needs a new proof.
        for name, version in sorted(entry["locked_versions"].items()):
            if locked.get(name) != [version]:
                errors.append(
                    f"allowance {ghsa} was proven for {name} {version}, the "
                    f"lockfile has {', '.join(locked.get(name, [])) or 'none'}: "
                    "re-check the proof and update locked_versions"
                )
        if applicable_fix(advisory["fix_available"], locked):
            errors.append(
                f"advisory {ghsa} has a patched release: update to it and "
                "remove the allowance"
            )
    return errors


def run_audit() -> dict[str, Any]:
    # npm audit exits non-zero whenever it reports any advisory, so the JSON decides.
    process = subprocess.run(
        AUDIT_COMMAND,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        check=False,
    )
    report = json.loads(process.stdout)
    if "error" in report:
        raise RuntimeError(f"npm audit failed: {report['error']}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    errors = audit_errors(run_audit(), load_allowances(), locked_versions())
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    print("npm audit: no advisory outside dashboard/audit-allowances.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
