import { describe, expect, it } from 'vitest';
import { answerConclusion, expiresIn, periodLabel, publishSentence } from '../utils/conclusions';
import { linkChanges } from 'src/api/skills/j6-recovery';

// Conclusion-first copy is derived from server data only; nothing is inferred when data is missing.
describe('conclusion sentences', () => {
  const row = (candidate, baseline) => ({ case_id: 'c', variants: [
    { variant: 'candidate', attempts: [{ answer_verdict: candidate }] },
    ...(baseline ? [{ variant: 'without_target', attempts: [{ answer_verdict: baseline }] }] : []),
  ] });
  it('says how many answer checks passed and whether the context made a difference', () => {
    expect(answerConclusion({ status: 'completed', subject: 'v3', rows: [row('pass', 'pass')], complete: true }))
      .toBe('Answer check passed for v3 on 1 of 1 case (also passes without the context).');
    expect(answerConclusion({ status: 'completed', subject: 'v3', rows: [row('pass', 'fail'), row('fail', 'fail')], complete: true }))
      .toBe('Answer check failed for v3 on 1 of 2 cases.');
    expect(answerConclusion({ status: 'running', subject: 'v3', rows: [], complete: false })).toBe('Answer check running for v3.');
    expect(answerConclusion({ status: 'failed', subject: 'v3', rows: [], complete: true })).toBe('Answer check failed for v3. No result is inferred.');
    // A partial page never claims a pass count.
    expect(answerConclusion({ status: 'completed', subject: 'v3', rows: [row('pass')], complete: false })).toBe('Answer check finished for v3.');
  });
  it('names the version publishing creates and what changes in it', () => {
    const review = { expectedHeadVersion: 1, changes: [{ path: 'skills/refund-policy/SKILL.md' }, { path: '.futureagi/context.json' }],
      acceptedLock: { links: [{ mount: 'skills/refund-policy/', version: 2 }] } };
    expect(publishSentence('billing-agent', review)).toBe('Publishing creates billing-agent v2: refund-policy v2; AGENTS.md is unchanged.');
    expect(publishSentence('billing-agent', { expectedHeadVersion: null, changes: [{ path: 'AGENTS.md' }] }))
      .toBe('Publishing creates billing-agent v1: no linked-skill changes; AGENTS.md changes.');
  });
  it('reads the context descriptor as linked-skill version moves, and refuses to guess on unreadable bytes', () => {
    const descriptor = (version) => JSON.stringify({ links: [{ skill_id: 's1', mount: 'skills/refund-policy/', version }, { skill_id: 's2', mount: 'skills/privacy/', version: 1 }] });
    expect(linkChanges(descriptor(2), descriptor(1))).toEqual([{ name: 'refund-policy', from: 2, to: 1 }]);
    expect(linkChanges('not json', descriptor(1))).toBeNull();
  });
  it('shows expiry and periods in words', () => {
    expect(expiresIn('2026-11-13T21:20:00Z', Date.parse('2026-11-13T20:50:00Z'))).toBe('Preview expires in 30 min');
    expect(periodLabel('2026-11-01T00:00:00+00:00', '2026-12-01T00:00:00+00:00')).toBe('Nov 1–30');
  });
});
