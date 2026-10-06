#!/usr/bin/env python3
"""Focused tests for the dashboard npm advisory gate."""

from __future__ import annotations

import copy
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from node_audit import audit_errors, load_allowances  # noqa: E402


BRACES_GHSA = "GHSA-vfj7-8cjw-p6xm"
SINGLEFILE_DOWNGRADE = {
    "name": "vite-plugin-singlefile",
    "version": "0.9.0",
    "isSemVerMajor": True,
}


def advisory(name: str, ghsa: str, vulnerable_range: str) -> dict:
    return {
        "name": name,
        "severity": "high",
        "url": f"https://github.com/advisories/{ghsa}",
        "range": vulnerable_range,
    }


def braces_report() -> dict:
    """The npm audit JSON shape for the braces chain in the current lockfile."""
    return {
        "vulnerabilities": {
            "braces": {
                "via": [advisory("braces", BRACES_GHSA, "<=3.0.3")],
                "fixAvailable": dict(SINGLEFILE_DOWNGRADE),
            },
            "micromatch": {
                "via": ["braces"],
                "fixAvailable": dict(SINGLEFILE_DOWNGRADE),
            },
            "vite-plugin-singlefile": {
                "via": ["micromatch"],
                "fixAvailable": dict(SINGLEFILE_DOWNGRADE),
            },
        }
    }


class NodeAuditTest(unittest.TestCase):
    def setUp(self) -> None:
        self.allowances = load_allowances()

    def test_allowed_unfixable_advisory_passes(self) -> None:
        self.assertEqual([], audit_errors(braces_report(), self.allowances))

    def test_empty_report_without_allowances_passes(self) -> None:
        self.assertEqual([], audit_errors({"vulnerabilities": {}}, []))

    def test_advisory_outside_the_allowances_fails(self) -> None:
        report = braces_report()
        report["vulnerabilities"]["source-map-js"] = {
            "via": [
                advisory("source-map-js", "GHSA-68fv-2mgg-jv7q", ">=1.0.0 <1.2.2")
            ],
            "fixAvailable": True,
        }

        errors = audit_errors(report, self.allowances)

        self.assertEqual(1, len(errors))
        self.assertIn("GHSA-68fv-2mgg-jv7q", errors[0])

    def test_allowance_expires_when_the_advisory_leaves_the_lockfile(self) -> None:
        errors = audit_errors({"vulnerabilities": {}}, self.allowances)

        self.assertEqual(1, len(errors))
        self.assertIn("no longer matches any advisory", errors[0])

    def test_allowance_expires_when_the_vulnerable_range_changes(self) -> None:
        report = braces_report()
        report["vulnerabilities"]["braces"]["via"] = [
            advisory("braces", BRACES_GHSA, "<3.0.4")
        ]

        errors = audit_errors(report, self.allowances)

        self.assertEqual(1, len(errors))
        self.assertIn("now reports braces <3.0.4", errors[0])

    def test_allowance_expires_when_npm_can_apply_a_fix(self) -> None:
        for fix in (
            True,
            {"name": "vite-plugin-singlefile", "version": "2.3.4", "isSemVerMajor": False},
        ):
            with self.subTest(fix=fix):
                report = copy.deepcopy(braces_report())
                report["vulnerabilities"]["braces"]["fixAvailable"] = fix

                errors = audit_errors(report, self.allowances)

                self.assertEqual(1, len(errors))
                self.assertIn("has a fix npm can apply", errors[0])

    def test_braces_proof_holds_for_the_current_vite_config(self) -> None:
        # The braces allowance relies on vite-plugin-singlefile never calling micromatch,
        # which holds only while it gets no inlinePattern.
        allowed = {entry["ghsa"] for entry in self.allowances}
        if BRACES_GHSA not in allowed:
            self.skipTest("no braces allowance")
        config = (ROOT / "dashboard/vite.config.ts").read_text(encoding="utf-8")

        calls = re.findall(r"viteSingleFile\(([^)]*)\)", config)

        self.assertEqual(
            [""],
            calls,
            "viteSingleFile() now takes options: re-check the braces proof "
            "in dashboard/audit-allowances.json",
        )


if __name__ == "__main__":
    unittest.main()
