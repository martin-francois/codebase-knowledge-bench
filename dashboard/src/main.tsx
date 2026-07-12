import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import embed from "vega-embed";
import "./styles.css";

type Point = {
  treatment: string;
  correctness: number;
  modeled_weighted_token_load: number;
  non_cached_input_tokens?: number | null;
  output_tokens?: number | null;
  solve_wall_seconds: number;
  warm_workflow_seconds?: number | null;
  execution_calls_started?: number | null;
  intended_tool_successful_calls?: number | null;
  estimated_monetary_cost?: number | null;
  task_success_rate: number;
  operational_eligible: boolean;
  strict_attribution_supported: boolean | null;
  correctness_delta?: number | null;
  token_change_percent?: number | null;
  time_change_percent?: number | null;
  call_change_percent?: number | null;
  intervals?: Record<string, {lower: number; upper: number} | null>;
  median?: Record<string, number | null>;
};
type DashboardData = {
  schema_version: string;
  suite_id: string;
  analysis_mode: string;
  tolerance_grid: number[];
  default_tolerance: number;
  points: Point[];
  exact_pareto_frontier: string[];
  tolerance_aware_pareto_frontiers: Record<string, string[]>;
  individual_runs: Array<Record<string, unknown>>;
};

const source = document.getElementById("dashboard-data")?.textContent ?? "{}";
const data = JSON.parse(source) as DashboardData;
const metrics: Record<string, string> = {
  modeled_weighted_token_load: "Modeled weighted token load",
  non_cached_input_tokens: "Non-cached input tokens",
  output_tokens: "Output tokens",
  solve_wall_seconds: "Solve wall time (seconds)",
  warm_workflow_seconds: "Warm end-to-end time (seconds)",
  execution_calls_started: "Execution calls",
  intended_tool_successful_calls: "Successful intended-tool calls",
  estimated_monetary_cost: "Estimated monetary cost",
};

function Chart({spec, label}: {spec: object; label: string}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    embed(ref.current, spec, {actions: false, renderer: "svg"}).then(result => {
      result.view.container()?.setAttribute("aria-label", label);
    });
  }, [spec, label]);
  return <div ref={ref} className="chart" role="img" aria-label={label} />;
}

function App() {
  const [view, setView] = useState<"absolute" | "relative">("absolute");
  const [metric, setMetric] = useState("modeled_weighted_token_load");
  const [tolerance, setTolerance] = useState(data.default_tolerance);
  const [showRuns, setShowRuns] = useState(false);
  const [showUncertainty, setShowUncertainty] = useState(true);
  const [showPareto, setShowPareto] = useState(true);
  const [issue, setIssue] = useState("all");
  const [repetition, setRepetition] = useState("all");
  const [scope, setScope] = useState<"solve" | "warm">("solve");
  const [summary, setSummary] = useState<"mean" | "median">("mean");
  const [includeInvalid, setIncludeInvalid] = useState(false);
  const issues = [...new Set(data.individual_runs.map(run => String(run.issue_id)))].sort();
  const repetitions = [...new Set(data.individual_runs.map(run => String(run.repetition)))].sort();
  const frontier = new Set(
    showPareto
      ? data.tolerance_aware_pareto_frontiers[String(tolerance)] ?? data.exact_pareto_frontier
      : []
  );
  const visibleTreatments = new Set(
    data.individual_runs
      .filter(run => issue === "all" || String(run.issue_id) === issue)
      .filter(run => repetition === "all" || String(run.repetition) === repetition)
      .filter(run => includeInvalid || run.operational_eligible === true)
      .map(run => String(run.treatment))
  );
  const chartRows = data.points
    .filter(point => visibleTreatments.has(point.treatment))
    .map(point => {
    const selectedMetric = scope === "warm" && metric === "solve_wall_seconds"
      ? "warm_workflow_seconds" : metric;
    const summaryValue = summary === "median"
      ? point.median?.[
          selectedMetric === "modeled_weighted_token_load" ? "tokens"
          : selectedMetric === "solve_wall_seconds" ? "time"
          : selectedMetric === "warm_workflow_seconds" ? "warm_time"
          : selectedMetric === "execution_calls_started" ? "calls"
          : selectedMetric
        ]
      : point[selectedMetric as keyof Point];
    const correctnessInterval = point.intervals?.correctness_delta as
      | {estimable?: boolean; lower_95?: number; upper_95?: number}
      | undefined;
    return ({
    ...point,
    x: view === "absolute" ? summaryValue : (
      metric.includes("time") ? point.time_change_percent
      : metric.includes("call") ? point.call_change_percent
      : point.token_change_percent
    ),
    y: view === "absolute" ? point.correctness : point.correctness_delta,
    yLower: correctnessInterval?.estimable ? correctnessInterval.lower_95 : null,
    yUpper: correctnessInterval?.estimable ? correctnessInterval.upper_95 : null,
    frontier: frontier.has(point.treatment),
    baseline: point.treatment === "baseline-none",
  })});
  const spec = useMemo(() => ({
    $schema: "https://vega.github.io/schema/vega-lite/v6.json",
    width: "container",
    height: 430,
    data: {values: chartRows},
    layer: [
      ...(view === "relative" ? [
        {mark: {type: "rule", color: "#36454f"}, encoding: {x: {datum: 0}}},
        {mark: {type: "rule", color: "#36454f"}, encoding: {y: {datum: 0}}},
        {mark: {type: "rect", opacity: 0.1, color: "#d27d2d"}, encoding: {
          y: {datum: -tolerance}, y2: {datum: 0}
        }},
      ] : []),
      ...(showUncertainty && view === "relative" ? [{
        mark: {type: "rule", strokeWidth: 2},
        transform: [{filter: "datum.yLower != null && datum.yUpper != null"}],
        encoding: {
          x: {field: "x", type: "quantitative"},
          y: {field: "yLower", type: "quantitative"},
          y2: {field: "yUpper"},
        },
      }] : []),
      {
        mark: {type: "point", filled: true, size: 150, strokeWidth: 2},
        encoding: {
          x: {field: "x", type: "quantitative", title: view === "absolute" ? metrics[metric] : "Paired resource change (%) - lower is better"},
          y: {field: "y", type: "quantitative", title: view === "absolute" ? "Correctness score" : "Paired correctness delta (points)"},
          color: {field: "frontier", type: "nominal", scale: {domain: [true, false], range: ["#007f73", "#b75d36"]}, legend: {title: "Pareto frontier"}},
          shape: {field: "baseline", type: "nominal", scale: {domain: [true, false], range: ["diamond", "circle"]}, legend: {title: "Baseline"}},
          tooltip: [
            {field: "treatment", title: "Treatment"},
            {field: "correctness", title: "Correctness", format: ".2f"},
            {field: "task_success_rate", title: "Task success rate", format: ".1%"},
            {field: "modeled_weighted_token_load", title: "Weighted tokens", format: ",.1f"},
            {field: "solve_wall_seconds", title: "Solve seconds", format: ",.1f"},
            {field: "correctness_delta", title: "Correctness delta", format: "+.2f"},
            {field: "token_change_percent", title: "Token change", format: "+.2f"},
            {field: "time_change_percent", title: "Time change", format: "+.2f"},
            {field: "operational_eligible", title: "Operationally eligible"},
            {field: "strict_attribution_supported", title: "Strict attribution"},
          ],
        },
      },
      {mark: {type: "text", dy: -13, fontWeight: 650}, encoding: {
        x: {field: "x", type: "quantitative"}, y: {field: "y", type: "quantitative"},
        text: {field: "treatment"}
      }},
    ],
    config: {view: {stroke: null}, axis: {labelFontSize: 12, titleFontSize: 13}},
  }), [chartRows, metric, tolerance, view, showUncertainty]);

  return <main>
    <header>
      <p className="eyebrow">Matched operational evidence</p>
      <h1>Correctness has a cost curve.</h1>
      <p className="lede">Explore the trade-off without hiding your correctness tolerance behind one composite score. Lower resource values are better.</p>
      <p className="status">{data.analysis_mode === "pilot_only" ? "Pilot observations. Statistical support is not estimable." : "Repeated matched analysis available."}</p>
    </header>
    <section className="controls" aria-label="Dashboard controls">
      <fieldset><legend>View</legend>
        <button aria-pressed={view === "absolute"} onClick={() => setView("absolute")}>Absolute</button>
        <button aria-pressed={view === "relative"} onClick={() => setView("relative")}>Relative to baseline</button>
      </fieldset>
      <label>X-axis metric<select value={metric} onChange={e => setMetric(e.target.value)}>
        {Object.entries(metrics).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select></label>
      <label>Issue<select value={issue} onChange={e => setIssue(e.target.value)}>
        <option value="all">All issues</option>{issues.map(value => <option key={value}>{value}</option>)}
      </select></label>
      <label>Repetition<select value={repetition} onChange={e => setRepetition(e.target.value)}>
        <option value="all">All repetitions</option>{repetitions.map(value => <option key={value}>{value}</option>)}
      </select></label>
      <label>Cost scope<select value={scope} onChange={e => setScope(e.target.value as "solve" | "warm")}>
        <option value="solve">Solve-only</option><option value="warm">Warm end-to-end</option>
      </select></label>
      <label>Summary<select value={summary} onChange={e => setSummary(e.target.value as "mean" | "median")}>
        <option value="mean">Mean</option><option value="median">Median</option>
      </select></label>
      <label>Correctness-loss tolerance: <strong>{tolerance} points</strong>
        <input type="range" min={0} max={10} step={0.5} value={tolerance} onChange={e => setTolerance(Number(e.target.value))} />
      </label>
      <label><input type="checkbox" checked={showRuns} onChange={e => setShowRuns(e.target.checked)} /> Individual runs</label>
      <label><input type="checkbox" checked={showUncertainty} onChange={e => setShowUncertainty(e.target.checked)} /> Uncertainty</label>
      <label><input type="checkbox" checked={showPareto} onChange={e => setShowPareto(e.target.checked)} /> Pareto frontier</label>
      <label><input type="checkbox" checked={includeInvalid} onChange={e => setIncludeInvalid(e.target.checked)} /> Include non-adherent or trust-invalid</label>
    </section>
    <section aria-labelledby="chart-heading">
      <h2 id="chart-heading">{view === "absolute" ? "Absolute quality and efficiency" : "Matched change from baseline"}</h2>
      <Chart spec={spec} label={view === "absolute" ? "Scatter chart of correctness against selected resource metric" : "Scatter chart of paired correctness and resource changes relative to baseline"} />
      {showRuns && <p className="note">Individual-run detail contains {data.individual_runs.length} records and is available in the table data artifact.</p>}
      {showUncertainty && <p className="note">Intervals are shown only when estimable. This suite: {data.analysis_mode}.</p>}
    </section>
    <section aria-labelledby="table-heading"><h2 id="table-heading">Accessible data table</h2>
      <div className="table-wrap"><table><thead><tr><th>Treatment</th><th>Correctness</th><th>Task success</th><th>Weighted tokens</th><th>Solve seconds</th><th>Correctness delta</th><th>Token change</th><th>Time change</th><th>Pareto</th></tr></thead>
      <tbody>{chartRows.map(row => <tr key={row.treatment}><th>{row.treatment}</th><td>{row.correctness.toFixed(2)}</td><td>{(row.task_success_rate * 100).toFixed(0)}%</td><td>{row.modeled_weighted_token_load.toFixed(1)}</td><td>{row.solve_wall_seconds.toFixed(1)}</td><td>{row.correctness_delta?.toFixed(2) ?? "N/A"}</td><td>{row.token_change_percent?.toFixed(2) ?? "N/A"}%</td><td>{row.time_change_percent?.toFixed(2) ?? "N/A"}%</td><td>{row.frontier ? "Yes" : "No"}</td></tr>)}</tbody></table></div>
    </section>
  </main>;
}

createRoot(document.getElementById("root")!).render(<App />);
