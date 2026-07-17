#!/usr/bin/env python3
"""Build the one minimal, content-addressable semantic replay OS root."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


SEED_PACKAGES = (
    "bash",
    "ca-certificates",
    "coreutils",
    "dash",
    "findutils",
    "gawk",
    "git",
    "grep",
    "iproute2",
    "mount",
    "sed",
    "tar",
    "unzip",
    "util-linux",
    "zstd",
)
TOOL_PATHS = {
    "posix_sh": "/bin/sh",
    "bash": "/usr/bin/bash",
    "git": "/usr/bin/git",
    "ip": "/usr/bin/ip",
    "mount": "/usr/bin/mount",
    "tar": "/usr/bin/tar",
    "unshare": "/usr/bin/unshare",
    "unzip": "/usr/bin/unzip",
    "zstd": "/usr/bin/zstd",
    "sha256sum": "/usr/bin/sha256sum",
    "awk": "/usr/bin/awk",
}
ROOT_LINKS = ("/bin", "/lib", "/lib64", "/sbin")
CONFIG_PATHS = (
    "/etc/alternatives",
    "/etc/ca-certificates",
    "/etc/ca-certificates.conf",
    "/etc/debian_version",
    "/etc/group",
    "/etc/hosts",
    "/etc/ld.so.cache",
    "/etc/ld.so.conf",
    "/etc/ld.so.conf.d",
    "/etc/localtime",
    "/etc/nsswitch.conf",
    "/etc/os-release",
    "/etc/passwd",
    "/etc/protocols",
    "/etc/services",
    "/etc/ssl",
    "/usr/lib/locale/C.utf8",
    "/usr/share/git-core/templates",
    "/usr/share/zoneinfo/UTC",
)
RUNTIME_DIRECTORIES = (
    "/dev",
    "/evidence",
    "/home",
    "/package",
    "/proc",
    "/run",
    "/sys",
    "/tmp",
    "/work",
)
SKIPPED_PREFIXES = (
    "/usr/share/doc/",
    "/usr/share/info/",
    "/usr/share/lintian/",
    "/usr/share/man/",
    "/usr/share/locale/",
)
OPTIONAL_CASEFOLD_COLLISION_PATTERNS = (
    r"^/usr/share/perl/[^/]+/pod(?:/|$)",
    r"^/usr/lib/[^/]+/perl/[^/]+/sys(?:/|$)",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def required_rootfs_paths() -> dict[str, str]:
    return dict(TOOL_PATHS)


def _parse_control(path: Path) -> dict[str, dict[str, str]]:
    paragraphs: dict[str, dict[str, str]] = {}
    current: dict[str, str] = {}
    last_key: str | None = None
    for line in path.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines() + [""]:
        if not line:
            package = current.get("Package")
            if package and current.get("Status", "").endswith(
                " installed"
            ):
                paragraphs[package] = current
            current = {}
            last_key = None
            continue
        if line[0].isspace() and last_key is not None:
            current[last_key] += " " + line.strip()
            continue
        if ":" in line:
            last_key, value = line.split(":", 1)
            current[last_key] = value.strip()
    return paragraphs


def _dependency_names(value: str) -> list[list[str]]:
    groups: list[list[str]] = []
    for raw_group in value.split(","):
        alternatives = []
        for raw in raw_group.split("|"):
            name = re.split(r"\s|\(|\[", raw.strip(), maxsplit=1)[0]
            name = name.split(":", 1)[0]
            if name:
                alternatives.append(name)
        if alternatives:
            groups.append(alternatives)
    return groups


def selected_packages(
    status: Mapping[str, Mapping[str, str]],
) -> list[str]:
    providers: dict[str, list[str]] = {}
    for package, row in status.items():
        for group in _dependency_names(row.get("Provides", "")):
            for virtual in group:
                providers.setdefault(virtual, []).append(package)
    selected: set[str] = set()
    pending = list(SEED_PACKAGES)
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        if name not in status:
            raise ValueError(f"rootfs source package is missing: {name}")
        selected.add(name)
        row = status[name]
        for field in ("Pre-Depends", "Depends"):
            for alternatives in _dependency_names(row.get(field, "")):
                dependency = next(
                    (item for item in alternatives if item in status),
                    None,
                )
                if dependency is None:
                    dependency = next(
                        (
                            provider
                            for alternative in alternatives
                            for provider in sorted(
                                providers.get(alternative, [])
                            )
                        ),
                        None,
                    )
                if dependency is None:
                    raise ValueError(
                        f"no installed dependency alternative for "
                        f"{name}: {alternatives}"
                    )
                if dependency not in selected:
                    pending.append(dependency)
    return sorted(selected)


def _safe_relative(path_text: str) -> Path:
    value = PurePosixPath(path_text)
    if not value.is_absolute() or ".." in value.parts:
        raise ValueError(f"unsafe rootfs source path: {path_text}")
    parts = value.parts[1:]
    if not parts:
        raise ValueError("root itself is not a copyable member")
    return Path(*parts)


def _copy_member(source_root: Path, output: Path, path_text: str) -> None:
    relative = _safe_relative(path_text)
    source = source_root / relative
    target = output / relative
    if not (source.exists() or source.is_symlink()):
        return
    if source.is_symlink():
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        os.symlink(os.readlink(source), target)
    elif source.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        target.chmod(stat.S_IMODE(source.stat().st_mode))
    elif source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)


def _copy_tree_members(
    source_root: Path, output: Path, path_text: str
) -> None:
    relative = _safe_relative(path_text)
    source = source_root / relative
    if not (source.exists() or source.is_symlink()):
        return
    if not source.is_dir() or source.is_symlink():
        _copy_member(source_root, output, path_text)
        return
    _copy_member(source_root, output, path_text)
    for child in sorted(
        source.rglob("*"), key=lambda item: item.as_posix()
    ):
        member = "/" + child.relative_to(source_root).as_posix()
        _copy_member(source_root, output, member)


def _package_list_path(
    source_root: Path, package: str, architecture: str | None
) -> Path:
    info = source_root / "var/lib/dpkg/info"
    candidates = [
        info / f"{package}.list",
        info / f"{package}:{architecture}.list" if architecture else None,
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    matches = sorted(info.glob(f"{package}:*.list"))
    if len(matches) == 1:
        return matches[0]
    raise ValueError(f"rootfs package list is missing: {package}")


def _skip_package_member(path_text: str) -> bool:
    if path_text.endswith("/"):
        path_text = path_text.rstrip("/")
    if any(path_text.startswith(prefix) for prefix in SKIPPED_PREFIXES):
        return not (
            path_text.startswith("/usr/share/doc/")
            and path_text.endswith("/copyright")
        )
    if any(
        re.match(pattern, path_text)
        for pattern in OPTIONAL_CASEFOLD_COLLISION_PATTERNS
    ):
        return True
    return False


def _usrmerge_path(path_text: str) -> str:
    for prefix, replacement in (
        ("/bin/", "/usr/bin/"),
        ("/sbin/", "/usr/sbin/"),
        ("/lib/", "/usr/lib/"),
        ("/lib64/", "/usr/lib64/"),
    ):
        if path_text.startswith(prefix):
            return replacement + path_text[len(prefix) :]
    return path_text


def _copy_symlink_chain(
    source_root: Path, output: Path, path_text: str
) -> None:
    current = PurePosixPath(path_text)
    seen: set[str] = set()
    for _ in range(32):
        rendered = str(current)
        if rendered in seen:
            raise ValueError(f"rootfs symlink loop: {path_text}")
        seen.add(rendered)
        _copy_member(source_root, output, rendered)
        source = source_root / _safe_relative(rendered)
        if not source.is_symlink():
            return
        link = PurePosixPath(os.readlink(source))
        if link.is_absolute():
            current = link
        else:
            current = PurePosixPath(
                os.path.normpath(str(current.parent / link))
            )
    raise ValueError(f"rootfs symlink depth exceeded: {path_text}")


def _normalize_absolute_symlinks(output: Path) -> None:
    for path in sorted(output.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_symlink():
            continue
        target = os.readlink(path)
        if not target.startswith("/"):
            continue
        staged_target = output / target.lstrip("/")
        relative_target = os.path.relpath(staged_target, path.parent)
        path.unlink()
        os.symlink(relative_target, path)


def _resolve_staged(output: Path, path_text: str) -> Path:
    current = PurePosixPath(path_text)
    seen: set[str] = set()
    for _ in range(64):
        rendered = str(current)
        if rendered in seen:
            raise ValueError(f"staged rootfs symlink loop: {path_text}")
        seen.add(rendered)
        path = output / _safe_relative(rendered)
        if not path.is_symlink():
            if not path.is_file():
                raise ValueError(f"staged rootfs tool missing: {path_text}")
            return path
        link = PurePosixPath(os.readlink(path))
        if link.is_absolute():
            current = link
        else:
            current = PurePosixPath(
                os.path.normpath(str(current.parent / link))
            )
        if not current.is_absolute() or ".." in current.parts:
            raise ValueError(f"staged rootfs tool escapes: {path_text}")
    raise ValueError(f"staged rootfs symlink depth exceeded: {path_text}")


def build_minimal_rootfs(
    source_root: Path,
    output: Path,
    *,
    source_image_digest: str,
) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ValueError("replay rootfs output must be empty")
    output.mkdir(parents=True, exist_ok=True)
    # A rootless user namespace maps files owned by the outer user, while
    # files owned by another outer identity appear as the overflow user.
    # Keep the root and bind-mount traversal points deliberately searchable.
    output.chmod(0o755)
    status_path = source_root / "var/lib/dpkg/status"
    status = _parse_control(status_path)
    packages = selected_packages(status)
    copied_paths: set[str] = set()
    for package in packages:
        row = status[package]
        listing = _package_list_path(
            source_root, package, row.get("Architecture")
        )
        for path_text in listing.read_text(
            encoding="utf-8", errors="strict"
        ).splitlines():
            if (
                not path_text
                or path_text == "/."
                or _skip_package_member(path_text)
            ):
                continue
            path_text = _usrmerge_path(path_text)
            _copy_member(source_root, output, path_text)
            copied_paths.add(path_text)
    for path_text in ROOT_LINKS:
        _copy_symlink_chain(source_root, output, path_text)
    for path_text in CONFIG_PATHS:
        _copy_tree_members(source_root, output, path_text)
    for path_text in TOOL_PATHS.values():
        _copy_symlink_chain(source_root, output, path_text)
    for path_text in RUNTIME_DIRECTORIES:
        path = output / _safe_relative(path_text)
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o755)
    (output / "tmp").chmod(0o1777)
    (output / "run").chmod(0o755)
    (output / "etc").mkdir(parents=True, exist_ok=True)
    (output / "etc/resolv.conf").write_bytes(b"")
    _normalize_absolute_symlinks(output)
    tool_rows: dict[str, Any] = {}
    for name, path_text in TOOL_PATHS.items():
        path = output / _safe_relative(path_text)
        resolved = _resolve_staged(output, path_text)
        tool_rows[name] = {
            "path": path_text,
            "resolved_path": "/"
            + resolved.relative_to(output.resolve()).as_posix(),
            "bytes": resolved.stat().st_size,
            "sha256": sha256_file(resolved),
        }
    package_rows = [
        {
            "name": name,
            "version": status[name].get("Version"),
            "architecture": status[name].get("Architecture"),
        }
        for name in packages
    ]
    return {
        "schema_id": "replay-rootfs-build-current",
        "status": "passed",
        "source_image_digest": source_image_digest,
        "source_os_release_sha256": sha256_file(
            source_root / "etc/os-release"
        ),
        "seed_packages": list(SEED_PACKAGES),
        "optional_casefold_collision_pruning": {
            "patterns": list(OPTIONAL_CASEFOLD_COLLISION_PATTERNS),
            "scope": "Perl generated headers and POD documentation only",
            "semantic_executables_or_shared_libraries_pruned": False,
        },
        "packages": package_rows,
        "package_count": len(package_rows),
        "tools": tool_rows,
        "copied_package_member_count": len(copied_paths),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-image-digest", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    result = build_minimal_rootfs(
        args.source_root.resolve(),
        args.output.resolve(),
        source_image_digest=args.source_image_digest,
    )
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
