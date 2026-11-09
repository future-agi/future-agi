// IT-R5 G3 (P3): the review loop's marks in the grid. A vote in the
// drawer's Review tab marks its row (`✓ B`, `= tie`) in the row-number
// cell, and the footer counts `9/9 reviewed` for the rows in view. The
// results view says which filter the queue walks (`Worse (9)`) and the
// next one (`Review Better (13) next`) for the last-row toast.
import { create } from "zustand";

export const useReviewMarks = create((set) => ({
  /** exampleId -> {choice: "A"|"B"|"tie", blind: boolean} */
  marks: {},
  /** `{label, next?: {label, onGo}}` — the filter the queue walks. */
  queue: null,
  // L7c g6 (status:population): a mark remembers the run pair it was
  // cast on, so `N reviewed` counts only votes on the pair in view.
  mark: (id, choice, blind = false, pair = null) =>
    set((s) => {
      const marks = { ...s.marks };
      if (choice) marks[id] = { choice, blind, pair };
      else delete marks[id];
      return { marks };
    }),
  setQueue: (queue) => set({ queue }),
}));

/** `✓ B` / `= tie` / `✓` (blind: never names the side). Pure. */
export function markText(mark) {
  if (!mark?.choice) return null;
  if (mark.choice === "tie") return "= tie";
  return mark.blind ? "✓" : `✓ ${mark.choice}`;
}

/** One key for a run pair, whichever side is A. */
export const pairOf = (a, b) => (a && b ? [a, b].sort().join("|") : null);

/**
 * `9/9 reviewed` over the rows in view, or null before the first vote.
 * With `pair`, only votes cast on that run pair count.
 */
export function reviewedText(marks, rowIds, pair = null) {
  const ids = rowIds ?? [];
  const n = ids.filter(
    (id) => marks?.[id]?.choice && (!pair || marks[id].pair === pair),
  ).length;
  return n ? `${n}/${ids.length} reviewed` : null;
}

/** The last-row toast: `Last row in Worse (9) · all reviewed`. Pure. */
export function lastRowText(queue, marks, rowIds) {
  const ids = rowIds ?? [];
  const n = ids.filter((id) => marks?.[id]?.choice).length;
  const where = queue?.label ? ` in ${queue.label}` : "";
  return `Last row${where} · ${n >= ids.length && ids.length ? "all reviewed" : `${n}/${ids.length} reviewed`}`;
}

/**
 * IT-R5 G3 (P2): the evals a row got worse on vs the baseline, for any
 * other run (the Worse pill's rule), the page's primary eval first:
 * `[{key, name}]`. Pure.
 */
export function worseEvalsOf(row, { baselineId, defsByKey, primaryKey } = {}) {
  const keys = new Set();
  Object.entries(row?.results ?? {}).forEach(([runId, r]) => {
    if (runId === baselineId) return;
    Object.entries(r?.movement ?? {}).forEach(([k, m]) => {
      if (m !== "regressed") return;
      const def = defsByKey?.[k];
      if (def && def.kind && def.kind !== "eval") return;
      keys.add(k);
    });
  });
  return [...keys]
    .sort((a, b) => Number(b === primaryKey) - Number(a === primaryKey))
    .map((k) => ({ key: k, name: defsByKey?.[k]?.name ?? k }));
}
