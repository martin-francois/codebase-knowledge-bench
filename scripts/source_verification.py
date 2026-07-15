#!/usr/bin/env python3
"""Bind semantic review to a Git subject while allowing generated report envelopes."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

GENERATED_PATTERNS = (
    "verification/current-canonical-verification-report.",
    "verification/llm-verification-report.",
    "verification/source-verification-envelope.",
    "verification/token-accounting-erratum.",
    "verification/token-accounting-corrected-effects.csv",
    "verification/verification-changes-table.",
    "verification/vnext-readiness.",
    "verification/test-results.",
    "verification/ci-command-log.txt",
    "verification/allowed-post-review-delta.",
)


def _git(repo: Path, *args: str, binary: bool = False) -> bytes | str:
    output = subprocess.check_output(["git", "-C", str(repo), *args])
    return output if binary else output.decode().strip()


def is_generated(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in GENERATED_PATTERNS)


def subject_manifest(repo: Path, commit: str) -> dict[str, Any]:
    commit = str(_git(repo, "rev-parse", commit))
    paths = str(_git(repo, "ls-tree", "-r", "--name-only", commit)).splitlines()
    entries = []
    for path in sorted(path for path in paths if not is_generated(path)):
        payload = _git(repo, "show", f"{commit}:{path}", binary=True)
        entries.append({"path": path, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return {"commit": commit, "entries": entries, "verification_subject_tree_sha256": hashlib.sha256(canonical).hexdigest()}


def build_envelope(repo: Path, reviewed_commit: str, report_commit: str) -> dict[str, Any]:
    reviewed = subject_manifest(repo, reviewed_commit)
    report = subject_manifest(repo, report_commit)
    changed = str(_git(repo, "diff", "--name-only", reviewed["commit"], report["commit"])).splitlines()
    forbidden = sorted(path for path in changed if not is_generated(path))
    if reviewed["verification_subject_tree_sha256"] != report["verification_subject_tree_sha256"] or forbidden:
        raise ValueError(f"report commit contains an unreviewed subject change: {forbidden}")
    patch = _git(repo, "diff", "--binary", reviewed["commit"], report["commit"], binary=True)
    delta = [{"path": path, "allowed_generated_verification_artifact": is_generated(path)} for path in sorted(changed)]
    return {
        "schema_version": "source-verification-envelope-v1",
        "reviewed_source_commit": reviewed["commit"],
        "verification_subject_manifest": reviewed["entries"],
        "verification_subject_tree_sha256": reviewed["verification_subject_tree_sha256"],
        "report_envelope_commit": report["commit"],
        "report_envelope_tree": str(_git(repo, "rev-parse", f"{report['commit']}^{{tree}}")),
        "allowed_post_review_delta": delta,
        "allowed_post_review_delta_sha256": hashlib.sha256(patch).hexdigest(),
        "subject_unchanged": True,
    }


def validate_envelope(repo: Path, envelope: dict[str, Any]) -> list[str]:
    errors = []
    try:
        actual = build_envelope(repo, envelope["reviewed_source_commit"], envelope["report_envelope_commit"])
    except (KeyError, ValueError, subprocess.CalledProcessError) as error:
        return [str(error)]
    for field in ("verification_subject_tree_sha256", "report_envelope_tree", "allowed_post_review_delta_sha256"):
        if envelope.get(field) != actual[field]:
            errors.append(f"source envelope {field} mismatch")
    if envelope.get("verification_subject_manifest") != actual["verification_subject_manifest"]:
        errors.append("source envelope subject manifest mismatch")
    if envelope.get("allowed_post_review_delta") != actual["allowed_post_review_delta"]:
        errors.append("source envelope allowed delta mismatch")
    return errors


def render(envelope: dict[str, Any]) -> str:
    return "\n".join([
        "# Source verification envelope", "",
        f"- Reviewed implementation commit: `{envelope['reviewed_source_commit']}`",
        f"- Reviewed subject tree: `{envelope['verification_subject_tree_sha256']}`",
        f"- Report envelope commit: `{envelope['report_envelope_commit']}`",
        f"- Report envelope tree: `{envelope['report_envelope_tree']}`",
        f"- Allowed generated delta hash: `{envelope['allowed_post_review_delta_sha256']}`",
        "- Subject unchanged by report generation: `true`", "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo", type=Path)
    parser.add_argument("reviewed_commit")
    parser.add_argument("report_commit")
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    envelope = build_envelope(args.repo.resolve(), args.reviewed_commit, args.report_commit)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "source-verification-envelope.json").write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n")
    (args.output_dir / "source-verification-envelope.md").write_text(render(envelope))
    patch = _git(args.repo.resolve(), "diff", "--binary", envelope["reviewed_source_commit"], envelope["report_envelope_commit"], binary=True)
    (args.output_dir / "allowed-post-review-delta.patch").write_bytes(patch)
    (args.output_dir / "allowed-post-review-delta.json").write_text(json.dumps(envelope["allowed_post_review_delta"], indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
