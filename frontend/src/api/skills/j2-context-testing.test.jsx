import { createElement } from 'react';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import axios from 'src/utils/axios';
import { useContextTestSuite, useSaveContextTestSuite, contextSuiteParts } from './context-testing';
import { useAcceptContextCapture } from './context-capture';

vi.mock('src/auth/hooks', () => ({ useAuthContext: () => ({ authenticated: true, loading: false, user: { id: 'author' } }) }));
vi.mock('src/contexts/OrganizationContext', () => ({ useOrganization: () => ({ isReady: true, currentOrganizationId: 'org' }) }));
vi.mock('src/contexts/WorkspaceContext', () => ({ useWorkspace: () => ({ isReady: true, currentWorkspaceId: 'workspace' }) }));
vi.mock('src/utils/axios', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn() } }));
const id = '10000000-0000-4000-8000-000000000001';
const suite = { revision: 1, cases: [], digest: null, updated_at: null, updated_by: null, visibility: 'workspace' };
let client;
let wrapper;
beforeEach(() => {
  vi.clearAllMocks();
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  wrapper = ({ children }) => createElement(QueryClientProvider, { client }, children);
});
afterEach(() => { cleanup(); client.clear(); });
it('reads the context suite, never the isolated leaf route', async () => {
  axios.get.mockResolvedValue({ data: { result: suite } });
  const hook = renderHook(() => useContextTestSuite(id), { wrapper });
  await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
  expect(axios.get).toHaveBeenCalledWith(`/model-hub/skills/contexts/${id}/tests/`, expect.objectContaining({ headers: { 'X-Organization-Id': 'org', 'X-Workspace-Id': 'workspace' } }));
});
it('saves with the reviewed revision and invalidates only the context suite', async () => {
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  axios.put.mockResolvedValue({ data: { result: suite } });
  const hook = renderHook(() => useSaveContextTestSuite(id), { wrapper });
  await act(async () => hook.result.current.mutateAsync({ revision: 0, cases: [] }));
  expect(axios.put.mock.calls[0].slice(0, 2)).toEqual([`/model-hub/skills/contexts/${id}/tests/`, { revision: 0, cases: [] }]);
  expect(invalidate.mock.calls[0][0].queryKey.slice(-3)).toEqual(contextSuiteParts(id));
});
it('refreshes a conflicting suite without silently resubmitting the stale review', async () => {
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  const conflict = Object.assign(new Error('Suite changed'), { response: { status: 409 } });
  axios.put.mockRejectedValue(conflict);
  const hook = renderHook(() => useSaveContextTestSuite(id), { wrapper });
  await act(async () => {
    await expect(hook.result.current.mutateAsync({ revision: 0, cases: [] })).rejects.toBe(conflict);
  });
  expect(axios.put).toHaveBeenCalledTimes(1);
  expect(axios.put.mock.calls[0][1]).toEqual({ revision: 0, cases: [] });
  expect(invalidate).toHaveBeenCalledTimes(1);
  expect(invalidate.mock.calls[0][0].queryKey.slice(-3)).toEqual(contextSuiteParts(id));
});
it('capture acceptance refreshes the context suite rather than a leaf cache', async () => {
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  axios.post.mockResolvedValue({ data: { result: suite } });
  const hook = renderHook(() => useAcceptContextCapture(id), { wrapper });
  await act(async () => hook.result.current.mutateAsync({ captureId: id, sourceFingerprint: 'sha256:test', suiteRevision: 0, acknowledgeWorkspaceSharing: true, idempotencyKey: id,
    case: { id, title: 'Refund', sourceGroupId: id, prompt: 'Refund request', fixtures: [], expectedTrigger: null, reviewed: true, checks: [] } }));
  expect(invalidate.mock.calls[0][0].queryKey.slice(-3)).toEqual(contextSuiteParts(id));
});
