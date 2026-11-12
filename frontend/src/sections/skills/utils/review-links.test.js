import { describe, expect, it } from 'vitest';
import { paths } from 'src/routes/paths';
import { reviewComposerUrl } from './review-links';

const candidate = {
  kind: 'agent_context', subjectId: 'context-1', name: 'Support & returns / Europe',
  candidateId: 'version-2', number: 2, digest: `sha256:${'a'.repeat(64)}`,
};
describe('reviewComposerUrl', () => {
  it('round-trips names, complete candidate identity and multiple typed evidence refs', () => {
    const url = new URL(reviewComposerUrl({ ...candidate, label: 'production', target: 'target-1', evidence: [
      { source: 'skills_text', sourceId: 'run-1', annotationRevision: 9 },
      { source: 'simulation_native', sourceId: 'run-2', annotationRevision: 11 },
    ] }), 'https://example.invalid');
    expect(url.pathname).toBe(paths.dashboard.skills.reviews);
    expect(Object.fromEntries(url.searchParams)).toEqual({
      compose: '1', kind: 'agent_context', subject: 'context-1', name: candidate.name,
      candidate: 'version-2', number: '2', digest: candidate.digest, label: 'production',
      target: 'target-1', evidence: 'skills_text:run-1,simulation_native:run-2',
    });
    expect(url.search).not.toContain('annotation');
  });
  it('omits optional values rather than serializing undefined or empty evidence', () => {
    const url = reviewComposerUrl(candidate);
    expect(url).not.toContain('undefined');
    for (const key of ['label', 'evidence', 'target']) expect(new URLSearchParams(url.split('?')[1]).has(key)).toBe(false);
  });
  it('accepts wire refs and summary rows without transporting their mutable metadata', () => {
    const url = reviewComposerUrl({ ...candidate, evidence: [
      { source: 'skills_text', source_id: 'wire', annotation_revision: 4 },
      { source: 'simulation_native', id: 'row', visibility: 'private' },
      { source: 'skills_text' },
    ] });
    expect(new URLSearchParams(url.split('?')[1]).get('evidence')).toBe('skills_text:wire,simulation_native:row');
    expect(url).not.toMatch(/annotation|visibility/);
  });
});
