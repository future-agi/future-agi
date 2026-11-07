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
const envelope = (data) => ({ data: { status: true, result: data } });
let client;
let wrapper;
beforeEach(() => {
  vi.clearAllMocks();
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
    expect(axios.get).toHaveBeenCalledWith('/model-hub/skills/', { params: { page: 2, page_size: 50, search: 'policy' }, signal: expect.any(AbortSignal) });
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
    expect(axios.post).toHaveBeenCalledWith('/model-hub/skills/', { name: 'policy', description: 'Use when reviewing support.', template_id: 'support-policy' });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: api.skillsKeys.all });
  });
  it('preserves a null label CAS and encodes path labels', async () => {
    axios.put.mockResolvedValue(envelope({ name: 'production', version: 2 }));
    const { result } = renderHook(() => api.useSetSkillLabel('id'), { wrapper });
    await act(() => result.current.mutateAsync({ label: 'production', version: 2, expectedVersion: null, note: 'Promote' }));
    expect(axios.put).toHaveBeenCalledWith('/model-hub/skills/id/labels/production/', { version: 2, expected_version: null, note: 'Promote' });
  });
  it('maps draft saves and updates the draft cache without a redundant GET', async () => {
    axios.put.mockResolvedValue(envelope({ revision: 3, base_version: 1, files: [] }));
    const { result } = renderHook(() => api.useSaveSkillDraft('id'), { wrapper });
    await act(() => result.current.mutateAsync({ files: [], baseVersion: 1, revision: 2, note: 'Draft' }));
    expect(axios.put).toHaveBeenCalledWith('/model-hub/skills/id/draft/', { files: [], base_version: 1, revision: 2, note: 'Draft' });
    expect(client.getQueryData(api.skillsKeys.draft('id'))).toMatchObject({ revision: 3, baseVersion: 1 });
    expect(axios.get).not.toHaveBeenCalled();
  });
  it('sends publish warning acknowledgment and force explicitly', async () => {
    axios.post.mockResolvedValue(envelope({ version: { number: 2 }, unchanged: false }));
    const { result } = renderHook(() => api.usePublishSkillDraft('id'), { wrapper });
    await act(() => result.current.mutateAsync({ note: 'Ready', labels: ['production'], acknowledgeWarnings: true, force: false }));
    expect(axios.post).toHaveBeenCalledWith('/model-hub/skills/id/draft/publish/', { note: 'Ready', labels: ['production'], acknowledge_warnings: true, force: false });
  });
  it('passes FormData unchanged so the browser sets the multipart boundary', async () => {
    axios.post.mockResolvedValue(envelope({ bundles: [] }));
    const form = new FormData();
    form.append('files', new Blob(['text']), 'support/SKILL.md');
    const { result } = renderHook(() => api.useImportPreview(), { wrapper });
    await act(() => result.current.mutateAsync(form));
    expect(axios.post).toHaveBeenCalledWith('/model-hub/skills/import/preview/', form);
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
});
