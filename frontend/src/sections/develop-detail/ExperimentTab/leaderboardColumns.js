// Column catalog of the leaderboard (ONE-VIEW-SPEC A2-07): ids, labels,
// groups and the ONE default set, in the shape G's `useColumnLayout` takes.
// Pure; the grid maps ids to colDefs. The user's shape (hidden, frozen,
// order, widths) lives in the layout prefs, never here.
import { runKindOf } from "src/sections/common/Runs/runKind";
// HO results R7 (W5 B-H2): run metrics as leaderboard columns.
import {
  RUN_METRICS_GROUP,
  runMetricCatalogEntries,
} from "src/sections/common/DerivedMetrics/leaderboardRunMetrics";

/** How many dataset evals show by default. */
export const DEFAULT_EVAL_COLUMNS = 2;

const isBlank = (v) => v == null || (typeof v === "number" && Number.isNaN(v));

/**
 * AG Grid comparator that keeps blanks ('—': null, undefined, NaN) LAST in
 * both directions (integration C2): AG negates the result when descending, so the
 * blank side is negated with it. Numbers compare numerically, strings by
 * locale.
 */
export function nullsLastComparator(a, b, _nodeA, _nodeB, isDescending) {
  const na = isBlank(a);
  const nb = isBlank(b);
  if (na && nb) return 0;
  const last = isDescending ? -1 : 1;
  if (na) return last;
  if (nb) return -last;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b));
}

/**
 * Review 2 (S9): an eval column's header clicks go best first — descending
 * on a higher-is-better eval, ascending on a lower-is-better one — then the
 * other way, then unsorted (AG Grid `sortingOrder`).
 * @param {{higher_is_better?: boolean}} def
 */
export function evalSortingOrder(def) {
  return def?.higher_is_better === false
    ? ["asc", "desc", null]
    : ["desc", "asc", null];
}

export const COLUMN_GROUPS = [
  "Summary",
  "Evals",
  RUN_METRICS_GROUP,
  "Human",
  "Cost & latency",
  "Run info",
  "Metadata",
];

// Metadata keys that already have their own column or say nothing new.
const HIDDEN_META = new Set(["source"]);

export const META_PREFIX = "meta:";

/** The name column: always shown, frozen by default (A2-06). */
export const NAME_COLUMN = "experiment";

/** Two-line rows (chip + config), like the DataTable rows. */
export const ROW_HEIGHT = 48;

/**
 * Default widths (px) of the default columns; the grid's colDefs use the
 * same numbers, so the fit below is what the grid draws. Each fits its
 * widest cell (`53.3% −12.3 pts`, `$0.21 +1,809%`, `85.6% +20.0 pts n=30`).
 */
export const COLUMN_WIDTH = Object.freeze({
  updated: 128,
  success_rate: 148, // IT-R1 G2 P1: fits `91.7% · best in #2`
  cost_per_task: 144, // IT-R2 G2 P8: fits `$0.00091 +1,809%` (never `50.0055`)
  latency_p50: 136, // IT-R3 G2 P3: fits `Time (p50)` / `Call length (p50)`
  eval: 176,
  target: 108,
  status: 132,
});

/** A catalog column's default width (px) in the grid. */
export const widthOfColumn = (col) =>
  col?.group === "Evals"
    ? COLUMN_WIDTH.eval
    : COLUMN_WIDTH[col?.id] ?? OTHER_WIDTH[col?.id] ?? 150;

// The rest of the grid's colDef widths (LeaderboardGrid).
const OTHER_WIDTH = {
  dataset: 230,
  latency_p99: 140,
  pass_k: 120,
  where_ran: 200,
  cost: 128,
  tokens: 110,
  errors: 100,
  trials: 140,
  examples: 120,
  win_rate: 150,
  created_by: 170,
  created: 130,
  description: 240,
};

/** The frozen name column (A2-06): 300 px, 260 below 1366 px wide. */
export const nameWidthFor = (windowWidth) => (windowWidth < 1366 ? 260 : 300);

/**
 * P5 "defaults that fit": which default columns go first when the window is
 * too narrow for all of them, highest priority first. The answer
 * (success, cost, time, the first eval) and Status stay; the second eval
 * and Target fold into `N hidden` until there is room.
 */
export const FIT_PRIORITY = Object.freeze([
  "success_rate",
  "cost_per_task",
  "latency_p50",
  "updated",
  "eval:1",
  "status",
  "eval:2",
  "target",
]);

/**
 * Pure: the default set for the room the grid has (P1: the window chooses
 * DEFAULTS only; the user's own show / hide always wins). Default-visible
 * columns are added in FIT_PRIORITY order while they fit; the rest start
 * hidden. No `room` (unknown) → the full default set.
 * @param {object[]} cols columnCatalog() output
 * @param {number} [room] px of the scrolling (unfrozen) part of the grid
 * @returns {object[]} the same columns with `defaultVisible` fitted
 */
export function fitDefaultColumns(cols, room) {
  if (!Number.isFinite(room) || room <= 0) return cols;
  let evalN = 0;
  const slotOf = new Map();
  cols.forEach((c) => {
    if (c.group === "Evals" && c.defaultVisible) {
      evalN += 1;
      slotOf.set(c.id, `eval:${evalN}`);
    } else if (c.defaultVisible && !c.locked) slotOf.set(c.id, c.id);
  });
  const widthOf = (slot) =>
    slot.startsWith("eval:") ? COLUMN_WIDTH.eval : COLUMN_WIDTH[slot] ?? 140;
  const keep = new Set();
  let used = 0;
  FIT_PRIORITY.forEach((slot) => {
    if (![...slotOf.values()].includes(slot)) return;
    const w = widthOf(slot);
    if (used + w <= room) {
      keep.add(slot);
      used += w;
    }
  });
  return cols.map((c) => {
    const slot = slotOf.get(c.id);
    if (!slot || !FIT_PRIORITY.includes(slot) || keep.has(slot)) return c;
    return { ...c, defaultVisible: false };
  });
}

/**
 * The unfrozen room of the grid for a content width (`main`'s width) and a
 * window width: `main` − 24 px gutters − the frozen name − the 40 px
 * checkbox column − 2 px borders.
 */
export const gridRoomFor = (mainWidth, windowWidth) =>
  Math.max(0, mainWidth - 24 - nameWidthFor(windowWidth) - 40 - 2);

/**
 * Header menu `Group by <label>` (A2-07): which columns group the board and
 * the `group=` value each one means.
 */
export const GROUP_BY_COLUMN = Object.freeze({
  experiment: "experiment",
  dataset: "dataset_version",
  target: "target_type",
  status: "status",
  created_by: "created_by",
});

/** The column a `group=` value belongs to (null for model, temperature…). */
export const columnOfGroup = (group) =>
  Object.entries(GROUP_BY_COLUMN).find(([, g]) => g === group)?.[0] ?? null;

/**
 * @param {object[]} metricDefs R1 `metric_defs`
 * @param {object[]} runs R1 results (for metadata keys and preferences)
 * @param {{room?: number, runMetrics?: object}} [opts] the grid's unfrozen
 *   room (fitDefaultColumns); `runMetrics` = the derived-metrics read
 *   ({metrics, values}), one shown column per run metric
 * @returns {{id, label, group, defaultVisible, locked?, defaultPinned?,
 *   sortable?, groupable?, def?}[]} in grid order
 */
export function columnCatalog(
  metricDefs = [],
  runs = [],
  { room, runMetrics } = {},
) {
  const cols = [];
  const add = (id, label, group, defaultVisible, extra = {}) =>
    cols.push({
      id,
      label,
      group,
      defaultVisible,
      ...(GROUP_BY_COLUMN[id] ? { groupable: true } : {}),
      ...extra,
    });

  // The answer first: the headline numbers, then the dataset's evals.
  add(NAME_COLUMN, "Experiment", "Summary", true, {
    locked: true,
    defaultPinned: "left",
    sortable: false,
  });
  // IT-R3 G2 P3: headers name the statistic (rule 20); a voice dataset
  // speaks calls (P9). The full rule is the header's tooltip.
  const voice =
    runs.length > 0 &&
    runs.filter((r) => runKindOf(r) === "voice").length * 2 >= runs.length;
  add(
    "success_rate",
    voice ? "Task completion" : "Success (pass@1)",
    "Summary",
    true,
    {
      description: voice
        ? "Task completion, first try (pass@1): completed tries ÷ calls that ran"
        : "Success, first try (pass@1): passed tries ÷ tries that ran. Errored tries count as fails",
    },
  );
  add(
    "cost_per_task",
    voice ? "Cost per call" : "Cost per task",
    "Summary",
    true,
    {
      description: voice
        ? "Total cost ÷ calls done"
        : "Total cost ÷ tasks done. — = not measured (runs on your key)",
    },
  );
  add(
    "latency_p50",
    voice ? "Call length (p50)" : "Time (p50)",
    "Summary",
    true,
    {
      description: voice
        ? "Median call length (p50)"
        : "Time per task: the median wall time of one task (p50)",
    },
  );
  // R4 (lane Rb, D17 + MUST 7): ONE default set: the run, the three
  // headline numbers and Updated; everything else one click away in
  // Columns (evals, pass^k, tokens, where it ran, owner, status…).
  add("updated", "Updated", "Summary", true);
  // W1-8: a run that set how each row's tries count is read by it here
  // (`pass^3 · 25% (7%–70%)`), so the column shows by default then.
  // Review S3: the column is named for what it shows: `pass^k` when every
  // run is read by it, `From tries` when a run in view set its own rule
  // (each cell then names its rule: `Best · 80% (…)`, `pass^3 · …`).
  const ownRule = runs.some((r) => r?.trial_reducer);
  add("pass_k", ownRule ? "From tries" : "pass^k", "Summary", ownRule, {
    description: ownRule
      ? "Each run's number from its tries: the rule the run set (named in the cell, with its 95% interval), else pass^k"
      : "Share of tasks where every try passed",
  });

  metricDefs
    .filter((d) => d.kind === "eval")
    .forEach((def) => add(def.key, def.name, "Evals", false, { def }));

  runMetricCatalogEntries(runMetrics).forEach(
    ({ id, label, group, defaultVisible, ...rest }) =>
      add(id, label, group, defaultVisible, rest),
  );

  metricDefs
    .filter((d) => d.kind === "human")
    .forEach((def) => add(def.key, def.name, "Human", false, { def }));
  if (runs.some((r) => r.metrics?.preference))
    add("win_rate", "Win rate", "Human", false);

  add("cost", "Total cost", "Cost & latency", false);
  add("latency_p99", "Latency p99", "Cost & latency", false);
  add("tokens", "Tokens", "Cost & latency", false);

  add("where_ran", "Where it ran", "Run info", false);
  add("target", "Target", "Run info", false);
  add("status", "Status", "Run info", false);
  // Redundant inside one dataset; still one click away (it shows the
  // version each run used).
  add("dataset", "Dataset", "Run info", false);
  // L7c g5 (status:leaderboard-errors): the Summary's own words.
  add("errors", "Errors · rows", "Run info", false);
  add("trials", "Trials", "Run info", false);
  add("examples", "Examples", "Run info", false);
  add("created_by", "Owner", "Run info", false);
  add("created", "Created", "Run info", false);
  add("description", "Description", "Run info", false);

  const metaKeys = [
    ...new Set(runs.flatMap((r) => Object.keys(r.metadata ?? {}))),
  ].filter((k) => !HIDDEN_META.has(k));
  metaKeys.forEach((k) => add(`${META_PREFIX}${k}`, k, "Metadata", false));

  return fitDefaultColumns(cols, room);
}

/**
 * One-time migration (A2-07): the old per-dataset Columns overrides
 * (`fa.expList.<id>` `columns: {id: bool}`) as layout column prefs. Only
 * known, unlocked ids that differ from the new default are kept; what the
 * layout already says wins.
 * @param {{[id: string]: boolean}} legacy
 * @param {object[]} catalog columnCatalog()
 * @param {object} [current] layout column prefs
 * @returns {object|null} the column prefs to write, or null when nothing
 *   changes
 */
export function migrateLegacyColumns(legacy, catalog, current = {}) {
  if (!legacy || typeof legacy !== "object") return null;
  const byId = new Map((catalog ?? []).map((c) => [c.id, c]));
  const visible = { ...(current?.visible ?? {}) };
  let changed = false;
  Object.entries(legacy).forEach(([id, shown]) => {
    const col = byId.get(id);
    if (!col || col.locked || typeof shown !== "boolean") return;
    if (id in visible) return;
    if (shown === (col.defaultVisible !== false)) return;
    visible[id] = shown;
    changed = true;
  });
  return changed ? { ...(current ?? {}), visible } : null;
}
