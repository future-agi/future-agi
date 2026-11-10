import axios from 'src/utils/axios';
import { apiPath } from 'src/api/contracts/api-surface';
import { idempotencyHeaders, skillV11Result, useSkillV11Mutation } from './testing';
import { retainedPlanAcceptanceInput, retainedPlanKeepInput } from './r3-intake';

const reviewPath = (plan_id) => apiPath('/model-hub/skills/intake/{plan_id}/review/', { plan_id });
const changes = (comparison) => comparison?.files.map((file) => ({
  path: file.path, status: file.status, fromText: file.from_text, toText: file.to_text,
}));

export function mapMigrationPlan(value) {
  return {
    id: value.id, fingerprint: value.fingerprint, expiresAt: value.expires_at, snapshotOnly: true, requiresKeepRights: true,
    rejected: value.inventory.rejected,
    skipped: value.inventory.skipped.map((path) => ({ path, reason: 'housekeeping' })),
    items: value.inventory.items.map((item) => ({
      key: item.key, root: item.root, name: item.name, kind: item.kind,
      blocked: item.blockers.length > 0,
      blockers: item.blockers.map((finding) => `${finding.path}: ${finding.reason}`),
      files: item.files.map((file) => ({ path: file.path, sizeBytes: file.size_bytes })),
      fileCount: item.files.length, totalBytes: item.files.reduce((total, file) => total + file.size_bytes, 0),
      destinationId: item.destination_id, expectedHeadVersion: item.expected_head_version,
      expectedHeadDigest: item.expected_head_digest,
      sourceOrigin: item.source?.origin, sourceProject: item.source?.project,
      sourceResource: item.source?.resource, sourceRevision: item.source?.revision,
      diverged: item.migration.hub_diverged, manifestComparison: item.migration.files,
    })),
  };
}

export function mapMigrationReview(value) {
  return { sourceChanges: changes(value.base_source), hubChanges: changes(value.base_hub),
    sourceHubChanges: changes(value.source_hub), hasDraft: value.has_draft,
    basisPayloadAvailable: value.basis_payload_available };
}

export function migrationDecisionInput(value) {
  const { plan_id: _planId, ...body } = value.decision === 'keep'
    ? retainedPlanKeepInput({ ...value, rightsConfirmed: value.rightsConfirmed ?? false }) : retainedPlanAcceptanceInput(value);
  return body;
}

export function useMigrationPreview() {
  return useSkillV11Mutation({ send: async ({ form, exportData }, config) => {
    const wire = skillV11Result(await axios.post(exportData
      ? apiPath('/model-hub/skills/intake/langfuse/preview/') : apiPath('/model-hub/skills/intake/preview/'),
    exportData || form, config));
    const plan = mapMigrationPlan(wire);
    const items = await Promise.all(plan.items.map(async (item) => {
      if (item.blocked) return item;
      const review = skillV11Result(await axios.post(reviewPath(plan.id), {
        item_key: item.key, expected_fingerprint: plan.fingerprint,
      }, config));
      return { ...item, ...mapMigrationReview(review) };
    }));
    return { ...plan, items };
  } });
}

export function useMigrationDecision() {
  return useSkillV11Mutation({ send: async ({ idempotencyKey, ...input }, config) => {
    const result = skillV11Result(await axios.post(apiPath('/model-hub/skills/intake/{plan_id}/accept/', { plan_id: input.planId }),
      migrationDecisionInput(input), { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } }));
    return { resourceId: result.resource_id, decision: result.decision, published: result.published,
      draftRevision: result.draft_revision, digest: result.digest, receiptId: `${input.planId}/${input.itemKey}` };
  }, invalidate: () => [['migration']], invalidateLegacy: () => [['skills', 'list']] });
}
