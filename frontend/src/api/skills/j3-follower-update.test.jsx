import { createElement } from 'react';
import { act, cleanup, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import axios from 'src/utils/axios';
import { useFollowerUpdates } from './context-follower-update';

vi.mock('src/auth/hooks', () => ({ useAuthContext: () => ({ authenticated: true, user: { id: 'author' } }) }));
vi.mock('src/contexts/OrganizationContext', () => ({ useOrganization: () => ({ isReady: true, currentOrganizationId: 'org' }) }));
vi.mock('src/contexts/WorkspaceContext', () => ({ useWorkspace: () => ({ isReady: true, currentWorkspaceId: 'ws' }) }));
vi.mock('src/utils/axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
const envelope = (result) => ({ data: { result } });
const digest = `sha256:${'a'.repeat(64)}`;
const lock = (version) => ({ schema: 1, links: [{ skill_id: 'child', mount: 'skills/policy/', version, version_id: `v${version}`, digest, requested: { label: 'production' } }] });
const draft = (id, base, revision = 1, version = 7) => ({ id: `draft-${id}`, revision, base_version: base, files: [{ path: 'AGENTS.md', content: '# root' }], note: '', context_spec: { schema: 1, description: 'Purpose', links: [{ skill_id: 'child', selector: { label: 'production' } }] }, resolved_lock: lock(version), digest, recovery_digest: digest, checks: { items: [], summary: { errors: 0, warnings: 0 } }, updated_at: 'now', expected_head_version: base, head_acknowledged: false, review_fingerprint: null });
const preview = (revision, fingerprint) => ({ revision, fingerprint, candidate_digest: digest, resolved_lock: lock(8), checks: { items: [], summary: { errors: 0, warnings: 0 } }, changes: [{ skill_id: 'child', mount: 'skills/policy/', change: 'updated', before: { skill_id: 'child', mount: 'skills/policy/', version: 7, version_id: 'v7', digest, requested: { label: 'production' } }, after: { skill_id: 'child', mount: 'skills/policy/', version: 8, version_id: 'v8', digest, requested: { label: 'production' } }, upstream_version: 8 }] });
const notFound = Object.assign(new Error('missing'), { code: 'draft_not_found', statusCode: 404 });
const target = (id, version = 1) => ({ contextId: id, name: id, version });
let client;
let wrapper;
beforeEach(() => {
  vi.clearAllMocks();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  wrapper = ({ children }) => createElement(QueryClientProvider, { client }, children);
});
afterEach(() => { cleanup(); client.clear(); });

it('starts a draft only from the viewed release, previews each context separately, and never replaces a different-origin draft', async () => {
  axios.get.mockImplementation(async (url) => {
    if (url.includes('/contexts/a/draft/')) throw notFound;
    if (url.includes('/contexts/b/draft/')) return envelope(draft('b', 1));
    if (url.includes('/contexts/c/draft/')) return envelope(draft('c', 5));
    throw new Error(`Unexpected read ${url}`);
  });
  axios.post.mockImplementation(async (url) => {
    if (url.includes('/contexts/a/draft/start/')) return envelope(draft('a', 1));
    if (url.includes('/dependencies/preview/')) return envelope(preview(1, url.includes('/contexts/a/') ? 'fp-a' : 'fp-b'));
    throw new Error(`Unexpected write ${url}`);
  });
  const hook = renderHook(() => useFollowerUpdates('child'), { wrapper });
  let items;
  await act(async () => { items = await hook.result.current.prepare([target('a'), target('b'), target('c')]); });
  expect(items.map((item) => item.status)).toEqual(['ready', 'ready', 'error']);
  expect(items[2]).toMatchObject({ code: 'different_origin' });
  const start = axios.post.mock.calls.find(([url]) => url.includes('/contexts/a/draft/start/'));
  expect(start[1]).toEqual({ revision: 0, from_version: 1 });
  expect(start[2].headers['Idempotency-Key']).toMatch(/^[0-9a-f-]{36}$/);
  const previews = axios.post.mock.calls.filter(([url]) => url.includes('/dependencies/preview/'));
  expect(previews).toHaveLength(2);
  expect(previews[0][1]).toMatchObject({ revision: 1, refresh_skill_ids: ['child'] });
  expect(axios.post.mock.calls.some(([url]) => url.includes('/contexts/c/draft/start/') || url.includes('/contexts/c/dependencies/'))).toBe(false);
});

it('applies each context with its own fingerprint and reports a stale plan only for that context', async () => {
  axios.get.mockImplementation(async (url) => {
    if (url.includes('/contexts/a/draft/')) return envelope(draft('a', 1, 2, 8));
    throw new Error(`Unexpected read ${url}`);
  });
  axios.post.mockImplementation(async (url) => {
    if (url.includes('/contexts/a/dependencies/apply/')) return envelope({ context_id: 'a', draft_id: 'draft-a', revision: 2, digest });
    if (url.includes('/contexts/b/dependencies/apply/')) throw Object.assign(new Error('stale'), { code: 'stale_plan', statusCode: 409 });
    throw new Error(`Unexpected write ${url}`);
  });
  const hook = renderHook(() => useFollowerUpdates('child'), { wrapper });
  const links = [{ skillId: 'child', selector: { label: 'production' } }];
  let outcomes;
  await act(async () => {
    outcomes = await hook.result.current.apply([
      { target: target('a'), links, plan: { revision: 1, fingerprint: 'fp-a' } },
      { target: target('b'), links, plan: { revision: 1, fingerprint: 'fp-b' } },
    ]);
  });
  expect(outcomes.map((item) => [item.target.contextId, item.status])).toEqual([['a', 'applied'], ['b', 'error']]);
  expect(outcomes[1]).toMatchObject({ code: 'stale_plan', httpStatus: 409 });
  const [aBody, bBody] = axios.post.mock.calls.map(([, body]) => body);
  expect(aBody.expected_fingerprint).toBe('fp-a');
  expect(bBody.expected_fingerprint).toBe('fp-b');
});
