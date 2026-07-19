#!/usr/bin/env python3
"""Build and validate the explicit narrow-task release descriptor."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Mapping

from source_only_ci import (
    BASE_COMMIT,
    ROUTING_NONCE,
    TASK_ID,
    browser_receipt_errors,
    canonical_bytes,
    source_only_receipt_errors,
)


HEX_40 = re.compile(r"[0-9a-f]{40}")
HEX_64 = re.compile(r"[0-9a-f]{64}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
TASK_RECEIPT = {
    "task_id": TASK_ID,
    "routing_nonce": ROUTING_NONCE,
    "base_commit": BASE_COMMIT,
    "base_tree": "45d7e4d793c04d7d8e76e0a3ae3db7fafdc9a84e",
    "prior_task_id": "final-source-reproducible-offline-replay",
    "prior_task_must_not_be_packaged": True,
    "methodology_changes_authorized": False,
    "source_only_playwright_required": True,
    "pinned_source_only_userspace_required": True,
    "exact_final_receipts_required": True,
    "model_calls_authorized": False,
    "benchmark_children_authorized": False,
}
PACKAGED_PATHS = {
    "task_receipt": "task-receipt.json",
    "source_only_ci_receipt": "source-only-ci-receipt.json",
    "source_only_browser": "source-only-browser-receipt.json",
    "exact_final_debian_12_receipt":
        "exact-final-debian-12-receipt.json",
    "exact_final_debian_13_receipt":
        "exact-final-debian-13-receipt.json",
    "exact_final_independent_validation":
        "final-outer.independent-validation.json",
    "portability_matrix": "final-outer.portability-matrix.json",
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def artifact_record(path: Path, packaged_path: str) -> dict[str, Any]:
    return {
        "path": packaged_path,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def outer_identity(path: Path) -> dict[str, Any]:
    return {
        "filename": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def inner_identity(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as outer:
        members = sorted(
            name
            for name in outer.namelist()
            if name.startswith("review-handoff/")
            and name.endswith(".zip")
        )
        if len(members) != 1:
            raise ValueError("final outer must contain exactly one inner ZIP")
        data = outer.read(members[0])
    return {
        "outer_member": members[0],
        "filename": Path(members[0]).name,
        "bytes": len(data),
        "sha256": sha256_bytes(data),
    }


def source_identity_errors(source: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    commit = str(source.get("commit", ""))
    tree = str(source.get("tree", ""))
    if not HEX_40.fullmatch(commit):
        errors.append("source commit is invalid")
    if commit == BASE_COMMIT:
        errors.append("stale source commit selected for packaging")
    if not HEX_40.fullmatch(tree):
        errors.append("source tree is invalid")
    return errors


def environment_image_identity_errors(
    receipt: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    requested = str(receipt.get("requested_image_reference", ""))
    repo_digest = receipt.get("repo_digest")
    image_id = str(receipt.get("image_id", ""))
    inspected = str(receipt.get("inspected_digest", ""))
    execution = str(receipt.get("execution_image_reference", ""))
    if not requested:
        errors.append("requested image reference is missing")
    if repo_digest is not None and not (
        isinstance(repo_digest, str)
        and re.fullmatch(r".+@sha256:[0-9a-f]{64}", repo_digest)
    ):
        errors.append("RepoDigest is invalid")
    if not DIGEST.fullmatch(image_id):
        errors.append("inspected image ID is invalid")
    if not DIGEST.fullmatch(inspected):
        errors.append("inspected digest is invalid")
    if inspected != image_id:
        errors.append("inspected digest differs from image ID")
    if execution != inspected:
        errors.append("execution image reference differs from inspection")
    if receipt.get("image_digest") != inspected:
        errors.append("environment image digest differs from inspection")
    if receipt.get("image_identity_match") is not True:
        errors.append("environment image identity match is not true")
    return errors


def _artifact_errors(
    name: str,
    record: Mapping[str, Any],
    artifacts: Mapping[str, bytes] | None,
) -> list[str]:
    errors: list[str] = []
    expected_path = PACKAGED_PATHS[name]
    if record.get("path") != expected_path:
        errors.append(f"{name} packaged path differs")
    if not HEX_64.fullmatch(str(record.get("sha256", ""))):
        errors.append(f"{name} SHA-256 is invalid")
    if not isinstance(record.get("bytes"), int) or record.get("bytes", 0) < 1:
        errors.append(f"{name} byte count is invalid")
    if artifacts is not None:
        data = artifacts.get(expected_path)
        if data is None:
            errors.append(f"{name} artifact is missing")
        elif (
            len(data) != record.get("bytes")
            or sha256_bytes(data) != record.get("sha256")
        ):
            errors.append(f"{name} artifact identity differs")
    return errors


def release_descriptor_errors(
    descriptor: Mapping[str, Any],
    artifacts: Mapping[str, bytes] | None = None,
) -> list[str]:
    errors: list[str] = []
    if descriptor.get("task_id") != TASK_ID:
        errors.append("stale or unknown task ID selected for packaging")
    if descriptor.get("routing_nonce") != ROUTING_NONCE:
        errors.append("release routing nonce differs")
    if descriptor.get("status") != "passed":
        errors.append("release descriptor status is not passed")
    if descriptor.get("validation_errors") not in (None, []):
        errors.append("release descriptor contains validation errors")
    source = {
        "commit": descriptor.get("source_commit"),
        "tree": descriptor.get("source_tree"),
    }
    errors.extend(source_identity_errors(source))
    for name in PACKAGED_PATHS:
        record = descriptor.get(name)
        if not isinstance(record, Mapping):
            errors.append(f"{name} descriptor is missing")
            continue
        errors.extend(_artifact_errors(name, record, artifacts))
    userspace = descriptor.get("source_only_userspace", {})
    if not isinstance(userspace, Mapping):
        errors.append("source-only userspace identity is missing")
    else:
        image = str(userspace.get("image", ""))
        digest = str(userspace.get("digest", ""))
        if not re.fullmatch(r".+@sha256:[0-9a-f]{64}", image):
            errors.append("source-only userspace image is not fully pinned")
        if not DIGEST.fullmatch(digest) or not image.endswith(
            "@" + digest
        ):
            errors.append("source-only userspace digest differs from image")
    chromium = descriptor.get("chromium_identity", {})
    if not isinstance(chromium, Mapping):
        errors.append("Chromium identity is missing")
    else:
        if not str(chromium.get("version", "")).strip():
            errors.append("Chromium version is missing")
        if not str(chromium.get("executable", "")).strip():
            errors.append("Chromium executable is missing")
        if not HEX_64.fullmatch(str(chromium.get("sha256", ""))):
            errors.append("Chromium SHA-256 is missing")
    if descriptor.get("source_only_ci_status") != "passed":
        errors.append("source-only CI did not pass")
    if descriptor.get("source_only_browser_status") != "passed":
        errors.append("dashboard_browser result is missing")
    browser_result = descriptor.get("source_only_browser_result")
    if (
        not isinstance(browser_result, Mapping)
        or not HEX_64.fullmatch(
            str(browser_result.get("sha256", ""))
        )
    ):
        errors.append("source-only browser result identity is missing")
    if not HEX_64.fullmatch(
        str(descriptor.get("workflow_definition_sha256", ""))
    ):
        errors.append("workflow definition SHA-256 is missing")
    if not HEX_64.fullmatch(
        str(descriptor.get("source_only_command_plan_sha256", ""))
    ):
        errors.append("source-only command-plan SHA-256 is missing")
    if descriptor.get("debian_12_exact_final_status") != "passed":
        errors.append("Debian 12 exact-final proof did not pass")
    if descriptor.get("debian_13_exact_final_status") != "passed":
        errors.append("Debian 13 exact-final proof did not pass")
    if descriptor.get("portability_status") != "passed":
        errors.append("portability matrix did not pass")
    for name in ("final_outer", "final_inner"):
        identity = descriptor.get(name)
        if (
            not isinstance(identity, Mapping)
            or not HEX_64.fullmatch(str(identity.get("sha256", "")))
            or not isinstance(identity.get("bytes"), int)
            or identity.get("bytes", 0) < 1
        ):
            errors.append(f"{name} identity is invalid")
    expected_source = {
        "commit": descriptor.get("source_commit"),
        "tree": descriptor.get("source_tree"),
    }
    if descriptor.get("inner_handoff_source_identity") != expected_source:
        errors.append("inner handoff source identity differs")
    if descriptor.get("outer_delivery_source_identity") != expected_source:
        errors.append("outer delivery source identity differs")
    if artifacts is None:
        return errors
    try:
        task = json.loads(artifacts[PACKAGED_PATHS["task_receipt"]])
        source_ci = json.loads(
            artifacts[PACKAGED_PATHS["source_only_ci_receipt"]]
        )
        browser = json.loads(
            artifacts[PACKAGED_PATHS["source_only_browser"]]
        )
        debian_12 = json.loads(
            artifacts[PACKAGED_PATHS["exact_final_debian_12_receipt"]]
        )
        debian_13 = json.loads(
            artifacts[PACKAGED_PATHS["exact_final_debian_13_receipt"]]
        )
        validation = json.loads(
            artifacts[
                PACKAGED_PATHS["exact_final_independent_validation"]
            ]
        )
        matrix = json.loads(
            artifacts[PACKAGED_PATHS["portability_matrix"]]
        )
    except (KeyError, json.JSONDecodeError) as exc:
        errors.append(f"release artifact parsing failed: {exc}")
        return errors
    if task != TASK_RECEIPT:
        errors.append("task receipt does not equal the narrow task contract")
    errors.extend(source_only_receipt_errors(source_ci, browser))
    errors.extend(browser_receipt_errors(browser))
    if source_ci.get("source") != {
        **expected_source,
        "worktree_clean": True,
    }:
        errors.append("source-only CI source identity differs")
    if browser.get("source") != source_ci.get("source"):
        errors.append("source-only browser source identity differs")
    expected_userspace = {
        "image": source_ci.get("source_only_userspace_image"),
        "digest": source_ci.get(
            "source_only_userspace_image_digest"
        ),
        **(
            {
                "distribution": source_ci.get(
                    "source_only_distribution"
                ),
                "glibc": source_ci.get("source_only_glibc"),
            }
            if "distribution" in descriptor.get(
                "source_only_userspace", {}
            )
            else {}
        ),
    }
    if descriptor.get("source_only_userspace") != expected_userspace:
        errors.append("release descriptor userspace identity differs")
    expected_chromium = {
        "version": source_ci.get("chromium_version"),
        "executable": source_ci.get("chromium_executable"),
        "sha256": source_ci.get("chromium_executable_sha256"),
    }
    if descriptor.get("chromium_identity") != expected_chromium:
        errors.append("release descriptor Chromium identity differs")
    if descriptor.get("source_only_browser_result") != browser.get(
        "result"
    ):
        errors.append("release descriptor browser result differs")
    if descriptor.get("workflow_definition_sha256") != source_ci.get(
        "workflow_definition_sha256"
    ):
        errors.append("release descriptor workflow identity differs")
    if descriptor.get(
        "source_only_command_plan_sha256"
    ) != source_ci.get("command_plan", {}).get("sha256"):
        errors.append("release descriptor command-plan identity differs")
    for name, receipt, expected_distribution in (
        ("Debian 12", debian_12, "debian 12"),
        ("Debian 13", debian_13, "debian 13"),
    ):
        errors.extend(
            f"{name}: {error}"
            for error in environment_image_identity_errors(receipt)
        )
        if expected_distribution not in str(
            receipt.get("host_userspace_distribution", "")
        ).lower():
            errors.append(f"{name} userspace distribution differs")
        if receipt.get("source") != expected_source:
            errors.append(f"{name} source identity differs")
        if receipt.get("status") != "passed":
            errors.append(f"{name} exact-final status is not passed")
        if receipt.get("final_outer") != descriptor.get("final_outer"):
            errors.append(f"{name} final outer identity differs")
        if receipt.get("final_inner") != descriptor.get("final_inner"):
            errors.append(f"{name} final inner identity differs")
    if validation.get("status") != "passed":
        errors.append("exact-final validation status is not passed")
    if matrix.get("status") != "passed":
        errors.append("portability matrix status is not passed")
    if validation.get("source") != expected_source:
        errors.append("exact-final validation source differs")
    if matrix.get("source") != expected_source:
        errors.append("portability matrix source differs")
    environments = {
        row.get("name"): row
        for row in matrix.get("environments", [])
        if isinstance(row, Mapping)
    }
    for receipt in (debian_12, debian_13):
        if environments.get(receipt.get("name")) != receipt:
            errors.append(
                f"{receipt.get('name')} differs from portability matrix"
            )
    if descriptor.get("final_outer") != validation.get("final_outer"):
        errors.append("release descriptor outer identity differs")
    if descriptor.get("final_inner") != validation.get("final_inner"):
        errors.append("release descriptor inner identity differs")
    return errors


def package_origin_errors(
    origin: Mapping[str, Any],
    descriptor: Mapping[str, Any],
    descriptor_bytes: bytes,
) -> list[str]:
    errors: list[str] = []
    if origin.get("status") != "passed":
        errors.append("package origin status is not passed")
    if origin.get("task_id") != TASK_ID:
        errors.append("package origin task ID differs")
    if origin.get("routing_nonce") != ROUTING_NONCE:
        errors.append("package origin routing nonce differs")
    if origin.get("source_commit") != descriptor.get("source_commit"):
        errors.append("package origin source commit differs")
    if origin.get("source_tree") != descriptor.get("source_tree"):
        errors.append("package origin source tree differs")
    if origin.get("selection_mode") != "explicit_release_descriptor":
        errors.append("package origin selection mode is not explicit")
    if origin.get("latest_output_selected") is not False:
        errors.append("package origin selected latest output")
    if origin.get("modification_time_selection_used") is not False:
        errors.append("package origin used modification time")
    if origin.get("prior_response_task_inference_used") is not False:
        errors.append("package origin inferred a task from prior response")
    if origin.get("selected_receipts") != {
        name: descriptor.get(name) for name in PACKAGED_PATHS
    }:
        errors.append("package origin selected receipt identities differ")
    if origin.get("validation_errors") not in (None, []):
        errors.append("package origin contains validation errors")
    record = origin.get("release_descriptor", {})
    if (
        record.get("path") != "release-descriptor.json"
        or record.get("bytes") != len(descriptor_bytes)
        or record.get("sha256") != sha256_bytes(descriptor_bytes)
    ):
        errors.append("package origin descriptor identity differs")
    return errors


def build_release_descriptor(
    *,
    task_receipt_path: Path,
    source_only_ci_path: Path,
    source_only_browser_path: Path,
    debian_12_path: Path,
    debian_13_path: Path,
    validation_path: Path,
    portability_matrix_path: Path,
    outer_path: Path,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    paths = {
        "task_receipt": task_receipt_path,
        "source_only_ci_receipt": source_only_ci_path,
        "source_only_browser": source_only_browser_path,
        "exact_final_debian_12_receipt": debian_12_path,
        "exact_final_debian_13_receipt": debian_13_path,
        "exact_final_independent_validation": validation_path,
        "portability_matrix": portability_matrix_path,
    }
    artifacts = {
        PACKAGED_PATHS[name]: path.read_bytes()
        for name, path in paths.items()
    }
    task = _read_json(task_receipt_path)
    source_ci = _read_json(source_only_ci_path)
    browser = _read_json(source_only_browser_path)
    debian_12 = _read_json(debian_12_path)
    debian_13 = _read_json(debian_13_path)
    validation = _read_json(validation_path)
    matrix = _read_json(portability_matrix_path)
    if task != TASK_RECEIPT:
        raise ValueError("task receipt is not the exact narrow contract")
    source = {
        "commit": source_ci.get("source", {}).get("commit"),
        "tree": source_ci.get("source", {}).get("tree"),
    }
    actual_outer = outer_identity(outer_path)
    actual_inner = inner_identity(outer_path)
    descriptor = {
        "schema_id": "final-source-only-release-descriptor-current",
        "status": "passed",
        "task_id": TASK_ID,
        "routing_nonce": ROUTING_NONCE,
        "source_commit": source["commit"],
        "source_tree": source["tree"],
        **{
            name: artifact_record(path, PACKAGED_PATHS[name])
            for name, path in paths.items()
        },
        "source_only_userspace": {
            "image": source_ci.get("source_only_userspace_image"),
            "digest": source_ci.get(
                "source_only_userspace_image_digest"
            ),
            "distribution": source_ci.get(
                "source_only_distribution"
            ),
            "glibc": source_ci.get("source_only_glibc"),
        },
        "chromium_identity": {
            "version": source_ci.get("chromium_version"),
            "executable": source_ci.get("chromium_executable"),
            "sha256": source_ci.get(
                "chromium_executable_sha256"
            ),
        },
        "workflow_definition_sha256": source_ci.get(
            "workflow_definition_sha256"
        ),
        "source_only_command_plan_sha256": source_ci.get(
            "command_plan", {}
        ).get("sha256"),
        "source_only_browser_result": browser.get("result"),
        "source_only_ci_status": source_ci.get("status"),
        "source_only_browser_status": browser.get("status"),
        "debian_12_exact_final_status": debian_12.get("status"),
        "debian_13_exact_final_status": debian_13.get("status"),
        "portability_status": matrix.get("status"),
        "final_outer": actual_outer,
        "final_inner": validation.get("final_inner"),
        "inner_handoff_source_identity": source,
        "outer_delivery_source_identity": source,
        "selection_guards": {
            "explicit_descriptor_required": True,
            "latest_output_selection_forbidden": True,
            "modification_time_selection_forbidden": True,
            "stale_task_rejected": True,
            "stale_source_rejected": True,
            "old_debian_13_identity_rejected": True,
        },
        "validation_errors": [],
    }
    if actual_outer != validation.get("final_outer"):
        descriptor["validation_errors"].append(
            "outer differs from exact-final validation"
        )
    validation_inner = validation.get("final_inner", {})
    for field in ("outer_member", "filename", "bytes", "sha256"):
        if actual_inner.get(field) != validation_inner.get(field):
            descriptor["validation_errors"].append(
                "inner differs from exact-final validation"
            )
            break
    descriptor["validation_errors"].extend(
        release_descriptor_errors(descriptor, artifacts)
    )
    if descriptor["validation_errors"]:
        descriptor["status"] = "failed"
    return descriptor, artifacts


def descriptor_markdown(value: Mapping[str, Any]) -> str:
    userspace = value.get("source_only_userspace", {})
    chromium = value.get("chromium_identity", {})
    lines = [
        "# Final source-only release descriptor",
        "",
        f"Status: `{value.get('status')}`",
        "",
        f"- Task ID: `{value.get('task_id')}`",
        f"- Routing nonce: `{value.get('routing_nonce')}`",
        f"- Source commit: `{value.get('source_commit')}`",
        f"- Source tree: `{value.get('source_tree')}`",
        f"- Source-only image: `{userspace.get('image')}`",
        f"- Source-only image digest: `{userspace.get('digest')}`",
        f"- Chromium: `{chromium.get('version')}`",
        f"- Chromium SHA-256: `{chromium.get('sha256')}`",
        f"- Source-only CI: `{value.get('source_only_ci_status')}`",
        f"- Source-only browser: "
        f"`{value.get('source_only_browser_status')}`",
        f"- Debian 12 exact-final: "
        f"`{value.get('debian_12_exact_final_status')}`",
        f"- Debian 13 exact-final: "
        f"`{value.get('debian_13_exact_final_status')}`",
        f"- Final outer SHA-256: "
        f"`{value.get('final_outer', {}).get('sha256')}`",
        f"- Final inner SHA-256: "
        f"`{value.get('final_inner', {}).get('sha256')}`",
        "",
    ]
    if value.get("validation_errors"):
        lines.extend(
            ["## Validation errors", ""]
            + [
                f"- {error}"
                for error in value.get("validation_errors", [])
            ]
            + [""]
        )
    return "\n".join(lines)


def build_package_origin(
    descriptor: Mapping[str, Any],
    descriptor_bytes: bytes,
) -> dict[str, Any]:
    origin = {
        "schema_id": "package-origin-current",
        "status": "passed",
        "task_id": descriptor.get("task_id"),
        "routing_nonce": descriptor.get("routing_nonce"),
        "source_commit": descriptor.get("source_commit"),
        "source_tree": descriptor.get("source_tree"),
        "selection_mode": "explicit_release_descriptor",
        "release_descriptor": {
            "path": "release-descriptor.json",
            "bytes": len(descriptor_bytes),
            "sha256": sha256_bytes(descriptor_bytes),
        },
        "latest_output_selected": False,
        "modification_time_selection_used": False,
        "prior_response_task_inference_used": False,
        "selected_receipts": {
            name: descriptor.get(name)
            for name in PACKAGED_PATHS
        },
        "validation_errors": [],
    }
    origin["validation_errors"] = package_origin_errors(
        origin, descriptor, descriptor_bytes
    )
    if origin["validation_errors"]:
        origin["status"] = "failed"
    return origin


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--task-receipt", type=Path, required=True)
    build.add_argument("--source-only-ci", type=Path, required=True)
    build.add_argument("--source-only-browser", type=Path, required=True)
    build.add_argument("--debian-12", type=Path, required=True)
    build.add_argument("--debian-13", type=Path, required=True)
    build.add_argument("--validation", type=Path, required=True)
    build.add_argument("--portability-matrix", type=Path, required=True)
    build.add_argument("--outer", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--markdown-output", type=Path, required=True)
    build.add_argument("--package-origin-output", type=Path, required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--descriptor", type=Path, required=True)
    validate.add_argument("--artifact-root", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "build":
        descriptor, _ = build_release_descriptor(
            task_receipt_path=args.task_receipt.resolve(),
            source_only_ci_path=args.source_only_ci.resolve(),
            source_only_browser_path=args.source_only_browser.resolve(),
            debian_12_path=args.debian_12.resolve(),
            debian_13_path=args.debian_13.resolve(),
            validation_path=args.validation.resolve(),
            portability_matrix_path=args.portability_matrix.resolve(),
            outer_path=args.outer.resolve(),
        )
        write_json(args.output.resolve(), descriptor)
        args.markdown_output.resolve().write_text(
            descriptor_markdown(descriptor), encoding="utf-8"
        )
        descriptor_bytes = args.output.resolve().read_bytes()
        origin = build_package_origin(descriptor, descriptor_bytes)
        write_json(args.package_origin_output.resolve(), origin)
        result = {
            "status": (
                "passed"
                if descriptor["status"] == "passed"
                and origin["status"] == "passed"
                else "failed"
            ),
            "descriptor": descriptor,
            "package_origin": origin,
        }
    else:
        descriptor_path = args.descriptor.resolve()
        descriptor = _read_json(descriptor_path)
        artifact_root = args.artifact_root.resolve()
        artifacts = {
            path: (artifact_root / path).read_bytes()
            for path in PACKAGED_PATHS.values()
            if (artifact_root / path).is_file()
        }
        errors = release_descriptor_errors(descriptor, artifacts)
        result = {
            "status": "passed" if not errors else "failed",
            "errors": errors,
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
