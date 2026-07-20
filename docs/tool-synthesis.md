# Tool Synthesis

This document records the comparison of the six uploaded documentation tools under
`/home/server/tools` with the repository's root `AGENTS.md` and `SPEC.md`.

## Decision rules

- Preserve the current operational tool comparison and attributable-tool-effect model.
- Preserve stable requirement IDs, root-level layout, private status, exact published profile,
  anti-leak gates, formulas, and existing traceability.
- Adopt a tool clause only when it is clearer, stricter, or more reproducible and does not
  restore superseded eligibility or scoring behavior.
- Do not copy alternate focus thresholds, field names, commands, or paths that conflict with the
  implemented versioned contract.

## Agent tools

### `AGENTS_1.md`

Strongest contribution: comprehensive operational discipline. Incorporated its centralized
derivation rule, immutable evidence handling, JSON preference, contextual error handling, stale-rule
search, validation ladder, archive hygiene, and GitHub completion expectations. Its repeated copies of
existing scoring and benchmark requirements were not duplicated because `SPEC.md` remains normative.

### `agents_2.md`

Strongest contribution: concise engineering boundaries. Incorporated pure derivation primitives,
tool-specific adapters versus tool-neutral scoring, untrusted-input handling, persisted rule
versions and denominators, complete-diff review, and formal read-only compliance-audit behavior.
References to lowercase `spec.md` were normalized to the root `SPEC.md`.

### `AGENTS_3.md`

Strongest contribution: completion and quality gates. Incorporated the cheap-to-expensive validation
ladder, full-precision/report-rounding discipline, explicit no-child-solve default for derivation work,
and stronger final checks for schemas, reports, output, and secrets. Pyrefly/Ruff mandates were not
adopted because this repository does not currently declare those dependencies or configurations.

## Specification tools

### `SPEC_1.md`

Strongest contribution: exhaustive lifecycle coverage and definition-of-done detail. Incorporated the
explicit 63-run published suite, immutable plan input, versioned classification metadata, centralized
derivation, audit method, and completion discipline. Its alternative focused-context rule (50 entities,
25,000 characters, and 20 percent precision) was rejected because the current specification uses the implemented
40-item, 4:1 rejected-to-accepted, and 400-node limits.

### `spec_2.md`

Strongest contribution: engineering and adapter contracts. Incorporated adapter-neutral boundaries,
safe derived-output publication, issue-context normalization, untrusted-input parsing, and explicit
audit/recomputation expectations. Alternate environment names such as `BENCH_REPO_URL` were not adopted
because the current interface uses `BENCH_TARGET_REPO_URL`.

### `spec_3.md`

Strongest contribution: concise field and lifecycle semantics. Incorporated full-precision versus
display-rounding requirements, versioned focus limits, published suite size, deterministic adapter
normalization, and formal audit verdict semantics. Duplicate descriptions of existing formulas,
fixtures, artifacts, and anti-leak rules were not repeated.

## Result

The current documents remain shorter than the union of all tools while now retaining their best
non-conflicting engineering guidance. `SPEC.md` remains implementation-independent and authoritative;
`AGENTS.md` translates it into repository-specific working rules.
