import { createElement } from 'react';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'src/utils/axios';
import * as api from '../../contexts';
import { mapConsumptionManifest, mapContextCredential } from '../../context-mappers';
import { skillsKeys } from '../../keys';
import wire from './wire-fixtures.json';

const state = vi.hoisted(() => ({
  auth: { authenticated: true, loading: false, user: { id: 'author' } },
  organization: { isReady: true, currentOrganizationId: 'org-a' },
  workspace: { isReady: true, currentWorkspaceId: 'ws-a' },
}));
vi.mock('src/auth/hooks', () => ({ useAuthContext: () => state.auth }));
vi.mock('src/contexts/OrganizationContext', () => ({ useOrganization: () => state.organization }));
vi.mock('src/contexts/WorkspaceContext', () => ({ useWorkspace: () => state.workspace }));
// Only HTTP is mocked. Generated paths, scoped hooks and mappers stay real.
vi.mock('src/utils/axios', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
const envelope = (result) => ({ data: { status: true, result }, status: 200 });
const page = (rows) => ({ results: rows, count: rows.length, page: 1, page_size: 25, total_pages: rows.length ? 1 : 0 });
const operation = '00000000-0000-4000-8000-000000000010';
const input = () => ({ ...mapContextCredential(wire.CredentialMetadata), idempotencyKey: operation });
let client;
let wrapper;
beforeEach(() => {
  vi.clearAllMocks();
  state.auth = { authenticated: true, loading: false, user: { id: 'author' } };
  state.organization = { isReady: true, currentOrganizationId: 'org-a' };
  state.workspace = { isReady: true, currentWorkspaceId: 'ws-a' };
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  wrapper = ({ children }) => createElement(QueryClientProvider, { client }, children);
});
afterEach(() => { cleanup(); client.clear(); });

describe('credential administration through the shared scope boundary', () => {
  it('uses the generated endpoint, pagination, identity headers and cancellation', async () => {
    axios.get.mockResolvedValue(envelope(page([wire.CredentialMetadata])));
    const { result } = renderHook(() => api.useContextCredentials({ pageSize: 25 }), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data.results[0]).toEqual(mapContextCredential(wire.CredentialMetadata));
    expect(axios.get).toHaveBeenCalledWith('/model-hub/skills/contexts/credentials/', {
      params: { page_size: 25 }, signal: expect.any(AbortSignal),
      headers: { 'X-Organization-Id': 'org-a', 'X-Workspace-Id': 'ws-a' },
    });
  });
  it('does not reinterpret forbidden or unavailable credential metadata as empty', async () => {
    axios.get.mockRejectedValue({ response: { status: 403, data: { code: 'principal_not_supported' } } });
    const { result } = renderHook(() => api.useContextCredentials(), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data).toBeUndefined();
    expect(axios.get).toHaveBeenCalledTimes(1);
  });
  it('does not request before authentication or while explicitly disabled', () => {
    const hook = renderHook(() => api.useContextCredentials({}, { enabled: false }), { wrapper });
    expect(axios.get).not.toHaveBeenCalled();
    hook.unmount();
    state.auth.authenticated = false;
    renderHook(() => api.useContextCredentials(), { wrapper });
    expect(axios.get).not.toHaveBeenCalled();
  });
  it('aborts late old-workspace responses and never repopulates the new scope', async () => {
    let finishOld;
    axios.get.mockImplementationOnce(() => new Promise((resolve) => { finishOld = resolve; }))
      .mockResolvedValueOnce(envelope(page([])));
    const hook = renderHook(() => api.useContextCredentials(), { wrapper });
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
    const signal = axios.get.mock.calls[0][1].signal;
    state.workspace = { ...state.workspace, currentWorkspaceId: 'ws-b' };
    hook.rerender();
    await waitFor(() => expect(hook.result.current.data?.count).toBe(0));
    await act(async () => finishOld(envelope(page([wire.CredentialMetadata]))));
    expect(signal.aborted).toBe(true);
    expect(hook.result.current.data.results).toEqual([]);
    const old = skillsKeys.v11({ organizationId: 'org-a', workspaceId: 'ws-a', userId: 'author' }, 'agent_context', 'credentials', {});
    expect(client.getQueryData(old)).toBeUndefined();
  });
  it('retries an unknown issuance only by an explicit resubmission of the original key', async () => {
    axios.post.mockRejectedValueOnce(new Error('Transport lost after send'))
      .mockResolvedValueOnce(envelope(wire.CredentialIssued));
    const hook = renderHook(() => api.useIssueContextCredential(), { wrapper });
    await act(async () => { await expect(hook.result.current.mutateAsync(input())).rejects.toThrow('Transport lost'); });
    expect(axios.post).toHaveBeenCalledTimes(1);
    await act(async () => { await hook.result.current.mutateAsync(input()); });
    expect(axios.post).toHaveBeenCalledTimes(2);
    for (const [url, body, config] of axios.post.mock.calls) {
      expect(url).toBe('/model-hub/skills/contexts/credentials/');
      expect(body).toEqual(wire.CredentialInput);
      expect(config.headers['Idempotency-Key']).toBe(operation);
    }
    await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
    expect(hook.result.current.data.secretAvailable).toBe(false);
    expect(hook.result.current.data.secret).toBeNull();
  });
  it('requires an original operation key before sending and does not cache a one-time secret', async () => {
    axios.post.mockResolvedValue(envelope({ ...wire.CredentialIssued, secret: 'synthetic-once', secret_available: true }));
    const hook = renderHook(() => api.useIssueContextCredential(), { wrapper });
    await act(async () => { await expect(hook.result.current.mutateAsync({ ...input(), idempotencyKey: undefined })).rejects.toThrow('Keep a UUID'); });
    expect(axios.post).not.toHaveBeenCalled();
    await act(async () => { await hook.result.current.mutateAsync(input()); });
    await waitFor(() => expect(hook.result.current.data?.secret).toBe('synthetic-once'));
    expect(JSON.stringify(client.getQueryCache().getAll())).not.toContain('synthetic-once');
    act(() => hook.result.current.reset());
    await waitFor(() => expect(hook.result.current.data).toBeUndefined());
  });
  it('suppresses a late mutation result after the principal changes', async () => {
    let finish;
    axios.post.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const hook = renderHook(() => api.useIssueContextCredential(), { wrapper });
    let pending;
    act(() => { pending = hook.result.current.mutateAsync(input()).catch((error) => error); });
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    state.auth = { ...state.auth, user: { id: 'reviewer' } };
    hook.rerender();
    await act(async () => finish(envelope({ ...wire.CredentialIssued, secret: 'late-private-secret', secret_available: true })));
    expect((await pending).message).toContain('scope changed');
    expect(hook.result.current.data).toBeUndefined();
  });
  it('revokes the exact generated UUID route and invalidates only credential metadata', async () => {
    axios.delete.mockResolvedValue({ status: 204 });
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const hook = renderHook(() => api.useRevokeContextCredential(), { wrapper });
    await act(async () => { await hook.result.current.mutateAsync({ credentialId: wire.CredentialMetadata.id }); });
    expect(axios.delete.mock.calls[0][0]).toBe(`/model-hub/skills/contexts/credentials/${wire.CredentialMetadata.id}/`);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: skillsKeys.v11({ organizationId: 'org-a', workspaceId: 'ws-a', userId: 'author' }, 'agent_context', 'credentials') });
  });
});

describe('bounded Context-credential reads', () => {
  const credential = 'synthetic-isolated-credential';
  const snapshot = () => mapConsumptionManifest(wire.ConsumptionManifest);
  it('resolves default production without adding a human identity or caching the credential', async () => {
    axios.get.mockResolvedValue(envelope(wire.ConsumptionManifest));
    const controller = new AbortController();
    const result = await api.resolveContext({ credential, name: 'synthetic-agent', signal: controller.signal });
    expect(result).toEqual(snapshot());
    const [url, config] = axios.get.mock.calls[0];
    expect(url).toBe('/model-hub/skills/contexts/resolve/?name=synthetic-agent');
    expect(config.signal).toBe(controller.signal);
    expect(config.headers).toEqual({ Authorization: `Context ${credential}`, 'X-Organization-Id': null, 'X-Workspace-Id': null });
    expect(result).not.toHaveProperty('credential');
  });
  it('continues the exact version and lease, not a newly moved label', async () => {
    axios.get.mockResolvedValue(envelope({ ...wire.ConsumptionManifest, file_offset: 25, next_file_offset: null }));
    const result = await api.readContextManifestPage({ credential, snapshot: snapshot(), fileOffset: 25 });
    const [url, config] = axios.get.mock.calls[0];
    expect(url).toBe(`/model-hub/skills/contexts/${snapshot().resourceId}/versions/1/manifest/?file_offset=25&file_limit=25`);
    expect(config.headers['X-Context-Resolution']).toBe(snapshot().resolutionId);
    expect(result.nextFileOffset).toBeNull();
    expect(result.versionId).toBe(snapshot().versionId);
  });
  it('maps the actual resource_id chunk shape without rejecting escaped short chunks', async () => {
    axios.get.mockResolvedValue(envelope(wire.ConsumptionChunk));
    const result = await api.readContextFileChunk({ credential, snapshot: snapshot(), path: 'AGENTS.md' });
    expect(result.text).toBe('"é');
    expect(result.nextOffset).toBe(2);
    expect(axios.get.mock.calls[0][0]).toContain('/files/?path=AGENTS.md&offset=0&max_bytes=32768');
  });
  it('sends access checks through the same credential-bound continuation', async () => {
    axios.post.mockResolvedValue(envelope(wire.AccessCheck));
    expect((await api.checkContextAccess({ credential, snapshot: snapshot() })).allowed).toBe(true);
    expect(axios.post.mock.calls[0][1]).toEqual({});
    expect(axios.post.mock.calls[0][2].headers['X-Context-Resolution']).toBe(snapshot().resolutionId);
  });
  it('rejects a changed version, kind or resolution instead of splicing snapshots', async () => {
    axios.get.mockResolvedValueOnce(envelope({ ...wire.ConsumptionChunk, version_id: wire.ConsumptionManifest.resource_id }))
      .mockResolvedValueOnce(envelope({ ...wire.ConsumptionManifest, resolution_id: wire.ConsumptionManifest.resource_id }))
      .mockResolvedValueOnce(envelope({ ...wire.ConsumptionManifest, kind: 'skill' }));
    await expect(api.readContextFileChunk({ credential, snapshot: snapshot(), path: 'AGENTS.md' })).rejects.toThrow('does not match');
    await expect(api.readContextManifestPage({ credential, snapshot: snapshot() })).rejects.toThrow('resolution changed');
    await expect(api.resolveContext({ credential, name: 'synthetic-agent' })).rejects.toThrow('exact resolved agent context');
  });
  it('fails closed for missing leases, denial and aborted late replies without retry', async () => {
    await expect(api.readContextManifestPage({ credential, snapshot: { ...snapshot(), resolutionId: undefined } })).rejects.toThrow('exact resolved');
    expect(axios.get).not.toHaveBeenCalled();
    const denied = { response: { status: 410, data: { code: 'resolution_expired' } } };
    axios.get.mockRejectedValueOnce(denied);
    await expect(api.readContextManifestPage({ credential, snapshot: snapshot() })).rejects.toBe(denied);
    expect(axios.get).toHaveBeenCalledTimes(1);
    const controller = new AbortController();
    axios.get.mockImplementationOnce(async () => { controller.abort(); return envelope(wire.ConsumptionManifest); });
    await expect(api.resolveContext({ credential, name: 'synthetic-agent', signal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' });
  });
});
