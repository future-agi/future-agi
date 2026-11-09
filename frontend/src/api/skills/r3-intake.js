import axios from 'src/utils/axios';
import { apiPath, getContractedApiMethods } from 'src/api/contracts/api-surface';
import { idempotencyHeaders, skillV11Result, useSkillV11Mutation, useSkillV11Query, v11PageParams } from './testing';
import { mapV11Page } from './v11-mappers';

// Retained intake operations use the server's reviewed plan, never fresh bytes.
export const retainedPlanAcceptanceInput = (value) => ({
  plan_id: value.planId, expected_fingerprint: value.expectedFingerprint, item_key: value.itemKey,
  mode: value.mode, expected_head_version: value.expectedHeadVersion, expected_head_digest: value.expectedHeadDigest,
  rights_confirmed: value.rightsConfirmed,
  ...(Object.hasOwn(value, 'tags') ? { tags: value.tags } : {}),
});
export const checkpointRestoreInput = (value) => ({
  checkpoint_id: value.checkpointId, revision: value.revision, expected_digest: value.expectedDigest,
});
// Keep is a separate explicit decision. Never pass it through the replacement
// mapper, which intentionally accepts no decision field.
export const retainedPlanKeepInput = (value) => ({
  plan_id: value.planId, expected_fingerprint: value.expectedFingerprint, item_key: value.itemKey,
  expected_head_version: value.expectedHeadVersion, expected_head_digest: value.expectedHeadDigest,
  decision: 'keep', mode: 'snapshot', rights_confirmed: value.rightsConfirmed,
});
export function checkpointCreateInput(value) {
  if (!/^sha256:[a-f0-9]{64}$/.test(value.expectedDigest || '')) throw new Error('Use the saved recovery digest for the checkpoint.');
  const reason = value.reason === 'switch' ? 'suspension' : value.reason;
  if (!['deletion', 'replacement', 'suspension', 'manual'].includes(reason)) throw new Error('Select an explicit checkpoint reason.');
  return { revision: value.revision, expected_digest: value.expectedDigest, reason, suspend: value.suspend ?? reason === 'suspension' };
}
export const mapCheckpoint = (value) => ({
  id: value.id, resourceId: value.resource_id, draftRevision: value.draft_revision,
  reason: value.reason, digest: value.digest, createdAt: value.created_at, expiresAt: value.expires_at,
  payloadExpired: value.payload_expired, restoredRevision: value.restored_revision,
});
const checkpointEndpoint = (kind, resource_id) => {
  if (kind === 'agent_context') return apiPath('/model-hub/skills/contexts/{resource_id}/draft/checkpoints/', { resource_id });
  if (kind === 'skill') return apiPath('/model-hub/skills/{resource_id}/draft/checkpoints/', { resource_id });
  throw new Error('Choose an explicit resource kind.');
};
export const checkpointsReady = (kind) => getContractedApiMethods(kind === 'agent_context' ? '/model-hub/skills/contexts/{resource_id}/draft/checkpoints/' : '/model-hub/skills/{resource_id}/draft/checkpoints/').includes('post');
export function useDraftCheckpoints(kind, resourceId, params = {}) {
  return useSkillV11Query({ parts: [kind, resourceId, 'checkpoints', params], url: () => checkpointEndpoint(kind, resourceId), params: v11PageParams(params), mapper: (value) => mapV11Page(value, mapCheckpoint), enabled: Boolean(resourceId) && checkpointsReady(kind) });
}
export function useCheckpointDraft(kind, resourceId) {
  return useSkillV11Mutation({
    send: async ({ idempotencyKey, ...input }, config) => {
      const value = skillV11Result(await axios.post(checkpointEndpoint(kind, resourceId), checkpointCreateInput(input), { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } }));
      return { checkpoint: mapCheckpoint(value.checkpoint), suspended: value.suspended, draftRevision: value.draft_revision, retainedRevision: value.retained_revision };
    },
    invalidate: () => [[kind, resourceId]],
    // Legacy draft keys are still scope/principal-partitioned by the common hook.
    invalidateLegacy: () => kind === 'skill' ? [['skills', resourceId, 'draft'], ['skills', resourceId, 'detail']] : [],
  });
}
