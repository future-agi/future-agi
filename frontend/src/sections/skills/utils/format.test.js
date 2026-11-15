import { describe, expect, it } from 'vitest';
import { paths } from 'src/routes/paths';
import { allowanceInput, formatBytes, formatExpiry, formatExpiryShort, formatTokens, formatUtcFull, formatWhen, shortDigest } from './format';
import { answerConclusion, impactSentence, publishSentence } from './conclusions';

it.each([[0, '0 B'], [1023, '1023 B'], [1024, '1 KiB'], [1536, '1.5 KiB'], [2097152, '2 MiB']])('formats %s UTF-8 bytes', (bytes, expected) => expect(formatBytes(bytes)).toBe(expected));
it('marks token counts as estimates and abbreviates only the digest prefix', () => {
  expect(formatTokens(5000)).toBe(`≈ ${(5000).toLocaleString()} tokens`);
  expect(shortDigest('sha256:0123456789abcdef')).toBe('0123456789ab');
});
it('registers the exact skills routes and API keys settings helper', () => {
  expect(paths.dashboard.skills).toMatchObject({ root: '/dashboard/skills' });
  expect(paths.dashboard.skills.detail('id')).toBe('/dashboard/skills/id');
  expect(paths.dashboard.skills.edit('id')).toBe('/dashboard/skills/id/edit');
  expect(paths.dashboard.skills.compare('id')).toBe('/dashboard/skills/id/compare');
  expect(paths.dashboard.settings.apiKeys).toBe('/dashboard/settings/api_keys');
});

describe('dates', () => {
  const now = Date.parse('2026-11-14T00:48:30Z');
  const utc = { now, timeZone: 'UTC' };
  it.each([
    ['2026-11-14T00:48:10Z', 'Just now'],
    ['2026-11-14T00:36:00Z', '12 min ago'],
    ['2026-11-13T21:48:00Z', '3 hours ago'],
    ['2026-11-13T23:48:00Z', '1 hour ago'],
    ['2026-11-14T01:00:30Z', 'In 12 min'],
    ['2026-11-12T00:48:59Z', '12 Nov, 00:48'],
    ['2025-11-12T09:05:00Z', '12 Nov 2025, 09:05'],
  ])('formats %s relative to now as %s, never with seconds', (value, expected) => {
    expect(formatWhen(value, utc)).toBe(expected);
  });
  it('uses the requested time zone for absolute times', () => {
    expect(formatWhen('2026-11-10T23:30:00Z', { now, timeZone: 'Asia/Kolkata' })).toBe('11 Nov, 05:00');
  });
  it.each([
    ['2026-11-21T00:48:30Z', 'Expires in 7 days'],
    ['2026-11-15T00:48:30Z', 'Expires in 1 day'],
    ['2026-11-14T03:48:30Z', 'Expires in 3 hours'],
    ['2026-11-12T00:48:30Z', 'Expired 2 days ago'],
    ['2026-11-14T00:48:20Z', 'Expired just now'],
  ])('describes expiry %s as %s', (value, expected) => {
    expect(formatExpiry(value, { now })).toBe(expected);
  });
  it.each([
    ['2026-12-13T00:48:30Z', 'in 29 days'],
    ['2026-11-15T00:48:30Z', 'in 1 day'],
    ['2026-11-14T03:48:30Z', 'in 3 hours'],
    ['2026-11-14T00:48:40Z', 'in less than a minute'],
    ['2026-11-12T00:48:30Z', 'expired 2 days ago'],
    ['2026-11-14T00:48:20Z', 'expired just now'],
  ])('describes expiry %s under an Expires header as %s', (value, expected) => {
    expect(formatExpiryShort(value, { now })).toBe(expected);
  });
  it('gives the full UTC time for tooltips and returns nothing for missing values', () => {
    expect(formatUtcFull('2026-11-14T00:48:46.123Z')).toBe('14 Nov 2026, 00:48 UTC');
    expect(formatUtcFull(undefined)).toBe('');
    expect(formatWhen('not a date', utc)).toBe('');
    expect(formatExpiry(null, { now })).toBe('');
    expect(formatExpiryShort('not a date', { now })).toBe('');
  });
});

it.each([[null, ''], ['', ''], ['20.00000000', '20.00'], ['20', '20.00'], ['20.5', '20.50'], ['12.00000001', '12.00000001'], ['0.125', '0.125']])('shows the allowance %s as %s without losing precision', (value, expected) => {
  expect(allowanceInput(value)).toBe(expected);
});

describe('conclusion sentences follow the job state and name what changes', () => {
  const attempt = (status, verdict) => ({ status, answer_verdict: verdict });
  const row = (...attempts) => ({ case_id: 'c', variants: [{ variant: 'candidate', attempts }] });
  it('says queued while the job or every attempt is still waiting, never running', () => {
    expect(answerConclusion({ status: 'queued', subject: 'v2', rows: [], complete: false })).toBe('Answer check queued for v2.');
    expect(answerConclusion({ status: 'running', subject: 'v2', rows: [row(attempt('not_started'), attempt('queued'))], complete: false })).toBe('Answer check queued for v2.');
    expect(answerConclusion({ status: 'running', subject: 'v2', rows: [row(attempt('running'), attempt('not_started'))], complete: false })).toBe('Answer check running for v2.');
    expect(answerConclusion({ status: 'running', subject: 'v2', rows: [], complete: false })).toBe('Answer check running for v2.');
    expect(answerConclusion({ status: 'cancelling', subject: 'v2', rows: [], complete: false })).toBe('Answer check stopping for v2.');
    expect(answerConclusion({ status: 'failed', subject: 'v2', rows: [], complete: true })).toBe('Answer check failed for v2. No result is inferred.');
    expect(answerConclusion({ status: 'completed', subject: 'v2', rows: [row(attempt('completed', 'pass'))], complete: true })).toBe('Answer check passed for v2 on 1 of 1 case.');
  });
  const descriptor = (version) => JSON.stringify({ links: [{ skill_id: 's1', mount: 'skills/refund-policy/', version }] });
  it('reads linked-skill moves from the descriptor and names AGENTS.md separately', () => {
    const review = { expectedHeadVersion: 1, changes: [
      { path: 'skills/refund-policy/SKILL.md', fromText: 'a', toText: 'b' },
      { path: '.futureagi/context.json', fromText: descriptor(1), toText: descriptor(2) },
    ], acceptedLock: { links: [{ mount: 'skills/refund-policy/', version: 2 }] } };
    expect(publishSentence('support-agent', review)).toBe('Publishing creates support-agent v2: refund-policy v1 → v2; AGENTS.md is unchanged.');
    expect(publishSentence('support-agent', { ...review, changes: [...review.changes, { path: 'AGENTS.md' }, { path: 'docs/tone.md' }] }))
      .toBe('Publishing creates support-agent v2: refund-policy v1 → v2; AGENTS.md changes, 1 other context file changes.');
  });
  it('never says "no instruction changes" and does not guess versions it cannot read', () => {
    const unreadable = { expectedHeadVersion: 2, changes: [{ path: 'skills/privacy/SKILL.md' }, { path: '.futureagi/context.json', fromText: 'not json', toText: '{}' }] };
    const sentence = publishSentence('', unreadable);
    expect(sentence).toBe('Publishing creates this context v3: privacy changes; AGENTS.md is unchanged.');
    expect(sentence).not.toMatch(/no instruction changes/);
    expect(publishSentence('support-agent', { expectedHeadVersion: 1, changes: [] })).toBe('Publishing creates support-agent v2: no linked-skill changes; AGENTS.md is unchanged.');
  });
});

describe('release impact', () => {
  it.each([
    [{}, 'No connected apps move with this release.'],
    [{ following: 1 }, '1 connected app moves with this release.'],
    [{ following: 2, pinned: 1 }, '2 connected apps move with this release. 1 is pinned and stays put.'],
    [{ following: 0, pinned: 3, unobserved: 1 }, "No connected apps move with this release. 3 are pinned and stay put. 1 app hasn't reported a version yet."],
    [{ following: 4, unobserved: 2 }, "4 connected apps move with this release. 2 apps haven't reported a version yet."],
  ])('states %j in short sentences, in app words', (impact, expected) => {
    expect(impactSentence(impact)).toBe(expected);
    expect(impactSentence(impact)).not.toMatch(/consumer|label/);
  });
});
