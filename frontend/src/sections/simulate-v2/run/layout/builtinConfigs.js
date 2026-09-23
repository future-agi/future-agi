import { DOMAINS } from "../../_mock/failures";

/**
 * Every built-in widget's shipped config. The registry pulls
 * `title`, `info`, and `compatibleCharts` from panelRegistry; this
 * file supplies the actual data + chart config that the unified
 * CustomWidgetBody renders. Editing a built-in via the kebab menu
 * saves patches into layout.overrides which merge on top of this
 * shipped default at render time. Reset clears the overrides.
 */

const ATTRIBUTION_COLORS = Object.fromEntries(
  Object.entries(DOMAINS).map(([id, d]) => [id, d.color]),
);

const CFG = {
  /* ── Breakdowns ─────────────────────────────────────────── */
  success_donut: {
    source: "tasks", chart: "donut", metric: "count",
    groupBy: "outcome_binary",
    colorMap: { Successful: "#16A34A", Unsuccessful: "#DC2626" },
    drilldown: true,
    info: "The single-line answer: what share of tasks the agent actually completed. Everything else on this page tries to explain the delta between this number and 100%.",
  },
  outcome_donut: {
    source: "tasks", chart: "donut", metric: "count",
    groupBy: "status",
    colorMap: { passed: "#16A34A", failed: "#DC2626", error: "#F59E0B", escalated: "#7857FC" },
    drilldown: true,
    info: "Splits the run four ways: passed, failed on evaluator, hard-errored, or escalated. Amber = infra problems; purple = the agent bailed instead of trying.",
  },
  sentiment_donut: {
    source: "tasks", chart: "donut", metric: "count",
    groupBy: "sentiment",
    colorMap: { positive: "#16A34A", neutral: "#94A3B8", negative: "#DC2626" },
    drilldown: true,
    info: "How the simulated caller sounded by the end. A big negative wedge even on passing tasks usually means the agent got the answer right the wrong way.",
  },
  disconnection_donut: {
    source: "tasks", chart: "donut", metric: "count",
    groupBy: "disconnection",
    colorMap: { complete: "#7857FC", escalated: "#F59E0B", incomplete: "#94A3B8", timeout: "#DB2777", error: "#DC2626" },
    drilldown: true,
    info: "How each task actually ended. Big Timeout/Error slices are infra smells; big Escalated is an over-cautious agent; big Incomplete is one that gave up mid-task.",
  },

  /* ── Latency (section id: trends) ─────────────────────────── */
  dual_line_over_time: {
    source: "run_positions", chart: "line", metric: "avg_latency",
    groupBy: "run_position_bucket",
    info: "Latency for each task in the order it ran. Random spikes = flaky infra; a steady climb = retries or context growth; a step change = a new tool or model kicking in mid-run.",
  },
  latency_percentiles: {
    source: "tasks", chart: "percentile_tiles", metric: "p90",
    groupBy: null,
    info: "p50 = typical; p90 = the slower 10% (your SLO number); p99 = your tail. Averages hide this — a run can have a great average and a broken p99.",
  },

  /* ── Distribution ───────────────────────────────────────── */
  distribution_summary: {
    source: "tasks", chart: "percentile_tiles", metric: "p90",
    groupBy: "use_case", limit: 8,
    info: "Percentile shape per use case. A big gap between p50 and p99 means a few outliers are dragging the run and are worth investigating first.",
  },
  attribution: {
    source: "attribution_layers", chart: "donut", metric: "count",
    groupBy: "attribution_layer",
    colorMap: ATTRIBUTION_COLORS,
    drilldown: false,
    info: "Groups every failure by the layer that owns the fix: agent behaviour, transport, environment, simulated caller, or grader. Skips passing tasks entirely.",
  },

  /* ── Failure analysis ───────────────────────────────────── */
  use_case_risk_list: {
    source: "tasks", chart: "bar", metric: "pass_rate",
    groupBy: "use_case", limit: 8,
    info: "Ranks the tasks by the use case they exercise and shows the pass rate for each. Use case at the top is what to fix first.",
  },
  evals_table: {
    source: "graders", chart: "table", metric: "pass_rate",
    groupBy: "eval", limit: 20,
    info: "One row per evaluator with its own pass rate. A task's overall pass/fail is an AND across every grader — a single grader in the red is often the bottleneck.",
  },

  /* ── Voice ──────────────────────────────────────────────── */
  voice_latency: {
    source: "voice_segments", chart: "slo_table", metric: "avg_latency",
    groupBy: "voice_segment",
    info: "Latency broken down by the four voice-pipeline segments: TTFW, LLM thinking, TTS, STT. Red p90 = callers heard silence past your SLO.",
  },
  voice_cost_breakdown: {
    source: "voice_pipeline", chart: "bar", metric: "sum_cost",
    groupBy: "pipeline_stage",
    colorMap: { LLM: "#7857FC", TTS: "#0EA5E9", STT: "#16A34A", Transport: "#F59E0B" },
    info: "Per-call spend, split by voice-pipeline stage. If LLM dominates → overspending on tokens. If TTS/STT → look at voice provider tier.",
  },

  /* ── Performance tails ──────────────────────────────────── */
  slowest_tasks: {
    source: "tasks", chart: "bar", metric: "avg_latency",
    groupBy: "use_case", limit: 8,
    info: "The eight worst offenders on latency. Fix one of these and your percentile tiles above visibly improve.",
  },
  expensive_tasks: {
    source: "tasks", chart: "bar", metric: "sum_cost",
    groupBy: "use_case", limit: 8,
    info: "The eight tasks that ate the most dollars this run. A handful usually dominate the total — shortening prompts on these saves more than optimising every task.",
  },
};

export function getBuiltinConfig(id) {
  return CFG[id] || null;
}

/* All built-in ids that have a unified config — for the retire step
   in Phase 4 we know which ones still need a hand-coded fallback. */
export const UNIFIED_IDS = new Set(Object.keys(CFG));
