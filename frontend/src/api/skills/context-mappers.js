// The r3.2 serializers own these shapes. Never recursively rename authored data.
export const mapContextSelector = (value) => {
  if (Object.hasOwn(value, 'version') === Object.hasOwn(value, 'label')) {
    throw new Error('Choose exactly one context label or version.');
  }
  if (Object.hasOwn(value, 'version')) {
    if (!Number.isSafeInteger(value.version) || value.version < 1) throw new Error('Choose a positive integer version.');
    return { version: value.version };
  }
  if (typeof value.label !== 'string' || !/^[a-z0-9][a-z0-9-]{0,39}$/.test(value.label)) {
    throw new Error('Choose an explicit valid context label.');
  }
  return { label: value.label };
};
export const contextSelectorInput = mapContextSelector;
export const mapContextLink = (value) => ({ skillId: value.skill_id, selector: mapContextSelector(value.selector) });
export const contextLinkInput = (value) => ({ skill_id: value.skillId, selector: contextSelectorInput(value.selector) });
export const mapLockedContextLink = (value) => ({
  mount: value.mount, skillId: value.skill_id, requested: mapContextSelector(value.requested),
  version: value.version, versionId: value.version_id, digest: value.digest,
});
export const mapContextSpec = (value) => ({ schema: value.schema, description: value.description, links: value.links.map(mapContextLink) });
export const mapContextLock = (value) => ({ schema: value.schema, links: value.links.map(mapLockedContextLink) });
export const mapContextFile = (value) => ({
  path: value.path, sha256: value.sha256, sizeBytes: value.size_bytes,
  origin: { kind: value.origin.kind, skillId: value.origin.skill_id, versionId: value.origin.version_id, sourcePath: value.origin.source_path },
});
export const mapConsumptionManifest = (value) => ({
  resourceId: value.resource_id, kind: value.kind, name: value.name, description: value.description,
  version: value.version, versionId: value.version_id, digest: value.digest,
  requested: mapContextSelector(value.requested), entrypoint: value.entrypoint, descriptorPath: value.descriptor_path,
  files: value.files.map(mapContextFile), fileCount: value.file_count, fileOffset: value.file_offset,
  nextFileOffset: value.next_file_offset, createdAt: value.created_at,
  resolutionId: value.resolution_id, resolutionExpiresAt: value.resolution_expires_at,
});
export const mapConsumptionChunk = (value) => ({
  resourceId: value.resource_id, versionId: value.version_id, digest: value.digest,
  path: value.path, sha256: value.sha256, sizeBytes: value.size_bytes, totalChars: value.total_chars,
  offset: value.offset, nextOffset: value.next_offset, eof: value.eof, text: value.text,
});
export const mapContextAccess = (value) => ({ allowed: value.allowed, versionId: value.version_id, digest: value.digest, checkedAt: value.checked_at });
export const contextDraftStartInput = ({ revision, fromVersion }) => ({ revision, from_version: fromVersion });
export const contextDraftSaveInput = (value) => ({
  revision: value.revision, base_version: value.baseVersion,
  files: value.files.map(({ path, content }) => ({ path, content })), description: value.description, note: value.note,
});
export const contextHeadAcknowledgmentInput = (value) => ({
  revision: value.revision, expected_digest: value.expectedDigest,
  expected_head_version: value.expectedHeadVersion, review_fingerprint: value.reviewFingerprint,
});
export const contextPublishInput = (value) => {
  if (Object.hasOwn(value, 'force') || Object.hasOwn(value, 'labels')) {
    throw new Error('Context publication does not accept force or label moves.');
  }
  if (Object.hasOwn(value, 'evidenceRefs') || Object.hasOwn(value, 'evidenceRunId')) {
    throw new Error('Publication evidence requires the integrated typed-evidence contract; it cannot be dropped.');
  }
  return {
    ...contextHeadAcknowledgmentInput(value), base_version: value.baseVersion,
    note: value.note, acknowledge_warnings: value.acknowledgeWarnings,
  };
};
export const contextDependencyPreviewInput = (value) => ({
  revision: value.revision, links: value.links.map(contextLinkInput), refresh_skill_ids: value.refreshSkillIds,
});
export const contextDependencyApplyInput = (value) => ({
  ...contextDependencyPreviewInput(value), expected_fingerprint: value.expectedFingerprint,
});
export const mapConsumeGrant = (value) => ({ kind: value.kind, resourceId: value.resource_id, labels: value.labels, versionIds: value.version_ids });
export const consumeGrantInput = (value) => ({ kind: value.kind, resource_id: value.resourceId, labels: value.labels, version_ids: value.versionIds });
export const contextCredentialInput = (value) => ({
  purpose: value.purpose, grants: value.grants.map(consumeGrantInput), binding_ids: value.bindingIds, expires_at: value.expiresAt,
});
export const mapContextCredential = (value) => ({
  id: value.id, purpose: value.purpose, grants: value.grants.map(mapConsumeGrant), bindingIds: value.binding_ids,
  expiresAt: value.expires_at, revokedAt: value.revoked_at, createdAt: value.created_at, lastUsedAt: value.last_used_at,
});
export const mapIssuedContextCredential = (value) => ({
  id: value.id, secret: value.secret, secretAvailable: value.secret_available, metadata: mapContextCredential(value.metadata),
});
