# Final source-only CI browser pre-fix audit

Status: `findings_reproduced`

Task: `final-source-only-ci-browser-and-image-pin`

Routing nonce: `FMCB-20260719-9D4E2A7B`

This audit was completed before source edits against clean commit
`86e1658f48539a8cd3e737d740f498ee649d214c`, tree
`45d7e4d793c04d7d8e76e0a3ae3db7fafdc9a84e`, with fetched
`origin/main` at the same commit. The external task receipt has SHA-256
`e80f4046bc067f94fb0c6f256d7a865b8279e33b2de9bf4c809f70b2f2f9f88f`.
No methodology change is authorized.

## NARROW-001 — browser absent from source-only command plan

Importing and serializing `scripts/source_only_ci.py:command_plan()` produced
SHA-256
`ea282028804d7d94714972304208e094d4d22c80712f7fa67a8549d4142a4703`.
The dashboard portion runs:

```text
npm ci --prefix dashboard
npm audit --prefix dashboard --package-lock-only
npm test --prefix dashboard -- --run
npm run build --prefix dashboard
```

It has neither a `dashboard_browser` row nor
`npm run test:browser --prefix dashboard`.

Focused regression:
`SourceOnlyCommandPlanTest.test_real_browser_spec_is_required`.

## NARROW-002 — no pinned source-only userspace

`.github/workflows/ci.yml` has SHA-256
`6d6616a8e510049aba0beea3131e7dadcd0d95aff5a23ad9a41d1a82ca81f7f2`.
The source-only job uses mutable `runs-on: ubuntu-latest`, has no job container,
and records no full OCI image digest.

Focused regression:
`PinnedUserspaceWorkflowTest.test_workflow_and_runner_use_the_same_digest_pinned_image`.

## NARROW-003 — no source-only Chromium identity

`scripts/source_only_ci.py` has SHA-256
`c34c2484976acddb806e6e7a97ef61e37107edee2049f17cb3c384143192012a`.
Its receipt omits the userspace image, image digest, Chromium executable,
Chromium version, Chromium executable SHA-256, and browser test count.

Focused regression:
`SourceOnlyReceiptTest.test_receipt_requires_userspace_runtime_and_browser_identity`.

## NARROW-004 — stale-task packaging is not rejected

At source commit `86e1658f48539a8cd3e737d740f498ee649d214c`,
`scripts/final_source_replay.py:validate_task_receipt()` accepted
`task_id=final-source-reproducible-offline-replay` with exit code zero. The
current delivery code has no explicit descriptor that binds the expected narrow
task, routing nonce, browser result, userspace digest, and new source identity.

Focused regression:
`ReleaseDescriptorGuardTest.test_old_task_and_stale_source_are_rejected`.

## NARROW-005 — Debian 13 old image identity is invalid

The old receipt records:

```text
sha256:95416cae1a21c7c393cd39ee0356a1c17a38aad59eb08b06649147a92623c1ff
```

Live inspection of the retained `ckg-replay-portability:debian13` image repeats
the recovered inspected ID:

```text
sha256:95416caefd1ffd129a991b4b8432862144c9386a64919f93ec14326b0986042c
```

The values differ. The recovered audit has SHA-256
`f653fa960dbc01cc9b6fdf6e9e0afd6e0301d1cf6e4fa56b8e9a6710ec707ece`;
its retained inspect log has SHA-256
`7a13f3d51293a4b0f92468d9821c7369ea171d71f70d3591944fc12ed4554473`.
The old identity cannot qualify.

Focused regression:
`ReleaseDescriptorGuardTest.test_environment_receipt_must_match_inspected_digest`.
