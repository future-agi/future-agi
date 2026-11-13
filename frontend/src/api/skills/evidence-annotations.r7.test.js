import { beforeEach, expect, it, vi } from 'vitest';
import axios from 'src/utils/axios';
import { loadEvidenceAnnotations } from './evidence-annotations';
vi.mock('src/utils/axios', () => ({ default: { get: vi.fn() } }));
vi.mock('src/api/contracts/api-surface', () => ({ apiPath: () => '/annotations/' }));
vi.mock('./testing', () => ({ skillV11Result: (response) => response.data.result, v11PageParams: ({ page, pageSize }) => ({ page, page_size: pageSize }) }));
const response = (id, extra) => ({ data: { result: { results: [{ id, actor_id: 'reviewer', attempt_id: 'attempt', verdict: 'disagree' }], ...extra } } });
beforeEach(() => vi.clearAllMocks());
it('reads every numbered annotation page with the same scoped headers and signal', async () => {
  axios.get.mockResolvedValueOnce(response('first', { total_pages: 2 })).mockResolvedValueOnce(response('second', { total_pages: 2 }));
  const config = { headers: { 'X-Workspace-Id': 'workspace' }, signal: new AbortController().signal };
  const result = await loadEvidenceAnnotations('simulation_native', 'run', config);
  expect(result.results.map((item) => item.id)).toEqual(['first', 'second']);
  expect(axios.get).toHaveBeenNthCalledWith(2, '/annotations/', { ...config, params: { page: 2, page_size: 100 } });
});
it('does not follow next URLs and completes only when next is null', async () => {
  axios.get.mockResolvedValueOnce(response('first', { next: 'https://untrusted.invalid/next' })).mockResolvedValueOnce(response('second', { next: null }));
  expect((await loadEvidenceAnnotations('simulation_native', 'run', {})).results).toHaveLength(2);
  expect(axios.get.mock.calls.every(([url]) => url === '/annotations/')).toBe(true);
});
it('fails the whole read when a later page fails instead of reporting partial reviews', async () => {
  axios.get.mockResolvedValueOnce(response('first', { total_pages: 2 })).mockRejectedValueOnce(new Error('Offline'));
  await expect(loadEvidenceAnnotations('simulation_native', 'run', {})).rejects.toThrow('Offline');
});
it('rejects repeated pages', async () => {
  axios.get.mockResolvedValue(response('same', { total_pages: 2 }));
  await expect(loadEvidenceAnnotations('simulation_native', 'run', {})).rejects.toThrow('changed while loading');
});
