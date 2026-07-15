from __future__ import annotations

import copy
import sys
import unittest
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from future_methodology import (
    METHODOLOGY_VERSION, cache_fairness_analysis, calibrate_mutants, compare_reference_scenarios,
    derive_token_usage, issue_diversity_preflight, modeled_token_load,
    pricing_cost_eligible, prompt_cache_key_supported, requirement_contract_diagnostics,
    score_requirement_contract,
)


def contract() -> dict:
    return {
        "issue_id": "fixture", "methodology_version": METHODOLOGY_VERSION,
        "requirements": [
            {"id": "core", "title": "Core", "description": "Core behavior", "weight": 3,
             "critical": True, "category": "core", "pass_rule": "all_cases",
             "protected_test_cases": ["core-a", "core-b"], "differential_scenarios": ["core"],
             "mutants": ["first-only", "hard-coded"]},
            {"id": "negative", "title": "No write", "description": "Validate before side effects", "weight": 1,
             "critical": True, "category": "side_effect", "pass_rule": "all_cases",
             "protected_test_cases": ["no-write"], "differential_scenarios": ["ambiguous"],
             "mutants": ["write-before-validation"]},
        ],
    }


class FutureTokenMethodologyTest(unittest.TestCase):
    def test_turn_usage_preserves_unknown_cache_writes_as_null(self):
        usage = derive_token_usage({"input_tokens": 100, "cached_input_tokens": 70, "output_tokens": 5, "reasoning_output_tokens": 2})
        self.assertEqual(30, usage["non_cached_input_tokens_observed"])
        self.assertIsNone(usage["cache_write_tokens"])
        self.assertIsNone(usage["uncached_nonwrite_input_tokens"])
        self.assertFalse(pricing_cost_eligible(usage, pinned_prices_complete=True))

    def test_cache_write_zero_differs_from_unavailable(self):
        usage = derive_token_usage({"input_tokens": 100, "cached_input_tokens": 70, "cache_write_tokens": 0, "output_tokens": 5, "reasoning_output_tokens": 2})
        self.assertTrue(usage["cache_write_metrics_available"])
        self.assertEqual(30, usage["uncached_nonwrite_input_tokens"])
        self.assertTrue(pricing_cost_eligible(usage, pinned_prices_complete=True))

    def test_cache_hit_rate_handles_zero_input(self):
        usage = derive_token_usage({"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0})
        self.assertEqual(0.0, usage["cache_hit_rate"])

    def test_token_weight_sensitivity_changes_winner(self):
        a = derive_token_usage({"input_tokens": 100, "cached_input_tokens": 90, "output_tokens": 0, "reasoning_output_tokens": 0})
        b = derive_token_usage({"input_tokens": 50, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0})
        self.assertLess(modeled_token_load(a, 0), modeled_token_load(b, 0))
        self.assertGreater(modeled_token_load(a, 1), modeled_token_load(b, 1))

    def test_cache_fairness_is_deterministic_and_never_calls_gap_cold(self):
        rows = [{
            "arm_key": "a::1::tool", "treatment": "tool", "issue_id": "a", "repetition": 1,
            "serial_position": 2, "elapsed_since_prior_arm_seconds": 1900,
            "elapsed_since_prior_same_issue_seconds": 4000, "prompt_policy_hash": "abc",
            "model": "gpt", "codex_cli_version": "1", "cache_isolation_mode": "natural",
            "input_tokens": 100, "cached_input_tokens": 50, "output_tokens": 1, "reasoning_output_tokens": 1,
        }]
        first = cache_fairness_analysis(rows)
        second = cache_fairness_analysis(reversed(rows))
        self.assertEqual(first, second)
        self.assertIn("minimum", first["cache_ttl_interpretation"])
        self.assertIn("not_proven_cold", first["arms"][0]["elapsed_gap_band"])

    def test_cache_key_requires_official_feature_detection(self):
        self.assertFalse(prompt_cache_key_supported({"official_prompt_cache_key": True}))
        self.assertTrue(prompt_cache_key_supported({"official_prompt_cache_key": True, "verified_with_current_codex_cli": True}))


class RequirementCorrectnessTest(unittest.TestCase):
    def test_duplicate_case_does_not_change_requirement_weight(self):
        base = score_requirement_contract(contract(), {"core-a": True, "core-b": True, "no-write": True}, common_regression_score=100, common_regression_full_pass=True, trust_valid=True)
        duplicate = contract()
        duplicate["requirements"][0]["protected_test_cases"].append("core-copy")
        copied = score_requirement_contract(duplicate, {"core-a": True, "core-b": True, "core-copy": True, "no-write": True}, common_regression_score=100, common_regression_full_pass=True, trust_valid=True)
        self.assertEqual(base["requested_behavior_score"], copied["requested_behavior_score"])

    def test_critical_failure_cannot_be_averaged_away(self):
        score = score_requirement_contract(contract(), {"core-a": True, "core-b": True, "no-write": False}, common_regression_score=100, common_regression_full_pass=True, trust_valid=True, patch_quality_score=20)
        self.assertFalse(score["critical_requirement_full_pass"])
        self.assertFalse(score["task_success"])
        self.assertEqual(["negative"], score["critical_requirement_failures"])

    def test_partial_behavior_gets_partial_requirement_score(self):
        partial = contract()
        partial["requirements"][0].update(pass_rule="minimum_fraction", minimum_fraction=0.5)
        score = score_requirement_contract(partial, {"core-a": True, "core-b": False, "no-write": True}, common_regression_score=100, common_regression_full_pass=True, trust_valid=True)
        self.assertGreater(score["requested_behavior_score"], 0)
        self.assertLess(score["requested_behavior_score"], 100)

    def test_historical_methodology_cannot_enter_vnext_scorer(self):
        historical = contract()
        historical["methodology_version"] = "operational-workflow-tool-effect-v4"
        with self.assertRaisesRegex(ValueError, "historical"):
            score_requirement_contract(historical, {}, common_regression_score=100, common_regression_full_pass=True, trust_valid=True)

    def test_candidate_tests_do_not_enter_protected_score(self):
        score = score_requirement_contract(contract(), {"core-a": True, "core-b": True, "no-write": True}, common_regression_score=100, common_regression_full_pass=True, trust_valid=True, candidate_test_quality=0)
        self.assertEqual(100, score["behavioral_correctness_score"])
        self.assertTrue(score["task_success"])

    def test_reference_diagnostic_is_not_failure(self):
        score = score_requirement_contract(contract(), {"core-a": True, "core-b": True, "no-write": True}, common_regression_score=100, common_regression_full_pass=True, trust_valid=True)
        self.assertIsNone(score["reference_behavior_match_rate"])
        self.assertTrue(score["task_success"])

    def test_reference_diagnostics_compare_observable_behavior_not_source(self):
        declared = [{"id": "safe", "expected": {"exit_status": 1, "side_effects": [], "irrelevant_wording": "reference"}}]
        result = compare_reference_scenarios(declared, {"safe": {"exit_status": 1, "side_effects": [], "irrelevant_wording": "different"}})
        self.assertEqual(1.0, result["match_rate"])
        self.assertFalse(result["source_similarity_used"])

    def test_overbroad_rejection_differs_from_correct_negative_behavior(self):
        declared = [
            {"id": "ambiguous", "expected": {"exit_status": 1, "side_effects": []}},
            {"id": "explicit-id", "expected": {"exit_status": 0, "side_effects": ["move"]}},
        ]
        result = compare_reference_scenarios(declared, {
            "ambiguous": {"exit_status": 1, "side_effects": []},
            "explicit-id": {"exit_status": 1, "side_effects": []},
        })
        self.assertEqual(0.5, result["match_rate"])

    def test_sparse_contract_diagnostic_blocks_broad_claim(self):
        sparse = {"requirements": [contract()["requirements"][0]]}
        result = requirement_contract_diagnostics(sparse)
        self.assertTrue(result["binary_score_risk"])
        self.assertTrue(result["broad_claim_blocked"])


class MutationAndIssueDiversityTest(unittest.TestCase):
    def test_source_controlled_issue_contracts_validate_strictly(self):
        from jsonschema import Draft202012Validator
        schema = json.loads((ROOT / "schemas" / "requirement-contract-vnext.schema.json").read_text())
        validator = Draft202012Validator(schema)
        for path in sorted((ROOT / "fixtures" / "methodology-vnext").glob("issue-*-requirements.json")):
            validator.validate(json.loads(path.read_text()))

    def test_weak_contract_fails_mutation_calibration(self):
        result = calibrate_mutants(contract(), {"first-only": True, "hard-coded": False, "write-before-validation": True})
        self.assertFalse(result["calibration_passed"])
        self.assertEqual(["hard-coded"], result["surviving_mutants"])
        self.assertFalse(result["affects_candidate_runtime_score"])

    def test_strong_contract_passes_mutation_calibration(self):
        result = calibrate_mutants(contract(), {"first-only": True, "hard-coded": True, "write-before-validation": True})
        self.assertTrue(result["calibration_passed"])

    def test_current_three_issue_shape_is_limited_and_detects_single_differentiator(self):
        issues = [
            {"issue_id": "486", "historical_scores": [100, 100], "expected_skill_dimensions": ["localized_parsing"], "independent_behavior_case_count": 1, "base_reference_discrimination": True, "mutant_detection": 1},
            {"issue_id": "488", "historical_scores": [100, 100], "expected_skill_dimensions": ["negative_side_effect_safety"], "independent_behavior_case_count": 2, "base_reference_discrimination": True, "mutant_detection": 1},
            {"issue_id": "498", "historical_scores": [0, 100], "expected_skill_dimensions": ["cross_file_behavior"], "independent_behavior_case_count": 1, "base_reference_discrimination": True, "mutant_detection": .5},
        ]
        result = issue_diversity_preflight(issues)
        self.assertEqual("limited_cluster_evidence", result["evidence_class"])
        self.assertTrue(result["one_issue_supplies_all_quality_differentiation"])
        self.assertFalse(result["broad_comparative_claims_supported"])

    def test_floor_task_is_detected(self):
        result = issue_diversity_preflight([{"issue_id": "floor", "historical_scores": [0, 0], "independent_behavior_case_count": 1}])
        self.assertTrue(result["issue_diversity_matrix"][0]["floor_risk"])
