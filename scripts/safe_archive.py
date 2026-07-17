#!/usr/bin/env python3
"""The sole bounded archive inspection, manifest, and extraction boundary."""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import shutil
import stat
import tarfile
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO


MAX_MEMBERS = 20_000
MAX_TOTAL_BYTES = 800_000_000
MAX_MEMBER_BYTES = 300_000_000
MAX_COMPRESSION_RATIO = 200
MANIFEST_SCHEMA_ID = "exact-archive-manifest-current"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_root(rows: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        rows, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or path.is_absolute()
        or "." in path.parts
        or ".." in path.parts
        or "\\" in name
        or "\x00" in name
    ):
        raise ValueError(f"unsafe archive path: {name}")
    return path


def _collisions(entries: list[tuple[str, bool]]) -> None:
    seen: dict[str, bool] = {}
    folded: dict[str, str] = {}
    for name, is_dir in entries:
        clean = str(_path(name)).rstrip("/")
        if clean in seen:
            raise ValueError(f"duplicate archive path: {name}")
        case_key = clean.casefold()
        if case_key in folded:
            raise ValueError(
                f"case-fold collision: {folded[case_key]} and {name}"
            )
        seen[clean] = is_dir
        folded[case_key] = clean
    for clean in seen:
        parts = PurePosixPath(clean).parts
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            if parent in seen and not seen[parent]:
                raise ValueError(f"file/directory collision: {clean}")


def _resolved_link(member: str, target: str, *, hardlink: bool) -> str:
    if (
        not target
        or target.startswith("/")
        or PurePosixPath(target).is_absolute()
        or "\\" in target
        or "\x00" in target
    ):
        raise ValueError(f"escaping archive link: {member} -> {target}")
    base = "" if hardlink else posixpath.dirname(member)
    resolved = posixpath.normpath(posixpath.join(base, target))
    if resolved == ".." or resolved.startswith("../") or resolved.startswith("/"):
        raise ValueError(f"escaping archive link: {member} -> {target}")
    _path(resolved)
    return resolved


def _tar_type(member: tarfile.TarInfo) -> str:
    if member.isfile():
        return "file"
    if member.isdir():
        return "directory"
    if member.issym():
        return "symlink"
    if member.islnk():
        return "hardlink"
    if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
        raise ValueError(f"special tar member rejected: {member.name}")
    raise ValueError(f"unsupported tar member type: {member.name}")


def _stream_sha256(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def _member_row(
    archive: tarfile.TarFile, member: tarfile.TarInfo
) -> dict[str, Any]:
    member_type = _tar_type(member)
    sha256: str | None = None
    if member_type == "file":
        stream = archive.extractfile(member)
        if stream is None:
            raise ValueError(f"missing tar payload: {member.name}")
        with stream:
            sha256 = _stream_sha256(stream)
    return {
        "path": str(_path(member.name)).rstrip("/"),
        "type": member_type,
        "bytes": member.size if member_type == "file" else 0,
        "sha256": sha256,
        "mode": member.mode & 0o7777,
        "symlink_target": member.linkname if member_type == "symlink" else None,
        "hardlink_target": member.linkname if member_type == "hardlink" else None,
    }


def _inspect_tar(
    archive: tarfile.TarFile, *, compressed_bytes: int | None = None
) -> tuple[list[tarfile.TarInfo], list[dict[str, Any]]]:
    members = archive.getmembers()
    if len(members) > MAX_MEMBERS:
        raise ValueError("tar member limit exceeded")
    typed = [(member, _tar_type(member)) for member in members]
    _collisions(
        [(member.name, member_type == "directory") for member, member_type in typed]
    )
    total = 0
    for member, member_type in typed:
        if member.size < 0 or member.size > MAX_MEMBER_BYTES:
            raise ValueError(f"tar member size limit exceeded: {member.name}")
        if member_type == "file":
            total += member.size
        if total > MAX_TOTAL_BYTES:
            raise ValueError("tar expanded-size limit exceeded")
        if member_type == "symlink":
            _resolved_link(member.name, member.linkname, hardlink=False)
        elif member_type == "hardlink":
            _resolved_link(member.name, member.linkname, hardlink=True)
    if (
        compressed_bytes is not None
        and compressed_bytes > 0
        and total / compressed_bytes > MAX_COMPRESSION_RATIO
    ):
        raise ValueError("tar compression-ratio limit exceeded")
    rows = [_member_row(archive, member) for member in members]
    rows_by_path = {row["path"]: row for row in rows}
    for row in rows:
        if row["type"] != "hardlink":
            continue
        resolved = _resolved_link(
            row["path"], str(row["hardlink_target"]), hardlink=True
        )
        linked = rows_by_path.get(resolved)
        if linked is None or linked["type"] not in {"file", "hardlink"}:
            raise ValueError(
                f"hardlink target is not a regular archive member: "
                f"{row['path']} -> {row['hardlink_target']}"
            )
        if linked["mode"] != row["mode"]:
            raise ValueError(
                f"hardlink mode differs from target: {row['path']}"
            )
    return members, rows


def exact_archive_manifest(
    archive_path: Path, archive: tarfile.TarFile
) -> dict[str, Any]:
    _, rows = _inspect_tar(
        archive, compressed_bytes=archive_path.stat().st_size
    )
    rows.sort(key=lambda row: row["path"])
    return {
        "schema_id": MANIFEST_SCHEMA_ID,
        "archive": archive_path.name,
        "archive_bytes": archive_path.stat().st_size,
        "archive_sha256": sha256_file(archive_path),
        "entry_count": len(rows),
        "expanded_bytes": sum(
            int(row["bytes"]) for row in rows if row["type"] == "file"
        ),
        "manifest_root": canonical_root(rows),
        "limits": {
            "members": MAX_MEMBERS,
            "member_bytes": MAX_MEMBER_BYTES,
            "total_expanded_bytes": MAX_TOTAL_BYTES,
            "compression_ratio": MAX_COMPRESSION_RATIO,
        },
        "entries": rows,
    }


def _manifest_errors(
    archive_path: Path,
    observed: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    required_top = {
        "schema_id",
        "archive",
        "archive_bytes",
        "archive_sha256",
        "entry_count",
        "expanded_bytes",
        "manifest_root",
        "limits",
        "entries",
    }
    if set(manifest) != required_top:
        errors.append("archive manifest field set mismatch")
    if manifest.get("schema_id") != MANIFEST_SCHEMA_ID:
        errors.append("archive manifest schema mismatch")
    if manifest.get("archive") != archive_path.name:
        errors.append("archive name mismatch")
    if manifest.get("archive_bytes") != archive_path.stat().st_size:
        errors.append("archive byte count mismatch")
    if manifest.get("archive_sha256") != sha256_file(archive_path):
        errors.append("archive hash mismatch")
    expected = manifest.get("entries")
    if not isinstance(expected, list):
        return errors + ["archive manifest entries are invalid"]
    required_entry = {
        "path",
        "type",
        "bytes",
        "sha256",
        "mode",
        "symlink_target",
        "hardlink_target",
    }
    manifest_members: list[tuple[str, bool]] = []
    for index, row in enumerate(expected):
        if not isinstance(row, dict) or set(row) != required_entry:
            errors.append(
                f"archive manifest entry field set mismatch: {index}"
            )
            continue
        member_type = row.get("type")
        if member_type not in {
            "file",
            "directory",
            "symlink",
            "hardlink",
        }:
            errors.append(
                f"archive manifest entry type mismatch: {index}"
            )
            continue
        try:
            member_path = str(_path(str(row["path"]))).rstrip("/")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if member_path != row["path"]:
            errors.append(
                f"archive manifest path is not canonical: {row['path']}"
            )
        manifest_members.append(
            (member_path, member_type == "directory")
        )
    try:
        _collisions(manifest_members)
    except ValueError as exc:
        errors.append(str(exc))
    if expected != sorted(
        expected,
        key=lambda row: (
            str(row.get("path"))
            if isinstance(row, dict)
            else ""
        ),
    ):
        errors.append("archive manifest entries are not canonically ordered")
    if manifest.get("entry_count") != len(expected):
        errors.append("archive manifest count mismatch")
    if manifest.get("expanded_bytes") != sum(
        int(row.get("bytes", 0))
        for row in expected
        if row.get("type") == "file"
    ):
        errors.append("archive expanded byte count mismatch")
    if manifest.get("manifest_root") != canonical_root(expected):
        errors.append("archive manifest root mismatch")
    if manifest.get("limits") != {
        "members": MAX_MEMBERS,
        "member_bytes": MAX_MEMBER_BYTES,
        "total_expanded_bytes": MAX_TOTAL_BYTES,
        "compression_ratio": MAX_COMPRESSION_RATIO,
    }:
        errors.append("archive manifest limits mismatch")
    expected_paths = [
        (
            str(row.get("path"))
            if isinstance(row, dict)
            else f"<invalid-entry-{index}>"
        )
        for index, row in enumerate(expected)
    ]
    observed_paths = [str(row.get("path")) for row in observed]
    missing = sorted(set(expected_paths) - set(observed_paths))
    unexpected = sorted(set(observed_paths) - set(expected_paths))
    if missing:
        errors.append(f"missing archive members: {missing}")
    if unexpected:
        errors.append(f"unexpected archive members: {unexpected}")
    expected_by_path = {
        str(row.get("path")): row for row in expected if isinstance(row, dict)
    }
    for row in observed:
        if expected_by_path.get(row["path"]) != row:
            errors.append(f"archive member mismatch: {row['path']}")
    if observed != sorted(observed, key=lambda row: row["path"]):
        errors.append("archive members are not canonically ordered")
    return errors


def validate_exact_tar(
    archive_path: Path, manifest_path: Path
) -> dict[str, Any]:
    errors: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        with tarfile.open(archive_path, "r:*") as archive:
            _, observed = _inspect_tar(
                archive, compressed_bytes=archive_path.stat().st_size
            )
        observed.sort(key=lambda row: row["path"])
        errors.extend(_manifest_errors(archive_path, observed, manifest))
    except (KeyError, OSError, TypeError, ValueError, tarfile.TarError) as exc:
        errors.append(str(exc))
        manifest = {}
        observed = []
    return {
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "archive": archive_path.name,
        "archive_sha256": (
            sha256_file(archive_path) if archive_path.is_file() else None
        ),
        "entry_count": len(observed),
        "manifest_root": (
            canonical_root(observed) if observed else None
        ),
    }


def _extract_inspected_tar(
    archive: tarfile.TarFile,
    destination: Path,
    members: list[tarfile.TarInfo],
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    hardlinks: list[tarfile.TarInfo] = []
    directories: list[tarfile.TarInfo] = []
    for member in members:
        member_type = _tar_type(member)
        target = destination / _path(member.name)
        if member_type == "directory":
            target.mkdir(parents=True, exist_ok=True)
            directories.append(member)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            raise ValueError(f"archive extraction collision: {member.name}")
        if member_type == "symlink":
            _resolved_link(member.name, member.linkname, hardlink=False)
            os.symlink(member.linkname, target)
            continue
        if member_type == "hardlink":
            hardlinks.append(member)
            continue
        stream = archive.extractfile(member)
        if stream is None:
            raise ValueError(f"missing tar payload: {member.name}")
        with stream, target.open("xb") as output:
            shutil.copyfileobj(stream, output)
        target.chmod(member.mode & 0o7777)
    for member in hardlinks:
        target = destination / _path(member.name)
        resolved = _resolved_link(
            member.name, member.linkname, hardlink=True
        )
        source = destination / _path(resolved)
        if not source.is_file() or source.is_symlink():
            raise ValueError(
                f"hardlink target is unavailable: {member.name} -> {member.linkname}"
            )
        os.link(source, target)
        target.chmod(member.mode & 0o7777)
    for member in sorted(
        directories,
        key=lambda row: len(PurePosixPath(row.name).parts),
        reverse=True,
    ):
        (destination / _path(member.name)).chmod(member.mode & 0o7777)


def safe_extract_exact_tar(
    archive_path: Path, manifest_path: Path, destination: Path
) -> dict[str, Any]:
    validation = validate_exact_tar(archive_path, manifest_path)
    if validation["status"] != "passed":
        raise ValueError(
            "exact archive validation failed: "
            + "; ".join(validation["errors"])
        )
    with tarfile.open(archive_path, "r:*") as archive:
        members, _ = _inspect_tar(
            archive, compressed_bytes=archive_path.stat().st_size
        )
        _extract_inspected_tar(archive, destination, members)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    archive_roots = sorted(
        {
            PurePosixPath(str(row["path"])).parts[0]
            for row in manifest["entries"]
        }
    )
    extracted: list[dict[str, Any]] = []
    for root_name in archive_roots:
        extracted.extend(inspect_tree(destination / root_name))
    extracted.sort(key=lambda row: row["path"])
    expected_by_path = {
        str(row["path"]): row for row in manifest["entries"]
    }
    extracted_by_path = {row["path"]: row for row in extracted}
    if set(extracted_by_path) != set(expected_by_path):
        raise ValueError("exact extracted member set differs from manifest")
    normalized: list[dict[str, Any]] = []
    for name, expected in sorted(expected_by_path.items()):
        actual = extracted_by_path[name]
        if expected["type"] == "hardlink":
            resolved = _resolved_link(
                name, str(expected["hardlink_target"]), hardlink=True
            )
            path = destination / _path(name)
            linked = destination / _path(resolved)
            if (
                actual["type"] != "file"
                or not linked.is_file()
                or linked.is_symlink()
                or not os.path.samefile(path, linked)
                or actual["mode"] != expected["mode"]
            ):
                raise ValueError(
                    f"exact extracted hardlink differs from manifest: {name}"
                )
            normalized.append(expected)
        else:
            normalized.append(actual)
    if normalized != manifest["entries"]:
        raise ValueError("exact extracted member set differs from manifest")
    return validation


def safe_extract_tar(
    archive: tarfile.TarFile,
    destination: Path,
    members: Iterable[tarfile.TarInfo] | None = None,
) -> None:
    materialized = list(
        archive.getmembers() if members is None else members
    )
    if len(materialized) > MAX_MEMBERS:
        raise ValueError("tar member limit exceeded")
    _collisions(
        [
            (member.name, _tar_type(member) == "directory")
            for member in materialized
        ]
    )
    total = 0
    for member in materialized:
        member_type = _tar_type(member)
        if member.size < 0 or member.size > MAX_MEMBER_BYTES:
            raise ValueError("tar member size limit exceeded")
        if member_type == "file":
            total += member.size
        if total > MAX_TOTAL_BYTES:
            raise ValueError("tar expanded-size limit exceeded")
        if member_type == "symlink":
            _resolved_link(member.name, member.linkname, hardlink=False)
        elif member_type == "hardlink":
            _resolved_link(member.name, member.linkname, hardlink=True)
    _extract_inspected_tar(archive, destination, materialized)


def safe_extract_zip(
    archive: zipfile.ZipFile,
    destination: Path,
    *,
    max_members: int = MAX_MEMBERS,
    max_member_bytes: int = MAX_MEMBER_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
    max_compression_ratio: int = MAX_COMPRESSION_RATIO,
    allowed_symlinks: Mapping[str, str] | None = None,
    expected_modes: Mapping[str, int] | None = None,
) -> None:
    infos = archive.infolist()
    if len(infos) > max_members:
        raise ValueError("ZIP member limit exceeded")
    _collisions(
        [
            (
                info.filename,
                (
                    (info.external_attr >> 16) & 0o170000
                )
                == stat.S_IFDIR
                or info.is_dir(),
            )
            for info in infos
        ]
    )
    total = 0
    declared_symlinks = dict(allowed_symlinks or {})
    declared_modes = dict(expected_modes or {})
    observed_symlinks: set[str] = set()
    observed_modes: set[str] = set()
    for info in infos:
        member_type = (info.external_attr >> 16) & 0o170000
        permissions = (info.external_attr >> 16) & 0o777
        member_name = str(_path(info.filename)).rstrip("/")
        if member_type and member_type not in {
            stat.S_IFREG,
            stat.S_IFDIR,
            stat.S_IFLNK,
        }:
            raise ValueError("unsupported ZIP member type rejected")
        if member_type == stat.S_IFLNK:
            if member_name not in declared_symlinks:
                raise ValueError("ZIP symlink rejected")
            try:
                symlink_target = archive.read(info).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("ZIP symlink target is not UTF-8") from exc
            if symlink_target != declared_symlinks[member_name]:
                raise ValueError("ZIP symlink target mismatch")
            _resolved_link(
                member_name, symlink_target, hardlink=False
            )
            observed_symlinks.add(member_name)
        elif member_name in declared_symlinks:
            raise ValueError("declared ZIP symlink is not a symlink")
        if declared_modes:
            if member_name not in declared_modes:
                raise ValueError("ZIP member has no declared mode")
            if permissions != declared_modes[member_name]:
                raise ValueError("ZIP member mode mismatch")
            observed_modes.add(member_name)
        if info.file_size > max_member_bytes:
            raise ValueError("ZIP member size limit exceeded")
        total += info.file_size
        if total > max_total_bytes:
            raise ValueError("ZIP expanded-size limit exceeded")
        if (
            info.compress_size
            and info.file_size / info.compress_size
            > max_compression_ratio
        ):
            raise ValueError("ZIP compression-ratio limit exceeded")
    if observed_symlinks != set(declared_symlinks):
        raise ValueError("declared ZIP symlink set mismatch")
    if declared_modes and observed_modes != set(declared_modes):
        raise ValueError("declared ZIP mode set mismatch")
    destination.mkdir(parents=True, exist_ok=True)
    for info in infos:
        member_type = (info.external_attr >> 16) & 0o170000
        if member_type == stat.S_IFLNK:
            continue
        target = destination / _path(info.filename)
        if member_type == stat.S_IFDIR or info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(
                (info.external_attr >> 16) & 0o777 or 0o755
            )
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, target.open("xb") as output:
            shutil.copyfileobj(source, output)
        target.chmod(
            (info.external_attr >> 16) & 0o777 or 0o644
        )
    for info in infos:
        member_type = (info.external_attr >> 16) & 0o170000
        if member_type != stat.S_IFLNK:
            continue
        target = destination / _path(info.filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        member_name = str(_path(info.filename)).rstrip("/")
        os.symlink(declared_symlinks[member_name], target)


def inspect_tree(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(
        [root, *root.rglob("*")],
        key=lambda item: (
            item.relative_to(root.parent).as_posix().casefold(),
            item.relative_to(root.parent).as_posix(),
        ),
    ):
        relative = path.relative_to(root.parent).as_posix()
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISREG(metadata.st_mode):
            member_type = "file"
            size = metadata.st_size
            digest: str | None = sha256_file(path)
            symlink_target = None
        elif stat.S_ISDIR(metadata.st_mode):
            member_type = "directory"
            size = 0
            digest = None
            symlink_target = None
        elif stat.S_ISLNK(metadata.st_mode):
            member_type = "symlink"
            size = 0
            digest = None
            symlink_target = os.readlink(path)
            _resolved_link(relative, symlink_target, hardlink=False)
        else:
            raise ValueError(f"unsupported source member type: {path}")
        rows.append(
            {
                "path": relative,
                "type": member_type,
                "bytes": size,
                "sha256": digest,
                "mode": mode,
                "symlink_target": symlink_target,
                "hardlink_target": None,
            }
        )
    _collisions(
        [(row["path"], row["type"] == "directory") for row in rows]
    )
    if len(rows) > MAX_MEMBERS:
        raise ValueError("source member limit exceeded")
    total = sum(row["bytes"] for row in rows)
    if total > MAX_TOTAL_BYTES:
        raise ValueError("source expanded-size limit exceeded")
    if any(row["bytes"] > MAX_MEMBER_BYTES for row in rows):
        raise ValueError("source member size limit exceeded")
    return sorted(rows, key=lambda row: row["path"])


def inspect_directory_contents(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for child in sorted(
        root.iterdir(), key=lambda path: (path.name.casefold(), path.name)
    ):
        rows.extend(inspect_tree(child))
    rows.sort(key=lambda row: row["path"])
    _collisions(
        [(row["path"], row["type"] == "directory") for row in rows]
    )
    if len(rows) > MAX_MEMBERS:
        raise ValueError("extracted member limit exceeded")
    if sum(row["bytes"] for row in rows) > MAX_TOTAL_BYTES:
        raise ValueError("extracted size limit exceeded")
    return rows


def build_exact_tar(
    source: Path, output: Path, arcname: str
) -> dict[str, Any]:
    if not source.is_dir():
        raise ValueError(f"archive source directory is missing: {source}")
    _path(arcname)
    output.parent.mkdir(parents=True, exist_ok=True)
    paths = [source, *source.rglob("*")]
    paths.sort(
        key=lambda item: (
            (Path(arcname) / item.relative_to(source)).as_posix().casefold(),
            (Path(arcname) / item.relative_to(source)).as_posix(),
        )
    )
    source_rows = inspect_tree(source)
    with tarfile.open(output, "w:zst", level=10) as archive:
        for path in paths:
            relative = path.relative_to(source)
            name = (
                Path(arcname)
                if relative == Path(".")
                else Path(arcname) / relative
            ).as_posix()
            metadata = path.lstat()
            info = tarfile.TarInfo(name)
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            info.mode = stat.S_IMODE(metadata.st_mode)
            if stat.S_ISDIR(metadata.st_mode):
                info.type = tarfile.DIRTYPE
                info.size = 0
                archive.addfile(info)
            elif stat.S_ISLNK(metadata.st_mode):
                info.type = tarfile.SYMTYPE
                info.size = 0
                info.linkname = os.readlink(path)
                _resolved_link(name, info.linkname, hardlink=False)
                archive.addfile(info)
            elif stat.S_ISREG(metadata.st_mode):
                info.type = tarfile.REGTYPE
                info.size = metadata.st_size
                with path.open("rb") as stream:
                    archive.addfile(info, stream)
            else:
                raise ValueError(f"unsupported source member type: {path}")
    with tarfile.open(output, "r:*") as archive:
        manifest = exact_archive_manifest(output, archive)
    expected_rows = [
        {
            **row,
            "path": (
                Path(arcname)
                / Path(row["path"]).relative_to(source.name)
            ).as_posix(),
        }
        for row in source_rows
    ]
    expected_rows.sort(key=lambda row: row["path"])
    if manifest["entries"] != expected_rows:
        raise ValueError("deterministic archive differs from source tree")
    return manifest
