// options-truth L7b G4 (one run ledger): whether a stopped run can resume,
// and what a resume response said about each run. Every Resume door (the
// status chip, the leaderboard row menu, the results run bar, the
// experiment header) reads these, so a run its cost cap stopped never offers
// Resume and a refused resume never toasts as a success. Pure.
import { stopKindOf } from "./runLedger";

const leftOf = (run) =>
  Math.max(
    0,
    (run?.progress?.total ?? run?.examples ?? 0) - (run?.progress?.done ?? 0),
  );

/**
 * The limit that stopped a run, from its ledger (`stopped_by`) or else its
 * recorded stop reason: cost_cap | time_limit | max_turns | stopped | null.
 */
export function stopLimitOf(run) {
  if (run?.status !== "Cancelled") return null;
  return run?.ledger?.stopped_by ?? stopKindOf(run?.status_detail) ?? null;
}

/** True when the run stopped at its cost cap (Resume cannot spend past it). */
export const stoppedAtCostCap = (run) => stopLimitOf(run) === "cost_cap";

/** True when Resume applies: a stopped run with rows left, not capped. */
export function canResumeRun(run) {
  return (
    run?.status === "Cancelled" && leftOf(run) > 0 && !stoppedAtCostCap(run)
  );
}

/**
 * The per-run outcome of a resume response (`{runs: [{run_id, error?,
 * pending?}]}`): the runs it resumed and the messages of those it refused.
 * @returns {{resumed: object[], refused: string[]}}
 */
export function resumeOutcomeOf(result) {
  const runs = Array.isArray(result?.runs) ? result.runs : [];
  return {
    resumed: runs.filter((r) => r && !r.error),
    refused: runs.filter((r) => r?.error).map((r) => r.error),
  };
}
