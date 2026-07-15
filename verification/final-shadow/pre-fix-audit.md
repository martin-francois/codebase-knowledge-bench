# Final production-shadow pre-fix audit

Source: `a74d9639c8dbcadb3855f4992539c4564c20168d`

## SHADOW-001: Live parser output does not satisfy current token schema

- Locations: scripts/run_benchmark.py:4032, schemas/token-usage-current.schema.json:3
- Command: `uv run python <embedded deterministic pre-fix audit>`
- Why: A future live row can fail its own current schema or omit cache-scope provenance.
- Planned fix: Route parser output through the authoritative token normalizer and use it for every row state.
- Regression: Parse a real turn.completed fixture and validate the actual row schema; remove each field and add retired fields.

```json
{
  "output_including_reasoning": 20,
  "parser_keys": [
    "cache_reads_observed",
    "cache_reuse_source_identifiable",
    "cache_write_tokens",
    "cached_input_tokens",
    "cross_arm_cache_reuse_identifiable",
    "errors",
    "execution_call_lifecycle",
    "execution_calls_cancelled",
    "execution_calls_completed",
    "execution_calls_failed",
    "execution_calls_started",
    "execution_calls_successful",
    "execution_calls_unfinished",
    "execution_lifecycle_anomalies",
    "file_change_items",
    "final_child_message",
    "input_tokens",
    "jsonl_parse_valid",
    "malformed_jsonl_count",
    "malformed_jsonl_lines",
    "mcp_calls_cancelled",
    "mcp_calls_completed",
    "mcp_calls_failed",
    "mcp_calls_started",
    "mcp_calls_successful",
    "mcp_calls_unfinished",
    "modeled_weighted_token_load",
    "non_reasoning_output_tokens",
    "observed_non_cached_input_tokens",
    "output_tokens_including_reasoning",
    "reasoning_output_tokens",
    "request_level_usage_available",
    "shell_calls_cancelled",
    "shell_calls_completed",
    "shell_calls_failed",
    "shell_calls_started",
    "shell_calls_successful",
    "shell_calls_unfinished",
    "token_weight_sensitivity",
    "total_reported_tokens",
    "total_tool_calls",
    "turn_completed",
    "turn_failed",
    "turn_started",
    "uncached_nonwrite_input_tokens",
    "unknown_events",
    "unknown_item_types",
    "warnings",
    "web_calls_cancelled",
    "web_calls_completed",
    "web_calls_failed",
    "web_calls_started",
    "web_calls_successful",
    "web_calls_unfinished"
  ],
  "reasoning": 5,
  "token_schema_errors": [
    "'cache_hit_rate' is a required property",
    "'cache_isolation_mode' is a required property",
    "'cache_maximum_retention_known' is a required property",
    "'cache_ttl_minimum_seconds' is a required property",
    "'cache_write_metrics_available' is a required property",
    "'cache_write_metrics_unavailable_reason' is a required property",
    "'token_accounting_id' is a required property",
    "Additional properties are not allowed ('errors', 'execution_call_lifecycle', 'execution_calls_cancelled', 'execution_calls_completed', 'execution_calls_failed', 'execution_calls_started', 'execution_calls_successful', 'execution_calls_unfinished', 'execution_lifecycle_anomalies', 'file_change_items', 'final_child_message', 'jsonl_parse_valid', 'malformed_jsonl_count', 'malformed_jsonl_lines', 'mcp_calls_cancelled', 'mcp_calls_completed', 'mcp_calls_failed', 'mcp_calls_started', 'mcp_calls_successful', 'mcp_calls_unfinished', 'modeled_weighted_token_load', 'shell_calls_cancelled', 'shell_calls_completed', 'shell_calls_failed', 'shell_calls_started', 'shell_calls_successful', 'shell_calls_unfinished', 'token_weight_sensitivity', 'total_tool_calls', 'turn_completed', 'turn_failed', 'turn_started', 'unknown_events', 'unknown_item_types', 'warnings', 'web_calls_cancelled', 'web_calls_completed', 'web_calls_failed', 'web_calls_started', 'web_calls_successful', 'web_calls_unfinished' were unexpected)"
  ]
}
```

## SHADOW-002: Execution row accepts retired fields

- Locations: schemas/execution-results.schema.json:28
- Command: `uv run python <embedded deterministic pre-fix audit>`
- Why: Retired fields can silently override or coexist with current semantics.
- Planned fix: Make a strict reusable current row schema with additionalProperties false.
- Regression: Validate a production row, then inject each retired field and require rejection.

```json
{
  "additionalProperties": null,
  "retired_field_accepted": {
    "common_regression_pass_fraction": false,
    "full_reference_conformance_pass": false,
    "issue_contract_command_passed": false,
    "patch_quality_raw_points": false
  },
  "synthesized_base_errors": [
    "'x' does not match '^issue-[0-9]+$'",
    "[] should be non-empty"
  ]
}
```

## SHADOW-003: Suite aggregation ignores task_success in favor of retired reference conformance

- Locations: scripts/run_benchmark_suite.py:1973, scripts/run_benchmark_suite.py:1977
- Command: `uv run python <embedded deterministic pre-fix audit>`
- Why: Successful current rows report zero successes and null per-success cost.
- Planned fix: Aggregate only task_success and keep diagnostics separate.
- Regression: Aggregate one current successful row and require count/rate one and finite cost per success.

```json
{
  "aggregate_task_success": 0,
  "full_reference_count": 0,
  "per_success_cost": null,
  "task_success_input": true
}
```

## SHADOW-004: Non-blocking diagnostic failure corrupts absolute task quality

- Locations: scripts/benchmark_hardening.py:862, scripts/benchmark_hardening.py:883
- Command: `uv run python <embedded deterministic pre-fix audit>`
- Why: Reference diagnostics become an undeclared second task-success gate.
- Planned fix: Delegate to authoritative task_success and classify diagnostics separately.
- Regression: Fail one required_for_task_success=false diagnostic and require task success unchanged.

```json
{
  "absolute_quality": {
    "behavioral_correctness_score": 100.0,
    "common_regression_full_pass": true,
    "common_regression_pass_fraction": null,
    "critical_requirement_status": null,
    "failed_requirements": [
      "diagnostic"
    ],
    "requested_behavior_score": 100,
    "task_quality_class": "task_partial",
    "task_success": false
  },
  "after_task_success": false,
  "before_task_success": true,
  "task_quality_class": "task_partial"
}
```

## SHADOW-005: Patch review is evaluated against pre-score state and emits a retired top-level field

- Locations: scripts/run_benchmark.py:5666, scripts/run_benchmark.py:5348
- Command: `uv run python <embedded deterministic pre-fix audit>`
- Why: Patch quality is silently zeroed in current scoring and its review reads fields that do not exist yet.
- Planned fix: Score protected behavior first, then derive nullable normalized patch quality as nested evidence and never gate success.
- Regression: Production shadow asserts call order and current patch-quality output.

```json
{
  "derive_and_score_from_run_metadata(": 228974,
  "patch_quality_raw_points": 230588,
  "patch_quality_score=": 229372,
  "qualitative_score(m, reference_patch)": 226542
}
```

## SHADOW-006: Scorer reference diagnostic is overwritten by retired reference fraction

- Locations: scripts/run_benchmark.py:5333, scripts/validate_benchmark_run.py:725
- Command: `uv run python <embedded deterministic pre-fix audit>`
- Why: Current requirement-vector diagnostics can be replaced by a broader retired metric without detection.
- Planned fix: Preserve scorer output and independently rederive the diagnostic rate.
- Regression: Inject a different retired rate and require validator failure.

```json
{
  "scorer_assignment_line": "scripts/run_benchmark.py:5333",
  "validator_reference_comparison_present": true
}
```

## SHADOW-007: Suite variant_rows is untyped

- Locations: schemas/suite-results.schema.json:5
- Command: `uv run python <embedded deterministic pre-fix audit>`
- Why: Any stale or malformed row can enter aggregation, reports, and dashboard.
- Planned fix: Reference the strict current execution-row schema.
- Regression: Validate generated suite rows and inject retired/missing fields.

```json
{
  "stale_row_schema_errors": [],
  "variant_rows_schema": {
    "type": "array"
  }
}
```

## SHADOW-008: Methodology fixture bypasses production parser, writer, aggregation, reports, browser, and handoff

- Locations: scripts/methodology_fixture.py:43, scripts/methodology_fixture.py:127
- Command: `uv run python <embedded deterministic pre-fix audit>`
- Why: Helper-level success does not prove the future live production dataflow.
- Planned fix: Replace fixture with a production-shadow orchestrator over reusable production stages.
- Regression: Require each named production stage and inject a failure at each stage.

```json
{
  "calls_present": {
    "aggregate": false,
    "build_review_handoff": false,
    "load_variant_records": false,
    "parse_jsonl": false,
    "write_report": false,
    "write_results": false
  },
  "manual_analysis": true,
  "manual_row_literal": true
}
```

## SHADOW-009: Retired fields remain across active layers

- Locations: SCORING-MODEL.md:10, SPEC.md:91, SPEC.md:506, scripts/benchmark_hardening.py:883, scripts/operational_tradeoffs.py:97, scripts/operational_tradeoffs.py:98, scripts/run_benchmark.py:3814, scripts/run_benchmark.py:3820, scripts/run_benchmark.py:3821, scripts/run_benchmark.py:3893, scripts/run_benchmark.py:3896, scripts/run_benchmark.py:3901, scripts/run_benchmark.py:3904, scripts/run_benchmark.py:5326, scripts/run_benchmark.py:5346, scripts/run_benchmark.py:5347, scripts/run_benchmark.py:5348, scripts/run_benchmark.py:5586, scripts/run_benchmark.py:5641, scripts/run_benchmark.py:5642
- Command: `uv run python <embedded deterministic pre-fix audit>`
- Why: Parallel semantics remain live and can contaminate results.
- Planned fix: Remove or replace every active match; isolate only opaque external evidence.
- Regression: Repository-wide no-retired-field audit over live paths.

```json
{
  "match_count": 82,
  "matches": [
    {
      "classification": "remove",
      "line": 10,
      "path": "SCORING-MODEL.md",
      "term": "common_regression_pass_fraction"
    },
    {
      "classification": "remove",
      "line": 91,
      "path": "SPEC.md",
      "term": "common_regression_pass_fraction"
    },
    {
      "classification": "remove",
      "line": 506,
      "path": "SPEC.md",
      "term": "common_regression_pass_fraction"
    },
    {
      "classification": "remove",
      "line": 883,
      "path": "scripts/benchmark_hardening.py",
      "term": "common_regression_pass_fraction"
    },
    {
      "classification": "remove",
      "line": 97,
      "path": "scripts/operational_tradeoffs.py",
      "term": "common_regression_pass_fraction"
    },
    {
      "classification": "remove",
      "line": 98,
      "path": "scripts/operational_tradeoffs.py",
      "term": "common_regression_pass_fraction"
    },
    {
      "classification": "remove",
      "line": 3814,
      "path": "scripts/run_benchmark.py",
      "term": "issue_contract_command_passed"
    },
    {
      "classification": "remove",
      "line": 3820,
      "path": "scripts/run_benchmark.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 3821,
      "path": "scripts/run_benchmark.py",
      "term": "issue_contract_command_passed"
    },
    {
      "classification": "remove",
      "line": 3893,
      "path": "scripts/run_benchmark.py",
      "term": "common_regression_pass_fraction"
    },
    {
      "classification": "remove",
      "line": 3896,
      "path": "scripts/run_benchmark.py",
      "term": "issue_contract_command_passed"
    },
    {
      "classification": "remove",
      "line": 3901,
      "path": "scripts/run_benchmark.py",
      "term": "reference_conformance_command_passed"
    },
    {
      "classification": "remove",
      "line": 3904,
      "path": "scripts/run_benchmark.py",
      "term": "issue_contract_command_passed"
    },
    {
      "classification": "remove",
      "line": 5326,
      "path": "scripts/run_benchmark.py",
      "term": "common_regression_pass_fraction"
    },
    {
      "classification": "remove",
      "line": 5346,
      "path": "scripts/run_benchmark.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 5347,
      "path": "scripts/run_benchmark.py",
      "term": "common_regression_pass_fraction"
    },
    {
      "classification": "remove",
      "line": 5348,
      "path": "scripts/run_benchmark.py",
      "term": "patch_quality_raw_points"
    },
    {
      "classification": "remove",
      "line": 5586,
      "path": "scripts/run_benchmark.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 5641,
      "path": "scripts/run_benchmark.py",
      "term": "issue_contract_command_passed"
    },
    {
      "classification": "remove",
      "line": 5642,
      "path": "scripts/run_benchmark.py",
      "term": "reference_conformance_command_passed"
    },
    {
      "classification": "remove",
      "line": 5658,
      "path": "scripts/run_benchmark.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 5673,
      "path": "scripts/run_benchmark.py",
      "term": "common_regression_pass_fraction"
    },
    {
      "classification": "remove",
      "line": 5728,
      "path": "scripts/run_benchmark.py",
      "term": "patch_quality_raw_points"
    },
    {
      "classification": "remove",
      "line": 6056,
      "path": "scripts/run_benchmark.py",
      "term": "patch_quality_raw_points"
    },
    {
      "classification": "remove",
      "line": 6089,
      "path": "scripts/run_benchmark.py",
      "term": "patch_quality_raw_points"
    },
    {
      "classification": "remove",
      "line": 6239,
      "path": "scripts/run_benchmark.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 7270,
      "path": "scripts/run_benchmark.py",
      "term": "issue_contract_command_passed"
    },
    {
      "classification": "remove",
      "line": 7273,
      "path": "scripts/run_benchmark.py",
      "term": "reference_conformance_command_passed"
    },
    {
      "classification": "remove",
      "line": 478,
      "path": "scripts/run_benchmark_suite.py",
      "term": "patch_quality_raw_points"
    },
    {
      "classification": "remove",
      "line": 480,
      "path": "scripts/run_benchmark_suite.py",
      "term": "common_regression_pass_fraction"
    },
    {
      "classification": "remove",
      "line": 725,
      "path": "scripts/run_benchmark_suite.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 725,
      "path": "scripts/run_benchmark_suite.py",
      "term": "full_reference_conformance_passes"
    },
    {
      "classification": "remove",
      "line": 726,
      "path": "scripts/run_benchmark_suite.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 731,
      "path": "scripts/run_benchmark_suite.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 731,
      "path": "scripts/run_benchmark_suite.py",
      "term": "full_reference_conformance_passes"
    },
    {
      "classification": "remove",
      "line": 1073,
      "path": "scripts/run_benchmark_suite.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 1073,
      "path": "scripts/run_benchmark_suite.py",
      "term": "full_reference_conformance_passes"
    },
    {
      "classification": "remove",
      "line": 1074,
      "path": "scripts/run_benchmark_suite.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 1081,
      "path": "scripts/run_benchmark_suite.py",
      "term": "issue_contract_command_passed"
    },
    {
      "classification": "remove",
      "line": 1085,
      "path": "scripts/run_benchmark_suite.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 1085,
      "path": "scripts/run_benchmark_suite.py",
      "term": "full_reference_conformance_passes"
    },
    {
      "classification": "remove",
      "line": 1088,
      "path": "scripts/run_benchmark_suite.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 1088,
      "path": "scripts/run_benchmark_suite.py",
      "term": "full_reference_conformance_passes"
    },
    {
      "classification": "remove",
      "line": 1977,
      "path": "scripts/run_benchmark_suite.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 2024,
      "path": "scripts/run_benchmark_suite.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 2024,
      "path": "scripts/run_benchmark_suite.py",
      "term": "full_reference_conformance_passes"
    },
    {
      "classification": "remove",
      "line": 2025,
      "path": "scripts/run_benchmark_suite.py",
      "term": "issue_contract_command_passed"
    },
    {
      "classification": "remove",
      "line": 2026,
      "path": "scripts/run_benchmark_suite.py",
      "term": "reference_conformance_command_passed"
    },
    {
      "classification": "remove",
      "line": 2027,
      "path": "scripts/run_benchmark_suite.py",
      "term": "reference_conformance_command_passed"
    },
    {
      "classification": "remove",
      "line": 2055,
      "path": "scripts/run_benchmark_suite.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 2055,
      "path": "scripts/run_benchmark_suite.py",
      "term": "full_reference_conformance_pass_rate"
    },
    {
      "classification": "remove",
      "line": 2114,
      "path": "scripts/run_benchmark_suite.py",
      "term": "patch_quality_raw_points"
    },
    {
      "classification": "remove",
      "line": 2117,
      "path": "scripts/run_benchmark_suite.py",
      "term": "common_regression_pass_fraction"
    },
    {
      "classification": "remove",
      "line": 2928,
      "path": "scripts/run_benchmark_suite.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 2931,
      "path": "scripts/run_benchmark_suite.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 4007,
      "path": "scripts/run_benchmark_suite.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 52,
      "path": "scripts/validate_benchmark_run.py",
      "term": "patch_quality_raw_points"
    },
    {
      "classification": "remove",
      "line": 54,
      "path": "scripts/validate_benchmark_run.py",
      "term": "common_regression_pass_fraction"
    },
    {
      "classification": "remove",
      "line": 1505,
      "path": "scripts/validate_benchmark_run.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 1505,
      "path": "scripts/validate_benchmark_run.py",
      "term": "full_reference_conformance_passes"
    },
    {
      "classification": "remove",
      "line": 1526,
      "path": "scripts/validate_benchmark_run.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 1526,
      "path": "scripts/validate_benchmark_run.py",
      "term": "full_reference_conformance_pass_rate"
    },
    {
      "classification": "false positive",
      "line": 85,
      "path": "scripts/verification_checkers.py",
      "term": "reasoning_output_tokens_including_reasoning"
    },
    {
      "classification": "false positive",
      "line": 120,
      "path": "tests/test_current_methodology.py",
      "term": "reasoning_output_tokens_including_reasoning"
    },
    {
      "classification": "remove",
      "line": 1096,
      "path": "tests/test_harness.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 1097,
      "path": "tests/test_harness.py",
      "term": "issue_contract_command_passed"
    },
    {
      "classification": "remove",
      "line": 1098,
      "path": "tests/test_harness.py",
      "term": "reference_conformance_command_passed"
    },
    {
      "classification": "remove",
      "line": 1138,
      "path": "tests/test_harness.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 1138,
      "path": "tests/test_harness.py",
      "term": "full_reference_conformance_pass_rate"
    },
    {
      "classification": "remove",
      "line": 1140,
      "path": "tests/test_harness.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 1140,
      "path": "tests/test_harness.py",
      "term": "full_reference_conformance_passes"
    },
    {
      "classification": "remove",
      "line": 1267,
      "path": "tests/test_harness.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 2670,
      "path": "tests/test_harness.py",
      "term": "common_regression_pass_fraction"
    },
    {
      "classification": "remove",
      "line": 2782,
      "path": "tests/test_harness.py",
      "term": "common_regression_pass_fraction"
    },
    {
      "classification": "remove",
      "line": 2783,
      "path": "tests/test_harness.py",
      "term": "patch_quality_raw_points"
    },
    {
      "classification": "remove",
      "line": 3079,
      "path": "tests/test_harness.py",
      "term": "full_reference_conformance_pass"
    },
    {
      "classification": "remove",
      "line": 46,
      "path": "tests/test_operational_tradeoffs.py",
      "term": "common_regression_pass_fraction"
    },
    {
      "classification": "remove",
      "line": 35,
      "path": "tests/test_publication_and_inference.py",
      "term": "common_regression_pass_fraction"
    },
    {
      "classification": "immutable external evidence only",
      "line": 496,
      "path": "verification/current-methodology-pre-fix-audit.json",
      "term": "reasoning_output_tokens_including_reasoning"
    },
    {
      "classification": "immutable external evidence only",
      "line": 501,
      "path": "verification/current-methodology-pre-fix-audit.json",
      "term": "reasoning_output_tokens_including_reasoning"
    },
    {
      "classification": "immutable external evidence only",
      "line": 68,
      "path": "verification/current-methodology-pre-fix-audit.md",
      "term": "reasoning_output_tokens_including_reasoning"
    },
    {
      "classification": "immutable external evidence only",
      "line": 69,
      "path": "verification/current-methodology-pre-fix-audit.md",
      "term": "reasoning_output_tokens_including_reasoning"
    }
  ]
}
```

## SHADOW-010: Many automated IDs share broad checker functions and fault mutations

- Locations: scripts/verification_checkers.py:162, verification/verification-registry.json:5
- Command: `uv run python <embedded deterministic pre-fix audit>`
- Why: A family-wide corruption can make many IDs appear independently adversarial.
- Planned fix: Give each ID a named fault, expected ID, collateral allowlist, and stability result.
- Regression: Run checker-specificity matrix and fail unexpected collateral damage.

```json
{
  "automated_count": 32,
  "shared_checker_ids": {
    "contract_binding": [
      "CONTRACT-001",
      "COR-CURRENT-001",
      "COR-CURRENT-009"
    ],
    "correctness_gate": [
      "COR-CURRENT-003",
      "COR-CURRENT-004",
      "COR-CURRENT-005",
      "COR-CURRENT-007",
      "COR-CURRENT-008"
    ],
    "dashboard_schema": [
      "DASH-002",
      "COR-CURRENT-010"
    ],
    "issue_scope": [
      "CONTRACT-002",
      "CONTRACT-003"
    ],
    "mutation_process": [
      "MUT-CURRENT-002",
      "MUT-CURRENT-003",
      "MUT-CURRENT-004"
    ],
    "normative_docs": [
      "DOC-001",
      "TOK-CURRENT-008"
    ],
    "pipeline": [
      "PIPELINE-001",
      "VERIFY-001"
    ],
    "token_cache_null": [
      "TOK-CURRENT-006",
      "TOK-CURRENT-007"
    ],
    "token_fields": [
      "DASH-001",
      "TOK-CURRENT-003",
      "TOK-CURRENT-004",
      "TOK-CURRENT-005",
      "TOK-CURRENT-009"
    ],
    "token_reasoning": [
      "TOK-CURRENT-001",
      "TOK-CURRENT-002"
    ]
  },
  "specific_unique_count": 4,
  "unique_checker_ids": 14
}
```
