# Token accounting v2

`token-accounting-current` treats `reasoning_output_tokens` as a subset of `output_tokens_including_reasoning`. Weighted load is observed non-cached input plus the selected cached-read weight plus output including reasoning. Reasoning is never added or priced again.

The published archive remains immutable and retains `legacy_modeled_weighted_token_load_v1_reasoning_double_counted`. The archive-bound erratum recomputes all 63 rows from raw usage fields and reports paired geometric effects and winner/frontier sensitivity without replacing historical results.

Cache writes remain null when Codex does not report them. Pricing is unavailable unless cache-write telemetry and every pinned price are complete. Turn aggregates cannot identify within-request or cross-run reuse. Tool, repetition, position, and gap correlations are descriptive, not causal. Thirty minutes is a minimum cache-eligibility lifetime, not an eviction guarantee.

Natural operational caching remains primary. An officially supported `prompt_cache_key` may be used only in a separately qualified sensitivity stratum. Such keys can affect routing, must respect documented per-key traffic guidance, and must never be pooled with natural-cache effects.
