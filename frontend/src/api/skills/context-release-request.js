import axios from 'src/utils/axios';
import { apiPath } from 'src/api/contracts/api-surface';
import { useSkillV11Mutation, skillV11Result, idempotencyHeaders } from './testing';

export function usePreviewContextLabelRequest(contextId) {
  return useSkillV11Mutation({ send: async ({ versionId, label }, config) => skillV11Result(await axios.post(apiPath('/model-hub/skills/release-requests/preview/'), {
    resource_id: contextId, kind: 'agent_context', candidate_version_id: versionId, label, evidence: [],
  }, config)) });
}
export function useSubmitContextLabelRequest() {
  return useSkillV11Mutation({
    send: async ({ packet, fingerprint, reviewerId, idempotencyKey }, config) => skillV11Result(await axios.post(apiPath('/model-hub/skills/release-requests/'), {
      packet, expected_fingerprint: fingerprint, recipient_ids: [reviewerId],
    }, { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } })),
    invalidate: () => [['review-inbox']],
  });
}
