// HO results R2 (W5 integration A-H5): `Hide rows with no output` as a switch.
// It is the expression pill the filter dialog's quick pick adds (one rule,
// one language): rows where no run in view has an output are left out.
import { expressionPill, isExpressionPill } from "./expressionPill";

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
