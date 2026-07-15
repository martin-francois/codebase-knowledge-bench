# Pre-cleanup independent findings

All ten uploaded findings were reproduced against commit `6631618f961a8f44b5a4743e0c378177f986a34b`.

| ID | Reproduced | Evidence | Final disposition |
| --- | --- | --- | --- |
| AUD-001 | yes | Prior handoff SHA-256 `5e87b575...cf3b4` | Superseded by a newly validated handoff. |
| AUD-002 | yes | 224 manifest entries | Dynamic recount in new validator. |
| AUD-003 | yes | Root `00bbd92d...6725` | New root independently recomputed. |
| AUD-004 | yes | 97 automated, 15 manual, 1 external, 113 total | Reports preserve kind counts. |
| AUD-005 | yes | Live code added reasoning to output | Current formula replaces it; recovery derivative deleted. |
| AUD-006 | yes | Mutants only flipped case booleans | Renamed scorer simulations; no execution claim. |
| AUD-007 | yes | Pipeline stages used `is_file()` | Replaced by behavioral fixture; mutation gate remains unavailable. |
| AUD-008 | yes | Reconstructed tree `ab0ec94...` differs from `69729ca...` | Raw Git tar and exact tree reconstruction required. |
| AUD-009 | yes | Source ZIP modes all `100644` | Raw Git modes and symlinks preserved. |
| AUD-010 | yes | Scan excluded source and response | All textual handoff members scanned. |
