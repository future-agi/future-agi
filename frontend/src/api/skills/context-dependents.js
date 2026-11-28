import { apiPath } from 'src/api/contracts/api-surface';
import { useSkillV11Query } from './testing';

export function useContextDependents(skillId, { view = 'active', page = 1 } = {}) {
  return useSkillV11Query({
    parts: [skillId, 'context-dependents', view, page],
    url: () => apiPath('/model-hub/skills/{skill_id}/context-dependents/', { skill_id: skillId }),
    params: { view, page, page_size: 25 },
    mapper: (value) => value,
    enabled: Boolean(skillId),
  });
}
