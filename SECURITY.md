# Security

## Reporting issues

If you discover a security issue in the benchmark runner or its automation, please
open a private disclosure with the repository maintainer rather than opening a
public issue.

## Repository-local policy

- Do not commit secrets, tokens, credentials, or `.env` files.
- Do not include raw benchmark artifacts that may contain private comments or internal
  CI metadata.
- Child solve runs use isolated homes, an allowlisted environment, and Bubblewrap filesystem
  and process isolation where available.
- Wrappers placed first on `PATH` block common web and GitHub clients and remote Git
  subcommands. These command-level controls reduce accidental or direct lookup paths.
- The reference profile matches a fresh Codex 0.146.0 trusted-repository Auto session: command
  network is off, cached web search is available for general documentation, and live search is off.
- Human and isolated AI approval deciders apply the same generic capability policy. Exact decisions
  are authenticated, bind redacted text to a digest of capability-relevant original parameters,
  are fsynced before response, and are persisted to an external operator TOML only at a safe
  boundary. Clean-source runs reject a mutable configuration inside either Git worktree.
- Solver and reviewer authentication homes are ephemeral transport state. Normal teardown removes
  them; interruption recovery removes them before archiving and retains only a path-only cleanup
  receipt, never credential bytes.
- These controls do not prove hard network denial. The Codex API connection remains available,
  and arbitrary network-capable code may still connect. Runs therefore record
  `network_disabled=false` and medium anti-leak confidence unless OS-level denial is
  independently enforced and recorded.
