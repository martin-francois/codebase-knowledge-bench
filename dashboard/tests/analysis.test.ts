import { describe, expect, it } from "vitest";
import { DashboardData, DashboardRun, METRICS, deriveView, metricAvailability } from "../src/analysis";

const metrics = (tokens: number, time: number, calls: number, cost: number | null = null) => ({
  modeled_weighted_token_load: tokens,
  non_cached_input_tokens: tokens * .8,
  output_tokens: tokens * .1,
  reasoning_output_tokens: tokens * .05,
  solve_wall_seconds: time,
  warm_workflow_seconds: time + 10,
  execution_calls_started: calls,
  intended_tool_successful_calls: 2,
  estimated_monetary_cost: cost,
});
const run = (treatment: string, issue: string, repetition: number, correctness: number, tokens: number, time: number, calls: number, eligible = true): DashboardRun => ({
  treatment, issue_id: issue, repetition, correctness, operational_eligible: eligible,
  exclusion_reason: eligible ? null : "trust-invalid", task_success: false,
  strict_attribution_supported: treatment === "baseline-none" ? null : false,
  metrics: metrics(tokens, time, calls),
});
const fixture = (): DashboardData => ({
  schema_version: "operational-dashboard-v3", suite_id: "fixture", analysis_mode: "repeated_matched",
  tolerance_grid: [0, 1, 2.5, 5, 7.5, 10], default_tolerance: 2.5,
  individual_runs: [
    run("baseline-none", "a", 1, 30, 1000, 500, 10),
    run("baseline-none", "a", 2, 30, 100, 300, 20),
    run("baseline-none", "a", 3, 30, 100, 300, 20),
    run("tool", "a", 1, 30, 700, 400, 8),
    run("tool", "a", 2, 30, 70, 200, 12),
    run("tool", "a", 3, 30, 70, 200, 12),
    run("baseline-none", "b", 1, 40, 2000, 600, 30),
    run("tool", "b", 1, 35, 1000, 300, 15),
    run("invalid", "a", 1, 100, 1, 1, 1, false),
  ],
  canonical: {comparisons: {}, coverage: {}, exact_pareto_frontier: [], tolerance_aware_pareto_frontiers: {}, preference_profiles: {}},
});

describe("dashboard derivation", () => {
  it("maps every metric to a unique relative field", () => {
    expect(new Set(Object.values(METRICS).map(metric => metric.relativeField)).size)
      .toBe(Object.keys(METRICS).length);
  });
  it("recomputes issue and repetition filters", () => {
    const all = deriveView(fixture(), "modeled_weighted_token_load", {issue: "all", repetition: "all", statistic: "mean", tolerance: 0, includeInvalid: false});
    const issue = deriveView(fixture(), "modeled_weighted_token_load", {issue: "b", repetition: "all", statistic: "mean", tolerance: 0, includeInvalid: false});
    expect(all.points.find(point => point.treatment === "tool")?.metricValue).not.toBe(issue.points.find(point => point.treatment === "tool")?.metricValue);
    const repetition = deriveView(fixture(), "solve_wall_seconds", {issue: "a", repetition: "1", statistic: "mean", tolerance: 0, includeInvalid: false});
    expect(repetition.points.find(point => point.treatment === "tool")?.metricChangePercent).toBeCloseTo(-20);
  });
  it("supports medians for every metric", () => {
    for (const key of Object.keys(METRICS) as Array<keyof typeof METRICS>) {
      const result = deriveView(fixture(), key, {issue: "a", repetition: "all", statistic: "median", tolerance: 0, includeInvalid: false});
      expect(result.points.find(point => point.treatment === "tool")?.metricValue).not.toBeUndefined();
    }
  });
  it("rejects unsupported tolerances", () => {
    expect(() => deriveView(fixture(), "solve_wall_seconds", {issue: "all", repetition: "all", statistic: "mean", tolerance: 3, includeInvalid: false})).toThrow("unsupported");
  });
  it("displays invalid runs without adding them to frontiers", () => {
    const result = deriveView(fixture(), "modeled_weighted_token_load", {issue: "all", repetition: "all", statistic: "mean", tolerance: 0, includeInvalid: true});
    expect(result.points.find(point => point.treatment === "invalid")?.authoritative).toBe(false);
    expect(result.frontier).not.toContain("invalid");
  });
  it("populates intended calls and disables unavailable cost", () => {
    const calls = deriveView(fixture(), "intended_tool_successful_calls", {issue: "all", repetition: "all", statistic: "mean", tolerance: 0, includeInvalid: false});
    expect(calls.points.find(point => point.treatment === "tool")?.metricValue).toBe(2);
    expect(metricAvailability(fixture(), false).estimated_monetary_cost).toBe(false);
  });
  it("does not invent intervals when canonical hierarchical uncertainty is unavailable", () => {
    const repeated = deriveView(fixture(), "solve_wall_seconds", {issue: "a", repetition: "all", statistic: "mean", tolerance: 0, includeInvalid: false});
    const pilot = deriveView(fixture(), "solve_wall_seconds", {issue: "b", repetition: "all", statistic: "mean", tolerance: 0, includeInvalid: false});
    expect(repeated.points.find(point => point.treatment === "tool")?.intervalStatus).toBe("not_estimable");
    expect(pilot.points.find(point => point.treatment === "tool")?.intervalStatus).toBe("not_estimable");
  });
  it("uses geometric paired resource ratios", () => {
    const data = fixture();
    data.individual_runs = [
      run("baseline-none", "a", 1, 30, 100, 100, 10),
      run("tool", "a", 1, 30, 50, 100, 10),
      run("baseline-none", "a", 2, 30, 100, 100, 10),
      run("tool", "a", 2, 30, 200, 100, 10),
    ];
    const result = deriveView(data, "modeled_weighted_token_load", {issue: "all", repetition: "all", statistic: "mean", tolerance: 0, includeInvalid: false}, "relative");
    expect(result.points.find(point => point.treatment === "tool")?.metricChangePercent).toBeCloseTo(0);
  });
  it("uses paired coordinates for relative individual runs", () => {
    const result = deriveView(fixture(), "solve_wall_seconds", {issue: "a", repetition: "1", statistic: "mean", tolerance: 0, includeInvalid: false}, "relative");
    const tool = result.individualRuns.find(run => run.treatment === "tool");
    expect(tool?.correctnessDelta).toBe(0);
    expect(tool?.metricChangePercent).toBeCloseTo(-20);
  });
  it("uses the complete block intersection for absolute points", () => {
    const data = fixture();
    const result = deriveView(data, "modeled_weighted_token_load", {issue: "all", repetition: "all", statistic: "mean", tolerance: 0, includeInvalid: false}, "absolute");
    expect(result.points.find(point => point.treatment === "baseline-none")?.coverageFraction).toBe(1);
    expect(result.points.find(point => point.treatment === "tool")?.coverageFraction).toBe(1);
    expect(result.points.find(point => point.treatment === "baseline-none")?.metricValue).toBeCloseTo(800);
  });
});
