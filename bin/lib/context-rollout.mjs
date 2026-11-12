import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { CampaignBatch, lockedVersion } from './context-campaign-batch.mjs';
import { probeCodexLoad } from './context-load-probe.mjs';

const ROOT = '/model-hub/skills/contexts/';
const execute = promisify(execFile);
const need = (condition, code) => { if (!condition) throw new Error(code); };
const sha256 = (value) => createHash('sha256').update(value).digest('hex');
const byRepository = (a, b) => (a.repository_id < b.repository_id ? -1 : a.repository_id > b.repository_id ? 1 : 0);

/** Stable JSON for review-packet digests: sorted keys, finite JSON values only. */
export function canonicalJson(value) {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') return JSON.stringify(value);
  if (typeof value === 'number' && Number.isFinite(value)) return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && Object.getPrototypeOf(value) === Object.prototype) {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
  }
  throw new TypeError('invalid_packet_value');
}
export const packetDigest = (body) => sha256(canonicalJson(body));

/**
 * Exception kinds whose disposition follows from an earlier explicit operator
 * decision (a deliberate pin, an ownership-stage exclusion) or an observed fact
 * (the repository is unavailable). They are shown together and acknowledged in
 * one review; nothing about them is decided silently. Any other exception (an
 * unproven delta, a local conflict) is reviewed on its own.
 */
export const HELD_KINDS = Object.freeze(['pinned_selector', 'pinned_current', 'operator_exclusion', 'unavailable']);
// Files that no adoption profile ever writes: personal/local overrides. They are
// listed (path and hash) as retained local-owned files, never grouped away silently.
export const NEVER_MANAGED = Object.freeze(['CLAUDE.local.md', 'AGENTS.override.md']);

function unitsOf({ groups, held, individual }) {
  return [
    ...groups.map((group) => ({ kind: 'group', key: group.key, repository_ids: group.members.map((row) => row.repository_id) })),
    ...(held.length ? [{ kind: 'held', key: 'held', repository_ids: held.map((row) => row.repository_id) }] : []),
    ...individual.map((row) => ({ kind: row.kind, key: `${row.kind}:${row.repository_id}`, repository_ids: [row.repository_id] })),
  ];
}

function sealed(body, expectedCount) {
  const units = unitsOf(body);
  const ids = units.flatMap((unit) => unit.repository_ids);
  need(ids.length === expectedCount && new Set(ids).size === expectedCount, 'review_items_not_exactly_once');
  return { ...body, units, packet_sha256: packetDigest(body) };
}

/**
 * One grouped review packet over a cold inventory. A group is a set of
 * repositories whose shared instruction files (every file an adoption may give a
 * managed segment) are byte-identical, so one reading decides their ownership.
 * Personal local overrides are listed per repository by hash and retained. The
 * unavailable or incompletely inventoried repositories are held exceptions.
 */
export function ownershipPacket(inventory) {
  need(Array.isArray(inventory?.targets) && inventory.targets.length === inventory.target_count, 'inventory_incomplete');
  const groups = new Map(); const held = []; const individual = [];
  for (const target of [...inventory.targets].sort(byRepository)) {
    if (target.state === 'unavailable') { held.push({ repository_id: target.repository_id, kind: 'unavailable', reason: 'Repository unavailable; no ownership is established.' }); continue; }
    const findings = target.observation?.findings ?? [];
    if (target.state !== 'inventoried' || target.observation?.inventory_complete !== true || target.observation.lock_sha256) {
      individual.push({ repository_id: target.repository_id, kind: 'inventory_exception', reason: 'Inventory incomplete or an existing lock was found; review this repository on its own.',
        observed: { state: target.state, lock_sha256: target.observation?.lock_sha256 ?? null } });
      continue;
    }
    const files = findings.filter((row) => row.sha256 !== undefined).map((row) => ({ path: row.path, sha256: row.sha256 }))
      .sort((a, b) => (a.path < b.path ? -1 : 1));
    const shared = files.filter((row) => !NEVER_MANAGED.includes(row.path.split('/').at(-1)));
    const retained = files.filter((row) => NEVER_MANAGED.includes(row.path.split('/').at(-1)));
    const key = packetDigest({ shared, retained_paths: retained.map((row) => row.path) });
    const group = groups.get(key) ?? { key, shared, members: [] };
    group.members.push({ repository_id: target.repository_id, retained });
    groups.set(key, group);
  }
  return sealed({ kind: 'ownership', target_count: inventory.target_count, groups: [...groups.values()], held, individual }, inventory.target_count);
}

/**
 * Campaign-level public rollout path. Each method is one operator command over
 * the whole frozen campaign. Review stays a separate human decision bound to a
 * packet digest; authorization re-derives the packet from server state and
 * refuses if anything changed or if the acknowledged exceptions differ.
 */
export class ContextRollout {
  constructor({ hub, executor, python, env = process.env, signal }) {
    need(typeof hub === 'function', 'rollout_transport_required');
    this.hub = hub; this.executor = executor; this.python = python; this.env = { ...env }; this.signal = signal;
  }

  batch() {
    return new CampaignBatch({ hub: this.hub, executor: this.executor, python: this.python, env: this.env, signal: this.signal });
  }

  // ---- Releases: create/edit, then one review and one approval per release ----
  /** Create the context and return the candidate's review packet in the same step (nothing is published). */
  async createContext({ name, description = '', files }) {
    const created = await this.hub('POST', ROOT, { name, description, tags: [], files });
    return { context_id: created.context_id, revision: created.revision,
      review: await this.reviewRelease({ contextId: created.context_id, revision: created.revision }) };
  }

  /** Start a draft from the current head, save the edited files and return the candidate's review packet in one step. */
  async editDraft({ contextId, fromVersion, files, description = '' }) {
    const root = `${ROOT}${contextId}/`;
    const draft = await this.hub('POST', `${root}draft/start/`, { revision: 0, from_version: fromVersion });
    const saved = await this.hub('PUT', `${root}draft/`, { revision: draft.revision, base_version: fromVersion, files, description, note: '' });
    return { revision: saved.revision, review: await this.reviewRelease({ contextId, revision: saved.revision }) };
  }

  /** The review packet the operator reads before approving publication. */
  async reviewRelease({ contextId, revision }) {
    const head = await this.hub('POST', `${ROOT}${contextId}/draft/head-review/`, { revision });
    return { ...head, context_id: contextId, packet_sha256: packetDigest({ context_id: contextId, revision: head.draft_revision ?? revision,
      candidate_digest: head.candidate_digest, expected_head_version: head.expected_head_version ?? null, review_fingerprint: head.review_fingerprint }) };
  }

  /** Approve exactly the reviewed candidate: acknowledgement and publication are bound to its digest and fingerprint. */
  async publishReviewed({ review, baseVersion, note = '' }) {
    const root = `${ROOT}${review.context_id}/`;
    const ack = await this.hub('POST', `${root}draft/acknowledge-head/`, { revision: review.draft_revision, expected_digest: review.candidate_digest,
      expected_head_version: review.expected_head_version, review_fingerprint: review.review_fingerprint });
    return (await this.hub('POST', `${root}draft/publish/`, { revision: ack.revision, expected_digest: review.candidate_digest,
      expected_head_version: review.expected_head_version, review_fingerprint: ack.review_fingerprint, base_version: baseVersion, note, acknowledge_warnings: true })).version;
  }

  // ---- Rollout rounds ----
  /**
   * Freeze every binding at one release, issue the planning credential the
   * frozen campaign implies (desired releases plus releases already locked in
   * the selected checkouts, so verify/restore keep working) and plan every
   * target. Returns the grouped review packet; nothing is authorized.
   */
  async start({ contextId, contextName, campaignName, parentCampaignId = null, bindings, version, selected, exclusions = {}, mode = 'plan', client, restoreLocks = {}, outDir }) {
    need(Array.isArray(bindings) && bindings.length > 0 && Number.isInteger(version) && contextName && campaignName, 'invalid_rollout_request');
    const created = await this.hub('POST', `${ROOT}campaigns/`, { name: campaignName, parent_campaign_id: parentCampaignId,
      targets: bindings.map((binding) => ({ binding_id: binding.id, version })) });
    const credential = await this.planningCredential({ contextId, campaignId: created.id, selected, restoreLocks });
    const review = await this.batch().planAll({ campaignId: created.id, selected, exclusions, mode, name: contextName, version, client, restoreLocks, outDir });
    return { campaign_id: created.id, credential_id: credential.id, review, packet: await this.packet({ campaignId: created.id, review }) };
  }

  async planningCredential({ contextId, campaignId, selected, restoreLocks }) {
    const versions = new Set((await this.batch().targets(campaignId)).map((row) => row.desired_version_id));
    for (const row of selected) { const locked = await lockedVersion(row.project).catch(() => null); if (locked) versions.add(locked); }
    for (const path of Object.values(restoreLocks)) versions.add(JSON.parse(await readFile(path, 'utf8')).version_id);
    const credential = await this.hub('POST', `${ROOT}credentials/`, { purpose: 'consume', grants: [{ kind: 'agent_context', resource_id: contextId,
      labels: [], version_ids: [...versions].sort() }], binding_ids: [], expires_at: new Date(Date.now() + 3_600_000).toISOString() });
    this.env = { ...this.env, FI_CONTEXT_CREDENTIAL: credential.secret };
    return { id: credential.id };
  }

  /** Grouped packet bound to the campaign revision, manifest digest and every plan digest. */
  async packet({ campaignId, review }) {
    const campaign = await this.hub('GET', `${ROOT}campaigns/${campaignId}/`);
    const strip = (row) => ({ binding_id: row.binding_id, repository_id: row.repository_id, kind: row.kind, reason: row.reason });
    const held = review.exceptions.filter((row) => HELD_KINDS.includes(row.kind)).map(strip).sort(byRepository);
    const individual = review.exceptions.filter((row) => !HELD_KINDS.includes(row.kind)).map(strip).sort(byRepository);
    const groups = review.groups.map((group) => ({ key: group.key, members: [...group.members].sort(byRepository) }));
    const decisions = [...review.decisions].sort((a, b) => (a.binding_id < b.binding_id ? -1 : 1));
    return sealed({ kind: 'rollout', campaign_id: campaignId, revision: campaign.revision, manifest_digest: campaign.manifest_digest,
      target_count: campaign.target_count, groups, held, individual, decisions }, campaign.target_count);
  }

  /**
   * One approval over the reviewed packet. The packet is re-derived from the
   * current server state; a changed revision, manifest, plan or exception set
   * refuses before any authorization. The acknowledged exceptions must be
   * exactly the packet's exceptions, so none is accepted implicitly.
   */
  async authorizeReviewed({ packet, acknowledgedExceptions }) {
    const { units, packet_sha256: digest, ...body } = packet;
    need(packetDigest(body) === digest, 'review_packet_altered');
    const exceptions = [...packet.held, ...packet.individual].map((row) => row.repository_id).sort();
    need(Array.isArray(acknowledgedExceptions) && canonicalJson([...acknowledgedExceptions].sort()) === canonicalJson(exceptions), 'exceptions_not_acknowledged');
    const campaign = await this.hub('GET', `${ROOT}campaigns/${packet.campaign_id}/`);
    need(campaign.status === 'draft' && campaign.revision === packet.revision && campaign.manifest_digest === packet.manifest_digest, 'review_stale');
    const targets = await this.batch().targets(packet.campaign_id);
    const decided = new Map(packet.decisions.map((row) => [row.binding_id, row]));
    need(targets.length === packet.target_count && targets.every((row) => decided.get(row.binding_id)?.plan_digest === (row.plan_digest ?? null)), 'review_stale');
    await this.hub('POST', `${ROOT}campaigns/${packet.campaign_id}/authorize/`, { revision: campaign.revision,
      expected_manifest_digest: campaign.manifest_digest, decisions: packet.decisions, acknowledge_repository_writes: true });
    return this.hub('GET', `${ROOT}campaigns/${packet.campaign_id}/`);
  }

  /** Pause, resume or cancel at the current revision with an explicit reason. */
  async setStatus({ campaignId, action, reason }) {
    need(['pause', 'resume', 'cancel'].includes(action) && typeof reason === 'string' && reason.trim(), 'invalid_status_change');
    const current = await this.hub('GET', `${ROOT}campaigns/${campaignId}/`);
    await this.hub('POST', `${ROOT}campaigns/${campaignId}/${action}/`, { revision: current.revision, reason });
    return this.hub('GET', `${ROOT}campaigns/${campaignId}/`);
  }

  /**
   * Recovery decision after reviewed ancestry was invalidated: cancel the stale
   * campaign (its uncertain receipts are retained) and plan a replacement linked
   * to it over the same fixed bindings. The replacement must be reviewed and
   * authorized again; nothing from the stale authorization carries over.
   */
  async replace({ campaignId, reason, ...request }) {
    const cancelled = await this.setStatus({ campaignId, action: 'cancel', reason });
    need(cancelled.status === 'cancelled', 'stale_campaign_not_cancelled');
    return { cancelled, ...(await this.start({ ...request, parentCampaignId: campaignId })) };
  }

  /**
   * After an explicit merge decision: record each merged target from the Git
   * host, then apply the merged plan to its checkout and verify the lock. Merge
   * and installation receipts stay separate; nothing here reports delivery.
   */
  async install({ campaignId, targets }) {
    const merged = await this.batch().executeAll({ campaignId, targets: targets.map((row) => ({ bindingId: row.binding_id,
      repository: row.repository, contents: row.contents, idempotencyKey: row.idempotency_key })) });
    const installable = targets.filter((row, index) => merged[index].state === 'merged' && merged[index].delivered === false);
    const installed = installable.length ? await this.batch().applyAll({ plans: installable.map((row) => ({ repository_id: row.repository_id, path: row.plan_path, project: row.project })) }) : [];
    return { merged, installed };
  }

  /** One load check over every installed checkout; each target gets its own model-boundary receipt. */
  async loadCheck({ host, targets, probe = probeCodexLoad }) {
    need(host?.executable && host.sha256, 'native_host_pin_required');
    const receipts = [];
    for (const row of targets) {
      this.signal?.throwIfAborted();
      try {
        receipts.push({ repository_id: row.repository_id, ...(await probe({ executable: host.executable, executableSha256: host.sha256,
          expectedVersion: host.version ?? null, project: row.project, expectedInstructions: row.expected_instructions, signal: this.signal })) });
      } catch (error) {
        if (this.signal?.aborted) throw error;
        receipts.push({ repository_id: row.repository_id, source: 'model_boundary', discovery: 'not_observed', delivery: 'not_observed', error: error.message });
      }
    }
    return receipts;
  }

  // ---- BEGIN one-step rollout paths (round 6) ----------------------------------
  // Each path is one operator command or decision over the whole set. Review
  // authority, packet binding, explicit exception acknowledgement and separate
  // merge/install/load receipts are unchanged; only mechanical follow-on
  // commands are carried out by the same step.

  /** Inventory the selected checkouts (no hooks are executed) and return the grouped ownership packet in one command. */
  async inventoryOwnership({ manifestPath }) {
    need(typeof this.python === 'string' && this.python && typeof manifestPath === 'string' && manifestPath, 'inventory_input_required');
    let stdout; let exitCode = 0;
    try {
      ({ stdout } = await execute(this.python, ['-I', '-m', 'fi.context', 'inventory', '--repositories', manifestPath],
        { signal: this.signal, timeout: 60_000, maxBuffer: 8 * 1024 * 1024 }));
    } catch (error) {
      // Exit 6 is the SDK's explicit incomplete/unavailable result: retained, never treated as success.
      if (error.code !== 6 || typeof error.stdout !== 'string') throw error;
      stdout = error.stdout; exitCode = 6;
    }
    const inventory = JSON.parse(stdout);
    return { exitCode, inventory, packet: ownershipPacket(inventory) };
  }

  /**
   * Approve exactly the reviewed candidate and, in the same step, freeze every
   * binding at the new release and plan all targets. The returned rollout packet
   * still needs its own review and authorization; nothing is written to Git.
   */
  async publishAndStart({ review, baseVersion, note = '', start }) {
    need(start && typeof start === 'object', 'invalid_rollout_request');
    const version = await this.publishReviewed({ review, baseVersion, note });
    return { version, started: await this.start({ ...start, version: version.number }) };
  }

  /**
   * One approval over the reviewed packet (authorizeReviewed's checks, unchanged),
   * after which exactly the included, authorized targets get their deterministic
   * PRs. A moved base stops the batch before any further write.
   */
  async authorizeAndOpen({ packet, acknowledgedExceptions, targets }) {
    const included = new Set((packet?.decisions ?? []).filter((row) => row.decision === 'include').map((row) => row.binding_id));
    need(Array.isArray(targets) && targets.length === included.size && new Set(targets.map((row) => row.bindingId)).size === targets.length
      && targets.every((row) => included.has(row.bindingId)), 'targets_not_authorized_set');
    const campaign = await this.authorizeReviewed({ packet, acknowledgedExceptions });
    return { campaign, receipts: await this.batch().executeAll({ campaignId: packet.campaign_id, targets }) };
  }

  /**
   * One merge decision for one reviewed unit: each PR is re-verified and merged
   * guarded by its authorized head; only what merged is recorded and installed,
   * only verified installations are load-checked (with a pinned host). Merge,
   * installation and load receipts stay separate and per target.
   */
  async mergeReviewed({ campaignId, targets, host = null }) {
    need(Array.isArray(targets) && targets.length > 0, 'invalid_targets');
    const merges = await this.batch().mergeAll({ targets: targets.map((row) => ({ repository_id: row.repository_id, repository: row.repository,
      pull_request_number: row.pull_request_number, commit: row.commit, plan: row.plan, contents: row.contents })) });
    const merged = new Set(merges.filter((row) => row.state === 'merged' && row.merged === true).map((row) => row.repository_id));
    const installable = targets.filter((row) => merged.has(row.repository_id));
    const settled = installable.length ? await this.install({ campaignId, targets: installable }) : { merged: [], installed: [] };
    const verified = new Set(settled.installed.filter((row) => row.state === 'installed' && row.verified === true).map((row) => row.repository_id));
    const loads = host ? await this.loadCheck({ host, targets: installable.filter((row) => verified.has(row.repository_id))
      .map((row) => ({ repository_id: row.repository_id, project: row.project, expected_instructions: row.expected_instructions })) }) : [];
    return { merges, recorded: settled.merged, installed: settled.installed, loads };
  }

  /**
   * Recovery decision after an unknown Git result: pause, look the target up
   * again (the deterministic branch and tree are reconciled, never written
   * twice) and resume only when its exact PR is observed. Anything else leaves
   * the campaign paused with the observation for a further decision.
   */
  async reconcileAndResume({ campaignId, target, reason }) {
    need(target && typeof reason === 'string' && reason.trim(), 'invalid_reconcile_request');
    await this.setStatus({ campaignId, action: 'pause', reason: 'Inspect uncertain Git result before retry.' });
    const [observed] = await this.batch().executeAll({ campaignId, targets: [target] });
    if (observed.state !== 'pr_open') return { observed, campaign: await this.hub('GET', `${ROOT}campaigns/${campaignId}/`) };
    return { observed, campaign: await this.setStatus({ campaignId, action: 'resume', reason }) };
  }

  /**
   * Follow-on of the operator's recovery decision on surfaced local conflicts:
   * re-plan exactly those targets in the same draft campaign and return the
   * complete review and a new packet over all targets. Only targets the plan
   * itself reported as local_conflict qualify; every other recorded plan and
   * decision is kept as reviewed. Nothing is authorized or written to Git.
   */
  async replanResolved({ campaignId, review, repositoryIds, request }) {
    need(Array.isArray(repositoryIds) && repositoryIds.length > 0 && new Set(repositoryIds).size === repositoryIds.length, 'invalid_replan_request');
    const conflicts = new Set(review.exceptions.filter((row) => row.kind === 'local_conflict').map((row) => row.repository_id));
    need(repositoryIds.every((id) => conflicts.has(id)), 'not_a_surfaced_local_conflict');
    const { contextName, selected, exclusions = {}, mode = 'plan', version, client, restoreLocks = {}, outDir } = request;
    const fresh = await this.batch().planAll({ campaignId, selected, exclusions, mode, name: contextName, version, client, restoreLocks, outDir, only: repositoryIds });
    const replanned = new Set(fresh.decisions.map((row) => row.binding_id));
    const groups = new Map(review.groups.map((group) => [group.key, { ...group, members: [...group.members] }]));
    for (const group of fresh.groups) {
      const current = groups.get(group.key) ?? { key: group.key, delta: group.delta, members: [] };
      current.members.push(...group.members); groups.set(group.key, current);
    }
    const merged = { ...review, revision: fresh.revision, groups: [...groups.values()],
      exceptions: [...review.exceptions.filter((row) => !replanned.has(row.binding_id)), ...fresh.exceptions],
      decisions: [...review.decisions.filter((row) => !replanned.has(row.binding_id)), ...fresh.decisions],
      patches: new Map([...review.patches, ...fresh.patches]) };
    need(merged.decisions.length === review.decisions.length, 'incomplete_decisions');
    return { review: merged, packet: await this.packet({ campaignId, review: merged }) };
  }
  // ---- END one-step rollout paths ----------------------------------------------
}
