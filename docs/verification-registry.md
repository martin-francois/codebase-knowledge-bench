# Verification registry

This table is generated from `verification/verification-registry.json`.

| ID | Area | Kind | Severity | Status | Invariant |
| --- | --- | --- | --- | --- | --- |
| `COR-001` | correctness | automated | blocker | implemented | Use requirement weights rather than test counts |
| `COR-002` | correctness | automated | high | implemented | Keep duplicate tests from changing score |
| `COR-003` | correctness | automated | blocker | implemented | Prevent critical failures from averaging away |
| `COR-004` | correctness | automated | blocker | implemented | Exclude candidate tests from protected correctness |
| `COR-005` | correctness | automated | high | implemented | Treat non-evaluable reference behavior as diagnostic |
| `COR-006` | correctness | automated | high | implemented | Fail source-similar behaviorally wrong code |
| `COR-007` | correctness | automated | high | implemented | Pass source-different behaviorally correct code |
| `COR-008` | correctness | automated | high | implemented | Represent partial requirements partially |
| `COR-009` | correctness | automated | high | implemented | Distinguish over-broad rejection from correct negative behavior |
| `COR-010` | correctness | automated | blocker | implemented | Fail side effects before critical validation |
| `COR-011` | correctness | automated | high | implemented | Represent regression-safe issue-incomplete results |
| `COR-012` | correctness | automated | high | implemented | Publish requirement vectors alongside scalar scores |
| `COR-013` | correctness | automated | high | implemented | Reproduce historical methodology unchanged |
| `COR-014` | correctness | automated | blocker | implemented | Prevent vNext from overwriting historical outputs |
| `COR-015` | correctness | automated | high | implemented | Expose requirement dimensions in dashboard |
| `COR-016` | correctness | automated | blocker | implemented | Derive task success from critical/direct/common rules |
| `COR-ISSUE-001` | correctness | automated | high | implemented | Detect ceiling tasks |
| `COR-ISSUE-002` | correctness | automated | high | implemented | Detect floor tasks |
| `COR-ISSUE-003` | correctness | automated | high | implemented | Warn when one issue supplies all quality differentiation |
| `COR-ISSUE-004` | correctness | automated | high | implemented | Report issue-skill coverage |
| `COR-ISSUE-005` | correctness | automated | high | implemented | Make minimum issue-cluster policy explicit |
| `COR-ISSUE-006` | correctness | automated | high | implemented | Require diversity for broad claims |
| `COR-ISSUE-007` | correctness | automated | high | implemented | Require minimum independent behavior cases |
| `COR-ISSUE-008` | correctness | automated | high | implemented | Keep issue-selection rationale source-controlled |
| `LLM-001` | documentation | llm_manual | high | documented | Cross-artifact semantic consistency |
| `LLM-002` | documentation | llm_manual | high | documented | Issue-contract fidelity |
| `LLM-003` | documentation | llm_manual | high | documented | Correctness-weight and criticality review |
| `LLM-004` | documentation | llm_manual | high | documented | Mutation adequacy review |
| `LLM-005` | documentation | llm_manual | high | documented | Cache interpretation and fairness review |
| `LLM-006` | documentation | llm_manual | high | documented | Statistical-claim calibration |
| `LLM-007` | documentation | llm_manual | high | documented | Operational-versus-attribution separation |
| `LLM-008` | documentation | llm_manual | high | documented | Archive and provenance completeness |
| `LLM-009` | documentation | llm_manual | high | documented | Recommendation calibration |
| `LLM-010` | documentation | llm_manual | high | documented | Regression-risk and reward-hacking review |
| `PUB-001` | publication | automated | blocker | implemented | Verify canonical ZIP SHA-256 before supplement generation |
| `PUB-002` | publication | automated | blocker | implemented | Verify canonical manifest count and root |
| `PUB-003` | publication | automated | blocker | implemented | Verify every canonical manifest path, size, and SHA-256 |
| `PUB-004` | publication | automated | blocker | implemented | Discover and validate every embedded review manifest dynamically |
| `PUB-005` | publication | automated | blocker | implemented | Parse all 63 primary JSONL streams and reconcile usage |
| `PUB-006` | publication | automated | blocker | implemented | Reconcile 63 terminal arms and 64 actual child spawns |
| `PUB-007` | publication | automated | blocker | implemented | Bind operator summary to one archive and canonical result |
| `PUB-008` | publication | automated | blocker | implemented | Recompute every displayed operator-summary value from canonical JSON |
| `PUB-009` | publication | automated | blocker | implemented | Label arithmetic aggregates descriptive |
| `PUB-010` | publication | automated | blocker | implemented | Label paired geometric effects primary matched effects |
| `PUB-011` | publication | automated | blocker | implemented | Distinguish intended-tool totals and means per task |
| `PUB-012` | publication | automated | blocker | implemented | Package all seven referenced retry-provenance artifacts |
| `PUB-013` | publication | automated | blocker | implemented | Match retry-provenance hashes to completed-retry evidence |
| `PUB-014` | publication | automated | blocker | implemented | Validate semantic invariants in retry provenance |
| `PUB-015` | publication | automated | blocker | implemented | Validate dashboard schema and canonical join |
| `PUB-016` | publication | automated | blocker | implemented | Reject external dashboard dependencies |
| `PUB-017` | publication | automated | blocker | implemented | Validate browser rendering and synchronized accessible table |
| `PUB-018` | publication | automated | blocker | implemented | Reconstruct all source roles |
| `PUB-019` | publication | automated | blocker | implemented | Validate detached checksums and receipts |
| `PUB-020` | publication | automated | blocker | implemented | Require non-inferiority, heterogeneity, cache/retry sensitivity, attribution, cost limits, and cluster warning |
| `PUB-021` | publication | automated | blocker | implemented | Validate supplement manifest paths and root |
| `PUB-022` | publication | automated | blocker | implemented | Require generator source, tests, and schemas to be tracked and archived |
| `PUB-023` | publication | automated | blocker | implemented | Require no task-related untracked or modified publication source |
| `PUB-024` | publication | automated | blocker | implemented | Reject values from another archive identity |
| `PUB-025` | publication | automated | blocker | implemented | Prohibit model or child-process launch during supplement generation |
| `SEC-001` | security | external_capability | high | not_automatable | Hard external-egress denial remains an explicit external capability limitation. |
| `TOK-001` | tokens | automated | high | implemented | Parse authoritative turn.completed token fields |
| `TOK-002` | tokens | automated | high | implemented | Enforce input equals cached plus observed non-cached |
| `TOK-003` | tokens | automated | high | implemented | Distinguish unavailable cache writes from zero |
| `TOK-004` | tokens | automated | high | implemented | Suppress pricing cost when cache writes or prices are incomplete |
| `TOK-005` | tokens | automated | high | implemented | Handle zero-input cache-hit rate |
| `TOK-006` | tokens | automated | high | implemented | Publish cache-weight winner sensitivity |
| `TOK-007` | tokens | automated | high | implemented | Summarize repetition and serial-position cache effects |
| `TOK-008` | tokens | automated | high | implemented | Describe 30-minute TTL as a minimum, not eviction |
| `TOK-009` | tokens | automated | high | implemented | Record natural mode when isolation is unavailable |
| `TOK-010` | tokens | automated | high | implemented | Use per-arm cache keys only after official feature detection |
| `TOK-011` | tokens | automated | high | implemented | Expose cached and observed non-cached dashboard views |
| `TOK-012` | tokens | automated | high | implemented | Keep arithmetic and paired token effects distinct |
| `TOK-013` | tokens | automated | high | implemented | Parse canonical historical usage with nullable future fields |
| `TOK-014` | tokens | automated | high | implemented | Use cache-write telemetry when present |
| `TOK-015` | tokens | automated | high | implemented | Surface unavailable telemetry in reports and tooltips |
| `TOK-016` | tokens | automated | high | implemented | Make cache-share and position/gap analysis deterministic |
| `TOK-017` | tokens | automated | high | implemented | Reproduce canonical cache analysis without model calls |
| `VER-001` | documentation | automated | blocker | implemented | Verification IDs, paths, enforcement, and rendered documentation remain synchronized. |
