import axios from 'src/utils/axios';
import { apiPath, getContractedApiMethods } from 'src/api/contracts/api-surface';
import { useSkillV11Query, useSkillV11Mutation, skillV11Result, idempotencyHeaders } from './testing';
import { mapV11Page } from './v11-mappers';
import { mapReleasePacket } from './r3-review';
import { loadContextReview } from './context-production';
import { endpoints } from './endpoints';
import { mapChecks, mapCompare } from './mappers';
import { readSkillFile } from './index';

// Leaf packets pin both sides of the transition, not whichever versions happen
// to be latest when a reviewer opens the inbox.
export async function loadSkillReview(skillId, packet, config) {
  const [skill, candidate] = await Promise.all([
    axios.get(endpoints.detail(skillId), config).then(skillV11Result),
    axios.get(endpoints.version(skillId, packet.transition.version), config).then(skillV11Result),
  ]);
  if (candidate.id !== packet.candidate_version_id || candidate.digest !== packet.candidate_digest) throw new Error('The candidate no longer matches the release packet.');
  let changes;
  if (packet.transition.expected_version !== null) {
    const comparison = skillV11Result(await axios.get(endpoints.compare(skillId), { ...config, params: { from: packet.transition.expected_version, to: packet.transition.version } }));
    if (comparison.from !== packet.transition.expected_version || comparison.to !== packet.transition.version || !Array.isArray(comparison.files)) throw new Error('The complete candidate comparison is unavailable.');
    changes = mapCompare(comparison).files.filter((file) => file.status !== 'unchanged');
    if (changes.some((file) => typeof file.fromText !== 'string' || typeof file.toText !== 'string')) throw new Error('The complete candidate comparison is unavailable.');
  } else {
    if (!Array.isArray(candidate.files) || candidate.files.length !== candidate.file_count) throw new Error('The complete candidate inventory is unavailable.');
    changes = await Promise.all(candidate.files.map(async (file) => {
      const content = await readSkillFile(skillId, candidate.number, file.path, config.signal, config.headers);
      if (content.sha256 !== file.sha256) throw new Error('The candidate file identity changed.');
      return { path: file.path, status: 'added', fromText: '', toText: content.text };
    }));
  }
  const label = skill.labels.find((item) => item.name === packet.transition.label);
  return {
    complete: true, candidateDigest: candidate.digest, originChanges: changes, checks: mapChecks(candidate.checks),
    // Leaf packets have no context-binding fleet inventory. Never invent zero
    // consumers from the existence of an impact fingerprint.
    impact: null,
    releaseState: { labels: [], permissions: { canPromote: label ? label.can_move === true : skill.permissions?.can_manage_label_protection === true } },
  };
}

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
    const inspected = packet.subject_kind === 'agent_context'
      ? await loadContextReview(raw.subject_id, null, config, { operation: 'move', target_version: packet.transition.version, label: packet.transition.label })
      : packet.subject_kind === 'skill' ? await loadSkillReview(raw.subject_id, packet, config) : null;
    // Revalidate after all comparison reads, not before potentially slow pages.
    const current = skillV11Result(await axios.post(preview(), { resource_id: raw.subject_id, kind: packet.subject_kind, candidate_version_id: packet.candidate_version_id, label: packet.transition.label, target: packet.target, evidence: packet.evidence.map((ref) => ({ source: ref.source, source_id: ref.source_id, annotation_revision: ref.annotation_revision })), external_pipeline: packet.external_pipeline }, config));
    const release = inspected?.releaseState;
    const label = release?.labels.find((item) => item.name === packet.transition.label);
    const canExecute = label ? label.canMove === true : release?.protectedLabelNames?.includes(packet.transition.label) ? release.permissions.canManageProtection === true : release?.permissions.canPromote === true;
    return {
      request: mapRequest({ ...raw, subject_kind: packet.subject_kind }),
      preview: { packet: mapReleasePacket(packet), fingerprint: raw.fingerprint, current: Boolean(inspected?.complete && inspected.candidateDigest === packet.candidate_digest && current.fingerprint === raw.fingerprint) },
      review: inspected ? { complete: inspected.complete, changes: inspected.originChanges, checks: inspected.checks, impact: inspected.impact } : undefined,
      canExecute,
    };
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
