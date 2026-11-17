// IT-R5 G6 P1 (rule 38): what the run header needs to pick its ONE primary
// action. The same page candidate and ship line as the Matrix and Summary
// (pageCandidateId + compareRuns + pageShipLine), reduced to a tone. Pure.
import { compareRuns } from "src/sections/common/Runs/runNumbers";
import { getRunStatus } from "src/sections/common/Runs/runStatus";
import { pageCandidateId, pageShipLine } from "./pageShip";

/**
 * @param {{matrix: object|null, runIds: string[], baselineId: string|null,
 *   cand?: string|null, runs?: object[], noun?: string}} a
 * @returns {"worse"|"better"|"tie"|null}  `worse` = the page reads
 *   `Don't ship …` (a regression): `Test a fix` is the primary.
 */
export function headerShipTone({
  matrix,
  runIds,
  baselineId,
  cand,
  runs,
  noun = "task",
}) {
  if (!matrix || !baselineId || (runIds ?? []).length < 2) return null;
  const candId = pageCandidateId({ cand, runIds, baselineId, runs });
  if (!candId) return null;
  // the capability contract: a candidate that Failed (every row errored) is no
  // regression to fix; the page names it Failed and Rerun leads.
  const candRun = (runs ?? []).find((r) => r?.run_id === candId);
  if (candRun && getRunStatus(candRun).key === "Failed") return null;
  let compare = null;
  try {
    compare = compareRuns({ matrix, baselineId, candidateId: candId, noun });
  } catch {
    return null;
  }
  try {
    const s = pageShipLine(compare, {
      cand: "c",
      base: "b",
      noun,
      rowLabel: (n) => `#${n}`,
    });
    return s?.tone ?? null;
  } catch {
    return null;
  }
}

/**
 * Header status pill words (rule 36, S3): read from the runs' own progress.
 * `Waiting` before any row is done, `Running · 23/312` once one is, never
 * `Queued` after a row is done; terminal states keep their label.
 * @param {{key: string, label: string, active: boolean, waiting: boolean}|null} status
 * @param {{done: number, total: number}} progress  summed over the active runs
 * @returns {string|null}
 */
export function headerStatusText(status, progress) {
  if (!status) return null;
  const done = progress?.done ?? 0;
  const total = progress?.total ?? 0;
  // L7c g5 (ledger:header-status-pill): the count is the rows scored
  // (runProgressOf `finished`), the same count as every run chip's bar
  // (leaderboard, RunBar, fix-run chip), so they agree at one moment.
  const shown = progress?.finished ?? done;
  if (status.waiting)
    return done > 0 && total ? `Waiting · ${shown}/${total}` : "Waiting";
  if (status.active) {
    if (done <= 0) return "Waiting";
    return total ? `Running · ${shown}/${total}` : "Running";
  }
  return status.label;
}

/** `Nothing ran yet · …` when no run of the experiment has a result. */
export function nothingRanYet(runs, progressOf) {
  const list = runs ?? [];
  if (!list.length) return false;
  return list.every((r) => (progressOf(r)?.done ?? 0) === 0);
}
