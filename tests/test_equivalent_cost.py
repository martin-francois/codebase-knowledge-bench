from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from equivalent_cost import (
    PRICING_DESCRIPTOR_RELATIVE_PATH,
    aggregate_equivalent_cost,
    canonical_sha256,
    derive_equivalent_cost,
    load_pricing_descriptor,
    request_usage_from_codex_jsonl,
    validate_pricing_descriptor,
)


class EquivalentCostTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.descriptor = load_pricing_descriptor(
            ROOT, configured_model_identity="gpt-5.6-sol"
        )
        cls.request_schema = ROOT / "schemas/request-usage.schema.json"
        cls.pricing_schema = ROOT / "schemas/pricing-descriptor.schema.json"

    def request(
        self,
        ordinal: int,
        *,
        input_tokens: int = 0,
        cached: int = 0,
        cache_write: int | None = 0,
        output: int = 0,
        reasoning: int = 0,
        retry_of: int | None = None,
        outcome: str = "completed",
        billable: bool = True,
    ) -> dict:
        observed = input_tokens - cached
        return {
            "ordinal": ordinal,
            "retry_of_ordinal": retry_of,
            "attempt_outcome": outcome,
            "billable": billable,
            "input_tokens": input_tokens,
            "cached_input_tokens": cached,
            "cache_write_tokens": cache_write,
            "ordinary_uncached_nonwrite_tokens": (
                None if cache_write is None else observed - cache_write
            ),
            "output_tokens_including_reasoning": output,
            "reasoning_output_tokens": reasoning,
            "model_identity": "gpt-5.6-sol",
            "long_context_classification": (
                "long_context" if input_tokens > 272000 else "standard"
            ),
            "execution_mode": "standard",
            "service_tier": "standard",
            "region": "global",
            "hosted_tool_usage": [],
            "evidence_source": "deterministic fixture",
            "evidence_schema_version": "current",
        }

    def artifact(self, requests: list[dict]) -> dict:
        complete_cache = all(
            request["cache_write_tokens"] is not None
            for request in requests
        )
        aggregate = {
            "input_tokens": sum(item["input_tokens"] for item in requests),
            "cached_input_tokens": sum(
                item["cached_input_tokens"] for item in requests
            ),
            "cache_write_tokens": (
                sum(item["cache_write_tokens"] for item in requests)
                if complete_cache
                else None
            ),
            "ordinary_uncached_nonwrite_tokens": (
                sum(
                    item["ordinary_uncached_nonwrite_tokens"]
                    for item in requests
                )
                if complete_cache
                else None
            ),
            "output_tokens_including_reasoning": sum(
                item["output_tokens_including_reasoning"]
                for item in requests
            ),
            "reasoning_output_tokens": sum(
                item["reasoning_output_tokens"] for item in requests
            ),
        }
        value = {
            "schema_id": "request-usage-current",
            "run_id": "run-test",
            "configured_model_identity": "gpt-5.6-sol",
            "evidence_level": "request",
            "evidence_source": "deterministic_fixture",
            "turn_aggregate": aggregate,
            "requests": requests,
            "request_count": len(requests),
            "billable_request_count": sum(
                item["billable"] for item in requests
            ),
            "retry_count": sum(
                item["retry_of_ordinal"] is not None for item in requests
            ),
            "terminal_attempts_complete": True,
            "request_aggregate_reconciled": True,
            "unavailable_reason": "",
        }
        value["content_sha256"] = canonical_sha256(
            value, excluded_field="content_sha256"
        )
        return value

    def derive(
        self,
        requests: list[dict],
        *,
        descriptor: dict | None = None,
    ) -> dict:
        return derive_equivalent_cost(
            self.artifact(requests),
            descriptor=descriptor or self.descriptor,
            request_schema_path=self.request_schema,
        )

    def rehash_request(self, value: dict) -> dict:
        value["content_sha256"] = canonical_sha256(
            value, excluded_field="content_sha256"
        )
        return value

    def rehash_descriptor(self, value: dict) -> dict:
        value["descriptor_content_sha256"] = canonical_sha256(
            value, excluded_field="descriptor_content_sha256"
        )
        return value

    def test_descriptor_is_frozen_content_addressed_and_official(self) -> None:
        path = ROOT / PRICING_DESCRIPTOR_RELATIVE_PATH
        self.assertTrue(path.is_file())
        validate_pricing_descriptor(
            self.descriptor,
            configured_model_identity="gpt-5.6-sol",
            schema_path=self.pricing_schema,
        )
        self.assertEqual(5000, self.descriptor[
            "rates_usd_nanos_per_token"
        ]["ordinary_uncached_input"])
        self.assertTrue(all(
            url.startswith("https://developers.openai.com/")
            for url in self.descriptor["source_urls"]
        ))

    def test_ordinary_cached_write_and_mixed_input_exact_cost(self) -> None:
        ordinary = self.derive([
            self.request(1, input_tokens=10)
        ])
        cached = self.derive([
            self.request(1, input_tokens=10, cached=10)
        ])
        write = self.derive([
            self.request(1, input_tokens=10, cache_write=10)
        ])
        mixed = self.derive([
            self.request(
                1, input_tokens=30, cached=10, cache_write=5
            )
        ])
        self.assertEqual(50_000, ordinary["exact_usd_nanos"])
        self.assertEqual(5_000, cached["exact_usd_nanos"])
        self.assertEqual(62_500, write["exact_usd_nanos"])
        self.assertEqual(111_250, mixed["exact_usd_nanos"])

    def test_missing_cache_write_produces_observed_range(self) -> None:
        result = self.derive([
            self.request(1, input_tokens=10, cache_write=None)
        ])
        self.assertEqual("bounded", result["status"])
        self.assertIsNone(result["exact_usd_nanos"])
        self.assertEqual(50_000, result["lower_bound_usd_nanos"])
        self.assertEqual(62_500, result["upper_bound_usd_nanos"])
        self.assertNotIn("±", result["reason"])

    def test_missing_usage_is_unavailable_not_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = request_usage_from_codex_jsonl(
                Path(temporary) / "missing.jsonl",
                run_id="run-missing",
                configured_model_identity="gpt-5.6-sol",
            )
        result = derive_equivalent_cost(
            artifact,
            descriptor=self.descriptor,
            request_schema_path=self.request_schema,
        )
        self.assertEqual("unavailable", result["status"])
        self.assertIsNone(result["exact_usd_nanos"])
        self.assertIsNone(result["lower_bound_usd_nanos"])

    def test_reasoning_is_not_double_counted(self) -> None:
        with_reasoning = self.derive([
            self.request(1, output=10, reasoning=6)
        ])
        without_reasoning = self.derive([
            self.request(1, output=10, reasoning=0)
        ])
        self.assertEqual(
            without_reasoning["exact_usd_nanos"],
            with_reasoning["exact_usd_nanos"],
        )
        self.assertEqual(300_000, with_reasoning["exact_usd_nanos"])

    def test_long_context_boundary_below_at_and_above(self) -> None:
        below = self.derive([
            self.request(1, input_tokens=271999)
        ])
        at = self.derive([
            self.request(1, input_tokens=272000)
        ])
        above = self.derive([
            self.request(1, input_tokens=272001)
        ])
        self.assertEqual(271999 * 5000, below["exact_usd_nanos"])
        self.assertEqual(272000 * 5000, at["exact_usd_nanos"])
        self.assertEqual(272001 * 5000 * 2, above["exact_usd_nanos"])

    def test_service_and_regional_rational_modifiers(self) -> None:
        descriptor = copy.deepcopy(self.descriptor)
        descriptor["service_tier_multiplier"] = {
            "numerator": 2,
            "denominator": 1,
        }
        descriptor["regional_processing_multiplier"] = {
            "numerator": 3,
            "denominator": 1,
        }
        self.rehash_descriptor(descriptor)
        result = self.derive([
            self.request(1, input_tokens=10)
        ], descriptor=descriptor)
        self.assertEqual(300_000, result["exact_usd_nanos"])

    def test_separately_priced_hosted_tool_is_rejected_when_unsupported(self) -> None:
        request = self.request(1)
        request["hosted_tool_usage"] = [{"tool": "web_search", "calls": 1}]
        with self.assertRaisesRegex(ValueError, "request-usage.schema"):
            self.derive([request])

    def test_multiple_requests_and_completed_retry_are_included(self) -> None:
        result = self.derive([
            self.request(1, input_tokens=10),
            self.request(
                2, input_tokens=20, retry_of=1, outcome="completed"
            ),
        ])
        self.assertEqual("exact", result["status"])
        self.assertEqual(150_000, result["exact_usd_nanos"])
        self.assertEqual(2, result["billable_request_count"])
        self.assertEqual(1, result["retry_count"])

    def test_nonbillable_failed_attempt_without_usage_is_excluded(self) -> None:
        result = self.derive([
            self.request(
                1, outcome="failed", billable=False
            ),
            self.request(2, input_tokens=10),
        ])
        self.assertEqual(50_000, result["exact_usd_nanos"])
        self.assertEqual(1, result["billable_request_count"])

    def test_duplicate_or_missing_request_ordinals_are_rejected(self) -> None:
        for requests in (
            [self.request(1), self.request(1)],
            [self.request(1), self.request(3)],
        ):
            with self.subTest(requests=requests):
                artifact = self.artifact(requests)
                with self.assertRaisesRegex(ValueError, "ordinals"):
                    derive_equivalent_cost(
                        artifact,
                        descriptor=self.descriptor,
                        request_schema_path=self.request_schema,
                    )

    def test_request_aggregate_disagreement_is_rejected(self) -> None:
        artifact = self.artifact([
            self.request(1, input_tokens=10)
        ])
        artifact["turn_aggregate"]["output_tokens_including_reasoning"] = 1
        self.rehash_request(artifact)
        with self.assertRaisesRegex(ValueError, "turn aggregate"):
            derive_equivalent_cost(
                artifact,
                descriptor=self.descriptor,
                request_schema_path=self.request_schema,
            )

    def test_wrong_model_and_descriptor_are_rejected(self) -> None:
        artifact = self.artifact([self.request(1)])
        artifact["requests"][0]["model_identity"] = "gpt-wrong"
        self.rehash_request(artifact)
        with self.assertRaisesRegex(ValueError, "model"):
            derive_equivalent_cost(
                artifact,
                descriptor=self.descriptor,
                request_schema_path=self.request_schema,
            )
        with self.assertRaisesRegex(ValueError, "configured model"):
            validate_pricing_descriptor(
                self.descriptor,
                configured_model_identity="gpt-wrong",
                schema_path=self.pricing_schema,
            )

    def test_tampered_usage_and_descriptor_hashes_are_rejected(self) -> None:
        artifact = self.artifact([
            self.request(1, input_tokens=10)
        ])
        artifact["turn_aggregate"]["input_tokens"] = 11
        with self.assertRaisesRegex(ValueError, "content SHA-256"):
            derive_equivalent_cost(
                artifact,
                descriptor=self.descriptor,
                request_schema_path=self.request_schema,
            )
        descriptor = copy.deepcopy(self.descriptor)
        descriptor["rates_usd_nanos_per_token"][
            "ordinary_uncached_input"
        ] += 1
        with self.assertRaisesRegex(ValueError, "content SHA-256"):
            validate_pricing_descriptor(
                descriptor,
                configured_model_identity="gpt-5.6-sol",
                schema_path=self.pricing_schema,
            )

    def test_decimal_presentation_rounds_half_up_only_at_display(self) -> None:
        result = self.derive([
            self.request(1, input_tokens=1000)
        ])
        self.assertEqual(5_000_000, result["exact_usd_nanos"])
        self.assertEqual("0.01", result["presentation_exact_usd"])

    def test_canonical_serialization_ignores_mapping_order(self) -> None:
        artifact = self.artifact([
            self.request(1, input_tokens=10)
        ])
        reverse = dict(reversed(list(artifact.items())))
        self.assertEqual(
            canonical_sha256(
                artifact, excluded_field="content_sha256"
            ),
            canonical_sha256(
                reverse, excluded_field="content_sha256"
            ),
        )

    def test_content_hash_is_stable_across_python_hash_seeds(self) -> None:
        program = (
            "from equivalent_cost import canonical_sha256;"
            "keys={'alpha','beta','gamma'};"
            "value={key:len(key) for key in keys};"
            "print(canonical_sha256(value,excluded_field='content_sha256'))"
        )
        outputs = []
        for seed in ("1", "9173"):
            completed = subprocess.run(
                [sys.executable, "-c", program],
                cwd=ROOT,
                env={
                    **os.environ,
                    "PYTHONHASHSEED": seed,
                    "PYTHONPATH": str(ROOT / "scripts"),
                },
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            )
            outputs.append(completed.stdout.strip())
        self.assertEqual(outputs[0], outputs[1])

    def test_exact_zero_remains_distinct_from_unavailable(self) -> None:
        exact = self.derive([self.request(1)])
        with tempfile.TemporaryDirectory() as temporary:
            unavailable_artifact = request_usage_from_codex_jsonl(
                Path(temporary) / "missing",
                run_id="run-missing",
                configured_model_identity="gpt-5.6-sol",
            )
        unavailable = derive_equivalent_cost(
            unavailable_artifact,
            descriptor=self.descriptor,
            request_schema_path=self.request_schema,
        )
        self.assertEqual(("exact", 0), (
            exact["status"], exact["exact_usd_nanos"]
        ))
        self.assertEqual(("unavailable", None), (
            unavailable["status"], unavailable["exact_usd_nanos"]
        ))

    def test_turn_aggregate_bounds_cache_and_long_context_without_midpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "run.jsonl"
            path.write_text(json.dumps({
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 300000,
                    "cached_input_tokens": 100000,
                    "output_tokens": 1000,
                    "reasoning_output_tokens": 500,
                },
            }) + "\n", encoding="utf-8")
            artifact = request_usage_from_codex_jsonl(
                path,
                run_id="run-aggregate",
                configured_model_identity="gpt-5.6-sol",
            )
        result = derive_equivalent_cost(
            artifact,
            descriptor=self.descriptor,
            request_schema_path=self.request_schema,
        )
        self.assertEqual("bounded", result["status"])
        self.assertLess(
            result["lower_bound_usd_nanos"],
            result["upper_bound_usd_nanos"],
        )
        self.assertIsNone(result["presentation_exact_usd"])
        self.assertIn("long-context", result["reason"])

    def test_suite_aggregation_preserves_exact_bounds_and_unavailable(self) -> None:
        exact = self.derive([
            self.request(1, input_tokens=10)
        ])
        bounded = self.derive([
            self.request(1, input_tokens=10, cache_write=None)
        ])
        exact_total = aggregate_equivalent_cost([
            {"equivalent_cost": exact},
            {"equivalent_cost": exact},
        ])
        self.assertEqual("exact", exact_total["status"])
        self.assertEqual(100_000, exact_total["exact_total_usd_nanos"])
        bounded_total = aggregate_equivalent_cost([
            {"equivalent_cost": exact},
            {"equivalent_cost": bounded},
        ])
        self.assertEqual("bounded", bounded_total["status"])
        self.assertEqual(100_000, bounded_total["lower_total_usd_nanos"])
        self.assertEqual(112_500, bounded_total["upper_total_usd_nanos"])
        unavailable = copy.deepcopy(exact)
        unavailable.update({
            "status": "unavailable",
            "exact_usd_nanos": None,
            "lower_bound_usd_nanos": None,
            "upper_bound_usd_nanos": None,
            "reason": "missing request usage",
        })
        unavailable_total = aggregate_equivalent_cost([
            {"equivalent_cost": exact},
            {"equivalent_cost": unavailable},
        ])
        self.assertEqual("unavailable", unavailable_total["status"])
        self.assertIsNone(unavailable_total["lower_total_usd_nanos"])


if __name__ == "__main__":
    unittest.main()
