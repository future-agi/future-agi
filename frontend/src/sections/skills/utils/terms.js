// Shared words for the current Skills product (SKILL-TRACES §16, Stream B).
//
// "Release request" is the object a person sends; "review" is only the second
// person's decision on it (UXF-14). Screens import these instead of typing
// their own names, so one object keeps one name everywhere. Skill-trace
// runtime copy lives in `runtime/runtimeCopy.js`, not here.

const capitalize = (value) => (value ? `${value.charAt(0).toUpperCase()}${value.slice(1)}` : value);

/** Display form of a label name ("production" → "Production"). */
export const labelDisplayName = (name) => capitalize(String(name ?? '').trim());

// UXF-14: one release request, one name.
export const RELEASE_REQUEST = 'Release request';
export const RELEASE_REQUESTS = 'Release requests';
export const REVIEW_DECISION = 'Review';
/** Header and Versions row: "Request release for v2". */
export const requestReleaseFor = (version) => `Request release for v${version}`;
export const COMPOSER_CONTINUE = 'Continue';
export const COMPOSER_SEND = 'Send request';
export const INBOX_OPEN_REQUEST = 'Open request';
export const RENEW_REQUEST = 'Renew request';
export const PUBLISH_AND_REQUEST_RELEASE = 'Publish and request release';
export const TEST_CASE_APPROVED = 'Approved';
export const TEST_CASE_NEEDS_APPROVAL = 'Needs approval';
export const HUMAN_CHECK = 'Human check';

// UXF-09: protected labels, explained where they appear.
export const PROTECTED_LABEL = 'Protected label (only admins can move it)';
export const CHANGE_RULES = 'Change rules';

// UXF-15 and §15 D17: connected apps, never "consumers".
export const CONNECTED_APPS = 'Connected apps';
export const OPEN_CONNECTED_APPS = 'Open connected apps';
export const NO_REPORT_YET = 'No report yet';

// UXF-16: the same names in the draft editor and the read views.
export const UPDATE_RULE = 'Update rule';
export const FOLLOWS_PRODUCTION = 'Follows production';
/** "Follows production", "Follows staging". */
export const followsLabel = (label) => `Follows ${String(label ?? '').trim()}`;
export const PINNED = 'Pinned';
/** "Pinned to v1" in read views. */
export const pinnedTo = (version) => `Pinned to v${version}`;
export const ADD_SKILL = 'Add skill';
export const CHECK_FOR_UPDATE = 'Check for update';

// UXF-16: files the platform adds, grouped the same way in every file tree.
export const OTHER_FILES = 'Other files';
export const OTHER_FILES_NOTE = 'Added by Future AGI so apps load the right versions.';

// UXF-12: row actions.
export const ROW_ACTIONS_LABEL = (name) => (name ? `Actions for ${name}` : 'Row actions');

/**
 * Every name above, for tests and for screens that list them. Functions are
 * called with sample values so their output is checked too.
 */
export const STREAM_B_TERMS = Object.freeze({
  RELEASE_REQUEST, RELEASE_REQUESTS, REVIEW_DECISION,
  requestReleaseFor: requestReleaseFor(2),
  COMPOSER_CONTINUE, COMPOSER_SEND, INBOX_OPEN_REQUEST, RENEW_REQUEST, PUBLISH_AND_REQUEST_RELEASE,
  TEST_CASE_APPROVED, TEST_CASE_NEEDS_APPROVAL, HUMAN_CHECK,
  PROTECTED_LABEL, CHANGE_RULES,
  CONNECTED_APPS, OPEN_CONNECTED_APPS, NO_REPORT_YET,
  UPDATE_RULE, FOLLOWS_PRODUCTION, followsLabel: followsLabel('staging'), PINNED, pinnedTo: pinnedTo(1), ADD_SKILL, CHECK_FOR_UPDATE,
  OTHER_FILES, OTHER_FILES_NOTE,
  ROW_ACTIONS_LABEL: ROW_ACTIONS_LABEL('support-agent'),
});

/**
 * Old names that must not come back (UXF-14, UXF-15, UXF-16, §2.1). Owner
 * lanes can assert their rendered text against this list.
 */
export const RETIRED_TERMS = Object.freeze([
  'Request release review',
  'Prepare review',
  'Send review request',
  'Renew review',
  'Publish and request review',
  'Needs review',
  'Human review',
  'consumer',
  'Update policy',
  'Follow label',
  'Add to proposed links',
  'Check update for',
]);

/**
 * The release rule that applies at the point of action (UXF-09), as plain
 * sentences built only from the rules given. Nothing is assumed: a rule the
 * caller does not supply produces no sentence.
 *
 * rules:
 *   label            the label being moved or requested (optional)
 *   protectedLabels  the workspace's protected label names for this object (optional array)
 *   reviewRequired   true when this release needs a second person's review (optional)
 *   subjectKind      'skill' | 'agent_context' (used only to explain why review is required)
 */
export function releaseRuleSentences(rules = {}) {
  const sentences = [];
  const protectedLabels = Array.isArray(rules.protectedLabels)
    ? [...new Set(rules.protectedLabels.map((name) => String(name).trim()).filter(Boolean))]
    : null;
  const label = typeof rules.label === 'string' && rules.label.trim() ? rules.label.trim() : null;
  if (protectedLabels) {
    if (label && protectedLabels.includes(label)) {
      sentences.push(`${labelDisplayName(label)} is a protected label: only workspace admins can move it, and automation can't.`);
    } else if (label) {
      sentences.push(`${labelDisplayName(label)} isn't a protected label: anyone who can edit can move it.`);
    } else if (protectedLabels.length === 1) {
      sentences.push(`${labelDisplayName(protectedLabels[0])} is a protected label: only workspace admins can move it, and automation can't.`);
    } else if (protectedLabels.length > 1) {
      sentences.push(`Protected labels (only workspace admins can move them, and automation can't): ${protectedLabels.join(', ')}.`);
    } else {
      sentences.push('No labels are protected.');
    }
  }
  if (rules.reviewRequired === true) {
    sentences.push(rules.subjectKind === 'agent_context'
      ? "Agent context releases need a second person's review, because one release can change several skills."
      : "This release needs a second person's review.");
  }
  return sentences;
}
