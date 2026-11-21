import { useQueryClient } from '@tanstack/react-query';
import axios from 'src/utils/axios';
import { apiPath } from 'src/api/contracts/api-surface';
import { useSkillV11Query, useSkillV11Mutation, useSkillV11Scope, skillV11Result, idempotencyHeaders } from './testing';
import { skillsKeys } from './keys';
import { mapContextBinding } from './context-consumers';
import { readContextText } from './context-production';
import { lineDiff } from 'src/sections/skills/contexts/closureDiff';
import { evidenceSelectionInput } from './r3-mappers';

// J6 recovery: one read model over existing contracted endpoints. Eligibility is
// derived from server facts (labels, quarantine, distribution blocks); the page
// never infers delivery or recovery from a pointer move.
const paths = {
  releaseState: (context_id) => apiPath('/model-hub/skills/contexts/{context_id}/release-state/', { context_id }),
  versions: (context_id) => apiPath('/model-hub/skills/contexts/{context_id}/versions/', { context_id }),
  consumers: (context_id) => apiPath('/model-hub/skills/contexts/{context_id}/consumers/', { context_id }),
  inspection: (context_id, number) => apiPath('/model-hub/skills/contexts/{context_id}/versions/{number}/inspection/', { context_id, number }),
  safety: (version_id) => apiPath('/model-hub/skills/version-safety/{version_id}/', { version_id }),
  skill: (skill_id) => apiPath('/model-hub/skills/{skill_id}/', { skill_id }),
  preview: () => apiPath('/model-hub/skills/release-requests/preview/'),
  requests: () => apiPath('/model-hub/skills/release-requests/'),
  request: (request_id) => apiPath('/model-hub/skills/release-requests/{request_id}/', { request_id }),
  credentials: () => apiPath('/model-hub/skills/contexts/credentials/'),
  members: () => apiPath('/accounts/organization/members/'),
};
const status = (error) => error?.response?.status ?? error?.statusCode ?? error?.status;

const fact = (value) => (value ? {
  versionId: value.version_id, digest: value.digest, observedAt: value.observed_at,
  instanceId: value.instance_id, sessionId: value.session_id,
  ...(value.diagnostic_codes ? { diagnosticCodes: value.diagnostic_codes, traceRef: value.trace_ref } : {}),
} : null);

export function deriveCandidates({ versions, productionVersionId, rows }) {
  const production = versions.find((version) => version.id === productionVersionId) ?? null;
  return versions.map((version) => {
    const row = rows[version.id];
    const reasons = [];
    if (version.id === productionVersionId) reasons.push('current_production');
    if (row.quarantined) reasons.push('quarantined');
    if (row.distributionBlocked) reasons.push(row.blockReason ?? 'distribution_blocked');
    return {
      ...version, ...row,
      isProduction: version.id === productionVersionId,
      lowerThanProduction: Boolean(production) && version.number < production.number,
      eligible: reasons.length === 0,
      // The release in production cannot be rolled back to, but its locks and applications can still be recovered.
      selectable: reasons.every((reason) => reason === 'current_production'),
      reasons,
    };
  });
}

export async function loadRecovery(contextId, config) {
  const [state, versionPage, consumerPage] = await Promise.all([
    axios.get(paths.releaseState(contextId), config).then(skillV11Result),
    axios.get(paths.versions(contextId), { ...config, params: { page: 1, page_size: 100 } }).then(skillV11Result),
    axios.get(paths.consumers(contextId), { ...config, params: { page: 1, page_size: 100, window_days: 30 } }).then(skillV11Result),
  ]);
  if (versionPage.total_pages > 1 || consumerPage.total_pages > 1) throw Object.assign(new Error("This context has more than 100 releases or consumers, which recovery can't review completely here."), { code: 'recovery_inventory_limit' });
  const versions = versionPage.results.map((value) => ({ id: value.id, number: value.number, digest: value.digest })).sort((a, b) => b.number - a.number);
  const production = state.labels.find((label) => label.name === 'production') ?? null;
  const rows = {};
  const unavailable = new Map();
  await Promise.all(versions.map(async (version) => {
    const [inspection, safety] = await Promise.all([
      axios.get(paths.inspection(contextId, version.number), { ...config, params: { file_offset: 0, file_limit: 1 } }).then(skillV11Result),
      axios.get(paths.safety(version.id), config).then(skillV11Result),
    ]);
    const links = inspection.descriptor?.links ?? [];
    rows[version.id] = {
      quarantined: safety.quarantined === true, quarantineReason: safety.reason || null,
      replacementVersionId: safety.replacement_version_id ?? null,
      distributionBlocked: inspection.distribution_blocked === true, blockReason: inspection.reason ?? null,
      fileCount: inspection.manifest.file_count,
      children: links.map((link) => ({ skillId: link.skill_id, mount: link.mount, versionId: link.version_id })),
    };
    links.forEach((link) => unavailable.set(link.skill_id, null));
  }));
  // A child whose source is no longer readable keeps its exact bytes in every
  // release that retained it. Report it; never substitute the current source.
  await Promise.all([...unavailable.keys()].map(async (skillId) => {
    try { await axios.get(paths.skill(skillId), config); unavailable.set(skillId, false); }
    catch (error) { if (status(error) === 404) unavailable.set(skillId, true); else throw error; }
  }));
  Object.values(rows).forEach((row) => { row.unavailableChildren = row.children.filter((child) => unavailable.get(child.skillId) === true); });
  return {
    releaseRevision: state.release_revision,
    latestVersion: state.latest_version,
    canPromote: state.permissions.can_promote === true,
    canIssueCredentials: state.permissions.can_issue_credentials === true,
    productionVersionId: production?.version_id ?? null,
    // Who last moved production: the natural first reviewer for a rollback request.
    lastReleaserId: production?.updated_by?.id != null ? String(production.updated_by.id) : null,
    candidates: deriveCandidates({ versions, productionVersionId: production?.version_id ?? null, rows }),
    asOf: consumerPage.as_of,
    cohort: consumerPage.results.map((row) => ({
      binding: mapContextBinding(row.binding), desired: fact(row.desired), loaded: fact(row.loaded),
      installed: fact(row.installed), latestFailure: fact(row.latest_failure ?? null),
      states: row.states, nextActionCode: row.next_action_code, observationsComplete: row.observations_complete,
    })),
  };
}

export function useContextRecovery(contextId) {
  return useSkillV11Query({ parts: ['agent_context', contextId, 'j6-recovery'], enabled: Boolean(contextId), load: (config) => loadRecovery(contextId, config) });
}

// UXF-08: the rollback tracker follows the server, not the page session. These are the
// caller's own open release requests (waiting or approved) for this context, read from the
// authored-requests list and each request's detail. The panel decides which one, if any, is a
// rollback it may show as a tracker; nothing here grants authority to move production.
const OPEN_REQUEST_STATUSES = ['pending', 'approved'];
const OPEN_REQUEST_PAGES = 5;
export async function loadOpenContextRequests(contextId, config) {
  const rows = [];
  try {
    for (const state of OPEN_REQUEST_STATUSES) {
      for (let page = 1; page <= OPEN_REQUEST_PAGES; page += 1) {
        const value = skillV11Result(await axios.get(paths.requests(), { ...config, params: { status: state, page, page_size: 100 } }));
        rows.push(...(value.results ?? []).filter((row) => row.subject_id === contextId && row.subject_kind === 'agent_context'));
        if (page >= (value.total_pages ?? 1)) break;
      }
    }
  } catch (error) {
    // People who can't write release requests have none to resume; their access is shown elsewhere.
    if (status(error) === 403) return [];
    throw error;
  }
  rows.sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)));
  const details = await Promise.all(rows.map(async (row) => {
    try {
      const item = skillV11Result(await axios.get(paths.request(row.id), config));
      const transition = item.packet?.transition;
      if (item.revision !== row.revision || !transition || item.subject_id !== contextId) return null;
      return {
        requestId: item.id, requestRevision: item.revision, status: item.status,
        candidateVersionId: item.packet.candidate_version_id, label: transition.label,
        expectedReleaseRevision: transition.expected_release_revision,
        reviewer: (item.recipients ?? []).map((person) => person.name).filter(Boolean).join(', ') || null,
      };
    } catch { return null; }
  }));
  return details.filter(Boolean);
}
export function useOpenContextRequests(contextId, enabled = true) {
  return useSkillV11Query({ parts: ['agent_context', contextId, 'open-release-requests'], enabled: Boolean(contextId) && enabled, load: (config) => loadOpenContextRequests(contextId, config) });
}

export function useRecoveryReviewers() {
  const access = useSkillV11Scope();
  const self = access.scope.userId;
  return useSkillV11Query({ parts: ['j6-recovery-reviewers', self], load: async (config) => {
    const value = (await axios.get(paths.members(), { ...config, params: { page: 1, limit: 100 } })).data?.result ?? {};
    // A request cannot be addressed to its own author: the server refuses it, so it is never offered.
    return (value.results ?? []).filter((row) => row.type !== 'invite' && row.status !== 'Pending' && row.id && row.id !== self)
      .map((row) => ({ id: row.id, name: row.name || row.email, email: row.email }));
  } });
}

export function usePreviewRecoveryRelease() {
  return useSkillV11Mutation({ send: async ({ contextId, candidateVersionId, evidence = [], external_pipeline = null }, config) => skillV11Result(await axios.post(paths.preview(), {
    resource_id: contextId, kind: 'agent_context', candidate_version_id: candidateVersionId,
    label: 'production', target: null, evidence: evidence.map(evidenceSelectionInput), external_pipeline,
  }, config)) });
}

export function useSubmitRecoveryRequest(contextId) {
  return useSkillV11Mutation({
    send: async ({ preview, reviewerId, idempotencyKey }, config) => skillV11Result(await axios.post(paths.requests(), {
      packet: preview.packet, expected_fingerprint: preview.fingerprint, recipient_ids: [reviewerId], revision: 0,
    }, { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } })),
    invalidate: () => [['agent_context', contextId, 'j6-recovery'], ['agent_context', contextId, 'open-release-requests'], ['review-inbox']],
  });
}

export function useIssueRecoveryCredential(contextId) {
  return useSkillV11Mutation({
    send: async ({ label, versionIds = [], expiresAt, idempotencyKey }, config) => skillV11Result(await axios.post(paths.credentials(), {
      purpose: 'consume', grants: [{ kind: 'agent_context', resource_id: contextId, labels: [label], version_ids: versionIds }],
      binding_ids: [], expires_at: expiresAt,
    }, { ...config, headers: { ...config.headers, ...idempotencyHeaders(idempotencyKey) } })),
  });
}

// Complete closure review: every file of the candidate against the release that
// is in production now. Unchanged files are listed by exact digest; every
// changed, added or removed file is read in full from its immutable release.
const REVIEW_CHANGED_LIMIT = 20;
export const DESCRIPTOR_PATH = '.futureagi/context.json';

const linkName = (mount) => String(mount || '').replace(/^skills\//, '').replace(/\/$/, '') || String(mount || '');
// The machine descriptor, read as people describe it: which linked skill moves between which versions.
// Returns null when either side is not a readable descriptor; the raw diff stays available either way.
export function linkChanges(beforeText, afterText) {
  const parse = (text) => {
    if (text == null) return [];
    const value = JSON.parse(text);
    if (!value || !Array.isArray(value.links)) throw new Error('not a descriptor');
    return value.links;
  };
  let before; let after;
  try { before = parse(beforeText); after = parse(afterText); } catch { return null; }
  const key = (link) => link.skill_id || link.mount;
  const old = new Map(before.map((link) => [key(link), link]));
  const now = new Map(after.map((link) => [key(link), link]));
  const rows = [];
  now.forEach((link, id) => {
    const prior = old.get(id);
    if (!prior) rows.push({ name: linkName(link.mount), from: null, to: link.version });
    else if (prior.version !== link.version || prior.digest !== link.digest) rows.push({ name: linkName(link.mount), from: prior.version, to: link.version });
  });
  old.forEach((link, id) => { if (!now.has(id)) rows.push({ name: linkName(link.mount), from: link.version, to: null }); });
  return rows.sort((a, b) => a.name.localeCompare(b.name));
}

async function readManifest(contextId, number, config) {
  let first;
  const files = [];
  for (let page = 0; page < 400; page += 1) {
    const value = skillV11Result(await axios.get(paths.inspection(contextId, number), { ...config, params: { file_offset: files.length, file_limit: 25 } }));
    if (value.distribution_blocked === true) throw new Error('This release is blocked from distribution.');
    first ??= value;
    if (value.manifest.version_id !== first.manifest.version_id || value.manifest.digest !== first.manifest.digest || value.manifest.file_offset !== files.length) throw new Error('The manifest changed during review.');
    files.push(...value.manifest.files);
    if (value.manifest.next_file_offset === null) break;
    if (value.manifest.next_file_offset !== files.length || !value.manifest.files.length) throw new Error('The complete manifest is unavailable.');
  }
  if (files.length !== first.manifest.file_count) throw new Error('The complete manifest is unavailable.');
  return { versionId: first.manifest.version_id, digest: first.manifest.digest, files: files.map((file) => ({ path: file.path, sha256: file.sha256, sizeBytes: file.size_bytes })) };
}

export async function loadClosureReview({ contextId, candidate, production }, config) {
  const [target, current] = await Promise.all([
    readManifest(contextId, candidate.number, config),
    production && production.id !== candidate.id ? readManifest(contextId, production.number, config) : null,
  ]);
  const now = new Map((current?.files ?? []).map((file) => [file.path, file]));
  const rows = target.files.map((file) => {
    const old = now.get(file.path);
    return { ...file, status: !old ? 'added' : old.sha256 === file.sha256 ? 'unchanged' : 'changed' };
  });
  const kept = new Set(target.files.map((file) => file.path));
  (current?.files ?? []).filter((file) => !kept.has(file.path)).forEach((file) => rows.push({ ...file, status: 'removed' }));
  const changed = rows.filter((row) => row.status !== 'unchanged');
  if (changed.length > REVIEW_CHANGED_LIMIT) throw new Error('Too many changed files for an inline review. Compare these releases in the Versions tab.');
  const changes = await Promise.all(changed.map(async (row) => {
    const [after, before] = await Promise.all([
      row.status === 'removed' ? null : readContextText({ contextId, number: candidate.number, path: row.path, config, expectedDigest: target.digest, expectedSha256: row.sha256 }),
      row.status === 'added' ? null : readContextText({ contextId, number: production.number, path: row.path, config, expectedDigest: current.digest }),
    ]);
    return { path: row.path, status: row.status, lines: lineDiff(before?.text ?? null, after?.text ?? null), ...(row.path === DESCRIPTOR_PATH ? { links: linkChanges(before?.text, after?.text) } : {}) };
  }));
  return { targetDigest: target.digest, currentDigest: current?.digest ?? null, rows, changes };
}

export function useRecoveryClosure(contextId, candidate, production) {
  return useSkillV11Query({
    parts: ['agent_context', contextId, 'j6-closure', candidate?.id, production?.id],
    enabled: Boolean(contextId && candidate), load: (config) => loadClosureReview({ contextId, candidate, production }, config),
  });
}

// One refresh for everything the page shows about this context: the recovery
// view and the release header must never disagree about production.
export function useRefreshContextFacts(contextId) {
  const access = useSkillV11Scope();
  const client = useQueryClient();
  // `options.cancelRefetch: false` lets a background refresh wait for a slow load instead of restarting it.
  return (options) => client.invalidateQueries({ queryKey: skillsKeys.v11(access.scope, 'agent_context', contextId) }, options);
}
