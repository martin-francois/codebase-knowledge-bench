from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLANS = ROOT / "verification" / "methodology-current" / "channel-plans"


class DeterministicProtectedTestCommandTest(unittest.TestCase):
    def test_every_protected_maven_command_disables_junit_parallelism(self) -> None:
        for plan_path in sorted(PLANS.glob("issue-*.json")):
            with self.subTest(plan=plan_path.name):
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                for channel_name, channel in plan["channels"].items():
                    command = channel["command"]
                    if command and command.startswith("./mvnw "):
                        self.assertIn(
                            "-Djunit.parallel.enabled=false",
                            command,
                            f"{plan_path.name}:{channel_name}",
                        )


if __name__ == "__main__":
    unittest.main()
