#!/usr/bin/env python3
"""Frozen-price equivalent Codex API cost from authenticated solve usage."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker

from codex_app_server import extract_app_server_usage


CONTRACT_ID = "equivalent-codex-api-cost-current"
PRICING_SCHEMA_ID = "equivalent-model-pricing-descriptor-current"
REQUEST_USAGE_SCHEMA_ID = "request-usage-current"
PRICING_DESCRIPTOR_RELATIVE_PATH = (
    "configs/pricing/gpt-5.6-sol-standard-global-2026-07-30.json"
)
USD_NANOS_PER_USD = 1_000_000_000


def canonical_sha256(value: Mapping[str, Any], *, excluded_field: str) -> str:
    payload = dict(value)
    payload.pop(excluded_field, None)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validate_schema(value: Any, schema_path: Path) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(
            schema, format_checker=FormatChecker()
        ).iter_errors(value),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "<root>"
        raise ValueError(
            f"{schema_path.name} validation failed at {location}: {first.message}"
        )


def _rational(value: Mapping[str, Any], *, field: str) -> Fraction:
    numerator = value.get("numerator")
    denominator = value.get("denominator")
    if (
        not isinstance(numerator, int)
        or isinstance(numerator, bool)
        or numerator < 0
        or not isinstance(denominator, int)
        or isinstance(denominator, bool)
        or denominator <= 0
    ):
        raise ValueError(f"{field} must be a non-negative rational")
    return Fraction(numerator, denominator)


def validate_pricing_descriptor(
    descriptor: Mapping[str, Any],
    *,
    configured_model_identity: str,
    schema_path: Path,
) -> None:
    _validate_schema(descriptor, schema_path)
    if descriptor.get("schema_id") != PRICING_SCHEMA_ID:
        raise ValueError("unsupported pricing descriptor schema")
    expected_hash = canonical_sha256(
        descriptor, excluded_field="descriptor_content_sha256"
    )
    if descriptor.get("descriptor_content_sha256") != expected_hash:
        raise ValueError("pricing descriptor content SHA-256 mismatch")
    if descriptor.get("model_identity") != configured_model_identity:
        raise ValueError(
            "pricing descriptor model does not match the configured model"
        )
    retrieved = date.fromisoformat(str(descriptor["retrieved_date"]))
    effective = date.fromisoformat(str(descriptor["benchmark_effective_date"]))
    if effective < retrieved:
        raise ValueError(
            "pricing descriptor benchmark-effective date precedes retrieval"
        )
    rates = descriptor["rates_usd_nanos_per_token"]
    if rates["cache_write"] * 4 != rates["ordinary_uncached_input"] * 5:
        raise ValueError(
            "pricing descriptor cache-write rate is inconsistent with its source"
        )
    for name in (
        "service_tier_multiplier",
        "regional_processing_multiplier",
    ):
        if _rational(descriptor[name], field=name) <= 0:
            raise ValueError(f"{name} must be positive")
    for name in ("input_multiplier", "output_multiplier"):
        if _rational(descriptor["long_context"][name], field=name) <= 0:
            raise ValueError(f"long-context {name} must be positive")


def load_pricing_descriptor(
    repo_root: Path,
    *,
    configured_model_identity: str,
) -> dict[str, Any]:
    descriptor_path = repo_root / PRICING_DESCRIPTOR_RELATIVE_PATH
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    validate_pricing_descriptor(
        descriptor,
        configured_model_identity=configured_model_identity,
        schema_path=repo_root / "schemas/pricing-descriptor.schema.json",
    )
    return descriptor


def _usage_from_codex_mapping(usage: Mapping[str, Any]) -> dict[str, Any]:
    supported = {
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "cache_write_tokens",
    }
    unknown = set(usage) - supported
    if unknown:
        raise ValueError(
            f"unsupported Codex request-usage fields: {sorted(unknown)}"
        )
    input_tokens = int(usage.get("input_tokens", 0))
    cached_input_tokens = int(usage.get("cached_input_tokens", 0))
    output_tokens = int(usage.get("output_tokens", 0))
    reasoning_tokens = int(usage.get("reasoning_output_tokens", 0))
    cache_write = usage.get("cache_write_tokens")
    cache_write = None if cache_write is None else int(cache_write)
    values = (
        input_tokens,
        cached_input_tokens,
        output_tokens,
        reasoning_tokens,
    )
    if any(value < 0 for value in values):
        raise ValueError("request-usage token counts must be non-negative")
    if cached_input_tokens > input_tokens:
        raise ValueError("cached input cannot exceed input")
    if reasoning_tokens > output_tokens:
        raise ValueError("reasoning output must be a subset of output")
    observed_non_cached = input_tokens - cached_input_tokens
    if cache_write is not None and not 0 <= cache_write <= observed_non_cached:
        raise ValueError(
            "cache writes must be within observed non-cached input"
        )
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "cache_write_tokens": cache_write,
        "ordinary_uncached_nonwrite_tokens": (
            None
            if cache_write is None
            else observed_non_cached - cache_write
        ),
        "output_tokens_including_reasoning": output_tokens,
        "reasoning_output_tokens": reasoning_tokens,
    }


def _with_content_hash(value: dict[str, Any]) -> dict[str, Any]:
    value["content_sha256"] = canonical_sha256(
        value, excluded_field="content_sha256"
    )
    return value


def _app_server_usage(usage: Mapping[str, Any]) -> dict[str, Any]:
    return _usage_from_codex_mapping(
        {
            "input_tokens": usage["input_tokens"],
            "cached_input_tokens": usage["cached_input_tokens"],
            "cache_write_tokens": usage["cache_write_tokens"],
            "output_tokens": usage["output_tokens"],
            "reasoning_output_tokens": usage["reasoning_output_tokens"],
        }
    )


def request_usage_from_codex_app_server_jsonl(
    path: Path,
    *,
    run_id: str,
    configured_model_identity: str,
    execution_mode: str,
    service_tier: str,
    region: str,
    long_context_threshold_input_tokens: int = 272_000,
) -> dict[str, Any]:
    """Derive request evidence from one fresh app-server wire journal."""

    try:
        evidence = extract_app_server_usage(path)
    except (OSError, UnicodeError, ValueError) as exc:
        return _with_content_hash(
            {
                "schema_id": REQUEST_USAGE_SCHEMA_ID,
                "run_id": run_id,
                "configured_model_identity": configured_model_identity,
                "evidence_level": "unavailable",
                "evidence_source": "codex_app_server_raw_response_completed",
                "turn_aggregate": None,
                "requests": [],
                "request_count": None,
                "billable_request_count": None,
                "retry_count": None,
                "terminal_attempts_complete": False,
                "request_aggregate_reconciled": False,
                "unavailable_reason": (
                    "Codex app-server evidence is malformed: " + str(exc)
                ),
            }
        )

    failures: list[str] = []
    starts = evidence["successful_thread_starts"]
    if len(starts) != 1:
        failures.append(
            f"expected one successful thread/start, observed {len(starts)}"
        )
        thread_id = ""
        start_params: Mapping[str, Any] = {}
    else:
        _, thread_id, start_params = starts[0]
        if start_params.get("experimentalRawEvents") is not True:
            failures.append("thread/start did not enable experimentalRawEvents")
        if start_params.get("ephemeral") is not True:
            failures.append("thread/start was not ephemeral")
        if start_params.get("model") != configured_model_identity:
            failures.append("thread/start model does not match configured model")

    terminals = evidence["terminal_turns"]
    if len(terminals) != 1:
        failures.append(
            f"expected one terminal turn, observed {len(terminals)}"
        )
        turn_id = ""
    else:
        _, terminal_thread, turn_id, terminal_status = terminals[0]
        if terminal_status != "completed":
            failures.append(
                f"turn terminal status was {terminal_status or 'absent'}"
            )
        if thread_id and terminal_thread != thread_id:
            failures.append("terminal turn belongs to a different thread")

    raw_responses = evidence["raw_responses"]
    if not raw_responses:
        failures.append("no rawResponse/completed notification was observed")
    response_ids = [item["response_id"] for item in raw_responses]
    if any(not value for value in response_ids):
        failures.append("raw response identity is absent")
    if len(response_ids) != len(set(response_ids)):
        failures.append("duplicate raw response identity was observed")
    if any(item["usage"] is None for item in raw_responses):
        failures.append("a raw response omitted usage")
    if thread_id and any(
        item["thread_id"] != thread_id for item in raw_responses
    ):
        failures.append("a raw response belongs to a different thread")
    if turn_id and any(
        item["turn_id"] != turn_id for item in raw_responses
    ):
        failures.append("a raw response belongs to a different turn")

    aggregates = [
        item
        for item in evidence["aggregate_updates"]
        if (not thread_id or item["thread_id"] == thread_id)
        and (not turn_id or item["turn_id"] == turn_id)
    ]
    final_aggregate = aggregates[-1]["usage"] if aggregates else None
    if final_aggregate is None:
        failures.append("final thread token aggregate is absent")

    requests: list[dict[str, Any]] = []
    threshold = int(long_context_threshold_input_tokens)
    if threshold < 0:
        raise ValueError("long-context threshold must be non-negative")
    if not any(item["usage"] is None for item in raw_responses):
        for ordinal, item in enumerate(raw_responses, 1):
            usage = _app_server_usage(item["usage"])
            requests.append(
                {
                    "ordinal": ordinal,
                    "journal_ordinal": item["journal_ordinal"],
                    "response_id": item["response_id"],
                    "thread_id": item["thread_id"],
                    "turn_id": item["turn_id"],
                    "attempt_outcome": "completed",
                    "billable": True,
                    **usage,
                    "model_identity": configured_model_identity,
                    "long_context_classification": (
                        "long_context"
                        if usage["input_tokens"] > threshold
                        else "standard"
                    ),
                    "execution_mode": execution_mode,
                    "service_tier": service_tier,
                    "region": region,
                    "hosted_tool_usage": [],
                    "evidence_source": (
                        "codex_app_server_raw_response_completed"
                    ),
                }
            )

    aggregate = (
        _app_server_usage(final_aggregate)
        if final_aggregate is not None
        else None
    )
    reconciled = False
    if aggregate is not None and requests:
        expected = {
            "input_tokens": sum(item["input_tokens"] for item in requests),
            "cached_input_tokens": sum(
                item["cached_input_tokens"] for item in requests
            ),
            "cache_write_tokens": sum(
                item["cache_write_tokens"] for item in requests
            ),
            "ordinary_uncached_nonwrite_tokens": sum(
                item["ordinary_uncached_nonwrite_tokens"]
                for item in requests
            ),
            "output_tokens_including_reasoning": sum(
                item["output_tokens_including_reasoning"]
                for item in requests
            ),
            "reasoning_output_tokens": sum(
                item["reasoning_output_tokens"] for item in requests
            ),
        }
        reconciled = expected == aggregate
        if not reconciled:
            failures.append(
                "raw completed-response usage disagrees with final aggregate"
            )

    if failures:
        if aggregate is not None:
            return _with_content_hash(
                {
                    "schema_id": REQUEST_USAGE_SCHEMA_ID,
                    "run_id": run_id,
                    "configured_model_identity": configured_model_identity,
                    "evidence_level": "turn_aggregate",
                    "evidence_source": (
                        "codex_app_server_raw_response_completed"
                    ),
                    "turn_aggregate": aggregate,
                    "requests": [],
                    "request_count": None,
                    "billable_request_count": None,
                    "retry_count": None,
                    "terminal_attempts_complete": None,
                    "request_aggregate_reconciled": None,
                    "unavailable_reason": "; ".join(failures),
                }
            )
        return _with_content_hash(
            {
                "schema_id": REQUEST_USAGE_SCHEMA_ID,
                "run_id": run_id,
                "configured_model_identity": configured_model_identity,
                "evidence_level": "unavailable",
                "evidence_source": (
                    "codex_app_server_raw_response_completed"
                ),
                "turn_aggregate": None,
                "requests": [],
                "request_count": None,
                "billable_request_count": None,
                "retry_count": None,
                "terminal_attempts_complete": False,
                "request_aggregate_reconciled": False,
                "unavailable_reason": "; ".join(failures),
            }
        )

    return _with_content_hash(
        {
            "schema_id": REQUEST_USAGE_SCHEMA_ID,
            "run_id": run_id,
            "configured_model_identity": configured_model_identity,
            "evidence_level": "request",
            "evidence_source": "codex_app_server_raw_response_completed",
            "turn_aggregate": aggregate,
            "requests": requests,
            "request_count": len(requests),
            "billable_request_count": len(requests),
            "retry_count": None,
            "terminal_attempts_complete": True,
            "request_aggregate_reconciled": reconciled,
            "unavailable_reason": "",
        }
    )


def _validate_usage_counts(usage: Mapping[str, Any]) -> None:
    input_tokens = usage.get("input_tokens")
    cached = usage.get("cached_input_tokens")
    output = usage.get("output_tokens_including_reasoning")
    reasoning = usage.get("reasoning_output_tokens")
    values = (input_tokens, cached, output, reasoning)
    if any(
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        for value in values
    ):
        raise ValueError("request usage requires non-negative integer tokens")
    if cached > input_tokens:
        raise ValueError("cached input cannot exceed input")
    if reasoning > output:
        raise ValueError("reasoning output must be a subset of output")
    observed = input_tokens - cached
    cache_write = usage.get("cache_write_tokens")
    ordinary = usage.get("ordinary_uncached_nonwrite_tokens")
    if cache_write is None:
        if ordinary is not None:
            raise ValueError(
                "ordinary uncached input must be null when cache writes are null"
            )
    elif (
        not isinstance(cache_write, int)
        or isinstance(cache_write, bool)
        or not 0 <= cache_write <= observed
        or ordinary != observed - cache_write
    ):
        raise ValueError(
            "cache-write and ordinary-input counts do not reconcile"
        )


def validate_request_usage(
    artifact: Mapping[str, Any],
    *,
    descriptor: Mapping[str, Any],
    schema_path: Path,
) -> None:
    _validate_schema(artifact, schema_path)
    if artifact.get("schema_id") != REQUEST_USAGE_SCHEMA_ID:
        raise ValueError("unsupported request-usage schema")
    if artifact.get("content_sha256") != canonical_sha256(
        artifact, excluded_field="content_sha256"
    ):
        raise ValueError("request-usage content SHA-256 mismatch")
    if (
        artifact.get("configured_model_identity")
        != descriptor.get("model_identity")
    ):
        raise ValueError(
            "request usage model does not match the pricing descriptor"
        )
    aggregate = artifact.get("turn_aggregate")
    if aggregate is not None:
        _validate_usage_counts(aggregate)
    level = artifact.get("evidence_level")
    requests = list(artifact.get("requests") or [])
    if level == "unavailable":
        if aggregate is not None or requests or not artifact.get(
            "unavailable_reason"
        ):
            raise ValueError("unavailable request usage has contradictory evidence")
        return
    if level == "turn_aggregate":
        if (
            aggregate is None
            or requests
            or artifact.get("request_count") is not None
            or artifact.get("billable_request_count") is not None
            or artifact.get("retry_count") is not None
            or artifact.get("terminal_attempts_complete") is not None
            or artifact.get("request_aggregate_reconciled") is not None
            or not artifact.get("unavailable_reason")
        ):
            raise ValueError(
                "turn-aggregate evidence must not claim request completeness"
            )
        return
    if level != "request":
        raise ValueError("unsupported request-usage evidence level")
    if (
        aggregate is None
        or artifact.get("terminal_attempts_complete") is not True
        or artifact.get("request_aggregate_reconciled") is not True
        or artifact.get("unavailable_reason")
    ):
        raise ValueError(
            "request evidence requires complete reconciled terminal attempts"
        )
    ordinals = [item.get("ordinal") for item in requests]
    if ordinals != list(range(1, len(requests) + 1)):
        raise ValueError("request ordinals must be contiguous and unique")
    journal_ordinals = [item.get("journal_ordinal") for item in requests]
    if (
        any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value <= 0
            for value in journal_ordinals
        )
        or journal_ordinals != sorted(set(journal_ordinals))
    ):
        raise ValueError(
            "request journal ordinals must be positive, ordered, and unique"
        )
    response_ids = [item.get("response_id") for item in requests]
    if (
        any(not isinstance(value, str) or not value for value in response_ids)
        or len(response_ids) != len(set(response_ids))
    ):
        raise ValueError("request response identities must be non-empty and unique")
    thread_ids = {item.get("thread_id") for item in requests}
    turn_ids = {item.get("turn_id") for item in requests}
    if (
        len(thread_ids) != 1
        or len(turn_ids) != 1
        or any(not isinstance(value, str) or not value for value in thread_ids)
        or any(not isinstance(value, str) or not value for value in turn_ids)
    ):
        raise ValueError("request records must belong to one thread and turn")
    threshold = int(descriptor["long_context"]["threshold_input_tokens"])
    totals = {
        key: 0
        for key in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens_including_reasoning",
            "reasoning_output_tokens",
        )
    }
    cache_write_total = 0
    ordinary_total = 0
    cache_components_complete = True
    billable_count = 0
    for request in requests:
        _validate_usage_counts(request)
        if request["model_identity"] != descriptor["model_identity"]:
            raise ValueError("request model does not match pricing descriptor")
        for field in ("execution_mode", "service_tier", "region"):
            if request[field] != descriptor[field]:
                raise ValueError(
                    f"request {field} does not match pricing descriptor"
                )
        expected_band = (
            "long_context"
            if request["input_tokens"] > threshold
            else "standard"
        )
        if request["long_context_classification"] != expected_band:
            raise ValueError("request long-context classification is wrong")
        if request["hosted_tool_usage"]:
            raise ValueError("descriptor has no separately priced hosted tools")
        if request["attempt_outcome"] == "completed" and not request["billable"]:
            raise ValueError("completed requests must be billable")
        if not request["billable"]:
            if any(
                request[field]
                for field in (
                    "input_tokens",
                    "cached_input_tokens",
                    "output_tokens_including_reasoning",
                    "reasoning_output_tokens",
                )
            ):
                raise ValueError(
                    "non-billable failed attempts must not carry usage"
                )
        else:
            billable_count += 1
        for field in totals:
            totals[field] += int(request[field])
        if request["cache_write_tokens"] is None:
            cache_components_complete = False
        else:
            cache_write_total += int(request["cache_write_tokens"])
            ordinary_total += int(
                request["ordinary_uncached_nonwrite_tokens"]
            )
    expected_aggregate = dict(totals)
    expected_aggregate["cache_write_tokens"] = (
        cache_write_total if cache_components_complete else None
    )
    expected_aggregate["ordinary_uncached_nonwrite_tokens"] = (
        ordinary_total if cache_components_complete else None
    )
    if dict(aggregate) != expected_aggregate:
        raise ValueError("request totals disagree with the turn aggregate")
    if artifact.get("request_count") != len(requests):
        raise ValueError("request count does not match request records")
    if artifact.get("billable_request_count") != billable_count:
        raise ValueError("billable request count does not match records")
    if artifact.get("retry_count") is not None:
        raise ValueError(
            "Codex raw response events do not expose retry relationships"
        )


def _apply_multiplier(value: Fraction, multiplier: Mapping[str, Any]) -> Fraction:
    return value * _rational(multiplier, field="pricing multiplier")


def _require_integer_nanos(value: Fraction) -> int:
    if value.denominator != 1:
        raise ValueError(
            "pricing descriptor produces fractional USD nanos"
        )
    return value.numerator


def _price_components(
    *,
    ordinary: int,
    cache_write: int,
    cached_read: int,
    output: int,
    long_context: bool,
    descriptor: Mapping[str, Any],
) -> int:
    rates = descriptor["rates_usd_nanos_per_token"]
    input_cost = Fraction(
        ordinary * int(rates["ordinary_uncached_input"])
        + cache_write * int(rates["cache_write"])
        + cached_read * int(rates["cached_input_read"])
    )
    output_cost = Fraction(
        output * int(rates["output_including_reasoning"])
    )
    if long_context:
        input_cost = _apply_multiplier(
            input_cost, descriptor["long_context"]["input_multiplier"]
        )
        output_cost = _apply_multiplier(
            output_cost, descriptor["long_context"]["output_multiplier"]
        )
    total = input_cost + output_cost
    total = _apply_multiplier(
        total, descriptor["service_tier_multiplier"]
    )
    total = _apply_multiplier(
        total, descriptor["regional_processing_multiplier"]
    )
    return _require_integer_nanos(total)


def _presentation_usd(value: int | None) -> str | None:
    if value is None:
        return None
    dollars = Decimal(value) / Decimal(USD_NANOS_PER_USD)
    return str(dollars.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _cost_result(
    *,
    status: str,
    reason: str,
    exact: int | None,
    lower: int | None,
    upper: int | None,
    descriptor: Mapping[str, Any],
    request_usage: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if status == "exact":
        if exact is None or lower != exact or upper != exact:
            raise ValueError("exact equivalent cost requires equal bounds")
    elif status == "bounded":
        if (
            exact is not None
            or lower is None
            or upper is None
            or lower >= upper
        ):
            raise ValueError(
                "bounded equivalent cost requires distinct finite bounds"
            )
    elif status == "unavailable":
        if any(value is not None for value in (exact, lower, upper)):
            raise ValueError("unavailable equivalent cost cannot carry values")
    else:
        raise ValueError("unsupported equivalent-cost state")
    return {
        "contract_id": CONTRACT_ID,
        "scope": "solve_only",
        "label": "Equivalent Codex API cost",
        "actual_invoice": False,
        "status": status,
        "currency": descriptor["currency"],
        "exact_usd_nanos": exact,
        "lower_bound_usd_nanos": lower,
        "upper_bound_usd_nanos": upper,
        "reason": reason,
        "pricing_descriptor_id": descriptor["descriptor_id"],
        "pricing_descriptor_sha256": descriptor[
            "descriptor_content_sha256"
        ],
        "request_usage_sha256": (
            request_usage["content_sha256"]
            if request_usage is not None
            else None
        ),
        "request_evidence_level": (
            request_usage["evidence_level"]
            if request_usage is not None
            else "unavailable"
        ),
        "request_count": (
            request_usage["request_count"]
            if request_usage is not None
            else None
        ),
        "billable_request_count": (
            request_usage["billable_request_count"]
            if request_usage is not None
            else None
        ),
        "retry_count": (
            request_usage["retry_count"]
            if request_usage is not None
            else None
        ),
        "presentation_exact_usd": _presentation_usd(exact),
        "presentation_lower_bound_usd": _presentation_usd(lower),
        "presentation_upper_bound_usd": _presentation_usd(upper),
    }


def unavailable_equivalent_cost(
    descriptor: Mapping[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    """Return a strict unavailable result for a row that has no measured solve."""

    if not reason:
        raise ValueError("unavailable equivalent cost requires a reason")
    return _cost_result(
        status="unavailable",
        reason=reason,
        exact=None,
        lower=None,
        upper=None,
        descriptor=descriptor,
        request_usage=None,
    )


def derive_equivalent_cost(
    request_usage: Mapping[str, Any],
    *,
    descriptor: Mapping[str, Any],
    request_schema_path: Path,
) -> dict[str, Any]:
    """Derive exact, bounded, or unavailable solve-only equivalent cost."""

    validate_request_usage(
        request_usage,
        descriptor=descriptor,
        schema_path=request_schema_path,
    )
    level = request_usage["evidence_level"]
    if level == "unavailable":
        return _cost_result(
            status="unavailable",
            reason=request_usage["unavailable_reason"],
            exact=None,
            lower=None,
            upper=None,
            descriptor=descriptor,
            request_usage=request_usage,
        )
    if level == "turn_aggregate":
        aggregate = request_usage["turn_aggregate"]
        input_tokens = int(aggregate["input_tokens"])
        cached = int(aggregate["cached_input_tokens"])
        observed = input_tokens - cached
        output = int(aggregate["output_tokens_including_reasoning"])
        threshold = int(descriptor["long_context"]["threshold_input_tokens"])
        possible_long_context = input_tokens > threshold
        cache_write = aggregate["cache_write_tokens"]
        allocations = (
            (observed, 0)
            if cache_write is None
            else (
                int(aggregate["ordinary_uncached_nonwrite_tokens"]),
                int(cache_write),
            )
        )
        if cache_write is None:
            endpoint_costs = []
            for ordinary, writes in ((observed, 0), (0, observed)):
                for long_context in (
                    (False, True) if possible_long_context else (False,)
                ):
                    endpoint_costs.append(
                        _price_components(
                            ordinary=ordinary,
                            cache_write=writes,
                            cached_read=cached,
                            output=output,
                            long_context=long_context,
                            descriptor=descriptor,
                        )
                    )
        else:
            ordinary, writes = allocations
            endpoint_costs = [
                _price_components(
                    ordinary=ordinary,
                    cache_write=writes,
                    cached_read=cached,
                    output=output,
                    long_context=long_context,
                    descriptor=descriptor,
                )
                for long_context in (
                    (False, True) if possible_long_context else (False,)
                )
            ]
        lower = min(endpoint_costs)
        upper = max(endpoint_costs)
        if lower == upper:
            return _cost_result(
                status="unavailable",
                reason=(
                    "turn-aggregate telemetry cannot prove request and retry "
                    "completeness for an exact result"
                ),
                exact=None,
                lower=None,
                upper=None,
                descriptor=descriptor,
                request_usage=request_usage,
            )
        reasons = [
            "request and retry boundaries are unavailable",
        ]
        if cache_write is None:
            reasons.append("cache-write allocation is unobserved")
        if possible_long_context:
            reasons.append(
                "request-level long-context pricing bands are unobserved"
            )
        return _cost_result(
            status="bounded",
            reason="; ".join(reasons),
            exact=None,
            lower=lower,
            upper=upper,
            descriptor=descriptor,
            request_usage=request_usage,
        )

    lower_total = 0
    upper_total = 0
    bounded_reasons: list[str] = []
    for request in request_usage["requests"]:
        if not request["billable"]:
            continue
        input_tokens = int(request["input_tokens"])
        cached = int(request["cached_input_tokens"])
        observed = input_tokens - cached
        output = int(request["output_tokens_including_reasoning"])
        long_context = request["long_context_classification"] == "long_context"
        cache_write = request["cache_write_tokens"]
        if cache_write is None:
            endpoints = [
                _price_components(
                    ordinary=ordinary,
                    cache_write=writes,
                    cached_read=cached,
                    output=output,
                    long_context=long_context,
                    descriptor=descriptor,
                )
                for ordinary, writes in ((observed, 0), (0, observed))
            ]
            lower_total += min(endpoints)
            upper_total += max(endpoints)
            bounded_reasons.append(
                f"request {request['ordinal']} omitted cache-write telemetry"
            )
        else:
            request_cost = _price_components(
                ordinary=int(
                    request["ordinary_uncached_nonwrite_tokens"]
                ),
                cache_write=int(cache_write),
                cached_read=cached,
                output=output,
                long_context=long_context,
                descriptor=descriptor,
            )
            lower_total += request_cost
            upper_total += request_cost
    if bounded_reasons:
        return _cost_result(
            status="bounded",
            reason="; ".join(bounded_reasons),
            exact=None,
            lower=lower_total,
            upper=upper_total,
            descriptor=descriptor,
            request_usage=request_usage,
        )
    return _cost_result(
        status="exact",
        reason="all request-level pricing inputs are observed and reconciled",
        exact=lower_total,
        lower=lower_total,
        upper=lower_total,
        descriptor=descriptor,
        request_usage=request_usage,
    )


def aggregate_equivalent_cost(
    rows: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Sum exact/range evidence without inventing unavailable values."""

    costs = [row.get("equivalent_cost") or {} for row in rows]
    statuses = [str(cost.get("status") or "unavailable") for cost in costs]
    exact_count = statuses.count("exact")
    bounded_count = statuses.count("bounded")
    unavailable_count = statuses.count("unavailable")
    result: dict[str, Any] = {
        "contract_id": CONTRACT_ID,
        "scope": "solve_only",
        "currency": "USD",
        "run_count": len(rows),
        "exact_run_count": exact_count,
        "bounded_run_count": bounded_count,
        "unavailable_run_count": unavailable_count,
        "status": (
            "unavailable"
            if unavailable_count
            else "bounded"
            if bounded_count
            else "exact"
        ),
        "exact_total_usd_nanos": None,
        "lower_total_usd_nanos": None,
        "upper_total_usd_nanos": None,
        "reasons": sorted(
            {
                str(cost.get("reason"))
                for cost in costs
                if cost.get("reason")
            }
        ),
    }
    if unavailable_count:
        return result
    lower = sum(int(cost["lower_bound_usd_nanos"]) for cost in costs)
    upper = sum(int(cost["upper_bound_usd_nanos"]) for cost in costs)
    result["lower_total_usd_nanos"] = lower
    result["upper_total_usd_nanos"] = upper
    if not bounded_count:
        result["exact_total_usd_nanos"] = lower
    return result
