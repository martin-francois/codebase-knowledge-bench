#!/usr/bin/env python3
"""Structured publication sanitization and embedded-manifest validation."""

from __future__ import annotations

import hashlib
import json
import re
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


PLACEHOLDER_RE = re.compile(r"\$[A-Z][A-Z0-9_]*")


def _replace_prefixes(value: str, prefixes: dict[str, str]) -> str:
    result = value
    for raw_prefix, replacement in sorted(prefixes.items(), key=lambda item: -len(item[0])):
        prefix = raw_prefix.rstrip("/")
        if not prefix or not Path(prefix).is_absolute():
            continue
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_.-]){re.escape(prefix)}(?=$|[/\\\s\"'`),:;\]}}])"
        )
        result = pattern.sub(replacement.rstrip("/"), result)
    return result


def sanitize_value(value: Any, prefixes: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _replace_prefixes(value, prefixes)
    if isinstance(value, list):
        return [sanitize_value(item, prefixes) for item in value]
    if isinstance(value, dict):
        return {key: sanitize_value(item, prefixes) for key, item in value.items()}
    return value


def sanitize_payload(data: bytes, suffix: str, prefixes: dict[str, str]) -> bytes:
    """Sanitize derived publication bytes without touching malformed raw evidence."""
    if suffix == ".json":
        parsed = json.loads(data.decode("utf-8"))
        return (json.dumps(sanitize_value(parsed, prefixes), indent=2, sort_keys=True) + "\n").encode()
    if suffix == ".jsonl":
        output: list[bytes] = []
        for line in data.splitlines(keepends=True):
            ending = b"\n" if line.endswith(b"\n") else b""
            body = line.rstrip(b"\r\n")
            try:
                parsed = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                output.append(line)
                continue
            output.append(
                json.dumps(sanitize_value(parsed, prefixes), sort_keys=True, separators=(",", ":")).encode()
                + ending
            )
        return b"".join(output)
    if suffix in {".md", ".txt", ".log"}:
        return _replace_prefixes(data.decode("utf-8"), prefixes).encode()
    return data


def canonical_relative_path(raw: str) -> PurePosixPath:
    if not raw or PLACEHOLDER_RE.search(raw) or "\\" in raw:
        raise ValueError(f"non-portable manifest path: {raw!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"non-canonical manifest path: {raw!r}")
    if path.as_posix() != raw:
        raise ValueError(f"non-canonical manifest path: {raw!r}")
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_embedded_manifests(root: Path) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    errors: list[str] = []
    for manifest_path in sorted(root.rglob("review-manifest.json")):
        relative_manifest = manifest_path.relative_to(root).as_posix()
        checked = 0
        try:
            document = json.loads(manifest_path.read_text())
            declared_root = document.get("manifest_root", ".")
            if declared_root != ".":
                raise ValueError("manifest_root must be '.' in published review manifests")
            entries = document.get("artifacts", document.get("entries", []))
            if not isinstance(entries, list):
                raise ValueError("manifest entries must be a list")
            for entry in entries:
                if not isinstance(entry, dict) or not entry.get("required", True):
                    continue
                rel = canonical_relative_path(str(entry.get("path", "")))
                target = manifest_path.parent.joinpath(*rel.parts)
                if not target.is_file():
                    raise ValueError(f"missing required artifact {rel}")
                if "bytes" in entry and target.stat().st_size != int(entry["bytes"]):
                    raise ValueError(f"byte mismatch for {rel}")
                if entry.get("sha256") and sha256_file(target) != entry["sha256"]:
                    raise ValueError(f"hash mismatch for {rel}")
                checked += 1
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            errors.append(f"{relative_manifest}: {exc}")
        reports.append({"manifest": relative_manifest, "required_entries_checked": checked})
    return {"schema_version": "embedded-manifest-validation-v1", "manifests": reports, "errors": errors}


def _safe_tar_members(archive: tarfile.TarFile) -> Iterable[tarfile.TarInfo]:
    for member in archive.getmembers():
        canonical_relative_path(member.name.rstrip("/"))
        if member.issym() or member.islnk():
            raise ValueError(f"source archive contains link: {member.name}")
        yield member


def validate_source_roles(root: Path) -> dict[str, Any]:
    checked: list[dict[str, Any]] = []
    errors: list[str] = []
    for lineage_path in sorted(root.rglob("recompute-lineage.json")):
        try:
            lineage = json.loads(lineage_path.read_text())
            provenance = lineage.get("role_source_provenance", {})
            archive_name = lineage.get("recompute_source_archive", {}).get("archive")
            if not provenance or not archive_name:
                continue
            candidates = sorted(root.rglob(Path(str(archive_name)).name))
            if len(candidates) != 1:
                raise ValueError(f"expected one effective-source archive, found {len(candidates)}")
            with tempfile.TemporaryDirectory(prefix="source-reconstruct-") as temp:
                target = Path(temp)
                with tarfile.open(candidates[0], "r:*") as archive:
                    archive.extractall(target, members=_safe_tar_members(archive))
                for role, record in sorted(provenance.items()):
                    for source in record.get("sources", []):
                        rel = canonical_relative_path(str(source["path"]))
                        source_path = target.joinpath(*rel.parts)
                        if not source_path.is_file():
                            raise ValueError(f"{role}: missing source {rel}")
                        if sha256_file(source_path) != source["sha256"]:
                            raise ValueError(f"{role}: source hash mismatch {rel}")
                    checked.append({"lineage": lineage_path.relative_to(root).as_posix(), "role": role})
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, tarfile.TarError) as exc:
            errors.append(f"{lineage_path.relative_to(root)}: {exc}")
    return {"schema_version": "source-role-validation-v1", "roles": checked, "errors": errors}


def validate_report_consistency(root: Path) -> dict[str, Any]:
    errors: list[str] = []
    checked: list[str] = []
    for results_path in sorted(root.rglob("results.json")) + sorted(root.rglob("suite-results.json")):
        if "original-derived" in results_path.parts:
            continue
        try:
            document = json.loads(results_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{results_path.relative_to(root)}: invalid results JSON: {exc}")
            continue
        rows = document.get("variants", document.get("variant_rows", []))
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if (not row.get("task_success") or not row.get("operational_rank_eligible")) and row.get("operational_rank") is not None:
                    errors.append(f"{results_path.relative_to(root)}: failed/ineligible arm has operational_rank")
                if row.get("variant") != "baseline-none" and row.get("operational_rank_eligible") and int(
                    row.get("intended_tool_successful_solve_invocation_count")
                    or row.get("intended_tool_successful_calls") or 0
                ) < 1:
                    errors.append(f"{results_path.relative_to(root)}: non-baseline eligibility lacks successful invocation")
        conclusion = document.get("aggregates", {}).get("operational_conclusion", document.get("operational_conclusion", {}))
        no_winner = isinstance(conclusion, dict) and "no operational winner" in str(conclusion.get("statement", "")).lower()
        report_candidates = [results_path.with_name("benchmark-report.md"), results_path.with_name("suite-report.md")]
        for report_path in report_candidates:
            if not report_path.is_file():
                continue
            checked.append(report_path.relative_to(root).as_posix())
            text = report_path.read_text(encoding="utf-8", errors="replace")
            if "## Ranked Table" in text:
                errors.append(f"{report_path.relative_to(root)}: descriptive ordering presented as operational ranking")
            if no_winner and re.search(r"(?i)(?:best operational (?:workflow|treatment)|scalar leader|observed pilot leader:\s*\*\*[^n])", text):
                errors.append(f"{report_path.relative_to(root)}: human report names a positive leader despite no winner")
    return {"schema_version": "report-consistency-validation-v1", "reports": sorted(set(checked)), "errors": errors}
