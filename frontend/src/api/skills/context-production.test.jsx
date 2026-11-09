import { createElement } from 'react';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import axios from 'src/utils/axios';
import { readContextText, loadContextReview, useContextInspection, useContextInspectionFile } from './context-production';

const state = vi.hoisted(() => ({ workspace: 'workspace-a' }));
vi.mock('src/auth/hooks', () => ({ useAuthContext: () => ({ authenticated: true, user: { id: 'author' } }) }));
vi.mock('src/contexts/OrganizationContext', () => ({ useOrganization: () => ({ isReady: true, currentOrganizationId: 'org' }) }));
vi.mock('src/contexts/WorkspaceContext', () => ({ useWorkspace: () => ({ isReady: true, currentWorkspaceId: state.workspace }) }));
vi.mock('src/utils/axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
const envelope = (result) => ({ data: { result } });
const digest = `sha256:${'a'.repeat(64)}`;
const chunk = { resource_id: 'context', version_id: 'version', digest, path: 'AGENTS.md', sha256: 'hash', offset: 0, next_offset: 4, total_chars: 8, text: 'one ', eof: false };
const metadata = { path: 'AGENTS.md', sha256: 'hash', size_bytes: 8, origin: { kind: 'owned' } };
const inspection = { descriptor: { kind: 'agent_context', name: 'context', links: [] }, manifest: { kind: 'agent_context', name: 'context', version: 4, version_id: 'version', digest, files: [metadata], file_count: 1, file_offset: 0, next_file_offset: null }, distribution_blocked: false };
let client;
let wrapper;
beforeEach(() => {
  vi.clearAllMocks(); state.workspace = 'workspace-a';
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  wrapper = ({ children }) => createElement(QueryClientProvider, { client }, children);
});
afterEach(() => { cleanup(); client.clear(); });
it('reads complete exact-version files rather than silently truncating the first chunk', async () => {
  axios.get.mockResolvedValueOnce(envelope(chunk)).mockResolvedValueOnce(envelope({ ...chunk, offset: 4, next_offset: null, text: 'two.', eof: true }));
  const result = await readContextText({ contextId: 'context', number: 4, path: 'AGENTS.md', expectedDigest: digest });
  expect(result.text).toBe('one two.');
  expect(axios.get.mock.calls[1][1].params.offset).toBe(4);
  expect(axios.get.mock.calls[0][0]).toBe('/model-hub/skills/contexts/context/versions/4/inspection/files/');
});
it('rejects changed immutable identity between chunks', async () => {
  axios.get.mockResolvedValueOnce(envelope(chunk)).mockResolvedValueOnce(envelope({ ...chunk, version_id: 'other', offset: 4, text: 'two.', eof: true }));
  await expect(readContextText({ contextId: 'context', number: 4, path: 'AGENTS.md' })).rejects.toThrow('identity changed');
});
it('rejects a file that is not part of the reviewed hash', async () => {
  axios.get.mockResolvedValue(envelope({ ...chunk, eof: true }));
  await expect(readContextText({ contextId: 'context', number: 4, path: 'AGENTS.md', expectedSha256: 'different' })).rejects.toThrow('identity changed');
});
it('loads the complete manifest using the serializer limit of 25', async () => {
  axios.get.mockResolvedValueOnce(envelope({ ...inspection, manifest: { ...inspection.manifest, next_file_offset: 1 } }))
    .mockResolvedValueOnce(envelope({ ...inspection, manifest: { ...inspection.manifest, file_offset: 1, files: [{ ...metadata, path: 'references/policy.md' }] } }));
  const { result } = renderHook(() => useContextInspection('context', 4), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data.files).toHaveLength(2);
  expect(axios.get.mock.calls[0][1].params.file_limit).toBe(25);
  expect(axios.get.mock.calls[1][1].params).toEqual({ file_offset: 1, file_limit: 25 });
});
it('fences a late multi-request file read after workspace changes', async () => {
  let resolveOld;
  axios.get.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
    .mockResolvedValue(envelope({ ...chunk, text: 'new workspace', eof: true }));
  const hook = renderHook(() => useContextInspectionFile('context', 4, 'AGENTS.md', 'version', digest), { wrapper });
  await waitFor(() => expect(axios.get).toHaveBeenCalledOnce());
  const signal = axios.get.mock.calls[0][1].signal;
  state.workspace = 'workspace-b'; hook.rerender();
  await waitFor(() => expect(hook.result.current.data?.text).toBe('new workspace'));
  await act(async () => resolveOld(envelope({ ...chunk, text: 'old workspace', eof: true })));
  expect(signal.aborted).toBe(true);
  expect(hook.result.current.data.text).toBe('new workspace');
});
it('refuses mixed release review pages before reading any comparison bytes', async () => {
  const diff = { files: [], total_pages: 2 };
  axios.post.mockResolvedValueOnce(envelope({ review_fingerprint: 'one', changes: diff, head_changes: diff }))
    .mockResolvedValueOnce(envelope({ review_fingerprint: 'two', changes: diff, head_changes: diff }));
  await expect(loadContextReview('context', 3, {})).rejects.toThrow('release changed');
  expect(axios.get).not.toHaveBeenCalled();
});
