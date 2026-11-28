import { beforeEach, expect, it, vi } from 'vitest';
import axios from 'src/utils/axios';
import { prepareReleaseReview, submitReviewedPacket } from './review-authoring';
import { loadSkillReview, loadReleaseInspection } from './review-production';

vi.mock('src/utils/axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
vi.mock('./review-production', () => ({ loadSkillReview: vi.fn(), loadReleaseInspection: vi.fn() }));
const raw = { fingerprint: 'exact-fingerprint', packet: { schema: 1, subject_kind: 'skill', subject_id: 'skill',
  candidate_version_id: 'candidate', candidate_digest: 'digest', transition: { label: 'production', version: 2, expected_version: 1, expected_release_revision: 1, expected_security_revision: 0, expected_impact_fingerprint: 'impact' },
  target: null, evidence: [], external_pipeline: null } };
const selection = { resourceId: 'skill', kind: 'skill', candidateId: 'candidate', label: 'production', target: null, evidence: [], externalPipeline: null };
const config = { headers: { 'X-Workspace-Id': 'workspace' } };
beforeEach(() => {
  vi.clearAllMocks();
  axios.post.mockResolvedValue({ data: { result: raw } });
  loadReleaseInspection.mockResolvedValue({ capabilities: { changes: [] }, summaries: [] });
  loadSkillReview.mockResolvedValue({ complete: true, candidateDigest: 'digest', originChanges: [{ path: 'SKILL.md', fromText: 'before', toText: 'after' }], checks: {}, impact: null });
});
it('revalidates the frozen packet after retrieving the complete exact diff', async () => {
  const prepared = await prepareReleaseReview(selection, config);
  expect(prepared.preview.current).toBe(true);
  expect(axios.post).toHaveBeenCalledTimes(2);
  expect(prepared.raw).toBe(raw);
  expect(loadSkillReview).toHaveBeenCalledWith('skill', raw.packet, config);
  expect(loadReleaseInspection).toHaveBeenCalledWith(raw.packet, raw.fingerprint, config);
  expect(prepared.review.capabilities.changes).toEqual([]);
});
it('rejects races during inspection instead of silently accepting new preconditions', async () => {
  axios.post.mockResolvedValueOnce({ data: { result: raw } }).mockResolvedValueOnce({ data: { result: { ...raw, fingerprint: 'changed' } } });
  await expect(prepareReleaseReview(selection, config)).rejects.toThrow('Review changed');
});
it('submits the authoritative wire packet and preserves the renewal revision and operation key', async () => {
  const prepared = await prepareReleaseReview(selection, config);
  const key = '00000000-0000-4000-8000-000000000001';
  await submitReviewedPacket(prepared, { packet: prepared.preview.packet, expectedFingerprint: raw.fingerprint, recipientIds: ['independent-reviewer'], requestId: 'original-request', revision: 3, idempotencyKey: key }, config);
  expect(axios.post).toHaveBeenLastCalledWith('/model-hub/skills/release-requests/', {
    packet: raw.packet, expected_fingerprint: raw.fingerprint, recipient_ids: ['independent-reviewer'], request_id: 'original-request', revision: 3,
  }, { headers: { ...config.headers, 'Idempotency-Key': key } });
});
it('never replaces reviewed identity with a mutated UI packet', async () => {
  const prepared = await prepareReleaseReview(selection, config);
  expect(() => submitReviewedPacket(prepared, { packet: { ...prepared.preview.packet, candidateDigest: 'another' }, expectedFingerprint: raw.fingerprint }, config)).toThrow('reviewed packet changed');
  expect(axios.post).toHaveBeenCalledTimes(2);
});
