/**
 * When Debug failures can read a finished run. The server refuses the same
 * cases (tracer/services/simulation_investigation.py) with the same words.
 */
export const MIN_CONNECTED_CALLS = 15;

export const TOO_FEW_CALLS_REASON = `You need at least ${MIN_CONNECTED_CALLS} connected calls to debug failures.`;
export const NOTHING_FAILED_REASON =
  "Nothing failed in this run, so there’s nothing to debug.";

// The server's refusal codes for the two cases, so a refused request reads the same.
export const DEBUG_FAILURES_REFUSALS: Readonly<Record<string, string>> = {
  investigation_too_few_calls: TOO_FEW_CALLS_REASON,
  investigation_nothing_failed: NOTHING_FAILED_REASON,
};

export const RUN_IN_PROGRESS_REASON =
  "Diagnosis is available once every call in this run finishes.";
export const CALLS_UNFINISHED_REASON =
  "Some calls in this run didn’t finish, so Debug failures can’t read it.";

// The server's refusals of a run whose calls have not all finished. Unlike the
// two above, they can change while the page is open, so asking again is offered.
const UNFINISHED_RUN_REFUSALS: Readonly<Record<string, string>> = {
  execution_not_completed: RUN_IN_PROGRESS_REASON,
  execution_calls_unfinished: CALLS_UNFINISHED_REASON,
};

/** The words for any refusal of a Debug failures request, or null for another error. */
export function debugFailuresRefusalReason(
  code?: string | null,
): string | null {
  if (!code) return null;
  return DEBUG_FAILURES_REFUSALS[code] ?? UNFINISHED_RUN_REFUSALS[code] ?? null;
}

export interface DebugFailuresCounts {
  connectedCalls?: number | null;
  failingCalls?: number | null;
}

/** Why Debug failures is blocked, or null. A count the server did not send blocks nothing. */
export function debugFailuresBlockedReason({
  connectedCalls,
  failingCalls,
}: DebugFailuresCounts): string | null {
  if (connectedCalls != null && connectedCalls < MIN_CONNECTED_CALLS) {
    return TOO_FEW_CALLS_REASON;
  }
  if (failingCalls != null && failingCalls < 1) return NOTHING_FAILED_REASON;
  return null;
}
