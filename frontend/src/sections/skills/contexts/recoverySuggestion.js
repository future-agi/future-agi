// Defaults for the recovery view. They save the operator a lookup, never a decision:
// the suggested release is only preselected, its complete closure is still reviewed and
// acknowledged, and the suggested reviewer is still checked by the server on submit.

/**
 * The newest eligible release older than every release a declared consumer failed on.
 * Returns null when no consumer failure is retained or no such release is eligible.
 */
export function suggestRelease({ candidates, cohort }) {
  const failing = cohort
    .map((row) => candidates.find((candidate) => candidate.id === row.latestFailure?.versionId)?.number)
    .filter(Number.isInteger);
  if (!failing.length) return null;
  const oldest = Math.min(...failing);
  const pick = candidates
    .filter((candidate) => candidate.eligible && candidate.selectable && !candidate.isProduction && candidate.number < oldest)
    .sort((a, b) => b.number - a.number)[0];
  return pick ? { candidateId: pick.id, number: pick.number, failing: oldest } : null;
}

/** The person who last moved production, when they are a listed reviewer; otherwise the only reviewer. */
export function suggestReviewer({ reviewers, lastReleaserId }) {
  const listed = reviewers ?? [];
  return listed.find((item) => item.id === lastReleaserId)?.id ?? (listed.length === 1 ? listed[0].id : "");
}
