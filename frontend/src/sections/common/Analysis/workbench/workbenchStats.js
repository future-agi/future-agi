// Plain sentences for the Workbench and the Overview additions (R2-SPEC
// §6.3.2, §6.3.4, addendum F2). Pure; the formulas are named in each
// card's `How is this computed?`.
import { biggestSpread, weakestByColumn } from "./workbenchPivot";
import { fmtValue } from "./workbenchFormat";

const Z_A = 1.96;
const Z_B = 0.84;

const twoSig = (x) => {
  if (!Number.isFinite(x) || x <= 0) return null;
  const p = 10 ** (Math.floor(Math.log10(x)) - 1);
  return Math.round(x / p) * p;
};

/**
 * Rows a paired binary comparison needs to settle a change of `delta`
 * (McNemar, α 0.05 two-sided, 80% power):
 * n = (1.96·√p_d + 0.84·√(p_d − d²))² / d², p_d = (improved + regressed)/n.
 * Rounded to 2 significant figures (`About 1,100 rows`).
 */
export function rowsToSettle({ improved, regressed, n, delta }) {
  const d = Math.abs(Number(delta) || 0);
  if (!n || !d) return null;
  const pd = ((improved ?? 0) + (regressed ?? 0)) / n;
  if (pd <= d * d) return null;
  const need =
    (Z_A * Math.sqrt(pd) + Z_B * Math.sqrt(pd - d * d)) ** 2 / (d * d);
  return twoSig(need);
}

/** Smallest change this comparison can detect (80% power), in points. */
export function detectablePts({ improved, regressed, n, delta = 0 }) {
  if (!n) return null;
  const pd = ((improved ?? 0) + (regressed ?? 0)) / n;
  const se = Math.sqrt(Math.max(0, pd - delta * delta)) / Math.sqrt(n);
  return Math.round((Z_A + Z_B) * se * 1000) / 10;
}

/**
 * `Biggest spread: multi_request (20% to 93%). #2B's weakest group is
 * refund (39%).` (generated, not AI). Null when it would say nothing.
 */
export function spreadSentence(pivot, measure, { baseCol, baseLabel } = {}) {
  if (!pivot || pivot.failedEval || pivot.rowKeys.length < 2) return null;
  const parts = [];
  const spread = biggestSpread(pivot);
  if (spread && spread.hi > spread.lo)
    parts.push(
      `Biggest spread: ${spread.group} (${fmtValue(measure, spread.lo)} to ${fmtValue(measure, spread.hi)}).`,
    );
  const weak = weakestByColumn(pivot);
  const col = baseCol ?? pivot.columns[0]?.key;
  const label = baseLabel ?? pivot.columns.find((c) => c.key === col)?.label;
  if (col && weak[col])
    parts.push(
      `${label}'s weakest group is ${weak[col]} (${fmtValue(measure, pivot.cells[weak[col]][col].value)}).`,
    );
  return parts.length ? parts.join(" ") : null;
}

/**
 * F1: failed attempts by their first failed Check, over the runs in view.
 * @param {object} matrix  R3 shape=matrix (pass@1)
 * @param {{isCheck: (key: string) => boolean, labels: Object<string, string>}} opts
 * @returns {Array<{key, attempts, rowIds, byRun: Object<string, number>}>}
 */
export function failedEvalSummary(matrix, { isCheck, labels = {} } = {}) {
  const acc = new Map();
  (matrix?.rows ?? []).forEach((row) =>
    Object.entries(row.cells ?? {}).forEach(([runId, cell]) =>
      (cell.trials ?? []).forEach((t, i) => {
        if (t !== 0) return; // errored attempts are not a Check failure
        const key = (cell.failed_evals?.[i] ?? []).find(
          (e) => isCheck?.(e) ?? true,
        );
        if (!key) return;
        if (!acc.has(key))
          acc.set(key, { key, attempts: 0, rowIds: new Set(), byRun: {} });
        const a = acc.get(key);
        a.attempts += 1;
        a.rowIds.add(row.row_id);
        const l = labels[runId] ?? runId;
        a.byRun[l] = (a.byRun[l] ?? 0) + 1;
      }),
    ),
  );
  return [...acc.values()]
    .map((a) => ({ ...a, rowIds: [...a.rowIds] }))
    .sort((a, b) => b.attempts - a.attempts || a.key.localeCompare(b.key));
}

/** `(31 attempts)` for the first item, then `(18, 18 on #2B)` / `(12)`. */
export function failedEvalNote(item, first) {
  const runs = Object.entries(item.byRun).sort((a, b) => b[1] - a[1]);
  const top = runs[0];
  let on = "";
  if (runs.length === 1) on = `, all ${top[0]}`;
  else if (top && top[1] / item.attempts >= 0.8)
    on = `, ${top[1]} on ${top[0]}`;
  return first ? `${item.attempts} attempts${on}` : `${item.attempts}${on}`;
}
