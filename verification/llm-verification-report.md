# Semantic maintenance self-review

Overall: **passed** for making the qualification-to-paid transition safely resumable.

- `LLM-001` preflight contract fidelity: **passed**
- `LLM-002` base/reference outcome plausibility: **passed**
- `LLM-003` skip-policy appropriateness: **passed**
- `LLM-004` process-validity semantics: **passed**
- `LLM-005` field-provenance honesty: **passed**
- `LLM-006` replay-package completeness: **not applicable until the replacement full run**

The initial paid transition correctly preserved the original no-model approval-protocol evidence.
Normal coordinator setup later regenerated the live evidence with new ephemeral paths, timestamps,
and an authenticated journal. A subsequent resume incorrectly required that regenerated evidence to
match the original qualification-only content hash. The repair uses the immutable preserved copy as
the original hash authority and independently validates the regenerated live evidence as internally
valid, no-model, and zero-child. Corrupt live evidence and any mutation of preserved evidence still
fail closed.

The v12 authorization binds the stopped source, tree, effective and frozen configuration hashes,
three completed comparisons, 21 validated and release-audited exact-cost diagnostic rows, 1,083
reconciled requests, 141 approvals, 1,131 blocked attempts, zero invalidating accesses,
77,879,563,000 USD nanos, and all named evidence hashes. None of those rows may be resumed, reused,
combined, reclassified, or published.

No treatment, scoring, timing, cost, matching, protected verification, approval decision, or
anti-leak rule changed. Focused regression tests pass. The complete harness and verification registry
remain required before commit. No additional model call was used. The new source still needs fresh
21-cell no-model qualification, exact-model cost and reviewer readiness, zero-child transition, an
explicit second zero-child resume, and a distinct 84-child cohort before publication.
