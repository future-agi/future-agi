import axios from 'src/utils/axios';
import { apiPath } from 'src/api/contracts/api-surface';
import { idempotencyHeaders, skillV11Result, useSkillV11Mutation, useSkillV11Query } from './testing';

const base = (context_id) => apiPath('/model-hub/skills/contexts/{context_id}/native/runs/', { context_id });
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
      base(contextId),
      { ...planInput(plan), execution_fingerprint: executionFingerprint, acknowledge_spend: acknowledgeSpend },
      { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } })).evidence_id,
    invalidate: () => [nativeParts(contextId)],
  });
}
export function useNativeRuns(contextId) {
  return useSkillV11Query({
    parts: [...nativeParts(contextId), 'runs'], url: () => base(contextId),
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
