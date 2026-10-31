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
import { PAIR_CLASSES, PAIR_ORDER } from "./pairs";
import { goalLabel } from "./scenarios";

/* ── pair class + flip (PRD v7 §4.10.7) ──────────────────────────────────── */

/*
  Rows carry `pair` once CompareRuns has read them (pairs.withPairs): the
  pair class against the baseline and the flip (passed → failed …). Movement
  is read in that vocabulary — "Exact · passed → failed" — never as a bare
  fixed / regressed, which says nothing about whether the pair compared.
*/
const inDelta = (row) => !row.pair || PAIR_CLASSES[row.pair.cls]?.inDelta;
const flipKind = (row) =>
  row.pair?.flip?.kind ||
  (row.broke
    ? "regressed"
    : row.fixed
      ? "improved"
      : row.changed
        ? "moved"
        : "same");
/** AC-14.6: an exact pair that passed on the baseline and fails now. */
export const isExactRegression = (row) =>
  row.pair?.cls === "exact" && row.pair.flip?.kind === "regressed";

const FLIP_LABEL = {
  regressed: "Passed → failed",
  improved: "Failed → passed",
  moved: "Moved otherwise",
  same: "Unchanged",
};

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
  {
    value: "movement",
    label: "What moved",
    type: "enum",
    choices: [
      "Passed → failed",
      "Failed → passed",
      "Moved otherwise",
      "Flaky",
      "Unchanged",
    ],
  },
  {
    value: "pair",
    label: "Pair class",
    type: "enum",
    choices: PAIR_ORDER.map((k) => PAIR_CLASSES[k].label),
  },
  /* "Not run" is a verdict here. A scenario one run never covered is a real
     state of the table, and leaving it out of the choices makes those rows
     unreachable by filter. */
  {
    value: "baseline",
    label: "Verdict on baseline",
    type: "enum",
    choices: ["Passed", "Failed", "Flaky", "Couldn't measure", "Not run"],
  },
  {
    value: "candidate",
    label: "Verdict on a later run",
    type: "enum",
    choices: ["Passed", "Failed", "Flaky", "Couldn't measure", "Not run"],
  },
  /* Attribution as a filter: "show me only what the agent actually did". */
  {
    value: "blame",
    label: "Failure attributed to",
    type: "enum",
    choices: ["Agent", "Environment", "Transport", "Simulator", "Grading"],
  },
  {
    value: "mode",
    label: "Failure mode",
    type: "enum",
    choices: FAILURE_MODES.map((m) => m.label),
  },
  {
    value: "critical",
    label: "Must-pass",
    type: "enum",
    choices: ["Yes", "No"],
  },
  {
    value: "saidNotDone",
    label: "Said, not done",
    type: "enum",
    choices: ["Yes", "No"],
  },
  ...(evals.length
    ? [
        {
          value: "grader",
          label: "Grader below threshold",
          type: "enum",
          choices: evals.map((e) => e.name),
        },
      ]
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
  unmeasured: "Couldn't measure",
  missing: "Not run",
};
const verdictLabel = (status) => VERDICT_LABEL[status] || "Not run";

export const activeFilterCount = (tokens) =>
  Array.isArray(tokens) ? tokens.length : 0;

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
  ]
    .join(" ")
    .toLowerCase();
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
      return enumResult(
        [FLIP_LABEL[flipKind(row)], row.flaky && "Flaky"].filter(Boolean),
      );
    case "pair":
      return enumResult(row.pair ? PAIR_CLASSES[row.pair.cls]?.label : null);
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
      return enumResult(
        row.cells.some((c) => c.task?.callLog?.unsupportedClaim) ? "Yes" : "No",
      );
    case "grader": {
      const failing = (evals || [])
        .filter((e) =>
          row.cells.some((c) =>
            (c.task?.evalResults || []).some((r) => r.id === e.id && !r.passed),
          ),
        )
        .map((e) => e.name);
      return enumResult(failing.length ? failing : ["\u2014"]);
    }
    case "scenario":
      return textMatches(`${row.title} ${row.task}`, operator, value);
    case "persona":
      return textMatches(
        `${row.persona?.name || ""} ${row.persona?.role || ""}`,
        operator,
        value,
      );
    default:
      return true;
  }
};

export const filterRows = (rows, { query, filters, evals }) =>
  rows.filter(
    (row) =>
      matchesSearch(row, query) &&
      (filters || []).every((token) => rowMatchesToken(row, token, evals)),
  );

export const FAILURE_MODE_OPTIONS = FAILURE_MODES;

/* ── ordering ────────────────────────────────────────────────────────────── */

export const SORTS = [
  { id: "movement", label: "Regressions first" },
  { id: "improvement", label: "Improvements first" },
  { id: "order", label: "Scenario order" },
  { id: "slowest", label: "Slowest first" },
  { id: "costliest", label: "Most tokens first" },
];

/* Regressions first: exact passed → failed, then the other comparable
   regressions, then whatever else moved; pairs outside the delta last. */
const worst = (row) => {
  if (!inDelta(row)) return 4;
  const k = flipKind(row);
  if (k === "regressed") return isExactRegression(row) || !row.pair ? 0 : 1;
  return k === "moved" ? 2 : 3;
};
const best = (row) => {
  if (!inDelta(row)) return 4;
  const k = flipKind(row);
  return k === "improved" ? 0 : k === "moved" ? 2 : 3;
};
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
      added.length === 0 &&
      dropped.length === 0 &&
      missedNow.length === 0 &&
      turnDelta === 0 &&
      firstDivergence === -1,
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
  {
    id: "table",
    label: "Table view",
    icon: "solar:list-linear",
    blurb: "One row per scenario",
  },
  {
    id: "grid",
    label: "Grid view",
    icon: "solar:widget-4-linear",
    blurb: "One column per agent version",
  },
  {
    id: "summary",
    label: "Summary view",
    icon: "solar:chart-square-linear",
    blurb: "Totals per version",
  },
];

export const ROW_HEIGHTS = [
  { id: "compact", label: "Compact", py: 0.75 },
  { id: "medium", label: "Medium", py: 1.5 },
  { id: "large", label: "Large", py: 2.25 },
];

export const GROUPINGS = [
  { id: "none", label: "Nothing" },
  /* Same set the single-run traces table offers, so a user who groups
     by Goal there and jumps to compare finds the same buckets here. */
  { id: "useCase", label: "Goal" },
  { id: "subGoal", label: "Sub-goal" },
  { id: "pattern", label: "Failure pattern" },
  { id: "status", label: "Status" },
  { id: "movement", label: "What moved · pair class" },
  { id: "critical", label: "Must-pass" },
];

/** Quick row filters — the one-click version of the filter panel. */
export const QUICK_FILTERS = [
  {
    id: "regressions",
    label: "Passed → failed",
    icon: "solar:arrow-down-linear",
  },
  {
    id: "improvements",
    label: "Failed → passed",
    icon: "solar:arrow-up-linear",
  },
  { id: "critical", label: "Must-pass", icon: "solar:danger-triangle-linear" },
  {
    id: "saidNotDone",
    label: "Said, not done",
    icon: "solar:eye-closed-linear",
  },
  /* Not a regression and not a pass — scenarios whose own samples disagreed.
     They belong to whoever wrote the scenario, not to whoever changed the
     agent, and they are the rows most likely to be misread as movement. */
  { id: "flaky", label: "Flaky", icon: "solar:refresh-circle-linear" },
  /* The rows that are ours to fix rather than the agent's. */
  {
    id: "unmeasured",
    label: "Couldn't measure",
    icon: "solar:shield-cross-linear",
  },
  { id: "unchanged", label: "Unchanged", icon: "solar:minus-circle-linear" },
];

export const defaultView = () => ({
  view: "table",
  rowHeight: "medium",
  showChart: true,
  columns: {
    csat: true,
    turns: true,
    latency: true,
    tokens: true,
    duration: false,
    cost: false,
    scorers: true,
  },
  /* Same default as the single-run traces table — grouping by Goal
     keeps scenarios that share a use case near each other, so the
     reader can read one use case at a time. */
  group: "useCase",
  quick: [],
  sort: "movement",
  diff: false,
});

/** Quick filters are additive: pick two and you get rows matching either. */
export const applyQuick = (rows, quick) => {
  if (!quick.length) return rows;
  return rows.filter((row) =>
    quick.some((q) => {
      if (q === "regressions")
        return inDelta(row) && flipKind(row) === "regressed";
      if (q === "improvements")
        return inDelta(row) && flipKind(row) === "improved";
      if (q === "critical") return row.critical;
      if (q === "saidNotDone")
        return row.cells.some((c) => c.task?.callLog?.unsupportedClaim);
      if (q === "flaky") return row.flaky;
      if (q === "unmeasured") return row.unmeasured;
      if (q === "unchanged") return flipKind(row) === "same";
      return true;
    }),
  );
};

/**
 * Rows in labelled groups.
 *
 * Returns a single unlabelled group when grouping is off, so the renderer has
 * one shape to draw rather than two code paths.
 */
/*
  Group by the same axes the single-run traces table uses. Where a mode
  needs a task (pattern / status), the "current" cell — the non-baseline
  one, or the baseline itself if it's the only column — is what the
  bucket is chosen against. That answers "how did the run being reviewed
  handle this scenario", which is the question compare exists to answer.
*/
const currentTaskOf = (row) => {
  const cells = row?.cells || [];
  if (!cells.length) return null;
  const nonBaseline = cells.slice(1).find((c) => c.task);
  return nonBaseline?.task || cells[0]?.task || null;
};

const patternOfCompareRow = (row) => {
  const t = currentTaskOf(row);
  if (!t) return null;
  if (t.status === "passed" || t.status === "unmeasured") return null;
  if (t.status === "error") return "Error";
  if (row.critical) return "Critical rule broken";
  if (t.callLog?.unsupportedClaim) return "Said, not done";
  if ((t.evalResults || []).some((r) => !r.passed)) return "Evaluation failed";
  return "Other failure";
};

const statusOfCompareRow = (row) => {
  const t = currentTaskOf(row);
  if (!t) return "Couldn't measure";
  if (t.status === "passed") return "Passed";
  if (t.status === "unmeasured") return "Couldn't measure";
  if (t.status === "error") return "Error";
  return "Failed";
};

const useCaseOfCompareRow = (row) => {
  const t = currentTaskOf(row);
  return goalLabel(row?.useCase || t?.useCase) || "Other";
};

const collectByLabel = (rows, labelFn) => {
  const byLabel = new Map();
  rows.forEach((r) => {
    const label = labelFn(r);
    if (!label) return;
    if (!byLabel.has(label)) byLabel.set(label, []);
    byLabel.get(label).push(r);
  });
  return byLabel;
};

const orderGroups = (byLabel, order) => {
  const entries = Array.from(byLabel.entries());
  if (!order) return entries.sort(([a], [b]) => a.localeCompare(b));
  return entries.sort(([a], [b]) => {
    const ai = order.indexOf(a);
    const bi = order.indexOf(b);
    if (ai === -1 && bi === -1) return a.localeCompare(b);
    if (ai === -1) return 1;
    if (bi === -1) return -1;
    return ai - bi;
  });
};

const GROUP_ORDER = {
  pattern: [
    "Critical rule broken",
    "Said, not done",
    "Evaluation failed",
    "Error",
    "Other failure",
  ],
  status: ["Failed", "Error", "Couldn't measure", "Passed"],
};

export const groupRows = (rows, group) => {
  if (group === "none") return [{ id: "all", label: null, rows }];

  if (group === "useCase") {
    const byLabel = collectByLabel(rows, useCaseOfCompareRow);
    return orderGroups(byLabel).map(([label, rs]) => ({
      id: label,
      label,
      rows: rs,
    }));
  }

  if (group === "pattern") {
    const byLabel = collectByLabel(rows, patternOfCompareRow);
    return orderGroups(byLabel, GROUP_ORDER.pattern).map(([label, rs]) => ({
      id: label,
      label,
      rows: rs,
    }));
  }

  if (group === "status") {
    const byLabel = collectByLabel(rows, statusOfCompareRow);
    return orderGroups(byLabel, GROUP_ORDER.status).map(([label, rs]) => ({
      id: label,
      label,
      rows: rs,
    }));
  }

  if (group === "movement") return pairGroups(rows);

  if (group === "critical") {
    return [
      {
        id: "critical",
        label: "Must-pass",
        rows: rows.filter((r) => r.critical),
      },
      {
        id: "rest",
        label: "Everything else",
        rows: rows.filter((r) => !r.critical),
      },
    ].filter((b) => b.rows.length);
  }

  return FAILURE_MODES.map((m) => ({
    id: m.id,
    label: m.label,
    rows: rows.filter((r) => modeOf(r) === m.id),
  })).filter((b) => b.rows.length);
};

/* ── bands by pair class (AC-14.6, 14.10) ─────────────────────────────────── */

/* What each band outside the delta says, and what can turn it into pairs. */
export const OUTSIDE_BANDS = {
  changed: {
    label: "Changed",
    note: "Side by side, outside the delta — a bridge run makes them exact",
    offer: "bridge",
  },
  new: {
    label: "New",
    note: "In the newer run only — outside the delta until backfilled",
    offer: "backfill",
  },
  "not-applicable": {
    label: "Stale",
    note: "Stale for one of the agent versions — ran, but counts toward no decision",
  },
  archived: { label: "Archived", note: "Archived by hand — outside the delta" },
  removed: { label: "Removed", note: "Only in the older run" },
};

const FLIP_ORDER = ["regressed", "improved", "moved", "same"];
const FLIP_WORDS = {
  regressed: "passed → failed",
  improved: "failed → passed",
  moved: "moved otherwise",
  same: "unchanged",
};

/**
 * "What moved", in pair classes: the comparable classes split by flip,
 * regressions first; then each class outside the delta in its own band.
 */
export const pairGroups = (rows) => {
  const inside = ["exact", "regraded", "rechecked"];
  const groups = [];
  FLIP_ORDER.forEach((flip) =>
    inside.forEach((cls) => {
      const members = rows.filter(
        (r) => (r.pair?.cls || "exact") === cls && flipKind(r) === flip,
      );
      if (members.length) {
        groups.push({
          id: `${cls}-${flip}`,
          label: `${PAIR_CLASSES[cls].label} · ${FLIP_WORDS[flip]}`,
          rows: members,
          ...(cls === "exact" &&
            flip === "regressed" && {
              note: "Regressions — passed on the baseline, fail now",
              tone: "bad",
            }),
        });
      }
    }),
  );
  return [...groups, ...outsideGroups(rows)];
};

const outsideGroups = (rows) =>
  Object.keys(OUTSIDE_BANDS)
    .map((cls) => ({
      id: `band-${cls}`,
      cls,
      ...OUTSIDE_BANDS[cls],
      rows: rows.filter((r) => r.pair?.cls === cls),
    }))
    .filter((b) => b.rows.length);

/**
 * The bands the table draws. Any grouping keeps three rules: exact
 * regressions on top (when sorted regressions-first), the comparable rows
 * grouped as asked, and every class outside the delta in its own band below.
 * `groupFn` groups the comparable rows (useCase, sub-goal, …).
 */
export const bandRows = (rows, { group, regressionsFirst, groupFn }) => {
  if (!rows.some((r) => r.pair)) return groupFn(rows);
  if (group === "movement") return pairGroups(rows);
  /* Exact pairs lead (sorted first); a re-graded or rechecked regression
     follows them, badged by its own class. */
  const top = regressionsFirst
    ? rows.filter((r) => inDelta(r) && flipKind(r) === "regressed")
    : [];
  const inside = rows.filter((r) => inDelta(r) && !top.includes(r));
  const outside = outsideGroups(rows);
  let grouped = inside.length ? groupFn(inside) : [];
  if (
    grouped.length === 1 &&
    !grouped[0].label &&
    (top.length || outside.length)
  ) {
    grouped = [{ ...grouped[0], label: "In the delta" }];
  }
  return [
    ...(top.length
      ? [
          {
            id: "band-regressions",
            label: "Regressions",
            note: "Passed on the baseline, fail now — exact pairs first",
            tone: "bad",
            rows: top,
          },
        ]
      : []),
    ...grouped,
    ...outside,
  ];
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
    /* Over the scenarios every run shares and measured — the same questions
       for every column — falling back to the whole-run rate only when there
       are none in common. */
    {
      id: "passRate",
      label: comparison.coverage?.common
        ? `Pass rate · ${comparison.coverage.common} shared scenarios`
        : "Pass rate",
      format: (v) => (v == null ? "—" : `${Math.round(v)}%`),
      get: (r) => (comparison.coverage?.common ? r.sharedPassRate : r.passRate),
      lowerIsBetter: false,
    },
    /* Scenarios that could not decide. A rate that improves while this climbs
       has not improved — it has got noisier. */
    {
      id: "flaky",
      label: "Flaky scenarios",
      format: (v) => `${v}`,
      get: (r) => r.flaky || 0,
      lowerIsBetter: true,
    },
    {
      id: "saidNotDone",
      label: "Said, not done",
      format: (v) => `${v}`,
      get: (r) => r.saidNotDone,
      lowerIsBetter: true,
    },
    {
      id: "duration",
      label: "Avg duration",
      format: (v) => `${(v / 1000).toFixed(1)}s`,
      get: (r) => r.avgDurationMs,
      lowerIsBetter: true,
    },
    {
      id: "tokens",
      label: "Tokens",
      format: (v) => v.toLocaleString(),
      get: (r) => r.tokens,
      lowerIsBetter: true,
    },
    {
      id: "cost",
      label: "Cost",
      format: (v) => `$${v.toFixed(2)}`,
      get: (r) => r.cost,
      lowerIsBetter: true,
    },
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
        better: moved
          ? line.lowerIsBetter
            ? now < before
            : now > before
          : null,
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
/* Filters are an array (see emptyFilters). Spreading with `{ ...filters }`
   converts them into an object, which breaks `filterRows` (it calls
   `.every()` / `.some()`) the moment a saved view round-trips through
   the snapshot pipeline. Keep the array shape. */
export const viewSnapshot = (filters, view) => ({
  filters: [...(filters || [])],
  view: { ...view },
});

export const snapshotsEqual = (a, b) => {
  if (!a || !b) return false;
  return (
    JSON.stringify(a.filters) === JSON.stringify(b.filters) &&
    JSON.stringify(a.view) === JSON.stringify(b.view)
  );
};

export const newViewId = (name, existing = []) => {
  const base =
    name
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "") || "view";
  let id = base;
  let n = 2;
  while (existing.some((v) => v.id === id)) {
    id = `${base}-${n}`;
    n += 1;
  }
  return id;
};
