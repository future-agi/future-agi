import axios from 'src/utils/axios';
import { apiPath, getContractedApiMethods } from 'src/api/contracts/api-surface';
import { useSkillV11Query, useSkillV11Mutation, skillV11Result, idempotencyHeaders } from './testing';
import { mapV11Page } from './v11-mappers';
import { mapReleasePacket, mapPointerTransition } from './r3-review';
import { loadContextReview } from './context-production';
import { endpoints } from './endpoints';
import { contextEndpoints } from './context-authoring';
import { mapChecks, mapCompare } from './mappers';
import { readSkillFile } from './index';
import { operationStatus } from './operation-errors';

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
    complete: true, candidateDigest: candidate.digest, originChanges: changes, changesFrom: packet.transition.expected_version, checks: mapChecks(candidate.checks),
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
export const mapRequest = (value) => ({ id: value.id, revision: value.revision, status: value.status, subjectId: value.subject_id, subjectKind: value.subject_kind, name: value.name, unread: value.unread, authorId: value.author_id, authorName: value.author_name, recipients: value.recipients, decision: value.decision, decidedByName: value.decided_by_name, createdAt: value.created_at, decidedAt: value.decided_at || (value.status === 'approved' ? value.updated_at : null), updatedAt: value.updated_at, transition: value.transition ? mapPointerTransition(value.transition) : value.packet?.transition ? mapPointerTransition(value.packet.transition) : undefined, packet: value.packet ? mapReleasePacket(value.packet) : undefined });
export function useReviewInbox({ page = 1, status = '', authored = false } = {}) {
  return useSkillV11Query({ parts: ['review-inbox', page, status, authored], enabled: reviewsReady(), load: async (config) => {
    const value = skillV11Result(await axios.get(authored ? apiPath('/model-hub/skills/release-requests/') : inbox(), { ...config, params: { page, ...(status ? { status } : {}) } }));
    // The list contract has no transition. Read only authorized detail for this
    // page and keep metadata separate from evidence; an expired/denied detail
    // must not remove the row or turn into fabricated version/actor values.
    const results = await Promise.all(value.results.map(async (row) => {
      try {
        const item = skillV11Result(await axios.get(detail(row.id), config));
        return item.revision === row.revision ? { ...row, author_id: item.author_id, author_name: item.author_name, transition: item.packet?.transition } : row;
      } catch { return row; }
    }));
    return { ...mapV11Page({ ...value, results }, mapRequest), unreadCount: value.unread_count };
  } });
}
export function useMarkReviewSeen() {
  return useSkillV11Mutation({ send: async ({ requestId, revision }, config) => { await axios.post(seen(requestId), { revision }, config); return { acknowledged: true }; }, invalidate: () => [['review-inbox']] });
}
export const mapReleaseReceipt = (result) => ({ requestId: result.request_id, decisionId: result.decision_id, revision: result.revision, executedBy: result.executed_by, executedByName: result.executed_by_name, approvedByName: result.approved_by_name, recordedAt: result.executed_at, name: result.name, subjectId: result.subject_id, subjectKind: result.subject_kind, previousVersion: result.previous_version, currentnessAtAction: result.currentness_at_action, postMoveState: { label: result.post_move_state.label, version: result.post_move_state.version, releaseRevision: result.post_move_state.release_revision, eventId: result.post_move_state.event_id } });

// Inspection reads only the reviewed immutable packet. The server revalidates
// after reading capabilities, target configuration and annotation summaries.
export async function loadReleaseInspection(packet, fingerprint, config) {
  const value = skillV11Result(await axios.post(apiPath('/model-hub/skills/release-requests/inspection/'), {
    resource_id: packet.subject_id, kind: packet.subject_kind,
    candidate_version_id: packet.candidate_version_id, label: packet.transition.label,
    target: packet.target, evidence: packet.evidence.map((ref) => ({ source: ref.source, source_id: ref.source_id, annotation_revision: ref.annotation_revision })),
    external_pipeline: packet.external_pipeline, expected_fingerprint: fingerprint,
  }, config));
  if (value.fingerprint !== fingerprint || value.capabilities.after_digest !== packet.candidate_digest) throw new Error('The inspection does not match the reviewed packet.');
  if (value.summaries.length !== packet.evidence.length || packet.evidence.some((ref) => !value.summaries.some((row) => row.source === ref.source && row.source_id === ref.source_id && row.annotation_revision === ref.annotation_revision))) throw new Error('The evidence inspection is incomplete.');
  return { capabilities: { ...value.capabilities, beforeDigest: value.capabilities.before_digest, afterDigest: value.capabilities.after_digest },
    summaries: value.summaries.map((row) => ({ ...row, sourceId: row.source_id, annotationRevision: row.annotation_revision, cannotAssess: row.cannot_assess,
      cases: (row.cases || []).map((cell) => ({ ...cell, caseId: cell.case_id, repeatIndex: cell.repeat_index })) })),
    targetConfiguration: value.target_configuration };
}

// --- J4: explicit reviewer renewal -----------------------------------------
// These server codes mean the reviewed packet no longer matches current state.
// They never refresh preconditions; they only expose explicit renewal.
export const STALE_REVIEW_CODES = Object.freeze(['release_conflict', 'impact_changed', 'target_configuration_changed', 'target_retired', 'annotation_conflict', 'evidence_expired', 'evidence_share_required', 'review_stale', 'review_conflict']);
export const isStaleReview = (error) => [403, 409, 410].includes(operationStatus(error)) && STALE_REVIEW_CODES.includes(error?.code);
const renewalPath = (request_id) => apiPath('/model-hub/skills/release-requests/{request_id}/renewal/', { request_id });
export const mapRenewal = (value) => {
  const packet = mapReleasePacket(value.packet);
  return {
    requestId: value.request_id, revision: value.revision, status: value.status, subjectId: value.subject_id,
    previousFingerprint: value.previous_fingerprint, fingerprint: value.fingerprint,
    packet: { ...packet, evidence: packet.evidence.map((ref) => ({ ...ref, resultState: 'not_loaded' })) },
    changes: value.changes.map((row) => ({ item: row.item, before: row.before, after: row.after })),
    removedEvidence: value.removed_evidence.map((row) => ({ source: row.source, sourceId: row.source_id, reason: row.reason })),
  };
};
export async function loadRenewal(requestId, config) {
  return mapRenewal(skillV11Result(await axios.get(renewalPath(requestId), config)));
}
export function useRenewalPreview(requestId, enabled) {
  return useSkillV11Query({ parts: ['release-renewal', requestId], enabled: Boolean(requestId) && enabled && getContractedApiMethods('/model-hub/skills/release-requests/{request_id}/renewal/').includes('get'), load: (config) => loadRenewal(requestId, config) });
}
export function useRenewReleaseRequest() {
  return useSkillV11Mutation({ send: async ({ requestId, revision, expectedFingerprint, idempotencyKey }, config) => {
    const value = skillV11Result(await axios.post(renewalPath(requestId), { revision, expected_fingerprint: expectedFingerprint }, { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } }));
    return { id: value.id, revision: value.revision, status: value.status, renewedBy: value.renewed_by, fromRevision: value.from_revision };
  }, invalidate: () => [['release-request'], ['release-renewal'], ['review-inbox']] });
}

export async function loadReleaseRequest(requestId, contextId, config, rereadAfterRenewal = false) {
    let raw;
    try {
      raw = skillV11Result(await axios.get(detail(requestId), config));
    } catch (error) {
      // Packet detail intentionally disappears when evidence is no longer shared
      // or retained. The separately authorized metadata receipt remains readable.
      // A context-scoped route must still validate its subject; never bypass it.
      const status = operationStatus(error);
      const unavailableEvidence = [404, 410].includes(status) || (status === 403 && error.code === 'evidence_share_required');
      if (unavailableEvidence && status !== 404) {
        // An assigned reviewer can still inspect what changed and renew
        // explicitly. The renewal endpoint authorizes only that reviewer.
        let renewal = null;
        let renewedMeanwhile = false;
        try { renewal = await loadRenewal(requestId, config); } catch (renewalError) {
          // The packet was renewed after this read started: read it again once.
          renewedMeanwhile = operationStatus(renewalError) === 409 && renewalError?.code === 'review_current';
        }
        if (renewedMeanwhile && !rereadAfterRenewal) return loadReleaseRequest(requestId, contextId, config, true);
        if (renewal && (!contextId || renewal.subjectId === contextId)) {
          // The renewal endpoint authorizes this read separately. Show its
          // candidate/transition read-only; never call it the expired packet.
          let name;
          try {
            const url = renewal.packet.subjectKind === 'agent_context' ? contextEndpoints.detail(renewal.subjectId) : endpoints.detail(renewal.subjectId);
            const resource = skillV11Result(await axios.get(url, config));
            name = resource.context?.name || resource.name;
          } catch { /* Metadata is optional; this never grants authority. */ }
          let review = { complete: false, evidence: renewal.packet.evidence.map((ref) => ({ ...ref, resultState: 'not_loaded' })) };
          try {
            const next = renewal.packet;
            const inspected = next.subjectKind === 'agent_context'
              ? await loadContextReview(renewal.subjectId, null, config, { operation: 'move', target_version: next.transition.version, label: next.transition.label })
              : await loadSkillReview(renewal.subjectId, { candidate_version_id: next.candidateVersionId, candidate_digest: next.candidateDigest, transition: { label: next.transition.label, version: next.transition.version, expected_version: next.transition.expectedVersion } }, config);
            const label = inspected.releaseState?.labels.find((item) => item.name === next.transition.label);
            // Context comparison uses the current pointer. Never present it as
            // this renewal's diff if that pointer moved during the read.
            const matchesBaseline = next.subjectKind === 'skill' || (label?.version ?? null) === next.transition.expectedVersion;
            if (inspected.candidateDigest === next.candidateDigest && matchesBaseline) review = { ...review, complete: false, changes: inspected.originChanges, changesFrom: inspected.changesFrom, dependencyChanges: inspected.dependencyChanges, dependencyError: inspected.dependencyError, checks: inspected.checks };
          } catch { /* Read-only detail is optional. Renewal remains explicit. */ }
          return { request: { id: requestId, revision: renewal.revision, status: renewal.status, name, subjectId: renewal.subjectId, subjectKind: renewal.packet.subjectKind }, preview: { packet: renewal.packet, fingerprint: renewal.fingerprint, current: false }, review, renewalRequired: error.code || 'evidence_expired', canExecute: false };
        }
      }
      if (contextId || !unavailableEvidence) throw error;
      const retained = skillV11Result(await axios.get(apiPath('/model-hub/skills/release-requests/{request_id}/receipt/', { request_id: requestId }), config));
      if (retained.request_id !== requestId) throw new Error('The release receipt does not match this request.');
      return { request: { id: requestId, status: 'released', revision: retained.revision }, receipt: mapReleaseReceipt(retained), canExecute: false };
    }
    if (contextId && raw.subject_id !== contextId) throw new Error('This review belongs to another context.');
    const packet = raw.packet;
    // A released packet is historical. Its old preconditions necessarily differ
    // after the move, and its evidence can later expire. Read the durable receipt
    // without trying to authorize that old transition against today's state.
    if (raw.status === 'released') {
      const receipt = skillV11Result(await axios.get(apiPath('/model-hub/skills/release-requests/{request_id}/receipt/', { request_id: requestId }), config));
      if (receipt.request_id !== raw.id || receipt.revision !== raw.revision) throw new Error('The release receipt does not match this request.');
      return { request: mapRequest({ ...raw, subject_kind: packet.subject_kind }), preview: { packet: mapReleasePacket(packet), fingerprint: raw.fingerprint, current: false }, receipt: mapReleaseReceipt(receipt), canExecute: false };
    }
    const inspected = packet.subject_kind === 'agent_context'
      ? await loadContextReview(raw.subject_id, null, config, { operation: 'move', target_version: packet.transition.version, label: packet.transition.label })
      : packet.subject_kind === 'skill' ? await loadSkillReview(raw.subject_id, packet, config) : null;
    // A race makes the reviewed packet stale. Keep the retained packet visible,
    // mark it not current and expose explicit renewal; never re-derive it here.
    let staleCode = null;
    let inspection;
    try { inspection = await loadReleaseInspection(packet, raw.fingerprint, config); } catch (error) {
      if (!isStaleReview(error)) throw error;
      staleCode = error.code;
      inspection = { capabilities: null, summaries: [], targetConfiguration: null, evidence: mapReleasePacket(packet).evidence.map((ref) => ({ ...ref, resultState: 'not_loaded' })) };
    }
    // Revalidate after all comparison reads, not before potentially slow pages.
    let current = null;
    try {
      current = skillV11Result(await axios.post(preview(), { resource_id: raw.subject_id, kind: packet.subject_kind, candidate_version_id: packet.candidate_version_id, label: packet.transition.label, target: packet.target, evidence: packet.evidence.map((ref) => ({ source: ref.source, source_id: ref.source_id, annotation_revision: ref.annotation_revision })), external_pipeline: packet.external_pipeline }, config));
    } catch (error) {
      if (!isStaleReview(error)) throw error;
      staleCode = staleCode || error.code;
    }
    const release = inspected?.releaseState;
    const label = release?.labels.find((item) => item.name === packet.transition.label);
    const canExecute = label ? label.canMove === true : release?.protectedLabelNames?.includes(packet.transition.label) ? release.permissions.canManageProtection === true : release?.permissions.canPromote === true;
    return {
      request: mapRequest({ ...raw, subject_kind: packet.subject_kind }),
      preview: { packet: mapReleasePacket(packet), fingerprint: raw.fingerprint, current: Boolean(!staleCode && inspected?.complete && inspected.candidateDigest === packet.candidate_digest && current?.fingerprint === raw.fingerprint) },
      review: inspected ? { ...inspection, complete: inspected.complete && !staleCode, changes: inspected.originChanges, changesFrom: inspected.changesFrom, dependencyChanges: inspected.dependencyChanges, dependencyError: inspected.dependencyError, checks: inspected.checks, impact: inspected.impact } : undefined,
      canExecute, staleCode,
    };
}
export function useReleaseRequest(requestId, contextId) {
  return useSkillV11Query({ parts: ['release-request', requestId, contextId], enabled: Boolean(requestId) && reviewsReady(), load: (config) => loadReleaseRequest(requestId, contextId, config) });
}
const decisionInput = ({ revision, expectedFingerprint, decision, comment }) => ({ revision, expected_fingerprint: expectedFingerprint, decision, comment });
export function useDecideReleaseRequest() {
  return useSkillV11Mutation({ send: async ({ requestId, idempotencyKey, ...input }, config) => mapRequest(skillV11Result(await axios.post(decisions(requestId), decisionInput(input), { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } }))), invalidate: () => [['release-request'], ['review-inbox']] });
}
export function useExecuteReleaseRequest() {
  return useSkillV11Mutation({ send: async ({ requestId, revision, expectedFingerprint, idempotencyKey }, config) => {
    const result = skillV11Result(await axios.post(execute(requestId), { revision, expected_fingerprint: expectedFingerprint }, { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } }));
    return mapReleaseReceipt(result);
  }, invalidate: () => [['release-request'], ['review-inbox'], ['agent_context']] });
}
