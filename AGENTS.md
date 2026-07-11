# Agent Instructions

## Authority and scope

Read `SPEC.md` before changing benchmark behavior. `SPEC.md` is the normative,
implementation-independent contract; `docs/prompt-history-traceability.md` records why
that contract exists. If an intentionally approved behavior change conflicts with the
specification, update the specification and traceability in the same commit. Never edit
the specification merely to excuse an implementation defect.

All active source lives at repository-root paths such as `scripts/`, `tests/`,
`reference-overlays/`, and `tool-guides/`. Never reintroduce an active
`.codex-benchmark/` wrapper. A target checkout and a runtime output root are external
inputs; generated output must not become source.

## Setup and checks

The harness uses Python's standard library and Bash. From the repository root:

```bash
python3 -m py_compile scripts/*.py tests/test_harness.py
python3 tests/test_harness.py -v
bash -n scripts/run_strict_suite.sh
python3 scripts/validate_benchmark_run.py /path/to/execution-or-suite
```

Use `./scripts/run_strict_suite.sh validation` only when a child-run integration check is
actually required. Use fixture-backed recomputation for scoring, reporting, schema, and
validator changes. Never launch the full benchmark matrix as reassurance.

## Determinism and compatibility

- Sort filesystem paths, mappings, sets, variants, issues, and report rows before output.
- Never depend on Python hash or set iteration order.
- Resolve a basename only when exactly one repository-relative path has that basename.
- Use stable JSON field names and deterministic JSON/Markdown ordering.
- Treat schemas and machine-readable fields as public compatibility contracts. Add fields
  compatibly; migrate old evidence explicitly; do not silently reinterpret old fields.
- Preserve legacy aliases only when their meaning is exact and documented. Do not use
  ambiguous aliases such as `valid_success` for `full_correctness_pass`.
- Test deterministic recomputation under at least two `PYTHONHASHSEED` values.

## Raw and derived evidence

Raw child JSONL, stderr, prompts, test logs, patches, snapshots, anti-leak logs, and suite
plans are immutable evidence. Scores, aggregate rows, reports, and bundles are derived.
Recompute derived data from copied or backed-up evidence and validate it before replacing
published derived files. Never rerun a completed solve merely to change scoring.

Every suite row must be reconstructible from execution `results.json`; validators must
compare reconstructed rows, populations, denominators, rankings, exclusions, metadata,
and reports. A mutation to a suite row must fail validation even when its aggregates are
self-consistent.

## Tool adapters

To add or change an adapter:

1. Record the official quickstart source in `tool-guides/quickstart-sources.md`.
2. Implement ordinary-user installation, clean-install measurement, repository setup,
   indexing, smoke, solve exposure, version capture, and sanitized configuration.
3. Keep installation/setup/indexing/onboarding/update outside the timed solve.
4. Expose exactly the intended tool to that non-baseline child.
5. Add focused, broad, empty, error, unavailable, and harness-misconfiguration fixtures.
6. Verify successful, focused, issue-specific returned context before setting
   `tool_integration_valid=true`.
7. Preserve genuine tool errors as operational evidence; classify missing wrappers,
   unknown MCP servers, bad `PATH`, and wrong repository targets as harness defects.

Never fine-tune a tool, add issue-specific hints, or give one treatment bespoke help.
Hosted upload is forbidden unless the target is public and the user explicitly enables it.

## Issue fixtures

To add an issue fixture, define its public issue identity, exact base commit, withheld
reference commit, common verification command, structured primary issue-contract checks,
extended reference-conformance checks, cutoff policy, sanitized issue snapshot, and
reference file list. Child runs must never receive the issue URL, reference commit, hidden
tests, future history, or solution metadata. Natural-language errors must be asserted by
category, required guidance, side effects, and behavior rather than one historical phrase.

Update the suite plan, schema/validator fixtures, README examples, `SPEC.md`, and
traceability together. Add an end-to-end fixture without committing generated run output.

## Scoring changes

Primary operational ranking and secondary attributable tool-effect ranking are separate.
Do not make correctness or useful tool output a primary workflow eligibility gate. Do not
zero a completed implementation because its tool was ineffective. Do not claim tool effect
without focused issue-specific returned context.

When changing scoring:

1. Version the scoring contract.
2. Preserve raw evidence and old derived outputs.
3. Add formula, edge-case, population, denominator, report-consistency, and mutation tests.
4. Recompute fixtures without child solves.
5. Validate machine-readable and Markdown outputs agree.

## Isolation and secrets

Children use fresh sealed one-commit repositories, fresh `codex exec --json` processes,
allowlisted environments, treatment-local homes/config, anti-leak wrappers, and the
strongest practical OS/Codex network isolation. Never expose remotes, sibling runs, global
agent configuration, raw issue URLs, future history, reference patches, credentials, or
tokens. Never print authentication values. Graphify must not document an API-key path.

Audit commands, JSONL, stderr, MCP calls, paths, Git configuration, remotes, and tool state.
Likely solution leakage invalidates evidence. Record reduced confidence when hard network
denial cannot be proved.

## Generated output and archives

Do not commit `executions/`, `runs/`, `suites/`, `sealed-repos/`, `export/`, `raw-issue/`,
tool caches, dependency caches, child homes, indexes, logs, bundles, secrets, or snapshots.
Keep `.gitignore`, archive filters, secret scans, and tests synchronized. Never recursively
include an older `suite-bundle.zip`, including any under `resume-history/`.

## Required testing

Behavior changes require focused unit fixtures plus relevant end-to-end fixture coverage.
Maintain the ten trust/integration/correctness cases in `SPEC.md`, the issue `#486`
acceptance fixture, issue `#488` semantic contract, duplicate-basename/hash-seed replay,
broad-versus-focused context, suite-row mutation, plan-based recomputation, archive
recursion, secret exclusion, root-path, and report consistency tests.

An unrelated plausible common-test failure may be rerun once in isolation with both logs
preserved. Never retry issue-contract or reference assertions to improve correctness.

## Token discipline and Git

Use static checks, fixtures, and targeted diagnostics before child runs. Abort an incapable
suite early, preserve evidence, repair the root cause, and validate narrowly. Do not add
issues, variants, or repetitions for reassurance. Wait out model rate limits and launch no
new arms while the relevant limit is active.

Never create merge commits and never force-push. Fetch and rebase instead of merging when a
remote branch advances. Stage only intended files, run relevant checks, inspect the full
diff, and verify each pushed commit exists remotely. For the reconstruction task, preserve
the required two commits: documentation first, implementation second.

## Release readiness

Keep README commands accurate, links valid, GitHub metadata truthful, the repository
private until the owner publishes it, and existing license/security/contribution files
coherent. Document external blockers rather than fabricating compliance. No generated
benchmark evidence or secret may enter a release artifact.
# Automatic specification and agent-guidance upkeep

Treat every user prompt as potential requirements evidence. Before implementing a request, determine
whether it changes intended benchmark behavior, data contracts, scoring, commands, configuration,
safety controls, repository workflow, or future-agent instructions. Do not wait for the user to ask
for documentation separately.

- If a prompt changes intended product behavior or introduces, removes, clarifies, or supersedes a
  requirement, update `SPEC.md` in the same change as the implementation. Use normative language,
  preserve stable requirement IDs where possible, add acceptance criteria and edge cases, and record
  explicit supersession rather than silently deleting history.
- If a prompt changes how coding agents should work in this repository, update this `AGENTS.md` in the
  same change. Keep instructions repository-specific, executable, and consistent with `SPEC.md`.
- If a prompt changes both behavior and agent workflow, update both files. Neither code nor tests are
  complete until their governing documentation agrees.
- Update `docs/prompt-history-traceability.md` when new prompt evidence materially changes or
  supersedes a requirement. Add stable source-unit IDs, classification, destination, implementation
  evidence, and verification status; never rewrite prior source evidence as though it never existed.
- Update `docs/spec-compliance.md`, schemas, fixtures, README, and scoring documentation whenever the
  affected requirement is represented there. Machine-readable outputs and narrative documentation
  MUST use the same terminology and semantics.
- Purely transient questions, status requests, typo corrections, and one-time operations do not need
  new normative specification text unless they reveal a durable requirement. Record the distinction
  when it could otherwise be ambiguous.
- A later user instruction supersedes an earlier conflicting instruction. Preserve the conflict and
  resolution in traceability, then update the active specification and agent guidance to the surviving
  rule.
- Before finishing any task, perform a documentation-drift check: compare the requested behavior, the
  implementation, tests, `SPEC.md`, and this file. Fix discovered drift as part of the task rather than
  merely reporting it.
