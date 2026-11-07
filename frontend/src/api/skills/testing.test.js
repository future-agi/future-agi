import { createElement } from 'react';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'src/utils/axios';
import { skillsKeys } from './keys';
import * as api from './testing';

const state = vi.hoisted(() => ({
  auth: { authenticated: true, loading: false, user: { id: 'author' } },
  organization: { isReady: true, currentOrganizationId: 'org-a' },
  workspace: { isReady: true, currentWorkspaceId: 'ws-a' },
}));
vi.mock('src/auth/hooks', () => ({ useAuthContext: () => state.auth }));
vi.mock('src/contexts/OrganizationContext', () => ({ useOrganization: () => state.organization }));
vi.mock('src/contexts/WorkspaceContext', () => ({ useWorkspace: () => state.workspace }));
vi.mock('src/utils/axios', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }));
// The single integration owner adds these two shared seams and generates paths.
vi.mock('./keys', () => ({ skillsKeys: { v11: (scope, ...parts) => ['skills', 'v11', scope.organizationId, scope.workspaceId, scope.userId, ...parts] } }));
vi.mock('./endpoints', () => ({ endpoints: {
  testSuite: (id) => `/skills/${id}/tests/`, testRuns: (id) => `/skills/${id}/test-runs/`,
  testRunPreview: (id) => `/skills/${id}/test-runs/preview/`,
  cancelTestRun: (id, run) => `/skills/${id}/test-runs/${run}/cancel/`,
  shareTestRun: (id, run) => `/skills/${id}/test-runs/${run}/share/`,
} }));
const envelope = (result) => ({ data: { status: true, result } });
const suite = { revision: 0, digest: null, cases: [], updated_at: null, updated_by: null, visibility: 'workspace' };
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

const configuration = () => ({
  candidate: { kind: 'draft', revision: 3, digest: 'sha256:draft' },
  cases: { kind: 'saved', suiteRevision: 4, caseIds: ['case'] },
  baselines: ['without_target'], competitors: [], model: { modelId: 'fixture-model', temperature: 0, maxOutputTokens: 100 },
  judge: null, profile: 'quick', repetitions: 1, purpose: 'regression', parentRunId: null,
  limits: { maxTotalTokens: 2000, maxEstimatedCostUsd: '1.00000000' },
});
const request = () => ({
  idempotencyKey: '00000000-0000-4000-8000-000000000001', configuration: configuration(), expectedFingerprint: 'plan', acknowledgeProviderProcessing: true,
  inventoryReview: { fingerprint: 'plan', reviewedAllFiles: true, noKnownAnswerKeys: true, classifications: [], falsePositiveSecretFindings: [] },
});

describe('principal-scoped reads', () => {
  it('carries explicit tenant headers and an AbortSignal and maps the absent suite', async () => {
    axios.get.mockResolvedValue(envelope(suite));
    const { result } = renderHook(() => api.useSkillTestSuite('skill'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toMatchObject({ revision: 0, updatedAt: null });
    expect(axios.get).toHaveBeenCalledWith('/skills/skill/tests/', { params: undefined, signal: expect.any(AbortSignal), headers: { 'X-Organization-Id': 'org-a', 'X-Workspace-Id': 'ws-a' } });
  });
  it('does not read before authentication and both scopes are ready', () => {
    state.auth.authenticated = false;
    renderHook(() => api.useSkillTestSuite('skill'), { wrapper });
    expect(axios.get).not.toHaveBeenCalled();
  });
  it('aborts and removes previous-principal data; a late response cannot replace current data', async () => {
    let completeOld;
    axios.get.mockImplementationOnce(() => new Promise((resolve) => { completeOld = resolve; }))
      .mockResolvedValueOnce(envelope({ ...suite, revision: 7 }));
    const { result, rerender } = renderHook(() => api.useSkillTestSuite('skill'), { wrapper });
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
    const oldSignal = axios.get.mock.calls[0][1].signal;
    state.workspace = { ...state.workspace, currentWorkspaceId: 'ws-b' };
    state.auth = { ...state.auth, user: { id: 'another-author' } };
    rerender();
    await waitFor(() => expect(result.current.data?.revision).toBe(7));
    await act(async () => { completeOld(envelope({ ...suite, revision: 99 })); });
    expect(oldSignal.aborted).toBe(true);
    expect(result.current.data.revision).toBe(7);
    expect(client.getQueryData(skillsKeys.v11({ organizationId: 'org-a', workspaceId: 'ws-a', userId: 'author' }, 'skill', 'suite'))).toBeUndefined();
  });
  it('does not reinterpret a private-resource denial as an empty suite', async () => {
    axios.get.mockRejectedValue({ code: 'resource_not_found' });
    const { result } = renderHook(() => api.useSkillTestSuite('foreign'), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data).toBeUndefined();
  });
});

describe('paid starts and polling', () => {
  it('preserves the caller key on explicit retry and never auto-retries paid mutations', async () => {
    axios.post.mockRejectedValueOnce(new Error('Response lost')).mockResolvedValueOnce(envelope({ id: 'run', status: 'queued', poll_after_ms: 2000, deadline_at: 'date', visibility: 'private', attempt_count: 2, execution_fingerprint: 'execution' }));
    const { result } = renderHook(() => api.useCreateSkillTestRun('skill'), { wrapper });
    const input = request();
    await act(async () => { await expect(result.current.mutateAsync(input)).rejects.toThrow('Response lost'); });
    expect(axios.post).toHaveBeenCalledTimes(1);
    await act(async () => { await result.current.mutateAsync(input); });
    expect(axios.post.mock.calls.map((call) => call[2].headers['Idempotency-Key'])).toEqual([input.idempotencyKey, input.idempotencyKey]);
    expect(axios.post.mock.calls[1][1]).toMatchObject({ expected_fingerprint: 'plan', inventory_review: { fingerprint: 'plan' }, configuration: { candidate: { revision: 3 }, limits: { max_estimated_cost_usd: '1.00000000' } } });
    expect(result.current.data.executionFingerprint).toBe('execution');
  });
  it('refuses a missing key rather than making an unrepeatable paid request', async () => {
    const { result } = renderHook(() => api.useCreateSkillTestRun('skill'), { wrapper });
    await act(async () => { await expect(result.current.mutateAsync({ ...request(), idempotencyKey: undefined })).rejects.toThrow('idempotencyKey'); });
    expect(axios.post).not.toHaveBeenCalled();
  });
  it('rejects the published target as a competitor before preview and start', async () => {
    const config = { ...configuration(), competitors: [{ skillId: 'skill', version: 2 }] };
    const preview = renderHook(() => api.usePreviewSkillTestRun('skill'), { wrapper });
    const start = renderHook(() => api.useCreateSkillTestRun('skill'), { wrapper });
    await act(async () => { await expect(preview.result.current.mutateAsync(config)).rejects.toThrow('competitor'); });
    await act(async () => { await expect(start.result.current.mutateAsync({ ...request(), configuration: config })).rejects.toThrow('competitor'); });
    expect(axios.post).not.toHaveBeenCalled();
  });
  it.each(['completed', 'failed', 'cancelled', 'timed_out', 'interrupted', 'unknown_future_status'])('stops polling for %s', (status) => {
    expect(api.skillJobPollInterval({ state: { data: { status } } })).toBe(false);
  });
  it.each(['queued', 'running', 'cancelling'])('polls only the compact summary while %s', (status) => {
    expect(api.skillJobPollInterval({ state: { data: { status } } })).toBe(2000);
  });
  it('invalidates only the result, run-list and evidence families on terminal observation', async () => {
    axios.get.mockResolvedValue(envelope({ id: 'run', status: 'interrupted' }));
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const { result } = renderHook(() => api.useSkillV11Query({ parts: ['skill', 'run', 'run'], url: () => '/run/', mapper: (value) => value, poll: true, terminalParts: [['skill', 'results', 'run'], ['skill', 'runs'], ['skill', 'evidence']] }), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(invalidate.mock.calls.map(([arg]) => arg.queryKey.slice(5))).toEqual([['skill', 'results', 'run'], ['skill', 'runs'], ['skill', 'evidence']]);
  });
  it('scope changes quarantine a late paid response without cancelling server work', async () => {
    let resolve;
    axios.post.mockImplementation(() => new Promise((done) => { resolve = done; }));
    const { result, rerender } = renderHook(() => api.useCancelSkillTestRun('skill', 'run'), { wrapper });
    let pending;
    await act(async () => { pending = result.current.mutateAsync({}).catch((error) => error); });
    state.organization = { ...state.organization, currentOrganizationId: 'org-b' };
    rerender();
    await act(async () => { resolve(envelope({ id: 'run', status: 'cancelling' })); });
    expect(await pending).toBeInstanceOf(Error);
    expect(axios.post).toHaveBeenCalledTimes(1);
  });
});
