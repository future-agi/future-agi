import axios from 'src/utils/axios';
import { apiPath } from 'src/api/contracts/api-surface';
import { contextParts } from './context-authoring';
import { useSkillV11Mutation, useSkillV11Query, skillV11Result, idempotencyHeaders } from './testing';
import { inventoryReviewInput, mapRunPreview } from './v11-mappers';

const previewPath = (context_id) => apiPath('/model-hub/skills/contexts/{context_id}/test-runs/preview/', { context_id });
const runsPath = (context_id) => apiPath('/model-hub/skills/contexts/{context_id}/test-runs/', { context_id });
const runPath = (context_id, run_id) => apiPath('/model-hub/skills/contexts/{context_id}/test-runs/{run_id}/', { context_id, run_id });
const resultsPath = (context_id, run_id) => apiPath('/model-hub/skills/contexts/{context_id}/test-runs/{run_id}/results/', { context_id, run_id });
const attemptPath = (context_id, run_id, attempt_id) => apiPath('/model-hub/skills/contexts/{context_id}/test-runs/{run_id}/attempts/{attempt_id}/', { context_id, run_id, attempt_id });
const cancelPath = (context_id, run_id) => apiPath('/model-hub/skills/contexts/{context_id}/test-runs/{run_id}/cancel/', { context_id, run_id });

// The compiled digest includes the accepted lock and selector policies. It must
// never be reconstructed from owned editor files with the leaf digest helper.
export function contextTestCandidate(saved) {
  if (!saved || !Number.isSafeInteger(saved.revision) || saved.revision < 1 || !/^sha256:[a-f0-9]{64}$/.test(saved.digest)) throw new Error('Save the complete context before previewing tests.');
  return { kind: 'draft', revision: saved.revision, digest: saved.digest };
}
export function usePreviewContextTest(contextId) {
  return useSkillV11Mutation({ send: async (configuration, config) => {
    const value = skillV11Result(await axios.post(previewPath(contextId), configuration, config));
    return { ...mapRunPreview(value), reference: value.reference };
  } });
}
export function useCreateContextTest(contextId) {
  return useSkillV11Mutation({
    send: async ({ configuration, expectedFingerprint, inventoryReview, idempotencyKey }, config) => skillV11Result(await axios.post(runsPath(contextId), {
      configuration, expected_fingerprint: expectedFingerprint,
      acknowledge_provider_processing: true, inventory_review: inventoryReviewInput(inventoryReview),
    }, { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } })),
    invalidate: () => [contextParts(contextId, 'test-runs')],
  });
}
export function useContextTestRuns(contextId, page = 1) {
  return useSkillV11Query({ parts: contextParts(contextId, 'test-runs', page), url: () => runsPath(contextId), params: { page, page_size: 25 }, mapper: (value) => value, enabled: Boolean(contextId) });
}
export function useContextTestRun(contextId, runId) {
  return useSkillV11Query({ parts: contextParts(contextId, 'test-run', runId), url: () => runPath(contextId, runId), mapper: (value) => value,
    enabled: Boolean(contextId && runId), poll: true, terminalParts: [contextParts(contextId, 'test-runs'), contextParts(contextId, 'test-results', runId)] });
}
export function useContextTestResults(contextId, runId, page = 1) {
  return useSkillV11Query({ parts: contextParts(contextId, 'test-results', runId, page), url: () => resultsPath(contextId, runId), params: { page, page_size: 25 }, mapper: (value) => value, enabled: Boolean(contextId && runId) });
}
export function useContextTestAttempt(contextId, runId, attemptId) {
  return useSkillV11Query({ parts: contextParts(contextId, 'test-attempt', runId, attemptId), url: () => attemptPath(contextId, runId, attemptId), mapper: (value) => value, enabled: Boolean(contextId && runId && attemptId) });
}
export function useCancelContextTest(contextId, runId) {
  return useSkillV11Mutation({ send: async (_, config) => skillV11Result(await axios.post(cancelPath(contextId, runId), {}, config)), invalidate: () => [contextParts(contextId, 'test-run', runId), contextParts(contextId, 'test-runs')] });
}
