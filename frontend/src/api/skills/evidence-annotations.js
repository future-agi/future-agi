import axios from 'src/utils/axios';
import { apiPath } from 'src/api/contracts/api-surface';
import { skillV11Result, useSkillV11Mutation, useSkillV11Query, v11PageParams } from './testing';
import { mapEvidenceRef } from './r3-mappers';
import { humanAnnotationInput } from './r3-cases';
import { mapV11Page } from './v11-mappers';

const endpoint = (source, source_id, annotations = false) => annotations
  ? apiPath('/model-hub/skills/evidence/{source}/{source_id}/annotations/', { source, source_id })
  : apiPath('/model-hub/skills/evidence/{source}/{source_id}/', { source, source_id });
const parts = (source, sourceId) => ['evidenceAnnotations', source, sourceId];
const mapAnnotation = (value) => ({
  id: value.id, revision: value.revision, actorId: value.actor_id,
  checkId: value.check_id, attemptId: value.attempt_id, verdict: value.verdict,
  citedText: value.cited_text, comment: value.comment, createdAt: value.created_at,
});
export function useEvidenceReference(source, sourceId) {
  return useSkillV11Query({ parts: [...parts(source, sourceId), 'reference'], url: () => endpoint(source, sourceId), mapper: mapEvidenceRef, enabled: Boolean(sourceId) });
}
export function useEvidenceAnnotations(source, sourceId, page) {
  return useSkillV11Query({ parts: [...parts(source, sourceId), 'page', page], url: () => endpoint(source, sourceId, true), params: v11PageParams({ page, pageSize: 25 }), mapper: (value) => mapV11Page(value, mapAnnotation), enabled: Boolean(sourceId) });
}
// Read every page through the scoped endpoint; never follow a server-provided URL.
export async function loadEvidenceAnnotations(source, sourceId, config) {
  const results = [];
  const ids = new Set();
  for (let page = 1; page <= 1000; page += 1) {
    const value = skillV11Result(await axios.get(endpoint(source, sourceId, true), {
      ...config, params: v11PageParams({ page, pageSize: 100 }),
    }));
    for (const row of value.results) {
      if (ids.has(row.id)) throw new Error('Human reviews changed while loading. Retry to read the complete history.');
      ids.add(row.id);
      results.push(mapAnnotation(row));
    }
    if ('next' in value ? value.next === null : Number.isSafeInteger(value.total_pages) && page >= value.total_pages) return { results };
    if (!value.results.length) throw new Error('The complete human review history is unavailable.');
  }
  throw new Error('The complete human review history is unavailable.');
}
export function useAllEvidenceAnnotations(source, sourceId) {
  return useSkillV11Query({
    parts: [...parts(source, sourceId), 'all'], enabled: Boolean(sourceId),
    load: (config) => loadEvidenceAnnotations(source, sourceId, config),
  });
}
export function useAppendEvidenceAnnotation(source, sourceId) {
  return useSkillV11Mutation({
    send: async (input, config) => {
      // The route owns evidence identity. Do not accept a second identity in the body.
      if (input.source !== source || input.sourceId !== sourceId) throw new Error('The selected evidence changed. Reopen its review.');
      const { source: _source, source_id: _sourceId, ...body } = humanAnnotationInput(input);
      return mapAnnotation(skillV11Result(await axios.post(endpoint(source, sourceId, true), body, config)));
    },
    invalidate: () => [parts(source, sourceId), ['release-request'], ['review-inbox']],
    refreshOnError: () => [parts(source, sourceId)],
  });
}
