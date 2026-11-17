// Δ vs the baseline per group × run (W5-2 B1). Pure. No new statistic:
// - rates (pass@1, a pass/fail eval, a score eval read as pass/fail) go
//   through the Summary's paired comparison `runNumbers.compareRuns` on the
//   group's rows (pts, its 95% range, `significant` when it excludes 0);
// - means (scores, cost, latency, steps…) take the paired mean difference
//   of results-math `compareRuns` (workbenchAggs.pairedMeanDelta).
// Counts (tool errors) and the Failed-eval pivot have no Δ.
import { compareRuns } from "src/sections/common/Runs/runNumbers";
import { attemptsOf } from "./workbenchModel";
import { meanOf, pairedMeanDelta } from "./workbenchAggs";

/** The matrix with each cell's trials replaced by the measure's pass / fail. */
function passMatrix(matrix, runIds, measure) {
  if (measure.key === "pass_at_1") return matrix;
  return {
    ...matrix,
    rows: (matrix?.rows ?? []).map((row) => {
      const cells = {};
      runIds.forEach((id) => {
        const cell = row.cells?.[id];
        if (!cell) return;
        cells[id] = {
          trials: attemptsOf(cell, measure).map((a) =>
            a && a.pass !== undefined ? a.pass : null,
          ),
        };
      });
      return { ...row, cells };
    }),
  };
}

/** Row id → the row's mean value of the measure on one run. */
export function rowMeans(matrix, runId, measure, rowIds) {
  const keep = rowIds ? new Set(rowIds) : null;
  const out = new Map();
  (matrix?.rows ?? []).forEach((row) => {
    if (keep && !keep.has(row.row_id)) return;
    const cell = row.cells?.[runId];
    if (!cell) return;
    const xs = attemptsOf(cell, measure)
      .filter((a) => a && typeof a.x === "number" && Number.isFinite(a.x))
      .map((a) => a.x);
    if (xs.length) out.set(row.row_id, meanOf(xs));
  });
  return out;
}

/** True when Δ for this measure is a pass-rate difference in pts. */
export const deltaIsRate = (measure) =>
  measure?.kind === "rate" && measure?.key !== "pass_hat_k";

/**
 * Δ of one candidate run vs the baseline on some rows.
 * @returns {null | {kind: "rate", delta: number, low: number, high: number,
 *   significant: boolean, n: number} | {kind: "mean", delta, low, high,
 *   significant, n}}  rate deltas and bounds are in pts
 */
export function deltaOn(matrix, { baselineId, candidateId, measure, rowIds }) {
  if (!matrix || !baselineId || !candidateId || !measure) return null;
  if (measure.kind === "count" || measure.key === "pass_hat_k") return null;
  if (deltaIsRate(measure)) {
    const cmp = compareRuns({
      matrix: passMatrix(matrix, [baselineId, candidateId], measure),
      baselineId,
      candidateId,
      scope: rowIds,
    });
    if (cmp.deltaPts == null) return null;
    return {
      kind: "rate",
      delta: cmp.deltaPts,
      low: cmp.range?.lo ?? null,
      high: cmp.range?.hi ?? null,
      significant: cmp.significant,
      n: cmp.better + cmp.worse + cmp.same,
    };
  }
  const d = pairedMeanDelta(
    rowMeans(matrix, candidateId, measure, rowIds),
    rowMeans(matrix, baselineId, measure, rowIds),
  );
  if (d.delta == null) return null;
  return { kind: "mean", ...d };
}

/**
 * Δ per group × single-run column of a pivot vs the baseline column.
 * @returns {Object<string, Object<string, object>>} group → column key → Δ
 */
export function groupDeltas(matrix, pivot, { baselineId, measure }) {
  const out = {};
  if (!pivot || pivot.failedEval || !baselineId) return out;
  pivot.rowKeys.forEach((g) => {
    const rowIds = pivot.rowTotals[g]?.rowIds ?? [];
    out[g] = {};
    pivot.columns.forEach((col) => {
      if (col.runIds.length !== 1 || col.runIds[0] === baselineId) return;
      const d = deltaOn(matrix, {
        baselineId,
        candidateId: col.runIds[0],
        measure,
        rowIds,
      });
      if (d) out[g][col.key] = d;
    });
  });
  return out;
}
