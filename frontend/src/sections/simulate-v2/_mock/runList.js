/**
 * What the Runs list contains — shared so every count of it agrees.
 *
 * The list is manual runs plus self-improvement trials (each trial was a full
 * run of a candidate prompt), plus one queued demo row. The Runs tab badge
 * used to count only the manual runs and said 2 over a list of 11.
 */
import { runSummaries, trialSummaries } from "./comparison";

/* Show at most this many trials, so the merged list stays readable. */
export const TRIAL_CAP = 8;

/* Prototype: one queued run on top of the table so the status is demoable. */
export const DEMO_QUEUED_ROW = true;

/** How many rows the Runs list shows — the same rows, the same rules. */
export function runListCount(env, envState) {
  if (!env || !envState) return 0;
  const manual = runSummaries(env, envState).filter((r) => !r.synthetic).length;
  const trials = Math.min(TRIAL_CAP, trialSummaries(env, envState).filter((r) => !r.synthetic).length);
  const finished = manual + trials;
  const queued = DEMO_QUEUED_ROW && !envState.demoQueuedRemoved && finished > 0 ? 1 : 0;
  return finished + queued;
}
