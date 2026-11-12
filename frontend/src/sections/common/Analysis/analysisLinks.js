// Where Summary links go (PRODUCT-SPEC §2.3, C2, C10). One builder per
// destination so every count on a Summary opens the grid that shows exactly
// those rows: results links carry `filters=` pill JSON; dataset links carry
// the existing grid filter JSON in `?tab=data&filters=`.
import { serializePills } from "src/sections/common/Results/filterPills";

const DEVELOP = "/dashboard/develop";

const query = (entries) => {
  const params = new URLSearchParams();
  entries.forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  });
  const s = params.toString();
  return s ? `?${s}` : "";
};

/**
 * The results grid (experiment `/data` or compare `/compare/data`).
 * @param {{mode:"experiment"|"compare", datasetId:string, experimentId?:string,
 *   runIds?:string[], baselineId?:string|null, pills?:object[],
 *   show?:string, row?:string, from?:string}} opts
 */
export function resultsGridHref({
  mode,
  datasetId,
  experimentId,
  runIds = [],
  baselineId,
  pills = [],
  show,
  row,
  from,
}) {
  const baseline = baselineId && baselineId !== "none" ? baselineId : null;
  const filters = serializePills(pills);
  if (mode === "experiment" && experimentId) {
    return `${DEVELOP}/experiment/${experimentId}/data${query([
      ["baseline", baseline],
      ["filters", filters],
      ["show", show],
      ["row", row],
    ])}`;
  }
  return `${DEVELOP}/${datasetId}/compare/data${query([
    ["runs", runIds.join(",")],
    ["baseline", baseline],
    ["filters", filters],
    ["show", show],
    ["row", row],
    ["from", from],
  ])}`;
}

/** The compare page's Summary: `DS/compare/summary?runs=&baseline=`. */
export function compareSummaryHref({ datasetId, runIds, baselineId, from }) {
  return `${DEVELOP}/${datasetId}/compare/summary${query([
    ["runs", (runIds ?? []).join(",")],
    ["baseline", baselineId],
    ["from", from],
  ])}`;
}

/** The dataset Data tab with grid filters (one-shot drill-down). */
export function datasetGridHref(datasetId, filters, { version, row } = {}) {
  return `${DEVELOP}/${datasetId}${query([
    ["tab", "data"],
    ["filters", filters?.length ? JSON.stringify(filters) : null],
    ["version", version],
    ["row", row],
  ])}`;
}

/** The focused review page for a queue (`DS/review/<queue>?from=`). */
export function reviewHref(datasetId, queueId, from) {
  return `${DEVELOP}/${datasetId}/review/${queueId}${query([["from", from]])}`;
}

// ---------------------------------------------------------------- Round 2
// R2 builders and parsers (R2-SPEC §0.4; frozen API, R2 manifest row 44).
// Every Iterate entry point builds its link with iterateHref so the dock
// opens the same way from all ten places.

const csv = (ids) => (ids ?? []).filter(Boolean).join(",");

/**
 * The Matrix tab: `…/compare/matrix?runs=&baseline=…` or
 * `…/experiment/<id>/matrix?…`.
 * @param {{mode:"experiment"|"compare", datasetId:string,
 *   experimentId?:string, runIds?:string[], baselineId?:string|null,
 *   pills?:object[], group?:string, sort?:string, show?:string,
 *   row?:string, from?:string}} opts
 */
export function matrixHref({
  mode,
  datasetId,
  experimentId,
  runIds = [],
  baselineId,
  pills = [],
  group,
  sort,
  show,
  row,
  from,
}) {
  const baseline = baselineId && baselineId !== "none" ? baselineId : null;
  const filters = serializePills(pills);
  const rest = [
    ["filters", filters],
    ["group", group && group !== "none" ? group : null],
    ["sort", sort],
    ["show", show && show !== "all" ? show : null],
    ["row", row],
  ];
  if (mode === "experiment" && experimentId)
    return `${DEVELOP}/experiment/${experimentId}/matrix${query([
      ["baseline", baseline],
      ...rest,
    ])}`;
  return `${DEVELOP}/${datasetId}/compare/matrix${query([
    ["runs", csv(runIds)],
    ["baseline", baseline],
    ...rest,
    ["from", from],
  ])}`;
}

export const ITERATE_SCOPES = Object.freeze([
  "all",
  "failed",
  "rows",
  "filter",
  "row",
]);

const ONE_SHOT_R2 = ["iterate", "scope", "rows", "change", "cause"];

/**
 * The same page with the one-shot Iterate params:
 * `iterate=<runId>&scope=<all|failed|rows|filter|row>&rows=<id,id>&change=<preset>&cause=<id>`.
 * Other params of `currentSearch` are kept; stale one-shot params dropped.
 * @param {string|URLSearchParams} currentSearch  `location.search` (with or
 *   without `?`), or a path with a query (`/a/b?x=1`)
 * @param {{runId:string, scope?:string, rows?:string[], change?:string,
 *   cause?:string}} opts
 * @returns {string} `?…` (or `path?…` when a path was passed)
 */
export function iterateHref(
  currentSearch,
  { runId, scope = "all", rows, change, cause } = {},
) {
  let path = "";
  let search = currentSearch ?? "";
  if (typeof search === "string" && !search.startsWith("?")) {
    const at = search.indexOf("?");
    if (at >= 0) {
      path = search.slice(0, at);
      search = search.slice(at);
    } else if (search.startsWith("/")) {
      path = search;
      search = "";
    }
  }
  const params = new URLSearchParams(search);
  ONE_SHOT_R2.forEach((k) => params.delete(k));
  params.set("iterate", runId);
  params.set("scope", ITERATE_SCOPES.includes(scope) ? scope : "all");
  if (rows?.length) params.set("rows", csv(rows));
  if (change) params.set("change", change);
  if (cause) params.set("cause", cause);
  return `${path}?${params.toString()}`;
}

/**
 * By dataset (the Workbench across datasets):
 * `…/compare/summary?runs=…&view=workbench&across=datasets&cand=…`.
 */
export function byDatasetHref({
  datasetId,
  runIds = [],
  baselineId,
  cand,
  configKey,
}) {
  return `${DEVELOP}/${datasetId}/compare/summary${query([
    ["runs", csv(runIds)],
    ["baseline", baselineId],
    ["view", "workbench"],
    ["across", "datasets"],
    ["cand", cand],
    ["config", configKey],
  ])}`;
}

/** The Workbench on the compare Summary tab. */
export function workbenchHref({
  datasetId,
  runIds = [],
  baselineId,
  measure,
  rows,
  cols,
  chart,
  cand,
  across,
  filters,
}) {
  const f = Array.isArray(filters) ? serializePills(filters) : filters;
  return `${DEVELOP}/${datasetId}/compare/summary${query([
    ["runs", csv(runIds)],
    ["baseline", baselineId],
    ["view", "workbench"],
    ["measure", measure],
    ["rows", rows],
    ["cols", cols],
    ["chart", chart],
    ["cand", cand],
    ["across", across],
    ["filters", f || null],
  ])}`;
}

/**
 * Compare two traces on the current page: `?compareTraces=a,b` (other
 * params kept). `currentPath` may carry a query.
 */
export function compareTracesHref(currentPath, [a, b] = []) {
  const at = String(currentPath ?? "").indexOf("?");
  const path = at >= 0 ? currentPath.slice(0, at) : currentPath ?? "";
  const params = new URLSearchParams(at >= 0 ? currentPath.slice(at) : "");
  params.set("compareTraces", csv([a, b]));
  return `${path}?${params.toString()}`;
}

export const R2_VIEWS = Object.freeze(["overview", "workbench"]);
export const R2_COLS = Object.freeze([
  "run",
  "harness",
  "model",
  "prompt_version",
  "agent_version",
  "run_kind",
  "iteration",
  "dataset_version",
]);
export const R2_CHARTS = Object.freeze([
  "distribution",
  "cost_quality",
  "bars",
  "trend",
  "heatmap",
]);
export const R2_SHOW = Object.freeze([
  "all",
  "disagree",
  "flaky",
  "nobody",
  "everyone",
]);
export const R2_GROUPS = Object.freeze([
  "none",
  "category",
  "difficulty",
  "split",
  "source",
  "failed_eval",
]);
export const R2_ACROSS = Object.freeze(["runs", "datasets"]);

/** `none|category|…` or `meta:<key>` or `path:<colName>:<faPath>`. */
export function isValidGroup(value) {
  if (!value) return false;
  if (R2_GROUPS.includes(value)) return true;
  if (/^meta:[^:]+$/.test(value)) return true;
  return /^path:[^:]+:.+$/.test(value);
}

const pick = (value, allowed, fallback) =>
  allowed.includes(value) ? value : fallback;
const list = (value) =>
  value
    ? String(value)
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean)
    : [];

/**
 * Every R2 search param, validated, with defaults.
 * `rows` is the Iterate row list; `rowsAxis` is the Workbench rows axis
 * (also the `rows` param on a `view=workbench` page).
 * @param {URLSearchParams|string|object} searchParams
 */
export function readR2Params(searchParams) {
  const sp =
    searchParams instanceof URLSearchParams
      ? searchParams
      : typeof searchParams === "string"
        ? new URLSearchParams(searchParams)
        : new URLSearchParams(
            Object.entries(searchParams ?? {}).filter(([, v]) => v != null),
          );
  const get = (k) => sp.get(k) || null;
  const view = pick(get("view"), R2_VIEWS, "overview");
  const iterate = get("iterate");
  const traces = list(get("compareTraces"));
  const group = get("group");
  return {
    iterate,
    scope: iterate ? pick(get("scope"), ITERATE_SCOPES, "all") : null,
    rows: view === "workbench" ? [] : list(get("rows")),
    change: get("change"),
    cause: get("cause"),
    across: pick(get("across"), R2_ACROSS, "runs"),
    vs: get("vs"),
    view,
    rowsAxis: view === "workbench" ? get("rows") : null,
    cols: pick(get("cols"), R2_COLS, "run"),
    measure: get("measure"),
    chart: pick(get("chart"), R2_CHARTS, "distribution"),
    cand: get("cand"),
    compareTraces: traces.length === 2 ? traces : null,
    config: get("config"),
    group: isValidGroup(group) ? group : "none",
    sort: get("sort"),
    show: pick(get("show"), R2_SHOW, "all"),
  };
}

// ---------------------------------------------------------------- Round 3
// Side by side (R3 G0 §B10, C4). Every door into the Task compare
// workspace builds its link with taskCompareHref; the workspace reads it
// with readTaskCompareParams. Unknown values fall back silently.

export const TASK_COMPARE_DEPTHS = Object.freeze(["story", "steps", "raw"]);
export const TASK_COMPARE_LEVELS = Object.freeze([
  "milestones",
  "calls",
  "all",
]);
export const TASK_COMPARE_RAILS = Object.freeze([
  "all",
  "differ",
  "flaky",
  "regressed",
  "unreviewed",
]);
export const TASK_COMPARE_RTABS = Object.freeze([
  "waterfall",
  "tools",
  "prompts",
  "workspace",
  "scores",
  "json",
]);
export const TASK_COMPARE_FMTS = Object.freeze(["abs", "delta", "ratio"]);
/** More lanes than this render as Cards. */
export const TASK_COMPARE_MAX_LANES = 4;

/** Defaults (omitted from the URL). `depth: null` = the screen picks by
 * mode (Simple `story`, Advanced the last depth used). */
export const TASK_COMPARE_DEFAULTS = Object.freeze({
  runIds: [],
  baselineId: null,
  rowId: null,
  depth: null,
  trials: {},
  level: "milestones",
  diff: false,
  step: null,
  blind: false,
  rail: "all",
  rtab: "waterfall",
  fmt: "abs",
  from: null,
});

const STEP_RE = /^[ms]\d+$/;

/** `{runId: n}` or `[{runId, n}]` → `runId:n,…` (null when empty). */
export function serializeTrials(trials) {
  const pairs = Array.isArray(trials)
    ? trials.map((t) => [t?.runId ?? t?.run_id, t?.n ?? t?.trial])
    : Object.entries(trials ?? {});
  const parts = pairs
    .filter(([id, n]) => id && Number.isInteger(Number(n)) && Number(n) > 0)
    .map(([id, n]) => `${id}:${Number(n)}`);
  return parts.length ? parts.join(",") : null;
}

/** `runId:n,…` → `{runId: n}`; malformed pairs are skipped. */
export function parseTrials(value) {
  const out = {};
  String(value ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .forEach((pair) => {
      const i = pair.lastIndexOf(":");
      if (i <= 0) return;
      const id = pair.slice(0, i);
      const n = Number(pair.slice(i + 1));
      if (Number.isInteger(n) && n > 0) out[id] = n;
    });
  return out;
}

/**
 * The Task compare workspace:
 * `/dashboard/develop/<ds>/compare/tasks?runs=&baseline=&row=&depth=&trial=…`.
 * @param {{datasetId: string, runIds?: string[], baselineId?: string|null,
 *   rowId?: string|null, depth?: "story"|"steps"|"raw"|null,
 *   trials?: Object<string, number>|{runId: string, n: number}[],
 *   rail?: string, step?: string|null, from?: string|null,
 *   level?: string, diff?: boolean, blind?: boolean, rtab?: string,
 *   fmt?: string}} opts
 */
export function taskCompareHref({
  datasetId,
  runIds = [],
  baselineId,
  rowId,
  depth,
  trials,
  rail,
  step,
  from,
  level,
  diff,
  blind,
  rtab,
  fmt,
} = {}) {
  const baseline = baselineId && baselineId !== "none" ? baselineId : null;
  const d = TASK_COMPARE_DEFAULTS;
  return `${DEVELOP}/${datasetId}/compare/tasks${query([
    ["runs", csv(runIds)],
    ["baseline", baseline],
    ["row", rowId],
    ["depth", TASK_COMPARE_DEPTHS.includes(depth) ? depth : null],
    ["trial", serializeTrials(trials)],
    [
      "level",
      TASK_COMPARE_LEVELS.includes(level) && level !== d.level ? level : null,
    ],
    ["diff", diff ? "1" : null],
    ["step", step && STEP_RE.test(step) ? step : null],
    ["blind", blind ? "1" : null],
    [
      "rail",
      TASK_COMPARE_RAILS.includes(rail) && rail !== d.rail ? rail : null,
    ],
    [
      "rtab",
      TASK_COMPARE_RTABS.includes(rtab) && rtab !== d.rtab ? rtab : null,
    ],
    ["fmt", TASK_COMPARE_FMTS.includes(fmt) && fmt !== d.fmt ? fmt : null],
    ["from", from],
  ])}`;
}

const oneOf = (list, value, fallback) =>
  list.includes(value) ? value : fallback;

/**
 * Parse the workspace URL (URLSearchParams or a search string).
 * @returns {{runIds: string[], baselineId: string|null, rowId: string|null,
 *   depth: "story"|"steps"|"raw"|null, trials: Object<string, number>,
 *   level: string, diff: boolean, step: string|null, blind: boolean,
 *   rail: string, rtab: string, fmt: string, from: string|null,
 *   cards: boolean}}
 */
export function readTaskCompareParams(searchParams) {
  const sp =
    searchParams instanceof URLSearchParams
      ? searchParams
      : new URLSearchParams(searchParams ?? "");
  const d = TASK_COMPARE_DEFAULTS;
  const runIds = [
    ...new Set(
      (sp.get("runs") ?? "")
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
    ),
  ];
  const step = sp.get("step");
  return {
    runIds,
    baselineId: sp.get("baseline") || null,
    rowId: sp.get("row") || null,
    depth: oneOf(TASK_COMPARE_DEPTHS, sp.get("depth"), d.depth),
    trials: parseTrials(sp.get("trial")),
    level: oneOf(TASK_COMPARE_LEVELS, sp.get("level"), d.level),
    diff: sp.get("diff") === "1",
    step: step && STEP_RE.test(step) ? step : null,
    blind: sp.get("blind") === "1",
    rail: oneOf(TASK_COMPARE_RAILS, sp.get("rail"), d.rail),
    rtab: oneOf(TASK_COMPARE_RTABS, sp.get("rtab"), d.rtab),
    fmt: oneOf(TASK_COMPARE_FMTS, sp.get("fmt"), d.fmt),
    from: sp.get("from") || null,
    cards: runIds.length > TASK_COMPARE_MAX_LANES,
  };
}
