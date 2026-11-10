import axios from 'src/utils/axios';
import { apiPath, getContractedApiMethods } from 'src/api/contracts/api-surface';
import { useSkillV11Query, useSkillV11Mutation, skillV11Result, idempotencyHeaders } from './testing';
import { contextEndpoints, contextParts, mapContextDraft, mapContextReleaseState } from './context-authoring';
import { mapContextFile, mapLockedContextLink, contextHeadAcknowledgmentInput, contextPublishInput } from './context-mappers';
import { mapChecks, mapPublish, mapVersion } from './mappers';
import { mapV11Page } from './v11-mappers';
import { mapContextBinding } from './context-consumers';

const inspection = (context_id, number) => apiPath('/model-hub/skills/contexts/{context_id}/versions/{number}/inspection/', { context_id, number });
const versionFile = (context_id, number) => apiPath('/model-hub/skills/contexts/{context_id}/versions/{number}/inspection/files/', { context_id, number });
const draftFile = (context_id) => apiPath('/model-hub/skills/contexts/{context_id}/draft/files/', { context_id });
const reviewPath = (context_id) => apiPath('/model-hub/skills/contexts/{context_id}/release/review/', { context_id });
const acknowledgePath = (context_id) => apiPath('/model-hub/skills/contexts/{context_id}/draft/acknowledge-head/', { context_id });
const publishPath = (context_id) => apiPath('/model-hub/skills/contexts/{context_id}/draft/publish/', { context_id });
const ready = (path, method = 'get') => getContractedApiMethods(path).includes(method);
export const contextInspectionReady = () => ready('/model-hub/skills/contexts/{context_id}/versions/{number}/inspection/');
export const contextReviewReady = () => ready('/model-hub/skills/contexts/{context_id}/release/review/', 'post') && ready('/model-hub/skills/contexts/{context_id}/draft/acknowledge-head/', 'post') && ready('/model-hub/skills/contexts/{context_id}/draft/publish/', 'post');

export function useContextVersions(contextId, page = 1) {
  return useSkillV11Query({ parts: contextParts(contextId, 'versions', page), url: () => contextEndpoints.versions(contextId), params: { page, page_size: 100 }, mapper: (value) => mapV11Page(value, mapVersion), enabled: Boolean(contextId) && contextInspectionReady() });
}

// Never use server-provided file URLs as authenticated fetch destinations. Every
// chunk uses the contracted scoped endpoint and is fenced to one immutable identity.
export async function readContextText({ contextId, number, revision, path, config, expectedDigest, expectedSha256 }) {
  let offset = 0;
  let text = '';
  let identity;
  for (let page = 0; page < 1024; page += 1) {
    const value = skillV11Result(await axios.get(revision == null ? versionFile(contextId, number) : draftFile(contextId), { ...config, params: { path, offset, max_bytes: 32768, ...(revision == null ? {} : { revision }) } }));
    const key = JSON.stringify([value.version_id, value.revision, value.digest, value.sha256, value.total_chars]);
    if (value.path !== path || value.offset !== offset || (identity && key !== identity) || (revision != null && value.revision !== revision) || (expectedDigest && value.digest !== expectedDigest) || (expectedSha256 && value.sha256 !== expectedSha256)) throw new Error('The reviewed file identity changed. Reload the exact release.');
    identity = key;
    text += value.text;
    if (value.eof) return { path, text, revision: value.revision, versionId: value.version_id, digest: value.digest, sha256: value.sha256 };
    if (!Number.isSafeInteger(value.next_offset) || value.next_offset <= offset) throw new Error('The complete file could not be read.');
    offset = value.next_offset;
  }
  throw new Error('The complete file exceeds the review read limit.');
}

export function useContextInspection(contextId, number) {
  return useSkillV11Query({
    parts: contextParts(contextId, 'inspection', number), url: () => inspection(contextId, number), params: { file_limit: 25 },
    enabled: Boolean(contextId && number) && contextInspectionReady(),
    load: async (config) => {
      const first = skillV11Result(await axios.get(inspection(contextId, number), config));
      const files = [...first.manifest.files];
      let offset = first.manifest.next_file_offset;
      while (offset !== null) {
        if (!Number.isSafeInteger(offset) || offset !== files.length || files.length > 10000) throw new Error('The complete manifest is unavailable.');
        const next = skillV11Result(await axios.get(inspection(contextId, number), { ...config, params: { file_offset: offset, file_limit: 25 } }));
        if (next.manifest.version_id !== first.manifest.version_id || next.manifest.digest !== first.manifest.digest || !next.manifest.files.length) throw new Error('The manifest changed during inspection.');
        files.push(...next.manifest.files);
        offset = next.manifest.next_file_offset;
      }
      const value = first;
      return {
      version: { id: value.manifest.version_id, number: value.manifest.version, digest: value.manifest.digest },
      descriptor: { ...value.descriptor, links: (value.descriptor.links || []).map(mapLockedContextLink) },
      files: files.map(mapContextFile),
      snapshot: { kind: value.manifest.kind, name: value.manifest.name, version: value.manifest.version, versionId: value.manifest.version_id, digest: value.manifest.digest },
      distributionBlocked: value.distribution_blocked,
      };
    },
  });
}
export function useContextInspectionFile(contextId, number, path, versionId, digest) {
  return useSkillV11Query({ parts: contextParts(contextId, 'inspection-file', number, versionId, path), enabled: Boolean(contextId && number && path && versionId) && contextInspectionReady(), load: async (config) => {
    const value = await readContextText({ contextId, number, path, config, expectedDigest: digest });
    if (value.versionId !== versionId) throw new Error('The file does not match the viewed release.');
    return value;
  } });
}

export const mapHeadReview = (value) => ({ subjectId: value.subject_id, kind: value.kind, draftRevision: value.draft_revision, candidateDigest: value.candidate_digest, baseVersion: value.base_version, expectedHeadVersion: value.expected_head_version, headDigest: value.head_digest, reviewFingerprint: value.review_fingerprint });

async function loadContextComparison(contextId, revision, config, published = null) {
  let snapshot;
  const origin = [];
  const head = [];
  for (let page = 1; page <= 100; page += 1) {
    const value = skillV11Result(await axios.post(reviewPath(contextId), { ...(published || { operation: 'publish', revision }), page }, config));
    if (snapshot && snapshot.review_fingerprint !== value.review_fingerprint) throw new Error('The release changed while reading its comparison. Review again.');
    snapshot = value;
    origin.push(...value.changes.files);
    head.push(...value.head_changes.files);
    if (page >= Math.max(value.changes.total_pages, value.head_changes.total_pages)) break;
    if (page === 100) throw new Error('The complete comparison is unavailable.');
  }
  if (!published && (snapshot.candidate.revision !== revision || !snapshot.head_review || snapshot.head_review.draft_revision !== revision || snapshot.head_review.candidate_digest !== snapshot.candidate.digest)) throw new Error('The saved draft no longer matches this review.');
  return { snapshot, origin, head };
}

// The comparison contains the complete compiled tree (including unchanged
// files). Read metadata only here; load selected bytes through the scoped draft
// endpoint, never through a consume credential or a server-provided URL.
export function useContextDraftInventory(contextId, revision) {
  return useSkillV11Query({ parts: contextParts(contextId, 'compiled-inventory', revision), enabled: Boolean(contextId && revision) && contextReviewReady(), load: async (config) => {
    const { snapshot, origin } = await loadContextComparison(contextId, revision, config);
    return { revision, digest: snapshot.candidate.digest, files: origin.filter((file) => file.to_sha256 !== null).map((file) => ({ path: file.path, sha256: file.to_sha256, revision })) };
  } });
}
export function useContextDraftFile(contextId, revision, path, inventory) {
  const file = inventory?.revision === revision ? inventory.files.find((item) => item.path === path) : null;
  return useSkillV11Query({ parts: contextParts(contextId, 'compiled-file', revision, inventory?.digest, path, file?.sha256), enabled: Boolean(file), load: (config) => readContextText({ contextId, revision, path, expectedDigest: inventory.digest, expectedSha256: file.sha256, config }) });
}

export async function loadContextReview(contextId, revision, config, published = null) {
  const { snapshot, origin, head } = await loadContextComparison(contextId, revision, config, published);
  const textCache = new Map();
  const read = (file, number, isDraft) => {
    const hash = isDraft ? file.to_sha256 : file.from_sha256;
    if (hash === null) return Promise.resolve('');
    const key = JSON.stringify([file.path, number, isDraft, hash]);
    if (!textCache.has(key)) textCache.set(key, readContextText({ contextId, path: file.path, number: isDraft && published ? published.target_version : number, revision: isDraft && !published ? revision : null, expectedDigest: isDraft ? snapshot.candidate.digest : undefined, expectedSha256: hash, config }).then((value) => value.text));
    return textCache.get(key);
  };
  const hydrate = (files, number) => Promise.all(files.filter((file) => file.status !== 'unchanged').map(async (file) => ({ path: file.path, status: file.status, fromText: await read(file, number, false), toText: await read(file, null, true) })));
  const changes = await hydrate(origin, snapshot.changes.from);
  const headChanges = await hydrate(head, snapshot.latest_version);
  return { review: snapshot.head_review ? mapHeadReview(snapshot.head_review) : null, originChanges: changes, headChanges, checks: mapChecks(snapshot.checks), releaseState: mapContextReleaseState(snapshot.release_state), candidateDigest: snapshot.candidate.digest, impact: { ...snapshot.impact, moreAvailable: snapshot.impact.more_available }, complete: true };
}
export function usePreviewContextRelease(contextId) {
  return useSkillV11Mutation({ send: (input, config) => loadContextReview(contextId, input.revision, config) });
}
export function useAcknowledgeContextHead(contextId) {
  return useSkillV11Mutation({ send: async (input, config) => mapContextDraft(skillV11Result(await axios.post(acknowledgePath(contextId), contextHeadAcknowledgmentInput(input), config))), invalidate: () => [contextParts(contextId)] });
}
export function usePublishContext(contextId) {
  return useSkillV11Mutation({ send: async ({ idempotencyKey, ...input }, config) => mapPublish(skillV11Result(await axios.post(publishPath(contextId), contextPublishInput(input), { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } }))), invalidate: () => [contextParts(contextId), contextParts(null, 'list')] });
}
const consumerPath = (context_id) => apiPath('/model-hub/skills/contexts/{context_id}/consumers/', { context_id });
export function useContextConsumers(contextId, { page = 1, windowDays = 7 } = {}) {
  const fact = (value) => value === null ? null : ({ versionId: value.version_id, digest: value.digest, observedAt: value.observed_at, receivedAt: value.received_at, origin: value.origin, instanceId: value.instance_id, sequence: value.sequence, sessionId: value.session_id });
  return useSkillV11Query({ parts: contextParts(contextId, 'consumers', page, windowDays), url: () => consumerPath(contextId), params: { page, window_days: windowDays }, enabled: Boolean(contextId) && ready('/model-hub/skills/contexts/{context_id}/consumers/'), mapper: (value) => ({ ...mapV11Page(value, (row) => ({ binding: mapContextBinding(row.binding), desired: fact(row.desired), resolved: fact(row.resolved), installed: fact(row.installed), discovered: fact(row.discovered), loaded: fact(row.loaded), observationAgeSeconds: row.observation_age_seconds, states: row.states, nextActionCode: row.next_action_code, observationsComplete: row.observations_complete })), asOf: value.as_of, coverage: value.coverage }) });
}
