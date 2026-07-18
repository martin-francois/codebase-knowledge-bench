#!/usr/bin/env python3
"""Build and validate exact-final cross-environment release receipts."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
ZERO_SHA256 = "0" * 64
PART_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
DETACHED_PART_FILES = {
    "agent-response.md",
    "final-outer.independent-validation.json",
    "final-outer.portability-matrix.json",
    "final-outer.sha256",
    "independent-verifier-bootstrap",
    "independent-verifier-bootstrap.sha256",
    "reconstruct.sh",
    "source-only-ci-receipt.json",
    "split-delivery-manifest.json",
    "split-index.json",
    "split-index.md",
}
FORBIDDEN_BOOTSTRAP_UTILITIES = (
    "awk",
    "sha256sum",
    "sort",
    "sed",
    "tr",
    "zipinfo",
    "git",
    "tar",
    "zstd",
    "unshare",
    "mount",
    "ip",
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def validate_source_generated_equality(
    source: bytes,
    packaged: bytes,
    *,
    artifact: str,
) -> dict[str, Any]:
    equal = source == packaged
    return {
        "schema_id": "source-generated-equality-current",
        "status": "passed" if equal else "failed",
        "artifact": artifact,
        "source_sha256": sha256_bytes(source),
        "packaged_sha256": sha256_bytes(packaged),
        "byte_equal": equal,
        "errors": (
            [] if equal else [f"source-generated {artifact} differs"]
        ),
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))


def final_outer_identity(path: Path) -> dict[str, Any]:
    return {
        "filename": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def final_inner_identity(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as outer:
        candidates = sorted(
            name
            for name in outer.namelist()
            if name.startswith("review-handoff/")
            and name.endswith(".zip")
        )
        if not candidates:
            return {
                "outer_member": None,
                "filename": None,
                "bytes": 0,
                "sha256": None,
                "manifest_entry_count": 0,
                "manifest_root": None,
                "qualifying_payload_entry_count": 0,
                "qualifying_payload_root": None,
            }
        if len(candidates) != 1:
            raise ValueError("final outer must contain exactly one inner ZIP")
        member = candidates[0]
        inner_bytes = outer.read(member)
    with zipfile.ZipFile(io.BytesIO(inner_bytes)) as inner:
        manifest = json.loads(
            inner.read("review-handoff-manifest.json")
        )
    return {
        "outer_member": member,
        "filename": Path(member).name,
        "bytes": len(inner_bytes),
        "sha256": sha256_bytes(inner_bytes),
        "manifest_entry_count": manifest.get("entry_count"),
        "manifest_root": manifest.get("manifest_root"),
        "qualifying_payload_entry_count": manifest.get(
            "qualifying_payload_entry_count"
        ),
        "qualifying_payload_root": manifest.get(
            "qualifying_payload_root"
        ),
    }


def _command_occurs(source: str, command: str) -> bool:
    pattern = re.compile(
        rf"(?m)^[ \t]*(?:exec[ \t]+)?(?:[^#\n]*[;&|][ \t]*)?"
        rf"{re.escape(command)}(?:[ \t]|$)"
    )
    return bool(pattern.search(source))


def validate_bootstrap_launcher(source: str) -> dict[str, Any]:
    errors: list[str] = []
    sanitized = bool(
        re.search(
            r"(?m)^unset LD_LIBRARY_PATH PYTHONPATH JAVA_HOME NODE_PATH$",
            source,
        )
    )
    if not sanitized:
        errors.append("bootstrap environment is not sanitized")
    global_packaged = bool(
        re.search(
            r"(?m)^[ \t]*(?:export[ \t]+)?LD_LIBRARY_PATH="
            r".*bootstrap-python/system-libs",
            source,
        )
    )
    if global_packaged:
        errors.append("global packaged LD_LIBRARY_PATH")
    loader_invocation = (
        "ld-linux-x86-64.so.2" in source
        and "--library-path" in source
        and "python3.14" in source
    )
    if not loader_invocation:
        errors.append("packaged Python loader invocation is missing")
    forbidden = [
        name
        for name in FORBIDDEN_BOOTSTRAP_UTILITIES
        if _command_occurs(source, name)
    ]
    if forbidden:
        errors.append(
            "forbidden host semantic utilities: " + ", ".join(forbidden)
        )
    fixed_streaming = (
        '"$UNZIP" -p "$OUTER"' in source
        and '"$UNZIP" -p "$INNER"' in source
        and "unzip -Z" not in source
        and "unzip -l" not in source
    )
    if not fixed_streaming:
        errors.append("bootstrap is not fixed exact-name unzip streaming")
    proc_exe_independent = "/proc/$$/exe" not in source
    if not proc_exe_independent:
        errors.append("bootstrap shell depends on /proc/<pid>/exe")
    shell_boundary = source.startswith("#!/bin/sh\n")
    if not shell_boundary:
        errors.append("bootstrap does not use POSIX /bin/sh")
    return {
        "schema_id": "bootstrap-launcher-validation-current",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "environment_sanitized": sanitized,
        "global_packaged_ld_library_path": global_packaged,
        "packaged_loader_invocation": loader_invocation,
        "fixed_exact_name_streaming": fixed_streaming,
        "proc_exe_independent": proc_exe_independent,
        "posix_shell": shell_boundary,
        "forbidden_host_semantic_utilities": forbidden,
    }


def validate_namespace_capability_receipt(
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    mode = receipt.get("mode")
    if receipt.get("status") != "passed":
        errors.append("namespace receipt status is not passed")
    if mode not in {"rootless", "privileged"}:
        errors.append("namespace mode is not explicit")
    for field in (
        "new_mount_namespace",
        "new_network_namespace",
        "new_pid_namespace",
    ):
        if receipt.get(field) is not True:
            errors.append(f"namespace prerequisite failed: {field}")
    mounts = receipt.get("mount_receipt", {})
    for field in ("package", "work", "evidence", "proc", "empty_resolver"):
        if mounts.get(field) is not True:
            errors.append(f"namespace mount failed: {field}")
    capabilities = receipt.get("capability_check", {})
    if mode == "rootless":
        if receipt.get("new_user_namespace") is not True:
            errors.append("rootless user namespace was not created")
        if capabilities.get("rootless_user_namespace") is not True:
            errors.append("rootless user namespace capability unavailable")
        if not str(receipt.get("uid_map", "")).strip().startswith("0 "):
            errors.append("rootless UID map does not map namespace root")
        if not str(receipt.get("gid_map", "")).strip().startswith("0 "):
            errors.append("rootless GID map does not map namespace root")
    if mode == "privileged":
        if capabilities.get("privileged_cap_sys_admin") is not True:
            errors.append("privileged CAP_SYS_ADMIN unavailable")
        if capabilities.get("privileged_cap_net_admin") is not True:
            errors.append("privileged CAP_NET_ADMIN unavailable")
    launcher = receipt.get("launcher_sha256")
    if not isinstance(launcher, str) or not re.fullmatch(
        r"[0-9a-f]{64}", launcher
    ):
        errors.append("namespace launcher identity is missing")
    return {
        "schema_id": "namespace-capability-validation-current",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "mode": mode,
    }


def validate_network_namespace_receipt(
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    tcp = bool(receipt.get("external_tcp_probe", {}).get("succeeded"))
    dns = bool(receipt.get("external_dns_probe", {}).get("succeeded"))
    route = bool(receipt.get("default_external_route_present"))
    derived = tcp or dns or route
    if receipt.get("status") != "passed":
        errors.append("network receipt status is not passed")
    if receipt.get("new_namespace") is not True:
        errors.append("network namespace was not changed")
    if route:
        errors.append("external default route is present")
    if tcp:
        errors.append("external TCP unexpectedly succeeded")
    if dns:
        errors.append("external DNS unexpectedly succeeded")
    if receipt.get("loopback_probe", {}).get("succeeded") is not True:
        errors.append("loopback listener/connect failed")
    if (
        receipt.get("dns_configuration", {}).get("host_dns_used")
        is not False
    ):
        errors.append("resolver is not isolated")
    if receipt.get("network_enabled") is not derived or derived:
        errors.append("network_enabled derivation is not isolated")
    return {
        "schema_id": "network-namespace-validation-current",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "derived_network_enabled": derived,
    }


def validate_failure_preservation(output: Path) -> dict[str, Any]:
    required = {
        "failure-receipt.json",
        "command-log.json",
        "stdout.log",
        "stderr.log",
        "partial-evidence-manifest.json",
        "last-completed-stage.json",
    }
    present = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file()
    }
    missing = sorted(required - present)
    replay_retained = (output / "replay").is_dir()
    fresh_work_retained = (output / "fresh-work").is_dir()
    fresh_work_manifest = (
        output / "fresh-work-manifest.json"
    ).is_file()
    errors = [f"failure evidence missing: {name}" for name in missing]
    if not replay_retained:
        errors.append("partial replay evidence directory was deleted")
    if not fresh_work_retained and not fresh_work_manifest:
        errors.append(
            "fresh work and its diagnostic manifest were both deleted"
        )
    return {
        "schema_id": "failure-preservation-validation-current",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "required": sorted(required),
        "present": sorted(present),
        "replay_evidence_retained": replay_retained,
        "fresh_work_retained": fresh_work_retained,
        "fresh_work_manifest_retained": fresh_work_manifest,
    }


def validate_detached_final_binding(
    outer: Path,
    validation: Mapping[str, Any],
    portability_matrix: Mapping[str, Any],
) -> dict[str, Any]:
    actual = final_outer_identity(outer)
    try:
        actual_inner = final_inner_identity(outer)
    except (
        KeyError,
        ValueError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
    ) as exc:
        actual_inner = None
        inner_error = str(exc)
    else:
        inner_error = None
    errors: list[str] = []
    if validation.get("status") != "passed":
        errors.append("independent validation status is not passed")
    if validation.get("final_outer") != actual:
        errors.append("independent validation is not final-outer-bound")
    if actual_inner is None:
        errors.append(f"final inner identity cannot be read: {inner_error}")
    elif validation.get("final_inner") != actual_inner:
        errors.append("independent validation is not final-inner-bound")
    if portability_matrix.get("status") != "passed":
        errors.append("portability matrix status is not passed")
    if portability_matrix.get("final_outer") != actual:
        errors.append("portability matrix is not final-outer-bound")
    if (
        actual_inner is not None
        and portability_matrix.get("final_inner") != actual_inner
    ):
        errors.append("portability matrix is not final-inner-bound")
    matrix_validation = validate_portability_matrix(portability_matrix)
    errors.extend(matrix_validation["errors"])
    environments = portability_matrix.get("environments", [])
    return {
        "schema_id": "detached-final-binding-validation-current",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "final_outer": actual,
        "final_inner": actual_inner,
        "environment_count": (
            len(environments) if isinstance(environments, list) else 0
        ),
    }


def validate_portability_matrix(
    matrix: Mapping[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    outer = matrix.get("final_outer")
    inner = matrix.get("final_inner")
    environments = matrix.get("environments")
    if matrix.get("status") != "passed":
        errors.append("portability matrix status is not passed")
    if not isinstance(outer, Mapping) or not re.fullmatch(
        r"[0-9a-f]{64}", str(outer.get("sha256"))
    ):
        errors.append("portability matrix final outer identity is invalid")
    if not isinstance(inner, Mapping):
        errors.append("portability matrix final inner identity is invalid")
    if not isinstance(environments, list) or len(environments) < 2:
        errors.append("two passed portability environments are required")
        environments = []
    identities: set[tuple[str, str]] = set()
    all_eight_different = False
    required_generic_tools = {
        "bash",
        "git",
        "ip",
        "mount",
        "tar",
        "unshare",
        "unzip",
        "zstd",
    }
    for index, row in enumerate(environments):
        prefix = f"environment {index + 1}"
        if not isinstance(row, Mapping) or row.get("status") != "passed":
            errors.append(f"{prefix} did not pass")
            continue
        image = str(row.get("image_digest", ""))
        host_glibc = str(row.get("host_userspace_glibc", ""))
        required_runtime_identity = (
            "host_userspace_distribution",
            "host_userspace_glibc",
            "host_kernel",
            "packaged_bootstrap_glibc",
            "packaged_replay_rootfs_glibc",
        )
        if (
            not image
            or not host_glibc
            or any(not row.get(field) for field in required_runtime_identity)
        ):
            errors.append(f"{prefix} identity is incomplete")
        distribution = str(
            row.get("host_userspace_distribution", "")
        ).lower()
        expected_debian_glibc = (
            "2.36"
            if "debian 12" in distribution
            else "2.41"
            if "debian 13" in distribution
            else None
        )
        if (
            expected_debian_glibc is not None
            and host_glibc != expected_debian_glibc
        ):
            errors.append(
                f"{prefix} host userspace glibc is wrong for "
                f"{distribution}: {host_glibc}"
            )
        identities.add((image, host_glibc))
        if row.get("namespace_mode") not in {"rootless", "privileged"}:
            errors.append(f"{prefix} namespace mode is not explicit")
        if row.get("replay_exit_code") != 0:
            errors.append(f"{prefix} replay did not exit zero")
        if row.get("network_status") != "passed":
            errors.append(f"{prefix} network isolation did not pass")
        if row.get("final_outer") != outer:
            errors.append(f"{prefix} final outer identity differs")
        if row.get("final_inner") != inner:
            errors.append(f"{prefix} final inner identity differs")
        if (
            row.get("verifier_receipt_final_outer_sha256")
            != outer.get("sha256") if isinstance(outer, Mapping) else None
        ):
            errors.append(f"{prefix} verifier receipt is not outer-bound")
        different = set(
            row.get(
                "host_generic_tool_hashes_different_from_builder",
                [],
            )
        )
        if required_generic_tools <= different:
            all_eight_different = True
    if len(identities) < 2:
        errors.append("Linux userspaces are not materially distinct")
    if not all_eight_different:
        errors.append(
            "no environment differs for all eight former host tools"
        )
    return {
        "schema_id": "portability-matrix-validation-current",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "environment_count": len(environments),
        "materially_distinct_identity_count": len(identities),
        "all_eight_host_tool_hashes_different": all_eight_different,
    }


def environment_result_from_verifier(
    *,
    outer: Path,
    verifier_root: Path,
    name: str,
    image_digest: str,
    builder_generic_tool_lock: Mapping[str, Any],
) -> dict[str, Any]:
    final_outer = final_outer_identity(outer)
    final_inner = final_inner_identity(outer)
    receipt_path = verifier_root / "independent-verifier-receipt.json"
    bootstrap_path = verifier_root / "bootstrap-contract.json"
    replay_root = verifier_root / "replay"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    bootstrap = json.loads(bootstrap_path.read_text(encoding="utf-8"))
    replay = json.loads(
        (replay_root / "replay-result.json").read_text(encoding="utf-8")
    )
    runtime = json.loads(
        (replay_root / "runtime-resolution.json").read_text(
            encoding="utf-8"
        )
    )
    namespace = json.loads(
        (replay_root / "namespace-capability-receipt.json").read_text(
            encoding="utf-8"
        )
    )
    network = json.loads(
        (replay_root / "network-namespace-receipt.json").read_text(
            encoding="utf-8"
        )
    )
    stages = json.loads(
        (replay_root / "stage-results.json").read_text(encoding="utf-8")
    )
    evidence_manifest = json.loads(
        (replay_root / "replay-evidence-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    errors: list[str] = []
    if receipt.get("status") != "passed":
        errors.append("independent verifier did not pass")
    if (
        receipt.get("input", {}).get("outer_delivery_sha256")
        != final_outer["sha256"]
    ):
        errors.append("verifier receipt is not exact-final-outer-bound")
    if receipt.get("replay_exit_code") != 0:
        errors.append("replay did not exit zero")
    if replay.get("status") != "passed" or replay.get("exit_code") != 0:
        errors.append("packaged replay result did not pass")
    if namespace.get("status") != "passed":
        errors.append("namespace capability receipt did not pass")
    if network.get("status") != "passed":
        errors.append("network namespace receipt did not pass")
    if runtime.get("status") != "passed":
        errors.append("packaged runtime resolution did not pass")
    if bootstrap.get("status") != "passed":
        errors.append("bootstrap prerequisite capabilities did not pass")
    observed_host_tools = bootstrap.get(
        "observed_host_generic_tools_not_used", {}
    )
    comparison: dict[str, Any] = {}
    for tool in (
        "bash",
        "git",
        "ip",
        "mount",
        "tar",
        "unshare",
        "unzip",
        "zstd",
    ):
        builder_row = builder_generic_tool_lock.get(tool, {})
        builder_sha = (
            builder_row.get("sha256")
            if isinstance(builder_row, Mapping)
            else builder_row
        )
        observed_row = observed_host_tools.get(tool, {})
        observed_sha = (
            observed_row.get("sha256_observed_not_locked")
            if isinstance(observed_row, Mapping)
            else None
        )
        comparison[tool] = {
            "builder_sha256": builder_sha,
            "environment_sha256": observed_sha,
            "different": bool(
                builder_sha
                and observed_sha
                and builder_sha != observed_sha
            ),
            "host_identity_used_by_replay": False,
        }
    different_tools = sorted(
        name
        for name, row in comparison.items()
        if row["different"]
    )
    host = bootstrap.get("host", {})
    return {
        "schema_id": "final-outer-environment-verifier-current",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "name": name,
        "image_digest": image_digest,
        "host_userspace_distribution": host.get(
            "host_userspace_distribution"
        ),
        "host_userspace_glibc": host.get("host_userspace_glibc"),
        "host_kernel": host.get("host_kernel"),
        "packaged_bootstrap_glibc": host.get(
            "packaged_bootstrap_glibc"
        ),
        "packaged_replay_rootfs_glibc": runtime.get(
            "packaged_replay_rootfs_glibc"
        ),
        "machine": host.get("machine"),
        "effective_uid": host.get("effective_uid"),
        "effective_gid": host.get("effective_gid"),
        "host_bootstrap_prerequisites": bootstrap,
        "host_generic_tool_comparison": comparison,
        "host_generic_tool_hashes_different_from_builder":
            different_tools,
        "final_outer": final_outer,
        "final_inner": final_inner,
        "verifier_receipt_final_outer_sha256": receipt.get(
            "input", {}
        ).get("outer_delivery_sha256"),
        "verifier_receipt": {
            "path": str(receipt_path),
            "bytes": receipt_path.stat().st_size,
            "sha256": sha256_file(receipt_path),
        },
        "verifier_input": receipt.get("input"),
        "namespace_mode": namespace.get("mode"),
        "namespace_capability": namespace,
        "replay_exit_code": receipt.get("replay_exit_code"),
        "replay_duration_seconds": receipt.get(
            "replay_duration_seconds"
        ),
        "replay_evidence_root": evidence_manifest.get("manifest_root"),
        "replay_evidence_entry_count": evidence_manifest.get(
            "entry_count"
        ),
        "packaged_runtime_root": runtime.get("replay_rootfs"),
        "network_status": network.get("status"),
        "network_receipt": network,
        "all_stage_results": stages,
        "independent_verifier_checks": receipt.get("checks"),
        "independent_verifier_duration_seconds": receipt.get(
            "duration_seconds"
        ),
    }


def failure_result_from_verifier(
    *,
    outer: Path,
    verifier_root: Path,
) -> dict[str, Any]:
    preservation = validate_failure_preservation(verifier_root)
    receipt_path = verifier_root / "independent-verifier-receipt.json"
    failure_path = verifier_root / "failure-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    actual = final_outer_identity(outer)
    errors = list(preservation["errors"])
    if receipt.get("status") != "failed":
        errors.append("fault-injected verifier did not fail")
    if (
        receipt.get("input", {}).get("outer_delivery_sha256")
        != actual["sha256"]
    ):
        errors.append("failure receipt is not exact-final-outer-bound")
    if failure.get("status") != "failed":
        errors.append("failure-receipt status is not failed")
    artifacts = []
    for path in sorted(
        item for item in verifier_root.rglob("*") if item.is_file()
    ):
        artifacts.append(
            {
                "path": path.relative_to(verifier_root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "schema_id": "exact-final-failure-preservation-current",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "final_outer": actual,
        "fault_injection_stage": "runtime_resolution",
        "verifier_status": receipt.get("status"),
        "replay_exit_code": receipt.get("replay_exit_code"),
        "failed_checks": failure.get("failed_checks"),
        "last_completed_stage": failure.get("last_completed_stage"),
        "preservation": preservation,
        "artifacts": artifacts,
        "artifact_count": len(artifacts),
    }


def build_detached_final_receipts(
    *,
    outer: Path,
    environments: Sequence[Mapping[str, Any]],
    source_commit: str,
    source_tree: str,
    failure_evidence_validation: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    final_outer = final_outer_identity(outer)
    final_inner = final_inner_identity(outer)
    environment_rows = [
        json.loads(json.dumps(row)) for row in environments
    ]
    matrix = {
        "schema_id": "exact-final-portability-matrix-current",
        "status": (
            "passed"
            if all(row.get("status") == "passed" for row in environment_rows)
            else "failed"
        ),
        "final_outer": final_outer,
        "final_inner": final_inner,
        "source": {
            "commit": source_commit,
            "tree": source_tree,
        },
        "environments": environment_rows,
    }
    matrix_check = validate_portability_matrix(matrix)
    matrix["status"] = matrix_check["status"]
    matrix["validation"] = matrix_check
    validation = {
        "schema_id": "exact-final-independent-validation-current",
        "status": (
            "passed"
            if matrix_check["status"] == "passed"
            and failure_evidence_validation.get("status") == "passed"
            else "failed"
        ),
        "final_outer": final_outer,
        "final_inner": final_inner,
        "final_manifest_counts_and_roots": {
            "inner_entry_count": final_inner.get(
                "manifest_entry_count"
            ),
            "inner_manifest_root": final_inner.get("manifest_root"),
            "qualifying_payload_entry_count": final_inner.get(
                "qualifying_payload_entry_count"
            ),
            "qualifying_payload_root": final_inner.get(
                "qualifying_payload_root"
            ),
        },
        "source": {
            "commit": source_commit,
            "tree": source_tree,
        },
        "input": (
            environment_rows[0].get("verifier_input", {})
            if environment_rows
            else {}
        ),
        "environment_identities": [
            {
                "name": row.get("name"),
                "image_digest": row.get("image_digest"),
                "host_userspace_distribution": row.get(
                    "host_userspace_distribution"
                ),
                "host_userspace_glibc": row.get(
                    "host_userspace_glibc"
                ),
                "host_kernel": row.get("host_kernel"),
                "packaged_bootstrap_glibc": row.get(
                    "packaged_bootstrap_glibc"
                ),
                "packaged_replay_rootfs_glibc": row.get(
                    "packaged_replay_rootfs_glibc"
                ),
            }
            for row in environment_rows
        ],
        "bootstrap_prerequisite_capabilities": [
            row.get("host_bootstrap_prerequisites")
            for row in environment_rows
        ],
        "packaged_runtime_roots": [
            row.get("packaged_runtime_root")
            for row in environment_rows
        ],
        "namespace_capability_modes": [
            {
                "name": row.get("name"),
                "mode": row.get("namespace_mode"),
                "receipt": row.get("namespace_capability"),
            }
            for row in environment_rows
        ],
        "replay_results": [
            {
                "name": row.get("name"),
                "exit_code": row.get("replay_exit_code"),
                "duration_seconds": row.get(
                    "replay_duration_seconds"
                ),
                "evidence_root": row.get("replay_evidence_root"),
            }
            for row in environment_rows
        ],
        "network_receipts": [
            {
                "name": row.get("name"),
                "receipt": row.get("network_receipt"),
            }
            for row in environment_rows
        ],
        "all_stage_results": [
            {
                "name": row.get("name"),
                "stages": row.get("all_stage_results"),
            }
            for row in environment_rows
        ],
        "environment_verifier_receipts": environment_rows,
        "portability_matrix_validation": matrix_check,
        "failure_evidence_preservation": json.loads(
            json.dumps(failure_evidence_validation)
        ),
    }
    return validation, matrix


def _zip_write(
    archive: zipfile.ZipFile,
    name: str,
    data: bytes,
    *,
    executable: bool = False,
) -> None:
    info = zipfile.ZipInfo(name, PART_TIMESTAMP)
    info.create_system = 3
    mode = 0o100755 if executable else 0o100644
    info.external_attr = (mode & 0xFFFF) << 16
    info.compress_type = zipfile.ZIP_STORED
    archive.writestr(info, data)


def _reconstruct_script() -> bytes:
    return b"""#!/bin/sh
set -eu
unset LD_LIBRARY_PATH PYTHONPATH JAVA_HOME NODE_PATH
if [ "$#" -lt 1 ]; then
  echo "usage: reconstruct.sh PART-ZIP..." >&2
  exit 64
fi
exec python3 - "$@" <<'PY'
import hashlib, io, json, pathlib, sys, zipfile
parts = [pathlib.Path(value) for value in sys.argv[1:]]
rows = []
manifest_bytes = None
for part in parts:
    with zipfile.ZipFile(part) as archive:
        current = archive.read("split-delivery-manifest.json")
        if manifest_bytes is None:
            manifest_bytes = current
        elif current != manifest_bytes:
            raise SystemExit("split manifests differ")
        payload_names = [n for n in archive.namelist() if n.startswith("payload.part-")]
        if len(payload_names) != 1:
            raise SystemExit("part payload member set mismatch")
        rows.append((payload_names[0], archive.read(payload_names[0])))
manifest = json.loads(manifest_bytes)
rows.sort()
output = pathlib.Path(manifest["final_outer"]["filename"])
digest = hashlib.sha256()
with output.open("wb") as stream:
    for _, payload in rows:
        stream.write(payload)
        digest.update(payload)
if output.stat().st_size != manifest["final_outer"]["bytes"]:
    raise SystemExit("reconstructed byte count mismatch")
if digest.hexdigest() != manifest["final_outer"]["sha256"]:
    raise SystemExit("reconstructed SHA-256 mismatch")
print(output)
PY
"""


def _split_index_markdown(manifest: Mapping[str, Any]) -> bytes:
    lines = [
        "# Split external-review delivery",
        "",
        f"Final outer: `{manifest['final_outer']['filename']}`",
        "",
        "| Part | Payload bytes | Payload SHA-256 |",
        "|---:|---:|---|",
    ]
    for row in manifest["parts"]:
        lines.append(
            f"| {row['index']} | {row['payload_bytes']} | "
            f"`{row['payload_sha256']}` |"
        )
    lines.extend(
        [
            "",
            "Part archive identities use SHA-256 after normalizing every "
            "embedded `part_zip_sha256` field to 64 zeroes. This explicit "
            "self-excluding mode avoids an impossible self-hash.",
            "",
        ]
    )
    return ("\n".join(lines)).encode("utf-8")


def _metadata_payloads(
    *,
    manifest: Mapping[str, Any],
    checksum: bytes,
    validation: bytes,
    matrix: bytes,
    agent_response: bytes,
    static_bootstrap: bytes,
    static_bootstrap_checksum: bytes,
    source_only_ci_receipt: bytes,
) -> dict[str, bytes]:
    return {
        "agent-response.md": agent_response,
        "final-outer.independent-validation.json": validation,
        "final-outer.portability-matrix.json": matrix,
        "final-outer.sha256": checksum,
        "independent-verifier-bootstrap": static_bootstrap,
        "independent-verifier-bootstrap.sha256":
            static_bootstrap_checksum,
        "reconstruct.sh": _reconstruct_script(),
        "source-only-ci-receipt.json": source_only_ci_receipt,
        "split-delivery-manifest.json": canonical_bytes(manifest),
        "split-index.json": canonical_bytes(manifest),
        "split-index.md": _split_index_markdown(manifest),
    }


def _part_archive_bytes(
    payload_name: str,
    payload: bytes,
    metadata: Mapping[str, bytes],
) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        _zip_write(archive, payload_name, payload)
        for name, data in sorted(metadata.items()):
            _zip_write(
                archive,
                name,
                data,
                executable=name
                in {
                    "reconstruct.sh",
                    "independent-verifier-bootstrap",
                },
            )
    return stream.getvalue()


def _zero_part_hashes(manifest: Mapping[str, Any]) -> dict[str, Any]:
    value = json.loads(json.dumps(manifest))
    for row in value["parts"]:
        row["part_zip_sha256"] = ZERO_SHA256
    return value


def build_split_delivery(
    *,
    outer: Path,
    checksum: Path,
    validation: Path,
    portability_matrix: Path,
    agent_response: Path,
    static_bootstrap: Path,
    static_bootstrap_checksum: Path,
    source_only_ci_receipt: Path,
    output: Path,
    payload_bytes: int = 480_000_000,
    maximum_part_zip_bytes: int = 500_000_000,
) -> list[Path]:
    if payload_bytes <= 0 or payload_bytes >= maximum_part_zip_bytes:
        raise ValueError("invalid split payload boundary")
    if output.exists() and any(output.iterdir()):
        raise ValueError("split output must be empty")
    output.mkdir(parents=True, exist_ok=True)
    outer_identity = final_outer_identity(outer)
    validation_value = json.loads(validation.read_text(encoding="utf-8"))
    matrix_value = json.loads(
        portability_matrix.read_text(encoding="utf-8")
    )
    binding = validate_detached_final_binding(
        outer, validation_value, matrix_value
    )
    if binding["status"] != "passed":
        raise ValueError(binding["errors"])
    checksum_value = checksum.read_text(encoding="utf-8").split()
    if not checksum_value or checksum_value[0] != outer_identity["sha256"]:
        raise ValueError("detached final checksum mismatch")
    bootstrap_digest = sha256_file(static_bootstrap)
    bootstrap_checksum_value = (
        static_bootstrap_checksum.read_text(encoding="utf-8").split()
    )
    if (
        not bootstrap_checksum_value
        or bootstrap_checksum_value[0] != bootstrap_digest
    ):
        raise ValueError("static bootstrap checksum mismatch")
    source_ci_value = json.loads(
        source_only_ci_receipt.read_text(encoding="utf-8")
    )
    source = validation_value.get("source", {})
    if (
        source_ci_value.get("status") != "passed"
        or source_ci_value.get("execution_stratum") != "source-only"
        or source_ci_value.get("source", {}).get("commit")
        != source.get("commit")
        or source_ci_value.get("source", {}).get("tree")
        != source.get("tree")
        or source_ci_value.get("source", {}).get("worktree_clean")
        is not True
    ):
        raise ValueError(
            "source-only CI receipt did not pass exact-source binding"
        )
    with zipfile.ZipFile(outer) as outer_archive:
        if (
            outer_archive.read("independent-verifier-bootstrap")
            != static_bootstrap.read_bytes()
            or outer_archive.read(
                "independent-verifier-bootstrap.sha256"
            )
            != static_bootstrap_checksum.read_bytes()
        ):
            raise ValueError(
                "split static bootstrap differs from final outer"
            )
    chunks: list[bytes] = []
    with outer.open("rb") as stream:
        while chunk := stream.read(payload_bytes):
            chunks.append(chunk)
    count = len(chunks)
    parts = []
    offset = 0
    for index, payload in enumerate(chunks, start=1):
        filename = (
            f"{outer.stem}-part-{index:03d}-of-{count:03d}.zip"
        )
        parts.append(
            {
                "index": index,
                "filename": filename,
                "payload_filename": f"payload.part-{index:03d}.bin",
                "payload_bytes": len(payload),
                "payload_sha256": sha256_bytes(payload),
                "payload_offset": offset,
                "part_zip_bytes": 0,
                "part_zip_sha256": ZERO_SHA256,
                "part_zip_sha256_validation_mode": (
                    "sha256_with_all_embedded_part_zip_sha256_fields_zeroed"
                ),
            }
        )
        offset += len(payload)
    manifest: dict[str, Any] = {
        "schema_id": "split-delivery-manifest-current",
        "status": "passed",
        "maximum_part_zip_bytes": maximum_part_zip_bytes,
        "payload_chunk_bytes": payload_bytes,
        "part_count": count,
        "final_outer": outer_identity,
        "final_inner": validation_value.get("final_inner"),
        "source_commit": source.get("commit"),
        "source_tree": source.get("tree"),
        "parts": parts,
        "detached_validation": {
            "status": validation_value.get("status"),
            "portability_matrix_status": matrix_value.get("status"),
        },
        "static_bootstrap": {
            "bytes": static_bootstrap.stat().st_size,
            "sha256": bootstrap_digest,
        },
        "source_only_ci_receipt_sha256": sha256_file(
            source_only_ci_receipt
        ),
    }
    raw_sidecars = {
        "checksum": checksum.read_bytes(),
        "validation": validation.read_bytes(),
        "matrix": portability_matrix.read_bytes(),
        "response": agent_response.read_bytes(),
        "static_bootstrap": static_bootstrap.read_bytes(),
        "static_bootstrap_checksum":
            static_bootstrap_checksum.read_bytes(),
        "source_only_ci_receipt": source_only_ci_receipt.read_bytes(),
    }
    # Stored ZIP members make archive length independent of same-length hash
    # substitutions. Iterate only until decimal byte-count fields stabilize.
    for _ in range(8):
        zero_manifest = _zero_part_hashes(manifest)
        metadata = _metadata_payloads(
            manifest=zero_manifest,
            checksum=raw_sidecars["checksum"],
            validation=raw_sidecars["validation"],
            matrix=raw_sidecars["matrix"],
            agent_response=raw_sidecars["response"],
            static_bootstrap=raw_sidecars["static_bootstrap"],
            static_bootstrap_checksum=raw_sidecars[
                "static_bootstrap_checksum"
            ],
            source_only_ci_receipt=raw_sidecars[
                "source_only_ci_receipt"
            ],
        )
        sizes = [
            len(
                _part_archive_bytes(
                    row["payload_filename"], payload, metadata
                )
            )
            for row, payload in zip(manifest["parts"], chunks, strict=True)
        ]
        if all(
            row["part_zip_bytes"] == size
            for row, size in zip(manifest["parts"], sizes, strict=True)
        ):
            break
        for row, size in zip(manifest["parts"], sizes, strict=True):
            row["part_zip_bytes"] = size
    else:
        raise RuntimeError("split part byte counts did not stabilize")
    zero_manifest = _zero_part_hashes(manifest)
    zero_metadata = _metadata_payloads(
        manifest=zero_manifest,
        checksum=raw_sidecars["checksum"],
        validation=raw_sidecars["validation"],
        matrix=raw_sidecars["matrix"],
        agent_response=raw_sidecars["response"],
        static_bootstrap=raw_sidecars["static_bootstrap"],
        static_bootstrap_checksum=raw_sidecars[
            "static_bootstrap_checksum"
        ],
        source_only_ci_receipt=raw_sidecars[
            "source_only_ci_receipt"
        ],
    )
    for row, payload in zip(manifest["parts"], chunks, strict=True):
        normalized = _part_archive_bytes(
            row["payload_filename"], payload, zero_metadata
        )
        row["part_zip_sha256"] = sha256_bytes(normalized)
    metadata = _metadata_payloads(
        manifest=manifest,
        checksum=raw_sidecars["checksum"],
        validation=raw_sidecars["validation"],
        matrix=raw_sidecars["matrix"],
        agent_response=raw_sidecars["response"],
        static_bootstrap=raw_sidecars["static_bootstrap"],
        static_bootstrap_checksum=raw_sidecars[
            "static_bootstrap_checksum"
        ],
        source_only_ci_receipt=raw_sidecars[
            "source_only_ci_receipt"
        ],
    )
    outputs: list[Path] = []
    for row, payload in zip(manifest["parts"], chunks, strict=True):
        data = _part_archive_bytes(
            row["payload_filename"], payload, metadata
        )
        if len(data) != row["part_zip_bytes"]:
            raise RuntimeError("split part byte identity drifted")
        if len(data) >= maximum_part_zip_bytes:
            raise ValueError("split part exceeds strict upload boundary")
        path = output / row["filename"]
        path.write_bytes(data)
        outputs.append(path)
    write_json(output / "split-delivery-manifest.json", manifest)
    (output / "split-index.json").write_bytes(canonical_bytes(manifest))
    (output / "split-index.md").write_bytes(
        _split_index_markdown(manifest)
    )
    return outputs


def _normalized_part_digest(
    archive: zipfile.ZipFile,
    manifest: Mapping[str, Any],
) -> str:
    payload_names = [
        name
        for name in archive.namelist()
        if name.startswith("payload.part-")
    ]
    if len(payload_names) != 1:
        raise ValueError("part payload member set mismatch")
    zero = _zero_part_hashes(manifest)
    metadata = _metadata_payloads(
        manifest=zero,
        checksum=archive.read("final-outer.sha256"),
        validation=archive.read(
            "final-outer.independent-validation.json"
        ),
        matrix=archive.read("final-outer.portability-matrix.json"),
        agent_response=archive.read("agent-response.md"),
        static_bootstrap=archive.read(
            "independent-verifier-bootstrap"
        ),
        static_bootstrap_checksum=archive.read(
            "independent-verifier-bootstrap.sha256"
        ),
        source_only_ci_receipt=archive.read(
            "source-only-ci-receipt.json"
        ),
    )
    data = _part_archive_bytes(
        payload_names[0], archive.read(payload_names[0]), metadata
    )
    return sha256_bytes(data)


def validate_split_delivery(
    parts: Sequence[Path],
    reconstruction_root: Path,
) -> dict[str, Any]:
    errors: list[str] = []
    if not parts:
        return {
            "schema_id": "split-delivery-validation-current",
            "status": "failed",
            "errors": ["no split parts provided"],
        }
    manifests: list[bytes] = []
    payloads: dict[int, bytes] = {}
    observed_archives: list[dict[str, Any]] = []
    sidecars: dict[str, bytes] | None = None
    for part in parts:
        try:
            with zipfile.ZipFile(part) as archive:
                names = set(archive.namelist())
                payload_names = sorted(
                    name
                    for name in names
                    if name.startswith("payload.part-")
                )
                if len(payload_names) != 1:
                    errors.append(f"{part.name}: payload member mismatch")
                    continue
                if names != DETACHED_PART_FILES | set(payload_names):
                    errors.append(f"{part.name}: member set mismatch")
                manifest_bytes = archive.read(
                    "split-delivery-manifest.json"
                )
                manifests.append(manifest_bytes)
                manifest = json.loads(manifest_bytes)
                if archive.read("split-index.json") != manifest_bytes:
                    errors.append(f"{part.name}: JSON index differs")
                row = next(
                    (
                        item
                        for item in manifest["parts"]
                        if item["filename"] == part.name
                    ),
                    None,
                )
                if row is None:
                    errors.append(f"{part.name}: absent from manifest")
                    continue
                payload = archive.read(payload_names[0])
                if (
                    len(payload) != row["payload_bytes"]
                    or sha256_bytes(payload) != row["payload_sha256"]
                ):
                    errors.append(f"{part.name}: payload identity mismatch")
                if part.stat().st_size != row["part_zip_bytes"]:
                    errors.append(f"{part.name}: part byte count mismatch")
                normalized = _normalized_part_digest(archive, manifest)
                if normalized != row["part_zip_sha256"]:
                    errors.append(
                        f"{part.name}: normalized part identity mismatch"
                    )
                payloads[row["index"]] = payload
                current_sidecars = {
                    name: archive.read(name)
                    for name in DETACHED_PART_FILES
                    if name
                    not in {
                        "split-delivery-manifest.json",
                        "split-index.json",
                        "split-index.md",
                        "reconstruct.sh",
                    }
                }
                if sidecars is None:
                    sidecars = current_sidecars
                elif current_sidecars != sidecars:
                    errors.append(f"{part.name}: detached artifacts differ")
                observed_archives.append(
                    {
                        "filename": part.name,
                        "bytes": part.stat().st_size,
                        "sha256": sha256_file(part),
                        "normalized_sha256": normalized,
                    }
                )
        except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
            errors.append(f"{part.name}: {exc}")
    if not manifests or any(value != manifests[0] for value in manifests):
        errors.append("split manifests are not identical")
        manifest = {}
    else:
        manifest = json.loads(manifests[0])
    expected_indices = list(range(1, int(manifest.get("part_count", 0)) + 1))
    if sorted(payloads) != expected_indices:
        errors.append("split part index set mismatch")
    reconstruction_root.mkdir(parents=True, exist_ok=True)
    outer_identity = manifest.get("final_outer", {})
    reconstructed = reconstruction_root / str(
        outer_identity.get("filename", "reconstructed-outer.zip")
    )
    digest = hashlib.sha256()
    with reconstructed.open("wb") as stream:
        for index in sorted(payloads):
            stream.write(payloads[index])
            digest.update(payloads[index])
    actual = final_outer_identity(reconstructed)
    if actual != outer_identity:
        errors.append("reconstructed final outer identity mismatch")
    detached_validation: dict[str, Any] = {}
    matrix: dict[str, Any] = {}
    if sidecars is None:
        errors.append("detached artifacts are missing")
    else:
        try:
            checksum = sidecars["final-outer.sha256"].decode().split()[0]
            detached_validation = json.loads(
                sidecars[
                    "final-outer.independent-validation.json"
                ]
            )
            matrix = json.loads(
                sidecars["final-outer.portability-matrix.json"]
            )
            if checksum != actual["sha256"]:
                errors.append("detached final checksum mismatch")
            binding = validate_detached_final_binding(
                reconstructed, detached_validation, matrix
            )
            errors.extend(binding["errors"])
            bootstrap_hash = sha256_bytes(
                sidecars["independent-verifier-bootstrap"]
            )
            declared_bootstrap_hash = sidecars[
                "independent-verifier-bootstrap.sha256"
            ].decode().split()[0]
            if bootstrap_hash != declared_bootstrap_hash:
                errors.append("static bootstrap checksum mismatch")
            source_ci = json.loads(
                sidecars["source-only-ci-receipt.json"]
            )
            if (
                source_ci.get("status") != "passed"
                or source_ci.get("execution_stratum")
                != "source-only"
                or source_ci.get("source", {}).get("commit")
                != detached_validation.get("source", {}).get("commit")
                or source_ci.get("source", {}).get("tree")
                != detached_validation.get("source", {}).get("tree")
                or source_ci.get("source", {}).get("worktree_clean")
                is not True
            ):
                errors.append(
                    "source-only CI receipt did not pass exact-source "
                    "binding"
                )
            with zipfile.ZipFile(reconstructed) as outer_archive:
                if (
                    outer_archive.read(
                        "independent-verifier-bootstrap"
                    )
                    != sidecars["independent-verifier-bootstrap"]
                    or outer_archive.read(
                        "independent-verifier-bootstrap.sha256"
                    )
                    != sidecars[
                        "independent-verifier-bootstrap.sha256"
                    ]
                ):
                    errors.append(
                        "split static bootstrap differs from final outer"
                    )
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"detached artifact parsing failed: {exc}")
    outer_validation: dict[str, Any] | None = None
    try:
        with zipfile.ZipFile(reconstructed) as archive:
            production_outer = "delivery-manifest.json" in archive.namelist()
        if production_outer:
            from external_review_delivery import validate

            outer_validation = validate(reconstructed)
            if outer_validation.get("overall_status") != "passed":
                errors.append("outer/inner delivery validation failed")
    except (ValueError, zipfile.BadZipFile) as exc:
        errors.append(f"outer/inner delivery validation failed: {exc}")
    return {
        "schema_id": "split-delivery-validation-current",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "part_archives": observed_archives,
        "manifest_identical": bool(manifests)
        and all(value == manifests[0] for value in manifests),
        "final_outer": actual,
        "exact_reconstruction": actual == outer_identity,
        "detached_final_validation_status": detached_validation.get(
            "status"
        ),
        "portability_matrix_status": matrix.get("status"),
        "outer_inner_validation": outer_validation,
    }


def build_readiness(
    *,
    outer: Path,
    validation: Mapping[str, Any],
    portability_matrix: Mapping[str, Any],
    fault_matrix_value: Mapping[str, Any],
    split_validation: Mapping[str, Any],
) -> dict[str, Any]:
    binding = validate_detached_final_binding(
        outer, validation, portability_matrix
    )
    matrix_validation = validate_portability_matrix(
        portability_matrix
    )
    environments = portability_matrix.get("environments", [])
    if not isinstance(environments, list):
        environments = []
    fault_cases = {
        row.get("id"): row
        for row in fault_matrix_value.get("cases", [])
        if isinstance(row, Mapping)
    }

    def fault_passed(name: str) -> bool:
        return fault_cases.get(name, {}).get("status") == "passed"

    bootstrap_passed = bool(environments) and all(
        row.get("host_bootstrap_prerequisites", {}).get("status")
        == "passed"
        and all(
            value is None
            for value in row.get(
                "host_bootstrap_prerequisites", {}
            )
            .get("sanitized_environment", {})
            .values()
        )
        for row in environments
    )
    no_host_semantic_runtime = bool(environments) and all(
        row.get("packaged_runtime_root")
        and all(
            comparison.get("host_identity_used_by_replay") is False
            for comparison in row.get(
                "host_generic_tool_comparison", {}
            ).values()
        )
        for row in environments
    )
    namespace_contract = bool(environments) and all(
        validate_namespace_capability_receipt(
            row.get("namespace_capability", {})
        )["status"]
        == "passed"
        for row in environments
    )
    measured_network = bool(environments) and all(
        validate_network_namespace_receipt(
            row.get("network_receipt", {})
        )["status"]
        == "passed"
        for row in environments
    )
    failure_preserved = (
        validation.get("failure_evidence_preservation", {}).get(
            "status"
        )
        == "passed"
    )
    outer_validation = split_validation.get(
        "outer_inner_validation", {}
    )
    generated_equality = (
        outer_validation.get("inner_validation", {})
        .get("detailed_inner_validation", {})
        .get("checks", {})
        .get("generated_artifact_equality")
        is True
    )
    requirements = {
        "outer_bootstrap_unaffected_by_packaged_libraries": (
            bootstrap_passed
            and fault_passed(
                "global_packaged_ld_library_path_contaminates_host_awk"
            )
            and fault_passed(
                "global_packaged_ld_library_path_contaminates_host_sha256sum"
            )
        ),
        "semantic_replay_uses_no_unbundled_exact_hash_locked_tool": (
            no_host_semantic_runtime
            and fault_passed("host_generic_tool_hashes_differ")
            and fault_passed("packaged_semantic_tool_hash_differs")
            and fault_passed("packaged_semantic_tool_missing")
        ),
        "platform_capability_contract_explicit": namespace_contract,
        "network_isolation_measured": measured_network,
        "failure_evidence_retained": failure_preserved,
        "source_generated_and_packaged_scripts_identical":
            generated_equality,
        "exact_final_outer_independently_verified": (
            binding["status"] == "passed"
        ),
        "two_materially_distinct_linux_userspaces_pass": (
            matrix_validation["status"] == "passed"
        ),
        "detached_final_receipts_match_final_outer": (
            binding["status"] == "passed"
        ),
        "split_reconstruction_and_validation_pass": (
            split_validation.get("status") == "passed"
            and split_validation.get("exact_reconstruction") is True
            and split_validation.get("portability_matrix_status")
            == "passed"
        ),
    }
    return {
        "schema_id": "cross-environment-replay-readiness-current",
        "status": (
            "GO" if all(requirements.values()) else "NO_GO"
        ),
        "requirements": requirements,
        "failed_requirements": sorted(
            name
            for name, passed in requirements.items()
            if not passed
        ),
        "final_outer": final_outer_identity(outer),
        "final_inner": final_inner_identity(outer),
        "environment_count": len(environments),
        "fault_matrix_status": fault_matrix_value.get("status"),
        "detached_binding": binding,
        "portability_validation": matrix_validation,
        "split_validation": json.loads(
            json.dumps(split_validation)
        ),
        "prohibited_work": {
            "model_calls": 0,
            "codex_implementation_children": 0,
            "qualifications": 0,
            "canaries": 0,
            "benchmark_matrices": 0,
        },
    }


def readiness_markdown(value: Mapping[str, Any]) -> str:
    lines = [
        "# Cross-environment replay readiness",
        "",
        f"Decision: **{value['status']}**.",
        "",
        "| Requirement | Result |",
        "|---|---|",
    ]
    for name, passed in value["requirements"].items():
        lines.append(
            f"| `{name}` | **{'passed' if passed else 'failed'}** |"
        )
    lines.extend(
        [
            "",
            f"Final outer: `{value['final_outer']['sha256']}` "
            f"({value['final_outer']['bytes']} bytes).",
            "",
            f"Portability environments: `{value['environment_count']}`.",
            "",
            "No model calls, Codex implementation children, "
            "qualifications, canaries, or benchmark matrices were run.",
            "",
        ]
    )
    return "\n".join(lines)


def release_command_exit_code(
    command: str, result: Mapping[str, Any]
) -> int:
    expected_status = "GO" if command == "readiness" else "passed"
    return 0 if result.get("status") == expected_status else 1


def fault_matrix(repo: Path) -> dict[str, Any]:
    launcher = (repo / "scripts/independent_verifier.sh").read_text(
        encoding="utf-8"
    )
    bootstrap_positive = validate_bootstrap_launcher(launcher)
    cases: list[dict[str, Any]] = []

    def row(
        name: str,
        detected: bool,
        boundary: str,
        *,
        expected_outcome: str = "rejected",
        evidence: Any = None,
    ) -> None:
        cases.append(
            {
                "id": name,
                "boundary": boundary,
                "fault_injected": True,
                "expected_outcome": expected_outcome,
                "observed_outcome": (
                    expected_outcome if detected else "not_detected"
                ),
                "rejected": (
                    detected and expected_outcome == "rejected"
                ),
                "evidence": evidence,
                "status": "passed" if detected else "failed",
            }
        )

    for utility in ("awk", "sha256sum"):
        fault = launcher.replace(
            "unset LD_LIBRARY_PATH",
            'export LD_LIBRARY_PATH="$STAGE/inner/runtime/'
            'bootstrap-python/system-libs"',
            1,
        )
        observed = validate_bootstrap_launcher(
            fault + f"\n{utility} --version\n"
        )
        row(
            f"global_packaged_ld_library_path_contaminates_host_{utility}",
            observed["status"] == "failed"
            and "global packaged LD_LIBRARY_PATH" in observed["errors"]
            and utility in observed["forbidden_host_semantic_utilities"],
            "outer bootstrap",
            evidence=observed,
        )

    from target_replay import (
        GENERIC_SEMANTIC_TOOLS,
        _generic_runtime_resolution,
        _replay_script,
    )

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        packaged: dict[str, Any] = {}
        paths: dict[str, Path] = {}
        for name in GENERIC_SEMANTIC_TOOLS:
            path = root / f"runtime/replay-rootfs/usr/bin/{name}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"packaged-{name}\n".encode())
            paths[name] = path
            packaged[name] = {
                "role": "packaged_semantic_runtime",
                "path": path.relative_to(root).as_posix(),
                "execution_path": f"/usr/bin/{name}",
                "sha256": sha256_file(path),
                "version": "fault fixture",
                "validation_mode": "exact_identity",
            }
        lock = {"packaged_semantic_runtime": packaged}
        generic, generic_errors = _generic_runtime_resolution(lock, root)
        row(
            "host_generic_tool_hashes_differ",
            not generic_errors
            and all(item["matches_lock"] for item in generic.values()),
            "packaged semantic runtime",
            expected_outcome="accepted_without_host_identity_use",
            evidence={
                "host_hashes_supplied": False,
                "host_identity_used": False,
                "packaged_tool_count": len(generic),
            },
        )
        paths["git"].write_bytes(b"mutated\n")
        _, hash_errors = _generic_runtime_resolution(lock, root)
        row(
            "packaged_semantic_tool_hash_differs",
            "packaged semantic tool identity mismatch: git"
            in hash_errors,
            "packaged semantic runtime",
            evidence={"errors": hash_errors},
        )
        paths["git"].write_bytes(b"packaged-git\n")
        paths["zstd"].unlink()
        _, missing_errors = _generic_runtime_resolution(lock, root)
        row(
            "packaged_semantic_tool_missing",
            "packaged semantic tool missing: zstd" in missing_errors,
            "packaged semantic runtime",
            evidence={"errors": missing_errors},
        )

    def namespace_fixture(mode: str) -> dict[str, Any]:
        rootless = mode == "rootless"
        return {
            "status": "passed",
            "mode": mode,
            "effective_uid": 0,
            "effective_gid": 0,
            "uid_map": "0 65534 1",
            "gid_map": "0 65534 1",
            "new_user_namespace": rootless,
            "new_mount_namespace": True,
            "new_network_namespace": True,
            "new_pid_namespace": True,
            "mount_receipt": {
                "package": True,
                "work": True,
                "evidence": True,
                "proc": True,
                "empty_resolver": True,
            },
            "capability_check": {
                "rootless_user_namespace": rootless,
                "privileged_cap_sys_admin": not rootless,
                "privileged_cap_net_admin": not rootless,
            },
            "launcher_sha256": "d" * 64,
        }

    rootless = namespace_fixture("rootless")
    rootless["capability_check"]["rootless_user_namespace"] = False
    rootless_result = validate_namespace_capability_receipt(rootless)
    row(
        "rootless_namespace_unavailable",
        rootless_result["status"] == "failed",
        "namespace capability contract",
        evidence=rootless_result,
    )
    privileged = namespace_fixture("privileged")
    privileged["capability_check"]["privileged_cap_sys_admin"] = False
    privileged_result = validate_namespace_capability_receipt(privileged)
    row(
        "privileged_capability_unavailable",
        privileged_result["status"] == "failed",
        "namespace capability contract",
        evidence=privileged_result,
    )

    network_positive = {
        "status": "passed",
        "new_namespace": True,
        "default_external_route_present": False,
        "dns_configuration": {"host_dns_used": False},
        "external_tcp_probe": {"succeeded": False},
        "external_dns_probe": {"succeeded": False},
        "loopback_probe": {"succeeded": True},
        "network_enabled": False,
    }
    for name, field in (
        ("external_route_present", "default_external_route_present"),
        ("dns_unexpectedly_succeeds", "external_dns_probe"),
    ):
        value = json.loads(json.dumps(network_positive))
        if field == "external_dns_probe":
            value[field]["succeeded"] = True
        else:
            value[field] = True
        observed = validate_network_namespace_receipt(value)
        row(
            name,
            observed["status"] == "failed",
            "measured network receipt",
            evidence=observed,
        )

    def matrix(
        identity: Mapping[str, Any],
        inner_identity: Mapping[str, Any],
    ) -> dict[str, Any]:
        generic_names = [
            "bash",
            "git",
            "ip",
            "mount",
            "tar",
            "unshare",
            "unzip",
            "zstd",
        ]
        environments = []
        for index, (image, glibc) in enumerate(
            (
                ("debian12@sha256:" + ("1" * 64), "2.36"),
                ("debian13@sha256:" + ("2" * 64), "2.41"),
            )
        ):
            environments.append(
                {
                    "status": "passed",
                    "image_digest": image,
                    "host_userspace_distribution": (
                        "debian 12"
                        if glibc == "2.36"
                        else "debian 13"
                    ),
                    "host_userspace_glibc": glibc,
                    "host_kernel": "Linux fixture",
                    "packaged_bootstrap_glibc": "2.36",
                    "packaged_replay_rootfs_glibc": "2.36",
                    "namespace_mode": "privileged",
                    "replay_exit_code": 0,
                    "network_status": "passed",
                    "final_outer": dict(identity),
                    "final_inner": dict(inner_identity),
                    "verifier_receipt_final_outer_sha256":
                        identity["sha256"],
                    "host_generic_tool_hashes_different_from_builder":
                        generic_names if index else [],
                }
            )
        return {
            "status": "passed",
            "final_outer": dict(identity),
            "final_inner": dict(inner_identity),
            "environments": environments,
        }

    with tempfile.TemporaryDirectory() as temporary:
        outer = Path(temporary) / "final.zip"
        with zipfile.ZipFile(outer, "w") as archive:
            _zip_write(archive, "fixture", b"final")
        identity = final_outer_identity(outer)
        inner_identity = final_inner_identity(outer)
        candidate = {
            "status": "passed",
            "final_outer": {
                **identity,
                "sha256": "0" * 64,
            },
            "final_inner": dict(inner_identity),
        }
        candidate_result = validate_detached_final_binding(
            outer, candidate, matrix(identity, inner_identity)
        )
        stale_inner = dict(inner_identity)
        stale_inner["manifest_root"] = "0" * 64
        stale_result = validate_detached_final_binding(
            outer,
            {
                "status": "passed",
                "final_outer": dict(identity),
                "final_inner": stale_inner,
            },
            matrix(identity, inner_identity),
        )
        row(
            "candidate_outer_receipt",
            candidate_result["status"] == "failed"
            and stale_result["status"] == "failed",
            "detached final receipt binding",
            evidence={
                "candidate_outer": candidate_result,
                "stale_inner_manifest": stale_result,
            },
        )

    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary)
        for name in (
            "failure-receipt.json",
            "command-log.json",
            "stdout.log",
            "stderr.log",
            "last-completed-stage.json",
        ):
            (output / name).write_text("{}\n", encoding="utf-8")
        (output / "replay").mkdir()
        preservation = validate_failure_preservation(output)
        row(
            "failure_evidence_deleted",
            preservation["status"] == "failed",
            "independent verifier failure packaging",
            evidence=preservation,
        )

    verifier_source = (
        repo / "scripts/independent_verifier.sh"
    ).read_bytes()
    verifier_equality = validate_source_generated_equality(
        verifier_source,
        verifier_source + b"# drift\n",
        artifact="independent verifier",
    )
    row(
        "source_generated_verifier_differs",
        verifier_equality["status"] == "failed",
        "package source equality",
        evidence=verifier_equality,
    )
    replay_source = _replay_script().encode()
    replay_equality = validate_source_generated_equality(
        replay_source,
        replay_source + b"# drift\n",
        artifact="replay launcher",
    )
    row(
        "source_generated_replay_differs",
        replay_equality["status"] == "failed",
        "package source equality",
        evidence=replay_equality,
    )
    return {
        "schema_id": "cross-environment-fault-matrix-current",
        "status": (
            "passed"
            if bootstrap_positive["status"] == "passed"
            and all(case["status"] == "passed" for case in cases)
            else "failed"
        ),
        "cases": cases,
        "case_count": len(cases),
        "rejected_fault_count": sum(case["rejected"] for case in cases),
        "positive_bootstrap_fixture": bootstrap_positive,
    }


def fault_matrix_markdown(value: Mapping[str, Any]) -> str:
    lines = [
        "# Cross-environment replay fault matrix",
        "",
        f"Status: **{value['status']}**.",
        "",
        "| Fault | Boundary | Expected | Observed | Status |",
        "|---|---|---|---|---|",
    ]
    for row in value["cases"]:
        lines.append(
            f"| `{row['id']}` | {row['boundary']} | "
            f"`{row['expected_outcome']}` | "
            f"`{row['observed_outcome']}` | "
            f"**{row['status']}** |"
        )
    lines.extend(
        [
            "",
            f"Cases: `{value['case_count']}`.",
            "",
            "The host-generic-hash variation is expected to be accepted "
            "without consulting host identity. Every corrupt, missing, "
            "capability, network, receipt, evidence-deletion, or generated-"
            "byte fault is expected to be rejected.",
            "",
        ]
    )
    return "\n".join(lines)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    bootstrap = sub.add_parser("validate-bootstrap")
    bootstrap.add_argument(
        "--launcher",
        type=Path,
        default=ROOT / "scripts/independent_verifier.sh",
    )
    faults = sub.add_parser("fault-matrix")
    faults.add_argument("--repo", type=Path, default=ROOT)
    faults.add_argument("--output", type=Path)
    faults.add_argument("--markdown-output", type=Path)
    capture = sub.add_parser("capture-environment")
    capture.add_argument("--outer", type=Path, required=True)
    capture.add_argument("--verifier-root", type=Path, required=True)
    capture.add_argument("--name", required=True)
    capture.add_argument("--image-digest", required=True)
    capture.add_argument(
        "--builder-generic-audit",
        type=Path,
        required=True,
    )
    capture.add_argument("--output", type=Path, required=True)
    capture_failure = sub.add_parser("capture-failure")
    capture_failure.add_argument("--outer", type=Path, required=True)
    capture_failure.add_argument(
        "--verifier-root", type=Path, required=True
    )
    capture_failure.add_argument("--output", type=Path, required=True)
    bind = sub.add_parser("bind-final")
    bind.add_argument("--outer", type=Path, required=True)
    bind.add_argument(
        "--environment",
        type=Path,
        action="append",
        required=True,
    )
    bind.add_argument("--source-commit", required=True)
    bind.add_argument("--source-tree", required=True)
    bind.add_argument(
        "--failure-evidence-validation",
        type=Path,
        required=True,
    )
    bind.add_argument("--checksum-output", type=Path, required=True)
    bind.add_argument("--validation-output", type=Path, required=True)
    bind.add_argument("--matrix-output", type=Path, required=True)
    split = sub.add_parser("split")
    split.add_argument("--outer", type=Path, required=True)
    split.add_argument("--checksum", type=Path, required=True)
    split.add_argument("--validation", type=Path, required=True)
    split.add_argument("--portability-matrix", type=Path, required=True)
    split.add_argument("--agent-response", type=Path, required=True)
    split.add_argument(
        "--static-bootstrap", type=Path, required=True
    )
    split.add_argument(
        "--static-bootstrap-checksum", type=Path, required=True
    )
    split.add_argument(
        "--source-only-ci-receipt", type=Path, required=True
    )
    split.add_argument("--output", type=Path, required=True)
    validate_split = sub.add_parser("validate-split")
    validate_split.add_argument("parts", type=Path, nargs="+")
    validate_split.add_argument(
        "--reconstruction-root", type=Path, required=True
    )
    readiness = sub.add_parser("readiness")
    readiness.add_argument("--outer", type=Path, required=True)
    readiness.add_argument("--validation", type=Path, required=True)
    readiness.add_argument(
        "--portability-matrix", type=Path, required=True
    )
    readiness.add_argument("--fault-matrix", type=Path, required=True)
    readiness.add_argument(
        "--split-validation", type=Path, required=True
    )
    readiness.add_argument("--output", type=Path, required=True)
    readiness.add_argument(
        "--markdown-output", type=Path, required=True
    )
    args = parser.parse_args()
    if args.command == "validate-bootstrap":
        result = validate_bootstrap_launcher(
            args.launcher.read_text(encoding="utf-8")
        )
    elif args.command == "fault-matrix":
        result = fault_matrix(args.repo.resolve())
        if args.output:
            write_json(args.output.resolve(), result)
        if args.markdown_output:
            args.markdown_output.resolve().write_text(
                fault_matrix_markdown(result),
                encoding="utf-8",
            )
    elif args.command == "capture-environment":
        builder_audit = json.loads(
            args.builder_generic_audit.read_text(encoding="utf-8")
        )
        result = environment_result_from_verifier(
            outer=args.outer.resolve(),
            verifier_root=args.verifier_root.resolve(),
            name=args.name,
            image_digest=args.image_digest,
            builder_generic_tool_lock=builder_audit["expected"],
        )
        write_json(args.output.resolve(), result)
    elif args.command == "capture-failure":
        result = failure_result_from_verifier(
            outer=args.outer.resolve(),
            verifier_root=args.verifier_root.resolve(),
        )
        write_json(args.output.resolve(), result)
    elif args.command == "bind-final":
        outer = args.outer.resolve()
        environments = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in args.environment
        ]
        validation, matrix = build_detached_final_receipts(
            outer=outer,
            environments=environments,
            source_commit=args.source_commit,
            source_tree=args.source_tree,
            failure_evidence_validation=json.loads(
                args.failure_evidence_validation.read_text(
                    encoding="utf-8"
                )
            ),
        )
        write_json(args.validation_output.resolve(), validation)
        write_json(args.matrix_output.resolve(), matrix)
        args.checksum_output.resolve().write_text(
            f"{sha256_file(outer)}  {outer.name}\n",
            encoding="utf-8",
        )
        binding = validate_detached_final_binding(
            outer, validation, matrix
        )
        result = {
            "status": binding["status"],
            "binding": binding,
            "validation": validation,
            "portability_matrix": matrix,
        }
    elif args.command == "split":
        outputs = build_split_delivery(
            outer=args.outer.resolve(),
            checksum=args.checksum.resolve(),
            validation=args.validation.resolve(),
            portability_matrix=args.portability_matrix.resolve(),
            agent_response=args.agent_response.resolve(),
            static_bootstrap=args.static_bootstrap.resolve(),
            static_bootstrap_checksum=(
                args.static_bootstrap_checksum.resolve()
            ),
            source_only_ci_receipt=(
                args.source_only_ci_receipt.resolve()
            ),
            output=args.output.resolve(),
        )
        result = {
            "status": "passed",
            "parts": [str(path) for path in outputs],
        }
    elif args.command == "validate-split":
        result = validate_split_delivery(
            [path.resolve() for path in args.parts],
            args.reconstruction_root.resolve(),
        )
    else:
        result = build_readiness(
            outer=args.outer.resolve(),
            validation=json.loads(
                args.validation.read_text(encoding="utf-8")
            ),
            portability_matrix=json.loads(
                args.portability_matrix.read_text(encoding="utf-8")
            ),
            fault_matrix_value=json.loads(
                args.fault_matrix.read_text(encoding="utf-8")
            ),
            split_validation=json.loads(
                args.split_validation.read_text(encoding="utf-8")
            ),
        )
        write_json(args.output.resolve(), result)
        args.markdown_output.resolve().parent.mkdir(
            parents=True, exist_ok=True
        )
        args.markdown_output.resolve().write_text(
            readiness_markdown(result),
            encoding="utf-8",
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return release_command_exit_code(args.command, result)


if __name__ == "__main__":
    raise SystemExit(main())
