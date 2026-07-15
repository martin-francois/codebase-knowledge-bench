#!/usr/bin/env python3
"""Portable archive extraction without traversal, device, or escaping-link hazards."""

from __future__ import annotations

import os
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable


def _safe_relative(name: str) -> PurePosixPath:
    value = PurePosixPath(name)
    if value.is_absolute() or not value.parts or any(part in {"", ".", ".."} for part in value.parts):
        raise ValueError(f"unsafe archive path: {name}")
    return value


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve())
        return True
    except ValueError:
        return False


def safe_extract_tar(archive: tarfile.TarFile, destination: Path, members: Iterable[tarfile.TarInfo] | None = None) -> None:
    root = destination.resolve()
    root.mkdir(parents=True, exist_ok=True)
    selected = list(archive.getmembers() if members is None else members)
    links: list[tuple[tarfile.TarInfo, Path]] = []
    for member in selected:
        relative = _safe_relative(member.name)
        target = root.joinpath(*relative.parts)
        if not _inside(root, target):
            raise ValueError(f"archive member escapes destination: {member.name}")
        if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
            raise ValueError(f"special archive member is forbidden: {member.name}")
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(0o755)
        elif member.isfile():
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"regular member has no payload: {member.name}")
            with stream, target.open("wb") as output:
                shutil.copyfileobj(stream, output)
            target.chmod(0o755 if member.mode & stat.S_IXUSR else 0o644)
        elif member.issym() or member.islnk():
            links.append((member, target))
        else:
            raise ValueError(f"unsupported archive member: {member.name}")
    for member, target in links:
        target.parent.mkdir(parents=True, exist_ok=True)
        link_name = PurePosixPath(member.linkname)
        link_target = root.joinpath(*_safe_relative(member.linkname).parts) if member.islnk() else target.parent.joinpath(*link_name.parts)
        if link_name.is_absolute() or not _inside(root, link_target):
            raise ValueError(f"archive link escapes destination: {member.name} -> {member.linkname}")
        if member.islnk():
            if not link_target.is_file():
                raise ValueError(f"hardlink target is unavailable: {member.linkname}")
            os.link(link_target, target)
        else:
            target.symlink_to(member.linkname)


def safe_extract_zip(archive: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    root.mkdir(parents=True, exist_ok=True)
    for info in archive.infolist():
        raw = info.filename.rstrip("/")
        if not raw:
            continue
        relative = _safe_relative(raw)
        target = root.joinpath(*relative.parts)
        if not _inside(root, target):
            raise ValueError(f"ZIP member escapes destination: {info.filename}")
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode):
            raise ValueError(f"ZIP links and special files are forbidden: {info.filename}")
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(0o755)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as stream, target.open("wb") as output:
                shutil.copyfileobj(stream, output)
            target.chmod(0o755 if mode & stat.S_IXUSR else 0o644)
