#!/usr/bin/env python3
"""Structured publication sanitization and embedded-manifest validation."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from benchmark_hardening import validate_tool_invocation_artifact


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
                if target.stat().st_size == 0 and not entry.get("may_be_empty", False):
                    raise ValueError(f"required artifact is unexpectedly empty: {rel}")
                checked += 1
            results_path = manifest_path.parent / "results.json"
            telemetry_contract_entries = [
                entry for entry in entries
                if isinstance(entry, dict)
                and str(entry.get("path") or "").endswith("/tool-invocations-solve.jsonl")
                and "may_be_empty" in entry
            ]
            if results_path.is_file() and telemetry_contract_entries:
                results = json.loads(results_path.read_text(encoding="utf-8"))
                for row in results.get("variants", []):
                    run_id = str(row.get("run_id") or "")
                    if not run_id:
                        continue
                    telemetry = manifest_path.parent / "runs" / run_id / "tool-invocations-solve.jsonl"
                    telemetry_errors = validate_tool_invocation_artifact(
                        telemetry,
                        treatment=str(row.get("variant") or ""),
                        solve_expected=bool(row.get("implementation_evaluated") or row.get("operational_rank_eligible")),
                    )
                    if telemetry_errors:
                        raise ValueError(f"{run_id}: {'; '.join(telemetry_errors)}")
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


def _reconstruct_source_archive(
    metadata_path: Path,
    source_metadata: dict[str, Any],
    provenance: dict[str, Any],
    root: Path,
    *,
    archive_path_override: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    archive_name = str(source_metadata.get("archive") or "")
    if not archive_name:
        raise ValueError("effective-source archive is not declared")
    archive_path = archive_path_override or metadata_path.parent / archive_name
    if not archive_path.is_file():
        raise ValueError(f"effective-source archive is missing: {archive_name}")
    if source_metadata.get("archive_sha256") and sha256_file(archive_path) != source_metadata["archive_sha256"]:
        raise ValueError("effective-source archive hash mismatch")
    with tempfile.TemporaryDirectory(prefix="source-reconstruct-") as temp:
        target = Path(temp)
        with tarfile.open(archive_path, "r:*") as archive:
            archive.extractall(target, members=_safe_tar_members(archive))
        declared_entries = source_metadata.get("effective_source_files", [])
        if not declared_entries:
            raise ValueError("effective-source file manifest is empty")
        reconstructed_entries: list[dict[str, str]] = []
        for entry in declared_entries:
            rel = canonical_relative_path(str(entry["path"]))
            source_path = target.joinpath(*rel.parts)
            if not source_path.is_file():
                raise ValueError(f"effective source is missing {rel}")
            actual_hash = sha256_file(source_path)
            if actual_hash != entry["sha256"]:
                raise ValueError(f"effective source hash mismatch {rel}")
            reconstructed_entries.append({"path": rel.as_posix(), "sha256": actual_hash})
        manifest_bytes = json.dumps(
            reconstructed_entries, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
        if manifest_hash != source_metadata.get("source_manifest_sha256"):
            raise ValueError("source manifest digest mismatch")
        content_digest = hashlib.sha256()
        for entry in reconstructed_entries:
            content_digest.update(entry["path"].encode("utf-8") + b"\0")
            content_digest.update(bytes.fromhex(entry["sha256"]))
        content_hash = content_digest.hexdigest()
        if content_hash != source_metadata.get("effective_source_content_sha256"):
            raise ValueError("effective source content digest mismatch")
        if source_metadata.get("source_hash_algorithm") != "sha256(path_utf8_nul_file_sha256_bytes)":
            raise ValueError("unsupported source hash algorithm")
        if source_metadata.get("source_hash_version") != "source-content-v1":
            raise ValueError("unsupported source hash version")
        declared_tree = str(source_metadata.get("harness_git_tree") or "")
        if declared_tree:
            subprocess.run(["git", "init", "-q"], cwd=target, check=True)
            subprocess.run(["git", "add", "-A"], cwd=target, check=True)
            actual_tree = subprocess.run(
                ["git", "write-tree"], cwd=target, check=True, text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
            if actual_tree != declared_tree:
                raise ValueError("reconstructed Git tree mismatch")
        checked: list[dict[str, Any]] = []
        for role, record in sorted(provenance.items()):
            files = record.get("sources") or [
                {"path": path, "sha256": record.get("hashes", {}).get(path)}
                for path in record.get("files", [])
            ]
            if not files:
                raise ValueError(f"{role}: source file list is empty")
            for source in files:
                rel = canonical_relative_path(str(source["path"]))
                source_path = target.joinpath(*rel.parts)
                if not source_path.is_file():
                    raise ValueError(f"{role}: missing source {rel}")
                if not source.get("sha256") or sha256_file(source_path) != source["sha256"]:
                    raise ValueError(f"{role}: source hash mismatch {rel}")
            checked.append({
                "metadata": metadata_path.relative_to(root).as_posix(),
                "role": role,
                "effective_source_content_sha256": content_hash,
                "source_manifest_sha256": manifest_hash,
            })
    return checked, {
        "metadata": metadata_path.relative_to(root).as_posix(),
        "archive": archive_path.relative_to(root).as_posix(),
        "effective_source_content_sha256": content_hash,
        "source_manifest_sha256": manifest_hash,
        "harness_git_tree": declared_tree or None,
    }


def validate_source_roles(root: Path) -> dict[str, Any]:
    checked: list[dict[str, Any]] = []
    archives: list[dict[str, Any]] = []
    errors: list[str] = []
    suite_plan_path = root / "suite-plan.json"
    suite_provenance: dict[str, Any] = {}
    if suite_plan_path.is_file():
        try:
            suite_plan = json.loads(suite_plan_path.read_text(encoding="utf-8"))
            suite_provenance = suite_plan.get("model_provenance", {}).get("roles", {})
        except (OSError, TypeError, json.JSONDecodeError) as exc:
            errors.append(f"suite-plan.json: invalid source provenance: {exc}")
    harness_metadata_paths = sorted(root.rglob("harness-source.json"))
    if suite_provenance and not harness_metadata_paths:
        errors.append("suite declares source roles but contains no harness-source.json")
    for metadata_path in harness_metadata_paths:
        try:
            source_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            role_checks, archive_record = _reconstruct_source_archive(
                metadata_path, source_metadata, suite_provenance, root
            )
            checked.extend(role_checks)
            archives.append(archive_record)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, tarfile.TarError, subprocess.SubprocessError) as exc:
            errors.append(f"{metadata_path.relative_to(root)}: {exc}")
    for lineage_path in sorted(root.rglob("recompute-lineage.json")):
        try:
            lineage = json.loads(lineage_path.read_text())
            provenance = lineage.get("role_source_provenance", {})
            source_metadata = lineage.get("recompute_source_archive", {})
            if not provenance or not source_metadata.get("archive"):
                continue
            candidates = sorted(root.rglob(Path(str(source_metadata["archive"])).name))
            if len(candidates) != 1:
                raise ValueError(f"expected one recompute source archive, found {len(candidates)}")
            role_checks, archive_record = _reconstruct_source_archive(
                lineage_path, source_metadata, provenance, root,
                archive_path_override=candidates[0],
            )
            checked.extend(role_checks)
            archives.append(archive_record)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, tarfile.TarError) as exc:
            errors.append(f"{lineage_path.relative_to(root)}: {exc}")
    if suite_provenance and len({item["role"] for item in checked}) < len(suite_provenance):
        errors.append("not every suite-declared source role was reconstructed")
    return {
        "schema_version": "source-role-validation-v2",
        "source_reconstruction_passed": bool(archives) and bool(checked) and not errors,
        "archives": archives,
        "roles": checked,
        "errors": errors,
    }


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
