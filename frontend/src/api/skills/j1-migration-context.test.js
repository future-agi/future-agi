import { afterEach, expect, it, vi } from 'vitest';
import { pinnedLinks, contextProposalFromPacket, prepareMigrationContext, publishMigrationContext } from './j1-migration-context';

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), review: vi.fn() }));
vi.mock('src/utils/axios', () => ({ default: { get: http.get, post: http.post } }));
vi.mock('./context-production', () => ({ loadContextReview: http.review }));
afterEach(() => vi.clearAllMocks());

const digest = (c) => `sha256:${c.repeat(64)}`;
const ok = (result) => ({ data: { status: true, result } });
const draftWire = (revision, extra = {}) => ({ id: 'draft', revision, base_version: null, files: [{ path: 'AGENTS.md', content: 'Root\n' }], note: '',
  context_spec: { schema: 1, description: 'Trial', links: [] }, resolved_lock: { schema: 1, links: [] }, digest: digest('d'), recovery_digest: digest('r'),
  checks: { summary: { errors: 0, warnings: 0 }, items: [] }, updated_at: '2026-11-11T00:00:00Z', expected_head_version: null, head_acknowledged: false, review_fingerprint: '', ...extra });
const keys = { create: '00000000-0000-4000-8000-000000000001', apply: '00000000-0000-4000-8000-000000000002', publish: '00000000-0000-4000-8000-000000000003' };
const links = [{ skillId: 'one', selector: { version: 1 } }];
const prepared = { contextId: 'ctx', name: 'trial', links, preview: { revision: 1, fingerprint: digest('f'), candidateDigest: digest('c'), resolvedLock: { links: [] }, checks: { summary: { errors: 0, warnings: 0 }, items: [] } } };
const review = (candidate) => ({ candidateDigest: candidate, checks: { summary: { errors: 0, warnings: 0 }, items: [] }, releaseState: { permissions: { canIssueCredentials: true } },
  review: { draftRevision: 2, candidateDigest: candidate, expectedHeadVersion: null, reviewFingerprint: digest('1'), baseVersion: null } });

it('pins each published skill once to its exact version and reads only well-formed proposals', () => {
  expect(pinnedLinks([{ skillId: 'one', version: 1 }, { skillId: 'one', version: 1 }, { skillId: 'bad', version: 0 }, { skillId: 'two', version: 3 }]))
    .toEqual([{ skillId: 'one', selector: { version: 1 } }, { skillId: 'two', selector: { version: 3 } }]);
  expect(contextProposalFromPacket({ context: { name: 'trial', description: 'Trial', agents_md: 'Root\n' } })).toEqual({ name: 'trial', description: 'Trial', agents: 'Root\n' });
  expect(contextProposalFromPacket({ context: { name: 'trial' } })).toBeNull();
});

it('prepares a private draft and only previews the pinned links', async () => {
  http.post.mockResolvedValueOnce(ok({ context_id: 'ctx', draft_id: 'draft', revision: 1, digest: digest('d') }))
    .mockResolvedValueOnce(ok({ revision: 1, fingerprint: digest('f'), candidate_digest: digest('c'), resolved_lock: { schema: 1, links: [] }, changes: [], checks: { summary: { errors: 0, warnings: 0 }, items: [] } }));
  http.get.mockResolvedValueOnce(ok(draftWire(1)));
  const result = await prepareMigrationContext({ name: 'trial', description: 'Trial', agents: 'Root\n', links, keys }, { headers: {} });
  expect(http.post.mock.calls[0][1]).toEqual({ name: 'trial', description: 'Trial', tags: [], files: [{ path: 'AGENTS.md', content: 'Root\n' }] });
  expect(http.post.mock.calls[0][2].headers['Idempotency-Key']).toBe(keys.create);
  expect(http.post.mock.calls[1][0]).toMatch(/dependencies\/preview\/$/);
  expect(http.post).toHaveBeenCalledTimes(2);
  expect(result.preview.candidateDigest).toBe(digest('c'));
});

it('publishes only the reviewed compiled candidate through an acknowledged head', async () => {
  http.post.mockResolvedValueOnce(ok(draftWire(2, { files: [{ path: 'AGENTS.md', content: 'Root\n' }] })))
    .mockResolvedValueOnce(ok(draftWire(3, { head_acknowledged: true, review_fingerprint: digest('2') })))
    .mockResolvedValueOnce(ok({ release_revision: 1, version: { id: 'v1', number: 1, digest: digest('c') }, unchanged: false, checks: { summary: { errors: 0, warnings: 0 }, items: [] } }));
  http.review.mockResolvedValueOnce(review(digest('c')));
  const result = await publishMigrationContext({ prepared, acknowledgeWarnings: false, keys }, { headers: {} });
  expect(http.post.mock.calls[0][1]).toMatchObject({ revision: 1, expected_fingerprint: digest('f') });
  expect(http.post.mock.calls[1][0]).toMatch(/acknowledge-head\/$/);
  expect(http.post.mock.calls[1][1]).toEqual({ revision: 2, expected_digest: digest('c'), expected_head_version: null, review_fingerprint: digest('1') });
  expect(http.post.mock.calls[2][1]).toMatchObject({ revision: 3, expected_digest: digest('c'), review_fingerprint: digest('2'), acknowledge_warnings: false });
  expect(http.post.mock.calls[2][2].headers['Idempotency-Key']).toBe(keys.publish);
  expect(result).toMatchObject({ contextId: 'ctx', canIssueCredentials: true });
});

it('stops before acknowledging or publishing when the compiled candidate differs from the reviewed one', async () => {
  http.post.mockResolvedValueOnce(ok(draftWire(2)));
  http.review.mockResolvedValueOnce(review(digest('e')));
  await expect(publishMigrationContext({ prepared, acknowledgeWarnings: false, keys }, { headers: {} })).rejects.toThrow(/differs from the one you reviewed/);
  expect(http.post).toHaveBeenCalledTimes(1);
});

it('requires an explicit warning acknowledgment for a candidate with warnings', async () => {
  http.post.mockResolvedValueOnce(ok(draftWire(2)));
  http.review.mockResolvedValueOnce({ ...review(digest('c')), checks: { summary: { errors: 0, warnings: 1 }, items: [] } });
  await expect(publishMigrationContext({ prepared, acknowledgeWarnings: false, keys }, { headers: {} })).rejects.toThrow(/Acknowledge the warnings/);
  expect(http.post).toHaveBeenCalledTimes(1);
});
