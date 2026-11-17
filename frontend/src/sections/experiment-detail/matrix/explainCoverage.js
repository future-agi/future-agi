// IT-R4 G6 P6: what the Explain popover covers, in words —
//   `Explains 6 of the 10 tasks that got better: the order id loses its A-
//   prefix. The other 4 got better by fewer failed tries.`
// and who fails a cause's check: `#2B fails all_requests_handled; #2D
// passes it`. Pure.
import { fmtCount } from "src/sections/common/format/fmt";

const lowerFirst = (s) => (s ? `${s[0].toLowerCase()}${s.slice(1)}` : s);

/**
 * @param {{compare: object, patternRows: string[], cause?: string|null,
 *   noun?: string, baseRateOf?: (rowId) => number|null}} a
 *   compare = runNumbers.compareRuns(baseline, candidate); patternRows =
 *   the rows the pattern explains; baseRateOf = the baseline's pass rate on
 *   a row (0..1)
 * @returns {string|null}
 */
export function explainCoverage({
  compare,
  patternRows,
  cause = null,
  noun = "task",
  baseRateOf,
}) {
  const better = (compare?.rows ?? []).filter((r) => r.change === "better");
  if (!better.length || !patternRows?.length) return null;
  const inPattern = new Set(patternRows);
  const explained = better.filter((r) => inPattern.has(r.id));
  if (!explained.length) return null;
  const rest = better.filter((r) => !inPattern.has(r.id));
  const what = cause
    ? `: ${lowerFirst(String(cause).replace(/\.$/, ""))}.`
    : ".";
  const head =
    explained.length === better.length
      ? `Explains all ${fmtCount(better.length, noun)} that got better${what}`
      : `Explains ${explained.length} of the ${fmtCount(better.length, noun)} that got better${what}`;
  if (!rest.length) return head;
  const partly =
    typeof baseRateOf === "function" &&
    rest.every((r) => (baseRateOf(r.id) ?? 0) > 0);
  return `${head} The other ${rest.length} got better ${
    partly ? "by fewer failed tries" : "for reasons this pattern does not cover"
  }.`;
}

/**
 * `#2B fails all_requests_handled; #2D passes it` from a root cause
 * (`run_id` fails `failed_eval_name`, `baseline_run_id` passes), else the
 * cause's own title.
 */
export function whoFailsText(cause, labelOf) {
  const evalName = cause?.failed_eval_name;
  const a = cause?.run_id ? labelOf(cause.run_id) : null;
  const b = cause?.baseline_run_id ? labelOf(cause.baseline_run_id) : null;
  if (evalName && a && b) return `${a} fails ${evalName}; ${b} passes it`;
  return cause?.title ?? "";
}
