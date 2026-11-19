import { beforeEach, expect, it, vi } from 'vitest';
import axios from 'src/utils/axios';
import { loadContextReview } from './context-production';
import { isStaleReview, loadReleaseRequest } from './review-production';

vi.mock('src/utils/axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
vi.mock('./context-production', () => ({ loadContextReview: vi.fn() }));
const packet = { schema: 1, subject_kind: 'agent_context', subject_id: 'context', candidate_version_id: 'candidate', candidate_digest: 'digest',
  transition: { label: 'production', version: 2, expected_version: 1, expected_release_revision: 1, expected_security_revision: 0, expected_impact_fingerprint: 'impact' },
  target: null, evidence: [], external_pipeline: null };
const detail = { id: 'request', revision: 1, status: 'approved', subject_id: 'context', author_id: 'author', fingerprint: 'reviewed', packet };
const inspection = { fingerprint: 'reviewed', capabilities: { before_digest: 'old', after_digest: 'digest', changes: [] }, target_configuration: null, summaries: [] };
const rejected = (statusCode, code) => Promise.reject({ statusCode, code });
beforeEach(() => {
  vi.clearAllMocks();
  loadContextReview.mockResolvedValue({ complete: true, candidateDigest: 'digest', originChanges: [], checks: {}, impact: null, releaseState: { labels: [{ name: 'production', canMove: true }] } });
});

it('keeps the retained packet but marks it stale on a server precondition conflict', async () => {
  axios.get.mockResolvedValueOnce({ data: { result: detail } });
  axios.post.mockResolvedValueOnce({ data: { result: inspection } }).mockImplementationOnce(() => rejected(409, 'release_conflict'));
  const value = await loadReleaseRequest('request', 'context', {});
  expect(value.preview).toMatchObject({ fingerprint: 'reviewed', current: false });
  expect(value.staleCode).toBe('release_conflict');
  expect(value.review.complete).toBe(false);
});

it('exposes only explicit renewal when detailed evidence expired', async () => {
  axios.get.mockImplementationOnce(() => rejected(410, 'evidence_expired')).mockResolvedValueOnce({ data: { result: {
    request_id: 'request', revision: 3, status: 'approved', subject_id: 'context', previous_fingerprint: 'reviewed', fingerprint: 'renewed', packet, changes: [], removed_evidence: [] } } });
  const value = await loadReleaseRequest('request', 'context', {});
  expect(value).toMatchObject({ renewalRequired: 'evidence_expired', canExecute: false, request: { id: 'request', revision: 3 } });
  expect(value.preview).toMatchObject({ current: false, packet: { candidateVersionId: 'candidate' } });
  expect(value.review.complete).toBe(false);
});

it.each([['digest', 1, true], ['different', 1, false], ['digest', 3, false]])('keeps renewal comparison read-only and bound to both versions (%s, %s)', async (candidateDigest, version, matches) => {
  const changes = [{ path: 'AGENTS.md', status: 'modified', fromText: 'Before', toText: 'Candidate' }];
  loadContextReview.mockResolvedValue({ complete: true, candidateDigest, originChanges: changes, dependencyChanges: [], releaseState: { labels: [{ name: 'production', version }] } });
  axios.get.mockImplementationOnce(() => rejected(410, 'evidence_expired')).mockResolvedValueOnce({ data: { result: {
    request_id: 'request', revision: 3, status: 'approved', subject_id: 'context', fingerprint: 'renewed', packet, changes: [], removed_evidence: [] } } }).mockResolvedValueOnce({ data: { result: { name: 'Support agent' } } });
  const value = await loadReleaseRequest('request', 'context', {});
  expect(value.review.changes).toEqual(matches ? changes : undefined);
  expect(value.review.complete).toBe(false);
  expect(value.preview.current).toBe(false);
  expect(value.canExecute).toBe(false);
});

it('never treats authorization or unrelated failures as stale', async () => {
  expect(isStaleReview({ statusCode: 403, code: 'permission_denied' })).toBe(false);
  expect(isStaleReview({ statusCode: 500, code: 'release_conflict' })).toBe(false);
  axios.get.mockResolvedValueOnce({ data: { result: detail } });
  axios.post.mockImplementationOnce(() => rejected(403, 'permission_denied'));
  await expect(loadReleaseRequest('request', 'context', {})).rejects.toMatchObject({ code: 'permission_denied' });
});

it('re-reads the request once when it was renewed while the stale read was in flight', async () => {
  axios.get.mockImplementationOnce(() => rejected(410, 'evidence_expired'))
    .mockImplementationOnce(() => rejected(409, 'review_current'))
    .mockResolvedValueOnce({ data: { result: { ...detail, revision: 2, status: 'pending', fingerprint: 'renewed' } } });
  axios.post.mockResolvedValueOnce({ data: { result: { ...inspection, fingerprint: 'renewed' } } }).mockResolvedValueOnce({ data: { result: { fingerprint: 'renewed' } } });
  const value = await loadReleaseRequest('request', 'context', {});
  expect(value.request).toMatchObject({ revision: 2, status: 'pending' });
  expect(value.preview).toMatchObject({ fingerprint: 'renewed', current: true });
  expect(axios.get).toHaveBeenCalledTimes(3);
});

it('does not loop when the re-read still cannot load', async () => {
  axios.get.mockImplementation(() => rejected(410, 'evidence_expired'));
  axios.get.mockImplementationOnce(() => rejected(410, 'evidence_expired')).mockImplementationOnce(() => rejected(409, 'review_current'))
    .mockImplementationOnce(() => rejected(410, 'evidence_expired')).mockImplementationOnce(() => rejected(409, 'review_current'));
  await expect(loadReleaseRequest('request', 'context', {})).rejects.toMatchObject({ code: 'evidence_expired' });
  expect(axios.get).toHaveBeenCalledTimes(4);
  axios.get.mockReset();
});
