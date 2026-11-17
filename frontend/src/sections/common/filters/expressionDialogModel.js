// W5-1 A2 · the Filter by expression dialog's pure parts (W5-SCOPE §5 A2):
// the placeholder, the live-count debounce and the count line.
import { fmtCount } from "src/sections/common/format/fmt";

// L7c g1 (expr:results): a neutral default; each door passes its own first
// example (gridPlaceholder, resultsPlaceholder), never a fixed expression
// naming runs or evals a page may not have.
export const PLACEHOLDER = "Type an expression";
export const COUNT_DEBOUNCE_MS = 300;

/** `Matches 14 of 248 rows` / `Matches no rows`. */
export function countLine({ matched, total }) {
  if (!Number.isFinite(matched)) return "";
  if (matched === 0) return "Matches no rows";
  return `Matches ${fmtCount(matched)} of ${fmtCount(total)} row${total === 1 ? "" : "s"}`;
}

/**
 * A quick pick added to what is typed (L7c g1): both sides are bracketed, so
 * an `a or b` pick ANDs as a whole (`(typed) and (a or b)`), never as
 * `(typed and a) or b`.
 */
export function withQuickPick(typed, pick) {
  const t = String(typed ?? "").trim();
  const q = String(pick ?? "").trim();
  if (!t) return q;
  if (!q) return t;
  return `(${t}) and (${q})`;
}
