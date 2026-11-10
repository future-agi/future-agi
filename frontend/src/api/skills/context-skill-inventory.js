import axios from 'src/utils/axios';
import { endpoints } from './endpoints';
import { mapSkill } from './mappers';
import { skillV11Result, useSkillV11Query } from './testing';

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
