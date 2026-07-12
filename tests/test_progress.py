#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import time
import unittest
import ast
import importlib.util
import os
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from benchmark_config import FIELDS
from benchmark_progress import ARM_STAGES, DurationHistory, ProgressReporter, estimate_seconds, render_line, stage_fingerprint, unclassified_config_keys


class ProgressTest(unittest.TestCase):
    def context(self, **changes):
        value = {"issue": "#8", "repository_tree": "tree-a", "reference_commit": "ref-a", "treatment": "serena", "model": "gpt-5.6-sol", "reasoning_effort": "high", "yolo": "true", "host": {"system": "Linux", "machine": "x86_64"}, "verification_hash": "v", "issue_contract_hash": "p", "reference_hash": "r"}
        value.update(changes)
        return value

    def observation(self, stage, context, duration, suite="suite-a", suffix="1"):
        fingerprint, inputs = stage_fingerprint(stage, context)
        return {"observation_id": f"{suite}-{stage}-{suffix}", "timestamp": f"2026-01-01T00:00:{int(suffix):02d}+00:00", "suite_id": suite, "run_id": "run", "stage": stage, "outcome": "completed", "duration_seconds": duration, "cohort_fingerprint": fingerprint, "fingerprint_inputs": inputs}

    def test_every_public_setting_has_timing_classification(self):
        self.assertEqual(set(), unclassified_config_keys(FIELDS))

    def test_suite_coordinator_does_not_shadow_configured_variants_function(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "scripts" / "run_benchmark_suite.py").read_text())
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_main")
        assigned = {target.id for node in ast.walk(main) if isinstance(node, (ast.Assign, ast.AnnAssign)) for target in ([*node.targets] if isinstance(node, ast.Assign) else [node.target]) if isinstance(target, ast.Name)}
        self.assertNotIn("configured_variants", assigned)

    def test_identity_changes_do_not_change_solve_cohort(self):
        self.assertEqual(stage_fingerprint("solve", self.context(suite_id="one", repetitions=1))[0], stage_fingerprint("solve", self.context(suite_id="two", repetitions=9))[0])

    def test_model_and_reasoning_switch_selects_retained_cohort(self):
        high = stage_fingerprint("solve", self.context(reasoning_effort="high"))[0]
        low = stage_fingerprint("solve", self.context(reasoning_effort="low"))[0]
        back = stage_fingerprint("solve", self.context(reasoning_effort="high"))[0]
        self.assertNotEqual(high, low)
        self.assertEqual(high, back)

    def test_every_solve_behavior_input_invalidates_its_cohort(self):
        base = self.context(
            codex_version="1", prompt_hash="p1", sanitized_issue_hash="i1",
            tool_config="c1", sandbox="workspace-write", network_mode="isolated",
            timeout=100, retry_policy=1, harness_version="h1",
        )
        changes = {
            "repository_tree": "tree-b", "issue": "#9", "treatment": "graphify",
            "adapter_version": "a2", "tool_version": "2", "model": "other",
            "reasoning_effort": "low", "yolo": "false", "codex_version": "2",
            "prompt_hash": "p2", "sanitized_issue_hash": "i2", "tool_config": "c2", "indexed_state": "s2",
            "sandbox": "read-only", "network_mode": "available", "timeout": 200,
            "retry_policy": 2, "harness_version": "h2",
        }
        original = stage_fingerprint("solve", base)[0]
        for key, value in changes.items():
            with self.subTest(key=key):
                self.assertNotEqual(original, stage_fingerprint("solve", {**base, key: value})[0])

    def test_cold_warm_and_tool_changes_only_invalidate_affected_preparation(self):
        base = self.context(tool_version="1", cache_state="cold", adapter_version="a1")
        for stage in ("installation", "setup", "indexing"):
            with self.subTest(stage=stage):
                self.assertNotEqual(stage_fingerprint(stage, base)[0], stage_fingerprint(stage, {**base, "cache_state": "reused"})[0])
                self.assertNotEqual(stage_fingerprint(stage, base)[0], stage_fingerprint(stage, {**base, "tool_version": "2"})[0])
        self.assertEqual(stage_fingerprint("solve", base)[0], stage_fingerprint("solve", {**base, "cache_state": "reused"})[0])

    def test_stage_categories_never_share_a_fingerprint(self):
        fingerprints = {stage_fingerprint(stage, self.context())[0] for stage in ARM_STAGES}
        self.assertEqual(len(ARM_STAGES), len(fingerprints))

    def test_fingerprint_is_canonical_across_mapping_order(self):
        left = self.context(host={"machine": "x86_64", "system": "Linux"})
        right = self.context(host={"system": "Linux", "machine": "x86_64"})
        self.assertEqual(stage_fingerprint("solve", left)[0], stage_fingerprint("solve", right)[0])

    def test_hidden_reference_change_only_invalidates_reference(self):
        self.assertEqual(stage_fingerprint("solve", self.context(reference_commit="a"))[0], stage_fingerprint("solve", self.context(reference_commit="b"))[0])
        self.assertNotEqual(stage_fingerprint("reference_conformance", self.context(reference_commit="a"))[0], stage_fingerprint("reference_conformance", self.context(reference_commit="b"))[0])

    def test_hidden_overlay_change_invalidates_contract_but_not_solve(self):
        left = self.context(issue_contract_hash="overlay-a")
        right = self.context(issue_contract_hash="overlay-b")
        self.assertEqual(stage_fingerprint("solve", left)[0], stage_fingerprint("solve", right)[0])
        self.assertNotEqual(stage_fingerprint("issue_contract", left)[0], stage_fingerprint("issue_contract", right)[0])

    def test_tool_version_invalidates_index_cohort(self):
        self.assertNotEqual(stage_fingerprint("indexing", self.context(tool_version="1"))[0], stage_fingerprint("indexing", self.context(tool_version="2"))[0])

    def test_solve_cohort_includes_execution_and_context_provenance(self):
        required = {"codex_version", "prompt_hash", "sanitized_issue_hash", "tool_config", "indexed_state", "sandbox", "network_mode", "harness_version"}
        from benchmark_progress import STAGE_INPUT_KEYS
        self.assertTrue(required.issubset(STAGE_INPUT_KEYS["solve"]))

    def test_fingerprint_and_estimate_are_stable_across_hash_seeds(self):
        script = """
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, 'scripts')
from benchmark_progress import DurationHistory, estimate_seconds, stage_fingerprint
context = {'issue':'#8','repository_tree':'tree','treatment':'serena','model':'gpt-5.6-sol','host':{'system':'Linux','machine':'x86_64'}}
with tempfile.TemporaryDirectory() as tmp:
    history = DurationHistory(Path(tmp) / 'history.json')
    fingerprint, inputs = stage_fingerprint('solve', context)
    for suffix, duration in [('b', 20), ('a', 10)]:
        history.append({'observation_id':suffix,'timestamp':suffix,'suite_id':'suite','run_id':'run','stage':'solve','outcome':'completed','duration_seconds':duration,'cohort_fingerprint':fingerprint,'fingerprint_inputs':inputs})
    print(json.dumps({'fingerprint':fingerprint,'estimate':estimate_seconds(history,[('solve',context)],suite_id='suite',min_samples=1)}, sort_keys=True))
"""
        outputs = []
        for seed in ("1", "2", "3"):
            env = {**os.environ, "PYTHONHASHSEED": seed}
            result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], env=env, text=True, capture_output=True, check=True)
            outputs.append(result.stdout)
        self.assertEqual(1, len(set(outputs)))

    def test_zero_history_and_deterministic_median(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = DurationHistory(Path(tmp) / "history.json")
            plan = [("solve", self.context())]
            self.assertEqual((None, "insufficient_history", 0), estimate_seconds(history, plan, suite_id="suite-a", min_samples=1))
            history.append(self.observation("solve", self.context(), 20, suffix="1"))
            history.append(self.observation("solve", self.context(), 10, suffix="2"))
            self.assertEqual((15.0, "current_suite", 2), estimate_seconds(history, plan, suite_id="suite-a", min_samples=1))

    def test_minimum_sample_threshold_and_prior_suite_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = DurationHistory(Path(tmp) / "history.json")
            context = self.context()
            history.append(self.observation("solve", context, 10, suite="prior", suffix="1"))
            self.assertEqual((None, "insufficient_history", 0), estimate_seconds(history, [("solve", context)], suite_id="current", min_samples=2))
            history.append(self.observation("solve", context, 20, suite="prior", suffix="2"))
            self.assertEqual((15.0, "persisted_exact_cohort", 2), estimate_seconds(history, [("solve", context)], suite_id="current", min_samples=2))

    def test_current_suite_samples_take_precedence_over_prior_suite(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = DurationHistory(Path(tmp) / "history.json")
            context = self.context()
            history.append(self.observation("solve", context, 100, suite="prior", suffix="1"))
            history.append(self.observation("solve", context, 20, suite="current", suffix="2"))
            self.assertEqual((20.0, "current_suite", 1), estimate_seconds(history, [("solve", context)], suite_id="current", min_samples=1))

    def test_failed_and_censored_do_not_inform_success_eta(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = DurationHistory(Path(tmp) / "history.json")
            row = self.observation("solve", self.context(), 4)
            row["outcome"] = "timed_out"
            history.append(row)
            self.assertIsNone(estimate_seconds(history, [("solve", self.context())], suite_id="suite-a", min_samples=1)[0])

    def test_concurrent_writers_keep_all_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = DurationHistory(Path(tmp) / "history.json")
            threads = [threading.Thread(target=history.append, args=(self.observation("solve", self.context(), i + 1, suffix=str(i)),)) for i in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(8, len(history.read()["observations"]))

    def test_corrupt_history_is_quarantined(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            path.write_text("{broken", encoding="utf-8")
            history = DurationHistory(path)
            self.assertEqual([], history.read()["observations"])
            self.assertTrue(history.diagnostics)
            self.assertTrue(list(Path(tmp).glob("history.json.corrupt-*")))

    def test_hash_inconsistent_history_is_quarantined(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            row = self.observation("solve", self.context(), 3)
            row["cohort_fingerprint"] = "0" * 64
            path.write_text(json.dumps({"schema_version": "1", "estimator_version": "median-v1", "observations": [row]}), encoding="utf-8")
            history = DurationHistory(path)
            self.assertEqual([], history.read()["observations"])
            self.assertTrue(list(Path(tmp).glob("history.json.corrupt-*")))

    def test_plain_and_interactive_rendering_contract(self):
        snapshot = {"percent": 34, "remaining_seconds": 5100, "repetition": 1, "repetitions": 3, "task_position": 2, "task_total": 3, "issue_id": "#498", "variant": "serena", "variant_position": 4, "variant_total": 7}
        plain = render_line(snapshot, interactive=False)
        self.assertEqual("Progress: 34% | Remaining: 1h 25m | Rep: 1/3 | Task: 2/3 (#498) | Serena (4/7)", plain)
        self.assertTrue(render_line(snapshot, interactive=True).startswith("⠋ Progress:"))
        self.assertNotIn("\x1b", plain)

    def test_plain_output_repeats_during_a_long_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, stream = Path(tmp), io.StringIO()
            reporter = ProgressReporter(root / "suite", "suite", [{"issue_id": "#8"}], ["serena"], 1, history_path=root / "history.json", stream=stream, interactive=False, plain_interval_seconds=0.05)
            reporter.consume({"stage": "setup", "status": "active", "issue": "#8", "variant": "serena"})
            time.sleep(0.32)
            reporter.close()
            self.assertGreaterEqual(stream.getvalue().count("Progress:"), 2)

    def test_serena_index_retry_is_idempotent(self):
        source = (Path(__file__).resolve().parents[1] / "scripts" / "run_benchmark.py").read_text()
        setup = source[source.index("def setup_serena"):source.index("def setup_graphify")]
        self.assertIn('"project", "index", "--log-level", "ERROR"', setup)
        self.assertNotIn('"--index"', setup)

    def test_runner_does_not_record_timed_out_solve_as_completed(self):
        source = (Path(__file__).resolve().parents[1] / "scripts" / "run_benchmark.py").read_text()
        self.assertIn('if v.status == "timeout"', source)
        self.assertIn('emit_progress_event(\n                "solve", solve_outcome', source)

    def test_reporter_counts_exclusion_and_resume_without_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reporter = ProgressReporter(root / "suite", "suite", [{"issue_id": "#8", "base_ref": "a", "reference_commit": "b"}], ["baseline-none", "serena"], 1, history_path=root / "history.json", stream=io.StringIO(), interactive=False, plain_interval_seconds=0, resumed_completed=[("#8", 1, "baseline-none")])
            reporter.consume({"stage": "arm", "status": "resumed", "issue": "#8", "repetition": 1, "variant": "baseline-none", "variant_position": 1})
            reporter.consume({"stage": "arm", "status": "excluded", "issue": "#8", "repetition": 1, "variant": "serena", "variant_position": 2})
            reporter.consume({"stage": "report", "status": "completed", "duration_seconds": 1, "issue": "#8", "repetition": 1, "variant": "serena", "variant_position": 2})
            reporter.consume({"stage": "validation", "status": "completed", "duration_seconds": 1, "issue": "#8", "repetition": 1, "variant": "serena", "variant_position": 2})
            reporter.close(complete=True)
            snapshots = [json.loads(line) for line in (root / "suite" / "progress-snapshots.jsonl").read_text().splitlines()]
            self.assertEqual([44, 88, 94, 100], [row["percent"] for row in snapshots])
            self.assertEqual(1, snapshots[-1]["states"]["excluded"])

    def test_completed_stage_is_removed_from_remaining_eta(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reporter = ProgressReporter(root / "suite", "suite", [{"issue_id": "#8", "base_ref": "tree-a", "reference_commit": "ref-a"}], ["serena"], 1, history_path=root / "history.json", stream=io.StringIO(), interactive=False, plain_interval_seconds=0, base_context={"model": "gpt-5.6-sol", "reasoning_effort": "high", "yolo": "true"})
            plan = reporter._remaining_plan()
            for index, (stage, context) in enumerate(plan):
                reporter.history.append(self.observation(stage, context, 10, suite="prior", suffix=str(index)))
            setup_context = next(context for stage, context in plan if stage == "setup")
            event = {**setup_context, "stage": "setup", "status": "active", "issue": "#8", "repetition": 1, "variant": "serena"}
            reporter.consume(event)
            before = reporter.current["remaining_seconds"]
            reporter.consume({**event, "status": "completed", "duration_seconds": 10})
            after = reporter.current["remaining_seconds"]
            reporter.close()
            self.assertEqual(100, before)
            self.assertEqual(90, after)

    def test_one_repetition_percentage_advances_at_stage_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reporter = ProgressReporter(root / "suite", "suite", [{"issue_id": "#8"}], ["serena"], 1, history_path=root / "history.json", stream=io.StringIO(), interactive=False, plain_interval_seconds=0)
            percentages = []
            for stage in ARM_STAGES:
                reporter.consume({"run_id": "run-a", "stage": stage, "status": "completed", "duration_seconds": 1, "issue": "#8", "repetition": 1, "variant": "serena"})
                percentages.append(reporter.current["percent"])
            reporter.consume({"run_id": "run-a", "stage": "arm", "status": "completed", "issue": "#8", "repetition": 1, "variant": "serena"})
            reporter.consume({"run_id": "suite", "stage": "report", "status": "completed", "duration_seconds": 1, "issue": "#8", "repetition": 1, "variant": "serena"})
            percentages.append(reporter.current["percent"])
            reporter.consume({"run_id": "suite", "stage": "validation", "status": "completed", "duration_seconds": 1, "issue": "#8", "repetition": 1, "variant": "serena"})
            percentages.append(reporter.current["percent"])
            reporter.close(complete=True)
            self.assertEqual([10, 20, 30, 40, 50, 60, 70, 80, 90, 100], percentages)

    def test_resume_reconstructs_terminal_arms_and_stage_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = ProgressReporter(root / "suite", "suite", [{"issue_id": "#8"}], ["serena"], 1, history_path=root / "history.json", stream=io.StringIO(), interactive=False, plain_interval_seconds=0)
            first.consume({"stage": "setup", "status": "completed", "duration_seconds": 1, "issue": "#8", "repetition": 1, "variant": "serena"})
            first.consume({"stage": "arm", "status": "completed", "issue": "#8", "repetition": 1, "variant": "serena"})
            first.close()
            resumed = ProgressReporter(root / "suite", "suite", [{"issue_id": "#8"}], ["serena"], 1, history_path=root / "history.json", stream=io.StringIO(), interactive=False)
            self.assertEqual({("#8", 1, "serena")}, resumed.completed)
            self.assertIn(("#8", 1, "serena", "setup"), resumed.finished_stages)
            snapshots = (root / "suite" / "progress-snapshots.jsonl").read_text().splitlines()
            self.assertEqual(len(snapshots), len(resumed.history_audit["events"]))
            resumed.close()

    def test_history_observation_is_deduplicated_across_replayed_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reporter = ProgressReporter(root / "suite", "suite", [{"issue_id": "#8"}], ["serena"], 1, history_path=root / "history.json", stream=io.StringIO(), interactive=False)
            event = {"run_id": "run-a", "stage": "solve", "status": "completed", "duration_seconds": 4, "issue": "#8", "repetition": 1, "variant": "serena"}
            reporter.consume({**event, "timestamp": "2026-01-01T00:00:00+00:00"})
            reporter.consume({**event, "timestamp": "2026-01-01T00:01:00+00:00"})
            reporter.close()
            self.assertEqual(1, len(reporter.history.read()["observations"]))

    def test_failed_or_excluded_stage_reduces_remaining_without_becoming_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reporter = ProgressReporter(root / "suite", "suite", [{"issue_id": "#8"}], ["serena"], 1, history_path=root / "history.json", stream=io.StringIO(), interactive=False)
            reporter.consume({"run_id": "run-a", "stage": "indexing", "status": "timed_out", "duration_seconds": 300, "issue": "#8", "repetition": 1, "variant": "serena"})
            reporter.consume({"run_id": "run-a", "stage": "arm", "status": "excluded", "issue": "#8", "repetition": 1, "variant": "serena"})
            reporter.close(complete=True)
            rows = reporter.history.read()["observations"]
            self.assertEqual("timed_out", rows[0]["outcome"])
            self.assertEqual([], reporter.history.successful_samples(rows[0]["cohort_fingerprint"]))
            self.assertEqual({"completed": 0, "active": 0, "pending": 0, "failed": 0, "excluded": 1, "resumed": 0}, reporter.current["states"])

    def test_disabled_history_does_not_create_runtime_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            history = DurationHistory(path, enabled=False)
            history.append(self.observation("solve", self.context(), 1))
            self.assertFalse(path.exists())

    def test_history_inputs_account_for_every_snapshot_and_validate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            suite_dir = root / "suite"
            reporter = ProgressReporter(suite_dir, "suite", [{"issue_id": "#8"}], ["serena"], 1, history_path=root / "history.json", stream=io.StringIO(), interactive=False)
            reporter.consume({"run_id": "run-a", "stage": "arm", "status": "completed", "issue": "#8", "repetition": 1, "variant": "serena"})
            reporter.consume({"run_id": "suite", "stage": "report", "status": "completed", "duration_seconds": 1, "issue": "#8", "repetition": 1, "variant": "serena"})
            reporter.consume({"run_id": "suite", "stage": "validation", "status": "completed", "duration_seconds": 1, "issue": "#8", "repetition": 1, "variant": "serena"})
            reporter.close(complete=True)
            audit = json.loads((suite_dir / "progress-history-inputs.json").read_text())
            snapshots = (suite_dir / "progress-snapshots.jsonl").read_text().splitlines()
            self.assertEqual(len(snapshots), len(audit["events"]))
            validator_path = Path(__file__).resolve().parents[1] / "scripts" / "validate_benchmark_run.py"
            spec = importlib.util.spec_from_file_location("progress_validator", validator_path)
            self.assertIsNotNone(spec)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            errors = []
            module.validate_suite_progress(suite_dir, {"repetitions": 1, "issues": [{"issue_id": "#8"}], "variants": "serena", "resolved_configuration": {"progress_enabled": True}}, errors)
            self.assertEqual([], errors)

    def test_progress_persistence_overhead_is_negligible_and_out_of_band(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = DurationHistory(Path(tmp) / "history.json")
            rows = [self.observation("solve", self.context(), 1, suffix=str(i)) for i in range(40)]
            started = time.perf_counter()
            for row in rows:
                history.append(row)
            elapsed = time.perf_counter() - started
            self.assertLess(elapsed / len(rows), 0.02)

    def test_idle_progress_renderer_uses_negligible_cpu(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reporter = ProgressReporter(root / "suite", "suite", [{"issue_id": "#8"}], ["serena"], 1, history_path=root / "history.json", stream=io.StringIO(), interactive=False, plain_interval_seconds=60)
            reporter.consume({"stage": "solve", "status": "active", "issue": "#8", "variant": "serena"})
            started = time.process_time()
            time.sleep(0.6)
            cpu_seconds = time.process_time() - started
            reporter.close()
            self.assertLess(cpu_seconds, 0.02)


if __name__ == "__main__":
    unittest.main()
