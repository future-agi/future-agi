import { createElement } from 'react';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import axios from 'src/utils/axios';
import { contextTestCandidate, useCreateContextTest, useContextTestRun, useContextTestAttempt } from './context-testing';
import { useContextDependents } from './context-dependents';

const state = vi.hoisted(() => ({ workspace: 'a' }));
vi.mock('src/auth/hooks', () => ({ useAuthContext: () => ({ authenticated: true, user: { id: 'author' } }) }));
vi.mock('src/contexts/OrganizationContext', () => ({ useOrganization: () => ({ isReady: true, currentOrganizationId: 'org' }) }));
vi.mock('src/contexts/WorkspaceContext', () => ({ useWorkspace: () => ({ isReady: true, currentWorkspaceId: state.workspace }) }));
vi.mock('src/utils/axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
const envelope = (result) => ({ data: { result } });
const digest = `sha256:${'a'.repeat(64)}`;
let client;
let wrapper;
beforeEach(() => {
  vi.clearAllMocks(); state.workspace = 'a';
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  wrapper = ({ children }) => createElement(QueryClientProvider, { client }, children);
});
afterEach(() => { cleanup(); client.clear(); });
it('uses the server compiled digest rather than hashing only owned files', () => {
  expect(contextTestCandidate({ revision: 7, digest, files: [{ path: 'AGENTS.md', content: 'root' }] })).toEqual({ kind: 'draft', revision: 7, digest });
  expect(() => contextTestCandidate({ revision: 7, files: [] })).toThrow('Save the complete context');
});
it('admits only on the context route with an exact fingerprint and retained operation key', async () => {
  axios.post.mockResolvedValue(envelope({ id: 'job', status: 'queued' }));
  const hook = renderHook(() => useCreateContextTest('parent'), { wrapper });
  const configuration = { harness: 'contexts-text-v1.2', candidate: contextTestCandidate({ revision: 7, digest }) };
  await act(() => hook.result.current.mutateAsync({ configuration, expectedFingerprint: 'reviewed', idempotencyKey: '00000000-0000-4000-8000-000000000001', inventoryReview: { fingerprint: 'reviewed', reviewedAllFiles: true, noKnownAnswerKeys: true, classifications: [], falsePositiveSecretFindings: [] } }));
  expect(axios.post.mock.calls[0][0]).toBe('/model-hub/skills/contexts/parent/test-runs/');
  expect(axios.post.mock.calls[0][1]).toMatchObject({ configuration, expected_fingerprint: 'reviewed', inventory_review: { fingerprint: 'reviewed' } });
  expect(axios.post.mock.calls[0][2].headers).toMatchObject({ 'X-Workspace-Id': 'a', 'Idempotency-Key': '00000000-0000-4000-8000-000000000001' });
});
it('retains context-only reference arms and root delivery evidence', async () => {
  const value = { id: 'run', status: 'completed', reference: { version: 4 }, routing_verdict: 'not_applicable', root_fully_delivered: true, receipts: [{ source_version_id: 'accepted-v8' }] };
  axios.get.mockResolvedValue(envelope(value));
  const run = renderHook(() => useContextTestRun('parent', 'run'), { wrapper });
  const attempt = renderHook(() => useContextTestAttempt('parent', 'run', 'attempt'), { wrapper });
  await waitFor(() => expect(attempt.result.current.data).toEqual(value));
  expect(run.result.current.data.reference.version).toBe(4);
  expect(axios.get.mock.calls.map(([url]) => url)).toContain('/model-hub/skills/contexts/parent/test-runs/run/attempts/attempt/');
});
it('fences delayed dependent reads on workspace changes and keeps history pagination explicit', async () => {
  let resolveOld;
  axios.get.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; })).mockResolvedValue(envelope({ results: [], count: 0 }));
  const hook = renderHook(() => useContextDependents('child', { view: 'history', page: 2 }), { wrapper });
  await waitFor(() => expect(axios.get).toHaveBeenCalledOnce());
  const signal = axios.get.mock.calls[0][1].signal;
  expect(axios.get.mock.calls[0][1].params).toEqual({ view: 'history', page: 2, page_size: 25 });
  state.workspace = 'b'; hook.rerender();
  await waitFor(() => expect(hook.result.current.data?.count).toBe(0));
  await act(async () => resolveOld(envelope({ results: [{ name: 'private old parent' }], count: 1 })));
  expect(signal.aborted).toBe(true);
  expect(hook.result.current.data.results).toEqual([]);
});
