// Presentation only: these labels never derive verification or change a consumer's policy.
export const consumerStateNames = {
  matches_target: 'Matches target', pinned_as_intended: 'Pinned as intended',
  update_available: 'Update available', local_modifications: 'Local modifications',
  mixed_sessions: 'Mixed sessions', old_thread_pinned: 'Old thread retained',
  stale_observation: 'Stale observation', unknown: 'No report from this app yet', quarantined: 'Quarantined',
};
export const consumerNextSteps = {
  review_quarantine: 'Review the quarantined release before continuing.',
  review_local_changes: 'Review and preserve local changes.',
  start_clean_thread: 'Review the retained thread before starting a clean one.',
  observe_runtime: 'Wait for a runtime delivery report.',
  inspect_runtime: 'Inspect the runtime and its delivery reports.',
  check_reporter: 'Check the reporter; its last observation is stale.',
  none: 'No action reported.',
};
export const consumerNextStep = (code) => consumerNextSteps[code] || 'Unknown';

export const humanizeCode = (code) => {
  if (!code) return 'Not reported';
  const words = String(code).replace(/[_-]+/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
};

// Completeness describes the bounded inventory read; unknown also includes missing
// runtime reports, incomplete delivery, later failures and lost authority.
export const needsDeliveryVerification = (row) => row.observationsComplete === false || row.states.includes('unknown');

// The API reports age relative to its snapshot, not the viewer's current clock.
// Missing, negative and out-of-range values must not invent a report timestamp.
export function consumerReportAt(row, asOf) {
  const age = row.observationAgeSeconds;
  if (!Number.isFinite(age) || age < 0 || !asOf) return null;
  const date = new Date(Date.parse(asOf) - age * 1000);
  return Number.isFinite(date.getTime()) ? date.toISOString() : null;
}
