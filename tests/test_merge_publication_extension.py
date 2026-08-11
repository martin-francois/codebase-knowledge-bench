from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from merge_publication_extension import exact_scope, read_compact_publication  # noqa: E402


class PublicationExtensionMergeTest(unittest.TestCase):
    def test_current_compact_publication_has_exact_authorized_historical_scope(self) -> None:
        manifest, research = read_compact_publication(ROOT / "publication")
        rows = research["sourceRecords"]["suiteResults"]["runs"]
        exact_scope(
            rows,
            issues=["issue-487", "issue-488", "issue-498"],
            repetitions=4,
            tools=[
                "baseline-none",
                "sverklo",
                "code-review-graph",
                "gitnexus",
                "jcodemunch-mcp",
                "serena",
                "graphify",
            ],
            label="fixture base publication",
        )
        self.assertEqual(84, manifest["expectedRunCount"])

    def test_scope_rejects_duplicate_or_historical_tool_extension_rows(self) -> None:
        rows = [
            {
                "issue_id": "issue-487",
                "repetition": repetition,
                "tool": "prethink",
                "operational_rank_eligible": True,
            }
            for repetition in range(1, 5)
        ]
        rows[3] = dict(rows[0])
        with self.assertRaisesRegex(SystemExit, "exact key scope"):
            exact_scope(
                rows,
                issues=["issue-487"],
                repetitions=4,
                tools=["prethink"],
                label="extension",
            )


if __name__ == "__main__":
    unittest.main()
