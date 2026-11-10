import { createElement } from 'react';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import axios from 'src/utils/axios';
import { useAcceptContextCapture, useContextCaptureSource, usePreviewContextCapture } from './context-capture';
import { useAppendEvidenceAnnotation, useEvidenceAnnotations, useEvidenceReference } from './evidence-annotations';
import { skillsKeys } from './keys';

const state = vi.hoisted(() => ({ workspaceId: 'ws-a', userId: 'author' }));
vi.mock('src/auth/hooks', () => ({ useAuthContext: () => ({ authenticated: true, loading: false, user: { id: state.userId } }) }));
vi.mock('src/contexts/OrganizationContext', () => ({ useOrganization: () => ({ isReady: true, currentOrganizationId: 'org-a' }) }));
vi.mock('src/contexts/WorkspaceContext', () => ({ useWorkspace: () => ({ isReady: true, currentWorkspaceId: state.workspaceId }) }));
vi.mock('src/utils/axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
const id = '10000000-0000-4000-8000-000000000001';
const key = '20000000-0000-4000-8000-000000000002';
const digest = `sha256:${'a'.repeat(64)}`;
const envelope = (result) => ({ data: { result } });
const traceRef = { projectId: id, traceId: 'trace-a', spanId: 'span-a', startTime: '2026-11-10T00:00:00.123456Z' };
const wireRef = { project_id: id, trace_id: 'trace-a', span_id: 'span-a', start_time: traceRef.startTime };
const source = { trace_ref: wireRef, source_fingerprint: digest, segments: [{ id: 'request-1', kind: 'request', text: 'Private request' }] };
const page = { count: 1, page: 1, page_size: 25, total_pages: 1, results: [{ id, revision: 3, actor_id: 'reviewer', check_id: id, attempt_id: key, verdict: 'agree', cited_text: 'retained text', comment: 'Confirmed', created_at: '2026-11-10T00:00:00Z' }] };
let client;
let wrapper;
beforeEach(() => {
  vi.clearAllMocks(); state.workspaceId = 'ws-a'; state.userId = 'author';
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  wrapper = ({ children }) => createElement(QueryClientProvider, { client }, children);
});
afterEach(() => { cleanup(); client.clear(); });

it('reads authorized source segments with the complete physical identity and scoped cancellation', async () => {
  axios.post.mockResolvedValue(envelope(source));
  const hook = renderHook(() => useContextCaptureSource(id, traceRef), { wrapper });
  await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
  expect(axios.post).toHaveBeenCalledWith(`/model-hub/skills/contexts/${id}/captures/source/`, { trace_ref: wireRef }, expect.objectContaining({ signal: expect.any(AbortSignal), headers: { 'X-Organization-Id': 'org-a', 'X-Workspace-Id': 'ws-a' } }));
  expect(hook.result.current.data).toEqual({ traceRef, sourceFingerprint: digest, segments: source.segments, authorized: true, readOnly: false });
});
it('does not read a source before an explicit physical selection', () => {
  renderHook(() => useContextCaptureSource(id, null), { wrapper });
  expect(axios.post).not.toHaveBeenCalled();
});
it('removes private source text on a scope change and rejects a late response', async () => {
  let finish;
  axios.post.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; })).mockResolvedValue(envelope({ ...source, segments: [] }));
  const hook = renderHook(() => useContextCaptureSource(id, traceRef), { wrapper });
  await waitFor(() => expect(axios.post).toHaveBeenCalledOnce());
  const signal = axios.post.mock.calls[0][2].signal;
  state.workspaceId = 'ws-b'; hook.rerender();
  await waitFor(() => expect(hook.result.current.data?.segments).toEqual([]));
  await act(async () => finish(envelope(source)));
  expect(signal.aborted).toBe(true);
  expect(JSON.stringify(client.getQueryCache().getAll().map((query) => query.state.data))).not.toContain('Private request');
});
it('previews only selected IDs and maps retained source authority, never client asserted lineage', async () => {
  axios.post.mockResolvedValue(envelope({ capture_id: key, expires_at: '2099-01-01T00:00:00Z', source_fingerprint: digest, recorded_context: null, segments: source.segments, omitted: [], outbound_findings: [], replay_scope: 'text_only' }));
  const hook = renderHook(() => usePreviewContextCapture(id), { wrapper });
  let value;
  await act(async () => { value = await hook.result.current.mutateAsync({ traceRef, selectedSegmentIds: ['request-1'], recordedContext: 'not-authority' }); });
  expect(axios.post.mock.calls[0][1]).toEqual({ trace_ref: wireRef, selected_segment_ids: ['request-1'] });
  expect(value).toMatchObject({ captureId: key, sourceFingerprint: digest, recordedContext: null, replayScope: 'text_only' });
});
it('accepts reviewed redactions with the original suite revision and operation key', async () => {
  axios.post.mockRejectedValueOnce(new Error('connection lost')).mockResolvedValueOnce(envelope({ revision: 5, digest, cases: [], updated_at: null, updated_by: null, visibility: 'workspace' }));
  const hook = renderHook(() => useAcceptContextCapture(id), { wrapper });
  const input = { captureId: key, sourceFingerprint: digest, suiteRevision: 4, acknowledgeWorkspaceSharing: true, idempotencyKey: key,
    case: { id, sourceGroupId: key, title: 'Reviewed', prompt: 'Redacted request', fixtures: [], expectedTrigger: null, reviewed: true, checks: [{ id: key, kind: 'contains', critical: true, config: { text: 'Approved', caseSensitive: true } }] } };
  await act(async () => { await expect(hook.result.current.mutateAsync(input)).rejects.toThrow('connection lost'); });
  await act(async () => { await hook.result.current.mutateAsync(input); });
  expect(axios.post.mock.calls[1]).toEqual(axios.post.mock.calls[0]);
  expect(axios.post.mock.calls[0][0]).toBe(`/model-hub/skills/contexts/${id}/captures/${key}/accept/`);
  expect(axios.post.mock.calls[0][1]).toMatchObject({ source_fingerprint: digest, suite_revision: 4, acknowledge_workspace_sharing: true, case: { expected_trigger: null, checks: [{ config: { text: 'Approved', case_sensitive: true } }] } });
  expect(axios.post.mock.calls[0][2].headers['Idempotency-Key']).toBe(key);
});
it('loads paginated attributed annotations from the exact evidence identity', async () => {
  axios.get.mockResolvedValue(envelope(page));
  const hook = renderHook(() => useEvidenceAnnotations('skills_text', id, 2), { wrapper });
  await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
  expect(axios.get.mock.calls[0][0]).toBe(`/model-hub/skills/evidence/skills_text/${id}/annotations/`);
  expect(axios.get.mock.calls[0][1].params).toEqual({ page: 2, page_size: 25 });
  expect(hook.result.current.data.results[0]).toMatchObject({ actorId: 'reviewer', checkId: id, attemptId: key, revision: 3 });
});
it('does not treat inaccessible evidence as an empty annotation revision', async () => {
  axios.get.mockRejectedValue({ response: { status: 410 } });
  const hook = renderHook(() => useEvidenceReference('skills_text', id), { wrapper });
  await waitFor(() => expect(hook.result.current.isError).toBe(true));
  expect(hook.result.current.data).toBeUndefined();
});
it('appends only a revision-bound citation and invalidates release review currentness', async () => {
  axios.post.mockResolvedValue(envelope(page.results[0]));
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  const hook = renderHook(() => useAppendEvidenceAnnotation('skills_text', id), { wrapper });
  const input = { source: 'skills_text', sourceId: id, annotationRevision: 2, checkId: id, attemptId: key, verdict: 'agree', citedText: 'retained text', comment: 'Confirmed', machineVerdict: 'pass' };
  await act(async () => { await hook.result.current.mutateAsync(input); });
  expect(axios.post.mock.calls[0][1]).toEqual({ annotation_revision: 2, check_id: id, attempt_id: key, verdict: 'agree', cited_text: 'retained text', comment: 'Confirmed' });
  expect(invalidate).toHaveBeenCalledWith({ queryKey: skillsKeys.v11({ organizationId: 'org-a', workspaceId: 'ws-a', userId: 'author' }, 'release-request') });
  await act(async () => { await expect(hook.result.current.mutateAsync({ ...input, sourceId: key })).rejects.toThrow('selected evidence changed'); });
  expect(axios.post).toHaveBeenCalledOnce();
});
