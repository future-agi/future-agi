import { describe, expect, it } from 'vitest';
import { suggestedEvidence } from './evidenceSuggestions';

// UXF-17: a carried run wins; otherwise only the newest Application test that
// matches the target is suggested, never the Answer check. Nothing private,
// expired or for other content is suggested.

const packet = { subjectId: 'context', subjectKind: 'agent_context', candidateDigest: 'digest', target: { configurationDigest: 'target-config' } };
const shared = { subjectId: 'context', subjectKind: 'agent_context', candidateDigest: 'digest', visibility: 'workspace', status: 'completed', payloadExpiresAt: '2999-01-01', annotationRevision: 1 };
const text = (id, overrides = {}) => ({ ...shared, source: 'skills_text', sourceId: id, configuration: { text: {} }, outcome: 'works_on_examples', completedAt: '2026-11-16T10:00:00Z', ...overrides });
const native = (id, overrides = {}) => ({ ...shared, source: 'simulation_native', sourceId: id, configuration: { native: {}, digest: 'target-config' }, outcome: 'works_on_examples', completedAt: '2026-11-15T10:00:00Z', ...overrides });
const pick = (refs) => refs.map((ref) => `${ref.source}:${ref.sourceId}`);

describe('suggestedEvidence', () => {
  it('never suggests the Answer check, even when it is the newest pass', () => {
    expect(suggestedEvidence([text('answer'), native('app')], packet)).toEqual([{ source: 'simulation_native', sourceId: 'app', annotationRevision: 1 }]);
    expect(suggestedEvidence([text('answer')], packet)).toEqual([]);
  });

  it('suggests the newest matching Application test', () => {
    const refs = [native('old', { completedAt: '2026-11-10T10:00:00Z' }), native('new', { completedAt: '2026-11-16T10:00:00Z', outcome: 'needs_changes' })];
    expect(pick(suggestedEvidence(refs, packet))).toEqual(['simulation_native:new']);
  });

  it('skips runs for another target, private runs and runs without a time to order by', () => {
    expect(suggestedEvidence([native('other', { configuration: { native: {}, digest: 'different' } })], packet)).toEqual([]);
    expect(suggestedEvidence([native('mine', { visibility: 'private' })], packet)).toEqual([]);
    expect(suggestedEvidence([native('a', { completedAt: null }), native('b')], packet)).toEqual([]);
  });

  it('a carried run wins over any suggestion, and a private carried run is left to the consent', () => {
    expect(pick(suggestedEvidence([text('answer'), native('app')], packet, ['skills_text:answer']))).toEqual(['skills_text:answer']);
    expect(suggestedEvidence([text('answer', { visibility: 'private' })], packet, ['skills_text:answer'])).toEqual([]);
  });
});
