from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import launch_accounting
import final_arm_recovery


def legacy_ledger() -> dict:
    arms = {}
    for index in range(7):
        key = f"issue-488::3::tool-{index}"
        terminal = index != 6
        arms[key] = {
            "launch_count": 1,
            "terminal": terminal,
            "status": "solve_completed" if terminal else "model_service_unavailable",
            "attempts": [{"started_at": f"2026-07-14T00:00:0{index}+00:00", "terminal": terminal}],
        }
    pending = "issue-488::3::tool-6"
    arms[pending]["launch_count"] = 2
    arms[pending]["attempts"].append({
        "started_at": "2026-07-14T01:00:00+00:00", "terminal": False,
    })
    return {
        "schema_version": "canonical-execution-ledger-v1",
        "maximum_launches": 8,
        "maximum_launches_per_arm": 2,
        "implementation_child_launches": 8,
        "arms": arms,
        "events": [],
    }


class LaunchAccountingTest(unittest.TestCase):
    def test_migration_distinguishes_pre_spawn_rejection(self) -> None:
        legacy = legacy_ledger()
        pending = "issue-488::3::tool-6"
        spawned = {key: [0] for key in legacy["arms"]}
        migrated = launch_accounting.migrate_legacy_ledger(
            legacy,
            spawned_attempt_indexes=spawned,
            pre_spawn_rejections={pending: {1: "sealed repository is not clean"}},
        )
        self.assertEqual(7, migrated["actual_implementation_child_spawns"])
        self.assertEqual(8, migrated["orchestration_attempts"])
        self.assertEqual(1, migrated["arms"][pending]["actual_child_spawn_count"])
        self.assertTrue(migrated["arms"][pending]["attempts"][1]["pre_spawn_rejected"])
        self.assertFalse(
            migrated["arms"][pending]["attempts"][1]["counts_as_implementation_child_launch"]
        )
        self.assertEqual([], launch_accounting.validate_ledger_accounting(migrated))

    def test_reservation_does_not_consume_child_budget(self) -> None:
        ledger = {
            "maximum_launches": 2,
            "maximum_launches_per_arm": 2,
            "orchestration_attempts": 0,
            "actual_implementation_child_spawns": 0,
            "arms": {"arm": {"attempts": [], "actual_child_spawn_count": 0}},
        }
        attempt = launch_accounting.reserve_attempt(ledger, "arm", started_at="2026-01-01T00:00:00+00:00")
        self.assertEqual(0, ledger["actual_implementation_child_spawns"])
        launch_accounting.mark_pre_spawn_rejected(ledger, "arm", "dirty")
        self.assertFalse(attempt["counts_as_implementation_child_launch"])
        self.assertEqual(0, ledger["actual_implementation_child_spawns"])

    def test_spawn_receipt_consumes_budget_once_and_third_spawn_fails_closed(self) -> None:
        ledger = {
            "maximum_launches": 2,
            "maximum_launches_per_arm": 2,
            "orchestration_attempts": 0,
            "actual_implementation_child_spawns": 0,
            "arms": {"arm": {"attempts": [], "actual_child_spawn_count": 0}},
        }
        for sequence in range(2):
            attempt = launch_accounting.reserve_attempt(
                ledger, "arm", started_at=f"2026-01-01T00:00:0{sequence}+00:00"
            )
            receipt = launch_accounting.child_spawn_receipt(
                "arm", attempt, 100 + sequence, observed_at=f"2026-01-01T00:01:0{sequence}+00:00"
            )
            launch_accounting.record_child_spawn(ledger, "arm", receipt)
        launch_accounting.reserve_attempt(ledger, "arm", started_at="2026-01-01T00:00:03+00:00")
        receipt = launch_accounting.child_spawn_receipt(
            "arm", ledger["arms"]["arm"]["attempts"][-1], 103,
            observed_at="2026-01-01T00:01:03+00:00",
        )
        with self.assertRaisesRegex(ValueError, "budget exhausted"):
            launch_accounting.record_child_spawn(ledger, "arm", receipt)

    def test_real_spawn_cannot_be_reclassified_as_pre_spawn(self) -> None:
        ledger = {
            "maximum_launches": 2, "maximum_launches_per_arm": 2,
            "orchestration_attempts": 0, "actual_implementation_child_spawns": 0,
            "arms": {"arm": {"attempts": [], "actual_child_spawn_count": 0}},
        }
        attempt = launch_accounting.reserve_attempt(ledger, "arm")
        launch_accounting.record_child_spawn(
            ledger, "arm", launch_accounting.child_spawn_receipt("arm", attempt, 123)
        )
        with self.assertRaisesRegex(ValueError, "cannot classify"):
            launch_accounting.mark_pre_spawn_rejected(ledger, "arm", "dirty")

    def test_migration_fails_closed_without_attempt_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "lacks spawn or rejection evidence"):
            launch_accounting.migrate_legacy_ledger(
                legacy_ledger(), spawned_attempt_indexes={}, pre_spawn_rejections={}
            )

    def test_virtual_restoration_digest_is_exact_or_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            (repo / "source.txt").write_text("candidate\n")
            (repo / "target").mkdir()
            (repo / "target" / "generated.txt").write_text("generated\n")
            expected = final_arm_recovery.virtual_smoke_state_digest(
                {"repo": repo},
                replacement_files={"source.txt": b"base\n"},
                excluded_repo_prefixes=("target",),
            )
            actual = final_arm_recovery.virtual_smoke_state_digest(
                {"repo": repo},
                replacement_files={"source.txt": b"base\n"},
                excluded_repo_prefixes=("target",),
            )
            mismatch = final_arm_recovery.virtual_smoke_state_digest(
                {"repo": repo}, replacement_files={}, excluded_repo_prefixes=("target",)
            )
            self.assertEqual(expected, actual)
            self.assertNotEqual(expected, mismatch)
            self.assertEqual("candidate\n", (repo / "source.txt").read_text())

    def test_attempt_manifest_is_written_after_all_mutable_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            execution = root / "execution"
            run = execution / "runs" / "run-007"
            repo = execution / "sealed-repos" / "run-007" / "repo"
            run.mkdir(parents=True)
            repo.mkdir(parents=True)
            (run / "run.jsonl").write_text('{"type":"turn.failed"}\n')
            (run / "diff.patch").write_text("patch\n")
            import subprocess
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Fixture"], cwd=repo, check=True)
            (repo / "file.txt").write_text("base\n")
            subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            (repo / "file.txt").write_text("candidate\n")
            destination = root / "attempt"
            payload = final_arm_recovery.copy_interrupted_attempt(execution, destination, "run-007")
            for entry in payload["files"]:
                path = destination / entry["path"]
                self.assertEqual(entry["bytes"], path.stat().st_size)
                self.assertEqual(entry["sha256"], final_arm_recovery.sha256_file(path))
            self.assertIn("dirty-git-status.txt", {row["path"] for row in payload["files"]})
            self.assertIn("dirty-worktree.patch", {row["path"] for row in payload["files"]})


if __name__ == "__main__":
    unittest.main()
