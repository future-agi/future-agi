// W1-4 (EV-04, EV-20): what a score cell's value may be, and how N/A and
// error cells are counted. Pure: the mock's one cell builder
// (scoreCell.scoreCellOf) checks every computed or uploaded score with
// `checkScore`, and the results header, the hover card and R1 / R3 read the
// counts with `statusCountsOf` / `naCountsText`. A judge's N/A and an
// invalid score are never coerced into a number: an N/A cell and an error
// cell both hold `value: null`, so every mean and pass rate leaves them out
// and the counts say how many were left out.

const PASS_WORDS = new Map([
  ["pass", 1],
  ["passed", 1],
  ["true", 1],
  ["fail", 0],
  ["failed", 0],
  ["false", 0],
]);
const NA_WORDS = new Set(["n/a", "na", "not applicable"]);

/**
 * W1-4: the type a score is checked by, from an eval's output type as the
 * template stores it: the editor's `percentage` is a 0-1 score and its
 * `deterministic` a choice among labels.
 */
export const scoreTypeOf = (outputType) =>
  ({
    pass_fail: "pass_fail",
    "Pass/Fail": "pass_fail",
    percentage: "score",
    score: "score",
    deterministic: "choices",
    choices: "choices",
    stars: "stars",
  })[outputType] ?? "score";

/** The value range of a numeric output type, or null (pass/fail, choices). */
export function rangeOf(outputType) {
  if (outputType === "stars") return { min: 1, max: 5 };
  if (outputType === "pass_fail" || outputType === "choices") return null;
  return { min: 0, max: 1 };
}

const shown = (v) => (typeof v === "string" ? JSON.stringify(v) : String(v));

/** Whether an uploaded or judged value says "not applicable". */
export const isNaValue = (v) =>
  (typeof v === "string" && NA_WORDS.has(v.trim().toLowerCase())) ||
  (v && typeof v === "object" && v.na === true);

/**
 * Check one score against its evaluator's output type.
 * @param {*} value  the score as computed or uploaded
 * @param {{outputType?: string, choices?: string[]|Object<string, number>}} [type]
 *   `choices` lists the labels (or maps each label to its score)
 * @returns {{ok: true, value: number|null, label?: string} |
 *   {ok: false, reason: string}}
 *   `reason` reads `score error: 1.4 > 1`, `score error: -0.2 < 0`,
 *   `score error: "maybe" is not a choice`, …
 */
export function checkScore(value, { outputType = "score", choices } = {}) {
  if (outputType === "pass_fail") {
    if (value === true || value === 1) return { ok: true, value: 1 };
    if (value === false || value === 0) return { ok: true, value: 0 };
    const word =
      typeof value === "string"
        ? PASS_WORDS.get(value.trim().toLowerCase())
        : undefined;
    if (word != null) return { ok: true, value: word };
    // A number out of 0-1 says which bound it crossed.
    if (typeof value === "number" && Number.isFinite(value)) {
      if (value > 1) return { ok: false, reason: `score error: ${value} > 1` };
      if (value < 0) return { ok: false, reason: `score error: ${value} < 0` };
    }
    return {
      ok: false,
      reason: `score error: ${shown(value)} is not pass or fail`,
    };
  }
  if (outputType === "choices") {
    const labels = Array.isArray(choices)
      ? choices
      : Object.keys(choices ?? {});
    const label = typeof value === "string" ? value.trim() : value;
    if (!labels.includes(label))
      return {
        ok: false,
        reason: `score error: ${shown(value)} is not a choice`,
      };
    // A label's own score when the eval maps one; a bare label has none.
    const scored = Array.isArray(choices) ? NaN : Number(choices[label]);
    return { ok: true, value: Number.isFinite(scored) ? scored : null, label };
  }
  const n =
    typeof value === "number"
      ? value
      : typeof value === "string" && value.trim() !== ""
        ? Number(value)
        : NaN;
  if (!Number.isFinite(n))
    return {
      ok: false,
      reason: `score error: ${shown(value)} is not a number`,
    };
  const { min, max } = rangeOf(outputType);
  if (n > max) return { ok: false, reason: `score error: ${n} > ${max}` };
  if (n < min) return { ok: false, reason: `score error: ${n} < ${min}` };
  return { ok: true, value: n };
}

/**
 * Count one eval's cells over a set of rows: `n` rows with a score, `n_na`
 * rows the judge answered N/A on, `n_err` rows whose score is an error
 * (an invalid score, or an errored try left out of scores). A row counts
 * as N/A or as an error only when none of its tries has a score.
 * @param {Array<Array<object>>} rowsOfCells  per row, its tries' cells
 */
export function statusCountsOf(rowsOfCells = []) {
  let n = 0;
  let nNa = 0;
  let nErr = 0;
  rowsOfCells.forEach((cells) => {
    const list = (cells ?? []).filter(Boolean);
    if (!list.length) return;
    if (list.some((c) => c.value != null && c.status !== "na")) n += 1;
    else if (list.some((c) => c.status === "na")) nNa += 1;
    else if (list.some((c) => c.status === "error")) nErr += 1;
  });
  return { n, n_na: nNa, n_err: nErr, na_rate: naRateOf(n, nNa) };
}

/** The share of answered rows that were N/A: n_na / (n_na + n), or null. */
export const naRateOf = (n, nNa) =>
  nNa > 0 ? Number((nNa / (nNa + (n ?? 0))).toFixed(4)) : nNa === 0 ? 0 : null;

/**
 * "9 N/A (45%) · 1 err", the part of a header or hover line that says
 * which rows a mean left out; "" when none were. With `withN`, it opens
 * with the scored rows: "n=11 · 9 N/A (45%) · 1 err".
 */
export function naCountsText(
  { n, n_na: nNa = 0, n_err: nErr = 0 } = {},
  { withN = false } = {},
) {
  const parts = [];
  if (nNa > 0)
    parts.push(`${nNa} N/A (${Math.round((naRateOf(n, nNa) ?? 0) * 100)}%)`);
  if (nErr > 0) parts.push(`${nErr} err`);
  if (withN && Number.isFinite(n)) parts.unshift(`n=${n}`);
  return parts.join(" · ");
}

/**
 * W1-4: what a header's value left out: `9 N/A (45%) · 1 err` beside it,
 * and `n=11 · 9 N/A (45%) · 1 err` in its tooltip; null when nothing.
 */
export function naNoteOf(counts) {
  const label = naCountsText(counts ?? {});
  if (!label) return null;
  return {
    label,
    tooltip: `${naCountsText(counts, { withN: true })}: N/A rows and score errors are left out of the value and the pass rate`,
  };
}
