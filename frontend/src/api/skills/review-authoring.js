import axios from 'src/utils/axios';
import { evidenceOutcome } from 'src/sections/skills/utils/review-display';
import { apiPath } from 'src/api/contracts/api-surface';
import { useSkillV11Query, useSkillV11Mutation, skillV11Result, idempotencyHeaders } from './testing';
import { mapEvidenceRef, mapTargetConfiguration, evidenceSelectionInput, targetConfigurationInput } from './r3-mappers';
import { mapReleasePacket } from './r3-review';
import { loadSkillReview, loadReleaseInspection } from './review-production';
import { loadContextReview } from './context-production';
import { contextEndpoints } from './context-authoring';
import { endpoints } from './endpoints';

const requests = () => apiPath('/model-hub/skills/release-requests/');
const previewPath = () => apiPath('/model-hub/skills/release-requests/preview/');
export function useReviewCandidates(kind, resourceId, page) {
  return useSkillV11Query({ parts: ['review-candidates', kind, resourceId, page], enabled: Boolean(resourceId),
    url: () => kind === 'agent_context' ? contextEndpoints.versions(resourceId) : endpoints.versions(resourceId),
    params: { page, page_size: 25 }, mapper: (value) => value });
}
export function useReviewSubjects(kind, page, search = '') {
  return useSkillV11Query({ parts: ['review-subjects', kind, page, search],
    url: () => kind === 'agent_context' ? contextEndpoints.list() : endpoints.list(),
    params: { page, page_size: 25, search }, mapper: (value) => value });
}
// A listed application run's reduced state, as a status badge; anything else stays unrecorded.
// Shared presentation preserves both text and application outcome vocabulary.
/** The release options for one candidate: its evidence (mapped), targets, recipients and the subject. */
export async function loadReviewOptions(kind, resourceId, candidateId, config = {}) {
  const [value, resource] = await Promise.all([
    axios.get(apiPath('/model-hub/skills/release-requests/options/'), { ...config, params: { resource_id: resourceId, kind, candidate_version_id: candidateId } }).then(skillV11Result),
    axios.get(kind === 'agent_context' ? contextEndpoints.detail(resourceId) : endpoints.detail(resourceId), config).then(skillV11Result).catch(() => null),
  ]);
  return { ...value, subject: resource?.context || resource, evidence: value.evidence.map((row) => ({ ...mapEvidenceRef(row), outcome: evidenceOutcome(row), counts: row.counts || undefined, completedAt: row.completed_at })), targets: value.targets.map((item) => ({ ...item, target: mapTargetConfiguration(item.target) })) };
}
export function useReviewOptions(kind, resourceId, candidateId) {
  return useSkillV11Query({ parts: ['review-options', kind, resourceId, candidateId], enabled: Boolean(resourceId && candidateId),
    load: (config) => loadReviewOptions(kind, resourceId, candidateId, config) });
}
export async function prepareReleaseReview(input, config) {
  const body = { resource_id: input.resourceId, kind: input.kind, candidate_version_id: input.candidateId,
    label: input.label, target: input.target ? targetConfigurationInput(input.target) : null,
    evidence: input.evidence.map(evidenceSelectionInput), external_pipeline: input.externalPipeline };
  const raw = skillV11Result(await axios.post(previewPath(), body, config));
  const inspected = input.kind === 'agent_context'
    ? await loadContextReview(input.resourceId, null, config, { operation: 'move', target_version: raw.packet.transition.version, label: input.label })
    : await loadSkillReview(input.resourceId, raw.packet, config);
  const inspection = await loadReleaseInspection(raw.packet, raw.fingerprint, config);
  const current = skillV11Result(await axios.post(previewPath(), body, config));
  if (!inspected.complete || inspected.candidateDigest !== raw.packet.candidate_digest || current.fingerprint !== raw.fingerprint) throw new Error('Review changed while reading. Prepare a new packet explicitly.');
  return { raw, preview: { packet: mapReleasePacket(raw.packet), fingerprint: raw.fingerprint, current: true },
    review: { ...inspection, complete: true, changes: inspected.originChanges, changesFrom: inspected.changesFrom, dependencyChanges: inspected.dependencyChanges, dependencyError: inspected.dependencyError, checks: inspected.checks, impact: inspected.impact } };
}
export function usePrepareReleaseReview() {
  return useSkillV11Mutation({ send: prepareReleaseReview });
}
export function submitReviewedPacket(prepared, input, config) {
  // Preserve the authoritative wire packet, including typed evidence. Never
  // reconstruct server identities from labels, UI strings or the latest head.
  if (input.expectedFingerprint !== prepared.raw.fingerprint || JSON.stringify(input.packet) !== JSON.stringify(prepared.preview.packet)) throw new Error('The reviewed packet changed.');
  return axios.post(requests(), { packet: prepared.raw.packet, expected_fingerprint: input.expectedFingerprint,
    recipient_ids: input.recipientIds, request_id: input.requestId ?? null, revision: input.revision },
  { ...config, headers: { ...config.headers, ...idempotencyHeaders(input.idempotencyKey) } }).then(skillV11Result);
}
export function useSubmitReleaseReview() {
  return useSkillV11Mutation({ send: ({ prepared, ...input }, config) => submitReviewedPacket(prepared, input, config),
    invalidate: () => [['review-inbox'], ['release-request']] });
}
export function useShareReleaseEvidence(resourceId, kind) {
  return useSkillV11Mutation({ send: async ({ source, sourceId, acknowledgeSnapshotSharing }, config) => {
    const url = source === 'simulation_native'
      ? apiPath('/model-hub/skills/release-requests/native-evidence/{evidence_id}/share/', { evidence_id: sourceId })
      : kind === 'agent_context'
        ? apiPath('/model-hub/skills/contexts/{context_id}/test-runs/{run_id}/share/', { context_id: resourceId, run_id: sourceId })
        : endpoints.shareTestRun(resourceId, sourceId);
    return skillV11Result(await axios.post(url, { acknowledge_snapshot_sharing: acknowledgeSnapshotSharing }, config));
  }, invalidate: () => [['review-options']] });
}
