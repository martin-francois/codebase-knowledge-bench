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
};

export const METRICS: Record<MetricKey, MetricDescriptor> = {
  modeled_weighted_token_load: {
    absoluteField: "modeled_weighted_token_load",
    relativeField: "modeled_weighted_token_load_change_percent",
    meanField: "modeled_weighted_token_load_mean",
    medianField: "modeled_weighted_token_load_median",
    direction: "lower", label: "Modeled weighted token load", unit: "tokens",
  },
  non_cached_input_tokens: {
    absoluteField: "non_cached_input_tokens",
    relativeField: "non_cached_input_tokens_change_percent",
    meanField: "non_cached_input_tokens_mean",
    medianField: "non_cached_input_tokens_median",
    direction: "lower", label: "Non-cached input tokens", unit: "tokens",
  },
  output_tokens: {
    absoluteField: "output_tokens",
    relativeField: "output_tokens_change_percent",
    meanField: "output_tokens_mean",
    medianField: "output_tokens_median",
    direction: "lower", label: "Output tokens", unit: "tokens",
  },
  reasoning_output_tokens: {
    absoluteField: "reasoning_output_tokens",
    relativeField: "reasoning_output_tokens_change_percent",
    meanField: "reasoning_output_tokens_mean",
    medianField: "reasoning_output_tokens_median",
    direction: "lower", label: "Reasoning output tokens", unit: "tokens",
  },
  solve_wall_seconds: {
    absoluteField: "solve_wall_seconds",
    relativeField: "solve_wall_seconds_change_percent",
    meanField: "solve_wall_seconds_mean",
    medianField: "solve_wall_seconds_median",
    direction: "lower", label: "Solve wall time", unit: "seconds",
  },
  warm_workflow_seconds: {
    absoluteField: "warm_workflow_seconds",
    relativeField: "warm_workflow_seconds_change_percent",
    meanField: "warm_workflow_seconds_mean",
    medianField: "warm_workflow_seconds_median",
    direction: "lower", label: "Warm end-to-end time", unit: "seconds",
  },
  execution_calls_started: {
    absoluteField: "execution_calls_started",
    relativeField: "execution_calls_started_change_percent",
    meanField: "execution_calls_started_mean",
    medianField: "execution_calls_started_median",
    direction: "lower", label: "Execution calls started", unit: "calls",
  },
  intended_tool_successful_calls: {
    absoluteField: "intended_tool_successful_calls",
    relativeField: "intended_tool_successful_calls_change_percent",
    meanField: "intended_tool_successful_calls_mean",
    medianField: "intended_tool_successful_calls_median",
    direction: "lower", label: "Successful intended-tool calls", unit: "calls",
  },
  estimated_monetary_cost: {
    absoluteField: "estimated_monetary_cost",
    relativeField: "estimated_monetary_cost_change_percent",
    meanField: "estimated_monetary_cost_mean",
    medianField: "estimated_monetary_cost_median",
    direction: "lower", label: "Estimated monetary cost", unit: "currency",
  },
};

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
  individual_runs: DashboardRun[];
  canonical: {
    comparisons: Record<string, unknown>;
    coverage: Record<string, unknown>;
    exact_pareto_frontier: string[];
    tolerance_aware_pareto_frontiers: Record<string, string[]>;
    preference_profiles: Record<string, unknown>;
  };
};

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
    return [key, hasValues && (!relative || baselineHasNonzero)];
  })) as Record<MetricKey, boolean>;
}

function dominates(a: ViewPoint, b: ViewPoint, tolerance: number): boolean {
  if (a.correctness == null || b.correctness == null || a.metricValue == null || b.metricValue == null) return false;
  return a.correctness >= b.correctness - tolerance
    && a.metricValue <= b.metricValue
    && (a.correctness > b.correctness || a.metricValue < b.metricValue);
}

function percentile(values: number[], p: number): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor((sorted.length - 1) * p)];
}

function simpleInterval(values: number[]): [number | null, number | null] {
  if (values.length < 3) return [null, null];
  return [percentile(values, 0.025), percentile(values, 0.975)];
}

export function deriveView(
  data: DashboardData,
  metric: MetricKey,
  filters: Filters,
): {points: ViewPoint[]; individualRuns: DashboardRun[]; frontier: string[]; objectiveWinners: Record<string, string[]>} {
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
  const points: ViewPoint[] = treatments.map(treatment => {
    const rows = authoritative.filter(run => run.treatment === treatment);
    const visibleRows = displayed.filter(run => run.treatment === treatment);
    const matched = rows.filter(run => baselineByBlock.has(blockId(run)));
    const correctnessValues = rows.flatMap(run => run.correctness == null ? [] : [run.correctness]);
    const metricValues = rows.flatMap(run => run.metrics[metric] == null ? [] : [run.metrics[metric] as number]);
    const correctnessDeltas: number[] = [];
    const metricChanges: number[] = [];
    for (const run of matched) {
      const baseline = baselineByBlock.get(blockId(run))!;
      if (run.correctness != null && baseline.correctness != null) correctnessDeltas.push(run.correctness - baseline.correctness);
      const treatmentValue = run.metrics[metric];
      const baselineValue = baseline.metrics[metric];
      if (treatmentValue != null && baselineValue != null && baselineValue !== 0) {
        metricChanges.push(100 * (treatmentValue / baselineValue - 1));
      }
    }
    const [correctnessLower, correctnessUpper] = simpleInterval(correctnessDeltas);
    const [metricLower, metricUpper] = simpleInterval(metricChanges);
    const isAuthoritative = rows.length > 0;
    return {
      treatment,
      correctness: summarize(correctnessValues, filters.statistic),
      metricValue: summarize(metricValues, filters.statistic),
      correctnessDelta: treatment === "baseline-none" ? 0 : summarize(correctnessDeltas, filters.statistic),
      metricChangePercent: treatment === "baseline-none" ? 0 : summarize(metricChanges, filters.statistic),
      taskSuccessRate: rows.length ? rows.filter(run => run.task_success).length / rows.length : null,
      coverageFraction: scheduled ? matched.length / scheduled : null,
      frontier: false,
      authoritative: isAuthoritative,
      exclusionReason: isAuthoritative ? null : visibleRows.map(run => run.exclusion_reason).filter(Boolean).join("; ") || "operationally ineligible",
      intervalStatus: correctnessLower == null || metricLower == null ? "not_estimable" : "estimable",
      correctnessLower,
      correctnessUpper,
      metricLower,
      metricUpper,
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
  return {
    points,
    individualRuns: displayed,
    frontier,
    objectiveWinners: {
      highest_correctness: winners("correctness", "higher"),
      lowest_selected_metric: winners("metricValue", "lower"),
    },
  };
}
