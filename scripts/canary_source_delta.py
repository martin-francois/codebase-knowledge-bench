#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def classify(path: str) -> str:
    if path == "configs/canonical-three-repetition.toml": return "canonical_config"
    if path.startswith("tests/"): return "test"
    if path.startswith("schemas/"): return "schema"
    if path.startswith("dashboard/"): return "dashboard"
    if path in {"SPEC.md", "README.md", "AGENTS.md"} or path.startswith("docs/"): return "documentation"
    if "validate" in path: return "validator"
    if "operator_summary" in path or "publish" in path: return "publisher"
    if "tradeoff" in path or "analysis" in path: return "analysis"
    return "runtime"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("reference")
    parser.add_argument("destination")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip()
    lines = subprocess.check_output(["git", "diff", "--name-status", f"{args.reference}..{head}"], cwd=root, text=True).splitlines()
    files = []
    for line in lines:
        status, path = line.split("\t", 1)
        files.append({"path": path, "git_status": status, "classification": classify(path)})
    payload = {"schema_version": "canary-source-delta-v1", "reference_commit": args.reference, "head_commit": head, "head_tree": tree, "files": files}
    destination = Path(args.destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "canary-source-delta.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    rows = "\n".join(f"| {item['git_status']} | {item['path']} | {item['classification']} |" for item in files)
    (destination / "canary-source-delta.md").write_text(f"# Canary source delta\n\n- Reference: `{args.reference}`\n- HEAD: `{head}`\n- Tree: `{tree}`\n\n| Status | Path | Classification |\n| --- | --- | --- |\n{rows}\n")
    return 0


if __name__ == "__main__": raise SystemExit(main())
