import { mapModelConfig, modelConfigInput } from './v11-mappers';

export const mapTextConfiguration = (value) => ({
  model: mapModelConfig(value.model), judge: value.judge === null ? null : mapModelConfig(value.judge),
  harnessVersion: value.harness_version, adapterVersion: value.adapter_version, evaluatorVersion: value.evaluator_version,
});
export const textConfigurationInput = (value) => ({
  model: modelConfigInput(value.model), judge: value.judge === null ? null : modelConfigInput(value.judge),
  harness_version: value.harnessVersion, adapter_version: value.adapterVersion, evaluator_version: value.evaluatorVersion,
});
export const mapNativeConfiguration = (value) => ({
  codeCommit: value.code_commit, codeDigest: value.code_digest, entrypoint: value.entrypoint,
  imageDigest: value.image_digest, runtimeVersion: value.runtime_version,
  packages: value.packages.map(({ name, version, sha256 }) => ({ name, version, sha256 })),
  packageLockDigest: value.package_lock_digest, model: mapModelConfig(value.model),
  connectionId: value.connection_id, connectionRevision: value.connection_revision,
  toolsDigest: value.tools_digest, retrievalFixtureDigest: value.retrieval_fixture_digest,
  stateSchema: value.state_schema, checkpointDigest: value.checkpoint_digest, sideEffectPolicy: value.side_effect_policy,
  resetEntrypoint: value.reset_entrypoint, contextMount: value.context_mount, observableBoundaries: value.observable_boundaries,
});
export const nativeConfigurationInput = (value) => ({
  code_commit: value.codeCommit, code_digest: value.codeDigest, entrypoint: value.entrypoint,
  image_digest: value.imageDigest, runtime_version: value.runtimeVersion,
  packages: value.packages.map(({ name, version, sha256 }) => ({ name, version, sha256 })),
  package_lock_digest: value.packageLockDigest, model: modelConfigInput(value.model),
  connection_id: value.connectionId, connection_revision: value.connectionRevision,
  tools_digest: value.toolsDigest, retrieval_fixture_digest: value.retrievalFixtureDigest,
  state_schema: value.stateSchema, checkpoint_digest: value.checkpointDigest, side_effect_policy: value.sideEffectPolicy,
  reset_entrypoint: value.resetEntrypoint, context_mount: value.contextMount, observable_boundaries: value.observableBoundaries,
});
export function mapEvidenceConfiguration(value) {
  if (Object.hasOwn(value, 'native') === Object.hasOwn(value, 'text')) {
    throw new Error('Evidence must name exactly one configuration kind.');
  }
  return {
    schema: value.schema, digest: value.digest,
    ...(Object.hasOwn(value, 'native') ? { native: mapNativeConfiguration(value.native) } : { text: mapTextConfiguration(value.text) }),
  };
}
export function mapEvidenceRef(value) {
  const configuration = mapEvidenceConfiguration(value.configuration);
  if ((value.source !== 'skills_text' || !configuration.text) && (value.source !== 'simulation_native' || !configuration.native)) {
    throw new Error('Evidence source and immutable configuration must agree.');
  }
  return {
    schema: value.schema, source: value.source, sourceId: value.source_id,
    subjectKind: value.subject_kind, subjectId: value.subject_id, candidateDigest: value.candidate_digest,
    executionFingerprint: value.execution_fingerprint, status: value.status, visibility: value.visibility,
    payloadExpiresAt: value.payload_expires_at, configuration, annotationRevision: value.annotation_revision,
  };
}
export const evidenceSelectionInput = (value) => ({ source: value.source, source_id: value.sourceId, annotation_revision: value.annotationRevision });
export function evidenceInput(value) {
  const legacy = Object.hasOwn(value, 'evidenceRunId');
  const typed = Object.hasOwn(value, 'evidenceRefs');
  if (legacy && typed) throw new Error('Choose typed evidence references or the legacy run ID, not both.');
  if (legacy) return { evidence_run_id: value.evidenceRunId };
  if (typed) return { evidence_refs: value.evidenceRefs.map(evidenceSelectionInput) };
  return {};
}
export const mapTargetConfiguration = (value) => ({
  bindingId: value.binding_id, environmentId: value.environment_id,
  revision: value.revision, configurationDigest: value.configuration_digest,
});
export function targetConfigurationInput(value) {
  if (value.bindingId === undefined || value.environmentId === undefined ||
      (value.bindingId === null) === (value.environmentId === null)) {
    throw new Error('Name one target binding or environment and set the other to null.');
  }
  return {
    binding_id: value.bindingId, environment_id: value.environmentId,
    revision: value.revision, configuration_digest: value.configurationDigest,
  };
}
