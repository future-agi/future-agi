import { expect, it, vi } from 'vitest';
import { useSkillAdoption } from './adoption';
vi.mock('./testing', () => ({ useSkillV11Query: (options) => options, v11PageParams: ({ pageSize, ...params }) => ({ ...params, page_size: pageSize }) }));
it('keeps explicit label, window and pagination in the key and wire parameters', () => {
  const query = useSkillAdoption('skill', { label: 'staging', days: 90, page: 2, pageSize: 50 });
  expect(query.parts).toEqual(['skill', 'adoption', { label: 'staging', days: 90, page: 2, pageSize: 50 }]);
  expect(query.params).toEqual({ label: 'staging', days: 90, page: 2, page_size: 50 });
});
it('does not fetch without a skill identity', () => {
  expect(useSkillAdoption(null).enabled).toBe(false);
});
