#!/usr/bin/env python3
"""Verify an outer delivery using only its extracted packaged runtime."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import posixpath
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
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
    "task",
    "verification",
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


def _git_object_id(kind: str, data: bytes) -> bytes:
    header = f"{kind} {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).digest()


def _git_tree_id(root: Path) -> str:
    def tree(path: Path) -> bytes:
        rows: list[tuple[bytes, bytes]] = []
        children = list(path.iterdir())
        children.sort(
            key=lambda child: child.name.encode("utf-8")
            + (b"/" if child.is_dir() and not child.is_symlink() else b"")
        )
        for child in children:
            name = child.name.encode("utf-8")
            metadata = child.lstat()
            if stat.S_ISDIR(metadata.st_mode):
                mode = b"40000"
                object_id = tree(child)
            elif stat.S_ISLNK(metadata.st_mode):
                mode = b"120000"
                object_id = _git_object_id(
                    "blob", os.readlink(child).encode("utf-8")
                )
            elif stat.S_ISREG(metadata.st_mode):
                mode = (
                    b"100755"
                    if stat.S_IMODE(metadata.st_mode) & 0o111
                    else b"100644"
                )
                object_id = _git_object_id("blob", child.read_bytes())
            else:
                raise ValueError(
                    f"unsupported source tree member: {child}"
                )
            rows.append((name, mode + b" " + name + b"\0" + object_id))
        data = b"".join(row for _, row in rows)
        return _git_object_id("tree", data)

    return tree(root).hex()


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
        permissions = _zip_permissions(info)
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


def _zip_permissions(info: zipfile.ZipInfo) -> int:
    return (info.external_attr >> 16) & 0o7777


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
            "independent-verifier-bootstrap",
            "independent-verifier-bootstrap.sha256",
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
        info_by_name = {info.filename: info for info in infos}
        for executable in (
            "independent-verifier-bootstrap",
            "independent-verifier.sh",
        ):
            if (
                _zip_permissions(info_by_name[executable]) != 0o755
            ):
                errors.append(
                    f"outer executable mode mismatch: {executable}"
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
            for name in (
                "independent-verifier-bootstrap",
                "independent-verifier-bootstrap.sha256",
            ):
                if archive.read(name) != inner_archive.read(
                    "verification/independent-verifier/" + name
                ):
                    errors.append(
                        f"outer {name} differs from inner source"
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
                    _zip_permissions(info) or 0o755
                )
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(
                    _zip_permissions(info) or 0o644
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
    reconstructed_tree = _git_tree_id(source_checkout)
    if reconstructed_tree != manifest["source_tree"]:
        errors.append("source tree reconstruction mismatch")
    verifier_root = (
        inner_root / "verification/independent-verifier"
    )
    bootstrap = verifier_root / "independent-verifier-bootstrap"
    bootstrap_checksum = (
        verifier_root / "independent-verifier-bootstrap.sha256"
    ).read_text(encoding="utf-8").split()[0]
    if sha256_file(bootstrap) != bootstrap_checksum:
        errors.append("static verifier bootstrap checksum mismatch")
    bootstrap_bytes = bootstrap.read_bytes()
    if (
        bootstrap_bytes[:4] != b"\x7fELF"
        or bootstrap_bytes[4:6] != b"\x02\x01"
    ):
        errors.append("static verifier bootstrap is not ELF64")
    else:
        program_offset = struct.unpack_from(
            "<Q", bootstrap_bytes, 32
        )[0]
        entry_size = struct.unpack_from(
            "<H", bootstrap_bytes, 54
        )[0]
        entry_count = struct.unpack_from(
            "<H", bootstrap_bytes, 56
        )[0]
        if any(
            struct.unpack_from(
                "<I",
                bootstrap_bytes,
                program_offset + index * entry_size,
            )[0]
            == 3
            for index in range(entry_count)
        ):
            errors.append("static verifier bootstrap contains PT_INTERP")
    for source_name, packaged_name in (
        (
            "independent_verifier.sh",
            "independent_verifier.sh",
        ),
        (
            "independent_verifier_bootstrap.c",
            "independent_verifier_bootstrap.c",
        ),
        (
            "independent-verifier-bootstrap",
            "independent-verifier-bootstrap",
        ),
        (
            "independent-verifier-bootstrap.sha256",
            "independent-verifier-bootstrap.sha256",
        ),
    ):
        if (
            source_checkout / "scripts" / source_name
        ).read_bytes() != (verifier_root / packaged_name).read_bytes():
            errors.append(
                "source/packaged verifier artifact mismatch: "
                + source_name
            )
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


def _content_manifest(root: Path, excluded: set[str]) -> dict[str, Any]:
    entries = []
    for path in sorted(
        item for item in root.rglob("*") if item.is_file()
    ):
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        entries.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "entries": entries,
        "entry_count": len(entries),
        "manifest_root": canonical_root(entries),
    }


def _progress(output: Path, stage: str) -> None:
    _write(
        output / "last-completed-stage.json",
        {
            "schema_id": "independent-verifier-progress-current",
            "last_completed_stage": stage,
        },
    )


def _bootstrap_capabilities() -> dict[str, Any]:
    unzip_path = Path(
        os.environ["INDEPENDENT_VERIFIER_UNZIP_PATH"]
    )
    shell_path = Path(
        os.environ["INDEPENDENT_VERIFIER_SHELL_PATH"]
    )
    basic_tools = {
        name: Path(os.environ[environment])
        for name, environment in (
            ("mkdir", "INDEPENDENT_VERIFIER_MKDIR_PATH"),
            ("chmod", "INDEPENDENT_VERIFIER_CHMOD_PATH"),
            ("mktemp", "INDEPENDENT_VERIFIER_MKTEMP_PATH"),
            ("readlink", "INDEPENDENT_VERIFIER_READLINK_PATH"),
            ("getconf", "INDEPENDENT_VERIFIER_GETCONF_PATH"),
            ("uname", "INDEPENDENT_VERIFIER_UNAME_PATH"),
        )
    }
    clean_host_environment = {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
    }
    unzip_version = subprocess.run(
        [str(unzip_path), "-v"],
        env={
            "PATH": "/usr/bin:/bin",
            "LANG": "C",
            "LC_ALL": "C",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    packaged_loader = Path(
        os.environ["INDEPENDENT_VERIFIER_PACKAGED_LOADER"]
    )
    packaged_loader_version = subprocess.run(
        [str(packaged_loader), "--version"],
        env=clean_host_environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    packaged_glibc_match = re.search(
        r"\b(\d+\.\d+)\b",
        packaged_loader_version.stdout.splitlines()[0]
        if packaged_loader_version.stdout
        else "",
    )
    packaged_bootstrap_glibc = (
        packaged_glibc_match.group(1)
        if packaged_glibc_match is not None
        else "unknown"
    )
    generic_version_arguments = {
        "bash": ["--version"],
        "git": ["--version"],
        "ip": ["-Version"],
        "mount": ["--version"],
        "tar": ["--version"],
        "unshare": ["--version"],
        "unzip": ["-v"],
        "zstd": ["--version"],
        "sha256sum": ["--version"],
        "awk": ["--version"],
    }
    observed_generic: dict[str, Any] = {}
    for name, arguments in generic_version_arguments.items():
        resolved = shutil.which(
            name, path=clean_host_environment["PATH"]
        )
        if resolved is None:
            observed_generic[name] = {
                "available": False,
                "semantic_identity_used": False,
            }
            continue
        path = Path(resolved).resolve()
        version = subprocess.run(
            [str(path), *arguments],
            env=clean_host_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        observed_generic[name] = {
            "available": True,
            "path": str(path),
            "sha256_observed_not_locked": sha256_file(path),
            "version": (
                version.stdout.splitlines()[0]
                if version.stdout
                else ""
            ),
            "version_exit_code": version.returncode,
            "semantic_identity_used": False,
        }
    sanitized_values = {
        key: os.environ.get(key)
        for key in (
            "LD_LIBRARY_PATH",
            "PYTHONPATH",
            "JAVA_HOME",
            "NODE_PATH",
        )
    }
    host_userspace_distribution = os.environ.get(
        "INDEPENDENT_VERIFIER_HOST_USERSPACE_DISTRIBUTION", "unknown"
    )
    host_userspace_glibc = os.environ.get(
        "INDEPENDENT_VERIFIER_HOST_USERSPACE_GLIBC", "unknown"
    )
    host_kernel = os.environ.get(
        "INDEPENDENT_VERIFIER_HOST_KERNEL", "unknown"
    )
    return {
        "schema_id": "bootstrap-prerequisite-capabilities-current",
        "status": (
            "passed"
            if unzip_version.returncode == 0
            and packaged_loader_version.returncode == 0
            and packaged_bootstrap_glibc != "unknown"
            and os.environ.get("INDEPENDENT_VERIFIER_STATIC_BOOTSTRAP") == "1"
            and all(value is None for value in sanitized_values.values())
            and unzip_path.is_file()
            and shell_path.is_file()
            and all(
                path.is_file() and os.access(path, os.X_OK)
                for path in basic_tools.values()
            )
            else "failed"
        ),
        "validation_mode": "capability",
        "sanitized_environment": sanitized_values,
        "static_bootstrap": {
            "used": os.environ.get(
                "INDEPENDENT_VERIFIER_STATIC_BOOTSTRAP"
            )
            == "1",
            "description": os.environ.get(
                "INDEPENDENT_VERIFIER_BOOTSTRAP"
            ),
        },
        "packaged_loader": {
            "path": str(packaged_loader),
            "version": (
                packaged_loader_version.stdout.splitlines()[0]
                if packaged_loader_version.stdout
                else ""
            ),
            "exit_code": packaged_loader_version.returncode,
        },
        "posix_shell": {
            "path": str(shell_path),
            "sha256_observed_not_locked": sha256_file(shell_path),
            "executable": os.access(shell_path, os.X_OK),
        },
        "unzip_exact_name_streaming": {
            "path": str(unzip_path),
            "sha256_observed_not_locked": sha256_file(unzip_path),
            "version": unzip_version.stdout.splitlines()[0]
            if unzip_version.stdout
            else "",
            "exit_code": unzip_version.returncode,
            "p_streaming_reached_packaged_python": True,
        },
        "basic_host_tools": {
            name: {
                "path": str(path),
                "sha256_observed_not_locked": sha256_file(path),
                "executable": os.access(path, os.X_OK),
                "capability_exercised_by_bootstrap": True,
            }
            for name, path in sorted(basic_tools.items())
        },
        "observed_host_generic_tools_not_used": observed_generic,
        "host": {
            "host_userspace_distribution": host_userspace_distribution,
            "host_userspace_glibc": host_userspace_glibc,
            "host_kernel": host_kernel,
            "packaged_bootstrap_glibc": packaged_bootstrap_glibc,
            "machine": platform.machine(),
            "effective_uid": os.geteuid(),
            "effective_gid": os.getegid(),
        },
    }


def verify(outer: Path, output: Path) -> dict[str, Any]:
    started = time.monotonic()
    if output.exists() and any(output.iterdir()):
        raise ValueError("independent verifier output must be empty")
    output.mkdir(parents=True, exist_ok=True)
    commands: list[dict[str, Any]] = []
    capabilities = _bootstrap_capabilities()
    _write(output / "bootstrap-contract.json", capabilities)
    commands.append(
        {
            "command": os.environ.get(
                "INDEPENDENT_VERIFIER_BOOTSTRAP"
            ),
            "exit_code": (
                0 if capabilities["status"] == "passed" else 1
            ),
            "role": "host_bootstrap_prerequisites",
            "validation_mode": "capability",
        }
    )
    _progress(output, "bootstrap_capabilities")
    outer_result, inner_bytes, inner_name = _validate_outer(outer)
    _progress(output, "outer_validation")
    verification_work = output / "inner-validation-work"
    verification_work.mkdir()
    inner_result = _validate_inner(inner_bytes, verification_work)
    inner_root = Path(inner_result.pop("root"))
    _progress(output, "inner_validation")
    replay_work = output / "fresh-work"
    replay_evidence = output / "replay"
    replay_work.mkdir()
    replay_evidence.mkdir()
    empty_home = output / "empty-home"
    empty_home.mkdir()
    namespace_mode = os.environ.get(
        "REPLAY_NAMESPACE_MODE", "privileged"
    )
    environment = {
        "HOME": str(empty_home),
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TMPDIR": str(output),
        "PYTHONDONTWRITEBYTECODE": "1",
        "REPLAY_NAMESPACE_MODE": namespace_mode,
    }
    release_fault = os.environ.get(
        "BENCH_RELEASE_FAULT_INJECTION_STAGE"
    )
    if release_fault is not None:
        environment[
            "BENCH_RELEASE_FAULT_INJECTION_STAGE"
        ] = release_fault
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
    replay_duration = time.monotonic() - replay_started
    (output / "stdout.log").write_bytes(process.stdout)
    (output / "stderr.log").write_bytes(process.stderr)
    commands.append(
        {
            "command": (
                "target/replay.sh $EMPTY_WORK_ROOT "
                "$EMPTY_EVIDENCE_ROOT"
            ),
            "exit_code": process.returncode,
            "duration_seconds": replay_duration,
            "environment": {
                "HOME": "$EMPTY_HOME",
                "PATH": "host bootstrap only before packaged rootfs",
                "namespace_mode": namespace_mode,
                "network": "packaged namespace launcher",
                "release_fault_injection_stage": release_fault,
            },
        }
    )
    _progress(output, "replay_execution")
    bootstrap_root = inner_root / "runtime/bootstrap-python"
    loader = (
        bootstrap_root / "system-libs/ld-linux-x86-64.so.2"
    )
    bootstrap = bootstrap_root / "bin/python3.14"
    libraries = (
        f"{bootstrap_root / 'system-libs'}:{bootstrap_root / 'lib'}"
    )
    validation_process = subprocess.run(
        [
            str(loader),
            "--library-path",
            libraries,
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
            "PYTHONHOME": str(bootstrap_root),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    commands.append(
        {
            "command": (
                "packaged-loader --library-path packaged-libraries "
                "packaged-python target/target-replay.py "
                "validate-evidence"
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
    _write(output / "command-log.json", commands)
    work_manifest = _content_manifest(replay_work, set())
    work_manifest.update(
        {
            "schema_id": "fresh-work-diagnostic-manifest-current",
            "worktree_pruned_after_manifest": (
                process.returncode == 0
                and replay_validation.get("status") == "passed"
            ),
        }
    )
    _write(output / "fresh-work-manifest.json", work_manifest)
    if (
        process.returncode == 0
        and replay_validation.get("status") == "passed"
    ):
        shutil.rmtree(replay_work)
    _progress(output, "failure_safe_evidence_packaging")
    checks = {
        "bootstrap_capabilities": capabilities["status"] == "passed",
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
        "packaged_semantic_runtime": (
            replay_validation.get("runtime_resolution") == "passed"
        ),
        "network_isolation_measured": (
            replay_validation.get("network_isolation") == "passed"
        ),
        "namespace_capability_measured": (
            replay_validation.get("namespace_capability") == "passed"
        ),
        "failure_evidence_preserved": (
            replay_evidence.is_dir()
            and (
                process.returncode == 0
                or replay_work.is_dir()
            )
        ),
    }
    result = {
        "schema_id": "independent-verifier-receipt-current",
        "status": "passed" if all(checks.values()) else "failed",
        "input": {
            "outer_delivery_only": True,
            "outer_delivery_name": outer.name,
            "outer_delivery_sha256": sha256_file(outer),
            "working_repository": False,
            "builder_home": False,
            "builder_caches": False,
            "host_semantic_runtimes_provided_to_replay": False,
            "host_java": False,
            "host_node": False,
            "host_chromium": False,
            "network": False,
            "previous_replay_outputs": False,
            "namespace_mode": namespace_mode,
            "bootstrap_prerequisites": capabilities,
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
        "replay_duration_seconds": replay_duration,
        "replay_validation": replay_validation,
        "checks": checks,
        "failure_evidence": {
            "replay_directory_retained": replay_evidence.is_dir(),
            "fresh_work_directory_retained": replay_work.is_dir(),
            "fresh_work_manifest": "fresh-work-manifest.json",
            "fresh_work_manifest_root": work_manifest["manifest_root"],
        },
        "command_log_sha256": sha256_file(
            output / "command-log.json"
        ),
        "stdout_sha256": sha256_file(output / "stdout.log"),
        "stderr_sha256": sha256_file(output / "stderr.log"),
        "duration_seconds": time.monotonic() - started,
    }
    _write(output / "independent-verifier-receipt.json", result)
    if result["status"] == "failed":
        failure = {
            "schema_id": "independent-verifier-failure-current",
            "status": "failed",
            "last_completed_stage": json.loads(
                (
                    output / "last-completed-stage.json"
                ).read_text(encoding="utf-8")
            )["last_completed_stage"],
            "replay_exit_code": process.returncode,
            "failed_checks": sorted(
                name for name, passed in checks.items() if not passed
            ),
            "replay_failure_receipt": (
                "replay/failure-receipt.json"
                if (replay_evidence / "failure-receipt.json").is_file()
                else None
            ),
        }
        _write(output / "failure-receipt.json", failure)
        partial = _content_manifest(
            output,
            {
                "partial-evidence-manifest.json",
                "inner-validation-work",
            },
        )
        partial["schema_id"] = (
            "independent-verifier-partial-evidence-manifest-current"
        )
        _write(output / "partial-evidence-manifest.json", partial)
    shutil.rmtree(verification_work)
    bootstrap_stage = os.environ.get(
        "INDEPENDENT_VERIFIER_BOOTSTRAP_STAGE"
    )
    if bootstrap_stage:
        shutil.rmtree(bootstrap_stage, ignore_errors=True)
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
