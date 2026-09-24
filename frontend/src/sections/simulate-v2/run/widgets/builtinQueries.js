/**
 * Each built-in analytics chart as a widget query — the exact data, chart
 * type, colours and axis format it has on the Analytics tab. Opening a
 * built-in in the editor shows the same graph; what you change there is what
 * the graph on the tab becomes when you save. "Reset to default" brings the
 * original back.
 */
import { DOMAINS } from "../../_mock/failures";
import { metricFromCatalog } from "./simWidgetQuery";

const m = (catalog, id, aggregation, name) => {
  const def = catalog.find((c) => c.id === id);
  if (!def) return null;
  const metric = metricFromCatalog(def);
  return { ...metric, ...(aggregation && { aggregation }), ...(name && { name }) };
};

const by = (id, name) => [{ id, name }];
const MEASURED = [{ id: "status", name: "Outcome", operator: "is_not", value: ["Not measured"] }];

/* Colours the built-in charts use. */
const PURPLE = "#7857FC";
const RED = "#DC2626";
const AMBER = "#F59E0B";
const TOOL_COLORS = ["#3B82F6", "#F97316", "#14B8A6", "#A855F7", "#EC4899", "#EAB308", "#22C55E", "#EF4444", "#06B6D4", "#8B5CF6"];

/* Axis formats the built-in charts print. */
const plainAxis = { leftY: { abbreviation: false, decimals: 0, showSeriesUnit: false } };
const msAxis = { leftY: { abbreviation: false, decimals: 0, unit: "ms", prefixSuffix: "suffix" } };
const countAxis = { leftY: { abbreviation: false, decimals: 0, showSeriesUnit: false } };
const pctAxis = { leftY: { abbreviation: false, decimals: 0 } };
const dollarAxis = (decimals) => ({ leftY: { abbreviation: false, decimals, unit: "$", prefixSuffix: "prefix" } });

const evalMetrics = (catalog, aggregation) => catalog
  .filter((c) => c.category === "evals")
  .map((def) => ({ ...metricFromCatalog(def), aggregation }));

const DEFS = {
  success_donut: (c) => ({
    chart_type: "pie",
    axis_config: countAxis,
    query: { metrics: [m(c, "calls")], breakdowns: by("call_outcome", "Call outcome"), filters: MEASURED },
    display: { colorMap: { Successful: PURPLE, Unsuccessful: RED } },
  }),
  outcome_donut: (c) => ({
    chart_type: "pie",
    axis_config: countAxis,
    query: { metrics: [m(c, "tasks")], breakdowns: by("status", "Outcome") },
    display: { colorMap: { Passed: "#34D399", Flaky: AMBER, Failed: "#F87171", "Not measured": "#94A3B8" } },
  }),
  sentiment_donut: (c) => ({
    chart_type: "pie",
    axis_config: countAxis,
    query: { metrics: [m(c, "tasks")], breakdowns: by("sentiment", "Caller sentiment"), filters: MEASURED },
    display: { colorMap: { Positive: "#16A34A", Neutral: "#94A3B8", Negative: RED } },
  }),
  disconnection_donut: (c) => ({
    chart_type: "pie",
    axis_config: countAxis,
    query: { metrics: [m(c, "tasks")], breakdowns: by("disconnection", "Disconnection reason") },
    display: {
      colorMap: {
        "Task complete": PURPLE, Escalated: AMBER, Incomplete: "#94A3B8",
        Timeout: "#DB2777", Dropped: "#0EA5E9", Error: RED,
      },
    },
  }),
  evals_table: (c) => ({
    chart_type: "table",
    query: { metrics: evalMetrics(c, "pass_rate") },
    axis_config: pctAxis,
  }),
  csat_distribution: (c) => ({
    chart_type: "column",
    query: { xAxis: "attr:csat_score", metrics: [m(c, "tasks", "count", "Calls")], filters: MEASURED },
    axis_config: countAxis,
    display: { categoryRule: { op: "lte", value: 4, match: RED, other: PURPLE } },
  }),
  voice_latency: (c) => ({
    chart_type: "table",
    query: {
      metrics: [m(c, "segment_latency", "p50"), m(c, "segment_latency", "p90"), m(c, "segment_latency", "p99")],
      breakdowns: by("segment", "Voice segment"),
    },
    axis_config: msAxis,
  }),
  voice_cost_breakdown: (c) => ({
    chart_type: "stacked_column",
    query: {
      xAxis: "call",
      metrics: [
        m(c, "cost_llm", "sum", "LLM"), m(c, "cost_tts", "sum", "TTS"),
        m(c, "cost_stt", "sum", "STT"), m(c, "cost_transport", "sum", "Transport"),
      ],
    },
    axis_config: dollarAxis(2),
    display: { colorMap: { LLM: PURPLE, TTS: "#0EA5E9", STT: AMBER, Transport: "#94A3B8" } },
  }),
  dual_line_over_time: (c) => ({
    chart_type: "line",
    query: { xAxis: "call", metrics: [m(c, "duration", "avg", "Call duration")], filters: MEASURED },
    axis_config: plainAxis,
    display: { seriesColor: PURPLE, curve: "smooth", markers: false, legend: "metric", yMinAuto: 2000 },
  }),
  latency_percentiles: (c) => ({
    chart_type: "stacked_line",
    query: { xAxis: "percentile", metrics: [m(c, "duration", "avg", "Call duration")], filters: MEASURED },
    axis_config: plainAxis,
    display: { seriesColor: "#0EA5E9", legend: "metric", yMinAuto: 2000 },
  }),
  agent_response_time: (c) => ({
    chart_type: "column",
    query: { xAxis: "attr:response_bucket", metrics: [m(c, "tasks", "count", "Calls")] },
    axis_config: countAxis,
    display: { categoryRule: { op: "gte", value: 550, match: RED, other: PURPLE } },
  }),
  distribution_summary: (c) => ({
    chart_type: "table",
    query: {
      metrics: [
        ...["p50", "p90", "p99", "max"].map((a) => m(c, "duration_s", a, "Call duration")),
        ...["p50", "p90", "p99", "max"].map((a) => m(c, "cost", a, "Cost per scenario")),
        ...["p50", "p90", "p99", "max"].map((a) => m(c, "tokens", a, "Tokens per scenario")),
        ...["p50", "p90", "p99", "max"].map((a) => m(c, "turns", a, "Turns per call")),
      ],
    },
    axis_config: { leftY: { abbreviation: false } },
    display: {
      decimals: { "Call duration": 1, "Cost per scenario": 3, "Tokens per scenario": 0, "Turns per call": 0 },
      units: { "Tokens per scenario": "", "Turns per call": "" },
    },
  }),
  attribution: (c) => ({
    chart_type: "pie",
    axis_config: countAxis,
    query: {
      metrics: [m(c, "tasks")],
      breakdowns: by("attribution", "Failure attribution"),
      filters: [{ id: "status", name: "Outcome", operator: "is_not", value: ["Passed"] }],
    },
    display: { colorMap: Object.fromEntries(Object.values(DOMAINS).map((d) => [d.label, d.color])) },
  }),
  use_case_risk_list: (c) => ({
    chart_type: "stacked_bar",
    query: {
      xAxis: "attr:use_case",
      metrics: [m(c, "calls")],
      breakdowns: by("call_verdict", "Call verdict"),
      filters: MEASURED,
      sort: { by: "weakest", good: "Passed", bad: "Failed" },
      limit: 7,
      reverse: true,
    },
    axis_config: countAxis,
    display: { colorMap: { Passed: PURPLE, Failed: RED }, legend: "breakdown", dataLabels: true },
  }),
  tool_call_volume: (c) => ({
    chart_type: "column",
    query: { xAxis: "attr:tool_name", metrics: [m(c, "tool_calls", "count", "Calls")], sort: "value_desc" },
    axis_config: countAxis,
    display: { categoryPalette: TOOL_COLORS, dataLabels: true },
  }),
  tool_failure_rate: (c) => ({
    chart_type: "stacked_bar",
    query: {
      xAxis: "attr:tool_name",
      metrics: [m(c, "tool_failure_rate", "avg", "Tool failed"), m(c, "tool_not_called_rate", "avg", "Not called when required")],
      sort: "value_desc",
    },
    axis_config: pctAxis,
    display: { colorMap: { "Tool failed": RED, "Not called when required": AMBER }, legend: "metric", valueMax: 100 },
  }),
  slowest_tasks: (c) => ({
    chart_type: "column",
    query: {
      xAxis: "call", metrics: [m(c, "duration_s", "avg", "Call duration")], filters: MEASURED,
      sort: "value_desc", limit: 8, rankLabels: true,
    },
    axis_config: { leftY: { abbreviation: false, decimals: 1, unit: "s", prefixSuffix: "suffix" } },
    display: { seriesColor: PURPLE, dataLabels: true, legend: "metric" },
  }),
  expensive_tasks: (c) => ({
    chart_type: "column",
    query: {
      xAxis: "call", metrics: [m(c, "cost", "sum", "Cost")],
      sort: "value_desc", limit: 8, rankLabels: true,
    },
    axis_config: dollarAxis(3),
    display: { seriesColor: "#DB2777", dataLabels: true, legend: "metric" },
  }),
};

/** { chart_type, query, axis_config?, display? } for a built-in panel id, or null. */
export function builtinQuery(panelId, catalog) {
  const make = DEFS[panelId];
  if (!make) return null;
  const { chart_type: chartType, query, axis_config: axisConfig, display } = make(catalog);
  return {
    chart_type: chartType,
    axis_config: axisConfig || null,
    display: display || null,
    query: {
      range: "this",
      xAxis: "run",
      filters: [],
      breakdowns: [],
      ...query,
      metrics: (query.metrics || []).filter(Boolean),
    },
  };
}

export const hasBuiltinQuery = (panelId) => !!DEFS[panelId];
