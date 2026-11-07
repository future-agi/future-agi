import { expect, it, vi } from 'vitest';
import axios from 'src/utils/axios';
import { useSetSkillLabelProtection, useSkillLabelProtectionHistory } from './governance';
vi.mock('src/utils/axios', () => ({ default: { put: vi.fn() } }));
vi.mock('./endpoints', () => ({ endpoints: { labelProtection: () => '/protection/' } }));
vi.mock('./testing', () => ({ useSkillV11Mutation: (options) => options, useSkillV11Query: (options) => options, skillV11Result: (response) => response.data.result, v11PageParams: ({ pageSize, ...params }) => ({ ...params, page_size: pageSize }) }));
it('sends aggregate release CAS even at revision zero and invalidates read-before-move state', async () => {
  axios.put.mockResolvedValue({ data: { result: { protected_label_names: [], release_revision: 1, can_manage: true } } });
  const operation = useSetSkillLabelProtection('skill');
  const value = await operation.send({ protectedLabelNames: [], expectedReleaseRevision: 0 }, {});
  expect(axios.put.mock.calls[0][1]).toEqual({ protected_label_names: [], expected_release_revision: 0 });
  expect(value).toEqual({ protectedLabelNames: [], releaseRevision: 1, canManage: true });
  expect(operation.invalidate()).toContainEqual(['skill', 'evidence']);
  expect(operation.invalidateLegacy()).toContainEqual(['skills', 'skill', 'labels']);
});
it('paginates policy history without querying a second policy source', () => {
  expect(useSkillLabelProtectionHistory('skill', { page: 2, pageSize: 50 }).params).toEqual({ page: 2, page_size: 50 });
});
