import { endpoints } from './endpoints';
import { mapAdoption } from './v11-mappers';
import { useSkillV11Query, v11PageParams } from './testing';

export function useSkillAdoption(skillId, { label = 'production', days = 7, page = 1, pageSize = 25 } = {}) {
  const params = { label, days, page, pageSize };
  return useSkillV11Query({ parts: [skillId, 'adoption', params], url: () => endpoints.adoption(skillId), params: v11PageParams(params), mapper: mapAdoption, enabled: Boolean(skillId) });
}
