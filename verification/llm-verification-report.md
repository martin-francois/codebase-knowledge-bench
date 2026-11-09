# Semantic maintenance self-review

Overall: **passed** for setting Serena up for the configured target language (SPEC `CFG-016`).

- `LLM-001` preflight contract fidelity: **not applicable**
- `LLM-002` base/reference outcome plausibility: **not applicable**
- `LLM-003` skip-policy appropriateness: **not applicable**
- `LLM-004` process-validity semantics: **passed**
- `LLM-005` field-provenance honesty: **passed**
- `LLM-006` replay-package completeness: **not applicable**

A new `target_language` setting, `java` by default, picks the language Serena's project is created for, and setup fails when indexing didn't install that language's server. Contracts, protected channels, the skip policy and replay packaging are unchanged.
