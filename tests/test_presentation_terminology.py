from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from current_reports import execution_report, suite_report  # noqa: E402


class PresentationTerminologyTest(unittest.TestCase):
    def test_machine_contract_uses_tool_run_and_comparison_terms(self) -> None:
        forbidden = re.compile(
            r"(^|_)(arm|arms|variant|variants|treatment|treatments|canonical)($|_)"
            r"|behavioral_correctness|modeled_weighted|warm_workflow|run_records|tool_rows"
            r"|execution_ids?$"
        )

        def contract_names(value: object) -> list[str]:
            if isinstance(value, dict):
                names = list(value)
                for key, item in value.items():
                    if key == "field" and isinstance(item, str):
                        names.append(item)
                    names.extend(contract_names(item))
                return names
            if isinstance(value, list):
                return [
                    name
                    for item in value
                    for name in contract_names(item)
                ]
            return []

        for relative in (
            "schemas/execution-results.schema.json",
            "schemas/suite-results.schema.json",
            "schemas/operator-summary.schema.json",
            "fixtures/current-execution-results.json",
            "verification/methodology-current/execution-field-provenance.json",
        ):
            document = json.loads((ROOT / relative).read_text(encoding="utf-8"))
            with self.subTest(relative=relative):
                for name in contract_names(document):
                    self.assertIsNone(forbidden.search(name), name)

        execution_schema = json.loads(
            (ROOT / "schemas/execution-results.schema.json").read_text(
                encoding="utf-8"
            )
        )
        suite_schema = json.loads(
            (ROOT / "schemas/suite-results.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("runs", execution_schema["required"])
        self.assertIn("runs", suite_schema["required"])
        self.assertIn("comparison_records", suite_schema["required"])

    def test_dashboard_and_contract_use_the_same_terms(self) -> None:
        descriptors = json.loads(
            (ROOT / "dashboard/src/metric-descriptors.json").read_text(encoding="utf-8")
        )
        weighted = descriptors["weighted_tokens"]
        self.assertEqual("Weighted tokens", weighted["label"])
        self.assertEqual("weighted_tokens", weighted["absoluteField"])
        self.assertEqual("weighted_tokens_average", weighted["averageField"])

        analysis = (ROOT / "dashboard/src/analysis.ts").read_text(encoding="utf-8")
        dashboard = (ROOT / "dashboard/src/main.tsx").read_text(encoding="utf-8")
        self.assertIn('correctness: {label: "Correctness"}', analysis)
        self.assertIn('<option value="average">Average</option>', dashboard)
        self.assertIn("<th>Tool or baseline</th>", dashboard)
        self.assertIn('title: "Tool or baseline"', dashboard)
        self.assertNotIn(">Mean</option>", dashboard)
        self.assertNotIn("published 95% paired intervals", dashboard)

    def test_human_reports_use_plain_labels(self) -> None:
        report = execution_report({"tools": []})
        self.assertIn("| tool or baseline |", report)
        self.assertIn("| correctness |", report)
        self.assertIn("| weighted tokens |", report)
        self.assertNotIn("behavioral correctness", report.lower())

        suite = suite_report("example", [], {"by_tool": {}})
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
        self.assertNotIn("primary operational run ranking", suite_runner)
        self.assertNotIn("lowest weighted tokens", suite_runner)

        scoring = (ROOT / "SCORING-MODEL.md").read_text(encoding="utf-8")
        tool_guide = (ROOT / "tool-guides/quickstart-sources.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("## Protected correctness", scoring)
        self.assertIn("Operational tool comparison", scoring)
        self.assertNotIn("operational run ranking", tool_guide)

    def test_generated_human_outputs_use_plain_run_labels(self) -> None:
        operator_summary = (ROOT / "scripts/operator_summary.py").read_text(
            encoding="utf-8"
        )
        readiness = (ROOT / "scripts/autonomous_readiness.py").read_text(
            encoding="utf-8"
        )
        published_suite = (ROOT / "scripts/published_suite.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("| Tool or baseline |", operator_summary)
        self.assertIn("Attribution-supported runs", operator_summary)
        self.assertIn("Maximum new benchmark runs", readiness)
        self.assertIn("Completed benchmark runs", published_suite)
        self.assertIn("| Benchmark run |", published_suite)
        self.assertNotIn('"# Published execution ledger"', published_suite)


if __name__ == "__main__":
    unittest.main()
