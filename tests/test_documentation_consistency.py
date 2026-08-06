from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CURRENT_DOCUMENTS = (
    "README.md",
    "SPEC.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
    "SUPPORT.md",
    "SCORING-MODEL.md",
)

# Concatenated so this test file never matches its own banned phrases. These
# claims are stale in current documentation: the repository is public and
# pre-1.0, the published suite uses issues 487, 488, and 498, uncertainty is
# the observed repetition range, and the result rule compares fully solved
# runs and task score together.
STALE_CLAIMS = (
    "private " + "pre-release",
    "An run" + " is",
    "issues 486, " + "488, and 498",
    "95% confidence" + " interval",
    "quality is ordered" + " by",
    "task-success" + "-first",
)


class CurrentDocumentationConsistencyTest(unittest.TestCase):
    def test_current_documentation_contains_no_stale_claims(self) -> None:
        paths = [
            ROOT / name
            for name in CURRENT_DOCUMENTS
            if (ROOT / name).exists()
        ]
        paths.extend(sorted((ROOT / "docs").glob("*.md")))
        self.assertGreater(len(paths), 8)
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for claim in STALE_CLAIMS:
                self.assertNotIn(
                    claim,
                    text,
                    f"{path.relative_to(ROOT)} still contains the stale "
                    f"claim {claim!r}",
                )


if __name__ == "__main__":
    unittest.main()
