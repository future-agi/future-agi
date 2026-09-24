/**
 * Query engine for run-analytics widgets.
 *
 * Same model as an Observe dashboard widget — metrics × aggregation, filters,
 * breakdowns, chart settings — evaluated in the browser over the simulated
 * tasks of this environment's runs. The x-axis is runs, not time: a run is
 * the unit a simulation moves in, so every widget can show whether a metric
 * got better or worse from one agent version to the next.
 *
 * Query shape (mirrors the dashboards `query_config`):
 *   {
 *     range: "this" | "3" | "5" | "10" | "all",
 *     xAxis: "run" | "agent_version",
 *     metrics:    [{ id, name, type, unit, aggregation, filters: [Filter] }],
 *     filters:    [Filter],                  // Filter = { id, name, operator: "is"|"is_not", value: string[] }
 *     breakdowns: [{ id, name }],
 *   }
 */
import { csatOf, latencyOf, deriveUseCaseLabel } from "../TraceTable";

const fmtMs = (v) => (v >= 1000 ? `${(v / 1000).toFixed(2)}s` : `${Math.round(v)}ms`);
import { deriveToolCalls, isToolFailure } from "../toolCalls";
import { COST_STAGES, costSplitOf } from "../costSplit";
import { VOICE_SEGMENTS, voiceSegmentsOf } from "../voiceSegments";
import {
  OUTCOME_LABELS, attributionOf, endReasonOf, isMeasured, outcomeOf, passShareOf, samplesOf, sentimentOf,
} from "../taskOutcome";
import { groupKey } from "../layout/customWidgetRenderer";
import { ALL_AGGREGATIONS } from "src/sections/dashboards/constants";
import { runSummaries, trialSummaries, buildComparison } from "../../_mock/comparison";

/* Same cap and numbering as the runs table, so "Run 7" means the same run here. */
const TRIAL_CAP = 8;

/**
 * Every run of the environment with its tasks, oldest first, numbered the way
 * the runs table numbers them (manual runs and improvement trials share one
 * sequence). Trials carry no tasks of their own, so they are rebuilt from
 * their source run with the trial's per-scenario outcomes laid over it — the
 * same hydration Compare uses.
 */
export function simRunsWithTasks(env, envState) {
  if (!env) return [];
  const manual = runSummaries(env, envState).filter((r) => !r.synthetic);
  const trials = trialSummaries(env, envState)
    .slice()
    .sort((a, b) => new Date(a.finishedAt || 0) - new Date(b.finishedAt || 0))
    .slice(0, TRIAL_CAP);
  const hydrated = trials.length
    ? buildComparison(env, envState, trials.map((t) => t.id)).runs || []
    : [];
  const byId = new Map(hydrated.map((r) => [r.id, r]));
  /* A trial ran a candidate prompt on top of its source run's agent, so it
     is not that agent version — it gets its own version key. Its tasks are
     the source run's with the trial's verdicts laid over them, the same
     tasks the trial's own run page shows. */
  const trialRuns = trials.map((t) => {
    const full = byId.get(t.id) || t;
    return {
      ...full,
      kind: "trial",
      agentVersion: `${full.agentVersion || "trial"} · ${full.selfImprovementName ? `${full.selfImprovementName} ` : ""}T${full.trialN ?? ""}`.trim(),
      tasks: (full.tasks || []).map((task) => ({ ...task, __trialCopy: true })),
    };
  });
  return [...manual, ...trialRuns]
    .sort((a, b) => new Date(a.finishedAt || 0) - new Date(b.finishedAt || 0))
    .map((r, i) => ({ ...r, storedOrdinal: r.ordinal, ordinal: i + 1 }));
}

/* ── x-axis ─────────────────────────────────────────────────────────── */

export const RUN_RANGE_PRESETS = [
  { label: "This run", value: "this" },
  { label: "Last 3 runs", value: "3" },
  { label: "Last 5 runs", value: "5" },
  { label: "Last 10 runs", value: "10" },
  { label: "All runs", value: "all" },
];

/* The x-axis, like the dashboards' granularity: runs (or agent versions)
   across the range, each call of the run, the percentile of a value, or the
   values of an attribute ("By CSAT score", "By tool name"…). */
export const X_AXIS_OPTIONS = [
  { label: "Per run", value: "run" },
  { label: "Per agent version", value: "agent_version" },
  { label: "Per call", value: "call" },
  { label: "Percentile", value: "percentile" },
];
export const attributeAxisValue = (id) => `attr:${id}`;

/* ── metric catalogue ───────────────────────────────────────────────── */

/* Left column of the picker — same role as the dashboards' METRIC_CATEGORIES. */
export const SIM_METRIC_CATEGORIES = [
  { key: "all", label: "All", icon: "mdi:view-grid-outline" },
  { key: "outcome", label: "Outcome", icon: "mdi:flag-checkered" },
  { key: "evals", label: "Evals", icon: "mdi:check-circle-outline" },
  { key: "latency", label: "Latency", icon: "mdi:timer-outline" },
  { key: "cost", label: "Cost & usage", icon: "mdi:currency-usd" },
  { key: "conversation", label: "Conversation", icon: "mdi:message-text-outline" },
  { key: "tools", label: "Tools", icon: "mdi:tools" },
];

export const SIM_ATTRIBUTE_CATEGORIES = [
  { key: "all", label: "All", icon: "mdi:view-grid-outline" },
  { key: "scenario", label: "Scenario", icon: "mdi:script-text-outline" },
  { key: "outcome", label: "Outcome", icon: "mdi:flag-checkered" },
  { key: "conversation", label: "Conversation", icon: "mdi:message-text-outline" },
  { key: "run", label: "Run", icon: "mdi:play-circle-outline" },
  { key: "tools", label: "Tools", icon: "mdi:tools" },
];

const pct = (b) => (b ? 100 : 0);
/* Each tool row carries its task, so task attributes (outcome, scenario,
   persona…) filter and break down tool metrics like any other. */
const toolRowsOf = (task) => deriveToolCalls([task]).map((row) => ({ ...task, ...row }));
/* One row per call attempt: a scenario ran `repeats` times and passed on a
   share of them. */
const callRowsOf = (task) => {
  const n = samplesOf(task);
  const k = Math.round((passShareOf(task) ?? 0) * n);
  return Array.from({ length: n }, (_, i) => ({ ...task, __callPassed: i < k }));
};
/* One row per voice-pipeline segment of a call. */
const segmentRowsOf = (task) => {
  const seg = voiceSegmentsOf(task);
  return VOICE_SEGMENTS.map((st) => ({ ...task, __segment: st.label, __segmentMs: seg[st.key] }));
};
const rowsOfTask = (task, grain) => (grain === "tool" ? toolRowsOf(task)
  : grain === "call" ? callRowsOf(task)
    : grain === "segment" ? segmentRowsOf(task)
      : [task]);
/* Trials read the same task values the run page shows for them. */
const own = (t, v) => v;

/*
  Every metric reads one number per row. `grain` says what a row is: a task
  (default) or a tool call. Rates are stored as 0/100 per row so Average gives
  the rate, the same way the dashboards read a pass/fail column. `measured`
  metrics skip tasks with no verdict (the environment, connection, simulated
  caller or grader broke) — those are never the agent's failures.
*/
const BASE_METRICS = [
  { id: "tasks", name: "Scenarios", category: "outcome", unit: "", aggregation: "count", allowed: ["count"], value: () => 1 },
  { id: "calls", name: "Calls", category: "outcome", grain: "call", unit: "", aggregation: "count", allowed: ["count"], value: () => 1 },
  { id: "pass_rate", name: "Pass rate", category: "outcome", unit: "%", aggregation: "avg", measured: true, value: (t) => (passShareOf(t) == null ? null : passShareOf(t) * 100) },
  { id: "fail_rate", name: "Fail rate", category: "outcome", unit: "%", aggregation: "avg", measured: true, value: (t) => (passShareOf(t) == null ? null : (1 - passShareOf(t)) * 100) },
  { id: "critical_failures", name: "Critical failures", category: "outcome", unit: "", aggregation: "sum", allowed: ["sum", "avg"], measured: true, value: (t) => (t.critical && outcomeOf(t) === "failed" ? 1 : 0) },
  { id: "not_measured", name: "Not measured", category: "outcome", unit: "", aggregation: "sum", allowed: ["sum", "avg"], value: (t) => (isMeasured(t) ? 0 : 1) },

  { id: "duration", name: "Call duration", category: "latency", unit: "ms", aggregation: "avg", measured: true, value: (t) => own(t, t.durationMs ?? null) },
  { id: "duration_s", name: "Call duration (s)", category: "latency", unit: "s", aggregation: "avg", measured: true, value: (t) => (t.durationMs == null ? null : t.durationMs / 1000) },
  { id: "segment_latency", name: "Voice segment latency", category: "latency", grain: "segment", unit: "ms", aggregation: "p50", measured: true, value: (r) => r.__segmentMs ?? null },
  { id: "agent_latency", name: "Agent latency", category: "latency", unit: "ms", aggregation: "avg", measured: true, value: (t) => own(t, latencyOf(t)) },

  { id: "cost", name: "Cost", category: "cost", unit: "$", aggregation: "sum", value: (t) => own(t, t.cost ?? null) },
  { id: "tokens", name: "Tokens", category: "cost", unit: "tokens", aggregation: "sum", value: (t) => own(t, t.tokens ?? null) },
  /* Cost per pipeline stage — the same split as "Cost breakdown by pipeline stage". */
  ...COST_STAGES.map((st) => ({
    id: `cost_${st.key}`, name: `${st.label} cost`, category: "cost", unit: "$", aggregation: "sum",
    value: (t) => (t.cost ? costSplitOf(t)[st.key] : null),
  })),

  { id: "turns", name: "Turn count", category: "conversation", unit: "", aggregation: "avg", measured: true, value: (t) => own(t, t.steps?.length ?? null) },
  { id: "csat", name: "CSAT", category: "conversation", unit: "", aggregation: "avg", measured: true, value: (t) => csatOf(t) },

  { id: "tool_calls", name: "Tool calls", category: "tools", grain: "tool", unit: "", aggregation: "count", allowed: ["count"], value: (r) => own(r, r.toolStatus === "not_called" ? null : 1) },
  /* Out of every time the tool was needed or used — the same denominator as
     "Required tool not called", so the two stack to the tool's total. */
  { id: "tool_failure_rate", name: "Tool failure rate", category: "tools", grain: "tool", unit: "%", aggregation: "avg", value: (r) => own(r, pct(isToolFailure(r))) },
  { id: "tool_not_called_rate", name: "Required tool not called", category: "tools", grain: "tool", unit: "%", aggregation: "avg", value: (r) => own(r, pct(r.toolStatus === "not_called")) },
  { id: "tool_latency", name: "Tool latency", category: "tools", grain: "tool", unit: "ms", aggregation: "avg", value: (r) => own(r, r.durationMs ?? null) },
];

/* Pass/fail aggregations the dashboards offer on eval columns. */
const EVAL_AGGREGATIONS = ["avg", "median", "min", "max", "pass_rate", "fail_rate", "pass_count", "fail_count", "p50", "p90"];

/** Metric catalogue for this environment: the base set plus one per eval the runs were graded with. */
export function simMetricCatalog(runs = []) {
  const evals = new Map();
  runs.forEach((run) => (run.tasks || []).forEach((t) => (t.evalResults || []).forEach((r) => {
    if (!evals.has(r.id)) evals.set(r.id, r.name || r.id);
  })));
  const evalMetrics = [...evals.entries()].map(([id, name]) => ({
    id: `eval:${id}`,
    name,
    category: "evals",
    type: "eval_metric",
    outputType: "SCORE",
    unit: "%",
    /* Pass rate, like the runs table's eval columns. */
    aggregation: "pass_rate",
    measured: true,
    allowed: EVAL_AGGREGATIONS,
    value: (t) => {
      const r = (t.evalResults || []).find((x) => x.id === id);
      return r ? { score: Number(r.score ?? 0) * 100, passed: !!r.passed } : null;
    },
  }));
  return [...BASE_METRICS.map((m) => ({ type: "system", ...m })), ...evalMetrics];
}

/* ── attributes (filter + breakdown) ─────────────────────────────────── */

export const SIM_ATTRIBUTES = [
  { id: "persona", name: "Persona", category: "scenario", get: (r) => groupKey(r, "persona") },
  /* Same labels as the Use case risk chart and the traces table. */
  { id: "use_case", name: "Use case", category: "scenario", get: (r) => deriveUseCaseLabel(r) || groupKey(r, "use_case") },
  { id: "scenario", name: "Scenario", category: "scenario", get: (r) => r.title || r.taskTitle || "—" },
  { id: "critical", name: "Critical scenario", category: "scenario", get: (r) => (r.critical ? "Critical" : "Not critical") },
  { id: "status", name: "Outcome", category: "outcome", get: (r) => OUTCOME_LABELS[outcomeOf(r)] },
  { id: "outcome_binary", name: "Successful vs unsuccessful", category: "outcome", get: (r) => {
    const o = outcomeOf(r);
    return o === "unmeasured" ? "Not measured" : o === "passed" ? "Successful" : "Unsuccessful";
  } },
  { id: "attribution", name: "Failure attribution", category: "outcome", get: (r) => attributionOf(r)?.label || "Passed" },
  { id: "sentiment", name: "Caller sentiment", category: "conversation", get: (r) => SENTIMENT_LABELS[sentimentOf(r)] || "—" },
  { id: "disconnection", name: "Disconnection reason", category: "conversation", get: (r) => END_REASON_LABELS[endReasonOf(r)] || endReasonOf(r) },
  { id: "call_outcome", name: "Call outcome", category: "outcome", grain: "call", get: (r) => (r.__callPassed ? "Successful" : "Unsuccessful"), categories: () => ["Successful", "Unsuccessful"] },
  { id: "call_verdict", name: "Call verdict", category: "outcome", grain: "call", get: (r) => (r.__callPassed ? "Passed" : "Failed"), categories: () => ["Passed", "Failed"] },
  { id: "segment", name: "Voice segment", category: "conversation", grain: "segment", get: (r) => r.__segment || "—", categories: () => VOICE_SEGMENTS.map((st) => st.label) },
  {
    id: "response_bucket", name: "Agent response time (25ms buckets)", category: "conversation", numeric: true,
    get: (r) => fmtMs(Math.floor(latencyOf(r) / 25) * 25),
    /* Every bucket from just below the fastest call to just above the
       slowest, empty ones included, like the built-in histogram. */
    categories: (rows) => {
      const v = rows.map((r) => latencyOf(r)).filter((x) => x > 0).sort((a, b) => a - b);
      if (!v.length) return [];
      const lo = Math.max(0, Math.floor(v[0] / 25) * 25 - 25);
      const hi = Math.ceil((v[v.length - 1] + 1) / 25) * 25 + 25;
      const out = [];
      for (let at = lo; at < hi; at += 25) out.push(fmtMs(at));
      return out;
    },
  },
  { id: "turn_bucket", name: "Turn count bucket", category: "conversation", get: (r) => groupKey(r, "turn_bucket") },
  { id: "csat_score", name: "CSAT score", category: "conversation", numeric: true, get: (r) => (isMeasured(r) ? String(csatOf(r)) : "—"), categories: () => Array.from({ length: 11 }, (_, i) => String(i)) },
  { id: "agent_version", name: "Agent version", category: "run", get: (r) => (r.__agentVersion ? `agent ${r.__agentVersion}` : "—") },
  { id: "run_kind", name: "Run type", category: "run", get: (r) => (r.__trialCopy ? "Improvement trial" : "Run") },
  { id: "tool_name", name: "Tool name", category: "tools", grain: "tool", get: (r) => r.toolName || "—" },
  { id: "tool_status", name: "Tool status", category: "tools", grain: "tool", get: (r) => r.toolStatus || "—" },
];

export const attributeById = (id) => SIM_ATTRIBUTES.find((a) => a.id === id);

const SENTIMENT_LABELS = { positive: "Positive", neutral: "Neutral", negative: "Negative" };
const END_REASON_LABELS = {
  complete: "Task complete", escalated: "Escalated", incomplete: "Incomplete",
  timeout: "Timeout", dropped: "Dropped", error: "Error",
};

export const SIM_FILTER_OPERATORS = [
  { label: "Is", value: "is", multi: true },
  { label: "Is not", value: "is_not", multi: true },
];

/** Distinct values of an attribute across the given runs — feeds the filter value picker. */
export function distinctAttributeValues(attrId, runs) {
  const attr = attributeById(attrId);
  if (!attr) return [];
  const set = new Set();
  runs.forEach((run) => (run.tasks || []).forEach((t) => {
    const rows = rowsOfTask(t, attr.grain || "task");
    rows.forEach((row) => set.add(String(attr.get({ ...row, __agentVersion: run.agentVersion }))));
  }));
  return [...set].sort();
}

/* ── evaluation ─────────────────────────────────────────────────────── */

const passesFilters = (row, filters) => (filters || []).every((f) => {
  const attr = attributeById(f.id);
  const vals = Array.isArray(f.value) ? f.value.map(String) : [];
  if (!attr || !vals.length) return true;
  const hit = vals.includes(String(attr.get(row)));
  return f.operator === "is_not" ? !hit : hit;
});

/* Nearest rank — the rule every analytics panel uses, so a p90 read in a
   widget is the same number as the p90 on the tab. */
const percentile = (sorted, p) => {
  if (!sorted.length) return null;
  return sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))];
};

/** Reduce raw per-row values with a dashboards aggregation id. */
export function aggregate(values, aggregation) {
  const raw = values.filter((v) => v != null);
  if (!raw.length) return aggregation === "count" ? 0 : null;
  const isEval = typeof raw[0] === "object";
  if (isEval) {
    const passed = raw.filter((v) => v.passed).length;
    if (aggregation === "pass_rate") return (passed / raw.length) * 100;
    if (aggregation === "fail_rate") return ((raw.length - passed) / raw.length) * 100;
    if (aggregation === "pass_count") return passed;
    if (aggregation === "fail_count") return raw.length - passed;
  }
  const nums = (isEval ? raw.map((v) => v.score) : raw).map(Number).filter(Number.isFinite);
  if (!nums.length) return null;
  const sorted = [...nums].sort((a, b) => a - b);
  /* Same rule as the analytics panels: a tail percentile on too few values
     is just the maximum, so it is withheld (p90 from 10, p95 from 20, p99
     from 100 values). */
  const MIN_N = { p90: 10, p95: 20, p99: 100 };
  if (MIN_N[aggregation] && sorted.length < MIN_N[aggregation]) return null;
  switch (aggregation) {
    case "count": return nums.length;
    case "count_distinct": return new Set(nums).size;
    case "sum": return nums.reduce((a, b) => a + b, 0);
    case "min": return sorted[0];
    case "max": return sorted[sorted.length - 1];
    case "median": return percentile(sorted, 0.5);
    case "p25": return percentile(sorted, 0.25);
    case "p50": return percentile(sorted, 0.5);
    case "p75": return percentile(sorted, 0.75);
    case "p90": return percentile(sorted, 0.9);
    case "p95": return percentile(sorted, 0.95);
    case "p99": return percentile(sorted, 0.99);
    case "avg":
    default: return nums.reduce((a, b) => a + b, 0) / nums.length;
  }
}

/** Units that no longer apply once an aggregation turns values into counts. */
export const unitFor = (metric) => {
  if (["count", "count_distinct", "pass_count", "fail_count"].includes(metric.aggregation)) return "";
  if (["pass_rate", "fail_rate"].includes(metric.aggregation)) return "%";
  return metric.unit || "";
};

export const aggLabel = (value) => ALL_AGGREGATIONS.find((a) => a.value === value)?.label
  || { pass_rate: "Pass Rate", fail_rate: "Fail Rate", pass_count: "Pass Count", fail_count: "Fail Count" }[value]
  || value;

/** Runs in scope for a range preset, oldest first, ending at the run being viewed. */
export function runsInRange(runs, range, currentRunId) {
  const ordered = [...(runs || [])];
  /* End at the run being viewed. If it isn't in the history (a live run, or
     a trial past the cap), read up to the latest run — never silently Run 1. */
  const endIdx = ordered.findIndex((r) => r.id === currentRunId);
  const upto = endIdx >= 0 ? ordered.slice(0, endIdx + 1) : ordered;
  if (range === "this") return upto.slice(-1);
  if (range === "all") return upto;
  const n = Number(range) || 5;
  return upto.slice(-n);
}

/* Natural order for category values: numbers (and "450ms" / "1.20s") by
   value, everything else alphabetically. */
const toNumber = (v) => {
  const m = String(v).match(/^(-?\d+(?:\.\d+)?)(ms|s)?$/);
  if (!m) return null;
  return m[2] === "s" ? Number(m[1]) * 1000 : Number(m[1]);
};
const naturalCompare = (a, b) => {
  const na = toNumber(a);
  const nb = toNumber(b);
  if (na != null && nb != null) return na - nb;
  return String(a).localeCompare(String(b));
};

/* p-th value of a sorted list by nearest rank — the same rule the analytics
   panels use, so a percentile read here matches the one on the tab. */
const nearestRank = (sorted, p) => (sorted.length
  ? sorted[Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length))]
  : null);

/* Wilson upper bound of a pass rate — how good a group could plausibly be. */
const wilsonUpper = (good, total) => {
  if (!total) return 1;
  const z = 1.96;
  const p = good / total;
  const denom = 1 + (z * z) / total;
  const centre = p + (z * z) / (2 * total);
  const margin = z * Math.sqrt((p * (1 - p) + (z * z) / (4 * total)) / total);
  return (centre + margin) / denom;
};

/**
 * Evaluate a query. Returns buckets for the x-axis and one series per
 * metric × breakdown value, in the shape the dashboards' series table and
 * helpers expect ({ name, key, metricIndex, metricName, aggregation, unit,
 * breakdownName, total, data: [{ x, y }] }).
 *
 * query.sort   — "value_desc" (largest first) or { by: "weakest", good, bad }
 * query.limit  — keep the first N x-axis values after sorting
 * query.rankLabels — label the x-axis #1, #2… (ranked charts)
 */
export function runSimQuery(query, runs, currentRunId, catalog) {
  const cat = catalog || simMetricCatalog(runs);
  const scoped = runsInRange(runs, query?.range || "5", currentRunId);
  const xAxis = query?.xAxis || "run";
  const breakdown = (query?.breakdowns || [])[0] ? attributeById(query.breakdowns[0].id) : null;
  const attrAxis = xAxis.startsWith("attr:") ? attributeById(xAxis.slice(5)) : null;

  const scopedTasks = scoped.flatMap((run) => (run.tasks || []).map((task) => ({ task, run })));

  /* Rows per metric, carrying which run and call they came from. */
  const metricRows = (query?.metrics || []).map((m) => {
    const def = cat.find((c) => c.id === m.id);
    if (!def) return null;
    const rows = scopedTasks.flatMap(({ task, run }) => {
      if (def.measured && !isMeasured(task)) return [];
      return rowsOfTask(task, def.grain || "task").map((row) => ({
        ...row, __agentVersion: run.agentVersion, __runId: run.id, __callKey: `${run.id}::${task.id}`,
      }));
    }).filter((row) => passesFilters(row, query.filters) && passesFilters(row, m.filters));
    return { m, def, rows };
  });

  /* x-axis buckets, and which bucket a row falls in. */
  let buckets;
  let bucketOf;
  if (xAxis === "agent_version") {
    const byVersion = new Map();
    scoped.forEach((run) => {
      const key = run.agentVersion || "—";
      if (!byVersion.has(key)) byVersion.set(key, { key, label: `agent ${key}`, runs: 0 });
      byVersion.get(key).runs += 1;
    });
    buckets = [...byVersion.values()].map((b) => ({ key: b.key, label: b.label, sub: `${b.runs} run${b.runs === 1 ? "" : "s"}` }));
    bucketOf = (row) => row.__agentVersion || "—";
  } else if (xAxis === "call") {
    /* Each call of the runs in range that passes the widget's filters, in
       the order it ran. */
    const calls = scopedTasks.filter(({ task }) => passesFilters(task, query.filters));
    buckets = calls.map(({ task, run }, i) => ({
      key: `${run.id}::${task.id}`, label: `T${i + 1}`, sub: task.title || task.task || "",
    }));
    bucketOf = (row) => row.__callKey;
  } else if (xAxis === "percentile") {
    buckets = Array.from({ length: 101 }, (_, p) => ({ key: String(p), label: `p${p}`, sub: "" }));
    bucketOf = null;
  } else if (attrAxis) {
    const all = metricRows.filter(Boolean).flatMap((mr) => mr.rows);
    const seen = new Set(all.map((row) => String(attrAxis.get(row))));
    const fixed = attrAxis.categories ? attrAxis.categories(all) : null;
    /* A ranked axis keeps first-appearance order for ties, like the built-in
       ranked charts; otherwise values sort naturally. */
    const values = fixed || (query?.sort ? [...seen] : [...seen].sort(naturalCompare));
    buckets = values.map((v) => ({ key: v, label: v, sub: "" }));
    bucketOf = (row) => String(attrAxis.get(row));
  } else {
    buckets = scoped.map((run) => ({
      key: run.id, label: `Run ${run.ordinal ?? ""}`.trim(), sub: `agent ${run.agentVersion || "—"}`,
    }));
    bucketOf = (row) => row.__runId;
  }

  const series = [];
  metricRows.forEach((mr, metricIndex) => {
    if (!mr) return;
    const { m, def, rows } = mr;
    const aggregation = m.aggregation || def.aggregation;
    const unit = unitFor({ ...def, aggregation });
    const aggShort = xAxis === "percentile" ? "percentile" : (AGG_SHORT[aggregation] || aggregation);
    const reduce = (list) => {
      if (!list.length) return aggregation === "count" ? 0 : null;
      return round(aggregate(list.map(def.value), aggregation));
    };
    const pointsFor = (subset) => {
      if (xAxis === "percentile") {
        const vals = subset.map(def.value).map((v) => (v && typeof v === "object" ? v.score : v))
          .filter((v) => v != null && Number.isFinite(Number(v)) && Number(v) > 0).map(Number).sort((a, b) => a - b);
        return buckets.map((b) => ({ x: b.label, y: vals.length ? round(nearestRank(vals, Number(b.key))) : null }));
      }
      const byBucket = new Map();
      subset.forEach((row) => {
        const k = bucketOf(row);
        if (!byBucket.has(k)) byBucket.set(k, []);
        byBucket.get(k).push(row);
      });
      return buckets.map((b) => ({ x: b.label, y: reduce(byBucket.get(b.key) || []) }));
    };
    /* The single number for the whole range (Agg. column, pie slice, metric
       card) is aggregated once over every row in scope — not recombined from
       per-bucket values, which would make "p90" the mean of several p90s. */
    const totalOf = (subset) => (xAxis === "percentile" ? null : (subset.length ? round(aggregate(subset.map(def.value), aggregation)) : null));

    if (!breakdown) {
      series.push({
        name: `${m.name || def.name} (${aggShort})`,
        key: `${m.id}|${aggregation}|`,
        metricIndex, metricName: m.name || def.name, aggregation, unit, breakdownName: null,
        total: totalOf(rows),
        data: pointsFor(rows),
      });
      return;
    }
    const present = new Set(rows.map((r) => String(breakdown.get(r))));
    const values = breakdown.categories
      ? breakdown.categories(rows).filter((v) => present.has(v))
      : [...present].sort(naturalCompare);
    values.forEach((val) => {
      const subset = rows.filter((r) => String(breakdown.get(r)) === val);
      series.push({
        name: `${m.name || def.name} / ${val} (${aggShort})`,
        key: `${m.id}|${aggregation}|${val}`,
        metricIndex, metricName: m.name || def.name, aggregation, unit, breakdownName: val,
        total: totalOf(subset),
        data: pointsFor(subset),
      });
    });
  });

  /* Optional ordering / cut of the x-axis (ranked charts). */
  let order = buckets.map((_, i) => i);
  const sort = query?.sort;
  if (sort === "value_desc") {
    const score = (i) => series.reduce((a, s) => a + (s.data[i]?.y || 0), 0);
    order.sort((a, b) => score(b) - score(a));
  } else if (sort && sort.by === "weakest") {
    const val = (i, name) => series.filter((s) => s.breakdownName === name).reduce((a, s) => a + (s.data[i]?.y || 0), 0);
    const risk = (i) => wilsonUpper(val(i, sort.good), val(i, sort.good) + val(i, sort.bad));
    const size = (i) => val(i, sort.good) + val(i, sort.bad);
    order.sort((a, b) => risk(a) - risk(b) || size(b) - size(a));
  }
  if (query?.limit) order = order.slice(0, query.limit);
  if (query?.reverse) order = [...order].reverse();
  if (sort || query?.limit || query?.reverse) {
    buckets = order.map((i, rank) => ({ ...buckets[i], label: query?.rankLabels ? `#${rank + 1}` : buckets[i].label, name: buckets[i].label }));
    series.forEach((s) => { s.data = order.map((i, rank) => ({ ...s.data[i], x: buckets[rank].label })); });
  }

  return { buckets, series, runsInScope: scoped };
}

/* Keep enough precision for small values (a $0.098 call) — display
   formatting decides how many decimals to show. */
const round = (v) => (v == null ? null : Math.round(v * 10000) / 10000);

/* Short aggregation names for series labels, dashboards-style "(p90)". */
const AGG_SHORT = { pass_rate: "pass rate", fail_rate: "fail rate", pass_count: "passes", fail_count: "fails", count_distinct: "distinct" };

/** The series' single value over the whole range. */
export const seriesTotal = (s) => (s?.total !== undefined ? s.total : null);

/** Keys of the top N series by their scalar over the range — the default
 *  selection when a breakdown produces more series than a chart can read. */
export const MAX_DEFAULT_SERIES = 10;
export function topSeriesKeys(series, n = MAX_DEFAULT_SERIES) {
  const scalar = (s) => (s.total == null ? -Infinity : s.total);
  return [...series].sort((a, b) => scalar(b) - scalar(a)).slice(0, n).map((s) => s.key);
}

/* ── defaults ───────────────────────────────────────────────────────── */

export const newSimQuery = () => ({
  range: "5",
  xAxis: "run",
  metrics: [],
  filters: [],
  breakdowns: [],
});

export const metricFromCatalog = (def) => ({
  id: def.id,
  name: def.name,
  type: def.type || "system",
  unit: def.unit || "",
  aggregation: def.aggregation,
  allowedAggregations: def.allowed || null,
  filters: [],
});
