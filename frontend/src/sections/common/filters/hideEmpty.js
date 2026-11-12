// HO results R2 (W5 integration A-H5): `Hide rows with no output` as a switch.
// It is the expression pill the filter dialog's quick pick adds (one rule,
// one language): rows where no run in view has an output are left out.
// The results endpoint has no filter for it (it refuses an expression with
// a 400), so the pill never goes to the server: the results reads leave
// those rows out of the rows they load (rowsWithOutput).
import { expressionPill, isExpressionPill } from "./expressionPill";
import { isEmptyValue } from "./expressionValues";

export const HIDE_EMPTY_EXPR = "not (all.output is empty)";

const norm = (expr) =>
  String(expr ?? "")
    .replace(/\s+/g, " ")
    .trim();

/** True for the switch's own pill (whitespace-insensitive). */
export const isHideEmptyPill = (pill) =>
  isExpressionPill(pill) && norm(pill.expr) === HIDE_EMPTY_EXPR;

/** True when the page's pills hide rows with no output. */
export const hidesEmpty = (pills) => (pills ?? []).some(isHideEmptyPill);

/** The pills with the switch turned `on` (other pills keep their order). */
export function withHideEmpty(pills, on) {
  const rest = (pills ?? []).filter((p) => !isHideEmptyPill(p));
  return on ? [...rest, expressionPill(HIDE_EMPTY_EXPR)] : rest;
}

/**
 * True when a results row (`results{runId: {output}}`) has an output from
 * at least one run in view: the pill's `not (all.output is empty)`, where
 * empty is missing, "", [] or {}. Pure.
 * @param {object} row
 * @param {string[]} [runIds] the runs in view (default: the row's runs)
 */
export function rowHasOutput(row, runIds) {
  const cells = row?.results ?? {};
  const ids = runIds?.length ? runIds : Object.keys(cells);
  return ids.some((id) => cells[id] != null && !isEmptyValue(cells[id].output));
}

/**
 * A results answer without the rows where no run in view has an output;
 * `total` counts the rows that stay. Pure.
 * @param {{rows?: object[], total?: number}} result
 * @param {string[]} [runIds]
 */
export function rowsWithOutput(result, runIds) {
  if (!Array.isArray(result?.rows)) return result;
  const rows = result.rows.filter((row) => rowHasOutput(row, runIds));
  return { ...result, rows, total: rows.length };
}

/**
 * The switch's line: `Hides 3 rows` once counted (the rows the pill leaves
 * out of the view without it), `No row is empty` when none would go.
 * @param {{without: number|null, with: number|null}} counts
 */
export function hideEmptyHint(counts) {
  const a = counts?.without;
  const b = counts?.with;
  if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
  const n = Math.max(0, a - b);
  if (n === 0) return "Every row in view has an output";
  return `Hides ${n.toLocaleString("en-US")} ${n === 1 ? "row" : "rows"} where no run has an output`;
}

/**
 * The filter types the R3 results endpoint accepts (dataset_results.py
 * PILL_FIELDS); it refuses an `error` pill that names a `cause`. The switch's
 * expression is not one of them.
 */
export const SERVER_FILTER_TYPES = Object.freeze([
  "rows",
  "meta",
  "split",
  "error",
  "flaky",
  "score",
  "fails",
  "latency",
  "cost",
]);

/** True when the results endpoint accepts every pill. Pure. */
export const serverAcceptsPills = (pills) =>
  (pills ?? []).every(
    (p) =>
      SERVER_FILTER_TYPES.includes(p?.type) &&
      !(p.type === "error" && p.cause != null && p.cause !== ""),
  );

/**
 * True when the switch's `Hides N rows` count can be read: the prototype's
 * mock evaluates every pill; a real server only its own filter types, so it
 * is never asked for the count (the switch still works without it).
 * @param {object[]} pills the view's pills without the switch's own
 * @param {{prototype?: boolean}} opts
 */
export const canCountHideEmpty = (pills, { prototype = false } = {}) =>
  prototype || serverAcceptsPills(withHideEmpty(pills, true));

/** The words of the switch and of its View menu line. */
export const HIDE_EMPTY_LABEL = "Hide rows with no output";

/**
 * The results View menu's `Show on this page` line (HO R2): the same pill
 * as the toolbar switch, for a toolbar row too full to hold the switch.
 * @param {object[]} pills the page's pills
 * @param {(pills: object[]) => void} setPills
 */
export const hideEmptyViewExtra = (pills, setPills) => ({
  id: "hide-empty",
  label: HIDE_EMPTY_LABEL,
  checked: hidesEmpty(pills),
  onChange: (v) => setPills(withHideEmpty(pills, Boolean(v))),
  description: "Leaves out the rows where no run in view has an output",
});
