#!/usr/bin/env python3
"""Build and validate the self-contained operational dashboard."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard"
SCHEMA = ROOT / "schemas" / "dashboard-data.schema.json"
VERSION = "operational-dashboard-v1"


def _mean_metric(record: dict[str, Any], field: str) -> float | None:
    value = record.get(field)
    if isinstance(value, dict):
        value = value.get("mean")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def dashboard_data(suite_result: dict[str, Any]) -> dict[str, Any]:
    aggregates = suite_result["aggregates"]
    analysis = aggregates["operational_tradeoffs"]
    points = []
    comparisons = analysis.get("matched_comparisons", {})
    for treatment, aggregate in sorted(analysis["absolute_quality"].items()):
        mean = aggregate["mean"]
        comparison = comparisons.get(treatment, {})
        relative = comparison.get("relative_to_matched_baseline", {})
        task = aggregate["task_success"]
        attribution_values = [
            row.get("attribution", {}).get("strict_direct_attribution_supported")
            for row in suite_result.get("variant_rows", [])
            if row.get("variant") == treatment
        ]
        strict = (
            all(value is True for value in attribution_values)
            if attribution_values and treatment != "baseline-none"
            else None
        )
        points.append({
            "treatment": treatment,
            "correctness": mean["correctness"],
            "modeled_weighted_token_load": mean["tokens"],
            "non_cached_input_tokens": _mean_metric(
                aggregates["by_variant"].get(treatment, {}), "non_cached_input_tokens"
            ),
            "output_tokens": _mean_metric(
                aggregates["by_variant"].get(treatment, {}), "output_tokens"
            ),
            "solve_wall_seconds": mean["time"],
            "warm_workflow_seconds": mean.get("warm_time"),
            "execution_calls_started": mean.get("calls"),
            "intended_tool_successful_calls": _mean_metric(
                aggregates["by_variant"].get(treatment, {}),
                "intended_tool_successful_solve_invocation_count",
            ),
            "estimated_monetary_cost": mean.get("cost"),
            "task_success_rate": (
                task["numerator"] / task["denominator"] if task["denominator"] else 0.0
            ),
            "operational_eligible": True,
            "strict_attribution_supported": strict,
            "correctness_delta": relative.get("correctness_delta_points", 0.0),
            "token_change_percent": (
                None if relative.get("token_ratio") is None
                else 100.0 * (relative["token_ratio"] - 1.0)
            ),
            "time_change_percent": (
                None if relative.get("time_ratio") is None
                else 100.0 * (relative["time_ratio"] - 1.0)
            ),
            "call_change_percent": (
                None if relative.get("call_ratio") is None
                else 100.0 * (relative["call_ratio"] - 1.0)
            ),
            "intervals": comparison.get("paired_intervals", {}),
            "median": aggregate.get("median", {}),
        })
    return {
        "schema_version": VERSION,
        "suite_id": suite_result["suite_id"],
        "analysis_mode": (
            "pilot_only" if analysis["decision_summary"]["pilot_only"] else "repeated"
        ),
        "tolerance_grid": analysis["correctness_loss_tolerance_grid_points"],
        "default_tolerance": 2.0,
        "points": points,
        "exact_pareto_frontier": analysis["exact_pareto_frontier"],
        "tolerance_aware_pareto_frontiers": analysis[
            "tolerance_aware_pareto_frontiers"
        ],
        "individual_runs": [
            {
                "issue_id": row.get("issue_id"),
                "repetition": row.get("repetition"),
                "treatment": row.get("variant"),
                "correctness": row.get("operational_correctness_score"),
                "modeled_weighted_token_load": row.get("modeled_weighted_token_load"),
                "solve_wall_seconds": row.get("solve_wall_seconds"),
                "warm_workflow_seconds": row.get("warm_workflow_seconds"),
                "execution_calls_started": row.get("execution_calls_started"),
                "intended_tool_successful_calls": row.get(
                    "intended_tool_successful_solve_invocation_count"
                ),
                "non_cached_input_tokens": row.get("non_cached_input_tokens"),
                "output_tokens": row.get("output_tokens"),
                "relative_to_matched_baseline": row.get(
                    "relative_to_matched_baseline"
                ),
                "operational_eligible": row.get("operational_rank_eligible"),
            }
            for row in suite_result.get("variant_rows", [])
        ],
    }


def build_dashboard(suite_dir: Path, suite_result: dict[str, Any]) -> Path:
    output = suite_dir / "report-assets" / "operational-dashboard"
    output.mkdir(parents=True, exist_ok=True)
    data = dashboard_data(suite_result)
    (output / "dashboard-data.json").write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    shutil.copy2(SCHEMA, output / "dashboard-data.schema.json")
    specs = output / "chart-specs"
    specs.mkdir(exist_ok=True)
    for name, x, y in (
        ("absolute", "modeled_weighted_token_load", "correctness"),
        ("baseline-relative", "token_change_percent", "correctness_delta"),
    ):
        (specs / f"{name}.json").write_text(json.dumps({
            "$schema": "https://vega.github.io/schema/vega-lite/v6.json",
            "description": f"{name} operational benchmark scatter chart",
            "mark": {"type": "point", "filled": True},
            "encoding": {
                "x": {"field": x, "type": "quantitative"},
                "y": {"field": y, "type": "quantitative"},
                "shape": {"field": "treatment", "type": "nominal"},
            },
        }, indent=2) + "\n", encoding="utf-8")
    subprocess.run(
        ["npm", "run", "build"],
        cwd=DASHBOARD,
        check=True,
        timeout=180,
    )
    template = (DASHBOARD / "dist" / "index.html").read_text(encoding="utf-8")
    if "__DASHBOARD_DATA__" not in template:
        raise ValueError("dashboard build omitted the dashboard-data marker")
    embedded = json.dumps(data, separators=(",", ":")).replace("<", "\\u003c")
    (output / "index.html").write_text(
        template.replace("__DASHBOARD_DATA__", embedded), encoding="utf-8"
    )
    return output


def validate_dashboard(
    suite_dir: Path, suite_result: dict[str, Any], errors: list[str]
) -> None:
    output = suite_dir / "report-assets" / "operational-dashboard"
    required = [
        output / "index.html",
        output / "dashboard-data.json",
        output / "dashboard-data.schema.json",
        output / "chart-specs" / "absolute.json",
        output / "chart-specs" / "baseline-relative.json",
    ]
    for path in required:
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"missing dashboard artifact: {path.relative_to(suite_dir)}")
    if errors or not (output / "dashboard-data.json").is_file():
        return
    stored = json.loads((output / "dashboard-data.json").read_text(encoding="utf-8"))
    expected = dashboard_data(suite_result)
    if stored != expected:
        errors.append("dashboard data differs from canonical suite analysis")
    page = (output / "index.html").read_text(encoding="utf-8")
    if "__DASHBOARD_DATA__" in page:
        errors.append("dashboard contains an unresolved data marker")
    for token in ("src=\"http", "href=\"http", "src='http", "href='http"):
        if token in page.lower():
            errors.append(f"dashboard has an external network dependency: {token}")
    for required_text in (
        "Accessible data table", "Correctness-loss tolerance", "aria-label",
        "prefers-reduced-motion",
    ):
        if required_text not in page:
            errors.append(f"dashboard missing accessibility feature: {required_text}")
