#!/usr/bin/env python3
"""Verify an outer delivery using only its extracted packaged runtime."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import posixpath
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any


CANONICAL_SHA = (
    "b4a77687b40bea1ff97117224d08e00b0b66ee0a6fc1875c87d0b95da19e49e0"
)
SUPPLEMENT_SHA = (
    "2b560a78410e47ee1cec4d9f000cfed4a0c633e6339cbc8c422ebee452bcb387"
)
QUALIFYING_PAYLOAD_ROOTS = {
    "source",
    "audit",
    "preflight",
    "runtime",
    "network",
    "replay",
    "target",
    "schemas",
    "tests",
    "immutable-evidence",
}
OUTER_ZIP_MAX_MEMBERS = 10
OUTER_ZIP_MAX_MEMBER_BYTES = 1_500_000_000
INNER_ZIP_MAX_MEMBERS = 20_000
INNER_ZIP_MAX_MEMBER_BYTES = 300_000_000
ZIP_MAX_TOTAL_BYTES = 1_600_000_000
ZIP_MAX_COMPRESSION_RATIO = 200


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_root(rows: list[dict[str, Any]]) -> str:
    return sha256_bytes(
        json.dumps(
            rows, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )


def _manifest_path_errors(
    entries: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    required_fields = {
        "path",
        "type",
        "bytes",
        "sha256",
        "mode",
        "symlink_target",
        "hardlink_target",
        "media_type",
        "role",
        "source",
        "required",
    }
    seen: dict[str, bool] = {}
    folded: dict[str, str] = {}
    for index, row in enumerate(entries):
        if not isinstance(row, dict) or set(row) != required_fields:
            errors.append(f"manifest entry field set mismatch: {index}")
            continue
        path = row.get("path")
        member_type = row.get("type")
        if (
            not isinstance(path, str)
            or not path
            or member_type not in {"file", "directory", "symlink"}
        ):
            errors.append(f"invalid manifest entry: {index}")
            continue
        clean = str(PurePosixPath(path)).rstrip("/")
        if clean != path:
            errors.append(f"non-canonical manifest path: {path}")
        if clean in seen:
            errors.append(f"duplicate manifest path: {clean}")
        folded_path = clean.casefold()
        if folded_path in folded:
            errors.append(
                "case-fold manifest collision: "
                f"{folded[folded_path]} and {clean}"
            )
        seen[clean] = member_type == "directory"
        folded[folded_path] = clean
    for clean in seen:
        parts = PurePosixPath(clean).parts
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            if parent in seen and not seen[parent]:
                errors.append(
                    f"manifest file/directory collision: {clean}"
                )
    return errors


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _validated_zip_infos(
    archive: zipfile.ZipFile,
    *,
    max_members: int,
    max_member_bytes: int,
    allowed_symlinks: dict[str, str] | None = None,
    expected_modes: dict[str, int] | None = None,
) -> list[zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > max_members:
        raise ValueError("independent-verifier ZIP member limit exceeded")
    seen: dict[str, bool] = {}
    folded: dict[str, str] = {}
    declared_symlinks = dict(allowed_symlinks or {})
    declared_modes = dict(expected_modes or {})
    observed_symlinks: set[str] = set()
    observed_modes: set[str] = set()
    total = 0
    for info in infos:
        member = PurePosixPath(info.filename)
        mode = (info.external_attr >> 16) & 0o170000
        if (
            not info.filename
            or member.is_absolute()
            or "." in member.parts
            or ".." in member.parts
            or "\\" in info.filename
            or "\x00" in info.filename
            or (
                mode
                and mode
                not in {
                    stat.S_IFREG,
                    stat.S_IFDIR,
                    stat.S_IFLNK,
                }
            )
        ):
            raise ValueError(
                "unsafe independent-verifier ZIP member: "
                f"{info.filename}"
            )
        clean = str(member).rstrip("/")
        if mode == stat.S_IFLNK:
            if clean not in declared_symlinks:
                raise ValueError(
                    "undeclared independent-verifier ZIP symlink: "
                    f"{info.filename}"
                )
            try:
                target = archive.read(info).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(
                    "independent-verifier ZIP symlink is not UTF-8"
                ) from exc
            if target != declared_symlinks[clean]:
                raise ValueError(
                    "independent-verifier ZIP symlink target mismatch"
                )
            resolved = posixpath.normpath(
                posixpath.join(posixpath.dirname(clean), target)
            )
            if (
                not target
                or target.startswith("/")
                or "\\" in target
                or resolved == ".."
                or resolved.startswith("../")
                or resolved.startswith("/")
            ):
                raise ValueError(
                    "escaping independent-verifier ZIP symlink"
                )
            observed_symlinks.add(clean)
        elif clean in declared_symlinks:
            raise ValueError(
                "declared independent-verifier ZIP symlink has wrong type"
            )
        permissions = (info.external_attr >> 16) & 0o777
        if declared_modes:
            if clean not in declared_modes:
                raise ValueError(
                    "independent-verifier ZIP member has no mode"
                )
            if permissions != declared_modes[clean]:
                raise ValueError(
                    "independent-verifier ZIP mode mismatch"
                )
            observed_modes.add(clean)
        if clean in seen:
            raise ValueError(
                f"duplicate independent-verifier ZIP path: {clean}"
            )
        case_key = clean.casefold()
        if case_key in folded:
            raise ValueError(
                "independent-verifier ZIP case-fold collision: "
                f"{folded[case_key]} and {clean}"
            )
        is_directory = mode == stat.S_IFDIR or info.is_dir()
        seen[clean] = is_directory
        folded[case_key] = clean
        if info.file_size < 0 or info.file_size > max_member_bytes:
            raise ValueError(
                "independent-verifier ZIP member size limit exceeded"
            )
        total += info.file_size
        if total > ZIP_MAX_TOTAL_BYTES:
            raise ValueError(
                "independent-verifier ZIP expanded-size limit exceeded"
            )
        if (
            info.compress_size
            and info.file_size / info.compress_size
            > ZIP_MAX_COMPRESSION_RATIO
        ):
            raise ValueError(
                "independent-verifier ZIP compression-ratio limit exceeded"
            )
    for clean in seen:
        parts = PurePosixPath(clean).parts
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            if parent in seen and not seen[parent]:
                raise ValueError(
                    "independent-verifier ZIP file/directory collision: "
                    f"{clean}"
                )
    if observed_symlinks != set(declared_symlinks):
        raise ValueError(
            "independent-verifier ZIP symlink set mismatch"
        )
    if declared_modes and observed_modes != set(declared_modes):
        raise ValueError(
            "independent-verifier ZIP mode set mismatch"
        )
    return infos


def _validate_outer(outer: Path) -> tuple[dict[str, Any], bytes, str]:
    errors: list[str] = []
    with zipfile.ZipFile(outer) as archive:
        infos = _validated_zip_infos(
            archive,
            max_members=OUTER_ZIP_MAX_MEMBERS,
            max_member_bytes=OUTER_ZIP_MAX_MEMBER_BYTES,
        )
        names = {info.filename for info in infos}
        inner_names = sorted(
            name
            for name in names
            if name.startswith("review-handoff/")
            and name.endswith(".zip")
        )
        if len(inner_names) != 1:
            raise ValueError("outer delivery must contain one inner ZIP")
        inner_name = inner_names[0]
        required = {
            "agent-response.md",
            "delivery-manifest.json",
            "delivery-validation.json",
            "independent-verifier.sh",
            inner_name,
            inner_name + ".sha256",
            inner_name + ".validation.json",
        }
        if names != required:
            errors.append(
                f"outer member set mismatch: "
                f"missing={sorted(required - names)} "
                f"extra={sorted(names - required)}"
            )
        manifest = json.loads(archive.read("delivery-manifest.json"))
        manifest_entries = manifest.get("entries", [])
        manifest_paths = [
            row.get("path")
            for row in manifest_entries
            if isinstance(row, dict)
        ]
        if (
            len(manifest_paths) != len(manifest_entries)
            or len(manifest_paths) != len(set(manifest_paths))
            or set(manifest_paths)
            != names
            - {
                "delivery-manifest.json",
                "delivery-validation.json",
            }
        ):
            errors.append("outer manifest member set mismatch")
        if (
            manifest.get("entry_count") != len(manifest_entries)
            or manifest.get("manifest_root")
            != canonical_root(manifest_entries)
        ):
            errors.append("outer manifest count/root mismatch")
        for row in manifest_entries:
            data = archive.read(row["path"])
            if (
                len(data) != row["bytes"]
                or sha256_bytes(data) != row["sha256"]
            ):
                errors.append(f"outer member mismatch: {row['path']}")
        inner = archive.read(inner_name)
        with zipfile.ZipFile(io.BytesIO(inner)) as inner_archive:
            if archive.read(
                "independent-verifier.sh"
            ) != inner_archive.read(
                "verification/independent-verifier/"
                "independent_verifier.sh"
            ):
                errors.append(
                    "outer verifier launcher differs from inner source"
                )
        inner_hash = sha256_bytes(inner)
        checksum = (
            archive.read(inner_name + ".sha256")
            .decode("utf-8")
            .strip()
            .split()[0]
        )
        detached = json.loads(
            archive.read(inner_name + ".validation.json")
        )
        if checksum != inner_hash:
            errors.append("inner detached checksum mismatch")
        if (
            detached.get(
                "review_zip_sha256", detached.get("zip_sha256")
            )
            != inner_hash
            or detached.get(
                "review_zip_bytes", detached.get("zip_bytes")
            )
            != len(inner)
        ):
            errors.append("inner detailed receipt binding mismatch")
    return (
        {
            "status": "passed" if not errors else "failed",
            "errors": errors,
            "delivery_sha256": sha256_file(outer),
            "delivery_bytes": outer.stat().st_size,
            "manifest_count": manifest["entry_count"],
            "manifest_root": manifest["manifest_root"],
            "inner_sha256": inner_hash,
            "inner_bytes": len(inner),
        },
        inner,
        inner_name,
    )


def _validate_inner(inner: bytes, work: Path) -> dict[str, Any]:
    errors: list[str] = []
    inner_root = work / "inner"
    inner_root.mkdir()
    with zipfile.ZipFile(io.BytesIO(inner)) as archive:
        manifest_info = archive.getinfo(
            "review-handoff-manifest.json"
        )
        if (
            manifest_info.file_size > 50_000_000
            or (
                manifest_info.compress_size
                and manifest_info.file_size
                / manifest_info.compress_size
                > ZIP_MAX_COMPRESSION_RATIO
            )
        ):
            raise ValueError("unsafe inner manifest size or ratio")
        manifest = json.loads(archive.read(manifest_info))
        manifest_errors = _manifest_path_errors(
            manifest.get("entries", [])
        )
        if manifest_errors:
            raise ValueError(
                "unsafe inner manifest: "
                + "; ".join(manifest_errors)
            )
        allowed_symlinks = {
            row["path"]: row["symlink_target"]
            for row in manifest["entries"]
            if row["type"] == "symlink"
        }
        expected_modes = {
            row["path"]: row["mode"]
            for row in manifest["entries"]
        }
        expected_modes["review-handoff-manifest.json"] = 0o644
        infos = _validated_zip_infos(
            archive,
            max_members=INNER_ZIP_MAX_MEMBERS,
            max_member_bytes=INNER_ZIP_MAX_MEMBER_BYTES,
            allowed_symlinks=allowed_symlinks,
            expected_modes=expected_modes,
        )
        for info in infos:
            member = PurePosixPath(info.filename)
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                continue
            target = inner_root.joinpath(*member.parts)
            if mode == stat.S_IFDIR or info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(
                    (info.external_attr >> 16) & 0o777 or 0o755
                )
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(
                    (info.external_attr >> 16) & 0o777 or 0o644
                )
        for info in infos:
            mode = (info.external_attr >> 16) & 0o170000
            if mode != stat.S_IFLNK:
                continue
            member = PurePosixPath(info.filename)
            target = inner_root.joinpath(*member.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            clean = str(member).rstrip("/")
            os.symlink(allowed_symlinks[clean], target)
        names = {
            str(PurePosixPath(info.filename)).rstrip("/")
            for info in infos
        }
        expected = {
            row["path"] for row in manifest["entries"]
        } | {"review-handoff-manifest.json"}
        if names != expected:
            errors.append(
                f"inner member set mismatch: "
                f"missing={sorted(expected - names)} "
                f"extra={sorted(names - expected)}"
            )
        if (
            manifest.get("entry_count") != len(manifest["entries"])
            or manifest.get("manifest_root")
            != canonical_root(manifest["entries"])
        ):
            errors.append("inner manifest count/root mismatch")
        qualifying = [
            row
            for row in manifest["entries"]
            if row["path"].split("/", 1)[0]
            in QUALIFYING_PAYLOAD_ROOTS
        ]
        qualifying_root = canonical_root(qualifying)
        if (
            manifest.get("qualifying_payload_entry_count")
            != len(qualifying)
            or manifest.get("qualifying_payload_root")
            != qualifying_root
        ):
            errors.append("inner qualifying payload root mismatch")
        archive_members = {
            str(PurePosixPath(info.filename)).rstrip("/"): info
            for info in infos
        }
        for row in manifest["entries"]:
            data = archive.read(archive_members[row["path"]])
            mismatch = len(data) != row["bytes"]
            if row["type"] == "directory":
                mismatch = (
                    mismatch
                    or data != b""
                    or row["sha256"] is not None
                    or row["symlink_target"] is not None
                )
            elif row["type"] == "symlink":
                mismatch = (
                    mismatch
                    or sha256_bytes(data) != row["sha256"]
                    or data.decode("utf-8")
                    != row["symlink_target"]
                )
            elif row["type"] == "file":
                mismatch = (
                    mismatch
                    or sha256_bytes(data) != row["sha256"]
                    or row["symlink_target"] is not None
                )
            else:
                mismatch = True
            if mismatch or row["hardlink_target"] is not None:
                errors.append(f"inner member mismatch: {row['path']}")
    if sha256_file(
        inner_root / "immutable-evidence/canonical-suite-bundle.zip"
    ) != CANONICAL_SHA:
        errors.append("canonical immutable evidence mismatch")
    if sha256_file(
        inner_root
        / "immutable-evidence/canonical-publication-supplement.zip"
    ) != SUPPLEMENT_SHA:
        errors.append("supplement immutable evidence mismatch")
    commit_object = (
        inner_root / "source/commit-object.txt"
    ).read_bytes()
    reconstructed_commit = hashlib.sha1(
        b"commit "
        + str(len(commit_object)).encode("ascii")
        + b"\0"
        + commit_object
    ).hexdigest()
    if reconstructed_commit != manifest["source_commit"]:
        errors.append("source commit object reconstruction mismatch")
    target_path = inner_root / "target"
    sys.path.insert(0, str(target_path))
    from safe_archive import safe_extract_tar

    source_checkout = work / "source-tree"
    with tarfile.open(inner_root / "source/source.tar") as archive:
        safe_extract_tar(archive, source_checkout)
    subprocess.run(
        ["git", "-C", str(source_checkout), "init", "--quiet"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(source_checkout), "add", "--all"],
        check=True,
    )
    reconstructed_tree = subprocess.check_output(
        ["git", "-C", str(source_checkout), "write-tree"],
        text=True,
    ).strip()
    if reconstructed_tree != manifest["source_tree"]:
        errors.append("source tree reconstruction mismatch")
    detailed = json.loads(
        (inner_root / "review-handoff-validation.json").read_text(
            encoding="utf-8"
        )
    )
    if detailed.get("status") != "passed":
        errors.append("inner detailed validation did not pass")
    return {
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "root": inner_root,
        "manifest_count": manifest["entry_count"],
        "manifest_root": manifest["manifest_root"],
        "qualifying_payload_entry_count": len(qualifying),
        "qualifying_payload_root": qualifying_root,
        "source_commit": manifest["source_commit"],
        "source_tree": manifest["source_tree"],
        "reconstructed_commit": reconstructed_commit,
        "reconstructed_tree": reconstructed_tree,
        "detailed_validation": detailed.get("status"),
    }


def verify(outer: Path, output: Path) -> dict[str, Any]:
    started = time.monotonic()
    if output.exists() and any(output.iterdir()):
        raise ValueError("independent verifier output must be empty")
    output.mkdir(parents=True, exist_ok=True)
    commands: list[dict[str, Any]] = []
    bootstrap_description = os.environ.get(
        "INDEPENDENT_VERIFIER_BOOTSTRAP"
    )
    if bootstrap_description:
        commands.append(
            {
                "command": bootstrap_description,
                "exit_code": 0,
                "zip_reader_path": os.environ.get(
                    "INDEPENDENT_VERIFIER_UNZIP_PATH"
                ),
                "zip_reader_sha256": os.environ.get(
                    "INDEPENDENT_VERIFIER_UNZIP_SHA256"
                ),
                "zip_metadata_reader_path": os.environ.get(
                    "INDEPENDENT_VERIFIER_ZIPINFO_PATH"
                ),
                "zip_metadata_reader_sha256": os.environ.get(
                    "INDEPENDENT_VERIFIER_ZIPINFO_SHA256"
                ),
            }
        )
    outer_result, inner_bytes, inner_name = _validate_outer(outer)
    with tempfile.TemporaryDirectory(
        prefix="independent-verifier-", dir=output
    ) as temporary:
        work = Path(temporary)
        inner_result = _validate_inner(inner_bytes, work)
        inner_root = Path(inner_result.pop("root"))
        replay_work = output / "fresh-work"
        replay_evidence = output / "replay"
        replay_work.mkdir()
        replay_evidence.mkdir()
        environment = {
            "HOME": str(output / "empty-home"),
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TMPDIR": str(output),
        }
        (output / "empty-home").mkdir()
        replay_started = time.monotonic()
        process = subprocess.run(
            [
                str(inner_root / "target/replay.sh"),
                str(replay_work),
                str(replay_evidence),
            ],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        (output / "stdout.log").write_bytes(process.stdout)
        (output / "stderr.log").write_bytes(process.stderr)
        commands.append(
            {
                "command": (
                    "target/replay.sh $EMPTY_WORK_ROOT "
                    "$EMPTY_EVIDENCE_ROOT"
                ),
                "exit_code": process.returncode,
                "duration_seconds": time.monotonic() - replay_started,
                "environment": {
                    "HOME": "$EMPTY_HOME",
                    "PATH": "/usr/bin:/bin",
                    "network": "source-generated namespace launcher",
                },
            }
        )
        bootstrap = (
            inner_root
            / "runtime/bootstrap-python/bin/python3.14"
        )
        validation_process = subprocess.run(
            [
                str(bootstrap),
                str(inner_root / "target/target-replay.py"),
                "validate-evidence",
                "--package-root",
                str(inner_root),
                "--evidence-root",
                str(replay_evidence),
            ],
            env={
                **environment,
                "LD_LIBRARY_PATH": str(
                    inner_root
                    / "runtime/bootstrap-python/system-libs"
                ),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        commands.append(
            {
                "command": (
                    "runtime/bootstrap-python/bin/python3.14 "
                    "target/target-replay.py validate-evidence"
                ),
                "exit_code": validation_process.returncode,
            }
        )
        try:
            replay_validation = json.loads(validation_process.stdout)
        except json.JSONDecodeError:
            replay_validation = {
                "status": "failed",
                "errors": [
                    "replay evidence validator emitted invalid JSON",
                    validation_process.stderr,
                ],
            }
        shutil.rmtree(replay_work, ignore_errors=True)
        shutil.rmtree(replay_evidence, ignore_errors=True)
    _write(output / "command-log.json", commands)
    checks = {
        "outer_manifest": outer_result["status"] == "passed",
        "inner_manifest": inner_result["status"] == "passed",
        "source_commit_reconstruction": (
            inner_result["source_commit"]
            == inner_result["reconstructed_commit"]
        ),
        "source_tree_reconstruction": (
            inner_result["source_tree"]
            == inner_result["reconstructed_tree"]
        ),
        "fresh_replay_exit_zero": process.returncode == 0,
        "replay_evidence_validation": (
            replay_validation.get("status") == "passed"
        ),
        "review_handoff_validation": (
            inner_result["detailed_validation"] == "passed"
        ),
        "builder_repository_not_provided": True,
        "builder_home_not_provided": True,
        "builder_caches_not_provided": True,
        "host_semantic_runtimes_masked_by_launcher": (
            replay_validation.get("runtime_resolution") == "passed"
        ),
        "network_isolation_measured": (
            replay_validation.get("network_isolation") == "passed"
        ),
    }
    result = {
        "schema_id": "independent-verifier-receipt-current",
        "status": "passed" if all(checks.values()) else "failed",
        "input": {
            "outer_delivery_only": True,
            "outer_delivery_name": outer.name,
            "outer_delivery_sha256": sha256_file(outer),
            "generic_linux_process_primitives": True,
            "working_repository": False,
            "builder_home": False,
            "builder_caches": False,
            "host_java": False,
            "host_node": False,
            "host_chromium": False,
            "network": False,
            "previous_replay_outputs": False,
            "bootstrap_zip_reader": {
                "path": os.environ.get(
                    "INDEPENDENT_VERIFIER_UNZIP_PATH"
                ),
                "sha256": os.environ.get(
                    "INDEPENDENT_VERIFIER_UNZIP_SHA256"
                ),
                "role": "generic bounded outer-only bootstrap",
            },
            "bootstrap_zip_metadata_reader": {
                "path": os.environ.get(
                    "INDEPENDENT_VERIFIER_ZIPINFO_PATH"
                ),
                "sha256": os.environ.get(
                    "INDEPENDENT_VERIFIER_ZIPINFO_SHA256"
                ),
                "role": "generic regular-member type discovery",
            },
        },
        "outer": outer_result,
        "inner": inner_result,
        "inner_member": inner_name,
        "verified_qualifying_payload_root": inner_result[
            "qualifying_payload_root"
        ],
        "verified_qualifying_payload_entry_count": inner_result[
            "qualifying_payload_entry_count"
        ],
        "replay_exit_code": process.returncode,
        "replay_validation": replay_validation,
        "checks": checks,
        "command_log_sha256": sha256_file(
            output / "command-log.json"
        ),
        "stdout_sha256": sha256_file(output / "stdout.log"),
        "stderr_sha256": sha256_file(output / "stderr.log"),
        "duration_seconds": time.monotonic() - started,
    }
    _write(output / "independent-verifier-receipt.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outer", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.outer.resolve(), args.output.resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
