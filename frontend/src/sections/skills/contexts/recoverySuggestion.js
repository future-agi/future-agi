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

// ---- One-page rollback (SKILL-TRACES §16 UXF-05, UXF-08) ----------------------------------------
// Pure helpers for the panel, so the rules below can be read and tested without rendering.

const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;

/**
 * Files a person reads and checks: every changed file except the generated settings file,
 * which sits under its own line (UXF-05, the same rule the release flow uses).
 */
export function checkedFiles(rows, descriptorPath) {
  return (rows ?? []).filter((row) => row.status !== 'unchanged' && row.path !== descriptorPath);
}

/**
 * The one tick, naming what is open above it (UXF-05): "I've checked the 2 changed files".
 * With no changed content file it says "the changes"; a change to linked skill versions is named.
 */
export function rollbackAcknowledgment({ files = 0, linkedSkillChanges = false } = {}) {
  const what = files > 0 ? `the ${plural(files, 'changed file', 'changed files')}` : 'the changes';
  return `I've checked ${what}${linkedSkillChanges ? ' and the linked skill versions' : ''}`;
}

/**
 * Tracker steps, in order, after the request is sent (UXF-08). The current step reads as the next thing
 * to do or wait for; a finished step reads as what happened, so a check mark never sits on "Waiting".
 */
export function trackerSteps(reviewerName, step = 0) {
  const reviewer = reviewerName || 'your reviewer';
  return [
    step > 0 ? `Approved by ${reviewer}` : `Waiting for ${reviewer}`,
    step > 1 ? 'Production moved' : 'Approved: move production',
    'Check apps updated',
  ];
}

const CLOSED = new Set(['changes_requested', 'superseded', 'cancelled', 'canceled', 'rejected', 'declined', 'expired']);

/**
 * Where the rollback is, from what this page session sent and what the server says about it.
 *
 *   step   -1 not requested yet, 0 waiting for the reviewer, 1 approved, 2 production moved
 *   phase  'form' | 'waiting' | 'approved' | 'closed' | 'moved'
 *
 * `request` is the server's read of the request (status string); anything unknown or still loading
 * reads as waiting, never as approved.
 */
export function rollbackProgress({ awaiting = false, isProduction = false, status } = {}) {
  if (isProduction) return { step: 2, phase: 'moved' };
  // `status` is the last request this session sent; it says nothing about another version chosen since.
  if (!awaiting) return { step: -1, phase: 'form' };
  if (status === 'released') return { step: 2, phase: 'moved' };
  if (status === 'approved') return { step: 1, phase: 'approved' };
  if (CLOSED.has(status)) return { step: 0, phase: 'closed' };
  return { step: 0, phase: 'waiting' };
}

/**
 * True only when production may be moved from the tracker. Everything must agree: the server says the
 * request is approved, current and executable by this person, and it is exactly the request this
 * session sent (same id, same packet fingerprint, same revision when the server reported one).
 * Any other case sends the person to the request page, which asks for its own check.
 */
export function canMoveFromTracker({ read, submitted } = {}) {
  if (!read || !submitted?.requestId || !submitted.fingerprint) return false;
  const { request, preview, canExecute, staleCode, renewalRequired } = read;
  if (!request || request.status !== 'approved' || request.id !== submitted.requestId) return false;
  if (canExecute !== true || preview?.current !== true || staleCode || renewalRequired) return false;
  if (preview.fingerprint !== submitted.fingerprint) return false;
  if (Number.isInteger(submitted.requestRevision) && request.revision !== submitted.requestRevision) return false;
  return Number.isInteger(request.revision);
}
