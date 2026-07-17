from __future__ import annotations

import copy
import ast
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from current_pipeline import RAW_METADATA_FIELDS
from current_row import EXECUTION_FIELDS
from external_review_delivery import _payload
from mutation_calibration import classify_calibration
from protected_verifier import load_channel_plan, validate_selector_isolation


class ProtectedChannelPlanTests(unittest.TestCase):
    def contracts(self):
        for issue in ("issue-486", "issue-488", "issue-498"):
            contract = json.loads((ROOT / f"verification/methodology-current/contracts/{issue}.json").read_text())
            channel_plan = json.loads((ROOT / f"verification/methodology-current/channel-plans/{issue}.json").read_text())
            yield issue, contract, channel_plan

    def test_PCI_001_current_contract_has_only_channel_specific_overlays(self):
        schema = json.loads((ROOT / "schemas/requirement-contract-current.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        for issue, contract, channel_plan in self.contracts():
            with self.subTest(issue=issue):
                Draft202012Validator(schema).validate(contract)
                self.assertNotIn("protected_overlay", contract)
                self.assertNotIn("applies_to_channels", json.dumps(contract))
                self.assertNotIn("protected_channels", contract)
                self.assertEqual({"common", "direct", "extended"}, set(channel_plan["channels"]))
                overlay_paths = [
                    row["overlay"]["path"]
                    for row in channel_plan["channels"].values()
                    if row["overlay"] is not None
                ]
                self.assertEqual(len(overlay_paths), len(set(overlay_paths)))
                self.assertTrue(all(path.endswith(("-common.patch", "-direct.patch", "-extended.patch")) for path in overlay_paths))

    def test_PCI_002_expected_selectors_are_disjoint(self):
        total_common = 0
        for issue, contract, channel_plan in self.contracts():
            plan = load_channel_plan(channel_plan, contract, ROOT)
            expected = {
                channel: set(plan["channels"][channel]["expected_selectors"])
                for channel in ("common", "direct", "extended")
            }
            total_common += len(expected["common"])
            self.assertFalse(expected["common"] & expected["direct"], issue)
            self.assertFalse(expected["common"] & expected["extended"], issue)
            self.assertFalse(expected["direct"] & expected["extended"], issue)
        self.assertEqual(1171, total_common)

    def test_PCI_003_observed_selector_isolation_has_narrow_negative(self):
        _, contract, channel_plan = next(row for row in self.contracts() if row[0] == "issue-488")
        plan = load_channel_plan(channel_plan, contract, ROOT)
        observed = {
            channel: [
                {"junit_selector": selector, "status": "passed", "junit_xml_path": "TEST.xml"}
                for selector in plan["channels"][channel]["expected_selectors"]
            ]
            for channel in ("common", "direct", "extended")
        }
        _, audit = validate_selector_isolation(plan, observed)
        self.assertEqual("passed", audit["status"])
        contaminated = copy.deepcopy(observed)
        contaminated["common"].append(copy.deepcopy(contaminated["direct"][0]))
        with self.assertRaisesRegex(ValueError, "cross-channel selector overlap"):
            validate_selector_isolation(plan, contaminated)

    def test_PCI_004_common_policy_has_no_file_copy_architecture(self):
        source = (ROOT / "scripts/protected_verifier.py").read_text()
        self.assertNotIn("files_copied", source)
        self.assertNotIn("def _protected_channel(", (ROOT / "scripts/run_benchmark.py").read_text())


class CompleteRederivationTests(unittest.TestCase):
    def test_RDR_001_descriptor_is_complete_and_raw_metadata_excludes_derived_fields(self):
        self.assertEqual(98, len(EXECUTION_FIELDS))
        self.assertTrue(set(RAW_METADATA_FIELDS) < set(EXECUTION_FIELDS))
        source = (ROOT / "scripts/validate_benchmark_run.py").read_text()
        self.assertIn("validate_rederived_row", source)
        self.assertIn("complete current-row rederivation failed", source)
        self.assertNotIn("expected_fields = {", source)

    def test_RDR_002_raw_metadata_schema_is_strict(self):
        schema = json.loads((ROOT / "schemas/raw-run-metadata.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(schema["properties"]["metadata"]["additionalProperties"])
        self.assertFalse(schema["properties"]["evidence"]["additionalProperties"])

    def test_RDR_003_one_token_parser_serves_writer_and_validator(self):
        definitions = sum(
            1
            for path in (ROOT / "scripts").glob("*.py")
            for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "token_usage_from_codex_jsonl"
        )
        self.assertEqual(1, definitions)
        self.assertIn(
            "token_usage_from_codex_jsonl(run_jsonl)",
            (ROOT / "scripts/current_pipeline.py").read_text(),
        )


class DownstreamConsumerTests(unittest.TestCase):
    def test_DOWNSTREAM_001_shadow_and_mutation_use_live_executor(self):
        shadow = (ROOT / "scripts/methodology_fixture.py").read_text()
        mutation = (ROOT / "scripts/mutation_calibration.py").read_text()
        self.assertIn("preflight_issue", shadow)
        self.assertNotIn("def _write_junit", shadow)
        self.assertIn("execute_protected_verification", mutation)
        self.assertIn("configured_common_full_pass", mutation)

    def test_DOWNSTREAM_002_dashboard_uses_honest_common_wording(self):
        text = (ROOT / "dashboard/src/main.tsx").read_text() + (ROOT / "dashboard/src/analysis.ts").read_text()
        self.assertIn("Configured protected common", text)
        self.assertIn("Direct and diagnostic selectors cannot appear", text)

    def test_DOWNSTREAM_003_common_failure_is_collateral_regression(self):
        result = classify_calibration(
            {"calibration_kind": "targeted"},
            intended_failure=True,
            unexpected_requested_collateral=set(),
            regression_gates_pass=True,
            common_pass=True,
            overlap_pass=True,
            process_valid=True,
        )
        self.assertEqual("killed", result["status"])
        self.assertTrue(result["calibrated"])
        rejected = classify_calibration(
            {"calibration_kind": "targeted"},
            intended_failure=True,
            unexpected_requested_collateral=set(),
            regression_gates_pass=False,
            common_pass=False,
            overlap_pass=True,
            process_valid=True,
        )
        self.assertEqual("collateral_regression", rejected["status"])
        self.assertFalse(rejected["calibrated"])

    def test_DOWNSTREAM_004_outer_and_inner_delivery_identities_are_fixed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            response = root / "agent-response.md"
            response.write_text("review response\n")
            inner = root / "review-handoff.zip"
            checksum = root / "review-handoff.zip.sha256"
            receipt = root / "review-handoff.zip.validation.json"
            with zipfile.ZipFile(inner, "w") as archive:
                archive.writestr(
                    "verification/independent-verifier/"
                    "independent_verifier.sh",
                    "#!/bin/sh\n",
                )
            for path in (checksum, receipt):
                path.write_bytes(b"fixture")
            self.assertEqual(
                {
                    "agent-response.md",
                    "independent-verifier.sh",
                    "review-handoff/review-handoff.zip",
                    "review-handoff/review-handoff.zip.sha256",
                    "review-handoff/review-handoff.zip.validation.json",
                },
                set(_payload(inner, checksum, receipt, response)),
            )
            alias = root / "delivery.zip"
            alias.write_bytes(b"fixture")
            with self.assertRaisesRegex(ValueError, "review-handoff.zip"):
                _payload(alias, checksum, receipt, response)


if __name__ == "__main__":
    unittest.main()
