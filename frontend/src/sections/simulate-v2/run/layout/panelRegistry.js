/**
 * Panel registry for the single-run analytics tab.
 *
 * Every built-in panel is enumerated here with a stable id, its
 * section, its default column span, and whether it requires a voice
 * surface. The RunAnalyticsV2 shell walks this list (filtered by the
 * user's chosen layout) and calls `renderPanel(id, ctx)` from
 * panelRenderers.jsx to produce the actual JSX.
 *
 * The registry is decoupled from the renderer so layout state can be
 * serialised, saved to localStorage, and reasoned about without
 * touching React.
 */

/**
 * `columns` = number of half-panels across on md+ screens. 4 = four
 * narrow tiles side by side (Breakdowns donuts); 2 = two half-panels
 * per row (everything else). A panel's `defaultSpan` is measured in
 * these column units — a span of 2 in a 4-column section takes half
 * the row; a span of 2 in a 2-column section takes the full width.
 */
export const SECTIONS = [
  { id: "breakdowns",         label: "Breakdowns",        columns: 4 },
  { id: "evaluations",        label: "Evaluations",       columns: 2 },
  { id: "voice",              label: "Voice latency SLOs", columns: 2 },
  { id: "trends",             label: "Trends",            columns: 2 },
  { id: "distribution",       label: "Distribution",      columns: 2 },
  { id: "failure_analysis",   label: "Failure analysis",  columns: 2 },
  { id: "tools",              label: "Tools",             columns: 2 },
  { id: "performance_tails",  label: "Performance tails", columns: 2 },
  { id: "custom",             label: "Custom widgets",    columns: 2 },
];

export function sectionMeta(id) {
  return SECTIONS.find((s) => s.id === id) || null;
}

/**
 * Column span options — 1 (half width on md+), 2 (full width).
 * A section's grid is repeat(2, 1fr) at md, so span=2 pushes a panel
 * onto its own full-width row.
 */
/* Compatible-chart whitelists per widget. The editor filters the
   chart-type dropdown down to these options so users can't pick a
   chart that breaks the widget's meaning (e.g. turning "Success
   donut" into a line chart). Missing => everything allowed. */
const CHARTS = {
  DONUT_LIKE:   ["donut", "bar", "bignumber"],
  RANK_BAR:     ["bar", "table", "bignumber"],
  DISTRIBUTION: ["bar", "line", "table", "percentile_tiles"],
  TREND:        ["line", "bar"],
  MATRIX:       ["heatmap", "stacked_bar", "bar", "table", "radar"],
  TABLE:        ["table", "bar", "bignumber"],
  ATTRIBUTION:  ["donut", "bar", "table"],
  SLO:          ["slo_table", "table", "bar"],
};

export const PANELS = [
  // ─── Breakdowns ────────────────────────────────────────────────
  { id: "success_donut",       section: "breakdowns", title: "Call successful",              defaultShown: true, defaultSpan: 1, compatibleCharts: CHARTS.DONUT_LIKE },
  { id: "outcome_donut",       section: "breakdowns", title: "Goal outcome breakdown",       defaultShown: true, defaultSpan: 1, compatibleCharts: CHARTS.DONUT_LIKE },
  { id: "sentiment_donut",     section: "breakdowns", title: "User sentiment",               defaultShown: true, defaultSpan: 1, compatibleCharts: CHARTS.DONUT_LIKE },
  { id: "disconnection_donut", section: "breakdowns", title: "Disconnection reason",         defaultShown: true, defaultSpan: 1, compatibleCharts: CHARTS.DONUT_LIKE },

  // ─── Trends ────────────────────────────────────────────────────
  { id: "dual_line_over_time", section: "trends", title: "Task latency",                      defaultShown: true, defaultSpan: 1, compatibleCharts: CHARTS.TREND },
  { id: "latency_percentiles", section: "trends", title: "Latency percentiles",               defaultShown: true, defaultSpan: 1, compatibleCharts: ["percentile_tiles", "bar", "bignumber"] },

  // ─── Distribution ──────────────────────────────────────────────
  { id: "distribution_summary", section: "distribution", title: "Distribution summary",       defaultShown: true, defaultSpan: 2, compatibleCharts: ["percentile_tiles", "table", "bar"] },
  { id: "duration_by_bucket",   section: "distribution", title: "Avg duration by complexity", defaultShown: true, defaultSpan: 1, compatibleCharts: CHARTS.DISTRIBUTION },
  { id: "turn_bars",            section: "distribution", title: "Pass / fail by conversation length", defaultShown: true, defaultSpan: 1, compatibleCharts: ["stacked_bar", "bar", "line"] },
  { id: "attribution",          section: "distribution", title: "Failure attribution",        defaultShown: true, defaultSpan: 2, compatibleCharts: CHARTS.ATTRIBUTION },

  // ─── Failure analysis ──────────────────────────────────────────
  { id: "use_case_risk_list",   section: "failure_analysis", title: "Use case risk",         defaultShown: true, defaultSpan: 2, compatibleCharts: CHARTS.RANK_BAR },
  { id: "evals_table",          section: "evaluations",      title: "Evaluations",           defaultShown: true, defaultSpan: 2, compatibleCharts: CHARTS.TABLE },

  // ─── Voice (surface-gated) ─────────────────────────────────────
  { id: "voice_latency",        section: "voice", title: "Voice latency SLOs",               defaultShown: true, defaultSpan: 1, voiceOnly: true, compatibleCharts: CHARTS.SLO },
  { id: "voice_cost_breakdown", section: "voice", title: "Cost breakdown by pipeline stage", defaultShown: true, defaultSpan: 1, voiceOnly: true, compatibleCharts: ["stacked_bar", "bar", "donut"] },

  // ─── Tools (Monika's ask) ──────────────────────────────────────
  { id: "tool_call_volume",   section: "tools", title: "Tool call volume",   defaultShown: true, defaultSpan: 1, compatibleCharts: ["donut", "bar", "table"] },
  { id: "tool_failure_rate",  section: "tools", title: "Tool failure rate",  defaultShown: true, defaultSpan: 1, compatibleCharts: ["table", "bar"] },

  // ─── Performance tails ─────────────────────────────────────────
  { id: "slowest_tasks",        section: "performance_tails", title: "Slowest tasks",         defaultShown: true, defaultSpan: 1, compatibleCharts: CHARTS.RANK_BAR },
  { id: "expensive_tasks",      section: "performance_tails", title: "Most expensive tasks",  defaultShown: true, defaultSpan: 1, compatibleCharts: CHARTS.RANK_BAR },

  /* Golden set + divergence panels — hidden for now. Registry
     entries kept out so the section header + widgets don't render
     even for users who already have them in a stored layout (the
     hydrate step drops unknown ids). Panel components live in
     goldenSet.jsx if we want to bring them back. */
];

export function compatibleChartsFor(id) {
  return getPanelMeta(id)?.compatibleCharts || null;
}

export function getPanelMeta(id) {
  return PANELS.find((p) => p.id === id) || null;
}

/**
 * Default visible ids for a given env surface. Excludes voice-only
 * panels for non-voice runs so a fresh chat-run reader isn't offered
 * "hide voice latency" as a choice.
 */
export function defaultVisibleIds(surface) {
  return PANELS
    .filter((p) => p.defaultShown)
    .filter((p) => !p.voiceOnly || surface === "voice")
    .map((p) => p.id);
}

/**
 * Every id that could ever be shown for the given surface. Used to
 * pre-populate the Customize drawer's "Hidden" bucket.
 */
export function allEligibleIds(surface) {
  return PANELS
    .filter((p) => !p.voiceOnly || surface === "voice")
    .map((p) => p.id);
}

export function panelSection(id) {
  return getPanelMeta(id)?.section || "custom";
}

export function panelTitle(id, fallback) {
  return getPanelMeta(id)?.title || fallback || id;
}

export function panelSpan(id) {
  return getPanelMeta(id)?.defaultSpan || 1;
}

/**
 * Snapshot a built-in panel into a custom-widget config so the user
 * can "Customize a copy" and get an editable equivalent to work
 * with. For panels that don't map cleanly to the generic renderer
 * (voice pipeline breakdown, distribution summary, dual-line trend)
 * we produce the closest single-chart approximation. The user can
 * then reshape it in the editor.
 */
export function snapshotAsCustom(id) {
  const meta = getPanelMeta(id);
  const title = meta?.title ? `${meta.title} (copy)` : "Custom widget";
  const base = {
    id: `custom-${Math.random().toString(36).slice(2, 10)}`,
    kind: "custom",
    title,
    source: "tasks",
    chart: "bar",
    metric: "count",
    groupBy: "persona",
    subGroupBy: null,
    limit: 8,
    filters: [],
  };
  switch (id) {
    case "success_donut":
      return { ...base, chart: "donut", metric: "count", groupBy: "outcome_binary" };
    case "outcome_donut":
      return { ...base, chart: "donut", metric: "count", groupBy: "status" };
    case "sentiment_donut":
      return { ...base, chart: "donut", metric: "count", groupBy: "sentiment" };
    case "disconnection_donut":
      return { ...base, chart: "donut", metric: "count", groupBy: "disconnection" };
    case "latency_percentiles":
      return { ...base, chart: "bignumber", metric: "avg_latency", groupBy: null };
    case "distribution_summary":
      return { ...base, chart: "table", metric: "avg_latency", groupBy: "use_case", limit: 10 };
    case "duration_by_bucket":
      return { ...base, chart: "bar", metric: "avg_latency", groupBy: "turn_bucket" };
    case "turn_bars":
      return { ...base, chart: "stacked_bar", metric: "count", groupBy: "turns", subGroupBy: "outcome_binary" };
    case "attribution":
      return { ...base, chart: "donut", metric: "count", groupBy: "attribution" };
    case "use_case_risk_list":
      return { ...base, chart: "bar", metric: "pass_rate", groupBy: "use_case", limit: 8 };
    case "evals_table":
      return { ...base, source: "graders", chart: "table", metric: "pass_rate", groupBy: "eval", limit: 20 };
    case "voice_latency":
      return { ...base, chart: "bar", metric: "avg_latency", groupBy: "use_case", limit: 8 };
    case "voice_cost_breakdown":
      return { ...base, chart: "stacked_bar", metric: "sum_cost", groupBy: "use_case", subGroupBy: "outcome_binary", limit: 8 };
    case "slowest_tasks":
      return { ...base, chart: "bar", metric: "avg_latency", groupBy: "use_case", limit: 8 };
    case "expensive_tasks":
      return { ...base, chart: "bar", metric: "sum_cost", groupBy: "use_case", limit: 8 };
    case "dual_line_over_time":
      return { ...base, source: "run_positions", chart: "line", metric: "avg_latency", groupBy: "run_position_bucket" };
    default:
      return base;
  }
}
