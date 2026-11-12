import { beforeEach, expect, it, vi } from 'vitest';
import axios from 'src/utils/axios';
import { loadReleaseInspection } from './review-production';

vi.mock('src/utils/axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
const packet = { subject_id: 'subject', subject_kind: 'skill', candidate_version_id: 'version', candidate_digest: 'digest', transition: { label: 'production' }, target: null,
  evidence: [{ source: 'skills_text', source_id: 'evidence', annotation_revision: 2 }], external_pipeline: null };
const result = () => ({ fingerprint: 'fingerprint', capabilities: { before_digest: 'old', after_digest: 'digest', changes: [] },
  target_configuration: null, summaries: [{ source: 'skills_text', source_id: 'evidence', annotation_revision: 2, disagreements: 1, cannot_assess: 0, planned: null, graded: null, incomplete: null, regressions: null, availability: 'unavailable' }] });
beforeEach(() => { vi.clearAllMocks(); axios.post.mockResolvedValue({ data: { result: result() } }); });
it('preserves unknowns and unresolved feedback, with no client-asserted grades', async () => {
  const value = await loadReleaseInspection(packet, 'fingerprint', {});
  expect(value.capabilities).toMatchObject({ beforeDigest: 'old', afterDigest: 'digest' });
  expect(value.summaries[0]).toMatchObject({ sourceId: 'evidence', annotationRevision: 2, disagreements: 1, graded: null, regressions: null, availability: 'unavailable' });
  expect(axios.post.mock.calls[0][1]).toMatchObject({ expected_fingerprint: 'fingerprint', evidence: [{ source: 'skills_text', source_id: 'evidence', annotation_revision: 2 }] });
});
it.each(['fingerprint', 'candidate', 'missing', 'annotation'])('rejects %s inspection mismatch', async (fault) => {
  const value = result();
  if (fault === 'fingerprint') value.fingerprint = 'other';
  if (fault === 'candidate') value.capabilities.after_digest = 'other';
  if (fault === 'missing') value.summaries = [];
  if (fault === 'annotation') value.summaries[0].annotation_revision = 3;
  axios.post.mockResolvedValue({ data: { result: value } });
  await expect(loadReleaseInspection(packet, 'fingerprint', {})).rejects.toThrow();
});
