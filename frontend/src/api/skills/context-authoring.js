import axios from 'src/utils/axios';
import { apiPath, getContractedApiMethods } from 'src/api/contracts/api-surface';
import { mapChecks, mapVersion, mapLabel } from './mappers';
import { mapV11Page } from './v11-mappers';
import { contextDraftSaveInput, contextDraftStartInput, contextDependencyPreviewInput, contextDependencyApplyInput, mapContextSpec, mapContextLock, mapLockedContextLink } from './context-mappers';
import { skillV11Result, useSkillV11Query, useSkillV11Mutation, idempotencyHeaders, v11PageParams } from './testing';

// These are the frozen human-authoring routes. Never fall back to a leaf route
// or to the credential-only consumption facade when a contract is unavailable.
export const contextEndpoints = {
  list: () => apiPath('/model-hub/skills/contexts/'),
  detail: (context_id) => apiPath('/model-hub/skills/contexts/{context_id}/', { context_id }),
  draft: (context_id) => apiPath('/model-hub/skills/contexts/{context_id}/draft/', { context_id }),
  start: (context_id) => apiPath('/model-hub/skills/contexts/{context_id}/draft/start/', { context_id }),
  versions: (context_id) => apiPath('/model-hub/skills/contexts/{context_id}/versions/', { context_id }),
  version: (context_id, number) => apiPath('/model-hub/skills/contexts/{context_id}/versions/{number}/', { context_id, number }),
  previewDependencies: (context_id) => apiPath('/model-hub/skills/contexts/{context_id}/dependencies/preview/', { context_id }),
  applyDependencies: (context_id) => apiPath('/model-hub/skills/contexts/{context_id}/dependencies/apply/', { context_id }),
};
export function contextAuthoringReady() {
  return [
    ['/model-hub/skills/contexts/', ['get', 'post']],
    ['/model-hub/skills/contexts/{context_id}/', ['get']],
    ['/model-hub/skills/contexts/{context_id}/draft/', ['get', 'put', 'delete']],
    ['/model-hub/skills/contexts/{context_id}/draft/start/', ['post']],
  ].every(([path, methods]) => methods.every((method) => getContractedApiMethods(path).includes(method)));
}
export const contextParts = (id, ...parts) => ['agent_context', ...(id ? [id] : []), ...parts];
export function mapContextSummary(value) {
  if (value.kind !== 'agent_context') throw new Error('Expected an agent context, not a leaf skill.');
  return {
    id: value.id, kind: value.kind, name: value.name, description: value.description, tags: value.tags,
    metadataRevision: value.metadata_revision, latestVersion: value.latest_version,
    labels: value.labels.map(mapLabel), releaseRevision: value.release_revision,
    securityRevision: value.security_revision, protectedLabelNames: value.protected_label_names,
    hasDraft: value.has_draft, updatedAt: value.updated_at, updatedBy: value.updated_by,
  };
}
export const mapContextReleaseState = (value) => ({
  latestVersion: value.latest_version, releaseRevision: value.release_revision, securityRevision: value.security_revision,
  protectedLabelNames: value.protected_label_names, labels: value.labels.map(mapLabel),
  permissions: {
    canPublish: value.permissions.can_publish, canPromote: value.permissions.can_promote,
    canManageProtection: value.permissions.can_manage_protection, canQuarantine: value.permissions.can_quarantine,
    canIssueCredentials: value.permissions.can_issue_credentials,
  },
});
export const mapContextDraft = (value) => ({
  id: value.id, revision: value.revision, baseVersion: value.base_version, files: value.files.map(({ path, content }) => ({ path, content })),
  note: value.note, contextSpec: mapContextSpec(value.context_spec), resolvedLock: mapContextLock(value.resolved_lock),
  description: value.context_spec.description, digest: value.digest, recoveryDigest: value.recovery_digest,
  checks: mapChecks(value.checks), updatedAt: value.updated_at, expectedHeadVersion: value.expected_head_version,
  headAcknowledged: value.head_acknowledged, reviewFingerprint: value.review_fingerprint,
});
export const mapContextDetail = (value) => ({
  context: mapContextSummary(value.context), releaseState: mapContextReleaseState(value.release_state),
  latest: value.latest === null ? null : mapVersion(value.latest),
  ownDraft: value.own_draft === null ? null : { revision: value.own_draft.revision, baseVersion: value.own_draft.base_version },
});
export function contextCreateInput(value) {
  return { name: value.name, description: value.description, tags: value.tags, files: value.files === null ? null : value.files.map(({ path, content }) => ({ path, content })) };
}
export function mapDraftOperation(value) {
  // A replay receipt is not permission to claim that today's draft was the
  // original result. Controllers must offer recovery rather than overwriting it.
  return {
    contextId: value.context_id ?? value.context?.id,
    draftId: value.draft_id ?? value.draft?.id,
    revision: value.revision ?? value.draft?.revision,
    digest: value.digest ?? value.draft?.digest,
    context: value.context ? mapContextSummary(value.context) : null,
    draft: value.draft ? mapContextDraft(value.draft) : null,
  };
}
export function requireAcknowledgedDraft(receipt, draft) {
  if (!draft || !Number.isSafeInteger(receipt.revision) || draft.revision !== receipt.revision ||
      (receipt.digest != null && receipt.digest !== draft.digest) ||
      (receipt.draftId != null && draft.id != null && receipt.draftId !== draft.id)) {
    const error = new Error('The operation is recorded, but the live draft has changed. Reload it explicitly; the original operation was not reapplied.');
    error.receipt = receipt;
    throw error;
  }
  return draft;
}
export function useContexts(params = {}, { enabled = true } = {}) {
  return useSkillV11Query({ parts: contextParts(null, 'list', params), url: contextEndpoints.list, params: v11PageParams(params), mapper: (value) => mapV11Page(value, mapContextSummary), enabled: enabled && contextAuthoringReady() });
}
export function useContextDetail(contextId, { enabled = true } = {}) {
  return useSkillV11Query({ parts: contextParts(contextId, 'detail'), url: () => contextEndpoints.detail(contextId), mapper: mapContextDetail, enabled: enabled && Boolean(contextId) && contextAuthoringReady() });
}
export function useContextDraft(contextId, { enabled = true } = {}) {
  return useSkillV11Query({ parts: contextParts(contextId, 'draft'), url: () => contextEndpoints.draft(contextId), mapper: mapContextDraft, enabled: enabled && Boolean(contextId) && contextAuthoringReady() });
}
export function useCreateContext() {
  return useSkillV11Mutation({
    send: async ({ idempotencyKey, ...input }, config) => mapDraftOperation(skillV11Result(await axios.post(contextEndpoints.list(), contextCreateInput(input), { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } }))),
    invalidate: () => [contextParts(null, 'list')],
  });
}
export function useStartContextDraft(contextId) {
  return useSkillV11Mutation({
    send: async ({ idempotencyKey, ...input }, config) => mapContextDraft(skillV11Result(await axios.post(contextEndpoints.start(contextId), contextDraftStartInput(input), { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } }))),
    invalidate: () => [contextParts(contextId), contextParts(null, 'list')],
  });
}
export function useSaveContextDraft(contextId) {
  return useSkillV11Mutation({
    send: async (input, config) => mapContextDraft(skillV11Result(await axios.put(contextEndpoints.draft(contextId), contextDraftSaveInput(input), config))),
    invalidate: () => [contextParts(contextId, 'draft'), contextParts(contextId, 'detail'), contextParts(null, 'list')],
  });
}
export function useDiscardContextDraft(contextId) {
  return useSkillV11Mutation({
    send: async ({ revision }, config) => { await axios.delete(contextEndpoints.draft(contextId), { ...config, data: { revision } }); },
    invalidate: () => [contextParts(contextId), contextParts(null, 'list')],
  });
}
export const mapDependencyPreview = (value) => ({
  revision: value.revision, fingerprint: value.fingerprint, candidateDigest: value.candidate_digest,
  resolvedLock: mapContextLock(value.resolved_lock), checks: mapChecks(value.checks),
  changes: value.changes.map((change) => ({ skillId: change.skill_id, mount: change.mount, change: change.change,
    before: change.before ? mapLockedContextLink(change.before) : null, after: change.after ? mapLockedContextLink(change.after) : null, upstreamVersion: change.upstream_version })),
});
export function usePreviewContextDependencies(contextId) {
  return useSkillV11Mutation({ send: async (input, config) => mapDependencyPreview(skillV11Result(await axios.post(contextEndpoints.previewDependencies(contextId), contextDependencyPreviewInput(input), config))) });
}
export function useApplyContextDependencies(contextId) {
  return useSkillV11Mutation({
    send: async ({ idempotencyKey, ...input }, config) => {
      const value = skillV11Result(await axios.post(contextEndpoints.applyDependencies(contextId), contextDependencyApplyInput(input), { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } }));
      if (value.files) return mapContextDraft(value);
      const receipt = mapDraftOperation(value);
      if (receipt.contextId !== contextId) throw new Error('The dependency receipt belongs to another context.');
      const draft = mapContextDraft(skillV11Result(await axios.get(contextEndpoints.draft(contextId), config)));
      return requireAcknowledgedDraft(receipt, draft);
    },
    invalidate: () => [contextParts(contextId)], refreshOnError: () => [contextParts(contextId)],
  });
}
