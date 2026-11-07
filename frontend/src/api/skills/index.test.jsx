import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import PropTypes from 'prop-types';
import axios from 'src/utils/axios';
import { saveAs } from 'file-saver';
import * as api from './index';

vi.mock('src/utils/axios', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() } }));
vi.mock('file-saver', () => ({ saveAs: vi.fn() }));
vi.mock('src/api/contracts/api-surface', () => ({ apiPath: (path, params = {}) => path.replace(/\{([^}]+)\}/g, (_, key) => encodeURIComponent(params[key])) }));
const context = vi.hoisted(() => ({ organizationId: 'org-a', workspaceId: 'ws-a', userId: 'user-a' }));
vi.mock('src/auth/hooks', () => ({ useAuthContext: () => ({ authenticated: true, user: { id: context.userId } }) }));
vi.mock('src/contexts/OrganizationContext', () => ({ useOrganization: () => ({ currentOrganizationId: context.organizationId, isReady: true }) }));
vi.mock('src/contexts/WorkspaceContext', () => ({ useWorkspace: () => ({ currentWorkspaceId: context.workspaceId, isReady: true }) }));
const headers = { 'X-Organization-Id': 'org-a', 'X-Workspace-Id': 'ws-a' };
const scoped = (...parts) => api.skillsKeys.v11(context, 'legacy', ...parts);
const envelope = (data) => ({ data: { status: true, result: data } });
let client;
let wrapper;
beforeEach(() => {
  vi.clearAllMocks();
  Object.assign(context, { organizationId: 'org-a', workspaceId: 'ws-a', userId: 'user-a' });
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  wrapper = function Wrapper({ children }) { return <QueryClientProvider client={client}>{children}</QueryClientProvider>; };
  wrapper.propTypes = { children: PropTypes.node };
});
afterEach(() => { cleanup(); client.clear(); });

 describe('file chunk reader', () => {
  it('reassembles code-point offsets and carries the cancellation signal', async () => {
    axios.get.mockResolvedValueOnce(envelope({ path: 'SKILL.md', sha256: 'abc', offset: 0, text: 'Hi😀', next_offset: 3, eof: false, total_chars: 4, size_bytes: 7 }))
      .mockResolvedValueOnce(envelope({ path: 'SKILL.md', sha256: 'abc', offset: 3, text: '!', next_offset: null, eof: true, total_chars: 4, size_bytes: 7 }));
    const signal = new AbortController().signal;
    expect(await api.readSkillFile('id', 1, 'SKILL.md', signal)).toEqual({ path: 'SKILL.md', text: 'Hi😀!', sha256: 'abc', sizeBytes: 7 });
    expect(axios.get.mock.calls[1][1]).toEqual({ signal, params: { path: 'SKILL.md', offset: 3 } });
  });
  it('rejects non-progressing chunks instead of looping', async () => {
    axios.get.mockResolvedValue(envelope({ path: 'SKILL.md', sha256: 'abc', offset: 0, text: '', next_offset: 0, eof: false, total_chars: 2, size_bytes: 2 }));
    await expect(api.readSkillFile('id', 1, 'SKILL.md')).rejects.toThrow('did not advance');
    expect(axios.get).toHaveBeenCalledTimes(1);
  });
  it('rejects prematurely ended chunks', async () => {
    axios.get.mockResolvedValue(envelope({ path: 'SKILL.md', sha256: 'abc', offset: 0, text: 'a', next_offset: null, eof: true, total_chars: 2, size_bytes: 2 }));
    await expect(api.readSkillFile('id', 1, 'SKILL.md')).rejects.toThrow('incomplete');
  });
});

describe('queries', () => {
  it('maps list pagination and sends page_size and an AbortSignal', async () => {
    axios.get.mockResolvedValue(envelope({ results: [], page: 2, page_size: 50, total_pages: 3, count: 101 }));
    const { result } = renderHook(() => api.useSkillsList({ page: 2, pageSize: 50, search: 'policy' }), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data.pageSize).toBe(50);
    expect(axios.get).toHaveBeenCalledWith('/model-hub/skills/', { params: { page: 2, page_size: 50, search: 'policy' }, signal: expect.any(AbortSignal), headers });
  });
  it('maps only draft_not_found to null', async () => {
    axios.get.mockRejectedValue({ code: 'draft_not_found', statusCode: 404 });
    const { result } = renderHook(() => api.useSkillDraft('id'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });
  it('does not suppress another tenant or a missing skill', async () => {
    axios.get.mockRejectedValue({ code: 'skill_not_found', statusCode: 404 });
    const { result } = renderHook(() => api.useSkillDraft('other'), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
  it('does not request incomplete selectors', () => {
    renderHook(() => api.useSkillVersion('id', null), { wrapper });
    renderHook(() => api.useSkillCompare('id', null, 2), { wrapper });
    renderHook(() => api.useSkillFile('id', 1, null), { wrapper });
    expect(axios.get).not.toHaveBeenCalled();
  });
});

describe('mutations', () => {
  it('maps creation template and invalidates skills', async () => {
    axios.post.mockResolvedValue(envelope({ id: 'new', latest_version: null }));
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const { result } = renderHook(() => api.useCreateSkill(), { wrapper });
    await act(() => result.current.mutateAsync({ name: 'policy', description: 'Use when reviewing support.', templateId: 'support-policy' }));
    expect(axios.post).toHaveBeenCalledWith('/model-hub/skills/', { name: 'policy', description: 'Use when reviewing support.', template_id: 'support-policy' }, { headers });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: scoped('list') });
  });
  it('preserves a null label CAS and encodes path labels', async () => {
    axios.put.mockResolvedValue(envelope({ name: 'production', version: 2 }));
    const { result } = renderHook(() => api.useSetSkillLabel('id'), { wrapper });
    await act(() => result.current.mutateAsync({ label: 'production', version: 2, expectedVersion: null, note: 'Promote' }));
    expect(axios.put).toHaveBeenCalledWith('/model-hub/skills/id/labels/production/', { version: 2, expected_version: null, note: 'Promote' }, { headers });
  });
  it('maps draft saves and updates the draft cache without a redundant GET', async () => {
    axios.put.mockResolvedValue(envelope({ revision: 3, base_version: 1, files: [] }));
    const { result } = renderHook(() => api.useSaveSkillDraft('id'), { wrapper });
    await act(() => result.current.mutateAsync({ files: [], baseVersion: 1, revision: 2, note: 'Draft' }));
    expect(axios.put).toHaveBeenCalledWith('/model-hub/skills/id/draft/', { files: [], base_version: 1, revision: 2, note: 'Draft' }, { headers });
    expect(client.getQueryData(scoped('id', 'draft'))).toMatchObject({ revision: 3, baseVersion: 1 });
    expect(axios.get).not.toHaveBeenCalled();
  });
  it('sends publish warning acknowledgment and force explicitly', async () => {
    axios.post.mockResolvedValue(envelope({ version: { number: 2 }, unchanged: false }));
    const { result } = renderHook(() => api.usePublishSkillDraft('id'), { wrapper });
    await act(() => result.current.mutateAsync({ note: 'Ready', labels: ['production'], acknowledgeWarnings: true, force: false, revision: 7 }));
    expect(axios.post).toHaveBeenCalledWith('/model-hub/skills/id/draft/publish/', { note: 'Ready', labels: ['production'], acknowledge_warnings: true, force: false, revision: 7 }, { headers });
  });
  it('passes FormData unchanged so the browser sets the multipart boundary', async () => {
    axios.post.mockResolvedValue(envelope({ bundles: [] }));
    const form = new FormData();
    form.append('files', new Blob(['text']), 'support/SKILL.md');
    const { result } = renderHook(() => api.useImportPreview(), { wrapper });
    await act(() => result.current.mutateAsync(form));
    expect(axios.post).toHaveBeenCalledWith('/model-hub/skills/import/preview/', form, { headers });
  });
});

it('downloads via the credentialed shared axios instance', async () => {
  const blob = new Blob(['zip'], { type: 'application/zip' });
  axios.get.mockResolvedValue({ data: blob });
  await api.downloadSkillsZip({ names: ['policy'], version: 2 });
  expect(axios.get).toHaveBeenCalledWith('/model-hub/skills/download/', { responseType: 'blob', params: { names: 'policy', tag: undefined, version: 2 } });
  expect(saveAs).toHaveBeenCalledWith(blob, 'futureagi-skills.zip');
});
it('reads normalized and raw Axios error codes', () => {
  expect(api.getSkillErrorCode({ code: 'stale_base' })).toBe('stale_base');
  expect(api.getSkillErrorCode({ response: { data: { code: 'checks_failed' } } })).toBe('checks_failed');
  expect(api.getSkillErrorCode({ code: 'ERR_BAD_REQUEST', response: { status: 409, data: { code: 'draft_conflict' } } })).toBe('draft_conflict');
});

describe('v1.1 release and scope integration', () => {
  it('sends digest, evidence and aggregate CAS without renaming draft revision', async () => {
    axios.post.mockResolvedValue(envelope({ version: { number: 2 }, release_revision: 9, evidence: null }));
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const { result } = renderHook(() => api.usePublishSkillDraft('id'), { wrapper });
    await act(() => result.current.mutateAsync({ revision: 7, expectedDigest: 'sha256:reviewed', expectedReleaseRevision: 8, evidenceRunId: 'run-id', labels: ['production'] }));
    expect(axios.post).toHaveBeenCalledWith('/model-hub/skills/id/draft/publish/', expect.objectContaining({ revision: 7, expected_digest: 'sha256:reviewed', expected_release_revision: 8, evidence_run_id: 'run-id' }), { headers });
    expect(axios.post.mock.calls[0][1]).not.toHaveProperty('expected_draft_revision');
    for (const part of ['evidence', 'run', 'runs', 'adoption', 'protection', 'source']) {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: api.skillsKeys.v11(context, 'id', part) });
    }
    expect(invalidate).not.toHaveBeenCalledWith({ queryKey: ['skills'] });
  });

  it('sends both protected pointer preconditions, including null first assignment', async () => {
    axios.put.mockResolvedValue(envelope({ name: 'production', version: 2, can_move: true, is_protected: true, release_revision: 6 }));
    const { result } = renderHook(() => api.useSetSkillLabel('id'), { wrapper });
    let value;
    await act(async () => { value = await result.current.mutateAsync({ label: 'production', version: 2, expectedVersion: null, expectedReleaseRevision: 5, expectedDigest: 'sha256:target', evidenceRunId: 'run' }); });
    expect(axios.put.mock.calls[0][1]).toMatchObject({ expected_version: null, expected_release_revision: 5, expected_digest: 'sha256:target', evidence_run_id: 'run' });
    expect(value).toMatchObject({ canMove: true, isProtected: true, releaseRevision: 6 });
  });

  it.each(['release_conflict', 'evidence_expired', 'candidate_changed'])('refreshes current review data after %s without retrying or dropping the evidence reference', async (code) => {
    axios.post.mockRejectedValue({ code });
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const { result } = renderHook(() => api.usePublishSkillDraft('id'), { wrapper });
    const input = { revision: 7, expectedReleaseRevision: 4, evidenceRunId: 'keep-this-run' };
    await act(async () => { await expect(result.current.mutateAsync(input)).rejects.toMatchObject({ code }); });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: scoped('id', 'draft') });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: scoped('id', 'detail') });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: api.skillsKeys.v11(context, 'id', 'evidence') });
    expect(result.current.variables).toBe(input);
    expect(input.evidenceRunId).toBe('keep-this-run');
    expect(axios.post).toHaveBeenCalledTimes(1);
  });

  it('refreshes the immutable version read’s mutable label metadata after a move', async () => {
    axios.put.mockResolvedValue(envelope({ name: 'production', version: 2 }));
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const { result } = renderHook(() => api.useSetSkillLabel('id'), { wrapper });
    await act(() => result.current.mutateAsync({ label: 'production', version: 2, expectedVersion: 1, expectedReleaseRevision: 4 }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: scoped('id', 'version') });
    expect(invalidate).not.toHaveBeenCalledWith({ queryKey: scoped('id', 'file') });
  });

  it('never exposes a late previous-workspace draft in the current query', async () => {
    let finish;
    axios.get.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const { result, rerender } = renderHook(() => api.useSkillDraft('id'), { wrapper });
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
    const oldSignal = axios.get.mock.calls[0][1].signal;
    context.workspaceId = 'ws-b';
    axios.get.mockResolvedValue(envelope({ revision: 1, files: [], note: 'New scope' }));
    rerender();
    await waitFor(() => expect(result.current.data?.note).toBe('New scope'));
    await act(async () => { finish(envelope({ revision: 99, files: [], note: 'Private old scope' })); });
    expect(oldSignal.aborted).toBe(true);
    expect(result.current.data.note).toBe('New scope');
    expect(client.getQueryData(api.skillsKeys.v11({ ...context, workspaceId: 'ws-a' }, 'legacy', 'id', 'draft'))).toBeUndefined();
  });

  it('rejects a late save after auth identity changes without writing its cache', async () => {
    let finish;
    axios.put.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const { result, rerender } = renderHook(() => api.useSaveSkillDraft('id'), { wrapper });
    let promise;
    act(() => { promise = result.current.mutateAsync({ revision: 1, files: [] }).catch((error) => error); });
    await waitFor(() => expect(axios.put).toHaveBeenCalled());
    context.userId = 'user-b';
    rerender();
    await act(async () => { finish(envelope({ revision: 2, files: [] })); });
    expect((await promise).message).toContain('scope changed');
    expect(client.getQueryData(scoped('id', 'draft'))).toBeUndefined();
  });
});
