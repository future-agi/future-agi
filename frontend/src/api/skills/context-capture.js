import axios from 'src/utils/axios';
import { apiPath } from 'src/api/contracts/api-surface';
import { idempotencyHeaders, skillV11Result, useSkillV11Mutation, useSkillV11Query } from './testing';
import { caseInput, mapOutboundFinding, mapSuite } from './v11-mappers';

export const traceRefInput = (value) => ({
  project_id: value.projectId, trace_id: value.traceId, span_id: value.spanId, start_time: value.startTime,
});
export const mapContextTraceRef = (value) => ({
  projectId: value.project_id, traceId: value.trace_id, spanId: value.span_id, startTime: value.start_time,
});
const sourceEndpoint = (context_id) => apiPath('/model-hub/skills/contexts/{context_id}/captures/source/', { context_id });
export function useContextCaptureSource(contextId, traceRef) {
  return useSkillV11Query({
    parts: ['agent_context', contextId, 'capture-source', traceRef], enabled: Boolean(contextId && traceRef),
    load: async (config) => {
      const value = skillV11Result(await axios.post(sourceEndpoint(contextId), { trace_ref: traceRefInput(traceRef) }, config));
      return { traceRef: mapContextTraceRef(value.trace_ref), sourceFingerprint: value.source_fingerprint, segments: value.segments, authorized: true, readOnly: false };
    },
  });
}
export function usePreviewContextCapture(contextId) {
  return useSkillV11Mutation({ send: async ({ traceRef, selectedSegmentIds }, config) => {
    const value = skillV11Result(await axios.post(apiPath('/model-hub/skills/contexts/{context_id}/captures/preview/', { context_id: contextId }), {
      trace_ref: traceRefInput(traceRef), selected_segment_ids: selectedSegmentIds,
    }, config));
    return { captureId: value.capture_id, expiresAt: value.expires_at, sourceFingerprint: value.source_fingerprint,
      recordedContext: value.recorded_context === null ? null : { versionId: value.recorded_context.version_id, digest: value.recorded_context.digest },
      segments: value.segments, omitted: value.omitted, outboundFindings: value.outbound_findings.map(mapOutboundFinding), replayScope: value.replay_scope };
  } });
}
export function useAcceptContextCapture(contextId) {
  return useSkillV11Mutation({
    send: async ({ captureId, sourceFingerprint, suiteRevision, case: example, acknowledgeWorkspaceSharing, idempotencyKey }, config) => mapSuite(skillV11Result(await axios.post(
      apiPath('/model-hub/skills/contexts/{context_id}/captures/{capture_id}/accept/', { context_id: contextId, capture_id: captureId }),
      { source_fingerprint: sourceFingerprint, suite_revision: suiteRevision, case: caseInput(example), acknowledge_workspace_sharing: acknowledgeWorkspaceSharing },
      { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } },
    ))),
    invalidate: () => [[contextId, 'suite']],
    refreshOnError: () => [[contextId, 'suite']],
  });
}
