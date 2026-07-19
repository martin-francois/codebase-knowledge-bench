# External review handoff

Run `uv run python scripts/build_review_handoff.py` with the immutable canonical and supplement ZIPs,
an output directory, and the final `agent-response.md`. The generator exports every tracked source file,
source identity and subject manifests, allowed report delta, machine and human reports, test evidence,
registry files, and immutable publications. It writes detached SHA-256 and validation receipts.

Validation safely extracts into a fresh directory, checks every path/size/hash and the aggregate root,
reconstructs source identity, verifies immutable evidence hashes, resolves `repo://` and `zip://` evidence
URIs, rejects host-only report paths, and scans report material for credential-shaped values. Dependency
caches, `.git`, build output, and credentials are excluded.

Because a file cannot contain its enclosing ZIP's final SHA-256 without a cryptographic fixed-point
problem, `agent-response.md` names the detached checksum and validation receipt; those detached files are
the authoritative post-construction ZIP identity.

The official outer-package verification command is:

```bash
independent-verifier-bootstrap independent-verifier.sh OUTER_ZIP OUTPUT_ROOT
```

The static bootstrap sanitizes loader and language-runtime environment variables before the shell
starts. Direct execution of `independent-verifier.sh` is not a hostile-environment-safe entrypoint.

The final split packager receives `release-descriptor.json` and `package-origin.json` explicitly.
Every numbered part carries those records, the task receipt, source-only CI/browser receipts,
Debian 12/13 exact-final receipts, portability matrix, detached outer receipt, split index, and
reconstruction script. `scripts/cross_environment_release.py validate-split` verifies the raw and
normalized part identities and the complete task/source/userspace/browser/portability binding after
reconstructing the exact outer.

`reconstruct.sh` is a convenience reconstruction and validation tool. Set
`RECONSTRUCT_PYTHON` to an explicit compatible interpreter when needed; otherwise it resolves
`python3`. It validates that interpreter before use and records its resolved path, version, and
SHA-256 in the reconstruction result. It clears Python, Java, and Node module-path variables but
preserves the selected interpreter's dynamic-loader environment. Hostile-loader verification does
not execute this convenience script directly: it uses the statically linked verifier bootstrap,
the packaged ELF loader, and packaged Python.
