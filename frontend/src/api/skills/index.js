import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { saveAs } from 'file-saver';
import axios from 'src/utils/axios';
import { endpoints } from './endpoints';
import { skillsKeys } from './keys';
import {
  mapSkill, mapPage, mapTemplate, mapVersion, mapCompare, mapLabel,
  mapLabelEvent, mapDraft, mapUsage, mapPublish, mapChecks,
  mapImportPreview, mapImportResult,
} from './mappers';

export { skillsKeys } from './keys';
export { endpoints } from './endpoints';
export const getSkillErrorCode = (error) => error?.code || error?.response?.data?.code;
const result = (response) => response.data.result;
const pageParams = ({ pageSize, ...params } = {}) => ({ ...params, ...(pageSize != null ? { page_size: pageSize } : {}) });

function useSkillsQuery(queryKey, url, mapper, params, enabled = true) {
  return useQuery({
    queryKey,
    queryFn: async ({ signal }) => mapper(result(await axios.get(url(), { params, signal }))),
    enabled,
    meta: { errorHandled: true },
  });
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
export async function readSkillFile(id, number, path, signal) {
  let offset = 0;
  let first;
  const chunks = [];
  for (;;) {
    const chunk = result(await axios.get(endpoints.file(id, number), { params: { path, offset }, signal }));
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
  return useQuery({ queryKey: skillsKeys.file(id, number, path), queryFn: ({ signal }) => readSkillFile(id, number, path, signal), enabled: Boolean(id && number && path), meta: { errorHandled: true } });
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
  return useQuery({
    queryKey: skillsKeys.draft(id), enabled: Boolean(id), meta: { errorHandled: true },
    queryFn: async ({ signal }) => {
      try { return mapDraft(result(await axios.get(endpoints.draft(id), { signal }))); }
      catch (error) { if (getSkillErrorCode(error) === 'draft_not_found') return null; throw error; }
    },
  });
}
export function useSkillUsage(id, days) {
  return useSkillsQuery(skillsKeys.usage(id, days), () => endpoints.usage(id), mapUsage, { days }, Boolean(id));
}

function useSkillsMutation(mutationFn, invalidate = true) {
  const client = useQueryClient();
  return useMutation({
    mutationFn,
    meta: { errorHandled: true },
    onSuccess: () => invalidate ? client.invalidateQueries({ queryKey: skillsKeys.all }) : undefined,
  });
}
export function useCreateSkill() {
  return useSkillsMutation(async ({ templateId, ...input }) => mapSkill(result(await axios.post(endpoints.list(), { ...input, ...(templateId ? { template_id: templateId } : {}) }))));
}
export function useUpdateSkillTags(id) {
  return useSkillsMutation(async ({ tags }) => mapSkill(result(await axios.patch(endpoints.detail(id), { tags }))));
}
export function useArchiveSkill() {
  return useSkillsMutation(async (id) => { await axios.delete(endpoints.detail(id)); });
}
export function useSetSkillLabel(id) {
  return useSkillsMutation(async ({ label, version, expectedVersion, note }) => mapLabel(result(await axios.put(endpoints.label(id, label), { version, note, ...(expectedVersion !== undefined ? { expected_version: expectedVersion } : {}) }))));
}
export function useRemoveSkillLabel(id) {
  return useSkillsMutation(async ({ label, expectedVersion }) => { await axios.delete(endpoints.label(id, label), { data: { ...(expectedVersion !== undefined ? { expected_version: expectedVersion } : {}) } }); });
}
export function useSaveSkillDraft(id) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ files, note, baseVersion, revision }) => mapDraft(result(await axios.put(endpoints.draft(id), { files, note, base_version: baseVersion, revision }))),
    meta: { errorHandled: true },
    onSuccess: (draft) => {
      client.setQueryData(skillsKeys.draft(id), draft);
      // A save changes draft indicators and draft compares, not immutable files.
      client.invalidateQueries({ queryKey: ['skills', 'list'] });
      client.invalidateQueries({ queryKey: skillsKeys.detail(id) });
      client.invalidateQueries({ queryKey: ['skills', id, 'compare'] });
    },
  });
}
export function useDiscardSkillDraft(id) {
  return useSkillsMutation(async () => { await axios.delete(endpoints.draft(id)); });
}
export function usePublishSkillDraft(id) {
  return useSkillsMutation(async ({ note, labels, acknowledgeWarnings, force }) => mapPublish(result(await axios.post(endpoints.publishDraft(id), { note, labels, acknowledge_warnings: acknowledgeWarnings, force }))));
}
export function useRestoreSkillVersion(id) {
  return useSkillsMutation(async ({ number, note }) => mapPublish(result(await axios.post(endpoints.restore(id, number), { note }))));
}
export function useRunSkillChecks() {
  return useSkillsMutation(async (input) => mapChecks(result(await axios.post(endpoints.checks(), input))), false);
}
export function useImportPreview() {
  return useSkillsMutation(async (form) => mapImportPreview(result(await axios.post(endpoints.importPreview(), form))), false);
}
export function useImportSkills() {
  return useSkillsMutation(async (form) => mapImportResult(result(await axios.post(endpoints.import(), form))));
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
