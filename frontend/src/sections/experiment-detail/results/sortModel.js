// Sort options of the results grid (PRODUCT-SPEC §3.10.6). Server-side;
// the drawer's J/K follow the same order. Param values:
//   order · regressed · delta:<metric>:<run>:asc|desc ·
//   score:<metric>:<run>:asc|desc · latency:<run> · cost:<run> ·
//   variance:<metric>:<run> · flakiness:<run>
// R3 (SVP-4.7): `regressed` = rows any run got worse on first, then rows
// that got better, then the rest (row order inside each); the default with
// a baseline and two or more runs.

import { resultsSortOffered } from "src/api/develop/results-shows";

export const REGRESSED_FIRST = "regressed";

/** The default sort of a view: Regressed first when there is a baseline to move against. */
export const defaultSortFor = ({ runCount, baselineId }) =>
  runCount > 1 && baselineId && baselineId !== "none"
    ? REGRESSED_FIRST
    : "order";

export const SORT_ENTRIES = [
  { id: "order", label: "Row order", fields: [] },
  {
    id: REGRESSED_FIRST,
    // iterate r2 G3 (P8): one regression word on the page.
    label: "Worse than baseline first",
    fields: [],
    compare: true,
  },
  {
    id: "regression",
    label: "Biggest regression first",
    fields: ["metric", "run"],
    compare: true,
  },
  {
    id: "improvement",
    label: "Biggest improvement first",
    fields: ["metric", "run"],
    compare: true,
  },
  {
    id: "score_asc",
    label: "Score ↑ (lowest first)",
    fields: ["metric", "run"],
  },
  {
    id: "score_desc",
    label: "Score ↓ (highest first)",
    fields: ["metric", "run"],
  },
  { id: "latency", label: "Slowest", fields: ["run"] },
  { id: "cost", label: "Most expensive", fields: ["run"] },
  // A metric's values across a run's tries: the most spread first.
  {
    id: "variance",
    label: "Most variable across trials",
    fields: ["metric", "run"],
    trials: true,
  },
  // W1-8: rows whose tries split most evenly first (1 − |2p − 1|).
  {
    id: "flakiness",
    label: "Most flaky (tries split evenly first)",
    fields: ["run"],
    trials: true,
  },
];

/** Sort string from an entry and its metric / run. */
export function sortString(entryId, { metric, run } = {}) {
  switch (entryId) {
    case REGRESSED_FIRST:
      return REGRESSED_FIRST;
    case "regression":
      return `delta:${metric}:${run}:asc`;
    case "improvement":
      return `delta:${metric}:${run}:desc`;
    case "score_asc":
      return `score:${metric}:${run}:asc`;
    case "score_desc":
      return `score:${metric}:${run}:desc`;
    case "variance":
      return `variance:${metric}:${run}`;
    case "latency":
    case "cost":
    case "flakiness":
      return `${entryId}:${run}`;
    default:
      return "order";
  }
}

/**
 * Split a sort string from both ends: metric keys contain a colon
 * (`eval:correctness`), run ids do not. Returns {kind, metric, run, dir}.
 */
export function splitSort(sort) {
  const parts = String(sort ?? "").split(":");
  const kind = parts[0];
  if (["latency", "cost", "flakiness"].includes(kind))
    return { kind, run: parts.slice(1).join(":") || null };
  if (kind === "variance")
    return {
      kind,
      run: parts.length > 1 ? parts.at(-1) || null : null,
      metric: parts.slice(1, -1).join(":") || null,
    };
  if (kind === "delta" || kind === "score") {
    const dir = ["asc", "desc"].includes(parts.at(-1)) ? parts.at(-1) : "asc";
    const rest = ["asc", "desc"].includes(parts.at(-1))
      ? parts.slice(1, -1)
      : parts.slice(1);
    return {
      kind,
      run: rest.at(-1) ?? null,
      metric: rest.slice(0, -1).join(":") || null,
      dir,
    };
  }
  return { kind: "order" };
}

/**
 * Whether `sort` names only runs this view holds: an order by one run's
 * values names one of `runIds`, and an order by a run's difference also
 * needs a baseline other than that run. An order by how far apart a run's
 * tries are also names a metric (`variance::<run>` names none). Every other
 * order fits any view.
 */
export function sortFitsView(sort, { runIds = [], baselineId } = {}) {
  const { kind, metric, run } = splitSort(sort);
  if (kind === "order") return true;
  if (!run || !runIds.includes(run)) return false;
  if (kind === "variance") return Boolean(metric);
  if (kind !== "delta") return true;
  return Boolean(baselineId) && baselineId !== "none" && baselineId !== run;
}

/**
 * The order a view reads from its link's `sort`, for its read, its Sort
 * button and its header alike. No `sort` reads as the view's default. An
 * order this build's read does not answer is not sent, so the read serves
 * Row order and the view reads Row order. An order that names a run this
 * view does not hold (the run was removed, or the baseline moved onto it)
 * reads as the view's default. The link keeps its `sort`, so it applies
 * again once its run is back in view, as while a pinned baseline loads.
 */
export function sortForView(sort, { runIds = [], baselineId } = {}) {
  const fallback = defaultSortFor({ runCount: runIds.length, baselineId });
  if (sort == null) return fallback;
  if (!resultsSortOffered(sort)) return "order";
  return sortFitsView(sort, { runIds, baselineId }) ? sort : fallback;
}

/** `{entryId, metric, run}` from a sort string (unknown -> row order). */
export function parseSort(sort) {
  if (!sort || sort === "order") return { entryId: "order" };
  if (sort === REGRESSED_FIRST) return { entryId: REGRESSED_FIRST };
  const { kind, metric, run, dir } = splitSort(sort);
  if (kind === "delta")
    return {
      entryId: dir === "desc" ? "improvement" : "regression",
      metric,
      run,
    };
  if (kind === "score")
    return {
      entryId: dir === "desc" ? "score_desc" : "score_asc",
      metric,
      run,
    };
  if (kind === "variance") return { entryId: kind, metric, run };
  if (["latency", "cost", "flakiness"].includes(kind))
    return { entryId: kind, run };
  return { entryId: "order" };
}

/** Button text: `Sort: Biggest regression · Task completion · #4A`. */
export function sortLabel(
  sort,
  { metricsByKey = {}, runsById = {}, short = false } = {},
) {
  const p = parseSort(sort);
  if (p.entryId === "order") return "Sort";
  // `short`: the narrow toolbar's form (the full label is the hover).
  if (p.entryId === REGRESSED_FIRST)
    return short ? "Sort: worse first" : "Sort: worse than baseline first";
  const entry = SORT_ENTRIES.find((e) => e.id === p.entryId);
  const parts = [entry?.label.replace(/ \(.*\)$/, "").replace(/ first$/, "")];
  if (p.metric) parts.push(metricsByKey[p.metric]?.name ?? p.metric);
  if (p.run) parts.push(runsById[p.run]?.label ?? "run");
  return `Sort: ${parts.join(" · ")}`;
}
