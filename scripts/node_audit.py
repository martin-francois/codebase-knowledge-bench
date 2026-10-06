#!/usr/bin/env python3
"""Fail on every dashboard npm advisory except a recorded, still-unfixable allowance."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
ALLOWANCES_PATH = ROOT / "dashboard/audit-allowances.json"
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


def applicable_fix(fix_available: Any) -> bool:
    # npm offers a semver-major "fix" that downgrades the dependent package; that is not a fix.
    if isinstance(fix_available, Mapping):
        return not fix_available.get("isSemVerMajor", False)
    return fix_available is True


def audit_errors(
    report: Mapping[str, Any],
    allowances: list[Mapping[str, Any]],
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
        if applicable_fix(advisory["fix_available"]):
            errors.append(
                f"advisory {ghsa} has a fix npm can apply: run npm audit fix "
                "and remove the allowance"
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
    errors = audit_errors(run_audit(), load_allowances())
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    print("npm audit: no advisory outside dashboard/audit-allowances.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
