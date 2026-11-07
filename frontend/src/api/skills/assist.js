import axios from 'src/utils/axios';
import { endpoints } from './endpoints';
import { skillsKeys } from './keys';
import { assistInput, mapAssist, mapAssistAck, mapAssistPreview, mapSourceDraft } from './v11-mappers';
import { idempotencyHeaders, skillV11Result, useSkillV11Mutation, useSkillV11Query } from './testing';

export function usePreviewSkillAssist(skillId) {
  return useSkillV11Mutation({ send: async (input, config) => mapAssistPreview(skillV11Result(await axios.post(endpoints.assistPreview(skillId), assistInput(input), config))) });
}
export function useCreateSkillAssist(skillId) {
  return useSkillV11Mutation({
    send: async ({ idempotencyKey, expectedInputFingerprint, acknowledgeProviderProcessing, outboundReview, ...input }, config) => mapAssistAck(skillV11Result(await axios.post(endpoints.assist(skillId), {
      ...assistInput(input), expected_input_fingerprint: expectedInputFingerprint,
      acknowledge_provider_processing: acknowledgeProviderProcessing,
      outbound_review: { fingerprint: outboundReview.fingerprint, false_positive_secret_findings: outboundReview.falsePositiveSecretFindings.map(({ findingId, reason }) => ({ finding_id: findingId, reason })) },
    }, { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } }))),
  });
}
export function useSkillAssist(skillId, proposalId) {
  return useSkillV11Query({ parts: [skillId, 'assist', proposalId], url: () => endpoints.assistProposal(skillId, proposalId), mapper: mapAssist, enabled: Boolean(skillId && proposalId), poll: true, terminalParts: [[skillId, 'evidence'], [skillId, 'suite']] });
}
export function useApplySkillAssist(skillId, proposalId) {
  return useSkillV11Mutation({
    send: async ({ revision, expectedDigest }, config) => mapSourceDraft(skillV11Result(await axios.post(endpoints.applyAssist(skillId, proposalId), { revision, expected_digest: expectedDigest }, config))),
    invalidate: () => [[skillId, 'assist', proposalId], [skillId, 'evidence'], [skillId, 'runs'], [skillId, 'suite']],
    invalidateLegacy: () => [skillsKeys.draft(skillId), skillsKeys.detail(skillId), ['skills', skillId, 'compare'], ['skills', 'list']],
  });
}
export function useCancelSkillAssist(skillId, proposalId) {
  return useSkillV11Mutation({ send: async (_, config) => skillV11Result(await axios.post(endpoints.cancelAssist(skillId, proposalId), {}, config)), invalidate: () => [[skillId, 'assist', proposalId]] });
}
