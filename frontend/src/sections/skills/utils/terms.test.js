import { describe, expect, it } from 'vitest';
import {
  ADD_SKILL, CHECK_FOR_UPDATE, COMPOSER_CONTINUE, COMPOSER_SEND, FOLLOWS_PRODUCTION, HUMAN_CHECK,
  INBOX_OPEN_REQUEST, OPEN_CONNECTED_APPS, OTHER_FILES, OTHER_FILES_NOTE, PINNED, PROTECTED_LABEL,
  PUBLISH_AND_REQUEST_RELEASE, RELEASE_REQUEST, RENEW_REQUEST, RETIRED_TERMS, STREAM_B_TERMS,
  TEST_CASE_APPROVED, TEST_CASE_NEEDS_APPROVAL, UPDATE_RULE, followsLabel, labelDisplayName, pinnedTo,
  releaseRuleSentences, requestReleaseFor,
} from './terms';

describe('UXF-14 release request names', () => {
  it.each([
    ['Header and Versions row', requestReleaseFor(2), 'Request release for v2'],
    ['Composer footer, first step', COMPOSER_CONTINUE, 'Continue'],
    ['Composer footer, send', COMPOSER_SEND, 'Send request'],
    ['Inbox row', INBOX_OPEN_REQUEST, 'Open request'],
    ['Stale banner', RENEW_REQUEST, 'Renew request'],
    ['Publish page secondary', PUBLISH_AND_REQUEST_RELEASE, 'Publish and request release'],
    ['Test case status, approved', TEST_CASE_APPROVED, 'Approved'],
    ['Test case status, waiting', TEST_CASE_NEEDS_APPROVAL, 'Needs approval'],
    ['Column and section', HUMAN_CHECK, 'Human check'],
    ['The object', RELEASE_REQUEST, 'Release request'],
  ])('%s reads "%s"', (_place, actual, expected) => expect(actual).toBe(expected));
});

describe('shared terms from UXF-09, UXF-15 and UXF-16', () => {
  it('explains protected labels and opens connected apps', () => {
    expect(PROTECTED_LABEL).toBe('Protected label (only admins can move it)');
    expect(OPEN_CONNECTED_APPS).toBe('Open connected apps');
  });
  it('uses the read-view names for linked skills', () => {
    expect(UPDATE_RULE).toBe('Update rule');
    expect(FOLLOWS_PRODUCTION).toBe('Follows production');
    expect(followsLabel('staging')).toBe('Follows staging');
    expect(PINNED).toBe('Pinned');
    expect(pinnedTo(1)).toBe('Pinned to v1');
    expect(ADD_SKILL).toBe('Add skill');
    expect(CHECK_FOR_UPDATE).toBe('Check for update');
  });
  it('names platform-added files the same way as the read view', () => {
    expect(OTHER_FILES).toBe('Other files');
    expect(OTHER_FILES_NOTE.startsWith('Added by Future AGI')).toBe(true);
  });
});

describe('retired names', () => {
  it.each(RETIRED_TERMS)('no term contains "%s"', (retired) => {
    for (const value of Object.values(STREAM_B_TERMS)) expect(value.toLowerCase()).not.toContain(retired.toLowerCase());
  });
  it('never says "review request" or uses runtime words for fetches (§2.1)', () => {
    for (const value of Object.values(STREAM_B_TERMS)) {
      expect(value).not.toMatch(/review request|release review/i);
      expect(value).not.toMatch(/\bused\b|\busing\b|\bin use\b/i);
    }
  });
});

describe('releaseRuleSentences (UXF-09)', () => {
  it('builds the protected-label sentence from the label it is given', () => {
    expect(releaseRuleSentences({ label: 'gold', protectedLabels: ['gold'] }))
      .toEqual(["Gold is a protected label: only workspace admins can move it, and automation can't."]);
    expect(releaseRuleSentences({ label: 'canary', protectedLabels: ['gold'] }))
      .toEqual(["Canary isn't a protected label: anyone who can edit can move it."]);
  });
  it('lists the workspace protected labels when no label is in play', () => {
    expect(releaseRuleSentences({ protectedLabels: ['gold', 'silver'] }))
      .toEqual(["Protected labels (only workspace admins can move them, and automation can't): gold, silver."]);
    expect(releaseRuleSentences({ protectedLabels: [] })).toEqual(['No labels are protected.']);
  });
  it('explains the review rule only when the caller says review is required', () => {
    expect(releaseRuleSentences({ reviewRequired: true, subjectKind: 'agent_context' }))
      .toEqual(["Agent context releases need a second person's review, because one release can change several skills."]);
    expect(releaseRuleSentences({ reviewRequired: true, subjectKind: 'skill' })).toEqual(["This release needs a second person's review."]);
    expect(releaseRuleSentences({ reviewRequired: false })).toEqual([]);
    expect(releaseRuleSentences({})).toEqual([]);
  });
  it('capitalises only for display', () => expect(labelDisplayName(' production ')).toBe('Production'));
});
