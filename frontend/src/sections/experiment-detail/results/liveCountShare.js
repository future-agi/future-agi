// options-truth L2 finalize: one count on the experiment page. The header's
// `Running · 3/30` pill read the experiment poll while the grid, its run chip
// and `2/30 · about 60 s left` read the results summary: two polls out of
// phase, so the pill ran a row ahead of (or behind) the grid most of the
// time. The Data tab publishes the counts it shows; the header reads them
// while that tab is open and its own poll otherwise. Status words, errors
// and every action stay the header's.
import { create } from "zustand";

const COUNT_FIELDS = ["done", "total", "scored", "eta_s"];

/**
 * Pure: `{runId: {done, total, scored?, eta_s?}}` from the runs the grid
 * shows (each with its live `progress`). Only finite numbers are kept, so a
 * missing field never blanks the header's own value.
 * @param {object[]} runs
 * @returns {Record<string, object>}
 */
export function countsByRun(runs) {
  const out = {};
  (runs ?? []).forEach((r) => {
    const p = r?.progress;
    if (!r?.run_id || !p) return;
    const counts = {};
    COUNT_FIELDS.forEach((k) => {
      if (Number.isFinite(p[k])) counts[k] = p[k];
    });
    if (Number.isFinite(counts.done) && Number.isFinite(counts.total))
      out[r.run_id] = counts;
  });
  return out;
}

/**
 * Pure: the header's runs with the grid's counts. `status`, `errors` and
 * everything else stay the header's; a run the grid does not show keeps its
 * own progress.
 * @param {object[]} runs  the experiment poll's runs
 * @param {Record<string, object>|null} shared  countsByRun of the grid
 */
export function withSharedCounts(runs, shared) {
  const list = runs ?? [];
  if (!shared) return list;
  return list.map((r) => {
    const counts = shared[r?.run_id];
    if (!counts) return r;
    return { ...r, progress: { ...(r.progress ?? {}), ...counts } };
  });
}

/** Stable key of a counts map (publish only when a number moved). */
export const countsKey = (counts) =>
  Object.keys(counts ?? {})
    .sort()
    .map((id) =>
      [id, ...COUNT_FIELDS.map((k) => counts[id][k] ?? "")].join(":"),
    )
    .join("|");

export const useLiveCounts = create((set) => ({
  byExperiment: {},
  publish: (experimentId, counts) =>
    set((s) => ({
      byExperiment: { ...s.byExperiment, [experimentId]: counts },
    })),
  clear: (experimentId) =>
    set((s) => {
      if (!(experimentId in s.byExperiment)) return s;
      const next = { ...s.byExperiment };
      delete next[experimentId];
      return { byExperiment: next };
    }),
}));
