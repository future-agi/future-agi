import { beforeEach, expect, it, vi } from 'vitest';
import axios from 'src/utils/axios';
import { loadContextReview } from './context-production';

vi.mock('src/utils/axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));

// The release review names three or fewer affected apps from the Connected apps rows of the same
// bindings. The names are display only: a failed or partial read never changes what was reviewed.
const fingerprint = `sha256:${'a'.repeat(64)}`;
const follower = { id: 'binding-follow', revision: 1, mode: 'new_thread', selector: { label: 'production' }, configuration_digest: fingerprint };
const pinned = { id: 'binding-pin', revision: 2, mode: 'manual', selector: { version: 2 }, configuration_digest: fingerprint };
const snapshot = (impact) => ({
  review_fingerprint: fingerprint, candidate: { kind: 'version', version: 2, digest: fingerprint },
  changes: { from: 1, files: [], total_pages: 1 }, head_changes: { from: 2, files: [], total_pages: 1 }, latest_version: 2,
  release_state: { latest_version: 2, release_revision: 1, security_revision: 0, protected_label_names: [], labels: [{ name: 'production', version: 1, can_move: true }], permissions: { can_promote: true } },
  checks: { items: [] }, head_review: null,
  impact: { as_of: '2026-11-15T00:00:00Z', scope: 'known_references', following: 1, pinned: 1, unobserved: 1, more_available: false, fingerprint, ...impact },
});
const row = (binding, name, age) => ({ binding: { ...binding, context_id: 'context', name, kind: 'runtime' }, observation_age_seconds: age, states: ['unknown'] });
const move = { operation: 'move', target_version: 2, label: 'production' };
let consumers;
beforeEach(() => {
  vi.clearAllMocks();
  consumers = { results: [row(pinned, 'acme-support application', null), row(follower, 'acme-support production agent', 4)], count: 2, page: 1, page_size: 25, total_pages: 1, as_of: '2026-11-15T00:00:00Z', coverage: 'declared_bindings' };
  axios.get.mockImplementation(async (url) => {
    if (url.endsWith('/consumers/')) return { data: { result: consumers } };
    throw new Error(`unexpected ${url}`);
  });
});

it('names each affected app, with its report status, for a label move of three apps or fewer', async () => {
  axios.post.mockResolvedValue({ data: { result: snapshot({ bindings: [follower, pinned] }) } });
  const review = await loadContextReview('context', null, { headers: {} }, move);
  expect(review.impact.apps).toEqual([
    { id: 'binding-follow', name: 'acme-support production agent', selector: { label: 'production' }, reported: true },
    { id: 'binding-pin', name: 'acme-support application', selector: { version: 2 }, reported: false },
  ]);
  // Counts, fingerprint and the compared base are the server's, unchanged.
  expect(review.impact).toMatchObject({ following: 1, pinned: 1, unobserved: 1, fingerprint, moreAvailable: false });
  expect(review.changesFrom).toBe(1);
});

it('keeps counts only when a name is missing, the read fails, or more than three apps are affected', async () => {
  axios.post.mockResolvedValue({ data: { result: snapshot({ bindings: [follower, pinned] }) } });
  consumers.results = consumers.results.slice(0, 1);
  expect((await loadContextReview('context', null, { headers: {} }, move)).impact.apps).toBeUndefined();
  axios.get.mockRejectedValue(new Error('offline'));
  const failed = await loadContextReview('context', null, { headers: {} }, move);
  expect(failed.impact.apps).toBeUndefined();
  expect(failed.impact.fingerprint).toBe(fingerprint);
  const many = [1, 2, 3, 4].map((n) => ({ ...follower, id: `binding-${n}` }));
  axios.post.mockResolvedValue({ data: { result: snapshot({ bindings: many, following: 4, pinned: 0, unobserved: 0 }) } });
  axios.get.mockClear();
  expect((await loadContextReview('context', null, { headers: {} }, move)).impact.apps).toBeUndefined();
  expect(axios.get.mock.calls.some(([url]) => url.endsWith('/consumers/'))).toBe(false);
});
