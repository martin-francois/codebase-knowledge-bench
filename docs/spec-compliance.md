# Current specification compliance

The normative source is [SPEC.md](../SPEC.md). This table maps its current requirement groups to the
sole live implementation and deterministic evidence. Detailed check records are produced by
`scripts/verification_registry.py validate` and the no-model production qualification.

| Requirements | Implementation | Required evidence |
| --- | --- | --- |
| `IDN-*` | project metadata, README, runtime defaults, schemas | repository identity consistency test |
| `PUR-*`, `SCP-*`, `MOD-*` | `SPEC.md`, current schemas | private pre-release cleanup and strict-schema checks |
| `LAY-*`, `CFG-*` | `scripts/benchmark_config.py`, current TOML files | old-field rejection and source/output separation |
| `CON-*` | current contracts and contract schema | exact selector ownership and declared expectation checks |
| `CHN-*`, `PRC-*` | current channel plans and `scripts/protected_verifier.py` | source hashes, overlap audit, JUnit coverage, process truth table |
| `PRE-*` | `scripts/current_preflight.py` | actual base/reference artifacts for issues 486, 488, and 498 |
| `SCR-*` | `scripts/requirement_evidence.py`, `scripts/current_methodology.py` | common skip truth table and requirement fault injections |
| `ROW-*`, `TOK-*` | `scripts/current_pipeline.py`, provenance registry | complete field comparison and token/correctness tamper rejection |
| `RPT-*` | suite loader, reports, dashboard, presentation terminology | strict suite schema, aggregate tamper, terminology, browser, and accessibility checks |
| `MUT-*` | `scripts/mutation_calibration.py` | actual protected execution and collateral-regression classification |
| `QUA-*` | `scripts/methodology_fixture.py` | no-model future-path production qualification and fault matrix |
| `RPL-*` | target bundle/replay builder | exact commit/tree validation and offline Maven replay receipt |
| `ISO-*`, `LIF-*` | runner isolation, progress, retry, and resume code | focused negative fixtures and content-addressed receipts |
| `VER-*` | verification registry and semantic self-review | callable positive/negative records with durations |
| `PUB-*`, `RDY-*` | handoff/delivery validators and readiness builder | clean pushed source, reconstruction, manifests, extracted validation |

Compliance is not asserted by this document alone. `GO` is emitted only when the final readiness
record binds every required result to the final source commit and validated delivery.
