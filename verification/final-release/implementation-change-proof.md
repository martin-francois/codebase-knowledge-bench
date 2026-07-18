# Final release implementation-change proof

Task: `final-release-compliance-enforcement`

The stale delivery was bound to commit
`1f8fd577a3f598bfcf388f9a61a9c2cf6ca1ef09`, tree
`d8b64130719654b81dae98be216ccabfbe809707`, and outer SHA-256
`4013fe18f4f42cc3c3e87e0c6945e57fd5f6a28023f4099597d33b790358c6f5`.
This remediation contains substantive tracked implementation and regression-test changes; it is not
a packaging-only delivery.

The static verifier is implemented in source-controlled C, distributed as a prebuilt static ELF,
and rebuilt by a checked-in equality/inspection proof. Its source SHA-256 is
`26182bcaf1878b84d3176dab0f376a5c1bbc619e907d3069fcbfb07dc903f99f`;
its binary SHA-256 is
`ca881726e72a752dffd1262456c26e1ce3f6d2503d97291e41f6e3738f5b9814`.
The outer and inner validators bind the shell, C source, binary, and checksum to source bytes.

Python support is exactly `>=3.14,<3.15`. The single source-only workflow invokes
`scripts/source_only_ci.py`, which receipts the frozen Python and Node checks. Production-shadow
source tests build a temporary Git repository from `fixtures/source-only-target`, inject protected
command execution, and retain the production preflight, protected-verifier, derivation, schema,
report, and fault-injection primitives. Artifact mode remains the real Maven, Bubblewrap,
namespace, mutation, dashboard/browser, and exact-replay qualification stratum.

Bubblewrap command construction accepts an injected executable path for unit tests. Production
resolution remains the artifact-backed path. The sequential lock test uses an explicit child
waiting notification through a pipe and repeats the acquisition race ten times without sleeps.

Machine receipts now distinguish `host_userspace_distribution`, `host_userspace_glibc`,
`host_kernel`, `packaged_bootstrap_glibc`, and `packaged_replay_rootfs_glibc`. The static sanitizer
captures host userspace identity before packaged Python starts; replay records its pivoted rootfs
glibc independently by executing the packaged libc itself, without requiring `ldd`.

The final commit and tree cannot be embedded in a tracked file without changing that identity.
They are bound after commit by the exact-final outer manifest, detached independent-validation
receipt, portability matrix, split manifest, and final response.
