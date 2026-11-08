import { createElement } from 'react';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import axios from 'src/utils/axios';
import { skillsKeys } from '../../keys';
import * as api from '../../context-campaigns';

const state = vi.hoisted(() => ({ ready: true, auth: { authenticated: true, loading: false, user: { id: 'author' } }, organization: { isReady: true, currentOrganizationId: 'org' }, workspace: { isReady: true, currentWorkspaceId: 'old' } }));
vi.mock('src/auth/hooks', () => ({ useAuthContext: () => state.auth }));
vi.mock('src/contexts/OrganizationContext', () => ({ useOrganization: () => state.organization }));
vi.mock('src/contexts/WorkspaceContext', () => ({ useWorkspace: () => state.workspace }));
vi.mock('src/utils/axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
vi.mock('src/api/contracts/api-surface', () => ({
  isContractedApiPath: () => state.ready,
  apiPath: (path, params = {}) => path.replace(/\{([^}]+)\}/g, (_, name) => encodeURIComponent(params[name])),
}));
const wire = { id: 'campaign', name: 'Synthetic campaign', revision: 3, status: 'draft', target_count: 40,
  manifest_digest: 'sha256:plan', parent_campaign_id: null, authorized_at: null, reason: '', created_at: '2026-11-08T00:00:00Z', provenance: 'client_reported' };
const envelope = (result) => ({ data: { status: true, result } });
let client;
let wrapper;
beforeEach(() => {
  vi.clearAllMocks(); state.ready = true; state.workspace.currentWorkspaceId = 'old';
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  wrapper = ({ children }) => createElement(QueryClientProvider, { client }, children);
});
afterEach(() => { cleanup(); client.clear(); });

it('keeps the exact fixed count and provenance in mapped output', () => {
  expect(api.mapCampaign(wire)).toMatchObject({ targetCount: 40, manifestDigest: 'sha256:plan', provenance: 'client_reported' });
});
it('does not query missing generated campaign contracts', async () => {
  state.ready = false;
  const { result } = renderHook(() => api.useContextCampaigns(), { wrapper });
  expect(result.current.fetchStatus).toBe('idle');
  expect(axios.get).not.toHaveBeenCalled();
});
it('forwards revision/digest/consent and does not replace decisions', async () => {
  axios.post.mockResolvedValue(envelope({ ...wire, status: 'authorized' }));
  const { result } = renderHook(() => api.useAuthorizeContextCampaign('campaign'), { wrapper });
  await act(async () => result.current.mutateAsync({ revision: 3, expectedManifestDigest: 'sha256:plan', acknowledgeRepositoryWrites: true,
    decisions: [{ bindingId: 'binding', planDigest: null, decision: 'exclude', reason: 'Unavailable repository' }] }));
  expect(axios.post.mock.calls[0][1]).toEqual({ revision: 3, expected_manifest_digest: 'sha256:plan', acknowledge_repository_writes: true,
    decisions: [{ binding_id: 'binding', plan_digest: null, decision: 'exclude', reason: 'Unavailable repository' }] });
});
it('retains the caller operation key when creation is retried after a lost response', async () => {
  axios.post.mockRejectedValueOnce(new Error('lost response')).mockResolvedValueOnce(envelope(wire));
  const { result } = renderHook(() => api.useCreateContextCampaign(), { wrapper });
  const input = { name: 'Synthetic campaign', targets: [{ bindingId: 'binding', version: 4 }], idempotencyKey: '00000000-0000-4000-8000-000000000001' };
  await act(async () => { await expect(result.current.mutateAsync(input)).rejects.toThrow('lost response'); });
  await act(async () => result.current.mutateAsync(input));
  expect(axios.post.mock.calls.map((args) => args[2].headers['Idempotency-Key'])).toEqual([input.idempotencyKey, input.idempotencyKey]);
});
it('does not populate the new workspace from a late target query', async () => {
  let resolveOld;
  axios.get.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; })).mockResolvedValueOnce(envelope({ results: [], count: 0, page: 1, page_size: 25, total_pages: 0 }));
  const { result, rerender } = renderHook(() => api.useContextCampaigns(), { wrapper });
  await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
  const signal = axios.get.mock.calls[0][1].signal;
  state.workspace.currentWorkspaceId = 'new'; rerender();
  await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(2));
  await act(async () => resolveOld(envelope({ results: [wire], count: 1, page: 1, page_size: 25, total_pages: 1 })));
  await waitFor(() => expect(result.current.data?.count).toBe(0));
  expect(signal.aborted).toBe(true);
  expect(client.getQueryData(skillsKeys.v11({ organizationId: 'org', workspaceId: 'old', userId: 'author' }, 'contexts', 'campaigns', {}))).toBeUndefined();
});
