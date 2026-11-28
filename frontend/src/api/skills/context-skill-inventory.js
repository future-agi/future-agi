import { useQueries } from '@tanstack/react-query';
import axios from 'src/utils/axios';
import { skillsKeys } from './keys';
import { endpoints } from './endpoints';
import { mapSkill } from './mappers';
import { skillV11Result, useSkillV11Query, useSkillV11Scope } from './testing';

// Fan out the existing skill-detail query with the same scoped cache keys as
// useSkill. useQueries keeps a changing linked set within the Rules of Hooks.
export function useLinkedSkillUpstream(links) {
  const access = useSkillV11Scope();
  const ids = [...new Set(links.filter((link) => link.selector?.label || link.requested?.label).map((link) => link.skillId))];
  const queries = useQueries({ queries: ids.map((id) => ({
    queryKey: skillsKeys.v11(access.scope, 'legacy', ...skillsKeys.detail(id).slice(1)),
    queryFn: async ({ signal }) => {
      const value = mapSkill(skillV11Result(await axios.get(endpoints.detail(id), { signal, headers: access.headers })));
      if (signal.aborted || access.queryIdentity.current !== access.identity) throw new Error('The Skills scope changed.');
      return value;
    },
    enabled: access.enabled, retry: false, meta: { errorHandled: true },
  })) });
  return Object.fromEntries(ids.map((id, index) => [id, queries[index]]));
}

export function useContextSkillInventory() {
  return useSkillV11Query({ parts: ['context-skill-inventory'], load: async (config) => {
    const skills = new Map();
    let count;
    for (let page = 1; ; page += 1) {
      const value = skillV11Result(await axios.get(endpoints.list(), { ...config, params: { page, page_size: 100, sort: 'name', order: 'asc' } }));
      if (!Array.isArray(value.results) || value.page !== page || !Number.isSafeInteger(value.total_pages) || (count !== undefined && count !== value.count)) throw new Error('The skill inventory changed. Reload available dependencies.');
      count = value.count;
      for (const skill of value.results) {
        if (skills.has(skill.id)) throw new Error('The skill inventory changed. Reload available dependencies.');
        skills.set(skill.id, mapSkill(skill));
      }
      if (page >= value.total_pages) break;
      if (!value.results.length) throw new Error('The complete skill inventory is unavailable.');
    }
    if (skills.size !== count) throw new Error('The complete skill inventory is unavailable.');
    return [...skills.values()].filter((skill) => skill.latestVersion !== null).map((skill) => ({ ...skill, kind: 'skill' }));
  } });
}
