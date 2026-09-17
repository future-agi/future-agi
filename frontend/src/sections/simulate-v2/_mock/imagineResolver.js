/**
 * Imagine resolver — turns a natural-language prompt about a simulation run
 * into a list of widget definitions the shared `WidgetRenderer` renders.
 *
 * Grounded in real run data. Every number the resolver hands back is
 * computed from the `tasks` array the caller passes in — nothing is
 * fabricated, so a chart reader can trace a number back to specific
 * scenarios. When a prompt asks about failures and the run has zero
 * failures, the resolver falls back to a broader distribution over
 * every measured scenario instead of returning nothing.
 *
 * Contract with the pane:
 *   resolvePrompt(prompt, { tasks, env }) →
 *     { reply: string, widgets: Widget[] } | null
 *
 * A widget matches the shape `WidgetRenderer` reads:
 *   { id, type, title, subtitle?, config }
 *
 * The pane treats a null return as "no matching widget builder — reply
 * with a text-only apology so the user retries with a different prompt".
 */

const paletteFail = ["#DC2626", "#F97316", "#F59E0B", "#EAB308", "#84CC16"];
const paletteMix = ["#7857FC", "#2563EB", "#0EA5E9", "#22C55E", "#F59E0B", "#DC2626"];

/* ── task classifiers used by every builder ─────────────────────────────── */

const isMeasured = (t) => t?.status && t.status !== "unmeasured";
const isPassed = (t) => t?.status === "passed";
const isFailed = (t) => isMeasured(t) && !isPassed(t);

const personaOf = (t) => t?.persona?.name || "No persona";
const goalOf = (t) => t?.useCase || t?.title || "Unknown goal";

/* Extract the specific rule name from titles shaped like
   "Refuse a request that would break: Refunds above $200 need supervisor
   approval". Non-rule scenarios return null and drop out of rule
   groupings — same convention the trace table uses. */
const ruleOf = (t) => {
  const m = (t?.title || "").match(/^Refuse a request that would break:\s*(.+)$/);
  return m ? m[1].trim() : null;
};

/* Percent, rounded, safe against 0-count denominators. */
const pct = (num, den) => (den ? Math.round((num / den) * 100) : 0);

/* Group tasks by a keyFn → { passed, failed, total } — the numbers every
   pass-rate widget wants. Skips null keys so an unrelated task doesn't get
   swept into a group it doesn't belong to. */
function groupCounts(tasks, keyFn) {
  const byKey = new Map();
  tasks.forEach((t) => {
    const k = keyFn(t);
    if (k == null) return;
    const bucket = byKey.get(k) || { passed: 0, failed: 0, total: 0 };
    bucket.total += 1;
    if (isPassed(t)) bucket.passed += 1;
    else if (isFailed(t)) bucket.failed += 1;
    byKey.set(k, bucket);
  });
  return byKey;
}

/* ── individual widget builders ─────────────────────────────────────────── */

function widgetPersonaFailBar(tasks) {
  const byPersona = groupCounts(tasks, personaOf);
  const rows = [...byPersona.entries()]
    .filter(([, v]) => v.total > 0)
    .map(([name, v]) => ({ name, failed: v.failed, passed: v.passed, passRate: pct(v.passed, v.total) }))
    .sort((a, b) => b.failed - a.failed || b.passed - a.passed);
  if (!rows.length) return null;
  return {
    id: `w-persona-${Date.now()}`,
    type: "bar_chart",
    title: "Failures by persona",
    subtitle: "Which caller types the agent handles least well",
    config: {
      horizontal: true,
      categories: rows.map((r) => r.name),
      series: [
        { name: "Failed", data: rows.map((r) => r.failed) },
        { name: "Passed", data: rows.map((r) => r.passed) },
      ],
      colors: ["#DC2626", "#16A34A"],
      stacked: true,
    },
  };
}

function widgetRuleBrokenDonut(tasks) {
  const byRule = groupCounts(tasks, ruleOf); // ruleOf returns null for non-rule scenarios → skipped
  const rows = [...byRule.entries()]
    .map(([rule, v]) => ({ rule, failed: v.failed, total: v.total }))
    .filter((r) => r.failed > 0)
    .sort((a, b) => b.failed - a.failed);
  if (!rows.length) return null;
  const truncate = (s) => (s.length > 42 ? `${s.slice(0, 39)}…` : s);
  const totalFail = rows.reduce((a, r) => a + r.failed, 0);
  return {
    id: `w-rule-${Date.now()}`,
    type: "donut_chart",
    title: "Rules broken most often",
    subtitle: "Only rule-check scenarios contribute — happy-path and edge cases are excluded",
    config: {
      labels: rows.map((r) => truncate(r.rule)),
      series: rows.map((r) => r.failed),
      colors: paletteFail,
      centerLabel: `${totalFail}`,
    },
  };
}

function widgetOutcomeDonut(tasks) {
  const c = { passed: 0, failed: 0, unmeasured: 0, errored: 0 };
  tasks.forEach((t) => {
    if (t.status === "passed") c.passed += 1;
    else if (t.status === "unmeasured") c.unmeasured += 1;
    else if (t.status === "error") c.errored += 1;
    else c.failed += 1;
  });
  const total = tasks.length || 1;
  return {
    id: `w-outcome-${Date.now()}`,
    type: "donut_chart",
    title: "Run outcome",
    subtitle: `${c.passed} of ${total - c.unmeasured} measured passed`,
    config: {
      labels: ["Passed", "Failed", "Errored", "Not measured"].filter((_, i) => [c.passed, c.failed, c.errored, c.unmeasured][i] > 0),
      series: [c.passed, c.failed, c.errored, c.unmeasured].filter((v) => v > 0),
      colors: ["#16A34A", "#DC2626", "#F97316", "#6B7280"],
      centerLabel: `${pct(c.passed, total - c.unmeasured)}%`,
    },
  };
}

function widgetTurnDistribution(tasks) {
  const failed = tasks.filter(isFailed).map((t) => t.turns).filter((n) => Number.isFinite(n));
  const passed = tasks.filter(isPassed).map((t) => t.turns).filter((n) => Number.isFinite(n));
  const both = [...failed, ...passed];
  if (!both.length) return null;
  const min = Math.floor(Math.min(...both));
  const max = Math.ceil(Math.max(...both));
  if (max === min) return null;
  const buckets = 6;
  const step = Math.max(1, Math.ceil((max - min) / buckets));
  const edges = Array.from({ length: buckets + 1 }, (_, i) => min + i * step);
  const categories = edges.slice(0, -1).map((e, i) => `${e}–${edges[i + 1]}`);
  const bucketOf = (n) => Math.min(buckets - 1, Math.max(0, Math.floor((n - min) / step)));
  const failSeries = new Array(buckets).fill(0);
  const passSeries = new Array(buckets).fill(0);
  failed.forEach((n) => { failSeries[bucketOf(n)] += 1; });
  passed.forEach((n) => { passSeries[bucketOf(n)] += 1; });
  return {
    id: `w-turns-${Date.now()}`,
    type: "bar_chart",
    title: "Turn count — failing vs passing",
    subtitle: "Long calls fail more than short ones (or the other way round)",
    config: {
      categories,
      series: [
        { name: "Failed", data: failSeries },
        { name: "Passed", data: passSeries },
      ],
      colors: ["#DC2626", "#16A34A"],
      stacked: false,
    },
  };
}

function widgetLatencyByGoal(tasks) {
  const byGoal = new Map();
  tasks.forEach((t) => {
    if (!Number.isFinite(t?.durationMs)) return;
    const g = goalOf(t);
    const b = byGoal.get(g) || { total: 0, count: 0 };
    b.total += t.durationMs;
    b.count += 1;
    byGoal.set(g, b);
  });
  const rows = [...byGoal.entries()]
    .map(([g, b]) => ({ goal: g, avgMs: Math.round(b.total / b.count) }))
    .sort((a, b) => b.avgMs - a.avgMs)
    .slice(0, 12);
  if (!rows.length) return null;
  return {
    id: `w-latency-${Date.now()}`,
    type: "bar_chart",
    title: "Average duration by goal",
    subtitle: "Slowest 12 goals — cross-check against the pass-rate column in the table",
    config: {
      horizontal: true,
      categories: rows.map((r) => r.goal.length > 50 ? `${r.goal.slice(0, 47)}…` : r.goal),
      series: [{ name: "Avg ms", data: rows.map((r) => r.avgMs) }],
      colors: ["#7857FC"],
    },
  };
}

function widgetGoalPassRateTable(tasks) {
  const byGoal = groupCounts(tasks, goalOf);
  const rows = [...byGoal.entries()]
    .map(([goal, v]) => ({
      goal,
      passed: v.passed,
      total: v.total,
      pass_rate: `${pct(v.passed, v.total)}%`,
    }))
    .sort((a, b) => parseInt(a.pass_rate, 10) - parseInt(b.pass_rate, 10))
    .slice(0, 15);
  if (!rows.length) return null;
  return {
    id: `w-goal-table-${Date.now()}`,
    type: "data_table",
    title: "Pass rate by goal",
    subtitle: "Worst 15, ascending — the goals to fix first",
    config: {
      columns: [
        { key: "goal", label: "Goal" },
        { key: "passed", label: "Passed" },
        { key: "total", label: "Total" },
        { key: "pass_rate", label: "Pass rate" },
      ],
      rows,
    },
  };
}

function widgetFailStepDistribution(tasks) {
  const failed = tasks.filter(isFailed);
  if (!failed.length) return null;
  const zones = { "Opening (0-25%)": 0, "Mid-call (25-75%)": 0, "Closing (75-100%)": 0, "No fail step": 0 };
  failed.forEach((t) => {
    const steps = (t.steps || []).length;
    if (!steps || typeof t.failStep !== "number") { zones["No fail step"] += 1; return; }
    const p = t.failStep / steps;
    if (p < 0.25) zones["Opening (0-25%)"] += 1;
    else if (p < 0.75) zones["Mid-call (25-75%)"] += 1;
    else zones["Closing (75-100%)"] += 1;
  });
  const labels = Object.keys(zones).filter((k) => zones[k] > 0);
  const series = labels.map((k) => zones[k]);
  if (!labels.length) return null;
  return {
    id: `w-failzone-${Date.now()}`,
    type: "donut_chart",
    title: "Where in the call the agent fails",
    subtitle: "Distribution of failure step position across measured failures",
    config: {
      labels,
      series,
      colors: paletteMix,
      centerLabel: `${failed.length}`,
    },
  };
}

function widgetSummaryMarkdown(tasks, env) {
  const total = tasks.length;
  const measured = tasks.filter(isMeasured).length;
  const passed = tasks.filter(isPassed).length;
  const failed = tasks.filter(isFailed).length;
  const critical = tasks.filter((t) => isFailed(t) && t.critical).length;

  const personaCounts = groupCounts(tasks, personaOf);
  const worstPersona = [...personaCounts.entries()]
    .sort((a, b) => b[1].failed - a[1].failed)[0];

  const ruleCounts = groupCounts(tasks, ruleOf);
  const worstRule = [...ruleCounts.entries()]
    .sort((a, b) => b[1].failed - a[1].failed)[0];

  const lines = [
    `**${passed} of ${measured} measured passed** (${pct(passed, measured)}%). ${failed} failed, ${critical} of them critical rule-breakers.`,
    "",
    ...(worstPersona && worstPersona[1].failed > 0
      ? [`- **Weakest persona:** ${worstPersona[0]} — ${worstPersona[1].failed} of ${worstPersona[1].total} calls failed.`]
      : []),
    ...(worstRule && worstRule[1].failed > 0
      ? [`- **Rule broken most:** _${worstRule[0]}_ — ${worstRule[1].failed} failure${worstRule[1].failed === 1 ? "" : "s"}.`]
      : []),
    ...(env?.rules?.length
      ? [`- **Contract:** ${env.rules.length} hard rule${env.rules.length === 1 ? "" : "s"} defined, ${ruleCounts.size} touched by this run.`]
      : []),
  ];
  return {
    id: `w-summary-${Date.now()}`,
    type: "markdown",
    title: "Run summary",
    config: { content: lines.filter(Boolean).join("\n") },
  };
}

/* ── prompt → widget list ───────────────────────────────────────────────── */

const RECIPES = [
  {
    match: /persona|caller.*type|caller.*fail|which caller/i,
    reply: "Grouping the run by persona and stacking failed vs passed calls.",
    build: (tasks) => [widgetPersonaFailBar(tasks)],
  },
  {
    match: /rule|policy|adherence|refus|break/i,
    reply: "Counting which hard rules broke most across the rule-check scenarios in this run.",
    build: (tasks) => [widgetRuleBrokenDonut(tasks)],
  },
  {
    match: /outcome|pass ?rate|overall/i,
    reply: "Overall outcome breakdown for the run.",
    build: (tasks) => [widgetOutcomeDonut(tasks)],
  },
  {
    match: /turn|long|short|length|conversation length/i,
    reply: "Distribution of turn counts, split by pass and fail.",
    build: (tasks) => [widgetTurnDistribution(tasks)],
  },
  {
    match: /latenc|duration|slow|fast|response time/i,
    reply: "Average call duration by goal — slow goals sort to the top.",
    build: (tasks) => [widgetLatencyByGoal(tasks)],
  },
  {
    match: /where.*(fail|break)|opening|closing|mid.?call|step.*(fail|break)/i,
    reply: "Where in the call the agent tends to break down — grouped by position in the conversation.",
    build: (tasks) => [widgetFailStepDistribution(tasks)],
  },
  {
    match: /goal|task|use ?case|worst.*goal|weakest/i,
    reply: "Pass rate per goal, worst first — these are the goals to work on.",
    build: (tasks) => [widgetGoalPassRateTable(tasks)],
  },
  {
    match: /summar|top|theme|overview/i,
    reply: "Here's a short summary of the run.",
    build: (tasks, env) => [widgetSummaryMarkdown(tasks, env)],
  },
  {
    match: /compare.*(pass|fail)|failing vs passing|fail.*vs.*pass/i,
    reply: "Comparing failing vs passing calls across turn count and duration.",
    build: (tasks) => [widgetTurnDistribution(tasks), widgetLatencyByGoal(tasks)],
  },
];

export function resolvePrompt(prompt, ctx = {}) {
  const { tasks = [], env } = ctx;
  const text = String(prompt || "").trim();
  if (!text) return null;

  const recipe = RECIPES.find((r) => r.match.test(text));
  if (!recipe) {
    /* Unknown prompt — hand back a summary so the user gets something
       useful instead of a blank canvas + apology. */
    const widget = widgetSummaryMarkdown(tasks, env);
    return widget
      ? {
          reply: "I couldn't map that to a chart directly, so here's the run summary — try asking about personas, rules, latency, or where in the call things break.",
          widgets: [widget],
        }
      : null;
  }

  const widgets = recipe.build(tasks, env).filter(Boolean);
  if (!widgets.length) {
    return {
      reply: "That angle has no data on this run — everything measured on that dimension came back empty.",
      widgets: [],
    };
  }
  return { reply: recipe.reply, widgets };
}

/* Suggested-prompt chips shown when the canvas is empty. Ordered by how
   often we think a user will reach for each in a debugging session. */
export const IMAGINE_SUGGESTIONS = [
  { label: "Which persona fails most?", icon: "solar:user-rounded-linear" },
  { label: "Which rules break most often?", icon: "solar:shield-cross-linear" },
  { label: "Where in the call does the agent fail?", icon: "solar:map-arrow-square-linear" },
  { label: "Compare failing vs passing calls", icon: "solar:transfer-horizontal-linear" },
  { label: "Pass rate by goal", icon: "solar:target-linear" },
  { label: "Summarize the run", icon: "solar:document-text-linear" },
];

export default resolvePrompt;
