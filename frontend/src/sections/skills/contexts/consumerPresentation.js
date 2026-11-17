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

// UXF-15 and §15 D17: the list says "apps", never "consumers". When no app has reported, the lead
// says so and the list shows "No report yet" once, so rows carry no per-row tag. A mixed list keeps the
// long tag on the rows that have not reported, which tells them apart.
const apps = (count) => `${count} ${count === 1 ? 'app' : 'apps'}`;

/**
 * The one-sentence lead above the Connected apps list.
 *   total      apps that follow this context (all pages)
 *   shown      apps on this page; `missing` counts within them
 *   missing    apps on this page that still need a delivery report
 *   unreported true when no app has reported anything
 */
export function appsSummary({ total, shown = total, missing = 0, unreported = false }) {
  const lead = `${apps(total)} ${total === 1 ? 'follows' : 'follow'} this context.`;
  if (unreported) return `${lead} ${total === 1 ? "It hasn't" : 'None has'} reported which version it received yet.`;
  const onPage = total > shown ? ' on this page' : '';
  if (missing > 0) return `${lead} ${missing} of ${shown}${onPage} still ${missing === 1 ? 'needs' : 'need'} a delivery report to confirm the version received.`;
  return `${lead} Apps report the version they receive; delivery does not prove use.`;
}

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

// A relative time inside a sentence ("As of just now", "in 2 min") starts lowercase; dates are left alone.
export const midSentence = (text) => String(text).replace(/^(Just now|In )/, (lead) => lead.toLowerCase());

// True only when the summary sentence is literally true: every app is unreported, none has an
// installed, discovered or delivered release, and none has a report time.
export const noAppHasReported = (rows, asOf) => rows.length > 0 && rows.every((row) => row.states.length > 0 && row.states.every((state) => state === 'unknown')
  && !row.installed && !row.discovered && !row.loaded && consumerReportAt(row, asOf) === null);
