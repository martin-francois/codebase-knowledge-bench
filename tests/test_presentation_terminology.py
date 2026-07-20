from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from current_reports import execution_report, suite_report  # noqa: E402


class PresentationTerminologyTest(unittest.TestCase):
    def test_dashboard_uses_plain_labels_without_renaming_contract_fields(self) -> None:
        descriptors = json.loads(
            (ROOT / "dashboard/src/metric-descriptors.json").read_text(encoding="utf-8")
        )
        weighted = descriptors["modeled_weighted_token_load"]
        self.assertEqual("Weighted tokens", weighted["label"])
        self.assertEqual("modeled_weighted_token_load", weighted["absoluteField"])
        self.assertEqual("modeled_weighted_token_load_mean", weighted["meanField"])

        analysis = (ROOT / "dashboard/src/analysis.ts").read_text(encoding="utf-8")
        dashboard = (ROOT / "dashboard/src/main.tsx").read_text(encoding="utf-8")
        self.assertIn('behavioral_correctness: {label: "Correctness"}', analysis)
        self.assertIn('<option value="mean">Average</option>', dashboard)
        self.assertIn("<th>Tool or baseline</th>", dashboard)
        self.assertIn('title: "Tool or baseline"', dashboard)
        self.assertNotIn(">Mean</option>", dashboard)
        self.assertNotIn("canonical 95% paired intervals", dashboard)

    def test_human_reports_use_plain_labels(self) -> None:
        report = execution_report({"variants": []})
        self.assertIn("| tool or baseline |", report)
        self.assertIn("| correctness |", report)
        self.assertIn("| weighted tokens |", report)
        self.assertNotIn("behavioral correctness", report.lower())

        suite = suite_report("example", [], {"by_variant": {}})
        self.assertIn("| tool or baseline |", suite)
        self.assertIn("| weighted tokens per success |", suite)

    def test_operator_guidance_uses_runs_and_tools(self) -> None:
        example = (ROOT / "examples/custom-suite.toml").read_text(encoding="utf-8")
        self.assertIn("# Tools compared for every selected challenge.", example)
        self.assertIn("scheduled benchmark run", example)
        self.assertNotIn("implementation arms", example)
        self.assertNotIn("non-baseline workflow", example)

        suite_runner = (ROOT / "scripts/run_benchmark_suite.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("expensive benchmark runs", suite_runner)
        self.assertIn("primary operational tool comparison", suite_runner)
        self.assertNotIn("primary operational workflow ranking", suite_runner)
        self.assertNotIn("lowest modeled weighted token load", suite_runner)


if __name__ == "__main__":
    unittest.main()
