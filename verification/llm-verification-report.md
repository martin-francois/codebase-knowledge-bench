# Semantic maintenance self-review

Overall: **passed** for clean diagnostic stderr repair commit
`685a4ea48e9cfb084dd81c85da8d657d6158b316`.

- `LLM-001` diagnostic versus solve evidence: **passed**
- `LLM-002` manifest and publication consistency: **passed**
- `LLM-003` narrow positive and negative regression: **passed**
- `LLM-004` failed-cohort preservation: **passed**
- `LLM-005` treatment and result isolation: **passed**
- `LLM-006` new replay-package completeness: **not applicable**

The source-`c5f3abb1e3ae` cohort stopped after seven completed children because
the manifest builder rejected an empty `tool-smoke-mcp-server.stderr` produced
by a clean MCP smoke-server exit. All seven attempts remain terminal invalid
evidence, their exact cost is $28.571237, no measured row is accepted, and the
77 untouched keys remain unlaunched. None of those children may be reused.

The repaired contract keeps that diagnostic capture required and
content-addressed while allowing its byte count to be zero. It does not relax
the separate non-empty solve-evidence requirement. A manifest-level regression
asserts `required=true` and `may_be_empty=true`; a negative regression proves an
arbitrary empty stderr still fails closed.

The repair does not change prompts, issue contracts, protected verification,
schedule, model settings, approval or retry policies, telemetry, pricing,
exact-cost arithmetic, scoring, matching, or inference. No measured child was
launched under the repaired source.

Deterministic review evidence included 565 passing Python tests, 37 passing
verification-registry checks, 19 passing dashboard unit tests, a production
dashboard build, and a real Chromium browser test.

This is implementing-agent self-review. It used no additional model call and is
not independent verification. The reviewed source still requires fresh
no-model qualification, exact-model cost readiness, zero-child transition,
package build, one-shot replay, and all 84 measured outcomes. Hard network
isolation is not required for measured children and is not inferred from replay
isolation.
