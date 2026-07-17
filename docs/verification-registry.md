# Verification registry

The machine-readable registry is `verification/verification-registry.json`. It contains nine
automated checks and six semantic self-review checks. Every automated check has one callable,
one positive fixture, one narrowly targeted negative fixture, structured evidence, and measured
duration.

| ID | Kind | Invariant |
| --- | --- | --- |
| `LIVE-PREFLIGHT-001` | automated | Canonical current config and `IssueSpec` invoke production issue preflight. |
| `SELECTOR-EQUALITY-001` | automated | Contract evidence and observed preflight selectors have exact ownership and equality. |
| `BASE-REFERENCE-001` | automated | Base/reference outcomes are observed through valid processes and satisfy scope rules. |
| `OLD-CONFIG-REJECTION-001` | automated | Removed configuration fields are rejected without translation. |
| `COMMON-SKIP-001` | automated | Common skips and empty suites fail closed. |
| `PROCESS-VALIDITY-001` | automated | Protected-channel process truth-table rules are authoritative. |
| `FIELD-PROVENANCE-001` | automated | Every execution field has one honest provenance kind. |
| `TARGET-BUNDLE-001` | automated | The Git bundle contains all exact target commits and trees. |
| `OFFLINE-REPLAY-001` | automated | The delivered target package replays every required stage without network. |
| `LLM-001`–`LLM-006` | semantic self-review | Contract fidelity, outcome plausibility, skip policy, process validity, provenance honesty, and replay completeness. |
