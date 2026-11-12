import axios from 'src/utils/axios';
import { apiPath } from 'src/api/contracts/api-surface';
import { contextParts } from './context-authoring';
import { idempotencyHeaders, skillV11Result, useSkillV11Mutation, useSkillV11Query } from './testing';
import { caseInput, inventoryReviewInput, mapRunPreview, mapSuite } from './v11-mappers';

// One API module for the context Tests tab. Three parts share it:
//   1. Regression cases: the reviewed, context-scoped example suite (captured from Observe).
//   2. Whole-context tests: the frozen standing root plus accepted linked skills, run privately.
//   3. Application verification: a pinned application target, matched native runs, per-cell
//      observations, ship/hold decision and the release packet that carries human review.

// ---- 1. Regression cases ----
const suiteEndpoint = (context_id) => apiPath('/model-hub/skills/contexts/{context_id}/tests/', { context_id });
export const contextSuiteParts = (id) => ['agent_context', id, 'suite'];
export function useContextTestSuite(contextId) {
  return useSkillV11Query({ parts: contextSuiteParts(contextId), url: () => suiteEndpoint(contextId), mapper: mapSuite, enabled: Boolean(contextId) });
}
export function useSaveContextTestSuite(contextId) {
  return useSkillV11Mutation({
    send: async ({ revision, cases }, config) => mapSuite(skillV11Result(await axios.put(suiteEndpoint(contextId), { revision, cases: cases.map(caseInput) }, config))),
    invalidate: () => [contextSuiteParts(contextId)],
    // Refresh the authoritative revision after a conflict or lost response. The
    // editor retains its original revision and edits; never retry/rebase silently.
    refreshOnError: () => [contextSuiteParts(contextId)],
  });
}

// ---- 2. Whole-context tests ----
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

// ---- 3. Application verification ----
const nativeRunsPath = (context_id) => apiPath('/model-hub/skills/contexts/{context_id}/native/runs/', { context_id });
const nativeParts = (id) => ['agent_context', id, 'native'];
const ACTIVE = ['queued', 'provisioning', 'running'];

const mapTarget = (value) => ({
  bindingId: value.binding_id, name: value.name, revision: value.revision, selector: value.selector,
  qualified: value.qualified, configurationDigest: value.configuration_digest, configuration: value.configuration,
});
const mapArm = (arm) => ({ variant: arm.variant, version: arm.version, versionId: arm.version_id, digest: arm.digest, catalogMembers: arm.catalog_members ?? null });
const mapRun = (value) => ({
  id: value.id, jobId: value.job_id,
  // Provisioning is still active work; the shared job poller only knows "running".
  status: ACTIVE.includes(value.status) ? 'running' : value.status, jobState: value.status,
  failure: value.failure, createdAt: value.created_at, visibility: value.visibility,
  candidateDigest: value.candidate_digest, executionFingerprint: value.execution_fingerprint,
  configurationDigest: value.configuration_digest, annotationRevision: value.annotation_revision,
  arms: value.arms.map(mapArm), planned: value.planned, received: value.received,
  result: value.result, decision: value.decision, runtime: value.runtime, cases: value.cases,
  cells: value.cells?.map((cell) => ({
    caseId: cell.case_id, variant: cell.variant, repeatIndex: cell.repeat_index, status: cell.status,
    answer: cell.answer, delivery: cell.delivery, selection: cell.selection, tools: cell.tools,
    failure: cell.failure, incomplete: cell.incomplete, attemptId: cell.attempt_id, observed: cell.observed,
  })),
});
const mapPlan = (value) => ({
  executionFingerprint: value.execution_fingerprint, runtime: value.runtime, target: mapTarget(value.target),
  arms: value.arms.map(mapArm), cases: value.cases.map((item) => ({
    id: item.id, title: item.title, historical: item.historical, expectedSkill: item.expected_skill, expectedAction: item.expected_action,
  })),
  cells: value.cells, ceilingUsd: value.ceiling_usd, reproducible: value.reproducible, reproduction: value.reproduction,
  missingInputs: value.missing_inputs.map((item) => ({ caseId: item.case_id, input: item.input, detail: item.detail })),
});
const planInput = (value) => ({
  binding_id: value.bindingId, binding_revision: value.bindingRevision, candidate_version: value.candidateVersion,
  baselines: value.baselines, case_ids: value.caseIds, repetitions: value.repetitions,
  historical_case_ids: value.historicalCaseIds,
});

export function useNativeTargets(contextId) {
  return useSkillV11Query({
    parts: [...nativeParts(contextId), 'targets'],
    url: () => apiPath('/model-hub/skills/contexts/{context_id}/native/targets/', { context_id: contextId }),
    mapper: (value) => ({ runtime: { available: value.runtime.available, runtime: value.runtime.runtime, evidenceClass: value.runtime.evidence_class }, targets: value.targets.map(mapTarget) }), enabled: Boolean(contextId),
  });
}
export function useRuntimeConfiguration(contextId, enabled) {
  return useSkillV11Query({
    parts: [...nativeParts(contextId), 'runtime-configuration'],
    url: () => apiPath('/model-hub/skills/contexts/{context_id}/native/runtime-configuration/', { context_id: contextId }),
    mapper: (value) => value.configuration, enabled: Boolean(contextId) && enabled,
  });
}
export function useCreateNativeTarget(contextId) {
  return useSkillV11Mutation({
    send: async ({ version }, config) => mapTarget(skillV11Result(await axios.post(
      apiPath('/model-hub/skills/contexts/{context_id}/native/targets/', { context_id: contextId }), { version }, config))),
    invalidate: () => [nativeParts(contextId)],
  });
}
export function useQualifyNativeTarget(contextId) {
  return useSkillV11Mutation({
    send: async ({ bindingId, revision, configuration }, config) => mapTarget(skillV11Result(await axios.put(
      apiPath('/model-hub/skills/contexts/{context_id}/native/targets/{binding_id}/', { context_id: contextId, binding_id: bindingId }),
      { revision, configuration }, config))),
    invalidate: () => [nativeParts(contextId)], refreshOnError: () => [nativeParts(contextId)],
  });
}
export function usePreviewNativeRun(contextId) {
  return useSkillV11Mutation({
    send: async (plan, config) => mapPlan(skillV11Result(await axios.post(
      apiPath('/model-hub/skills/contexts/{context_id}/native/preview/', { context_id: contextId }), planInput(plan), config))),
  });
}
export function useCreateNativeRun(contextId) {
  return useSkillV11Mutation({
    send: async ({ idempotencyKey, plan, executionFingerprint, acknowledgeSpend }, config) => skillV11Result(await axios.post(
      nativeRunsPath(contextId),
      { ...planInput(plan), execution_fingerprint: executionFingerprint, acknowledge_spend: acknowledgeSpend },
      { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } })).evidence_id,
    invalidate: () => [nativeParts(contextId)],
  });
}
export function useNativeRuns(contextId) {
  return useSkillV11Query({
    parts: [...nativeParts(contextId), 'runs'], url: () => nativeRunsPath(contextId),
    mapper: (value) => value.results.map(mapRun), enabled: Boolean(contextId),
  });
}
export function useNativeRun(contextId, evidenceId) {
  return useSkillV11Query({
    parts: [...nativeParts(contextId), 'run', evidenceId],
    url: () => apiPath('/model-hub/skills/contexts/{context_id}/native/runs/{evidence_id}/', { context_id: contextId, evidence_id: evidenceId }),
    mapper: mapRun, enabled: Boolean(contextId && evidenceId), poll: true,
    terminalParts: [[...nativeParts(contextId), 'runs']],
  });
}

const shareEndpoint = (contextId, evidenceId) => apiPath('/model-hub/skills/contexts/{context_id}/native/runs/{evidence_id}/share/', { context_id: contextId, evidence_id: evidenceId });
/** Shares the exact native evidence, then previews the version-bound release packet for the separate review job. */
export function usePrepareReleasePacket(contextId) {
  return useSkillV11Mutation({
    send: async ({ run, target, label, pipeline, userId }, config) => {
      await axios.post(shareEndpoint(contextId, run.id), { acknowledge_snapshot_sharing: true }, config);
      const reference = skillV11Result(await axios.get(apiPath('/model-hub/skills/evidence/{source}/{source_id}/', { source: 'simulation_native', source_id: run.id }), config));
      const candidate = run.arms.find((arm) => arm.variant === 'candidate');
      const packet = skillV11Result(await axios.post(apiPath('/model-hub/skills/release-requests/preview/'), {
        resource_id: contextId, kind: 'agent_context', candidate_version_id: candidate.versionId, label,
        target: { binding_id: target.bindingId, environment_id: null, revision: target.revision, configuration_digest: target.configurationDigest },
        evidence: [{ source: 'simulation_native', source_id: run.id, annotation_revision: reference.annotation_revision }],
        external_pipeline: { url: pipeline.url, configuration_identity: pipeline.configurationIdentity, decision: pipeline.decision, trust: 'operator_recorded_unimported', recorded_by: userId },
      }, config));
      return { fingerprint: packet.fingerprint, candidateVersion: candidate.version, evidence: packet.packet.evidence.length, targetDigest: packet.packet.target.configuration_digest, pipelineUrl: packet.packet.external_pipeline.url };
    },
    invalidate: () => [nativeParts(contextId)],
  });
}
