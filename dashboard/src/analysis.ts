export type MetricKey =
  | "modeled_weighted_token_load"
  | "non_cached_input_tokens"
  | "output_tokens"
  | "reasoning_output_tokens"
  | "solve_wall_seconds"
  | "warm_workflow_seconds"
  | "execution_calls_started"
  | "intended_tool_successful_calls"
  | "estimated_monetary_cost";

export type MetricDescriptor = {
  absoluteField: MetricKey;
  relativeField: string;
  meanField: string;
  medianField: string;
  direction: "lower";
  label: string;
  unit: string;
  availability: "required" | "optional";
  baselineRelativeMeaningful: boolean;
};

import descriptorSource from "./metric-descriptors.json";

export const METRICS = descriptorSource as Record<MetricKey, MetricDescriptor>;
/* The checked-in JSON is also consumed by Python publication code. */

export type DashboardRun = {
  issue_id: string;
  repetition: number;
  treatment: string;
  operational_eligible: boolean;
  exclusion_reason: string | null;
  task_success: boolean;
  strict_attribution_supported: boolean | null;
  correctness: number | null;
  metrics: Record<MetricKey, number | null>;
};

export type DashboardData = {
  schema_version: string;
  suite_id: string;
  analysis_mode: string;
  tolerance_grid: number[];
  default_tolerance: number;
  metric_descriptors: Record<string, {
    absolute_field: string;
    relative_field: string;
    mean_field: string;
    median_field: string;
    direction: "lower";
    label: string;
    unit: string;
    availability: "required" | "optional";
    baseline_relative_meaningful: boolean;
    absolute_available: boolean;
    relative_available: boolean;
  }>;
  individual_runs: DashboardRun[];
  canonical: {
    comparisons: Record<string, unknown>;
    coverage: Record<string, unknown>;
    exact_pareto_frontier: string[];
    tolerance_aware_pareto_frontiers: Record<string, string[]>;
    preference_profiles: Record<string, unknown>;
    supported_findings?: Record<string, unknown>;
    correctness_tolerance_lenses?: Record<string, unknown>;
    resource_priority_candidates?: Record<string, unknown>;
  };
};

export function assertMetricDescriptorParity(data: DashboardData): void {
  const published = data.metric_descriptors;
  const expectedKeys = (Object.keys(METRICS) as MetricKey[]).sort();
  if (JSON.stringify(Object.keys(published).sort()) !== JSON.stringify(expectedKeys)) {
    throw new Error("published metric descriptor keys differ from the dashboard kernel");
  }
  for (const key of expectedKeys) {
    const expected = METRICS[key];
    const actual = published[key];
    const pairs: Array<[unknown, unknown, string]> = [
      [actual.absolute_field, expected.absoluteField, "absolute field"],
      [actual.relative_field, expected.relativeField, "relative field"],
      [actual.mean_field, expected.meanField, "mean field"],
      [actual.median_field, expected.medianField, "median field"],
      [actual.direction, expected.direction, "direction"],
      [actual.label, expected.label, "label"],
      [actual.unit, expected.unit, "unit"],
      [actual.availability, expected.availability, "availability"],
      [actual.baseline_relative_meaningful, expected.baselineRelativeMeaningful, "relative meaning"],
    ];
    for (const [actualValue, expectedValue, field] of pairs) {
      if (actualValue !== expectedValue) throw new Error(`${key}: ${field} descriptor mismatch`);
    }
  }
}

export type Filters = {
  issue: string;
  repetition: string;
  statistic: "mean" | "median";
  tolerance: number;
  includeInvalid: boolean;
};

export type ViewPoint = {
  treatment: string;
  correctness: number | null;
  metricValue: number | null;
  correctnessDelta: number | null;
  metricChangePercent: number | null;
  taskSuccessRate: number | null;
  coverageFraction: number | null;
  frontier: boolean;
  authoritative: boolean;
  exclusionReason: string | null;
  intervalStatus: "estimable" | "not_estimable";
  correctnessLower: number | null;
  correctnessUpper: number | null;
  metricLower: number | null;
  metricUpper: number | null;
};

export type IndividualViewPoint = DashboardRun & {
  correctnessDelta: number | null;
  metricChangePercent: number | null;
  matched: boolean;
};

const average = (values: number[]) =>
  values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
const median = (values: number[]) => {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
};
const summarize = (values: number[], statistic: "mean" | "median") =>
  statistic === "mean" ? average(values) : median(values);
const blockId = (run: DashboardRun) => `${run.issue_id}::${run.repetition}`;

export function metricAvailability(data: DashboardData, relative: boolean): Record<MetricKey, boolean> {
  const baseline = data.individual_runs.filter(run => run.treatment === "baseline-none" && run.operational_eligible);
  return Object.fromEntries((Object.keys(METRICS) as MetricKey[]).map(key => {
    const hasValues = data.individual_runs.some(run => run.metrics[key] != null);
    const baselineHasNonzero = baseline.some(run => (run.metrics[key] ?? 0) > 0);
    return [key, hasValues && (!relative || (METRICS[key].baselineRelativeMeaningful && baselineHasNonzero))];
  })) as Record<MetricKey, boolean>;
}

function dominates(a: ViewPoint, b: ViewPoint, tolerance: number): boolean {
  if (a.correctness == null || b.correctness == null || a.metricValue == null || b.metricValue == null) return false;
  return a.correctness >= b.correctness - tolerance
    && a.metricValue <= b.metricValue
    && (a.correctness > b.correctness || a.metricValue < b.metricValue);
}

const summarizeRatios = (ratios: number[], statistic: "mean" | "median") => {
  if (!ratios.length) return null;
  const logs = ratios.map(Math.log);
  const logSummary = summarize(logs, statistic);
  return logSummary == null ? null : Math.exp(logSummary);
};

export function deriveView(
  data: DashboardData,
  metric: MetricKey,
  filters: Filters,
  view: "absolute" | "relative" = "absolute",
): {points: ViewPoint[]; individualRuns: IndividualViewPoint[]; frontier: string[]; objectiveWinners: Record<string, string[]>} {
  if (!data.tolerance_grid.includes(filters.tolerance)) {
    throw new Error(`unsupported correctness tolerance: ${filters.tolerance}`);
  }
  const selected = data.individual_runs
    .filter(run => filters.issue === "all" || run.issue_id === filters.issue)
    .filter(run => filters.repetition === "all" || String(run.repetition) === filters.repetition);
  const authoritative = selected.filter(run => run.operational_eligible);
  const displayed = filters.includeInvalid ? selected : authoritative;
  const baselineByBlock = new Map(
    authoritative
      .filter(run => run.treatment === "baseline-none")
      .map(run => [blockId(run), run]),
  );
  const scheduled = baselineByBlock.size;
  const treatments = [...new Set(displayed.map(run => run.treatment))].sort();
  const authoritativeTreatments = [...new Set(authoritative.map(run => run.treatment))].sort();
  const blockSets = authoritativeTreatments.map(treatment => new Set(
    authoritative.filter(run => run.treatment === treatment).map(blockId),
  ));
  const completeBlocks = new Set(
    blockSets.length ? [...blockSets[0]].filter(block => blockSets.every(set => set.has(block))) : [],
  );
  const points: ViewPoint[] = treatments.map(treatment => {
    const treatmentRows = authoritative.filter(run => run.treatment === treatment);
    const rows = view === "absolute"
      ? treatmentRows.filter(run => completeBlocks.has(blockId(run)))
      : treatmentRows;
    const visibleRows = displayed.filter(run => run.treatment === treatment);
    const matched = rows.filter(run => baselineByBlock.has(blockId(run)));
    const correctnessValues = rows.flatMap(run => run.correctness == null ? [] : [run.correctness]);
    const metricValues = rows.flatMap(run => run.metrics[metric] == null ? [] : [run.metrics[metric] as number]);
    const correctnessDeltas: number[] = [];
    const metricRatios: number[] = [];
    for (const run of matched) {
      const baseline = baselineByBlock.get(blockId(run))!;
      if (run.correctness != null && baseline.correctness != null) correctnessDeltas.push(run.correctness - baseline.correctness);
      const treatmentValue = run.metrics[metric];
      const baselineValue = baseline.metrics[metric];
      if (treatmentValue != null && baselineValue != null && baselineValue !== 0) {
        metricRatios.push(treatmentValue / baselineValue);
      }
    }
    const summarizedRatio = summarizeRatios(metricRatios, filters.statistic);
    const isAuthoritative = rows.length > 0;
    return {
      treatment,
      correctness: summarize(correctnessValues, filters.statistic),
      metricValue: summarize(metricValues, filters.statistic),
      correctnessDelta: treatment === "baseline-none" ? 0 : summarize(correctnessDeltas, filters.statistic),
      metricChangePercent: treatment === "baseline-none" ? 0 : summarizedRatio == null ? null : 100 * (summarizedRatio - 1),
      taskSuccessRate: rows.length ? rows.filter(run => run.task_success).length / rows.length : null,
      coverageFraction: scheduled ? matched.length / scheduled : null,
      frontier: false,
      authoritative: isAuthoritative,
      exclusionReason: isAuthoritative ? null : visibleRows.map(run => run.exclusion_reason).filter(Boolean).join("; ") || "operationally ineligible",
      intervalStatus: "not_estimable",
      correctnessLower: null,
      correctnessUpper: null,
      metricLower: null,
      metricUpper: null,
    };
  });
  const authoritativePoints = points.filter(point => point.authoritative);
  const frontier = authoritativePoints
    .filter(point => !authoritativePoints.some(other => other !== point && dominates(other, point, filters.tolerance)))
    .map(point => point.treatment).sort();
  points.forEach(point => { point.frontier = frontier.includes(point.treatment); });
  const winners = (field: "correctness" | "metricValue", direction: "higher" | "lower") => {
    const available = authoritativePoints.filter(point => point[field] != null);
    if (!available.length) return [];
    const values = available.map(point => point[field] as number);
    const best = direction === "higher" ? Math.max(...values) : Math.min(...values);
    return available.filter(point => point[field] === best).map(point => point.treatment).sort();
  };
  const individualRuns: IndividualViewPoint[] = displayed.map(run => {
    const baseline = baselineByBlock.get(blockId(run));
    const baselineMetric = baseline?.metrics[metric];
    const runMetric = run.metrics[metric];
    return {
      ...run,
      matched: run.treatment === "baseline-none" || Boolean(baseline),
      correctnessDelta: run.treatment === "baseline-none" ? 0 :
        baseline && run.correctness != null && baseline.correctness != null ? run.correctness - baseline.correctness : null,
      metricChangePercent: run.treatment === "baseline-none" ? 0 :
        baselineMetric != null && baselineMetric !== 0 && runMetric != null ? 100 * (runMetric / baselineMetric - 1) : null,
    };
  });
  return {
    points,
    individualRuns,
    frontier,
    objectiveWinners: {
      highest_correctness: winners("correctness", "higher"),
      lowest_selected_metric: winners("metricValue", "lower"),
    },
  };
}
