/**
 * Reading a comparison.
 *
 * The rows are settled by `comparison.js`; this is everything about *looking*
 * at them — searching, filtering, ordering, and the diff between what one
 * agent version did and what the next one did.
 *
 * The diff is the part worth thinking about. A model comparison diffs one
 * output string against another, because that is the whole artifact. A run is
 * an episode: the agent said things and it did things, and the interesting
 * change is usually the second kind. An agent that stops calling `issue_refund`
 * and starts saying it issued the refund reads as a near-identical transcript
 * and is a completely different agent.
 */

import { FAILURE_MODES, modeOf } from "./coverage";

/* ── filtering ───────────────────────────────────────────────────────────── */

/**
 * The fields a comparison can be narrowed by, in the shape the platform's
 * shared `FilterPanel` speaks: `{ value, label, type, choices }` in, and
 * `[{ field, operator, value }]` tokens back out.
 *
 * Almost all enums. Everything a scenario can be filtered by here is
 * categorical — it moved or it did not, it is a blocker or it is not — and a
 * free-text operator on a category invites "contains fail" to mean four
 * different things.
 */
export const filterFields = (evals = []) => [
  { value: "movement", label: "What moved", type: "enum", choices: ["Fixed", "Broke", "Changed", "Flaky", "Unchanged"] },
  /* "Not run" is a verdict here. A scenario one run never covered is a real
     state of the table, and leaving it out of the choices makes those rows
     unreachable by filter. */
  { value: "baseline", label: "Verdict on baseline", type: "enum", choices: ["Passed", "Failed", "Flaky", "Not measured", "Not run"] },
  { value: "candidate", label: "Verdict on a later run", type: "enum", choices: ["Passed", "Failed", "Flaky", "Not measured", "Not run"] },
  /* Attribution as a filter: "show me only what the agent actually did". */
  { value: "blame", label: "Failure attributed to", type: "enum", choices: ["Agent", "Environment", "Transport", "Simulator", "Grading"] },
  { value: "mode", label: "Failure mode", type: "enum", choices: FAILURE_MODES.map((m) => m.label) },
  { value: "critical", label: "Release blocker", type: "enum", choices: ["Yes", "No"] },
  { value: "saidNotDone", label: "Said, not done", type: "enum", choices: ["Yes", "No"] },
  ...(evals.length
    ? [{ value: "grader", label: "Grader below threshold", type: "enum", choices: evals.map((e) => e.name) }]
    : []),
  { value: "scenario", label: "Scenario", type: "string" },
  { value: "persona", label: "Persona", type: "string" },
];

/** Tokens, not a settings object — the shared panel emits a list. */
export const emptyFilters = () => [];

/* Four verdicts, not two: a scenario can also disagree with itself, or not
   have run in that run at all. */
const VERDICT_LABEL = {
  passed: "Passed",
  failed: "Failed",
  flaky: "Flaky",
  unmeasured: "Not measured",
  missing: "Not run",
};
const verdictLabel = (status) => VERDICT_LABEL[status] || "Not run";

export const activeFilterCount = (tokens) => (Array.isArray(tokens) ? tokens.length : 0);

const asList = (v) => (Array.isArray(v) ? v : v == null || v === "" ? [] : [v]);

const textMatches = (haystack, operator, value) => {
  const hay = String(haystack || "").toLowerCase();
  const needle = String(value || "").toLowerCase();
  if (!needle) return true;
  if (operator === "equals") return hay === needle;
  if (operator === "not_equals") return hay !== needle;
  if (operator === "starts_with") return hay.startsWith(needle);
  if (operator === "not_contains") return !hay.includes(needle);
  return hay.includes(needle);
};

const matchesSearch = (row, q) => {
  if (!q) return true;
  const hay = [
    row.title,
    row.task,
    row.persona?.name,
    row.persona?.role,
    ...row.cells.map((c) => c.deciding?.text || ""),
  ].join(" ").toLowerCase();
  return hay.includes(q.toLowerCase());
};

/** Does one row satisfy one token? */
const rowMatchesToken = (row, token, evals) => {
  const { field, operator, value } = token;
  const chosen = asList(value).map((v) => String(v));
  const negate = operator === "is_not";
  const enumResult = (actual) => {
    if (!chosen.length) return true;
    const hit = asList(actual).some((a) => chosen.includes(String(a)));
    return negate ? !hit : hit;
  };

  const [base, ...rest] = row.cells;

  switch (field) {
    case "movement":
      return enumResult([
        row.fixed && "Fixed",
        row.broke && "Broke",
        row.flaky && "Flaky",
        row.changed && "Changed",
        !row.changed && "Unchanged",
      ].filter(Boolean));
    case "baseline":
      return enumResult(verdictLabel(base?.status));
    case "candidate":
      return enumResult(rest.map((c) => verdictLabel(c.status)));
    case "blame":
      return enumResult(row.cells.map((c) => c.domain?.short).filter(Boolean));
    case "mode":
      return enumResult(FAILURE_MODES.find((m) => m.id === modeOf(row))?.label);
    case "critical":
      return enumResult(row.critical ? "Yes" : "No");
    case "saidNotDone":
      return enumResult(row.cells.some((c) => c.task?.callLog?.unsupportedClaim) ? "Yes" : "No");
    case "grader": {
      const failing = (evals || [])
        .filter((e) => row.cells.some((c) =>
          (c.task?.evalResults || []).some((r) => r.id === e.id && !r.passed)))
        .map((e) => e.name);
      return enumResult(failing.length ? failing : ["\u2014"]);
    }
    case "scenario":
      return textMatches(`${row.title} ${row.task}`, operator, value);
    case "persona":
      return textMatches(`${row.persona?.name || ""} ${row.persona?.role || ""}`, operator, value);
    default:
      return true;
  }
};

export const filterRows = (rows, { query, filters, evals }) =>
  rows.filter((row) => matchesSearch(row, query)
    && (filters || []).every((token) => rowMatchesToken(row, token, evals)));

export const FAILURE_MODE_OPTIONS = FAILURE_MODES;

/* ── ordering ────────────────────────────────────────────────────────────── */

export const SORTS = [
  { id: "movement", label: "Regressions first" },
  { id: "improvement", label: "Improvements first" },
  { id: "order", label: "Scenario order" },
  { id: "slowest", label: "Slowest first" },
  { id: "costliest", label: "Most tokens first" },
];

const worst = (row) => (row.broke ? 0 : row.changed ? 1 : 2);
const best = (row) => (row.fixed ? 0 : row.changed ? 1 : 2);
const lastDuration = (row) => row.cells[row.cells.length - 1]?.durationMs || 0;
const lastTokens = (row) => row.cells[row.cells.length - 1]?.tokens || 0;

export const sortRows = (rows, sort) => {
  const out = [...rows];
  if (sort === "movement") out.sort((a, b) => worst(a) - worst(b));
  if (sort === "improvement") out.sort((a, b) => best(a) - best(b));
  if (sort === "slowest") out.sort((a, b) => lastDuration(b) - lastDuration(a));
  if (sort === "costliest") out.sort((a, b) => lastTokens(b) - lastTokens(a));
  return out;
};

/* ── the diff ────────────────────────────────────────────────────────────── */

const callNames = (task) => (task?.callLog?.calls || []).map((c) => c.name);

/**
 * What this run did differently from the baseline.
 *
 * Tools first, because that is the difference a transcript hides, then the
 * shape of the conversation, then the first place the two actually diverge.
 */
export const behaviourDiff = (baseTask, task) => {
  if (!baseTask || !task) return null;

  const before = callNames(baseTask);
  const after = callNames(task);
  const added = after.filter((n) => !before.includes(n));
  const dropped = before.filter((n) => !after.includes(n));

  /* A tool the scenario needed that this run skipped and the baseline did not
     is the strongest single line this screen can print. */
  const missedNow = (task.callLog?.missing || []).filter(
    (n) => !(baseTask.callLog?.missing || []).includes(n),
  );

  const turnDelta = (task.steps?.length || 0) - (baseTask.steps?.length || 0);

  const firstDivergence = (() => {
    const a = baseTask.steps || [];
    const b = task.steps || [];
    for (let i = 0; i < Math.max(a.length, b.length); i += 1) {
      if ((a[i]?.text || "") !== (b[i]?.text || "")) return i;
    }
    return -1;
  })();

  return {
    added,
    dropped,
    missedNow,
    turnDelta,
    firstDivergence,
    verdictChanged: baseTask.status !== task.status,
    /* Nothing moved at all — worth saying rather than showing an empty row. */
    identical:
      added.length === 0 && dropped.length === 0 && missedNow.length === 0 &&
      turnDelta === 0 && firstDivergence === -1,
  };
};

/**
 * Two transcripts, aligned turn by turn.
 *
 * Deliberately positional rather than a real sequence alignment: the runs share
 * a scenario and a seeded world, so turn 3 is turn 3 in both. A smarter
 * alignment would invent structure that the runs do not have.
 */
export const turnDiff = (baseTask, task) => {
  const a = baseTask?.steps || [];
  const b = task?.steps || [];
  const rows = [];
  for (let i = 0; i < Math.max(a.length, b.length); i += 1) {
    const left = a[i];
    const right = b[i];
    let status = "same";
    if (left && !right) status = "removed";
    else if (!left && right) status = "added";
    else if (left.text !== right.text) status = "changed";
    rows.push({ index: i, left, right, status });
  }
  return rows;
};

/* ── view state ──────────────────────────────────────────────────────────── */

/**
 * How the comparison is being looked at.
 *
 * Three shapes for three questions. The table answers "what moved" — one row
 * per scenario, every run stacked inside it. The grid answers "how did each
 * version handle this" — a column per agent version, read across. The summary
 * answers "which version is better" without reference to any single scenario.
 */
export const VIEWS = [
  { id: "table", label: "Table view", icon: "solar:list-linear", blurb: "One row per scenario" },
  { id: "grid", label: "Grid view", icon: "solar:widget-4-linear", blurb: "One column per agent version" },
  { id: "summary", label: "Summary view", icon: "solar:chart-square-linear", blurb: "Totals per version" },
];

export const ROW_HEIGHTS = [
  { id: "compact", label: "Compact", py: 0.75 },
  { id: "medium", label: "Medium", py: 1.5 },
  { id: "large", label: "Large", py: 2.25 },
];

export const GROUPINGS = [
  { id: "none", label: "Nothing" },
  { id: "mode", label: "Failure mode" },
  { id: "movement", label: "What moved" },
  { id: "critical", label: "Release blockers" },
];

/** Quick row filters — the one-click version of the filter panel. */
export const QUICK_FILTERS = [
  { id: "regressions", label: "Regressions", icon: "solar:arrow-down-linear" },
  { id: "improvements", label: "Improvements", icon: "solar:arrow-up-linear" },
  { id: "critical", label: "Release blockers", icon: "solar:danger-triangle-linear" },
  { id: "saidNotDone", label: "Said, not done", icon: "solar:eye-closed-linear" },
  /* Not a regression and not a pass — scenarios whose own samples disagreed.
     They belong to whoever wrote the scenario, not to whoever changed the
     agent, and they are the rows most likely to be misread as movement. */
  { id: "flaky", label: "Flaky", icon: "solar:refresh-circle-linear" },
  /* The rows that are ours to fix rather than the agent's. */
  { id: "unmeasured", label: "Not measured", icon: "solar:shield-cross-linear" },
  { id: "unchanged", label: "Unchanged", icon: "solar:minus-circle-linear" },
];

export const defaultView = () => ({
  view: "table",
  rowHeight: "medium",
  showChart: true,
  columns: { duration: true, tokens: true, cost: false, scorers: true },
  group: "none",
  quick: [],
  sort: "movement",
  diff: false,
});

/** Quick filters are additive: pick two and you get rows matching either. */
export const applyQuick = (rows, quick) => {
  if (!quick.length) return rows;
  return rows.filter((row) => quick.some((q) => {
    if (q === "regressions") return row.broke;
    if (q === "improvements") return row.fixed;
    if (q === "critical") return row.critical;
    if (q === "saidNotDone") return row.cells.some((c) => c.task?.callLog?.unsupportedClaim);
    if (q === "flaky") return row.flaky;
    if (q === "unmeasured") return row.unmeasured;
    if (q === "unchanged") return !row.changed;
    return true;
  }));
};

/**
 * Rows in labelled groups.
 *
 * Returns a single unlabelled group when grouping is off, so the renderer has
 * one shape to draw rather than two code paths.
 */
export const groupRows = (rows, group) => {
  if (group === "none") return [{ id: "all", label: null, rows }];

  if (group === "movement") {
    const buckets = [
      { id: "broke", label: "Broke", rows: rows.filter((r) => r.broke) },
      { id: "fixed", label: "Fixed", rows: rows.filter((r) => r.fixed) },
      { id: "unmeasured", label: "Not measured — nothing to attribute to the agent", rows: rows.filter((r) => r.unmeasured) },
      { id: "flaky", label: "Flaky — the scenario disagreed with itself", rows: rows.filter((r) => r.flaky && !r.broke && !r.fixed && !r.unmeasured) },
      { id: "changed", label: "Changed otherwise", rows: rows.filter((r) => r.changed && !r.broke && !r.fixed && !r.flaky && !r.unmeasured) },
      { id: "same", label: "Unchanged", rows: rows.filter((r) => !r.changed && !r.unmeasured) },
    ];
    return buckets.filter((b) => b.rows.length);
  }

  if (group === "critical") {
    return [
      { id: "critical", label: "Release blockers", rows: rows.filter((r) => r.critical) },
      { id: "rest", label: "Everything else", rows: rows.filter((r) => !r.critical) },
    ].filter((b) => b.rows.length);
  }

  return FAILURE_MODES
    .map((m) => ({ id: m.id, label: m.label, rows: rows.filter((r) => modeOf(r) === m.id) }))
    .filter((b) => b.rows.length);
};

/* ── summary view ────────────────────────────────────────────────────────── */

/**
 * The comparison with the scenarios taken out.
 *
 * Every number here is already on the summary screen; the point of repeating it
 * is that these are only the runs being compared, read against the baseline.
 */
export const summaryRows = (comparison, evals) => {
  const [baseline] = comparison.runs;
  const metric = (run, get) => get(run);

  const lines = [
    { id: "passRate", label: "Pass rate", format: (v) => `${Math.round(v)}%`, get: (r) => r.passRate, lowerIsBetter: false },
    /* Scenarios that could not decide. A rate that improves while this climbs
       has not improved — it has got noisier. */
    { id: "flaky", label: "Flaky scenarios", format: (v) => `${v}`, get: (r) => r.flaky || 0, lowerIsBetter: true },
    { id: "saidNotDone", label: "Said, not done", format: (v) => `${v}`, get: (r) => r.saidNotDone, lowerIsBetter: true },
    { id: "duration", label: "Avg duration", format: (v) => `${(v / 1000).toFixed(1)}s`, get: (r) => r.avgDurationMs, lowerIsBetter: true },
    { id: "tokens", label: "Tokens", format: (v) => v.toLocaleString(), get: (r) => r.tokens, lowerIsBetter: true },
    { id: "cost", label: "Cost", format: (v) => `$${v.toFixed(2)}`, get: (r) => r.cost, lowerIsBetter: true },
    ...evals.map((e) => ({
      id: `eval:${e.id}`,
      label: e.name,
      format: (v) => `${Math.round(v)}%`,
      get: (r) => r.scores?.[e.id] ?? 0,
      lowerIsBetter: false,
    })),
  ];

  return lines.map((line) => ({
    ...line,
    values: comparison.runs.map((run) => {
      const now = metric(run, line.get);
      const before = metric(baseline, line.get);
      const moved = run.id !== baseline.id && now !== before;
      return {
        run,
        value: now,
        text: line.format(now),
        moved,
        better: moved ? (line.lowerIsBetter ? now < before : now > before) : null,
      };
    }),
  }));
};

/* ── saved views ─────────────────────────────────────────────────────────── */

/**
 * A saved view is the reading, not the runs.
 *
 * "Critical scenarios that regressed, grouped by failure mode, graders on" is
 * a question a team asks after every run; the runs it was first asked about
 * will be gone next month. So a view stores the filters and the display and
 * nothing else — no run ids, and not the search box, which is a thing you type
 * once and clear.
 */
export const viewSnapshot = (filters, view) => ({ filters: { ...filters }, view: { ...view } });

export const snapshotsEqual = (a, b) => {
  if (!a || !b) return false;
  return JSON.stringify(a.filters) === JSON.stringify(b.filters)
    && JSON.stringify(a.view) === JSON.stringify(b.view);
};

export const newViewId = (name, existing = []) => {
  const base = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "view";
  let id = base;
  let n = 2;
  while (existing.some((v) => v.id === id)) { id = `${base}-${n}`; n += 1; }
  return id;
};
