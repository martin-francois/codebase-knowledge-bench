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
