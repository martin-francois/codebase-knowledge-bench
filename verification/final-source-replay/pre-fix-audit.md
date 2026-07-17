# Final source replay pre-fix audit

Status: `findings_reproduced`

This audit was completed against clean source commit
`4c1dc4023e71634ccb9884603dcadcf293945cf9` before any source edit. The
mandatory external task receipt already existed. The previous outer delivery was
`8d8419a558092bf45e7ec9f18c28d6cb1fa591a21642b218a5482a6b52f26d7d`
(460,502,234 bytes), and its inner review ZIP was
`72caafad2e7f5537de85edd2ce866dec2d27e54af070afb71a5e27dda312459e`
(460,494,944 bytes).

## REPLAY-001 — source generator differs from packaged replay

- Command: generate `target_replay._replay_script()` and run a byte comparison and
  unified diff against the previous `target/replay.sh`.
- Source: `scripts/target_replay.py:85-157,263-265`.
- Observed: generated SHA-256
  `bec2bc41d77558c212e84b10b2c50237547083a14a80b107fe3207d5ecf5216b`
  (3,386 bytes) did not equal packaged SHA-256
  `9a0f261462a9616630edbca48657e9e1421ed5aafbe58a3502048a5deb08ee6a`
  (3,594 bytes).
- Why it matters: the reviewed executable is not regenerable from committed
  source.
- Planned correction: keep one source generator and fail packaging on any byte
  drift.
- Focused negative fixture: mutate one packaged replay byte and require the
  generator-equality gate to reject it.

The reproduced unified diff was:

```diff
--- source-generated/replay.sh
+++ previous-delivery/target/replay.sh
@@ -4,10 +4,14 @@
 TARGET_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
 HANDOFF_ROOT=$(CDPATH= cd -- "$TARGET_DIR/.." && pwd)
 WORK_ROOT=${1:-"$TARGET_DIR/replay-work"}
+MODE=${2:-"--full"}
+if [[ "$MODE" != "--finalize-existing" ]]; then
 rm -rf "$WORK_ROOT"
 mkdir -p "$WORK_ROOT/benchmark" "$WORK_ROOT/home"

 tar -xf "$HANDOFF_ROOT/source/source.tar" -C "$WORK_ROOT/benchmark"
+git init --quiet "$WORK_ROOT/benchmark"
+git -C "$WORK_ROOT/benchmark" add --all
 git init --quiet "$WORK_ROOT/target"
 git -C "$WORK_ROOT/target" fetch --quiet "$TARGET_DIR/target-repository.bundle"   '+refs/replay/*:refs/replay/*'
 tar --zstd -xf "$TARGET_DIR/maven-repository.tar.zst" -C "$WORK_ROOT"
@@ -15,17 +19,19 @@
 tar --zstd -xf "$TARGET_DIR/python-environment.tar.zst" -C "$WORK_ROOT/benchmark"
 tar --zstd -xf "$TARGET_DIR/dashboard-node-modules.tar.zst" -C "$WORK_ROOT/benchmark/dashboard"
 ln -sfn "$WORK_ROOT/python-runtime/bin/python3.14" "$WORK_ROOT/benchmark/.venv/bin/python"
+fi

 export MAVEN_USER_HOME="$WORK_ROOT/maven-home"
 export MAVEN_OPTS="-Dmaven.repo.local=$WORK_ROOT/maven-home/.m2/repository"
 export HOME="$WORK_ROOT/home"
-ln -s "$WORK_ROOT/maven-home/.m2" "$HOME/.m2"
+ln -sfn "$WORK_ROOT/maven-home/.m2" "$HOME/.m2"
 export BENCH_MAVEN_OFFLINE=true
 export BENCH_TARGET_REPO_PATH="$WORK_ROOT/target"
 export BENCH_CURRENT_PREFLIGHT_CACHE_ROOT="$WORK_ROOT/preflight"
 PYTHON="$WORK_ROOT/benchmark/.venv/bin/python"
 cd "$WORK_ROOT/benchmark"

+if [[ "$MODE" != "--finalize-existing" ]]; then
 for issue in issue-486 issue-488 issue-498; do
@@ -35,6 +41,7 @@
 $PYTHON scripts/mutation_calibration.py ...
 $PYTHON scripts/methodology_fixture.py ... --build-browser
+fi

 $PYTHON - "$WORK_ROOT" <<'PY'
@@ -51,7 +58,6 @@
 }
-(root / 'replay-result.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '
-')
+(root / 'replay-result.json').write_text(json.dumps(result, indent=2, sort_keys=True) + chr(10))
 raise SystemExit(0 if result['status'] == 'passed' else 1)
 PY
```

## REPLAY-002 — source-generated receipt code is invalid

- Command: extract the Python here-document and call `compile(..., "exec")`.
- Source: `scripts/target_replay.py:139-156`, especially line 154.
- Observed: the generated body split the quoted newline across lines 15 and 16
  and raised `SyntaxError: unterminated string literal`.
- Why it matters: the source replay cannot write its result or qualify.
- Planned correction: generate valid code and compile every embedded Python body
  during generation and validation.
- Focused negative fixture: inject the split literal and require syntax validation
  to reject it.

## REPLAY-003 — packaged replay uses post-generation behavior

- Command: search source-generated and packaged text for the five known changes.
- Source: `scripts/target_replay.py:85-157,263-280`.
- Observed: `--finalize-existing`, benchmark `git init/add`, the idempotent
  symlink, the `chr(10)` receipt fix, and conditional stage execution were all
  present only in the package.
- Why it matters: the prior apparent qualification depended on manual
  post-generation behavior.
- Planned correction: remove qualifying finalization and make every qualifying
  artifact generator-owned.
- Focused negative fixture: add any packaged-only token and require provenance
  and equality checks to fail.

## REPLAY-004 — replay fails without host JDK 25

- Command: run the packaged replay from a fresh empty root with `env -i`,
  Temurin 21.0.7 as the only Java on `PATH`, and an explicit JDK 21
  `JAVA_HOME`.
- Source: `scripts/target_replay.py:95-112,235-261`; target `.sdkmanrc:1`;
  target `pom.xml:12-13`.
- Observed: exit code 1. The JVM rejected
  `--sun-misc-unsafe-memory-access=allow`, created zero JUnit cases, and made the
  protected process invalid. The target declares `java=25.0.3-zulu` and Maven
  release 25. Resolved Java/Javac were Temurin 21 with SHA-256 values
  `217126ab9708f797fb5cee09228392055d3bd9213147f83ff4987addb6f92494`
  and
  `b9c73d2642a9a1bcf436e58f96738c80649cf984d57e98731bf9eaf6e5806227`.
- Why it matters: the verifier described by the task has no usable Java runtime.
- Planned correction: package, lock, explicitly select, and preflight-hash the
  exact JDK.
- Focused negative fixture: expose only incompatible host Java and require the
  locked packaged Java to be used.

## REPLAY-005 — host Node and Chromium are used

- Command: resolve and hash `node`, `npm`, and `chromium`; list target members;
  inspect packaged runtime archives for those binaries.
- Source: `scripts/target_replay.py:256-261`,
  `scripts/dashboard.py:232-237,278-298`, and
  `dashboard/playwright.config.ts:8-10`.
- Observed: Node v22.22.0 resolved to `/usr/bin/node`
  (`1bec56ef...`), npm 10.9.4 to the host npm CLI (`8e5f6f34...`), and Chromium
  150.0.7871.114 to `/usr/bin/chromium` (`c008d3aa...`). No JDK, Node,
  Chromium, or runtime-lock member existed in the target package.
- Why it matters: dashboard semantics depend on unpinned host programs.
- Planned correction: package and lock the exact Node/npm and Chromium closures
  and set explicit paths.
- Focused negative fixture: hide or replace host binaries and require only
  packaged locked paths.

## REPLAY-006 — network status is hard-coded

- Command: search the generator, packaged replay, config, and result for network
  claims and isolation/probe code.
- Source: `scripts/target_replay.py:139-155,266-278`.
- Observed: `network_enabled=false` was written in source and package, while no
  namespace launcher or connectivity receipt existed.
- Why it matters: the delivery claims offline behavior without enforcement or
  measurement.
- Planned correction: enforce a network namespace, measure routes/DNS/external
  and loopback probes, and derive the field from the receipt.
- Focused negative fixture: add an external route or failed probe and require
  readiness to fail.

## REPLAY-007 — replay evidence is not packaged

- Command: compare required replay evidence paths with the target directory and
  previous review ZIP member list.
- Source: `scripts/target_replay.py:139-155,378-404`.
- Observed: no `replay/` member existed. Stdout, stderr, command log, runtime
  lock, network receipt, replayed preflight, mutation, and production-shadow
  artifacts and hashes were absent.
- Why it matters: broad stored pass strings cannot be traced to commands or
  artifacts.
- Planned correction: package an exact replay evidence manifest and derive every
  stage status and hash from preserved files.
- Focused negative fixture: remove each required evidence class independently
  and require validation to fail.

## ARCHIVE-001 — extra member is accepted

- Command: archive an expected file and an unmanifested extra file, manifest only
  the expected file, and call `_validate_archive()`.
- Source: `scripts/target_replay.py:285-311`.
- Observed: validation returned `{"status":"passed","errors":[]}`.
- Why it matters: unreviewed bytes can be included in a validated runtime.
- Planned correction: exact member/type manifests and a fail-closed safe
  extractor.
- Focused negative fixture: the same one-extra-file archive must be rejected.

## ARCHIVE-002 — escaping symlink is accepted

- Command: archive the expected file plus
  `payload/escaping-link -> ../../outside`, omit the link from the manifest, and
  call `_validate_archive()`.
- Source: `scripts/target_replay.py:285-311`.
- Observed: validation again returned `passed` with no errors.
- Why it matters: direct extraction processes a hostile member that validation
  ignores.
- Planned correction: reject unexpected paths and escaping link targets before
  extraction.
- Focused negative fixture: the same escaping-link archive must be rejected
  without side effects.

## PREFLIGHT-STATUS-001 — skipped requested base is accepted

- Command: change one requested selector from `base_status=failed` to
  `base_status=skipped`, retain `base_passed=false`, and call
  `validate_current_preflight()`.
- Source: `scripts/current_preflight.py:171-196,269-340`.
- Observed: validation accepted the mutated artifact.
- Why it matters: a skipped selector can masquerade as the required base
  behavior failure.
- Planned correction: exact statuses become authoritative and Boolean values are
  derived and cross-checked.
- Focused negative fixture: requested base skipped with false Boolean must fail.

## PREFLIGHT-STATUS-002 — errored requested base is accepted

- Command: repeat the previous mutation with `base_status=error`.
- Source: `scripts/current_preflight.py:171-196,269-340`.
- Observed: validation accepted the mutated artifact.
- Why it matters: an infrastructure error can masquerade as behavioral
  discrimination.
- Planned correction: reject error/skip for all contract selectors and validate
  the exact declared status.
- Focused negative fixture: requested base error with false Boolean must fail.
