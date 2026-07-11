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
- These controls do not prove hard network denial. The Codex API connection remains available,
  and arbitrary network-capable code may still connect. Runs therefore record
  `network_disabled=false` and medium anti-leak confidence unless OS-level denial is
  independently enforced and recorded.
