// W1-9 B6 · the one Failed rule of a fix run. Pure.
//
// A row fails a run when any of its tries failed or errored (pass^k: a row
// passes only when every try passes; an errored try is a failed try, as the
// Matrix counts it). Every count of failing rows reads this rule:
//   - the dock's `Failed (13)` chip and the rows `Test a fix` reruns
//     (iterateModel.scopeSets),
//   - the banner's `Fixed 6 of 13 failing rows · 7 still fail`
//     (iterateSummary.summarizeIteration → fixVerdict),
//   - failedRowsOf (fixVerdict.js), the per-run lists both equal,
//   - the results grid's `Fails <eval>` pill and the Analysis failing rows
//     (results-math `failsRow` = `failsCell` below, serial merge M2 / M5)
//     for a per-try pass / fail eval, so a 2/3 row fails there too. A score
//     eval keeps its row mean against the pass threshold (scores are
//     averaged per example),
//   - the expression language's `<eval> = fail` (W5 resultsScope, M7).

const trialsOf = (cell) =>
  Array.isArray(cell) ? cell : Array.isArray(cell?.trials) ? cell.trials : [];

/** True when a matrix cell (or a trials array) has a failed or errored try. */
export const failsSome = (cell) =>
  trialsOf(cell).some((t) => t === 0 || t === "error");

// The per-try pass / fail output types (one verdict per try).
const PER_TRY_TYPES = new Set(["pass_fail", "thumbs"]);
const FLOAT_EPS = 1e-9;

/** True for an eval whose rows fail by their tries (pass / fail, thumbs). */
export const failsByTries = (def) => PER_TRY_TYPES.has(def?.output_type);

/**
 * Does a results row fail `metric` on one run? `cell` = an R3 row's
 * `runs[runId]`. A per-try pass / fail eval fails the row when any of its
 * tries failed or errored (`failsSome`, pass^k): it reads the try verdicts
 * (`trial_verdicts`, "error" for an errored try), else the scored tries
 * (`trial_values`). A score eval keeps "the row's trial mean below the pass
 * threshold". Serial merge M5 made this the one implementation (results-math
 * `failsRow` is this function); M7 moved it here so the expression language
 * (common/filters/resultsScope, a pure FE module) reads the same rule.
 */
export function failsCell(cell, metric, def) {
  if (!cell) return false;
  if (failsByTries(def)) {
    const verdicts = cell.trial_verdicts?.[metric];
    if (Array.isArray(verdicts) && verdicts.length) return failsSome(verdicts);
    const tries = cell.trial_values?.[metric];
    if (tries?.length)
      return failsSome(tries.map((v) => (v >= 0.5 - FLOAT_EPS ? 1 : 0)));
  }
  const v = cell.values?.[metric];
  return (
    typeof v === "number" &&
    Number.isFinite(v) &&
    v < (def?.pass_threshold ?? 0.5)
  );
}

/**
 * Row ids that fail `runId` (matrix order).
 * @param {{rows?: object[]}} matrix  R3 shape=matrix
 * @param {string} runId
 * @param {{only?: (row: object) => boolean}} [opts]  `only` = which rows
 *   count (e.g. rows both runs ran, or the rows a child reran)
 * @returns {string[]}
 */
export function failedRowIds(matrix, runId, { only = null } = {}) {
  return (matrix?.rows ?? [])
    .filter((r) => r.cells?.[runId] && (!only || only(r)))
    .filter((r) => failsSome(r.cells[runId]))
    .map((r) => r.row_id);
}
