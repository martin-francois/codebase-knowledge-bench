# Publication supplement implementation audit

The publication supplement is source-controlled and reproducible from one immutable published archive.

| Classification | Path | Purpose | Source-control decision |
| --- | --- | --- | --- |
| Source | `scripts/publication_supplement.py` | Verifies the published ZIP, derives archive-bound tables and reports, packages retry proofs, and writes detached supplement artifacts. | Track. It is required to reproduce and independently review publication. |
| Test | `tests/test_publication_supplement.py` | Rejects stale-run values, mislabeled arithmetic effects, missing support, incomplete retry evidence, stale manifest counts, and omitted sensitivity. | Track. It protects publication semantics. |
| Schema | `schemas/publication-supplement-validation.schema.json` | Defines the detached supplement validation receipt. | Track. It makes validation machine-readable. |

Generated supplement directories and ZIPs stay under the external output root and are not source. `.gitignore` excludes `*-bundle.zip` and runtime output roots. A clean checkout can reproduce the supplement when supplied the immutable published archive, detached receipt, and seven content-addressed retry artifacts. `verification/current-canonical-verification-report.json` records the generator source commit and both archive identities. `PUB-022` and `PUB-023` fail release readiness if these source files are untracked or task-related changes remain.

The generator performs no model, Codex, qualification, canary, or child-run operation. Its imports and tests are publication-only, and `PUB-025` protects that boundary.
