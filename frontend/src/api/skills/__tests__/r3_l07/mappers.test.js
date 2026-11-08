import { describe, expect, it } from 'vitest';
import * as context from '../../context-mappers';
import * as r3 from '../../r3-mappers';
import * as cases from '../../r3-cases';
import * as consumers from '../../context-consumers';
import { mapContextTraceRef, traceRefInput } from '../../context-capture';
import { checkpointRestoreInput, retainedPlanAcceptanceInput } from '../../r3-intake';
import { mapReleasePacket, pointerTransitionInput } from '../../r3-review';
import { spendPolicyInput } from '../../r3-spend';
import wire from './wire-fixtures.json';

// wire-fixtures.json is emitted by the actual backend DRF serializers using
// r3_l07/export_wire_fixtures.py; its currency is checked by backend pytest.
describe('frozen r3.2 wire mappings', () => {
  it('reads resource_id/kind, bounded continuation and separate provenance', () => {
    const manifest = context.mapConsumptionManifest(wire.ConsumptionManifest);
    expect(manifest).toEqual({
      resourceId: wire.ConsumptionManifest.resource_id, kind: 'agent_context', name: 'synthetic-agent',
      description: 'Synthetic purpose', version: 1, versionId: wire.ConsumptionManifest.version_id,
      digest: wire.ConsumptionManifest.digest, requested: { label: 'production' }, entrypoint: 'AGENTS.md',
      descriptorPath: '.futureagi/context.json', files: [{ path: 'AGENTS.md', sha256: 'a'.repeat(64), sizeBytes: 5,
        origin: { kind: 'owned', skillId: null, versionId: null, sourcePath: null } }],
      fileCount: 26, fileOffset: 0, nextFileOffset: 25, createdAt: wire.ConsumptionManifest.created_at,
      resolutionId: wire.ConsumptionManifest.resolution_id, resolutionExpiresAt: wire.ConsumptionManifest.resolution_expires_at,
    });
    const leaf = context.mapConsumptionManifest({ ...wire.ConsumptionManifest, kind: 'skill', entrypoint: 'SKILL.md', descriptor_path: null });
    expect(leaf.kind).toBe('skill');
    expect(leaf.descriptorPath).toBeNull();
  });
  it('keeps partial UTF-8 text and advancing character offsets without imposing a minimum chunk size', () => {
    expect(context.mapConsumptionChunk(wire.ConsumptionChunk)).toEqual({
      resourceId: wire.ConsumptionChunk.resource_id, versionId: wire.ConsumptionChunk.version_id, digest: wire.ConsumptionChunk.digest,
      path: 'AGENTS.md', sha256: 'a'.repeat(64), sizeBytes: 15, totalChars: 9, offset: 0, nextOffset: 2, eof: false, text: '"é',
    });
    expect(context.mapContextAccess(wire.AccessCheck)).toEqual({
      allowed: true, versionId: wire.AccessCheck.version_id, digest: wire.AccessCheck.digest, checkedAt: wire.AccessCheck.checked_at,
    });
  });
  it('keeps durable accepted locks separate from refresh selectors', () => {
    const spec = context.mapContextSpec(wire.ContextSpec);
    const lock = context.mapContextLock(wire.ResolvedLock);
    expect(spec.links.map(context.contextLinkInput)).toEqual(wire.ContextSpec.links);
    expect(lock.links[0]).toEqual({ mount: 'skills/synthetic/', skillId: wire.ResolvedLock.links[0].skill_id,
      requested: { label: 'production' }, version: 8, versionId: wire.ResolvedLock.links[0].version_id, digest: wire.ResolvedLock.links[0].digest });
    const input = { revision: 1, links: spec.links, refreshSkillIds: wire.DependencyPreview.refresh_skill_ids, expectedFingerprint: wire.DependencyApply.expected_fingerprint };
    expect(context.contextDependencyPreviewInput(input)).toEqual(wire.DependencyPreview);
    expect(context.contextDependencyApplyInput(input)).toEqual(wire.DependencyApply);
  });
  it('preserves hotfix origin, reviewed head, source bytes and exact CAS inputs', () => {
    expect(context.contextDraftStartInput({ revision: 0, fromVersion: 4 })).toEqual(wire.DraftStart);
    expect(context.contextDraftSaveInput({ revision: 1, baseVersion: 4, files: wire.DraftSave.files, description: 'Retained purpose', note: ' note ' })).toEqual(wire.DraftSave);
    const head = { revision: 1, expectedDigest: wire.HeadAcknowledgment.expected_digest, expectedHeadVersion: 6, reviewFingerprint: wire.HeadAcknowledgment.review_fingerprint };
    expect(context.contextHeadAcknowledgmentInput(head)).toEqual(wire.HeadAcknowledgment);
    expect(context.contextPublishInput({ ...head, baseVersion: 4, note: 'repair', acknowledgeWarnings: true })).toEqual(wire.ContextPublish);
    expect(() => context.contextPublishInput({ ...head, force: false })).toThrow('does not accept');
    expect(() => context.contextPublishInput({ ...head, labels: [] })).toThrow('does not accept');
    expect(() => context.contextPublishInput({ ...head, evidenceRefs: [] })).toThrow('cannot be dropped');
    expect(() => context.contextPublishInput({ ...head, evidenceRunId: null })).toThrow('cannot be dropped');
  });
  it('does not silently convert an ambiguous selector into a pin or production', () => {
    expect(context.contextSelectorInput({ version: 3 })).toEqual({ version: 3 });
    expect(() => context.contextSelectorInput({ label: 'production', version: 3 })).toThrow('exactly one');
    expect(() => context.contextSelectorInput({})).toThrow('exactly one');
  });
  it('round trips typed grants and never fabricates a replay secret or last-use observation', () => {
    const metadata = context.mapContextCredential(wire.CredentialMetadata);
    expect(context.contextCredentialInput(metadata)).toEqual(wire.CredentialInput);
    expect(metadata.lastUsedAt).toBeNull();
    expect(metadata.grants.map(context.consumeGrantInput)).toEqual(wire.CredentialInput.grants);
    expect(context.mapIssuedContextCredential(wire.CredentialIssued)).toEqual({ id: wire.CredentialIssued.id, secret: null, secretAvailable: false, metadata });
    expect(metadata).not.toHaveProperty('secret');
  });
  it('maps all immutable native and text execution identities without relabeling native verdicts', () => {
    expect(r3.nativeConfigurationInput(r3.mapNativeConfiguration(wire.NativeConfiguration))).toEqual(wire.NativeConfiguration);
    expect(r3.textConfigurationInput(r3.mapTextConfiguration(wire.TextConfiguration))).toEqual(wire.TextConfiguration);
    const evidence = r3.mapEvidenceRef(wire.EvidenceRef);
    expect(evidence.source).toBe('simulation_native');
    expect(evidence.status).toBe('failed');
    expect(evidence.annotationRevision).toBe(2);
    expect(evidence.configuration.native.sideEffectPolicy).toBe('fixture_only');
    expect(evidence.payloadExpiresAt).toBe(wire.EvidenceRef.payload_expires_at);
    expect(() => r3.mapEvidenceRef({ ...wire.EvidenceRef, source: 'skills_text' })).toThrow('must agree');
    expect(() => r3.mapEvidenceConfiguration({ schema: 1, digest: 'digest', text: wire.TextConfiguration, native: wire.NativeConfiguration })).toThrow('exactly one');
  });
  it('maps release evidence and nullable targets while retaining unimported external trust', () => {
    const packet = mapReleasePacket(wire.ReleasePacket);
    expect(pointerTransitionInput(packet.transition)).toEqual(wire.PointerTransition);
    expect(r3.targetConfigurationInput(packet.target)).toEqual(wire.TargetConfiguration);
    expect(packet.evidence[0]).toEqual(r3.mapEvidenceRef(wire.EvidenceRef));
    expect(packet.externalPipeline).toEqual({
      url: 'https://example.test/ci/123', configurationIdentity: 'ci-v1', decision: 'Operator recorded hold',
      trust: 'operator_recorded_unimported', recordedBy: wire.ReleasePacket.external_pipeline.recorded_by,
    });
    expect(mapReleasePacket({ ...wire.ReleasePacket, target: null, external_pipeline: null })).toMatchObject({ target: null, externalPipeline: null });
    expect(() => r3.targetConfigurationInput({ bindingId: 'id', environmentId: 'env' })).toThrow('one target');
    expect(() => r3.targetConfigurationInput({ bindingId: null, environmentId: null })).toThrow('one target');
  });
  it('keeps supplied empty evidence distinct from omitted evidence and rejects legacy ambiguity', () => {
    expect(r3.evidenceInput({})).toEqual({});
    expect(r3.evidenceInput({ evidenceRefs: [] })).toEqual({ evidence_refs: [] });
    expect(r3.evidenceInput({ evidenceRunId: null })).toEqual({ evidence_run_id: null });
    expect(() => r3.evidenceInput({ evidenceRunId: null, evidenceRefs: [] })).toThrow('not both');
    expect(r3.evidenceInput({ evidenceRefs: [{ source: 'simulation_native', sourceId: 'source', annotationRevision: 5 }] })).toEqual({ evidence_refs: [{ source: 'simulation_native', source_id: 'source', annotation_revision: 5 }] });
  });
  it('preserves explicit child expectations and human text without overwriting machine grades', () => {
    expect(cases.nativeSelectionInput(cases.mapNativeSelection(wire.NativeSelection))).toEqual(wire.NativeSelection);
    expect(cases.humanAnnotationInput(cases.mapHumanAnnotation(wire.HumanAnnotation))).toEqual(wire.HumanAnnotation);
    expect(cases.mapHumanAnnotation(wire.HumanAnnotation).citedText).toBe(' {"user_key": "retain_me"}\n');
  });
  it('distinguishes omitted tags from explicit clearing and retains plan/checkpoint CAS', () => {
    const input = { planId: wire.RetainedPlanAcceptance.plan_id, expectedFingerprint: wire.RetainedPlanAcceptance.expected_fingerprint,
      itemKey: 'source/policy', mode: 'patch', expectedHeadVersion: 4, expectedHeadDigest: wire.RetainedPlanAcceptance.expected_head_digest, rightsConfirmed: true };
    expect(retainedPlanAcceptanceInput(input)).toEqual(wire.RetainedPlanAcceptance);
    expect(retainedPlanAcceptanceInput({ ...input, tags: [] })).toEqual({ ...wire.RetainedPlanAcceptance, tags: [] });
    expect(checkpointRestoreInput({ checkpointId: wire.CheckpointRestore.checkpoint_id, revision: 0, expectedDigest: wire.CheckpointRestore.expected_digest })).toEqual(wire.CheckpointRestore);
  });
  it('keeps zero-denominated disabled spending explicit and rejects binary floats', () => {
    expect(spendPolicyInput({ revision: 0, enabled: false, periodLimitUsd: '0.00000000' })).toEqual(wire.SpendPolicyInput);
    expect(() => spendPolicyInput({ revision: 0, enabled: false, periodLimitUsd: 0 })).toThrow('decimal string');
  });
  it('preserves full physical trace, repository scope, nullable session and ordered receipt identity', () => {
    expect(traceRefInput(mapContextTraceRef(wire.TraceRef))).toEqual(wire.TraceRef);
    const binding = consumers.mapContextBinding(wire.BindingOut);
    const { id, context_id: contextId, revision, retired_at: retiredAt, ...input } = wire.BindingOut;
    expect(consumers.contextBindingInput(binding)).toEqual(input);
    expect(binding).toMatchObject({ id, contextId, revision, retiredAt });
    expect(consumers.contextReceiptInput(consumers.mapContextReceiptInput(wire.ReceiptInput))).toEqual(wire.ReceiptInput);
    expect(consumers.contextSessionInput({ clientSessionKey: 'synthetic-thread', incarnationId: wire.SessionInput.incarnation_id,
      resumeSessionId: null, adapterVersion: '1', runtimeVersion: '1', runtimeConfigDigest: wire.SessionInput.runtime_config_digest })).toEqual(wire.SessionInput);
    expect(consumers.contextQuarantineInput({ revision: 0, quarantined: true, reason: 'Synthetic safety exercise', replacementVersionId: null })).toEqual(wire.QuarantineInput);
  });
});
