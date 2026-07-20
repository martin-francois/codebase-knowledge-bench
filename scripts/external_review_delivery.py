#!/usr/bin/env python3
"""Build and validate the single upload-ready external-review delivery ZIP."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import mimetypes
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from build_review_handoff import scan_text, validate as validate_handoff, write_zip
from safe_archive import safe_extract_zip


DELIVERY_ZIP_MAX_MEMBERS = 12
DELIVERY_ZIP_MAX_MEMBER_BYTES = 1_500_000_000
DELIVERY_ZIP_MAX_TOTAL_BYTES = 1_600_000_000
DELIVERY_ZIP_MAX_COMPRESSION_RATIO = 200


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def published_sha256(value: Any) -> str:
    return sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def media_type(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def validate_detached_binding(inner_name: str, inner_data: bytes, checksum_text: str, receipt: dict[str, Any]) -> dict[str, Any]:
    inner_hash = sha256_bytes(inner_data)
    receipt_name = Path(str(receipt.get("review_zip_path") or receipt.get("zip_path") or "")).name
    receipt_hash = receipt.get("review_zip_sha256", receipt.get("zip_sha256"))
    receipt_bytes = receipt.get("review_zip_bytes", receipt.get("zip_bytes"))
    passed = (
        checksum_text.strip().split()[0] == inner_hash
        and receipt_name == Path(inner_name).name
        and receipt_hash == inner_hash
        and receipt_bytes == len(inner_data)
    )
    return {"status": "passed" if passed else "failed", "inner_name": inner_name, "inner_sha256": inner_hash, "receipt_name": receipt_name}


def _payload(inner_zip: Path, checksum: Path, receipt: Path, agent_response: Path) -> dict[str, bytes]:
    if inner_zip.name != "review-handoff.zip":
        raise ValueError("inner review package must be named review-handoff.zip")
    if checksum.name != "review-handoff.zip.sha256":
        raise ValueError("inner detached checksum must be named review-handoff.zip.sha256")
    if receipt.name != "review-handoff.zip.validation.json":
        raise ValueError("inner validation receipt must be named review-handoff.zip.validation.json")
    prefix = "review-handoff/"
    with zipfile.ZipFile(inner_zip) as archive:
        verifier_launcher = archive.read(
            "verification/independent-verifier/independent_verifier.sh"
        )
        verifier_bootstrap = archive.read(
            "verification/independent-verifier/"
            "independent-verifier-bootstrap"
        )
        verifier_bootstrap_checksum = archive.read(
            "verification/independent-verifier/"
            "independent-verifier-bootstrap.sha256"
        )
    return {
        prefix + inner_zip.name: inner_zip.read_bytes(),
        prefix + checksum.name: checksum.read_bytes(),
        prefix + receipt.name: receipt.read_bytes(),
        "agent-response.md": agent_response.read_bytes(),
        "independent-verifier.sh": verifier_launcher,
        "independent-verifier-bootstrap": verifier_bootstrap,
        "independent-verifier-bootstrap.sha256":
            verifier_bootstrap_checksum,
    }


def build(inner_zip: Path, checksum: Path, receipt: Path, agent_response: Path, output: Path) -> tuple[Path, dict[str, Any]]:
    members = _payload(inner_zip, checksum, receipt, agent_response)
    entries = [
        {"path": name, "bytes": len(data), "sha256": sha256_bytes(data),
         "media_type": media_type(name), "role": "agent-response" if name == "agent-response.md" else "inner-review-handoff",
         "source": "generated-or-content-addressed", "required": True}
        for name, data in sorted(members.items())
    ]
    manifest = {
        "schema_id": "external-review-delivery-manifest-current",
        "entries": entries,
        "entry_count": len(entries),
        "manifest_root": published_sha256(entries),
    }
    detailed = json.loads(receipt.read_text(encoding="utf-8"))
    inner_member = "review-handoff/" + inner_zip.name
    with zipfile.ZipFile(io.BytesIO(members[inner_member])) as inner_archive:
        response_matches = inner_archive.read("agent-response.md") == members["agent-response.md"]
        verifier_matches = (
            inner_archive.read(
                "verification/independent-verifier/independent_verifier.sh"
            )
            == members["independent-verifier.sh"]
        )
        bootstrap_matches = (
            inner_archive.read(
                "verification/independent-verifier/"
                "independent-verifier-bootstrap"
            )
            == members["independent-verifier-bootstrap"]
            and inner_archive.read(
                "verification/independent-verifier/"
                "independent-verifier-bootstrap.sha256"
            )
            == members["independent-verifier-bootstrap.sha256"]
        )
    binding = validate_detached_binding(inner_zip.name, members[inner_member], checksum.read_text(encoding="utf-8"), detailed)
    if (
        not response_matches
        or not verifier_matches
        or not bootstrap_matches
        or binding["status"] != "passed"
    ):
        raise ValueError(
            "inner response, verifier, or detached sidecar binding is invalid"
        )
    inner_status = detailed.get("overall_status", detailed.get("status"))
    validation = {
        "schema_id": "external-review-delivery-validation-current",
        "inner_review_zip_name": inner_zip.name,
        "inner_review_zip_sha256": sha256_bytes(inner_zip.read_bytes()),
        "inner_review_zip_bytes": inner_zip.stat().st_size,
        "inner_detailed_validation_status": detailed.get("overall_status", detailed.get("status")),
        "delivery_manifest_count": manifest["entry_count"],
        "delivery_manifest_root": manifest["manifest_root"],
        "required_sidecars_present": True,
        "receipt_bound_to_inner_zip": Path(str(detailed.get("review_zip_path") or detailed.get("zip_path") or "")).name == inner_zip.name,
        "agent_response_matches_inner": response_matches,
        "verifier_launcher_matches_inner": verifier_matches,
        "static_verifier_bootstrap_matches_inner": bootstrap_matches,
        "overall_status": "passed" if inner_status == "passed" else "NO_GO",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(members.items()):
            write_zip(
                archive,
                name,
                data,
                mode=(
                    0o755
                    if name
                    in {
                        "independent-verifier-bootstrap",
                        "independent-verifier.sh",
                    }
                    else 0o644
                ),
            )
        write_zip(archive, "delivery-manifest.json", json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n")
        write_zip(archive, "delivery-validation.json", json.dumps(validation, indent=2, sort_keys=True).encode() + b"\n")
    observed = validate(output)
    return output, observed


def validate(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        roots = {
            "delivery-manifest.json",
            "delivery-validation.json",
            "agent-response.md",
            "independent-verifier.sh",
            "independent-verifier-bootstrap",
            "independent-verifier-bootstrap.sha256",
        }
        inner = sorted(name for name in names if name.startswith("review-handoff/") and name.endswith(".zip"))
        if len(inner) != 1:
            raise ValueError("delivery must contain exactly one inner review ZIP")
        inner_name = inner[0]
        required = roots | {inner_name, inner_name + ".sha256", inner_name + ".validation.json"}
        if names != required:
            raise ValueError(f"delivery member mismatch: missing={sorted(required - names)} extra={sorted(names - required)}")
        for executable in (
            "independent-verifier-bootstrap",
            "independent-verifier.sh",
        ):
            if (
                archive.getinfo(executable).external_attr >> 16
            ) & 0o7777 != 0o755:
                raise ValueError(
                    f"outer executable mode mismatch: {executable}"
                )
        manifest = json.loads(archive.read("delivery-manifest.json"))
        validation = json.loads(archive.read("delivery-validation.json"))
        entries = manifest["entries"]
        if manifest["entry_count"] != len(entries) or manifest["manifest_root"] != published_sha256(entries):
            raise ValueError("delivery manifest count or root mismatch")
        for entry in entries:
            data = archive.read(entry["path"])
            if len(data) != entry["bytes"] or sha256_bytes(data) != entry["sha256"]:
                raise ValueError(f"delivery member hash mismatch: {entry['path']}")
        inner_data = archive.read(inner_name)
        checksum_text = archive.read(inner_name + ".sha256").decode().strip().split()[0]
        inner_hash = sha256_bytes(inner_data)
        if checksum_text != inner_hash:
            raise ValueError("detached checksum does not match inner review ZIP")
        receipt = json.loads(archive.read(inner_name + ".validation.json"))
        binding = validate_detached_binding(Path(inner_name).name, inner_data, checksum_text, receipt)
        if binding["status"] != "passed":
            raise ValueError("detailed validation receipt or checksum does not bind inner ZIP")
        with zipfile.ZipFile(io.BytesIO(inner_data)) as inner_archive:
            inner_response = inner_archive.read("agent-response.md")
            inner_verifier = inner_archive.read(
                "verification/independent-verifier/independent_verifier.sh"
            )
            inner_bootstrap = inner_archive.read(
                "verification/independent-verifier/"
                "independent-verifier-bootstrap"
            )
            inner_bootstrap_checksum = inner_archive.read(
                "verification/independent-verifier/"
                "independent-verifier-bootstrap.sha256"
            )
        outer_response = archive.read("agent-response.md")
        if outer_response != inner_response:
            raise ValueError("outer and inner agent responses differ")
        if archive.read("independent-verifier.sh") != inner_verifier:
            raise ValueError(
                "outer and inner independent verifier launchers differ"
            )
        if (
            archive.read("independent-verifier-bootstrap")
            != inner_bootstrap
            or archive.read("independent-verifier-bootstrap.sha256")
            != inner_bootstrap_checksum
        ):
            raise ValueError(
                "outer and inner static verifier bootstrap artifacts differ"
            )
        scan_errors = scan_text("agent-response.md", outer_response)
        if scan_errors:
            raise ValueError(f"delivery response scan failed: {scan_errors}")
        with tempfile.TemporaryDirectory(prefix="external-review-delivery-") as temporary:
            extracted = Path(temporary)
            safe_extract_zip(
                archive,
                extracted,
                max_members=DELIVERY_ZIP_MAX_MEMBERS,
                max_member_bytes=DELIVERY_ZIP_MAX_MEMBER_BYTES,
                max_total_bytes=DELIVERY_ZIP_MAX_TOTAL_BYTES,
                max_compression_ratio=(
                    DELIVERY_ZIP_MAX_COMPRESSION_RATIO
                ),
            )
            handoff_result = validate_handoff(extracted / inner_name)
    if validation.get("inner_review_zip_name") != Path(inner_name).name:
        raise ValueError("delivery validation names another inner ZIP")
    return {
        "schema_id": "external-review-delivery-validation-current",
        "delivery_zip_path": str(path),
        "delivery_zip_bytes": path.stat().st_size,
        "delivery_zip_sha256": sha256_bytes(path.read_bytes()),
        "delivery_manifest_count": manifest["entry_count"],
        "delivery_manifest_root": manifest["manifest_root"],
        "inner_review_zip_name": Path(inner_name).name,
        "inner_review_zip_sha256": inner_hash,
        "inner_manifest_count": handoff_result.get("manifest_entry_count"),
        "inner_manifest_root": handoff_result.get("manifest_root"),
        "inner_validation": handoff_result,
        "detailed_receipt_status": receipt.get("overall_status", receipt.get("status")),
        "agent_response_matches_inner": True,
        "verifier_launcher_matches_inner": True,
        "static_verifier_bootstrap_matches_inner": True,
        "secret_scan": "passed",
        "host_path_scan": "passed",
        "outer_extraction_validation": "passed",
        "inner_extraction_validation": handoff_result.get("status"),
        "identity_wording": {
            "outer": "delivery_zip_sha256 identifies the outer upload delivery ZIP after construction",
            "inner": "inner_review_zip_sha256 identifies the nested review-handoff ZIP",
        },
        "overall_status": "passed",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build")
    for name in ("inner_zip", "checksum", "receipt", "agent_response", "output"):
        build_parser.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.command == "build":
        _, result = build(args.inner_zip, args.checksum, args.receipt, args.agent_response, args.output)
    else:
        result = validate(args.path)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
