import { beforeEach, expect, it, vi } from 'vitest';
import axios from 'src/utils/axios';
import { loadReleaseRequest } from './review-production';

vi.mock('src/utils/axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
const envelope = (result) => ({ data: { result } });
const packet = {
  schema: 1, subject_kind: 'skill', subject_id: 'skill', candidate_version_id: 'version', candidate_digest: 'digest',
  transition: { label: 'production', expected_version: 1, version: 2, expected_release_revision: 1, expected_security_revision: 0, expected_impact_fingerprint: 'impact' },
  target: null, evidence: [], external_pipeline: null,
};
const raw = { id: 'request', revision: 1, status: 'released', subject_id: 'skill', author_id: 'author', packet, fingerprint: 'packet-hash' };
const receipt = { request_id: 'request', revision: 1, decision_id: 'decision', executed_by: 'reviewer', currentness_at_action: { packet_fingerprint: 'packet-hash', release_revision: 1, evidence: [] }, post_move_state: { label: 'production', version: 2, release_revision: 2, event_id: 'event' } };
beforeEach(() => {
  vi.clearAllMocks();
  axios.get.mockImplementation(async (url) => {
    if (url.endsWith('/receipt/')) return envelope(receipt);
    if (url.endsWith('/release-requests/request/')) return envelope(raw);
    throw new Error('Historical receipt must not depend on mutable live evidence or pointers');
  });
  axios.post.mockRejectedValue(new Error('Historical review must not be revalidated for execution'));
});
it('opens a durable receipt after reload without revalidating the already-executed transition', async () => {
  const result = await loadReleaseRequest('request', undefined, {});
  expect(result.receipt).toMatchObject({ requestId: 'request', decisionId: 'decision', executedBy: 'reviewer', currentnessAtAction: { release_revision: 1 }, postMoveState: { version: 2, releaseRevision: 2, eventId: 'event' } });
  expect(result.canExecute).toBe(false);
  expect(result.preview.current).toBe(false);
  expect(axios.get).toHaveBeenCalledTimes(2);
  expect(axios.post).not.toHaveBeenCalled();
});
it.each([{ request_id: 'other' }, { revision: 2 }])('rejects a receipt for a different request or packet revision: %j', async (patch) => {
  const read = axios.get.getMockImplementation();
  axios.get.mockImplementation((url, config) => url.endsWith('/receipt/') ? envelope({ ...receipt, ...patch }) : read(url, config));
  await expect(loadReleaseRequest('request', undefined, {})).rejects.toThrow('does not match');
});
it('reads only authorized metadata when expired evidence hides the packet detail', async () => {
  axios.get.mockImplementation(async (url) => {
    if (url.endsWith('/receipt/')) return envelope(receipt);
    throw Object.assign(new Error('Packet unavailable'), { response: { status: 404 } });
  });
  const result = await loadReleaseRequest('request', undefined, {});
  expect(result.receipt.requestId).toBe('request');
  expect(result.preview).toBeUndefined();
  expect(axios.post).not.toHaveBeenCalled();
});
it.each([401, 403, 500])('does not replace an authorization or transport failure (%s) with a receipt read', async (status) => {
  axios.get.mockRejectedValue(Object.assign(new Error('Unavailable'), { response: { status } }));
  await expect(loadReleaseRequest('request', undefined, {})).rejects.toThrow('Unavailable');
  expect(axios.get).toHaveBeenCalledTimes(1);
});
it('does not bypass context subject validation when packet detail is unavailable', async () => {
  axios.get.mockRejectedValue(Object.assign(new Error('Unavailable'), { response: { status: 404 } }));
  await expect(loadReleaseRequest('request', 'context', {})).rejects.toThrow('Unavailable');
  expect(axios.get).toHaveBeenCalledTimes(1);
});
it('retains the subject-scope check before reading a durable receipt', async () => {
  await expect(loadReleaseRequest('request', 'other-context', {})).rejects.toThrow('another context');
  expect(axios.get).toHaveBeenCalledTimes(1);
});
