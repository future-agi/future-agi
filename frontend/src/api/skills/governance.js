import axios from 'src/utils/axios';
import { endpoints } from './endpoints';
import { skillsKeys } from './keys';
import { mapProtection, mapProtectionEvent, mapV11Page } from './v11-mappers';
import { skillV11Result, useSkillV11Mutation, useSkillV11Query, v11PageParams } from './testing';

export function useSkillLabelProtection(skillId) {
  return useSkillV11Query({ parts: [skillId, 'protection'], url: () => endpoints.labelProtection(skillId), mapper: mapProtection, enabled: Boolean(skillId) });
}
export function useSetSkillLabelProtection(skillId) {
  return useSkillV11Mutation({
    send: async ({ protectedLabelNames, expectedReleaseRevision }, config) => mapProtection(skillV11Result(await axios.put(endpoints.labelProtection(skillId), { protected_label_names: protectedLabelNames, expected_release_revision: expectedReleaseRevision }, config))),
    invalidate: () => [[skillId, 'protection'], [skillId, 'protectionHistory'], [skillId, 'evidence']],
    invalidateLegacy: () => [skillsKeys.detail(skillId), skillsKeys.labels(skillId), ['skills', 'list']],
  });
}
export function useSkillLabelProtectionHistory(skillId, params = {}) {
  return useSkillV11Query({ parts: [skillId, 'protectionHistory', params], url: () => endpoints.labelProtectionHistory(skillId), params: v11PageParams(params), mapper: (value) => mapV11Page(value, mapProtectionEvent), enabled: Boolean(skillId) });
}
