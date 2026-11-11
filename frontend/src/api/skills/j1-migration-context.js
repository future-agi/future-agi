import axios from 'src/utils/axios';
import { apiPath } from 'src/api/contracts/api-surface';
import { idempotencyHeaders, skillV11Result, useSkillV11Mutation } from './testing';
import { contextCreateInput, contextEndpoints, contextParts, mapContextDraft, mapDependencyPreview, mapDraftOperation, requireAcknowledgedDraft } from './context-authoring';
import { contextDependencyApplyInput, contextDependencyPreviewInput, contextHeadAcknowledgmentInput, contextPublishInput } from './context-mappers';
import { loadContextReview } from './context-production';
import { mapPublish } from './mappers';

// J1: assemble one agent context from the versions a retained migration just
// published. Two steps, each bound to what the owner saw:
//  1. prepare: create the private draft (AGENTS.md) and preview the exact pinned
//     links read-only. Nothing is linked or published yet.
//  2. publish: apply exactly the previewed links (server-checked fingerprint),
//     confirm the compiled candidate is the one reviewed, acknowledge its head
//     and publish. Any difference stops before publication.

const headers = (config, key) => ({ ...config, headers: { ...config.headers, ...idempotencyHeaders(key) } });

/** A packet proposal is plain data from a local file; never trust its shape. */
export function contextProposalFromPacket(value) {
  const context = value?.context;
  if (!context || typeof context.name !== 'string' || typeof context.description !== 'string' || typeof context.agents_md !== 'string') return null;
  return { name: context.name, description: context.description, agents: context.agents_md };
}

/** Exact version pins for every published outcome, one per skill. */
export function pinnedLinks(published) {
  const seen = new Set();
  return published.filter((link) => {
    if (typeof link?.skillId !== 'string' || !Number.isSafeInteger(link.version) || link.version < 1 || seen.has(link.skillId)) return false;
    seen.add(link.skillId);
    return true;
  }).map((link) => ({ skillId: link.skillId, selector: { version: link.version } }));
}

async function readDraft(contextId, receipt, config) {
  const draft = mapContextDraft(skillV11Result(await axios.get(contextEndpoints.draft(contextId), config)));
  return requireAcknowledgedDraft(receipt, draft);
}

export async function prepareMigrationContext({ name, description, agents, links, keys }, config) {
  if (!links.length || links.length > 20) throw new Error('An agent context links 1–20 published skills.');
  const created = mapDraftOperation(skillV11Result(await axios.post(contextEndpoints.list(),
    contextCreateInput({ name, description, tags: [], files: [{ path: 'AGENTS.md', content: agents }] }), headers(config, keys.create))));
  if (!created.contextId) throw new Error('The context draft was not created.');
  const draft = await readDraft(created.contextId, created, config);
  const preview = mapDependencyPreview(skillV11Result(await axios.post(contextEndpoints.previewDependencies(created.contextId),
    contextDependencyPreviewInput({ revision: draft.revision, links, refreshSkillIds: [] }), config)));
  return { contextId: created.contextId, name, draft, preview, links };
}

export async function publishMigrationContext({ prepared, acknowledgeWarnings, keys }, config) {
  const { contextId, preview, links } = prepared;
  const value = skillV11Result(await axios.post(contextEndpoints.applyDependencies(contextId),
    contextDependencyApplyInput({ revision: preview.revision, links, refreshSkillIds: [], expectedFingerprint: preview.fingerprint }), headers(config, keys.apply)));
  const applied = value.files ? mapContextDraft(value) : await readDraft(contextId, mapDraftOperation(value), config);
  const review = await loadContextReview(contextId, applied.revision, config);
  const head = review.review;
  if (!head || head.candidateDigest !== preview.candidateDigest || review.candidateDigest !== preview.candidateDigest) {
    throw new Error('The compiled context differs from the one you reviewed. Nothing was published; review it again.');
  }
  if (review.checks.summary.errors > 0) throw new Error('The compiled context has blocking checks. Nothing was published.');
  if (review.checks.summary.warnings > 0 && !acknowledgeWarnings) throw new Error('Acknowledge the warnings for this exact candidate first.');
  const acknowledged = mapContextDraft(skillV11Result(await axios.post(apiPath('/model-hub/skills/contexts/{context_id}/draft/acknowledge-head/', { context_id: contextId }),
    contextHeadAcknowledgmentInput({ revision: head.draftRevision, expectedDigest: head.candidateDigest, expectedHeadVersion: head.expectedHeadVersion, reviewFingerprint: head.reviewFingerprint }), config)));
  if (!acknowledged.headAcknowledged || acknowledged.expectedHeadVersion !== head.expectedHeadVersion) throw new Error('The publication head changed. Nothing was published; review it again.');
  const receipt = mapPublish(skillV11Result(await axios.post(apiPath('/model-hub/skills/contexts/{context_id}/draft/publish/', { context_id: contextId }),
    contextPublishInput({ revision: acknowledged.revision, expectedDigest: head.candidateDigest, expectedHeadVersion: head.expectedHeadVersion,
      reviewFingerprint: acknowledged.reviewFingerprint, baseVersion: acknowledged.baseVersion, note: '', acknowledgeWarnings: Boolean(acknowledgeWarnings) }), headers(config, keys.publish))));
  return { contextId, receipt, canIssueCredentials: review.releaseState.permissions.canIssueCredentials === true };
}

export function usePrepareMigrationContext() {
  return useSkillV11Mutation({ send: prepareMigrationContext, invalidate: () => [contextParts(null, 'list')] });
}
export function usePublishMigrationContext() {
  return useSkillV11Mutation({ send: publishMigrationContext, invalidate: (value) => [contextParts(value.contextId), contextParts(null, 'list')] });
}
