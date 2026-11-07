import { useQuery } from '@tanstack/react-query';
import { saveAs } from 'file-saver';
import axios from 'src/utils/axios';
import { endpoints } from './endpoints';
import { skillsKeys } from './keys';
import { useSkillV11Scope, useSkillV11Query, useSkillV11Mutation } from './testing';
import {
  mapSkill, mapPage, mapTemplate, mapVersion, mapCompare, mapLabel,
  mapLabelEvent, mapDraft, mapUsage, mapPublish, mapChecks,
  mapImportPreview, mapImportResult,
} from './mappers';

export { skillsKeys } from './keys';
export { endpoints } from './endpoints';
export * from './testing';
export * from './assist';
export * from './github';
export * from './governance';
export * from './adoption';
export const getSkillErrorCode = (error) => error?.response?.data?.code || error?.code;
const result = (response) => response.data.result;
const pageParams = ({ pageSize, ...params } = {}) => ({ ...params, ...(pageSize != null ? { page_size: pageSize } : {}) });

function useSkillsQuery(queryKey, url, mapper, params, enabled = true) {
  return useSkillV11Query({ parts: ['legacy', ...queryKey.slice(1)], url, mapper, params, enabled });
}

export function useSkillsList(params = {}) {
  return useSkillsQuery(skillsKeys.list(params), endpoints.list, (data) => mapPage(data, mapSkill), pageParams(params));
}
export function useSkill(id) {
  return useSkillsQuery(skillsKeys.detail(id), () => endpoints.detail(id), mapSkill, undefined, Boolean(id));
}
export function useSkillTemplates() {
  return useSkillsQuery(skillsKeys.templates(), endpoints.templates, (data) => data.map(mapTemplate));
}
export function useSkillVersions(id, params = {}) {
  return useSkillsQuery(skillsKeys.versions(id, params), () => endpoints.versions(id), (data) => mapPage(data, mapVersion), pageParams(params), Boolean(id));
}
export function useSkillVersion(id, number) {
  return useSkillsQuery(skillsKeys.version(id, number), () => endpoints.version(id, number), mapVersion, undefined, Boolean(id && number));
}

// The file endpoint is character-offset based, with a UTF-8 byte cap per chunk.
// Keep one cancellation signal for the entire read and reject non-progressing
// or mismatched chunks rather than displaying silently incomplete instructions.
export async function readSkillFile(id, number, path, signal, headers) {
  let offset = 0;
  let first;
  const chunks = [];
  for (;;) {
    const chunk = result(await axios.get(endpoints.file(id, number), { params: { path, offset }, signal, ...(headers ? { headers } : {}) }));
    first ||= chunk;
    if (chunk.path !== path || chunk.offset !== offset || chunk.sha256 !== first.sha256 || chunk.total_chars !== first.total_chars || chunk.size_bytes !== first.size_bytes || typeof chunk.text !== 'string') {
      throw new Error('The file changed or returned an invalid chunk. Reload the file.');
    }
    chunks.push(chunk.text);
    // Python offsets count Unicode code points, not JavaScript UTF-16 units.
    const end = offset + Array.from(chunk.text).length;
    if (chunk.eof) {
      if (chunk.next_offset !== null || end !== chunk.total_chars) throw new Error('The file response is incomplete.');
      break;
    }
    if (chunk.next_offset !== end || end <= offset || end > chunk.total_chars) throw new Error('The file response did not advance.');
    offset = chunk.next_offset;
  }
  return { path, text: chunks.join(''), sha256: first.sha256, sizeBytes: first.size_bytes };
}
export function useSkillFile(id, number, path) {
  const access = useSkillV11Scope();
  return useQuery({
    queryKey: skillsKeys.v11(access.scope, 'legacy', ...skillsKeys.file(id, number, path).slice(1)),
    queryFn: async ({ signal }) => {
      const file = await readSkillFile(id, number, path, signal, access.headers);
      if (signal.aborted || access.current.current !== access.identity) throw new Error('The Skills scope changed.');
      return file;
    },
    enabled: access.enabled && Boolean(id && number && path), meta: { errorHandled: true },
  });
}
export function useSkillCompare(id, from, to) {
  return useSkillsQuery(skillsKeys.compare(id, from, to), () => endpoints.compare(id), mapCompare, { from, to }, Boolean(id && from != null && to != null));
}
export function useSkillLabels(id) {
  return useSkillsQuery(skillsKeys.labels(id), () => endpoints.labels(id), (data) => data.map(mapLabel), undefined, Boolean(id));
}
export function useSkillLabelHistory(id, label) {
  return useSkillsQuery(skillsKeys.labelHistory(id, label), () => endpoints.labelHistory(id), (data) => mapPage(data, mapLabelEvent), { label }, Boolean(id));
}
export function useSkillDraft(id) {
  const access = useSkillV11Scope();
  return useQuery({
    queryKey: skillsKeys.v11(access.scope, 'legacy', ...skillsKeys.draft(id).slice(1)),
    enabled: access.enabled && Boolean(id), retry: false, meta: { errorHandled: true },
    queryFn: async ({ signal }) => {
      try {
        const response = await axios.get(endpoints.draft(id), { signal, headers: access.headers });
        if (signal.aborted || access.current.current !== access.identity) throw new Error('The Skills scope changed.');
        return mapDraft(result(response));
      } catch (error) {
        if (!signal.aborted && access.current.current === access.identity && getSkillErrorCode(error) === 'draft_not_found') return null;
        throw error;
      }
    },
  });
}
export function useSkillUsage(id, days) {
  return useSkillsQuery(skillsKeys.usage(id, days), () => endpoints.usage(id), mapUsage, { days }, Boolean(id));
}

// Invalidate only this principal's affected feature resources. Published file
// keys are immutable and never invalidated by a draft save or label movement.
const evidenceParts = (id) => ['evidence', 'run', 'runs'].map((part) => [id, part]);
const draftKeys = (id) => [['skills', 'list'], skillsKeys.detail(id), skillsKeys.draft(id), ['skills', id, 'compare']];
const releaseKeys = (id) => [...draftKeys(id), ['skills', id, 'versions'], ['skills', id, 'version'], skillsKeys.labels(id), ['skills', id, 'labelHistory']];
const refreshReleaseOnError = (id) => (error) => [
  'draft_conflict', 'draft_not_found', 'candidate_changed', 'label_conflict',
  'release_conflict', 'release_precondition_required', 'protected_label_forbidden',
  'evidence_mismatch', 'evidence_expired', 'evidence_share_required',
].includes(getSkillErrorCode(error))
  ? [...evidenceParts(id), [id, 'protection'], ...releaseKeys(id).map((key) => ['legacy', ...key.slice(1)])]
  : [];
const releaseInput = ({ expectedDigest, expectedReleaseRevision, evidenceRunId }) => ({
  ...(expectedDigest !== undefined ? { expected_digest: expectedDigest } : {}),
  ...(expectedReleaseRevision !== undefined ? { expected_release_revision: expectedReleaseRevision } : {}),
  ...(evidenceRunId ? { evidence_run_id: evidenceRunId } : {}),
});
export function useCreateSkill() {
  return useSkillV11Mutation({
    send: async ({ templateId, ...input }, config) => mapSkill(result(await axios.post(endpoints.list(), { ...input, ...(templateId ? { template_id: templateId } : {}) }, config))),
    invalidateLegacy: (skill) => draftKeys(skill.id),
  });
}
export function useUpdateSkillTags(id) {
  return useSkillV11Mutation({ send: async ({ tags }, config) => mapSkill(result(await axios.patch(endpoints.detail(id), { tags }, config))), invalidateLegacy: () => [['skills', 'list'], skillsKeys.detail(id)] });
}
export function useArchiveSkill() {
  return useSkillV11Mutation({
    send: async (input, config) => {
      const { id, expectedReleaseRevision } = typeof input === 'string' ? { id: input } : input;
      await axios.delete(endpoints.detail(id), { ...config, data: releaseInput({ expectedReleaseRevision }) });
      return id;
    },
    invalidate: (id) => [[id]], invalidateLegacy: (id) => releaseKeys(id),
  });
}
export function useSetSkillLabel(id) {
  return useSkillV11Mutation({
    send: async ({ label, version, expectedVersion, note, ...release }, config) => mapLabel(result(await axios.put(endpoints.label(id, label), { version, note, ...(expectedVersion !== undefined ? { expected_version: expectedVersion } : {}), ...releaseInput(release) }, config))),
    invalidate: () => [...evidenceParts(id), [id, 'adoption'], [id, 'protection']], invalidateLegacy: () => releaseKeys(id),
    refreshOnError: refreshReleaseOnError(id),
  });
}
export function useRemoveSkillLabel(id) {
  return useSkillV11Mutation({
    send: async ({ label, expectedVersion, expectedReleaseRevision }, config) => { await axios.delete(endpoints.label(id, label), { ...config, data: { ...(expectedVersion !== undefined ? { expected_version: expectedVersion } : {}), ...releaseInput({ expectedReleaseRevision }) } }); },
    invalidate: () => [...evidenceParts(id), [id, 'adoption'], [id, 'protection']], invalidateLegacy: () => releaseKeys(id),
    refreshOnError: refreshReleaseOnError(id),
  });
}
export function useSaveSkillDraft(id) {
  return useSkillV11Mutation({
    send: async ({ files, note, baseVersion, revision }, config) => mapDraft(result(await axios.put(endpoints.draft(id), { files, note, base_version: baseVersion, revision }, config))),
    writeLegacy: (draft) => [[skillsKeys.draft(id), draft]],
    invalidate: () => evidenceParts(id), invalidateLegacy: () => draftKeys(id).filter((key) => key[2] !== 'draft'),
  });
}
export function useDiscardSkillDraft(id) {
  return useSkillV11Mutation({ send: async (_, config) => { await axios.delete(endpoints.draft(id), config); }, invalidate: () => evidenceParts(id), invalidateLegacy: () => draftKeys(id) });
}
export function usePublishSkillDraft(id) {
  return useSkillV11Mutation({
    send: async ({ note, labels, acknowledgeWarnings, force, revision, ...release }, config) => mapPublish(result(await axios.post(endpoints.publishDraft(id), { note, labels, acknowledge_warnings: acknowledgeWarnings, force, revision, ...releaseInput(release) }, config))),
    invalidate: () => [...evidenceParts(id), [id, 'source'], [id, 'adoption'], [id, 'protection']], invalidateLegacy: () => releaseKeys(id),
    refreshOnError: refreshReleaseOnError(id),
  });
}
export function useRestoreSkillVersion(id) {
  return useSkillV11Mutation({ send: async ({ number, note }, config) => mapPublish(result(await axios.post(endpoints.restore(id, number), { note }, config))), invalidate: () => [...evidenceParts(id), [id, 'source']], invalidateLegacy: () => releaseKeys(id) });
}
export function useRunSkillChecks() {
  return useSkillV11Mutation({ send: async (input, config) => mapChecks(result(await axios.post(endpoints.checks(), input, config))) });
}
export function useImportPreview() {
  return useSkillV11Mutation({ send: async (form, config) => mapImportPreview(result(await axios.post(endpoints.importPreview(), form, config))) });
}
export function useImportSkills() {
  return useSkillV11Mutation({
    send: async (form, config) => mapImportResult(result(await axios.post(endpoints.import(), form, config))),
    invalidate: (value) => [...value.created, ...value.updated, ...value.unchanged].flatMap((item) => item.skillId ? [...evidenceParts(item.skillId), [item.skillId, 'adoption'], [item.skillId, 'protection']] : []),
    invalidateLegacy: (value) => [['skills', 'list'], ...[...value.created, ...value.updated, ...value.unchanged].flatMap((item) => item.skillId ? releaseKeys(item.skillId) : [])],
  });
}

async function download(url, params) {
  const response = await axios.get(url, { params, responseType: 'blob' });
  saveAs(response.data, 'futureagi-skills.zip');
  return response.data;
}
export function downloadSkillsZip({ names = [], tag, label, version }) {
  if (label && version != null) throw new Error('Choose a label or a version, not both.');
  if (version != null && (names.length !== 1 || tag)) throw new Error('A pinned version requires exactly one skill.');
  return download(endpoints.download(), { names: names.length ? names.join(',') : undefined, tag, ...(version != null ? { version } : { label: label || 'production' }) });
}
export function downloadVersionZip(id, number) {
  return download(endpoints.versionDownload(id, number));
}
