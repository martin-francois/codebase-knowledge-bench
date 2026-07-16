import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import embed from "vega-embed";
import {
  DashboardData, Filters, METRICS, MetricKey, QUALITY_AXES, QualityAxis, TOKEN_VIEWS, TokenView,
  assertMetricDescriptorParity, deriveView, metricAvailability, qualityAvailability,
} from "./analysis";
import "./styles.css";

const source = document.getElementById("dashboard-data")?.textContent ?? "{}";
const data = JSON.parse(source) as DashboardData;
assertMetricDescriptorParity(data);

function Chart({spec, label}: {spec: object; label: string}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    embed(ref.current, spec, {actions: false, renderer: "svg"}).then(result => {
      result.view.container()?.setAttribute("aria-label", label);
    });
  }, [spec, label]);
  return <div ref={ref} data-testid="chart" className="chart" role="img" aria-label={label} />;
}

function App() {
  const [view, setView] = useState<"absolute" | "relative">("absolute");
  const [metric, setMetric] = useState<MetricKey>("modeled_weighted_token_load");
  const [qualityAxis, setQualityAxis] = useState<QualityAxis>("behavioral_correctness");
  const [tokenView, setTokenView] = useState<TokenView>("weighted_load");
  const [filters, setFilters] = useState<Filters>({
    issue: "all", repetition: "all", statistic: "mean",
    tolerance: data.default_tolerance, includeInvalid: false,
  });
  const [showRuns, setShowRuns] = useState(false);
  const [showUncertainty, setShowUncertainty] = useState(true);
  const [showPareto, setShowPareto] = useState(true);
  const availability = metricAvailability(data, view === "relative");
  const qualityAvailable = qualityAvailability(data);
  const derived = useMemo(() => deriveView(data, metric, filters, view, qualityAxis), [metric, filters, view, qualityAxis]);
  const issues = [...new Set(data.individual_runs.map(run => run.issue_id))].sort();
  const repetitions = [...new Set(data.individual_runs.map(run => String(run.repetition)))].sort();
  const descriptor = METRICS[metric];
  const qualityLabel = QUALITY_AXES[qualityAxis].label;
  const aggregateRows = derived.points.map(point => ({
    ...point,
    frontier: showPareto && point.frontier,
    x: view === "absolute" ? point.metricValue : point.metricChangePercent,
    y: view === "absolute" ? point.correctness : point.correctnessDelta,
    xLower: view === "relative" ? point.metricLower : null,
    xUpper: view === "relative" ? point.metricUpper : null,
    yLower: view === "relative" ? point.correctnessLower : null,
    yUpper: view === "relative" ? point.correctnessUpper : null,
    baseline: point.treatment === "baseline-none",
    pointKind: "aggregate",
  }));
  const individualRows = showRuns ? derived.individualRuns
    .filter(run => run.metrics[metric] != null && run.selectedQuality != null)
    .map(run => ({
      treatment: run.treatment,
      issue_id: run.issue_id,
      repetition: run.repetition,
      x: view === "absolute" ? run.metrics[metric] : run.metricChangePercent,
      y: view === "absolute" ? run.selectedQuality : run.correctnessDelta,
      authoritative: run.operational_eligible,
      matched: run.matched,
      pointKind: "individual",
    })) : [];
  const chartRows = [...aggregateRows, ...individualRows];
  const spec = useMemo(() => ({
    $schema: "https://vega.github.io/schema/vega-lite/v6.json",
    width: "container", height: 430,
    data: {values: chartRows},
    layer: [
      ...(view === "relative" ? [
        {mark: {type: "rule", color: "#36454f"}, encoding: {x: {datum: 0}}},
        {mark: {type: "rule", color: "#36454f"}, encoding: {y: {datum: 0}}},
        {mark: {type: "text", align: "left", baseline: "top", dx: 8, dy: 8, opacity: .55}, encoding: {x: {datum: 0}, y: {datum: 0}, text: {value: `Lower ${descriptor.label.toLowerCase()} is left`}}},
      ] : []),
      ...(showUncertainty ? [
        {transform: [{filter: "datum.pointKind === 'aggregate' && datum.intervalStatus === 'estimable'"}], mark: {type: "rule"}, encoding: {x: {field: "xLower", type: "quantitative"}, x2: {field: "xUpper"}, y: {field: "y", type: "quantitative"}}},
        {transform: [{filter: "datum.pointKind === 'aggregate' && datum.intervalStatus === 'estimable'"}], mark: {type: "rule"}, encoding: {x: {field: "x", type: "quantitative"}, y: {field: "yLower", type: "quantitative"}, y2: {field: "yUpper"}}},
      ] : []),
      {
        mark: {type: "point", filled: true, strokeWidth: 2},
        encoding: {
          x: {field: "x", type: "quantitative", title: view === "absolute" ? `${descriptor.label} (${descriptor.unit}) - lower is better` : `Paired ${descriptor.label.toLowerCase()} change (%) - lower is better`},
          y: {field: "y", type: "quantitative", title: view === "absolute" ? `${qualityLabel} score` : `Paired ${qualityLabel.toLowerCase()} delta (points)`},
          size: {field: "pointKind", type: "nominal", scale: {domain: ["aggregate", "individual"], range: [170, 38]}, legend: null},
          opacity: {field: "pointKind", type: "nominal", scale: {domain: ["aggregate", "individual"], range: [1, .24]}, legend: null},
          color: {field: "authoritative", type: "nominal", scale: {domain: [true, false], range: ["#007f73", "#777"]}, legend: {title: "Authoritative"}},
          stroke: {field: "frontier", type: "nominal", scale: {domain: [true, false], range: ["#111", "transparent"]}, legend: {title: "Tolerance frontier"}},
          shape: {field: "baseline", type: "nominal", scale: {domain: [true, false], range: ["diamond", "circle"]}, legend: {title: "Baseline"}},
          tooltip: [
            {field: "treatment", title: "Treatment"}, {field: "issue_id", title: "Issue"},
            {field: "repetition", title: "Repetition"}, {field: "y", title: "Correctness", format: ".2f"},
            {field: "x", title: descriptor.label, format: ",.2f"}, {field: "coverageFraction", title: "Coverage", format: ".1%"},
            {field: "taskSuccessRate", title: "Task success", format: ".1%"},
            {field: "intervalStatus", title: "Uncertainty"}, {field: "exclusionReason", title: "Exclusion"},
          ],
        },
      },
      {transform: [{filter: "datum.pointKind === 'aggregate'"}], mark: {type: "text", dy: -13, fontWeight: 650}, encoding: {x: {field: "x", type: "quantitative"}, y: {field: "y", type: "quantitative"}, text: {field: "treatment"}}},
    ],
    config: {view: {stroke: null}, axis: {labelFontSize: 12, titleFontSize: 13}},
  }), [chartRows, descriptor, qualityLabel, showUncertainty, view]);

  const update = <K extends keyof Filters>(key: K, value: Filters[K]) =>
    setFilters(current => ({...current, [key]: value}));
  return <main>
    <header><p className="eyebrow">Matched operational evidence</p><h1>Correctness has a cost curve.</h1>
      <p className="lede">Absolute task quality, relative correctness, resource trade-offs, and direct attribution remain separate.</p>
      <p className="status">{data.analysis_mode === "pilot_only" ? "Pilot observations. Statistical support is not estimable." : "Repeated matched analysis available."}</p>
    </header>
    <section className="controls" aria-label="Dashboard controls">
      <fieldset><legend>View</legend><button aria-pressed={view === "absolute"} onClick={() => setView("absolute")}>Absolute</button><button aria-pressed={view === "relative"} onClick={() => setView("relative")}>Relative to baseline</button></fieldset>
      <label>Quality axis<select aria-label="Quality axis" value={qualityAxis} onChange={event => setQualityAxis(event.target.value as QualityAxis)}>
        {(Object.keys(QUALITY_AXES) as QualityAxis[]).map(key => <option key={key} value={key} disabled={!qualityAvailable[key]}>{QUALITY_AXES[key].label}{!qualityAvailable[key] ? " (unavailable)" : ""}</option>)}
      </select></label>
      <label>Token view<select aria-label="Token view" value={tokenView} onChange={event => { const selected = event.target.value as TokenView; setTokenView(selected); const mapped = TOKEN_VIEWS[selected].metric; if (mapped) setMetric(mapped); }}>
        {(Object.keys(TOKEN_VIEWS) as TokenView[]).map(key => { const mapped = TOKEN_VIEWS[key].metric; const enabled = mapped != null && availability[mapped]; return <option key={key} value={key} disabled={!enabled}>{TOKEN_VIEWS[key].label}{!enabled ? " (unavailable)" : ""}</option>; })}
      </select></label>
      <label>X-axis metric<select aria-label="X-axis metric" value={metric} onChange={event => setMetric(event.target.value as MetricKey)}>
        {(Object.keys(METRICS) as MetricKey[]).map(key => <option key={key} value={key} disabled={!availability[key]}>{METRICS[key].label}{!availability[key] ? " (unavailable)" : ""}</option>)}
      </select></label>
      <label>Issue<select aria-label="Issue" value={filters.issue} onChange={event => update("issue", event.target.value)}><option value="all">All issues</option>{issues.map(value => <option key={value}>{value}</option>)}</select></label>
      <label>Repetition<select aria-label="Repetition" value={filters.repetition} onChange={event => update("repetition", event.target.value)}><option value="all">All repetitions</option>{repetitions.map(value => <option key={value}>{value}</option>)}</select></label>
      <label>Summary<select aria-label="Summary statistic" value={filters.statistic} onChange={event => update("statistic", event.target.value as "mean" | "median")}><option value="mean">Mean</option><option value="median">Median</option></select></label>
      <label>Correctness-loss tolerance<select aria-label="Correctness-loss tolerance" value={filters.tolerance} onChange={event => update("tolerance", Number(event.target.value))}>{data.tolerance_grid.map(value => <option key={value} value={value}>{value} points</option>)}</select></label>
      <label><input type="checkbox" checked={showRuns} onChange={event => setShowRuns(event.target.checked)} /> Individual runs</label>
      <label><input type="checkbox" checked={showUncertainty} onChange={event => setShowUncertainty(event.target.checked)} /> Uncertainty</label>
      <label><input type="checkbox" checked={showPareto} onChange={event => setShowPareto(event.target.checked)} /> Pareto frontier</label>
      <label><input type="checkbox" checked={filters.includeInvalid} onChange={event => update("includeInvalid", event.target.checked)} /> Include non-adherent or trust-invalid</label>
    </section>
    <section><h2>{view === "absolute" ? "Absolute quality and efficiency" : "Matched change from baseline"}</h2>
      {view === "relative" && <ul className="quadrants" aria-label="Relative chart quadrants"><li>Upper-left: better and lower resource use</li><li>Lower-left: lower resource use with a quality trade-off</li><li>Upper-right: better but higher resource use</li><li>Lower-right: worse and higher resource use</li></ul>}
      <Chart spec={spec} label={view === "absolute" ? "Absolute correctness and selected resource scatter chart" : "Baseline-relative correctness and selected resource scatter chart"} />
      <p className="note">Selected-chart 2D frontier: {showPareto ? derived.frontier.join(", ") || "not comparable" : "hidden"}. Global operational frontier: {data.canonical.exact_pareto_frontier.join(", ") || "not comparable"}. Uncertainty: {aggregateRows.every(row => row.intervalStatus === "not_estimable") ? "not estimable" : "canonical 95% paired intervals shown where estimable"}.</p>
    </section>
    <section><h2>Accessible filtered data table</h2><div className="table-wrap"><table data-testid="data-table"><thead><tr>
      <th>Treatment</th><th>{view === "absolute" ? qualityLabel : `${qualityLabel} delta`}</th><th>{view === "absolute" ? descriptor.label : `${descriptor.label} change`}</th><th>Task success</th><th>Full common suite pass/fail/skip</th><th>Coverage</th><th>Eligibility</th><th>Frontier at {filters.tolerance}</th><th>Uncertainty</th><th>Candidate-test diagnostics</th>
    </tr></thead><tbody>{aggregateRows.map(row => { const diagnosticRuns = data.individual_runs.filter(run => run.treatment === row.treatment); const changed = diagnosticRuns.reduce((total, run) => { const changes = run.candidate_test_changes; return total + (changes?.added?.length ?? 0) + (changes?.modified?.length ?? 0) + (changes?.deleted?.length ?? 0) + (changes?.renamed?.length ?? 0); }, 0); const commonPass = diagnosticRuns.reduce((total, run) => total + run.protected_common_pass_count, 0); const commonFail = diagnosticRuns.reduce((total, run) => total + run.protected_common_fail_count, 0); const commonSkip = diagnosticRuns.reduce((total, run) => total + run.protected_common_skip_count, 0); return <tr key={row.treatment} data-treatment={row.treatment}><th>{row.treatment}</th><td>{(view === "absolute" ? row.correctness : row.correctnessDelta)?.toFixed(2) ?? "N/A"}</td><td>{(view === "absolute" ? row.metricValue : row.metricChangePercent)?.toFixed(2) ?? "N/A"} {view === "relative" ? "%" : descriptor.unit}</td><td>{row.taskSuccessRate == null ? "N/A" : `${(row.taskSuccessRate * 100).toFixed(0)}%`}</td><td>{commonPass}/{commonFail}/{commonSkip}</td><td>{row.coverageFraction == null ? "N/A" : `${(row.coverageFraction * 100).toFixed(0)}%`}</td><td>{row.authoritative ? "Authoritative" : `Excluded: ${row.exclusionReason}`}</td><td>{showPareto && row.frontier ? "Yes" : "No"}</td><td>{row.intervalStatus === "estimable" ? "95% interval shown" : "Not estimable"}</td><td>{changed} candidate test change(s); protected effect none</td></tr>})}</tbody></table></div>
    </section>
    <section aria-labelledby="cache-panel-title"><h2 id="cache-panel-title">Prompt-cache observability</h2>
      <p>Cached and observed non-cached input are separate. Cache-write tokens and pricing-based cost are unavailable unless telemetry is explicit and pinned prices are complete.</p>
      <p className="note">A 30-minute cache lifetime is a minimum eligibility period, not an eviction guarantee. Cache isolation mode: natural unless an official, verified per-arm key capability is recorded.</p>
    </section>
    <section aria-labelledby="requirement-panel-title"><h2 id="requirement-panel-title">Requirement-based correctness</h2>
      <p>Current methodology <code>behavioral-correctness-current</code> exposes requirement weights, critical failures, protected common regressions, mutation readiness, reference diagnostics, candidate-test quality, and patch quality separately.</p>
      <p className="note">This panel does not retroactively rescore historical suites.</p>
    </section>
  </main>;
}

createRoot(document.getElementById("root")!).render(<App />);
