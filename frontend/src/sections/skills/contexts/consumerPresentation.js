// Presentation only: these labels never derive verification or change a consumer's policy.
export const consumerStateNames = {
  matches_target: 'Matches target', pinned_as_intended: 'Pinned as intended',
  update_available: 'Update available', local_modifications: 'Local modifications',
  mixed_sessions: 'Mixed sessions', old_thread_pinned: 'Old thread retained',
  stale_observation: 'Stale observation', unknown: 'Runtime loading not observed', quarantined: 'Quarantined',
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
