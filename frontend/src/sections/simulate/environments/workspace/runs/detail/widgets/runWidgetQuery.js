import { ALL_AGGREGATIONS } from "src/sections/dashboards/constants";
import { runOutcome } from "../trace/traceTable.constants";

/**
 * Query engine for run analytics widgets: the Observe dashboard widget model
 * (metrics x aggregation, filters, breakdowns) evaluated in the browser over
 * the calls of one run. Every row is a `RunTask` from `mapCallRow`, so a
 * widget reads exactly what the run's trace table shows.
 *
 * Query shape (mirrors the dashboards `query_config`):
 *   {
 *     range: "this",
 *     xAxis: "run" | "call" | "percentile" | "attr:<attribute id>",
 *     metrics:    [{ id, name, type, unit, aggregation, filters: [Filter] }],
 *     filters:    [Filter],  // { id, name, operator: "is" | "is_not", value: string[] }
 *     breakdowns: [{ id, name }],
 *   }
 */

// Only this run's calls are read; a range over earlier runs needs their
// calls joined per environment, which the API doesn't serve yet.
export const RUN_RANGE_PRESETS = [
  { label: "This run", value: "this" },
  { label: "Last 3 runs", value: "3", soon: true },
  { label: "Last 5 runs", value: "5", soon: true },
  { label: "Last 10 runs", value: "10", soon: true },
  { label: "All runs", value: "all", soon: true },
];

export const X_AXIS_OPTIONS = [
  { label: "Per run", value: "run" },
  { label: "Per call", value: "call" },
  { label: "Percentile", value: "percentile" },
];
export const attributeAxisValue = (id) => `attr:${id}`;

export const RUN_METRIC_CATEGORIES = [
  { key: "all", label: "All", icon: "solar:widget-4-linear" },
  { key: "outcome", label: "Outcome", icon: "solar:flag-linear" },
  { key: "evals", label: "Evals", icon: "solar:check-circle-linear" },
  { key: "latency", label: "Latency", icon: "solar:clock-circle-linear" },
  { key: "usage", label: "Usage", icon: "solar:database-linear" },
  {
    key: "conversation",
    label: "Conversation",
    icon: "solar:chat-round-line-linear",
  },
];

export const RUN_ATTRIBUTE_CATEGORIES = [
  { key: "all", label: "All", icon: "solar:widget-4-linear" },
  { key: "scenario", label: "Scenario", icon: "solar:document-text-linear" },
  { key: "outcome", label: "Outcome", icon: "solar:flag-linear" },
  {
    key: "conversation",
    label: "Conversation",
    icon: "solar:chat-round-line-linear",
  },
];

// A call with a verdict; the rest (queued, running, errored, inconclusive)
// never count as the agent's failures.
const isMeasured = (task) =>
  task?.status === "passed" || task?.status === "failed";

const fmtMs = (value) =>
  value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${Math.round(value)}ms`;

// Every metric reads one number per call. Rates are stored as 0/100 per call
// so Average gives the rate, the way the dashboards read a pass/fail column.
const BASE_METRICS = [
  {
    id: "calls",
    name: "Calls",
    category: "outcome",
    unit: "",
    aggregation: "count",
    allowed: ["count"],
    value: () => 1,
  },
  {
    id: "pass_rate",
    name: "Pass rate",
    category: "outcome",
    unit: "%",
    aggregation: "avg",
    measured: true,
    value: (task) => (task.status === "passed" ? 100 : 0),
  },
  {
    id: "fail_rate",
    name: "Fail rate",
    category: "outcome",
    unit: "%",
    aggregation: "avg",
    measured: true,
    value: (task) => (task.status === "failed" ? 100 : 0),
  },
  {
    id: "not_measured",
    name: "Not measured",
    category: "outcome",
    unit: "",
    aggregation: "sum",
    allowed: ["sum", "avg"],
    value: (task) => (isMeasured(task) ? 0 : 1),
  },
  {
    id: "errors",
    name: "Errors",
    category: "outcome",
    unit: "",
    aggregation: "sum",
    allowed: ["sum", "avg"],
    value: (task) => (task.status === "error" ? 1 : 0),
  },
  {
    id: "duration",
    name: "Call duration",
    category: "latency",
    unit: "ms",
    aggregation: "avg",
    value: (task) => task.durationMs ?? null,
  },
  {
    id: "duration_s",
    name: "Call duration (s)",
    category: "latency",
    unit: "s",
    aggregation: "avg",
    value: (task) => (task.durationMs == null ? null : task.durationMs / 1000),
  },
  {
    id: "agent_latency",
    name: "Agent latency",
    category: "latency",
    unit: "ms",
    aggregation: "avg",
    value: (task) => task.latencyMs ?? null,
  },
  {
    id: "stop_latency",
    name: "Stop time after interruption",
    category: "latency",
    unit: "ms",
    aggregation: "avg",
    value: (task) => task.stopLatencyMs ?? null,
  },
  {
    id: "tokens",
    name: "Tokens",
    category: "usage",
    unit: "tokens",
    aggregation: "sum",
    value: (task) => task.tokens ?? null,
  },
  {
    id: "turns",
    name: "Turn count",
    category: "conversation",
    unit: "",
    aggregation: "avg",
    value: (task) => task.turns ?? null,
  },
  {
    id: "ai_interruptions",
    name: "AI interruptions",
    category: "conversation",
    unit: "",
    aggregation: "avg",
    value: (task) => task.aiInterruptions ?? null,
  },
  {
    id: "csat",
    name: "CSAT",
    category: "conversation",
    unit: "",
    aggregation: "avg",
    value: (task) => task.csat ?? null,
  },
];

// Pass/fail aggregations the dashboards offer on eval columns.
const EVAL_AGGREGATIONS = [
  "avg",
  "median",
  "min",
  "max",
  "pass_rate",
  "fail_rate",
  "pass_count",
  "fail_count",
  "p50",
  "p90",
];

/** The metric catalogue: the base set plus one per eval the calls carry. */
export function runMetricCatalog(tasks = []) {
  const evals = new Map();
  tasks.forEach((task) =>
    (task.evalResults || []).forEach((result) => {
      if (!evals.has(result.id)) evals.set(result.id, result.name || result.id);
    }),
  );
  const evalMetrics = [...evals.entries()].map(([id, name]) => ({
    id: `eval:${id}`,
    name,
    category: "evals",
    type: "eval_metric",
    outputType: "SCORE",
    unit: "%",
    // Pass rate, like the trace table's eval columns.
    aggregation: "pass_rate",
    measured: true,
    allowed: EVAL_AGGREGATIONS,
    value: (task) => {
      const result = (task.evalResults || []).find((item) => item.id === id);
      if (!result || (result.score == null && result.passed == null))
        return null;
      const score = result.score == null ? null : Number(result.score) * 100;
      return {
        score,
        passed: result.passed ?? (score != null ? score >= 50 : false),
      };
    },
  }));
  return [
    ...BASE_METRICS.map((item) => ({ type: "system", ...item })),
    ...evalMetrics,
  ];
}

const TURN_BUCKETS = ["1-5", "6-10", "11-20", "21+"];
const turnBucket = (turns) => {
  if (turns == null || turns < 1) return "-";
  if (turns <= 5) return TURN_BUCKETS[0];
  if (turns <= 10) return TURN_BUCKETS[1];
  if (turns <= 20) return TURN_BUCKETS[2];
  return TURN_BUCKETS[3];
};

// Attributes a filter, a breakdown or the x-axis can use.
export const RUN_ATTRIBUTES = [
  {
    id: "persona",
    name: "Persona",
    category: "scenario",
    get: (task) => task.persona || "-",
  },
  {
    id: "scenario",
    name: "Scenario",
    category: "scenario",
    get: (task) => task.sourceScenario || "-",
  },
  {
    id: "goal",
    name: "Goal",
    category: "scenario",
    get: (task) => task.goal || "-",
  },
  {
    id: "trial",
    name: "Trial",
    category: "scenario",
    get: (task) => (task.trialIndex == null ? "-" : String(task.trialIndex)),
  },
  {
    id: "status",
    name: "Outcome",
    category: "outcome",
    get: (task) => runOutcome(task.status).label,
  },
  {
    id: "outcome_binary",
    name: "Successful vs unsuccessful",
    category: "outcome",
    get: (task) => {
      if (!isMeasured(task)) return "Not measured";
      return task.status === "passed" ? "Successful" : "Unsuccessful";
    },
  },
  {
    id: "csat_score",
    name: "CSAT score",
    category: "conversation",
    get: (task) => (task.csat == null ? "-" : String(Math.round(task.csat))),
    categories: () => Array.from({ length: 11 }, (_, i) => String(i)),
  },
  {
    id: "turn_bucket",
    name: "Turn count bucket",
    category: "conversation",
    get: (task) => turnBucket(task.turns),
    categories: (rows) => {
      const present = new Set(rows.map((task) => turnBucket(task.turns)));
      return TURN_BUCKETS.filter((bucket) => present.has(bucket));
    },
  },
  {
    id: "response_bucket",
    name: "Agent response time (25ms buckets)",
    category: "conversation",
    get: (task) =>
      task.latencyMs == null
        ? "-"
        : fmtMs(Math.floor(task.latencyMs / 25) * 25),
    // Every bucket from just below the fastest call to just above the
    // slowest, empty ones included, like a histogram.
    categories: (rows) => {
      const values = rows
        .map((task) => task.latencyMs)
        .filter((value) => value > 0)
        .sort((a, b) => a - b);
      if (!values.length) return [];
      const lo = Math.max(0, Math.floor(values[0] / 25) * 25 - 25);
      const hi = Math.ceil((values[values.length - 1] + 1) / 25) * 25 + 25;
      const out = [];
      for (let at = lo; at < hi; at += 25) out.push(fmtMs(at));
      return out;
    },
  },
];

export const attributeById = (id) =>
  RUN_ATTRIBUTES.find((attribute) => attribute.id === id);

export const RUN_FILTER_OPERATORS = [
  { label: "Is", value: "is" },
  { label: "Is not", value: "is_not" },
];

/** Distinct values of an attribute across the calls, for the value picker. */
export function distinctAttributeValues(attrId, tasks = []) {
  const attribute = attributeById(attrId);
  if (!attribute) return [];
  return [...new Set(tasks.map((task) => String(attribute.get(task))))].sort();
}

const passesFilters = (row, filters) =>
  (filters || []).every((filter) => {
    const attribute = attributeById(filter.id);
    const values = Array.isArray(filter.value) ? filter.value.map(String) : [];
    if (!attribute || !values.length) return true;
    const hit = values.includes(String(attribute.get(row)));
    return filter.operator === "is_not" ? !hit : hit;
  });

// Nearest rank: the rule every analytics panel uses, so a p90 read in a
// widget is the same number as the p90 on the tab.
const nearestRank = (sorted, p) =>
  sorted.length
    ? sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))]
    : null;

// A tail percentile on too few values is just the maximum, so it is withheld.
const MIN_VALUES = { p90: 10, p95: 20, p99: 100 };

/** Reduce raw per-call values with a dashboards aggregation id. */
export function aggregate(values, aggregation) {
  const raw = values.filter((value) => value != null);
  if (!raw.length) return aggregation === "count" ? 0 : null;
  const isEval = typeof raw[0] === "object";
  if (isEval) {
    const passed = raw.filter((value) => value.passed).length;
    if (aggregation === "pass_rate") return (passed / raw.length) * 100;
    if (aggregation === "fail_rate")
      return ((raw.length - passed) / raw.length) * 100;
    if (aggregation === "pass_count") return passed;
    if (aggregation === "fail_count") return raw.length - passed;
  }
  const nums = (isEval ? raw.map((value) => value.score) : raw)
    .filter((value) => value != null)
    .map(Number)
    .filter(Number.isFinite);
  if (!nums.length) return null;
  const sorted = [...nums].sort((a, b) => a - b);
  if (MIN_VALUES[aggregation] && sorted.length < MIN_VALUES[aggregation])
    return null;
  const percentileOf = {
    median: 0.5,
    p25: 0.25,
    p50: 0.5,
    p75: 0.75,
    p90: 0.9,
    p95: 0.95,
    p99: 0.99,
  };
  if (percentileOf[aggregation] != null)
    return nearestRank(sorted, percentileOf[aggregation]);
  switch (aggregation) {
    case "count":
      return nums.length;
    case "count_distinct":
      return new Set(nums).size;
    case "sum":
      return nums.reduce((a, b) => a + b, 0);
    case "min":
      return sorted[0];
    case "max":
      return sorted[sorted.length - 1];
    default:
      return nums.reduce((a, b) => a + b, 0) / nums.length;
  }
}

/** The unit left once an aggregation turns values into counts or rates. */
export const unitFor = (metric) => {
  if (
    ["count", "count_distinct", "pass_count", "fail_count"].includes(
      metric.aggregation,
    )
  )
    return "";
  if (["pass_rate", "fail_rate"].includes(metric.aggregation)) return "%";
  return metric.unit || "";
};

export const aggLabel = (value) =>
  ALL_AGGREGATIONS.find((item) => item.value === value)?.label ||
  {
    pass_rate: "Pass Rate",
    fail_rate: "Fail Rate",
    pass_count: "Pass Count",
    fail_count: "Fail Count",
  }[value] ||
  value;

// Numbers (and "450ms" / "1.20s") by value, everything else alphabetically.
const toNumber = (value) => {
  const match = String(value).match(/^(-?\d+(?:\.\d+)?)(ms|s)?$/);
  if (!match) return null;
  return match[2] === "s" ? Number(match[1]) * 1000 : Number(match[1]);
};
const naturalCompare = (a, b) => {
  const na = toNumber(a);
  const nb = toNumber(b);
  if (na != null && nb != null) return na - nb;
  return String(a).localeCompare(String(b));
};

// Enough precision for small values; display formatting picks the decimals.
const round = (value) =>
  value == null ? null : Math.round(value * 10000) / 10000;

// Short aggregation names for series labels, dashboards-style "(p90)".
const AGG_SHORT = {
  pass_rate: "pass rate",
  fail_rate: "fail rate",
  pass_count: "passes",
  fail_count: "fails",
  count_distinct: "distinct",
};

/**
 * Evaluate a query over the run's calls. Returns the x-axis buckets and one
 * series per metric x breakdown value, in the shape the dashboards' series
 * table and helpers read ({ name, key, metricIndex, metricName, aggregation,
 * unit, breakdownName, total, data: [{ x, y }] }).
 *
 * query.sort   "value_desc" orders the x-axis largest first
 * query.limit  keeps the first N x-axis values after sorting
 */
export function runWidgetQuery(query, tasks = [], options = {}) {
  const catalog = options.catalog || runMetricCatalog(tasks);
  const xAxis = query?.xAxis || "call";
  const breakdown = (query?.breakdowns || [])[0]
    ? attributeById(query.breakdowns[0].id)
    : null;
  const attrAxis = xAxis.startsWith("attr:")
    ? attributeById(xAxis.slice(5))
    : null;
  const callKey = (task, index) => task.id ?? `call-${index}`;
  const indexed = tasks.map((task, index) => ({
    ...task,
    __callKey: callKey(task, index),
  }));

  const metricRows = (query?.metrics || []).map((metric) => {
    const def = catalog.find((item) => item.id === metric.id);
    if (!def) return null;
    const rows = indexed.filter(
      (task) =>
        (!def.measured || isMeasured(task)) &&
        passesFilters(task, query.filters) &&
        passesFilters(task, metric.filters),
    );
    return { metric, def, rows };
  });

  let buckets;
  let bucketOf;
  if (xAxis === "call") {
    buckets = indexed
      .filter((task) => passesFilters(task, query?.filters))
      .map((task, index) => ({
        key: task.__callKey,
        label: `C${index + 1}`,
        sub: task.sourceScenario || task.scenario || "",
      }));
    bucketOf = (row) => row.__callKey;
  } else if (xAxis === "percentile") {
    buckets = Array.from({ length: 101 }, (_, p) => ({
      key: String(p),
      label: `p${p}`,
      sub: "",
    }));
    bucketOf = null;
  } else if (attrAxis) {
    const all = metricRows.filter(Boolean).flatMap((item) => item.rows);
    const seen = new Set(all.map((row) => String(attrAxis.get(row))));
    const fixed = attrAxis.categories ? attrAxis.categories(all) : null;
    const values =
      fixed || (query?.sort ? [...seen] : [...seen].sort(naturalCompare));
    buckets = values.map((value) => ({ key: value, label: value, sub: "" }));
    bucketOf = (row) => String(attrAxis.get(row));
  } else {
    buckets = [
      {
        key: "run",
        label: options.runLabel || "This run",
        sub: `${tasks.length} call${tasks.length === 1 ? "" : "s"}`,
      },
    ];
    bucketOf = () => "run";
  }

  const series = [];
  metricRows.forEach((entry, metricIndex) => {
    if (!entry) return;
    const { metric, def, rows } = entry;
    const aggregation = metric.aggregation || def.aggregation;
    const unit = unitFor({ ...def, aggregation });
    const aggShort =
      xAxis === "percentile"
        ? "percentile"
        : AGG_SHORT[aggregation] || aggregation;
    const reduce = (list) =>
      list.length
        ? round(aggregate(list.map(def.value), aggregation))
        : aggregation === "count"
          ? 0
          : null;
    const pointsFor = (subset) => {
      if (xAxis === "percentile") {
        const values = subset
          .map(def.value)
          .map((value) =>
            value && typeof value === "object" ? value.score : value,
          )
          .filter(
            (value) =>
              value != null && Number.isFinite(Number(value)) && value > 0,
          )
          .map(Number)
          .sort((a, b) => a - b);
        return buckets.map((bucket) => ({
          x: bucket.label,
          y: values.length
            ? round(nearestRank(values, Number(bucket.key) / 100))
            : null,
        }));
      }
      const byBucket = new Map();
      subset.forEach((row) => {
        const key = bucketOf(row);
        if (!byBucket.has(key)) byBucket.set(key, []);
        byBucket.get(key).push(row);
      });
      return buckets.map((bucket) => ({
        x: bucket.label,
        y: reduce(byBucket.get(bucket.key) || []),
      }));
    };
    // The one number for the whole run (Agg. column, pie slice, metric card)
    // is aggregated once over every call, not recombined from buckets.
    const totalOf = (subset) =>
      xAxis === "percentile" || !subset.length
        ? null
        : round(aggregate(subset.map(def.value), aggregation));
    const name = metric.name || def.name;

    if (!breakdown) {
      series.push({
        name: `${name} (${aggShort})`,
        key: `${metric.id}|${aggregation}|`,
        metricIndex,
        metricName: name,
        aggregation,
        unit,
        breakdownName: null,
        total: totalOf(rows),
        data: pointsFor(rows),
      });
      return;
    }
    const present = new Set(rows.map((row) => String(breakdown.get(row))));
    const values = breakdown.categories
      ? breakdown.categories(rows).filter((value) => present.has(value))
      : [...present].sort(naturalCompare);
    values.forEach((value) => {
      const subset = rows.filter((row) => String(breakdown.get(row)) === value);
      series.push({
        name: `${name} / ${value} (${aggShort})`,
        key: `${metric.id}|${aggregation}|${value}`,
        metricIndex,
        metricName: name,
        aggregation,
        unit,
        breakdownName: value,
        total: totalOf(subset),
        data: pointsFor(subset),
      });
    });
  });

  let order = buckets.map((_, index) => index);
  if (query?.sort === "value_desc") {
    const score = (index) =>
      series.reduce((sum, item) => sum + (item.data[index]?.y || 0), 0);
    order.sort((a, b) => score(b) - score(a));
  }
  if (query?.limit) order = order.slice(0, query.limit);
  if (query?.sort || query?.limit) {
    buckets = order.map((index) => buckets[index]);
    series.forEach((item) => {
      item.data = order.map((index) => item.data[index]);
    });
  }

  return { buckets, series };
}

/** Keys of the top N series by their total: the default selection when a
 *  breakdown gives more series than a chart can read. */
export const MAX_DEFAULT_SERIES = 10;
export function topSeriesKeys(series, n = MAX_DEFAULT_SERIES) {
  const scalar = (item) => (item.total == null ? -Infinity : item.total);
  return [...series]
    .sort((a, b) => scalar(b) - scalar(a))
    .slice(0, n)
    .map((item) => item.key);
}

export const newRunQuery = () => ({
  range: "this",
  xAxis: "call",
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

const metricOf = (catalog, id, aggregation) => {
  const def = catalog.find((item) => item.id === id);
  if (!def) return null;
  return { ...metricFromCatalog(def), ...(aggregation && { aggregation }) };
};

// Built-in analytics widgets whose numbers the calls carry, as a query. The
// rest read run-level aggregates the call list doesn't have (provider flags,
// sentiment, voice pipeline, cost split, tools), so they open blank.
const BUILTIN_QUERIES = {
  goal_outcome: (catalog) => ({
    chart_type: "pie",
    query: {
      xAxis: "run",
      metrics: [metricOf(catalog, "calls")],
      breakdowns: [{ id: "status", name: "Outcome" }],
    },
  }),
  evaluations: (catalog) => ({
    chart_type: "bar",
    query: {
      xAxis: "run",
      metrics: catalog
        .filter((item) => item.category === "evals")
        .map((item) => metricOf(catalog, item.id)),
    },
  }),
  csat: (catalog) => ({
    chart_type: "column",
    query: {
      xAxis: attributeAxisValue("csat_score"),
      metrics: [metricOf(catalog, "calls")],
    },
  }),
  call_duration: (catalog) => ({
    chart_type: "line",
    query: { xAxis: "call", metrics: [metricOf(catalog, "duration_s")] },
  }),
  duration_percentiles: (catalog) => ({
    chart_type: "line",
    query: { xAxis: "percentile", metrics: [metricOf(catalog, "duration_s")] },
  }),
  task_latency: (catalog) => ({
    chart_type: "line",
    query: { xAxis: "call", metrics: [metricOf(catalog, "agent_latency")] },
  }),
  percentiles: (catalog) => ({
    chart_type: "line",
    query: {
      xAxis: "percentile",
      metrics: [metricOf(catalog, "agent_latency")],
    },
  }),
  response_time: (catalog) => ({
    chart_type: "column",
    query: { xAxis: "call", metrics: [metricOf(catalog, "agent_latency")] },
  }),
  slowest: (catalog) => ({
    chart_type: "column",
    query: {
      xAxis: "call",
      metrics: [metricOf(catalog, "duration_s")],
      sort: "value_desc",
      limit: 10,
    },
  }),
};

/** A built-in widget as an editable query, or null when the calls lack it. */
export function builtinQuery(panelId, catalog) {
  const build = BUILTIN_QUERIES[panelId];
  if (!build) return null;
  const { chart_type: chartType, query } = build(catalog);
  return {
    chart_type: chartType,
    query: {
      ...newRunQuery(),
      ...query,
      metrics: (query.metrics || []).filter(Boolean),
    },
  };
}
