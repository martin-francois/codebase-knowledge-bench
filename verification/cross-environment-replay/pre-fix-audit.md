# Cross-environment replay pre-fix audit

Task: `cross-environment-replay-portability-final`

Base commit: `7954bc49e4f8bcb0b7314171cb679f1b74b05e7e`

Base tree: `56f3404a7cfa60399f05ba7238830ea6f3af8fcd`

Status: **all six required defects reproduced before source remediation**

The mandatory task receipt was created outside Git before this audit. No repository source implementation was edited before these findings were recorded. The reconstructed current outer is 958,010,382 bytes with SHA-256 `68dc0fac10abc632cb4d7b83c39cb19b075b8106f1907ee3ac6ff7373f4376f1`.

## PORT-001 — packaged libraries contaminate host tools

Source ownership:

- `scripts/independent_verifier.sh:192-207` globally exports the packaged bootstrap `LD_LIBRARY_PATH`, then invokes host `sha256sum` and `awk`.
- `scripts/independent_verifier.sh:21-185` also depends on a broad, untested set of host ZIP and text utilities before packaged Python starts.

Exact command:

```bash
docker run --rm --privileged --network none \
  -v "$OUTPUT_ROOT/cross-environment-replay-final/pre-fix/current-delivery/current-outer.zip:/input/current-outer.zip:ro" \
  -v "$OUTPUT_ROOT/cross-environment-replay-final/pre-fix/PORT-001:/evidence" \
  ckg-replay-portability:debian13 \
  bash -c 'unzip -p /input/current-outer.zip independent-verifier.sh >/tmp/independent-verifier.sh && chmod 755 /tmp/independent-verifier.sh && /tmp/independent-verifier.sh /input/current-outer.zip /evidence/result'
```

The Debian 13.6 environment used glibc 2.41, GNU Awk 5.2.1 at `/usr/bin/gawk` (`c90aba59e13752526d9cb767533f3802d3b7cd5dd6355615516d21abbc28b005`), and GNU coreutils 9.7 `sha256sum` (`89f8c1d1ba3c76138f3771e1a91e2796ade6180b1c1e4258c04698ff32787c97`). The image ID was `sha256:95416caefd1ffd129a991b4b8432862144c9386a64919f93ec14326b0986042c`.

Exact stderr and exit:

```text
sha256sum: /tmp/independent-verifier-bootstrap.vewoMi/inner/runtime/bootstrap-python/system-libs/libc.so.6: version `GLIBC_2.38' not found (required by /lib/x86_64-linux-gnu/libcrypto.so.3)
awk: /tmp/independent-verifier-bootstrap.vewoMi/inner/runtime/bootstrap-python/system-libs/libc.so.6: version `GLIBC_2.38' not found (required by /lib/x86_64-linux-gnu/libreadline.so.8)
awk: /tmp/independent-verifier-bootstrap.vewoMi/inner/runtime/bootstrap-python/system-libs/libc.so.6: version `GLIBC_2.38' not found (required by /lib/x86_64-linux-gnu/libgmp.so.10)
*** stack smashing detected ***: terminated
/tmp/independent-verifier.sh: line 1:  8952 Aborted                 (core dumped) INDEPENDENT_VERIFIER_UNZIP_SHA256=$(sha256sum "$INDEPENDENT_VERIFIER_UNZIP_PATH" | awk '{print $1}')
exit code: 134
```

Why it matters: the verifier aborts before the packaged verifier begins. A builder-host pass does not establish an independent replay.

Planned implementation: sanitize `LD_LIBRARY_PATH`, `PYTHONPATH`, `JAVA_HOME`, and `NODE_PATH` before all host commands; retain only a capability-tested POSIX `/bin/sh` and `unzip -p` fixed-member stream boundary; invoke packaged Python with its packaged loader and scoped `--library-path`.

Focused negative fixture: use the exact production launcher and real packaged libraries with hostile incoming library variables on Debian 12 and Debian 13. A mutated launcher that globally exports packaged libraries must be rejected.

Raw evidence is under `output://cross-environment-replay-final/pre-fix/PORT-001/`.

## PORT-002 — the current portability test is inadequate

Source ownership: `tests/test_final_source_replay.py:465-505`.

Exact command:

```bash
uv run python -m unittest -v tests.test_final_source_replay.NetworkNamespaceLauncherTest.test_outer_only_bootstrap_executes_with_system_awk
```

Exact result:

```text
test_outer_only_bootstrap_executes_with_system_awk (...) ... ok
Ran 1 test
OK
exit code: 0
```

The fixture writes `runtime/bootstrap-python/bin/python3.14` as `#!/bin/sh; exit 0` and does not include `runtime/bootstrap-python/system-libs`. It therefore cannot reproduce PORT-001.

Why it matters: a passing structurally fake fixture created a false portability assurance.

Planned implementation: the production-package fixture will contain the real packaged loader and shared libraries and will execute the exact generated launcher.

Focused negative fixture: introduce only the former global `LD_LIBRARY_PATH` export and require deterministic contamination detection on the newer userspace.

Raw evidence is under `output://cross-environment-replay-final/pre-fix/PORT-002/`.

## PORT-003 — unbundled generic tools are exact-hash locked

Source ownership:

- `scripts/target_replay.py:492-514` builds the builder-host lock.
- `scripts/target_replay.py:1399-1421` resolves and exact-hash-compares host PATH executables.

Exact command:

```bash
docker run --rm --network none \
  -v "$CURRENT_PACKAGE:/package:ro" \
  -v "$OUTPUT_ROOT/cross-environment-replay-final/pre-fix/PORT-003:/audit:ro" \
  ckg-replay-portability:debian13 \
  /package/runtime/bootstrap-python/system-libs/ld-linux-x86-64.so.2 \
  --library-path /package/runtime/bootstrap-python/system-libs \
  /package/runtime/bootstrap-python/bin/python3.14 \
  /audit/run-generic-resolution.py /package/runtime/runtime-lock.json
```

Exact result: `_generic_runtime_resolution(runtime_lock)` returned all eight mismatches:

```text
bash    223fe8564b60636bc738b6178d3ba9a50ef7d791266b0efae6363bb716e4c47f
git     356db14e102d68a1a37d8a1ac577dfd678d45d46e92f468bef8b7154e7bfdc60
ip      024784c6d16b183dc7c97d93a943a9270976f167f95f771e14d72442dc657f0c
mount   92253cf919646b4a0242998526d35eea39204dff0363e74086290d63ccb39fed
tar     38adf50825f773d353d6bda1a7cd70c22f8877823e44821c1d2bd0a52ddc3871
unshare d82900dfd64b5dd01493d206236575623c2dcf306c466dbe127e171c18cb4614
unzip   0e8d7c498e9143e0e2529e6a76d5785d53755ef04ee6f9286088ea645621da73
zstd    3eb7744cf8fd1ecd46d617098ee870b3e7911fe570ffaba662c1c5b7d6456020
exit code: 0 (diagnostic helper completed; returned errors contain the failure)
```

Package inventory returned `NOT_SUPPLIED` for bash, git, ip, mount, tar, unshare, unzip, and zstd.

Why it matters: exact replay identity depends on bytes that the package does not provide, making cross-distro replay impossible by construction.

Planned implementation: one content-addressed replay rootfs will provide every generic semantic utility, loader, and transitive library. Host path/hash comparison will be removed; packaged bytes will use exact-identity validation.

Focused negative fixtures: different host hashes must not affect replay; a missing packaged semantic tool and a one-byte packaged tool mutation must each fail.

Raw evidence is under `output://cross-environment-replay-final/pre-fix/PORT-003/`.

## PORT-004 — namespace capability is implicit

Source ownership:

- `scripts/target_replay.py:150-245` invokes host namespace and network tools without a declared user mapping or privilege mode.
- `SPEC.md:343-347,377-382` omits effective UID, capabilities, user namespace settings, and supported rootless/privileged modes.

The current command under a normal container capability set failed:

```text
effective uid: 0
CapEff: 00000000a80425fb
CAP_SYS_ADMIN: absent
CAP_NET_ADMIN: absent
unprivileged_userns_clone: 1
max_user_namespaces: 61428
unshare: unshare failed: Operation not permitted
exit code: 1
```

A rootless user/network/mount probe with `--user --map-root-user --net --mount --propagation unchanged` succeeded with UID/GID map `0 65534 1`, a new user/network/mount namespace, a successful tmpfs mount, and loopback-only interfaces/routes. The explicit privileged probe also succeeded. The current full launcher is not equivalent to the successful reduced rootless probe.

Why it matters: the package silently depends on a capability configuration that many containers and hosts do not provide.

Planned implementation: classify kernel capabilities separately, ship one exact namespace launcher strategy, test UID/GID mapping and mount/network/PID operations before replay, and record namespace, capability, mount, and network receipts. Any privileged mode will be explicit and capability-gated.

Focused negative fixtures: rootless user namespaces unavailable and privileged capabilities unavailable must each fail early with a boundary-specific retained receipt.

Raw evidence is under `output://cross-environment-replay-final/pre-fix/PORT-004/`.

## PORT-005 — receipt is not bound to the final outer

Source ownership:

- `scripts/final_source_replay.py:133-152,253-285` creates and verifies a candidate.
- `scripts/final_source_replay.py:336-355` embeds that receipt before rebuilding the inner and final outer.
- `scripts/external_review_delivery.py` validates an inner sidecar but does not emit a detached final-outer receipt.

Exact comparison:

```text
packaged receipt outer:
  candidate-outer-7954bc4.zip
  958001461 bytes
  d1c5ed2e2a8f38d984c088f4d23cdf10acf4b3fca8db374e39359cf100ab0fe4
actual final outer:
  current-outer.zip
  958010382 bytes
  68dc0fac10abc632cb4d7b83c39cb19b075b8106f1907ee3ac6ff7373f4376f1
packaged receipt inner manifest:
  count 4609
  root 6c8be377aace8833a57eb4d7a32e919fd17fead3b16cc21e0a2a1f0c701b1358
actual final inner manifest:
  count 4613
  root 1963409a4e17491ec2a7badba339bebd2eae93a662e707c53710f6c0471c6490
outer identity matches: false
inner manifest identity matches: false
```

Why it matters: a receipt for earlier bytes does not authenticate what the reviewer receives.

Planned implementation: build the immutable final outer, then run that outer’s own verifier against those exact bytes in every userspace. The authoritative SHA-256, validation receipt, and portability matrix will remain detached.

Focused negative fixture: a candidate outer identity or stale inner manifest in a detached receipt must be rejected by the exact release validator.

Raw evidence is under `output://cross-environment-replay-final/pre-fix/PORT-005/`.

## PORT-006 — failed replay evidence is deleted

Source ownership:

- `scripts/independent_verifier.py:616-710` creates and then unconditionally removes `fresh-work` and `replay`.
- `scripts/independent_verifier.py:711-794` writes the failure receipt only afterward.

The exact current outer was run in the Debian 12 audit image with `unshare` deterministically removed:

```text
fresh offline replay failed with exit code 127
verifier exit code: 1
replay exit code: 127
fresh-work after failure: absent
replay after failure: absent
remaining: command-log.json, independent-verifier-receipt.json, stderr.log, stdout.log
```

Why it matters: only a generic exit and a list of missing expected artifacts remain. Runtime, namespace, partial-stage, and network diagnostic state is destroyed.

Planned implementation: retain failure logs and partial replay evidence, record the last completed stage, create a failure receipt and content-addressed partial evidence manifest, and prune any large worktree only after its diagnostic manifest is durable.

Focused negative fixture: remove one packaged semantic tool after namespace entry and require the failure receipt, command log, stdout/stderr, last stage, partial evidence manifest, and any produced runtime/network receipts to remain.

Raw evidence is under `output://cross-environment-replay-final/pre-fix/PORT-006/`.

## Audit conclusion

The prior GO is not portable. These are repository-fixable deterministic boundary defects, so remediation proceeds rather than returning an intermediate `NO_GO`.

No expensive or prohibited work was performed:

```text
model calls: 0
Codex implementation children: 0
qualifications: 0
canaries: 0
benchmark matrices: 0
```
