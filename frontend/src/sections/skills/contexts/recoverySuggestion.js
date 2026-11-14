// Defaults for the recovery view. They save the operator a lookup, never a decision:
// the suggested release is only preselected, its complete closure is still reviewed and
// acknowledged, and the suggested reviewer is still checked by the server on submit.

/**
 * The newest eligible release older than every release a declared consumer failed on.
 * With no retained failure, use production as the boundary so rollback is discoverable.
 * Returns null when no earlier selectable release is eligible.
 */
export function suggestRelease({ candidates, cohort }) {
  const failing = cohort
    .map((row) => candidates.find((candidate) => candidate.id === row.latestFailure?.versionId)?.number)
    .filter(Number.isInteger);
  const production = candidates.find((candidate) => candidate.isProduction)?.number;
  if (!failing.length && !Number.isInteger(production)) return null;
  const oldest = failing.length ? Math.min(...failing) : production;
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

/** No earlier version exists to restore. A newer publication is a release-review task. */
export function initialReleaseState(candidates, releaseRevision) {
  const production = candidates.find((candidate) => candidate.isProduction);
  if (!production || candidates.some((candidate) => candidate.number < production.number)) return null;
  const next = candidates.filter((candidate) => candidate.number > production.number && candidate.eligible && candidate.selectable)
    .sort((a, b) => b.number - a.number)[0];
  // A reload after a rollback can also have v1 in production and newer versions.
  // Only the first label revision establishes the initial-release case; otherwise
  // keep consumer verification reachable instead of inferring release history.
  // Keep restriction details visible when the only alternatives are excluded.
  if (candidates.length > 1 && (releaseRevision !== 1 || !next)) return null;
  return { production, next };
}
