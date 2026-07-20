#!/usr/bin/env python3
"""Semantic formula and field audit for current normative documents."""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Mapping


DOCUMENTS = ("SPEC.md", "SCORING-MODEL.md", "README.md", "CONTRIBUTING.md", "docs/methodology.md", "docs/result-schema.md")
RETIRED = (
    "Token accounting v2",
    "legacy_weighted_tokens_v1_reasoning_double_counted",
    "common_regression_pass_fraction",
)
STALE_FORMULA = re.compile(
    r"output_tokens_including_reasoning\s*\+\s*reasoning_output_tokens|"
    r"reasoning_output_tokens\s*\+\s*(?:0\.1\s*\*\s*)?cached_input_tokens",
    re.IGNORECASE,
)


def _production_formula(repo: Path) -> dict[str, object]:
    tree = ast.parse((repo / "scripts/current_methodology.py").read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "weighted_token_count")
    returned = next(node for node in ast.walk(function) if isinstance(node, ast.Return))
    names = sorted(node.id for node in ast.walk(returned.value) if isinstance(node, ast.Name))
    attributes = sorted(node.attr for node in ast.walk(returned.value) if isinstance(node, ast.Attribute))
    fields = sorted(
        node.value for node in ast.walk(returned.value)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    return {"names": names, "attributes": attributes, "fields": fields, "ast": ast.dump(returned.value, include_attributes=False)}


def audit_texts(repo: Path, texts: Mapping[str, str]) -> dict[str, object]:
    findings = []
    for name, text in texts.items():
        retired = [term for term in RETIRED if term.lower() in text.lower()]
        stale = [match.group(0) for match in STALE_FORMULA.finditer(text)]
        if retired or stale:
            findings.append({"document": name, "retired_fields": retired, "stale_formula_fragments": stale})
    production = _production_formula(repo)
    production_valid = (
        "output_tokens_including_reasoning" in production["fields"]
        and "reasoning_output_tokens" not in production["fields"]
        and "cached_input_tokens" in production["fields"]
    )
    spec = texts.get("SPEC.md", "")
    scoring = texts.get("SCORING-MODEL.md", "")
    formula_documents_valid = all(
        "observed_non_cached_input_tokens" in text
        and "cached_input_tokens" in text
        and "output_tokens_including_reasoning" in text
        for text in (spec, scoring)
    )
    return {
        "status": "passed" if not findings and production_valid and formula_documents_valid else "failed",
        "documents": sorted(texts),
        "findings": findings,
        "production_formula": production,
        "production_formula_valid": production_valid,
        "formula_documents_valid": formula_documents_valid,
    }


def run(repo: Path) -> dict[str, object]:
    texts = {name: (repo / name).read_text(encoding="utf-8") for name in DOCUMENTS}
    positive = audit_texts(repo, texts)
    negative_fixtures = []
    injection = "\nToken accounting v2\ncommon_regression_pass_fraction\noutput_tokens_including_reasoning + reasoning_output_tokens + 0.1 * cached_input_tokens\n"
    for name in DOCUMENTS:
        mutated = dict(texts)
        mutated[name] += injection
        result = audit_texts(repo, mutated)
        negative_fixtures.append({"document": name, "rejected": result["status"] == "failed", "findings": result["findings"]})
    return {
        "schema_id": "normative-document-audit-current",
        **positive,
        "negative_fixtures": negative_fixtures,
        "all_targeted_negative_fixtures_rejected": all(row["rejected"] for row in negative_fixtures),
        "invocation": "uv run python scripts/normative_document_audit.py --output <evidence-root>/audit/normative-document-audit.json",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.repo.resolve())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "passed" and result["all_targeted_negative_fixtures_rejected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
