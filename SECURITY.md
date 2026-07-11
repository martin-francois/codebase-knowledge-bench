# Security

## Reporting issues

If you discover a security issue in the benchmark runner or its automation, please
open a private disclosure with the repository maintainer rather than opening a
public issue.

## Repository-local policy

- Do not commit secrets, tokens, credentials, or `.env` files.
- Do not include raw benchmark artifacts that may contain private comments or internal
  CI metadata.
- The harness intentionally blocks web access for child solve runs and limits external
  lookups to preserve anti-leak behavior.

