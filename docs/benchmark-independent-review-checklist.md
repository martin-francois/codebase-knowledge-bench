# Independent review checklist

1. Verify the outer ZIP byte count and SHA-256, then extract it safely.
2. Verify the delivery manifest count/root and every member hash.
3. Verify the detached inner checksum and detailed validation sidecar.
4. Extract the inner ZIP and verify its manifest count/root and every member hash.
5. Reconstruct the exact Git tree and commit from `source/` and compare `origin/main` with `HEAD`.
6. Inspect the pre-fix audit and the old-preflight removal scan.
7. For each issue, compare contract selectors with actual preflight selectors, channels, source hashes,
   base/reference outcomes, and process validity.
8. Recompute the common skip and protected-process truth tables.
9. Rederive execution rows from raw JSONL, JUnit, receipts, frozen policy, and preflight artifacts.
10. Rebuild suite rows and aggregates; compare reports and dashboard data with strict schemas.
11. Inspect targeted mutation results for intended kills, neighboring requested behavior, zero common
    skips, regression gates, process validity, and collateral failures.
12. Verify the target Git bundle contains every required commit and exact tree.
13. Verify dependency manifests, run `target/replay.sh` with network disabled, and compare its receipt.
14. Review all nine automated checks and their focused negative fixtures.
15. Treat the six semantic checks as implementing-agent self-review, not independent assurance.
