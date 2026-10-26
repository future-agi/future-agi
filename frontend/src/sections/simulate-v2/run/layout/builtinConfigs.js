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
    source: "tasks",
    chart: "donut",
    metric: "count",
    groupBy: "outcome_binary",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    colorMap: {
      Successful: "#16A34A",
      Unsuccessful: "#DC2626",
      "Not measured": "#94A3B8",
    },
    drilldown: true,
    info: "Share of tasks the agent completed.",
  },
  outcome_donut: {
    source: "tasks",
    chart: "donut",
    metric: "count",
    groupBy: "status",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    colorMap: {
      Passed: "#16A34A",
      Flaky: "#F59E0B",
      Failed: "#DC2626",
      "Not measured": "#94A3B8",
    },
    drilldown: true,
    info: "Passed, flaky, failed or not measured. Grey is infrastructure; amber is inconsistency.",
  },

  sentiment_donut: {
    source: "tasks",
    chart: "donut",
    metric: "count",
    groupBy: "sentiment",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    colorMap: { positive: "#16A34A", neutral: "#94A3B8", negative: "#DC2626" },
    drilldown: true,
    info: "How the caller sounded at the end. Negative on passing tasks means right answer, wrong way.",
  },
  disconnection_donut: {
    source: "tasks",
    chart: "donut",
    metric: "count",
    groupBy: "disconnection",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    colorMap: {
      complete: "#7857FC",
      escalated: "#F59E0B",
      incomplete: "#94A3B8",
      timeout: "#DB2777",
      dropped: "#0EA5E9",
      error: "#DC2626",
    },
    drilldown: true,
    info: "How each task ended. Timeout and Error are infrastructure, not the agent.",
  },

  /* ── Latency (section id: trends) ─────────────────────────── */
  dual_line_over_time: {
    source: "run_positions",
    chart: "line",
    metric: "avg_latency",
    groupBy: "run_position_bucket",
    info: "Latency per task in run order. A steady climb usually means growing context.",
  },
  latency_percentiles: {
    source: "tasks",
    chart: "percentile_tiles",
    metric: "p90",
    groupBy: null,
    info: "p50 is typical, p90 the slowest 10%, p99 the tail.",
  },

  /* ── Distribution ───────────────────────────────────────── */
  distribution_summary: {
    source: "tasks",
    chart: "percentile_tiles",
    metric: "p90",
    groupBy: "use_case",
    limit: 8,
    info: "Percentile shape per use case. A big gap between p50 and p99 means a few outliers are dragging the run and are worth investigating first.",
  },
  attribution: {
    source: "attribution_layers",
    chart: "donut",
    metric: "count",
    groupBy: "attribution_layer",
    colorMap: ATTRIBUTION_COLORS,
    drilldown: false,
    info: "Failures by who owns the fix: agent, transport, environment, caller or grader.",
  },

  /* ── Failure analysis ───────────────────────────────────── */
  use_case_risk_list: {
    source: "tasks",
    chart: "bar",
    metric: "pass_rate",
    groupBy: "use_case",
    limit: 8,
    info: "Ranks the tasks by the use case they exercise and shows the pass rate for each. Use case at the top is what to fix first.",
  },
  evals_table: {
    source: "graders",
    chart: "table",
    metric: "pass_rate",
    groupBy: "eval",
    limit: 20,
    info: "Pass rate per evaluator. The one failing most is usually the bottleneck.",
  },

  /* ── Voice ──────────────────────────────────────────────── */
  voice_latency: {
    source: "voice_segments",
    chart: "slo_table",
    metric: "avg_latency",
    groupBy: "voice_segment",
    info: "Latency broken down by the four voice-pipeline segments: TTFW, LLM thinking, TTS, STT. Red p90 = callers heard silence past your SLO.",
  },
  voice_cost_breakdown: {
    source: "voice_pipeline",
    chart: "bar",
    metric: "sum_cost",
    groupBy: "pipeline_stage",
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    colorMap: {
      LLM: "#7857FC",
      TTS: "#0EA5E9",
      STT: "#16A34A",
      Transport: "#F59E0B",
    },
    info: "Per-call spend, split by voice-pipeline stage. If LLM dominates → overspending on tokens. If TTS/STT → look at voice provider tier.",
  },

  /* ── Performance tails ──────────────────────────────────── */
  slowest_tasks: {
    source: "tasks",
    chart: "bar",
    metric: "avg_latency",
    groupBy: "use_case",
    limit: 8,
    info: "The eight worst offenders on latency. Fix one of these and your percentile tiles above visibly improve.",
  },
  expensive_tasks: {
    source: "tasks",
    chart: "bar",
    metric: "sum_cost",
    groupBy: "use_case",
    limit: 8,
    info: "The eight costliest tasks — trimming these usually saves the most.",
  },
};

export function getBuiltinConfig(id) {
  return CFG[id] || null;
}

/* All built-in ids that have a unified config — for the retire step
   in Phase 4 we know which ones still need a hand-coded fallback. */
export const UNIFIED_IDS = new Set(Object.keys(CFG));
