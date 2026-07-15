import { expect, test } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
const descriptorSource = JSON.parse(
  fs.readFileSync(path.join(process.cwd(), "src", "metric-descriptors.json"), "utf8"),
) as Record<string, {
  absoluteField: string; relativeField: string; meanField: string; medianField: string;
  direction: "lower"; label: string; unit: string; availability: "required" | "optional";
  baselineRelativeMeaningful: boolean;
}>;

const publishedDescriptors = Object.fromEntries(Object.entries(descriptorSource).map(([key, value]) => [key, {
  absolute_field: value.absoluteField, relative_field: value.relativeField,
  mean_field: value.meanField, median_field: value.medianField,
  direction: value.direction, label: value.label, unit: value.unit,
  availability: value.availability,
  baseline_relative_meaningful: value.baselineRelativeMeaningful,
  absolute_available: key !== "estimated_monetary_cost",
  relative_available: key !== "estimated_monetary_cost" && value.baselineRelativeMeaningful,
}]));

const metricValues = (tokens: number, time: number, calls: number) => ({
  modeled_weighted_token_load: tokens, observed_non_cached_input_tokens: tokens * .8,
  output_tokens_including_reasoning: tokens * .1, reasoning_output_tokens_including_reasoning: tokens * .05,
  solve_wall_seconds: time, warm_workflow_seconds: time + 10,
  execution_calls_started: calls, intended_tool_successful_calls: 2,
  estimated_monetary_cost: null,
});
const makeRun = (treatment: string, issue: string, repetition: number, correctness: number, tokens: number, time: number, calls: number, eligible = true) => ({
  treatment, issue_id: issue, repetition, correctness, operational_eligible: eligible,
  exclusion_reason: eligible ? null : "trust-invalid", task_success: false,
  strict_attribution_supported: treatment === "baseline-none" ? null : false,
  metrics: metricValues(tokens, time, calls),
});
const data = {
  schema_version: "operational-dashboard-v4", suite_id: "browser-fixture",
  analysis_mode: "repeated_matched", tolerance_grid: [0, 1, 2.5, 5, 7.5, 10],
  default_tolerance: 2.5, metric_descriptors: publishedDescriptors, points: [],
  individual_runs: [
    makeRun("baseline-none", "a", 1, 30, 1000, 500, 10),
    makeRun("baseline-none", "a", 2, 30, 100, 300, 20),
    makeRun("baseline-none", "a", 3, 30, 100, 300, 20),
    makeRun("tool", "a", 1, 30, 700, 400, 8),
    makeRun("tool", "a", 2, 30, 70, 200, 12),
    makeRun("tool", "a", 3, 30, 70, 200, 12),
    makeRun("baseline-none", "b", 1, 40, 2000, 600, 30),
    makeRun("tool", "b", 1, 35, 1000, 300, 15),
    makeRun("invalid", "a", 1, 100, 1, 1, 1, false),
  ],
  canonical: {comparisons: {}, coverage: {}, complete_block_frontier: {}, exact_pareto_frontier: [], tolerance_aware_pareto_frontiers: {}, preference_profiles: {}, objective_specific_winners: {}, operational_stability: {}, observed_findings: {}, supported_findings: {}, correctness_tolerance_lenses: {}, resource_priority_candidates: {}},
};

test("offline dashboard controls and table remain synchronized", async ({page}) => {
  const errors: string[] = [];
  const external: string[] = [];
  page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
  page.on("request", request => { if (!request.url().startsWith("file:") && !request.url().startsWith("data:")) external.push(request.url()); });
  await page.emulateMedia({reducedMotion: "reduce"});
  const template = fs.readFileSync(path.join(process.cwd(), "dist", "index.html"), "utf8");
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "dashboard-browser-"));
  const target = path.join(directory, "index.html");
  fs.writeFileSync(target, template.replace("__DASHBOARD_DATA__", JSON.stringify(data).replaceAll("<", "\\u003c")));
  await page.goto(pathToFileURL(target).href);
  await expect(page.getByTestId("data-table")).toBeVisible();
  await expect(page.getByLabel("Quality axis")).toBeVisible();
  await expect(page.getByLabel("Token view")).toBeVisible();
  await expect(page.getByLabel("Quality axis").locator('option[value="requested_behavior"]')).toHaveAttribute("disabled", "");
  await expect(page.getByLabel("Token view").locator('option[value="cache_writes"]')).toHaveAttribute("disabled", "");
  await expect(page.getByText(/minimum eligibility period, not an eviction guarantee/)).toBeVisible();
  await expect(page.getByText(/does not retroactively rescore historical suites/)).toBeVisible();
  await expect(page.locator('tr[data-treatment="tool"]')).toContainText("460.00");
  await page.getByRole("button", {name: "Relative to baseline"}).click();
  await page.getByLabel("X-axis metric").selectOption("solve_wall_seconds");
  await expect(page.locator('tr[data-treatment="tool"]')).toContainText("-35.07");
  await page.getByLabel("X-axis metric").selectOption("execution_calls_started");
  await expect(page.locator('tr[data-treatment="tool"]')).toContainText("-38.40");
  await expect(page.locator('tr[data-treatment="tool"]')).toContainText("Not estimable");
  await page.getByLabel("Issue", {exact: true}).selectOption("b");
  await expect(page.locator('tr[data-treatment="tool"]')).toContainText("-50.00");
  await page.getByLabel("Issue", {exact: true}).selectOption("a");
  await page.getByLabel("Summary statistic").selectOption("median");
  await page.getByRole("button", {name: "Absolute"}).click();
  await page.getByLabel("X-axis metric").selectOption("modeled_weighted_token_load");
  await expect(page.locator('tr[data-treatment="tool"]')).toContainText("70.00");
  const before = await page.locator("svg path").count();
  await page.getByLabel("Individual runs").check();
  await expect.poll(() => page.locator("svg path").count()).toBeGreaterThan(before);
  await page.getByLabel("Correctness-loss tolerance").selectOption("5");
  await expect(page.getByLabel("Correctness-loss tolerance")).toHaveValue("5");
  await expect(page.getByLabel("X-axis metric").locator('option[value="estimated_monetary_cost"]')).toHaveAttribute("disabled", "");
  await page.getByLabel("Include non-adherent or trust-invalid").check();
  await expect(page.locator('tr[data-treatment="invalid"]')).toContainText("Excluded: trust-invalid");
  await page.getByRole("button", {name: "Absolute"}).focus();
  await expect(page.getByRole("button", {name: "Absolute"})).toBeFocused();
  await page.keyboard.press("Tab");
  expect(await page.evaluate(() => document.activeElement?.tagName)).not.toBe("BODY");
  expect(await page.locator("main").getAttribute("aria-hidden")).not.toBe("true");
  expect(errors).toEqual([]);
  expect(external).toEqual([]);
});
