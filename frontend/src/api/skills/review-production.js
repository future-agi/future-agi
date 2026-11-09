import axios from 'src/utils/axios';
import { apiPath, getContractedApiMethods } from 'src/api/contracts/api-surface';
import { useSkillV11Query, useSkillV11Mutation, skillV11Result, idempotencyHeaders } from './testing';
import { mapV11Page } from './v11-mappers';
import { mapReleasePacket } from './r3-review';
import { loadContextReview } from './context-production';

const inbox = () => apiPath('/model-hub/skills/review-inbox/');
const seen = (request_id) => apiPath('/model-hub/skills/review-inbox/{request_id}/seen/', { request_id });
const detail = (request_id) => apiPath('/model-hub/skills/release-requests/{request_id}/', { request_id });
const preview = () => apiPath('/model-hub/skills/release-requests/preview/');
const decisions = (request_id) => apiPath('/model-hub/skills/release-requests/{request_id}/decisions/', { request_id });
const execute = (request_id) => apiPath('/model-hub/skills/release-requests/{request_id}/execute/', { request_id });
export const reviewsReady = () => getContractedApiMethods('/model-hub/skills/review-inbox/').includes('get') && getContractedApiMethods('/model-hub/skills/release-requests/{request_id}/').includes('get');
const mapRequest = (value) => ({ id: value.id, revision: value.revision, status: value.status, subjectId: value.subject_id, subjectKind: value.subject_kind, name: value.name, unread: value.unread, authorId: value.author_id });
export function useReviewInbox({ page = 1, status = '' } = {}) {
  return useSkillV11Query({ parts: ['review-inbox', page, status], url: inbox, params: { page, ...(status ? { status } : {}) }, enabled: reviewsReady(), mapper: (value) => ({ ...mapV11Page(value, mapRequest), unreadCount: value.unread_count }) });
}
export function useMarkReviewSeen() {
  return useSkillV11Mutation({ send: async ({ requestId, revision }, config) => { await axios.post(seen(requestId), { revision }, config); return { acknowledged: true }; }, invalidate: () => [['review-inbox']] });
}
export function useReleaseRequest(requestId, contextId) {
  return useSkillV11Query({ parts: ['release-request', requestId, contextId], enabled: Boolean(requestId) && reviewsReady(), load: async (config) => {
    const raw = skillV11Result(await axios.get(detail(requestId), config));
    if (contextId && raw.subject_id !== contextId) throw new Error('This review belongs to another context.');
    const packet = raw.packet;
    const current = skillV11Result(await axios.post(preview(), { resource_id: raw.subject_id, kind: packet.subject_kind, candidate_version_id: packet.candidate_version_id, label: packet.transition.label, target: packet.target, evidence: packet.evidence.map((ref) => ({ source: ref.source, source_id: ref.source_id, annotation_revision: ref.annotation_revision })), external_pipeline: packet.external_pipeline }, config));
    const inspected = packet.subject_kind === 'agent_context' ? await loadContextReview(raw.subject_id, null, config, { operation: 'move', target_version: packet.transition.version, label: packet.transition.label }) : null;
    return { request: mapRequest(raw), preview: { packet: mapReleasePacket(packet), fingerprint: raw.fingerprint, current: Boolean(inspected?.complete && inspected.candidateDigest === packet.candidate_digest && current.fingerprint === raw.fingerprint) }, review: inspected ? { changes: inspected.originChanges, checks: inspected.checks } : undefined, canExecute: inspected?.releaseState.permissions.canPromote === true };
  } });
}
const decisionInput = ({ revision, expectedFingerprint, decision, comment }) => ({ revision, expected_fingerprint: expectedFingerprint, decision, comment });
export function useDecideReleaseRequest() {
  return useSkillV11Mutation({ send: async ({ requestId, idempotencyKey, ...input }, config) => mapRequest(skillV11Result(await axios.post(decisions(requestId), decisionInput(input), { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } }))), invalidate: () => [['release-request'], ['review-inbox']] });
}
export function useExecuteReleaseRequest() {
  return useSkillV11Mutation({ send: async ({ requestId, revision, expectedFingerprint, idempotencyKey }, config) => {
    const result = skillV11Result(await axios.post(execute(requestId), { revision, expected_fingerprint: expectedFingerprint }, { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } }));
    return { requestId: result.request_id, decisionId: result.decision_id, revision: result.revision, executedBy: result.executed_by, currentnessAtAction: result.currentness_at_action, postMoveState: { label: result.post_move_state.label, version: result.post_move_state.version, releaseRevision: result.post_move_state.release_revision, eventId: result.post_move_state.event_id } };
  }, invalidate: () => [['release-request'], ['review-inbox'], ['agent_context']] });
}
