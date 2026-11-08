import axios from 'src/utils/axios';
import { apiPath, isContractedApiPath } from 'src/api/contracts/api-surface';
import { useSkillV11Mutation, useSkillV11Query, idempotencyHeaders, skillV11Result, v11PageParams } from './testing';

const ROOT = '/model-hub/skills/contexts/campaigns/';
const DETAIL = `${ROOT}{campaign_id}/`;
const TARGETS = `${DETAIL}targets/`;
const TARGET = `${TARGETS}{binding_id}/`;

// No uncontracted fallback. The disabled screen is safe before I lands the
// serializer-owned routes and regenerates the shared API catalog.
export const campaignsContractReady = () => [ROOT, DETAIL, TARGETS, `${DETAIL}authorize/`, `${DETAIL}pause/`, `${DETAIL}resume/`, `${DETAIL}cancel/`, `${DETAIL}rollback/preview/`, `${TARGET}observations/`].every(isContractedApiPath);
const detailPath = (campaignId, suffix = '') => apiPath(`${DETAIL}${suffix}`, { campaign_id: campaignId });

export function mapCampaign(value) {
  return {
    id: value.id, name: value.name, revision: value.revision, status: value.status,
    targetCount: value.target_count, manifestDigest: value.manifest_digest,
    parentCampaignId: value.parent_campaign_id, authorizedAt: value.authorized_at,
    reason: value.reason, createdAt: value.created_at, provenance: value.provenance,
  };
}
export const mapCampaignDetail = (value) => ({ ...mapCampaign(value), enabled: value.enabled, canAuthorize: value.can_authorize });

export function mapCampaignPlan(value) {
  if (value === null) return null;
  return {
    campaignRevision: value.campaign_revision, bindingRevision: value.binding_revision,
    repositoryId: value.repository_id, baseCommit: value.base_commit,
    previousLockDigest: value.previous_lock_digest, desiredVersionId: value.desired_version_id,
    materializationDigest: value.materialization_digest, planDigest: value.plan_digest,
    localReviewRequired: value.local_review_required, blockers: value.blockers,
    changes: value.changes.map((row) => ({ path: row.path, action: row.action,
      beforeSha256: row.before_sha256, afterSha256: row.after_sha256,
      ownership: row.ownership, diffPreview: row.diff_preview })),
  };
}

export function mapCampaignTarget(value) {
  return {
    bindingId: value.binding_id, name: value.name, bindingRevision: value.binding_revision,
    selector: value.selector, desiredVersion: value.desired_version,
    desiredVersionId: value.desired_version_id, desiredDigest: value.desired_digest,
    repository: { provider: value.repository.provider, repositoryId: value.repository.repository_id,
      defaultBranch: value.repository.default_branch, projectScope: value.repository.project_scope },
    plan: mapCampaignPlan(value.plan), planDigest: value.plan_digest,
    decision: value.decision, reason: value.reason, prState: value.pr_state, mergeState: value.merge_state,
    provenance: value.provenance,
    attempt: value.attempt === null ? null : {
      id: value.attempt.attempt_id, generation: value.attempt.claim_generation,
      expiresAt: value.attempt.expires_at, mode: value.attempt.mode, state: value.attempt.state,
      branch: value.attempt.branch, marker: value.attempt.marker,
      receipt: value.attempt.receipt === null ? null : {
        state: value.attempt.receipt.state, repositoryCommit: value.attempt.receipt.repository_commit,
        pullRequestNumber: value.attempt.receipt.pull_request_number, reason: value.attempt.receipt.reason,
      },
    },
  };
}

export function mapCampaignObservations(value) {
  const stage = (row) => row === null ? null : ({ id: row.id, versionId: row.version_id,
    digest: row.digest, observedAt: row.observed_at, origin: row.origin, stale: row.stale });
  return { bindingId: value.binding_id, coverage: value.coverage,
    installed: stage(value.installed), discovered: stage(value.discovered),
    delivered: stage(value.instructions_delivered) };
}
const mapPage = (value, mapper) => ({ results: value.results.map(mapper), count: value.count,
  page: value.page, pageSize: value.page_size, totalPages: value.total_pages });

export function useContextCampaignAvailability() {
  const path = '/model-hub/skills/contexts/capabilities/';
  return useSkillV11Query({ parts: ['contexts', 'capabilities'], url: () => apiPath(path),
    mapper: (value) => ({ enabled: value.campaigns_enabled === true }),
    enabled: campaignsContractReady() && isContractedApiPath(path) });
}

export function useContextCampaigns(params = {}) {
  return useSkillV11Query({ parts: ['contexts', 'campaigns', params], url: () => apiPath(ROOT),
    params: v11PageParams(params), mapper: (value) => mapPage(value, mapCampaign), enabled: campaignsContractReady() });
}
export function useContextCampaign(campaignId) {
  return useSkillV11Query({ parts: ['contexts', 'campaign', campaignId], url: () => detailPath(campaignId),
    mapper: mapCampaignDetail, enabled: Boolean(campaignId) && campaignsContractReady() });
}
export function useContextCampaignTargets(campaignId, params = {}) {
  return useSkillV11Query({ parts: ['contexts', 'campaign', campaignId, 'targets', params],
    url: () => detailPath(campaignId, 'targets/'), params: v11PageParams(params),
    mapper: (value) => mapPage(value, mapCampaignTarget), enabled: Boolean(campaignId) && campaignsContractReady() });
}
export function useContextCampaignObservations(campaignId, bindingId) {
  return useSkillV11Query({ parts: ['contexts', 'campaign', campaignId, 'observations', bindingId],
    url: () => apiPath(`${TARGET}observations/`, { campaign_id: campaignId, binding_id: bindingId }),
    mapper: mapCampaignObservations, enabled: Boolean(campaignId && bindingId) && campaignsContractReady() });
}
export function useCreateContextCampaign() {
  return useSkillV11Mutation({
    send: async ({ name, targets, parentCampaignId = null, idempotencyKey }, config) => mapCampaign(skillV11Result(await axios.post(apiPath(ROOT), {
      name, targets: targets.map(({ bindingId, version }) => ({ binding_id: bindingId, version })), parent_campaign_id: parentCampaignId,
    }, { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } }))),
    invalidate: () => [['contexts', 'campaigns']],
  });
}
export function campaignAuthorizationInput({ revision, expectedManifestDigest, decisions, acknowledgeRepositoryWrites }) {
  return { revision, expected_manifest_digest: expectedManifestDigest,
    decisions: decisions.map(({ bindingId, planDigest, decision, reason }) => ({ binding_id: bindingId, plan_digest: planDigest, decision, reason })),
    acknowledge_repository_writes: acknowledgeRepositoryWrites };
}
export function useAuthorizeContextCampaign(campaignId) {
  return useSkillV11Mutation({
    send: async (input, config) => mapCampaign(skillV11Result(await axios.post(detailPath(campaignId, 'authorize/'), campaignAuthorizationInput(input), config))),
    invalidate: () => [['contexts', 'campaign', campaignId], ['contexts', 'campaigns']],
    refreshOnError: () => [['contexts', 'campaign', campaignId]],
  });
}
export function useChangeContextCampaignStatus(campaignId) {
  return useSkillV11Mutation({
    send: async ({ action, revision, reason }, config) => {
      if (!['pause', 'resume', 'cancel'].includes(action)) throw new Error('Unknown campaign action.');
      return mapCampaign(skillV11Result(await axios.post(detailPath(campaignId, `${action}/`), { revision, reason }, config)));
    },
    invalidate: () => [['contexts', 'campaign', campaignId], ['contexts', 'campaigns']],
    refreshOnError: () => [['contexts', 'campaign', campaignId]],
  });
}
export function usePreviewContextCampaignRollback(campaignId) {
  return useSkillV11Mutation({ send: async ({ revision }, config) => {
    const value = skillV11Result(await axios.post(detailPath(campaignId, 'rollback/preview/'), { revision }, config));
    return { parentCampaignId: value.parent_campaign_id, revision: value.revision, executable: value.executable,
      targets: value.targets.map((row) => ({ bindingId: row.binding_id, previousLockDigest: row.previous_lock_digest,
        baseCommit: row.base_commit, state: row.state, blockers: row.blockers,
        reverseChanges: row.reverse_changes.map((change) => ({ path: change.path, action: change.action,
          beforeSha256: change.before_sha256, afterSha256: change.after_sha256, ownership: change.ownership, diffPreview: change.diff_preview })) })) };
  } });
}
