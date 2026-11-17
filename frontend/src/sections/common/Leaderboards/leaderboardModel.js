// W5-5 (AG-07; W5-SCOPE §7 C2): leaderboards across datasets. A definition
// is {id, name, dataset_ids[], run_filter, columns: [{key, label}], sort:
// {column, dir}}; its rows are every finished run on those datasets whose
// runs scope matches `run_filter` (the one expression language, runs
// catalogue), computed at read time, so a new matching run appears without
// editing the board. Pure: the mock world and the page share these rules.
import { getRunStatus } from "src/sections/common/Runs/runStatus";
import { aggValueOf } from "src/sections/develop-detail/ExperimentTab/leaderboardModel";

/** Statuses whose numbers no longer move (a run that finished its rows). */
const FINISHED = new Set(["Completed", "PartialCompleted"]);

export const isFinishedRun = (run) => FINISHED.has(getRunStatus(run).key);

/**
 * Metric kinds a leaderboard column can show (R1 `metric_defs[].kind`):
 * evals, and the system numbers every run has (latency, cost, tokens).
 */
const COLUMN_KINDS = new Set(["eval", "system"]);

/**
 * Column options for a board over several datasets: the union of their R1
 * `metric_defs` (evals and latency / cost / tokens), first-seen order, each
 * with the number of chosen datasets that carry it.
 * @param {Array<Array<object>>} defsByDataset one `metric_defs` per dataset
 */
export function columnOptions(defsByDataset) {
  const byKey = new Map();
  (defsByDataset ?? []).forEach((defs) =>
    (defs ?? []).forEach((d) => {
      if (!d?.key || !COLUMN_KINDS.has(d.kind)) return;
      const cur = byKey.get(d.key);
      if (cur) cur.datasets += 1;
      else byKey.set(d.key, { ...d, label: d.name ?? d.key, datasets: 1 });
    }),
  );
  return [...byKey.values()];
}

/** The direction a column ranks in: best first. */
export const bestFirstDir = (def) =>
  def?.higher_is_better === false ? "asc" : "desc";

/** A sort that is valid for these columns (the first column by default). */
export function normalizeSort(sort, columns, defsByKey = {}) {
  const keys = (columns ?? []).map((c) => c.key);
  const column = keys.includes(sort?.column) ? sort.column : keys[0] ?? null;
  const dir =
    sort?.dir === "asc" || sort?.dir === "desc"
      ? sort.dir
      : bestFirstDir(defsByKey[column]);
  return { column, dir };
}

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/** The value a run shows in a column: the leaderboard's own aggregate rule. */
export const runColumnValue = (run, def) => (def ? aggValueOf(run, def) : null);

/**
 * Rank rows by the sort column. Rows without a value there are left out of
 * the ranking (counted, never hidden silently). Ties keep the label order.
 * @returns {{rows: object[], noValue: object[]}}
 */
export function rankRows(rows, sort) {
  const col = sort?.column;
  const withValue = [];
  const noValue = [];
  (rows ?? []).forEach((r) =>
    (isNum(r.values?.[col]) ? withValue : noValue).push(r),
  );
  const sign = sort?.dir === "asc" ? 1 : -1;
  withValue.sort(
    (a, b) =>
      sign * (a.values[col] - b.values[col]) ||
      String(a.dataset ?? "").localeCompare(String(b.dataset ?? "")) ||
      String(a.label ?? "").localeCompare(String(b.label ?? ""), undefined, {
        numeric: true,
      }),
  );
  return {
    rows: withValue.map((r, i) => ({ ...r, rank: i + 1 })),
    noValue,
  };
}

/**
 * Problems with a definition body, field by field (empty when valid).
 * `known` = {datasetIds: Set, columnKeys: Set} from the world or the page.
 */
export function definitionErrors(body, known = {}) {
  const errors = {};
  const name = String(body?.name ?? "").trim();
  if (!name) errors.name = "Give the leaderboard a name.";
  const ids = Array.isArray(body?.dataset_ids) ? body.dataset_ids : [];
  if (!ids.length) errors.dataset_ids = "Pick at least one dataset.";
  else if (known.datasetIds) {
    const missing = ids.filter((id) => !known.datasetIds.has(id));
    if (missing.length)
      errors.dataset_ids = `Unknown dataset: ${missing.join(", ")}`;
  }
  const cols = Array.isArray(body?.columns) ? body.columns : [];
  if (!cols.length) errors.columns = "Pick at least one column.";
  else if (known.columnKeys) {
    const missing = cols
      .map((c) => c?.key)
      .filter((k) => !known.columnKeys.has(k));
    if (missing.length)
      errors.columns = `These datasets have no ${missing.join(", ")}.`;
  }
  if (
    body?.sort?.column &&
    cols.length &&
    !cols.some((c) => c?.key === body.sort.column)
  )
    errors.sort = "Sort by one of the columns.";
  return errors;
}

/**
 * L7c g1 (wlb:editor): the `Runs where` placeholder: a rule over a tag this
 * workspace has (`tags contains "baseline"`), else one every run can read.
 */
export function rulePlaceholder(tags) {
  const name = (tags ?? [])
    .map((t) => t?.name)
    .find((n) => n && /^[\w .-]+$/.test(n));
  return name ? `tags contains ${JSON.stringify(name)}` : "cost_usd < 1";
}

/** The page link (boards live in this browser in the prototype). */
export const leaderboardHref = (id) => `/dashboard/develop/leaderboards/${id}`;

/** "12 runs · 2 datasets" for a list row. */
export function boardCountsText({ rows, datasets }) {
  const r = `${rows} run${rows === 1 ? "" : "s"}`;
  const d = `${datasets} dataset${datasets === 1 ? "" : "s"}`;
  return `${r} · ${d}`;
}

/**
 * "1 matching run with no Tone value, listed unranked · 2 not finished yet,
 * left out" (null when nothing is set aside).
 */
export function leftOutText(leftOut, sortLabel) {
  const parts = [];
  const nv = leftOut?.no_value ?? 0;
  const nf = leftOut?.not_finished ?? 0;
  if (nv)
    parts.push(
      `${nv} matching run${nv === 1 ? "" : "s"} with no ${sortLabel ?? "value"} value, listed unranked`,
    );
  if (nf) parts.push(`${nf} not finished yet, left out`);
  return parts.length ? parts.join(" · ") : null;
}
