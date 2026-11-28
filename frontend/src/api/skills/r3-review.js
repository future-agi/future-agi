import { mapEvidenceRef, mapTargetConfiguration } from './r3-mappers';

export const mapPointerTransition = (value) => ({
  label: value.label, expectedVersion: value.expected_version, version: value.version,
  expectedReleaseRevision: value.expected_release_revision, expectedSecurityRevision: value.expected_security_revision,
  expectedImpactFingerprint: value.expected_impact_fingerprint,
});
export const pointerTransitionInput = (value) => ({
  label: value.label, expected_version: value.expectedVersion, version: value.version,
  expected_release_revision: value.expectedReleaseRevision, expected_security_revision: value.expectedSecurityRevision,
  expected_impact_fingerprint: value.expectedImpactFingerprint,
});
export const mapExternalPipelineReference = (value) => ({
  trust: value.trust, url: value.url, configurationIdentity: value.configuration_identity,
  decision: value.decision, recordedBy: value.recorded_by,
});
// This is an authoritative response, not a write body. In particular callers
// cannot submit asserted grades, visibility, expiry or reviewer attribution.
export const mapReleasePacket = (value) => ({
  schema: value.schema, subjectKind: value.subject_kind, subjectId: value.subject_id,
  candidateVersionId: value.candidate_version_id, candidateDigest: value.candidate_digest,
  transition: mapPointerTransition(value.transition), target: value.target === null ? null : mapTargetConfiguration(value.target),
  evidence: value.evidence.map(mapEvidenceRef),
  externalPipeline: value.external_pipeline === null ? null : mapExternalPipelineReference(value.external_pipeline),
});
