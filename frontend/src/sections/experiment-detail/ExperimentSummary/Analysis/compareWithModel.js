// L7c g6 (sum:compare-with): `Compare with…` from a one-run Summary. The
// run you came from is the candidate; the baseline is the dataset's pinned
// baseline when you picked it, else the first picked run that has results.
// A waiting or empty run is never the baseline. Pure.
import { runProgressOf } from "src/sections/common/Runs/runStatus";

/** True when a run has at least one finished (scored or done) result. */
export const hasResults = (run) => {
  if (!run) return false;
  const p = runProgressOf(run);
  return (p.scored ?? p.done) > 0;
};

/**
 * @param {string[]} picked  run ids the user ticked (in order)
 * @param {string} fromRunId the one run of this Summary
 * @param {object[]} runs    R1 runs of the dataset (for progress)
 * @param {string|null} pinnedId  the dataset's pinned baseline
 * @returns {{runIds: string[], baselineId: string|null, cand: string}}
 */
export function compareWithPlan(picked, fromRunId, runs = [], pinnedId = null) {
  const byId = Object.fromEntries((runs ?? []).map((r) => [r.run_id, r]));
  const list = (picked ?? []).filter((id) => id && id !== fromRunId);
  const base =
    (pinnedId && list.includes(pinnedId) && hasResults(byId[pinnedId])
      ? pinnedId
      : null) ??
    list.find((id) => hasResults(byId[id])) ??
    null;
  const rest = list.filter((id) => id !== base);
  return {
    runIds: [...(base ? [base] : []), ...rest, fromRunId],
    baselineId: base,
    cand: fromRunId,
  };
}
