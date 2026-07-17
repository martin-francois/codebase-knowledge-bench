# Cross-environment replay fault matrix

Status: **passed**.

| Fault | Boundary | Expected | Observed | Status |
|---|---|---|---|---|
| `global_packaged_ld_library_path_contaminates_host_awk` | outer bootstrap | `rejected` | `rejected` | **passed** |
| `global_packaged_ld_library_path_contaminates_host_sha256sum` | outer bootstrap | `rejected` | `rejected` | **passed** |
| `host_generic_tool_hashes_differ` | packaged semantic runtime | `accepted_without_host_identity_use` | `accepted_without_host_identity_use` | **passed** |
| `packaged_semantic_tool_hash_differs` | packaged semantic runtime | `rejected` | `rejected` | **passed** |
| `packaged_semantic_tool_missing` | packaged semantic runtime | `rejected` | `rejected` | **passed** |
| `rootless_namespace_unavailable` | namespace capability contract | `rejected` | `rejected` | **passed** |
| `privileged_capability_unavailable` | namespace capability contract | `rejected` | `rejected` | **passed** |
| `external_route_present` | measured network receipt | `rejected` | `rejected` | **passed** |
| `dns_unexpectedly_succeeds` | measured network receipt | `rejected` | `rejected` | **passed** |
| `candidate_outer_receipt` | detached final receipt binding | `rejected` | `rejected` | **passed** |
| `failure_evidence_deleted` | independent verifier failure packaging | `rejected` | `rejected` | **passed** |
| `source_generated_verifier_differs` | package source equality | `rejected` | `rejected` | **passed** |
| `source_generated_replay_differs` | package source equality | `rejected` | `rejected` | **passed** |

Cases: `13`.

The host-generic-hash variation is expected to be accepted without consulting host identity. Every corrupt, missing, capability, network, receipt, evidence-deletion, or generated-byte fault is expected to be rejected.
