/**
 * Reading a comparison: searching, ordering and grouping its rows and the
 * display settings. The rows themselves come from `useComparison`.
 */
import { NONE } from "./compareFormat";

const meanOf = (trials, field) => {
  const xs = trials.map((t) => t[field]).filter((v) => v != null);
  return xs.length ? xs.reduce((a, v) => a + v, 0) / xs.length : null;
};

// A run's numbers on one scenario, averaged over its trials.
export const METRICS = {
  csat: {
    label: "CSAT",
    get: (trials) => {
      const v = meanOf(trials, "csat");
      return v == null ? NONE : String(Math.round(v * 10) / 10);
    },
  },
  turns: {
    label: "Turns",
    get: (trials) => {
      const v = meanOf(trials, "turns");
      return v == null ? NONE : String(Math.round(v));
    },
  },
  latency: {
    label: "Latency",
    get: (trials) => {
      const v = meanOf(trials, "latencyMs");
      return v == null ? NONE : `${Math.round(v)}ms`;
    },
  },
  duration: {
    label: "Duration",
    get: (trials) => {
      const v = meanOf(trials, "durationMs");
      return v == null ? NONE : `${(v / 1000).toFixed(1)}s`;
    },
  },
  tokens: {
    label: "Tokens",
    get: (trials) => {
      const v = meanOf(trials, "tokens");
      return v == null ? NONE : Math.round(v).toLocaleString();
    },
  },
};
export const METRIC_ORDER = ["csat", "turns", "latency", "duration", "tokens"];

/** One eval on one run's trials: the mean score, failing if any trial failed. */
export function evalCell(trials, evalId) {
  const results = trials
    .map((t) => t.evalResults.find((r) => r.id === evalId))
    .filter(Boolean);
  const scores = results.map((r) => r.score).filter((v) => v != null);
  if (!scores.length) return null;
  return {
    score: scores.reduce((a, v) => a + v, 0) / scores.length,
    failed: results.some((r) => r.passed === false),
  };
}

const inDelta = (row) => !row.pair || row.pair.cls === "paired";
const flipKind = (row) => row.pair?.flip?.kind || "same";

/** A scenario both runs played that passed on the baseline and fails now. */
export const isRegression = (row) =>
  row.pair?.cls === "paired" && flipKind(row) === "regressed";

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
    blurb: "One column per run",
  },
  {
    id: "summary",
    label: "Summary view",
    icon: "solar:chart-square-linear",
    blurb: "Totals per run",
  },
];

export const ROW_HEIGHTS = [
  { id: "compact", label: "Compact", py: 0.75 },
  { id: "medium", label: "Medium", py: 1.5 },
  { id: "large", label: "Large", py: 2.25 },
];

// What a comparison can be grouped by. Sub-goal and Must-pass need data the
// API doesn't send yet, so they're listed as coming.
export const GROUPINGS = [
  { id: "none", label: "Nothing" },
  { id: "goal", label: "Goal" },
  { id: "pattern", label: "Failure pattern" },
  { id: "status", label: "Status" },
  { id: "movement", label: "What moved" },
];
export const SOON_GROUPINGS = [
  { id: "subGoal", label: "Sub-goal", what: "Grouping by sub-goal" },
  { id: "critical", label: "Must-pass", what: "A must-pass flag per scenario" },
];

/** Quick row filters. Pick two and you get rows matching either. */
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
  { id: "flaky", label: "Flaky", icon: "solar:refresh-circle-linear" },
  {
    id: "unmeasured",
    label: "Not measured",
    icon: "solar:shield-cross-linear",
  },
  { id: "unchanged", label: "Unchanged", icon: "solar:minus-circle-linear" },
];
export const SOON_QUICK_FILTERS = [
  { id: "critical", label: "Must-pass", what: "A must-pass flag per scenario" },
  {
    id: "saidNotDone",
    label: "Said, not done",
    what: "Claims the call log doesn't support",
    plural: true,
  },
];

export const SORTS = [
  { id: "movement", label: "Regressions first" },
  { id: "improvement", label: "Improvements first" },
  { id: "order", label: "Scenario order" },
  { id: "slowest", label: "Slowest first" },
  { id: "costliest", label: "Most tokens first" },
];

export const COLUMNS = [
  { id: "csat", label: "CSAT" },
  { id: "turns", label: "Turns" },
  { id: "latency", label: "Latency" },
  { id: "duration", label: "Duration" },
  { id: "tokens", label: "Tokens" },
  { id: "cost", label: "Cost" },
  { id: "scorers", label: "Eval scores" },
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
  group: "goal",
  quick: [],
  sort: "movement",
});

const matchesSearch = (row, query) => {
  if (!query) return true;
  const hay = [row.title, row.goal, row.persona, row.scenarioDetails]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return hay.includes(query.toLowerCase());
};

export const searchRows = (rows, query) =>
  rows.filter((row) => matchesSearch(row, query.trim()));

export const applyQuick = (rows, quick) => {
  if (!quick.length) return rows;
  return rows.filter((row) =>
    quick.some((q) => {
      if (q === "regressions")
        return inDelta(row) && flipKind(row) === "regressed";
      if (q === "improvements")
        return inDelta(row) && flipKind(row) === "improved";
      if (q === "flaky") return row.flaky;
      if (q === "unmeasured") return row.unmeasured;
      if (q === "unchanged") return flipKind(row) === "same";
      return true;
    }),
  );
};

// Regressions first: paired passed → failed, then whatever else moved;
// scenarios outside the pairs last.
const worst = (row) => {
  if (!inDelta(row)) return 4;
  const k = flipKind(row);
  if (k === "regressed") return 0;
  return k === "moved" ? 2 : 3;
};
const best = (row) => {
  if (!inDelta(row)) return 4;
  const k = flipKind(row);
  if (k === "improved") return 0;
  return k === "moved" ? 2 : 3;
};
const lastTrials = (row, runIds) => row.cells[runIds[runIds.length - 1]] || [];
const lastTotal = (row, runIds, field) =>
  lastTrials(row, runIds).reduce((a, t) => a + (t[field] || 0), 0);

export const sortRows = (rows, sort, runIds) => {
  const out = [...rows];
  if (sort === "movement") out.sort((a, b) => worst(a) - worst(b));
  if (sort === "improvement") out.sort((a, b) => best(a) - best(b));
  if (sort === "slowest")
    out.sort(
      (a, b) =>
        lastTotal(b, runIds, "durationMs") - lastTotal(a, runIds, "durationMs"),
    );
  if (sort === "costliest")
    out.sort(
      (a, b) => lastTotal(b, runIds, "tokens") - lastTotal(a, runIds, "tokens"),
    );
  return out;
};

// The run being reviewed: the newest one that played the scenario.
const currentVerdict = (row, runIds) => {
  const ran = [...runIds]
    .reverse()
    .find((id) => row.verdicts[id].status !== "missing");
  return ran ? { verdict: row.verdicts[ran], trials: row.cells[ran] } : null;
};

const patternOf = (row, runIds) => {
  const current = currentVerdict(row, runIds);
  if (!current) return null;
  const { verdict, trials } = current;
  if (verdict.status === "passed" || verdict.status === "unmeasured")
    return null;
  if (verdict.status === "error") return "Error";
  if (trials.some((t) => t.evalResults.some((r) => r.passed === false)))
    return "Evaluation failed";
  return "Other failure";
};

const STATUS_LABEL = {
  passed: "Passed",
  failed: "Failed",
  flaky: "Flaky",
  error: "Error",
  in_progress: "In progress",
};
const statusOf = (row, runIds) =>
  STATUS_LABEL[currentVerdict(row, runIds)?.verdict.status] || "Not measured";

const GROUP_ORDER = {
  pattern: ["Evaluation failed", "Error", "Other failure"],
  status: ["Failed", "Flaky", "Error", "Not measured", "In progress", "Passed"],
};

const collect = (rows, labelFn, order) => {
  const byLabel = new Map();
  rows.forEach((row) => {
    const label = labelFn(row);
    if (!label) return;
    if (!byLabel.has(label)) byLabel.set(label, []);
    byLabel.get(label).push(row);
  });
  const rank = (label) => {
    const i = order ? order.indexOf(label) : -1;
    return i === -1 ? Number.MAX_SAFE_INTEGER : i;
  };
  return [...byLabel.entries()]
    .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
    .map(([label, members]) => ({ id: label, label, rows: members }));
};

const FLIP_ORDER = ["regressed", "improved", "moved", "same"];
const FLIP_WORDS = {
  regressed: "Passed → failed",
  improved: "Failed → passed",
  moved: "Moved otherwise",
  same: "Unchanged",
};

// The bands outside the pairs, and what each says.
export const OUTSIDE_BANDS = {
  new: { label: "New", note: "Only in the newer run" },
  removed: { label: "Removed", note: "Only in the baseline" },
};

const outsideGroups = (rows) =>
  Object.keys(OUTSIDE_BANDS)
    .map((cls) => ({
      id: `band-${cls}`,
      cls,
      ...OUTSIDE_BANDS[cls],
      rows: rows.filter((r) => r.pair?.cls === cls),
    }))
    .filter((band) => band.rows.length);

const pairGroups = (rows) => [
  ...FLIP_ORDER.map((flip) => ({
    id: `paired-${flip}`,
    label: FLIP_WORDS[flip],
    rows: rows.filter((r) => inDelta(r) && flipKind(r) === flip),
    ...(flip === "regressed" && {
      note: "Passed on the baseline, fail now",
      tone: "bad",
    }),
  })).filter((g) => g.rows.length),
  ...outsideGroups(rows),
];

export const groupRows = (rows, group, runIds) => {
  if (group === "goal") return collect(rows, (r) => r.goal || "Other");
  if (group === "pattern")
    return collect(rows, (r) => patternOf(r, runIds), GROUP_ORDER.pattern);
  if (group === "status")
    return collect(rows, (r) => statusOf(r, runIds), GROUP_ORDER.status);
  if (group === "movement") return pairGroups(rows);
  return [{ id: "all", label: null, rows }];
};

/**
 * The bands the table draws: regressions on top (when sorted that way), the
 * paired rows grouped as asked, and the scenarios outside the pairs in their
 * own bands below.
 */
export const bandRows = (rows, { group, sort, runIds }) => {
  if (!rows.some((r) => r.pair)) return groupRows(rows, group, runIds);
  if (group === "movement") return pairGroups(rows);
  const top =
    sort === "movement"
      ? rows.filter((r) => inDelta(r) && flipKind(r) === "regressed")
      : [];
  const inside = rows.filter((r) => inDelta(r) && !top.includes(r));
  const outside = outsideGroups(rows);
  let grouped = inside.length ? groupRows(inside, group, runIds) : [];
  if (
    grouped.length === 1 &&
    !grouped[0].label &&
    (top.length || outside.length)
  ) {
    grouped = [{ ...grouped[0], label: "Paired" }];
  }
  return [
    ...(top.length
      ? [
          {
            id: "band-regressions",
            label: "Regressions",
            note: "Passed on the baseline, fail now",
            tone: "bad",
            rows: top,
          },
        ]
      : []),
    ...grouped,
    ...outside,
  ];
};

export const viewSnapshot = (view) => ({ view: { ...view } });

export const snapshotsEqual = (a, b) =>
  !!a && !!b && JSON.stringify(a.view) === JSON.stringify(b.view);
