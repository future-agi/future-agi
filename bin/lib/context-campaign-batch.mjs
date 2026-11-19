import { execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { promisify } from 'node:util';
import { join } from 'node:path';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const run = promisify(execFile);
const exporter = fileURLToPath(new URL('./context-campaign-export.py', import.meta.url));
const requireValue = (value, code) => { if (!value) throw new Error(code); };
const ROOT = '/model-hub/skills/contexts/';
const LOCK = '.futureagi-context.lock.json';
const INSTRUCTIONS = ['AGENTS.md', 'CLAUDE.md', 'CLAUDE.local.md', 'nested/AGENTS.md', 'AGENTS.override.md'];

/** Expose an excluded checkout's lock and local-instruction state for review, read-only. */
export async function observeLocal(project) {
  const read = async (path) => {
    try { return await readFile(join(project, path)); } catch (error) { if (error.code === 'ENOENT') return null; throw error; }
  };
  const lock = await read(LOCK);
  const instructions = {};
  for (const path of INSTRUCTIONS) {
    const bytes = await read(path);
    if (bytes !== null) instructions[path] = createHash('sha256').update(bytes).digest('hex');
  }
  return { lock: lock === null ? 'absent' : 'present', local_instructions: instructions };
}

/** The release recorded by a checkout's lock, or null when it has none. */
export async function lockedVersion(project) {
  let bytes;
  try { bytes = await readFile(join(project, LOCK), 'utf8'); } catch (error) { if (error.code === 'ENOENT') return null; throw error; }
  const versionId = JSON.parse(bytes)?.version_id;
  requireValue(typeof versionId === 'string' && versionId.length > 0, 'invalid_local_lock');
  return versionId;
}

/**
 * Campaign-level public commands. Each method is one operator command over the
 * whole fixed campaign; per-target work stays individually checked and keyed.
 * Nothing here authorizes or reports delivery. Merging is only the explicit
 * mergeAll decision over PRs that verifyPulls has just proven exact. The caller
 * supplies a human-bound hub transport and the executor that owns the fixed Git host.
 */
export class CampaignBatch {
  constructor({ hub, executor, python, env = process.env, signal }) {
    requireValue(typeof hub === 'function' && executor, 'batch_transport_required');
    this.hub = hub; this.executor = executor; this.python = python; this.env = env; this.signal = signal;
  }

  async sdk(args, expected = [0]) {
    requireValue(this.python, 'installed_sdk_required');
    let stdout; let code = 0;
    try {
      ({ stdout } = await run(this.python, ['-I', '-m', 'fi.context', ...args],
        { env: this.env, signal: this.signal, timeout: 60_000, maxBuffer: 8 * 1024 * 1024 }));
    } catch (error) {
      if (typeof error.code !== 'number' || typeof error.stdout !== 'string') throw new Error('sdk_process_did_not_complete');
      code = error.code; stdout = error.stdout;
    }
    requireValue(expected.includes(code), 'sdk_unexpected_exit');
    return { code, value: JSON.parse(stdout) };
  }

  async targets(campaignId) {
    const rows = [];
    for (let page = 1; page <= 100; page++) {
      const response = await this.hub('GET', `${ROOT}campaigns/${campaignId}/targets/?page=${page}`);
      requireValue(Array.isArray(response?.results) && Number.isInteger(response.total_pages) && response.total_pages <= 100, 'invalid_target_page');
      rows.push(...response.results);
      if (page >= response.total_pages) return rows;
    }
    throw new Error('incomplete_target_pagination');
  }

  /** One command binds every selected repository; pins are explicit selectors. */
  async bindAll({ contextId, rows }) {
    requireValue(Array.isArray(rows) && rows.length > 0 && rows.length <= 100, 'invalid_binding_rows');
    requireValue(new Set(rows.map((row) => row.repository_id)).size === rows.length, 'duplicate_repository');
    const bindings = [];
    for (const row of rows) {
      bindings.push(await this.hub('POST', `${ROOT}${contextId}/bindings/`, { name: row.name, kind: 'repository',
        selector: row.selector, update_mode: 'repository_lock', profile: row.profile, profile_version: '1',
        repository: { provider: 'github', repository_id: row.repository_id, default_branch: 'main', project_scope: '' },
        expected_report_interval_seconds: 3600 }));
    }
    return bindings;
  }

  /**
   * Plan, export and record every fixed target. Base commits are read from Git,
   * not caller bookkeeping. Identical managed deltas whose local remainder the
   * SDK exporter proved unchanged share one review group. Pins, unavailable
   * repositories, operator exclusions and unprovable plans stay exceptions.
   */
  async planAll({ campaignId, selected, exclusions = {}, mode = 'plan', name, version, client, restoreLocks = {}, outDir, only = null }) {
    requireValue(['plan', 'restore'].includes(mode) && outDir, 'invalid_plan_request');
    // `only`: re-plan exactly these fixed targets (after a resolved local conflict); the rest keep their recorded plans.
    requireValue(only === null || (Array.isArray(only) && only.length > 0 && new Set(only).size === only.length), 'invalid_plan_subset');
    const byId = new Map(selected.map((row) => [row.repository_id, row]));
    requireValue(byId.size === selected.length, 'duplicate_selection');
    const campaign = await this.hub('GET', `${ROOT}campaigns/${campaignId}/`);
    requireValue(campaign.status === 'draft', 'campaign_not_draft');
    let revision = campaign.revision;
    const targets = await this.targets(campaignId);
    requireValue(targets.length === campaign.target_count, 'incomplete_target_set');
    const groups = new Map(); const exceptions = []; const decisions = []; const patches = new Map();
    const wanted = only === null ? targets : targets.filter((row) => only.includes(row.repository.repository_id));
    requireValue(only === null || wanted.length === only.length, 'plan_subset_not_fixed_targets');
    for (const target of wanted) {
      this.signal?.throwIfAborted();
      const repositoryId = target.repository.repository_id;
      const row = byId.get(repositoryId);
      requireValue(row, 'target_not_selected');
      const exclude = (kind, reason, observed = {}) => {
        exceptions.push({ binding_id: target.binding_id, repository_id: repositoryId, kind, reason, observed });
        decisions.push({ binding_id: target.binding_id, plan_digest: target.plan_digest ?? null, decision: 'exclude', reason });
      };
      if (target.selector && Object.hasOwn(target.selector, 'version')) {
        // A deliberate pin never advances. At its own pinned release it is
        // adopted like any target until its checkout's lock records that
        // release; afterwards it needs no write and stays an explicit exception.
        if (target.selector.version !== target.desired_version) {
          exclude('pinned_selector', `Deliberate pin to version ${target.selector.version}; no write authorized.`);
          continue;
        }
        const installed = await lockedVersion(row.project);
        if (installed !== null && installed === target.desired_version_id) {
          exclude('pinned_current', `Deliberate pin already installed at version ${target.selector.version}; no write needed.`,
            { lock_version_id: installed });
          continue;
        }
      }
      if (Object.hasOwn(exclusions, repositoryId)) {
        exclude('operator_exclusion', exclusions[repositoryId], await observeLocal(row.project));
        continue;
      }
      const gitRoot = `https://api.github.com/repos/${row.repository}`;
      let base;
      try {
        const metadata = await this.executor.request(gitRoot, { github: true, signal: this.signal });
        requireValue(String(metadata.id) === repositoryId, 'repository_identity_mismatch');
        base = (await this.executor.request(`${gitRoot}/commits/${encodeURIComponent(metadata.default_branch)}`, { github: true, signal: this.signal })).sha;
      } catch (error) {
        if (error.status !== 404) throw error;
        exclude('unavailable', 'Repository unavailable to the configured Git authorization; retained as an explicit exception.', { git_status: 404 });
        continue;
      }
      const path = join(outDir, `${mode}-${campaignId}-${repositoryId}${only === null ? '' : `-replan-${revision}`}.json`);
      let planned;
      if (mode === 'restore') {
        requireValue(restoreLocks[repositoryId], 'restore_lock_required');
        planned = await this.sdk(['restore', '--project', row.project, '--lock', restoreLocks[repositoryId], '--allow-preview', '--out', path], [0, 5]);
      } else {
        planned = await this.sdk(['plan', '--name', name, '--version', String(version), '--client', client, '--project', row.project,
          '--adopt-local', '--allow-preview', '--out', path], [0, 5]);
      }
      if (planned.code !== 0) {
        // A checkout whose managed content was edited locally is refused by the
        // SDK before any plan exists. It becomes its own reviewed exception (the
        // rest of the campaign is still planned); nothing is written or merged.
        requireValue(planned.value?.error?.code === 'local_modifications', 'sdk_unexpected_exit');
        exclude('local_conflict', 'Managed content was edited locally; resolve it (keep a copy of the local edit) before this repository is planned.',
          { error: 'local_modifications', plan_written: false });
        continue;
      }
      const inspected = (await this.sdk(['inspect', '--plan', path, '--diff'])).value;
      const { stdout } = await run(this.python, ['-I', exporter, path, repositoryId, base, String(target.binding_revision), String(revision)],
        { env: this.env, signal: this.signal, timeout: 65_000, maxBuffer: 16 * 1024 * 1024 });
      const exported = JSON.parse(stdout);
      requireValue(exported.plan?.desired_version_id === target.desired_version_id, 'plan_version_mismatch');
      const recorded = await this.hub('PUT', `${ROOT}campaigns/${campaignId}/targets/${target.binding_id}/plan/`, exported.plan);
      requireValue(recorded.plan_digest === exported.plan.plan_digest, 'plan_record_mismatch');
      revision = recorded.revision;
      patches.set(target.binding_id, { repository_id: repositoryId, repository: row.repository, project: row.project,
        path, plan: exported.plan, contents: exported.contents, inspected });
      const reason = exported.plan.local_review_required
        ? 'Reviewed managed block; every preexisting local byte proven preserved.' : 'Reviewed exact managed change.';
      decisions.push({ binding_id: target.binding_id, plan_digest: exported.plan.plan_digest, decision: 'include', reason });
      if (exported.review_group) {
        const group = groups.get(exported.review_group.key) ?? { key: exported.review_group.key, delta: exported.review_group.delta, members: [] };
        group.members.push({ binding_id: target.binding_id, repository_id: repositoryId, plan_digest: exported.plan.plan_digest });
        groups.set(group.key, group);
      } else {
        exceptions.push({ binding_id: target.binding_id, repository_id: repositoryId, kind: 'ungrouped',
          reason: exported.review_exception ?? 'unproven_delta', observed: { plan_digest: exported.plan.plan_digest } });
      }
    }
    requireValue(decisions.length === wanted.length, 'incomplete_decisions');
    return { campaign_id: campaignId, revision, target_count: targets.length,
      groups: [...groups.values()], exceptions, decisions, patches };
  }

  /**
   * Execute exact authorized targets. A moved base stops the batch before any
   * further write: the whole campaign must be reviewed again, never partially
   * retried. Every target keeps its own idempotency key for transport retries.
   */
  async executeAll({ campaignId, targets }) {
    requireValue(Array.isArray(targets) && targets.length > 0 && targets.length <= 100, 'invalid_targets');
    const receipts = []; let stopped = null;
    for (const target of targets) {
      if (stopped) { receipts.push({ bindingId: target.bindingId, state: 'not_attempted', reason: stopped }); continue; }
      let receipt;
      try {
        receipt = await this.executor.execute({ campaignId, bindingId: target.bindingId, repository: target.repository,
          contents: target.contents, idempotencyKey: target.idempotencyKey ?? randomUUID(), signal: this.signal });
      } catch {
        receipt = { state: 'unknown', reason: 'Target did not finish. Retain its idempotency key and inspect server/Git observations before retry.' };
      }
      receipts.push({ bindingId: target.bindingId, ...receipt });
      if (/Base moved/.test(receipt.reason ?? '')) stopped = 'Stopped after base movement; review a replacement campaign before further writes.';
    }
    return receipts;
  }

  /**
   * Independently re-verify one review unit's open PRs before a merge decision:
   * each is open and unmerged, its head is exactly the commit the authorized
   * attempt recorded, its only parent is the authorized base and its tree is the
   * base tree plus exactly the reviewed bytes. Any mismatch refuses the unit.
   * With acceptMerged (merge-reviewed only), a PR that is already merged with
   * exactly the verified head, and whose merge commit has that head as a parent,
   * is reconciled as merged instead of refusing the unit: a rerun after an
   * interrupted or unknown merge keeps earlier results rather than re-deciding.
   */
  async verifyPulls({ targets, acceptMerged = false }) {
    requireValue(Array.isArray(targets) && targets.length > 0 && targets.length <= 100, 'invalid_targets');
    requireValue(new Set(targets.map((row) => row.repository_id)).size === targets.length, 'duplicate_repository');
    const verified = [];
    for (const target of targets) {
      this.signal?.throwIfAborted();
      const { repository, pull_request_number: number, commit, plan, contents } = target;
      requireValue(/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository) && Number.isInteger(number) && number > 0
        && /^[0-9a-f]{40}$/.test(commit) && /^[0-9a-f]{40}$/.test(plan?.base_commit) && Array.isArray(plan.changes), 'invalid_merge_target');
      const git = (suffix) => this.executor.request(`https://api.github.com/repos/${repository}${suffix}`, { github: true, signal: this.signal });
      const pr = await git(`/pulls/${number}`);
      const alreadyMerged = acceptMerged && pr.number === number && pr.merged === true && /^[0-9a-f]{40}$/.test(pr.merge_commit_sha ?? '');
      requireValue(alreadyMerged || (pr.number === number && pr.state === 'open' && pr.merged === false), 'pull_request_not_open');
      requireValue(pr.head?.sha === commit, 'pull_request_head_changed');
      const patch = plan.changes.map((change) => {
        if (change.action === 'remove') return { path: change.path, content: null };
        const bytes = Buffer.from(contents?.[change.path] ?? '', 'base64');
        requireValue(createHash('sha256').update(bytes).digest('hex') === change.after_sha256, 'patch_hash_mismatch');
        return { path: change.path, content: bytes.toString('utf8') };
      });
      const base = await git(`/git/commits/${plan.base_commit}`);
      requireValue(base.sha === plan.base_commit, 'invalid_base_commit');
      const baseTree = await git(`/git/trees/${base.tree.sha}?recursive=1`);
      requireValue(baseTree.truncated === false && Array.isArray(baseTree.tree), 'incomplete_base_tree');
      await this.executor.verifyCommit(git, commit, plan.base_commit, baseTree, patch);
      const row = { repository_id: target.repository_id, repository, pull_request_number: number, head: commit, base: plan.base_commit };
      if (alreadyMerged) {
        const merge = await git(`/git/commits/${pr.merge_commit_sha}`);
        requireValue(merge.sha === pr.merge_commit_sha && pr.merge_commit_sha !== commit
          && Array.isArray(merge.parents) && merge.parents.some((parent) => parent.sha === commit), 'merge_commit_unverified');
        Object.assign(row, { already_merged: true, merge_commit: pr.merge_commit_sha });
      }
      verified.push(row);
    }
    return verified;
  }

  /**
   * One explicit merge decision for one verified review unit. The whole unit is
   * verified again first; each merge is guarded by its verified head SHA, so a
   * PR that changes after review is refused by the Git host, never merged.
   * Every target gets its own receipt: merged (newly, or reconciled because it
   * was already merged at the verified head), refused by the host, or unknown.
   * A failed or unconfirmed merge never stops the remaining verified targets and
   * is never reported as merged; rerunning the same unit reconciles it.
   */
  async mergeAll({ targets }) {
    const verified = await this.verifyPulls({ targets, acceptMerged: true });
    const receipts = [];
    for (const row of verified) {
      this.signal?.throwIfAborted();
      if (row.already_merged) {
        receipts.push({ ...row, state: 'merged', merged: true, reconciled: true });
        continue;
      }
      let result;
      try {
        result = await this.executor.request(`https://api.github.com/repos/${row.repository}/pulls/${row.pull_request_number}/merge`,
          { method: 'PUT', body: { sha: row.head, merge_method: 'merge' }, github: true, signal: this.signal });
      } catch (error) {
        if (this.signal?.aborted) throw error;
        const refused = [405, 409, 422].includes(error.status);
        receipts.push({ ...row, state: refused ? 'refused' : 'unknown', merged: false,
          reason: refused ? 'The Git host refused the head-guarded merge; review the pull request again before any new decision.'
            : 'The merge response was not received; rerun merge-reviewed for this unit to reconcile from the Git host before any other action.' });
        continue;
      }
      if (result?.merged === true && /^[0-9a-f]{40}$/.test(result.sha ?? '') && result.sha !== row.head) {
        receipts.push({ ...row, state: 'merged', merged: true, merge_commit: result.sha });
      } else {
        receipts.push({ ...row, state: 'unknown', merged: false,
          reason: 'The merge was not confirmed; rerun merge-reviewed for this unit to reconcile from the Git host before any other action.' });
      }
    }
    return receipts;
  }

  /** Apply reviewed merged plans to selected checkouts and verify each lock. */
  async applyAll({ plans }) {
    const results = [];
    for (const row of plans) {
      this.signal?.throwIfAborted();
      const installed = (await this.sdk(['apply', '--plan', row.path, '--quiescent'])).value;
      const verified = (await this.sdk(['verify', '--project', row.project])).value;
      results.push({ repository_id: row.repository_id, state: installed.state, delivery: installed.delivery, verified: verified.verified });
    }
    return results;
  }
}
