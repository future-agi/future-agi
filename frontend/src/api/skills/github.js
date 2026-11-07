import axios from 'src/utils/axios';
import { endpoints } from './endpoints';
import { skillsKeys } from './keys';
import { mapGitHubPreview, mapJobAck, mapSource, mapSourceDraft, mapStagedImport } from './v11-mappers';
import { idempotencyHeaders, skillV11Result, useSkillV11Mutation, useSkillV11Query } from './testing';

export function githubPreviewInput(input) {
  if (input.parentPreviewId !== undefined) {
    if (input.url !== undefined || input.ref !== undefined) throw new Error('Root selection must continue the existing preview without a new URL or ref.');
    return { parent_preview_id: input.parentPreviewId, path: input.path };
  }
  return { url: input.url, path: input.path ?? null, ref: input.ref ?? null };
}
export function useCreateGitHubPreview() {
  return useSkillV11Mutation({ send: async ({ idempotencyKey, ...input }, config) => mapJobAck(skillV11Result(await axios.post(endpoints.githubPreviews(), githubPreviewInput(input), { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } }))) });
}
export function useGitHubPreview(previewId, skillId) {
  return useSkillV11Query({ parts: ['githubPreview', previewId], url: () => endpoints.githubPreview(previewId), mapper: mapGitHubPreview, enabled: Boolean(previewId), poll: true, terminalParts: skillId ? [[skillId, 'source']] : [] });
}
export function useStageGitHubImport() {
  return useSkillV11Mutation({
    send: async ({ previewId, rights }, config) => mapStagedImport(skillV11Result(await axios.post(endpoints.githubImport(), { preview_id: previewId, rights: { confirmed: rights.confirmed, review_note: rights.reviewNote } }, config))),
    invalidate: (_, input) => [['githubPreview', input.previewId]],
    invalidateLegacy: (value) => [['skills', 'list'], skillsKeys.detail(value.skill.id), skillsKeys.draft(value.skill.id)],
  });
}
export function useSkillSource(skillId) {
  return useSkillV11Query({ parts: [skillId, 'source'], url: () => endpoints.source(skillId), mapper: mapSource, enabled: Boolean(skillId) });
}
export function useCheckSkillSource(skillId) {
  return useSkillV11Mutation({ send: async ({ idempotencyKey, expectedSourceRevision }, config) => mapJobAck(skillV11Result(await axios.post(endpoints.checkSource(skillId), { expected_source_revision: expectedSourceRevision }, { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } }))), invalidate: () => [[skillId, 'source']] });
}
export function useStageSkillSourceUpdate(skillId) {
  return useSkillV11Mutation({
    send: async ({ previewId, revision, expectedSourceRevision, expectedLatestVersion, expectedLatestDigest, acceptReplacement, rights }, config) => {
      const value = skillV11Result(await axios.post(endpoints.stageSourceUpdate(skillId), {
        preview_id: previewId, revision, expected_source_revision: expectedSourceRevision,
        expected_latest_version: expectedLatestVersion, expected_latest_digest: expectedLatestDigest,
        accept_replacement: acceptReplacement, rights: { confirmed: rights.confirmed, review_note: rights.reviewNote },
      }, config));
      return { draft: mapSourceDraft(value.draft), published: value.published };
    },
    invalidate: (_, input) => [['githubPreview', input.previewId], [skillId, 'source'], [skillId, 'evidence'], [skillId, 'runs']],
    invalidateLegacy: () => [skillsKeys.draft(skillId), skillsKeys.detail(skillId), ['skills', skillId, 'compare'], ['skills', 'list']],
  });
}
