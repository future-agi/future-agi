import axios from 'src/utils/axios';
import { apiPath, getContractedApiMethods } from 'src/api/contracts/api-surface';
import { idempotencyHeaders, skillV11Result, useSkillV11Mutation, useSkillV11Query, v11PageParams } from './testing';
import { mapV11Page } from './v11-mappers';
import { contextSelectorInput, mapContextSelector } from './context-mappers';
import { mapContextTraceRef, traceRefInput } from './context-capture';

const repositoryInput = (value) => value === null ? null : ({
  provider: value.provider, repository_id: value.repositoryId, default_branch: value.defaultBranch, project_scope: value.projectScope,
});
const mapRepository = (value) => value === null ? null : ({
  provider: value.provider, repositoryId: value.repository_id, defaultBranch: value.default_branch, projectScope: value.project_scope,
});
export const contextBindingInput = (value) => ({
  name: value.name, kind: value.kind, selector: contextSelectorInput(value.selector), update_mode: value.updateMode,
  profile: value.profile, profile_version: value.profileVersion, repository: repositoryInput(value.repository),
  expected_report_interval_seconds: value.expectedReportIntervalSeconds,
});
export const mapContextBinding = (value) => ({
  id: value.id, contextId: value.context_id, revision: value.revision, retiredAt: value.retired_at,
  name: value.name, kind: value.kind, selector: mapContextSelector(value.selector), updateMode: value.update_mode,
  profile: value.profile, profileVersion: value.profile_version, repository: mapRepository(value.repository),
  expectedReportIntervalSeconds: value.expected_report_interval_seconds,
});
export const contextSessionInput = (value) => ({
  client_session_key: value.clientSessionKey, incarnation_id: value.incarnationId, resume_session_id: value.resumeSessionId,
  adapter_version: value.adapterVersion, runtime_version: value.runtimeVersion, runtime_config_digest: value.runtimeConfigDigest,
});
export const contextReceiptInput = (value) => ({
  event_id: value.eventId, session_id: value.sessionId, instance_id: value.instanceId, sequence: value.sequence,
  stage: value.stage, version_id: value.versionId, digest: value.digest, materialization_digest: value.materializationDigest,
  observed_at: value.observedAt,
  details: {
    path: value.details.path, sha256: value.details.sha256, ranges: value.details.ranges,
    runtime_version: value.details.runtimeVersion, adapter_version: value.details.adapterVersion,
    diagnostic_codes: value.details.diagnosticCodes,
    trace_ref: value.details.traceRef === null ? null : traceRefInput(value.details.traceRef),
  },
});
export const mapContextReceiptInput = (value) => ({
  eventId: value.event_id, sessionId: value.session_id, instanceId: value.instance_id, sequence: value.sequence,
  stage: value.stage, versionId: value.version_id, digest: value.digest, materializationDigest: value.materialization_digest,
  observedAt: value.observed_at,
  details: {
    path: value.details.path, sha256: value.details.sha256, ranges: value.details.ranges,
    runtimeVersion: value.details.runtime_version, adapterVersion: value.details.adapter_version,
    diagnosticCodes: value.details.diagnostic_codes,
    traceRef: value.details.trace_ref === null ? null : mapContextTraceRef(value.details.trace_ref),
  },
});
export const contextQuarantineInput = (value) => ({
  revision: value.revision, quarantined: value.quarantined, reason: value.reason, replacement_version_id: value.replacementVersionId,
});
const bindingList = (context_id) => apiPath('/model-hub/skills/contexts/{context_id}/bindings/', { context_id });
const bindingDetail = (binding_id) => apiPath('/model-hub/skills/contexts/bindings/{binding_id}/', { binding_id });
export function useContextBindings(contextId, params = {}) {
  return useSkillV11Query({ parts: ['agent_context', contextId, 'bindings', params], url: () => bindingList(contextId), params: v11PageParams(params), mapper: (value) => mapV11Page(value, mapContextBinding), enabled: Boolean(contextId) && getContractedApiMethods('/model-hub/skills/contexts/{context_id}/bindings/').includes('get') });
}
export function useCreateContextBinding(contextId) {
  return useSkillV11Mutation({
    send: async ({ idempotencyKey, ...input }, config) => mapContextBinding(skillV11Result(await axios.post(bindingList(contextId), contextBindingInput(input), { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } }))),
    invalidate: () => [['agent_context', contextId]],
  });
}
export function useUpdateContextBinding(contextId) {
  return useSkillV11Mutation({
    send: async ({ bindingId, revision, binding }, config) => mapContextBinding(skillV11Result(await axios.patch(bindingDetail(bindingId), { revision, binding: contextBindingInput(binding) }, config))),
    invalidate: () => [['agent_context', contextId]], refreshOnError: () => [['agent_context', contextId]],
  });
}
export function useRetireContextBinding(contextId) {
  return useSkillV11Mutation({
    send: async ({ bindingId, revision }, config) => { await axios.delete(bindingDetail(bindingId), { ...config, data: { revision } }); },
    invalidate: () => [['agent_context', contextId]],
  });
}
export const mapVersionSafety = (value) => ({ revision: value.revision, quarantined: value.quarantined, reason: value.reason, replacementVersionId: value.replacement_version_id, affectedContextCount: value.affected_context_count });
const safetyEndpoint = (version_id) => apiPath('/model-hub/skills/version-safety/{version_id}/', { version_id });
export function useVersionSafety(versionId) {
  return useSkillV11Query({ parts: ['versionSafety', versionId], url: () => safetyEndpoint(versionId), mapper: mapVersionSafety, enabled: Boolean(versionId) && getContractedApiMethods('/model-hub/skills/version-safety/{version_id}/').includes('get') });
}
export function useSetVersionSafety() {
  return useSkillV11Mutation({
    send: async ({ versionId, ...input }, config) => mapVersionSafety(skillV11Result(await axios.put(safetyEndpoint(versionId), contextQuarantineInput(input), config))),
    invalidate: () => [['versionSafety'], ['agent_context']],
    invalidateLegacy: () => [['skills']],
  });
}
