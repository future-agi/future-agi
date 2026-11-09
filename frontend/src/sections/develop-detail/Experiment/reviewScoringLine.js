import { reducerLabel } from "src/sections/common/Analysis/trialReducers";

/**
 * The run-settings scoring line on Review: how a row's repeated tries
 * become one score. Error-policy wording belongs to the scope summary
 * (ScopePolicySummary), so this line never states a policy on its own.
 * Returns null when every run plays a single try.
 * @param {{trialReducer?: string, maxTries?: number}} settings
 */
export function reviewScoringLine({ trialReducer, maxTries = 1 } = {}) {
  const k = Math.max(1, Math.round(Number(maxTries) || 1));
  if (k <= 1) return null;
  return `Each row scored by ${reducerLabel(trialReducer ?? "mean", k)} of its ${k} tries`;
}
