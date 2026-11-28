import test from 'node:test';
import assert from 'node:assert/strict';
import { chmod, mkdir, mkdtemp, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { CampaignBatch } from '../../../bin/lib/context-campaign-batch.mjs';
import { reviewUnits } from '../../benchmarks/context-hub/drivers/j5.mjs';

const campaignId = '00000000-0000-4000-8000-000000000001';
const desired = '00000000-0000-4000-8000-0000000000aa';
const target = (n, extra = {}) => ({ binding_id: `00000000-0000-4000-8000-00000000010${n}`, binding_revision: 1,
  desired_version: 2, desired_version_id: desired, selector: { label: 'production' }, plan_digest: null, repository: { repository_id: String(1000 + n) }, ...extra });

// Stub SDK interpreter: plan/inspect succeed; the exporter's group is chosen per repository.
async function stubPython(groups) {
  const dir = await mkdtemp(join(tmpdir(), 'j5-batch-'));
  const path = join(dir, 'python');
  await writeFile(path, `#!/usr/bin/env node
const args = process.argv.slice(2);
if (args[1] === '-m') { console.log('{}'); process.exit(0); }
const repo = args[3]; const groups = ${JSON.stringify(groups)};
console.log(JSON.stringify({ plan: { desired_version_id: '${desired}', plan_digest: 'sha256:' + repo.padStart(64, '0'), local_review_required: true },
  contents: {}, review_group: groups[repo] ? { key: groups[repo], delta: {} } : null, review_exception: groups[repo] ? null : 'local_segment_changed' }));
`);
  await chmod(path, 0o755);
  return { dir, path };
}

function fakes(targets) {
  const recorded = [];
  const hub = async (method, path, body) => {
    if (method === 'GET' && path.endsWith(`${campaignId}/`)) return { status: 'draft', revision: 1, target_count: targets.length };
    if (method === 'GET') return { results: targets, total_pages: 1 };
    if (method === 'PUT') { recorded.push(path); return { revision: 1 + recorded.length, plan_digest: body.plan_digest }; }
    throw new Error('unexpected');
  };
  const executor = { request: async (url) => {
    const id = url.includes('repo-4') ? null : url.split('/repos/synthetic/repo-')[1]?.split('/')[0];
    if (!id) { const error = new Error('git_http_404'); error.status = 404; throw error; }
    return url.includes('/commits/') ? { sha: 'a'.repeat(40) } : { id: Number(`100${id}`), default_branch: 'main' };
  } };
  return { hub, executor, recorded };
}

test('planAll groups only proven-identical deltas and keeps every exception individually', async () => {
  const targets = [target(1), target(2), target(3, { selector: { version: 1 } }), target(4), target(5), target(6)];
  const { hub, executor, recorded } = fakes(targets);
  const python = await stubPython({ 1001: 'sha256:same', 1002: 'sha256:same', 1006: null });
  const batch = new CampaignBatch({ hub, executor, python: python.path });
  const selected = targets.map((row, i) => ({ repository_id: row.repository.repository_id, repository: `synthetic/repo-${i + 1}`, project: join(python.dir, `p${i}`) }));
  const review = await batch.planAll({ campaignId, selected, exclusions: { 1005: 'Locally modified.' }, name: 'n', version: 2, client: 'codex', outDir: python.dir });
  assert.deepEqual(review.groups.map((group) => [group.key, group.members.map((row) => row.repository_id)]), [['sha256:same', ['1001', '1002']]]);
  assert.deepEqual(review.exceptions.map((row) => [row.repository_id, row.kind]).sort(),
    [['1003', 'pinned_selector'], ['1004', 'unavailable'], ['1005', 'operator_exclusion'], ['1006', 'ungrouped']]);
  assert.equal(review.exceptions.find((row) => row.kind === 'operator_exclusion').observed.lock, 'absent');
  assert.equal(review.decisions.length, 6);
  assert.deepEqual(review.decisions.filter((row) => row.decision === 'include').map((row) => row.binding_id).sort(),
    [targets[0], targets[1], targets[5]].map((row) => row.binding_id).sort());
  assert.equal(recorded.length, 3, 'only planned targets upload plans');
  // Units over the same review expose each repository exactly once.
  const repos = targets.map((row) => ({ id: row.repository.repository_id }));
  assert.equal(reviewUnits(review, repos).length, 5);
  const duplicated = structuredClone(review); duplicated.exceptions.push({ ...duplicated.exceptions[0] });
  assert.throws(() => reviewUnits(duplicated, repos), /review_item_count/);
  const omitted = structuredClone(review); omitted.exceptions.pop();
  assert.throws(() => reviewUnits(omitted, repos), /review_item_count/);
});

test('a deliberate pin is adopted at its own release once, then held without a write', async () => {
  // Pin to version 2 in a version-2 campaign: no lock yet, so it is planned.
  const targets = [target(1), target(2, { selector: { version: 2 } }), target(3, { selector: { version: 2 } }), target(4, { selector: { version: 1 } })];
  const { hub, executor, recorded } = fakes(targets);
  const python = await stubPython({ 1001: 'sha256:same', 1002: 'sha256:same', 1003: 'sha256:same' });
  const projects = targets.map((_, i) => join(python.dir, `pin-${i}`));
  // Repository 1003's checkout already records the pinned release.
  await mkdir(projects[2], { recursive: true });
  await writeFile(join(projects[2], '.futureagi-context.lock.json'), JSON.stringify({ version_id: desired }));
  // Repository 1004 has a lock at a different release; it is pinned elsewhere, so never written.
  await mkdir(projects[3], { recursive: true });
  await writeFile(join(projects[3], '.futureagi-context.lock.json'), JSON.stringify({ version_id: 'other' }));
  const batch = new CampaignBatch({ hub, executor, python: python.path });
  const selected = targets.map((row, i) => ({ repository_id: row.repository.repository_id, repository: `synthetic/repo-${i + 1}`, project: projects[i] }));
  const review = await batch.planAll({ campaignId, selected, name: 'n', version: 2, client: 'codex', outDir: python.dir });
  assert.deepEqual(review.groups.map((group) => group.members.map((row) => row.repository_id)), [['1001', '1002']]);
  assert.deepEqual(review.exceptions.map((row) => [row.repository_id, row.kind]), [['1003', 'pinned_current'], ['1004', 'pinned_selector']]);
  assert.equal(review.exceptions[0].observed.lock_version_id, desired);
  assert.equal(recorded.length, 2, 'held pins upload no plan');
  // A corrupt lock is refused rather than treated as absent.
  await writeFile(join(projects[2], '.futureagi-context.lock.json'), JSON.stringify({ schema: 1 }));
  await assert.rejects(batch.planAll({ campaignId, selected, name: 'n', version: 2, client: 'codex', outDir: python.dir }), /invalid_local_lock/);
});

function pullFakes({ head = 'c'.repeat(40), merged = null, mergeParents = null, mergeFailure = null } = {}) {
  const bytes = 'Managed v2\n';
  const blob = (text) => createHash('sha1').update(`blob ${Buffer.byteLength(text)}\0`).update(text).digest('hex');
  const plan = { base_commit: 'b'.repeat(40), changes: [{ path: 'AGENTS.md', action: 'replace', before_sha256: 'e'.repeat(64),
    after_sha256: createHash('sha256').update(bytes).digest('hex') }] };
  const merges = [];
  const executor = {
    request: async (url, options = {}) => {
      const suffix = url.split('/repos/synthetic/r1')[1];
      if (suffix === '/pulls/7') return merged ? { number: 7, state: 'closed', merged: true, merge_commit_sha: merged, head: { sha: head } }
        : { number: 7, state: 'open', merged: false, head: { sha: head } };
      if (suffix === `/git/commits/${'b'.repeat(40)}`) return { sha: 'b'.repeat(40), tree: { sha: 't'.repeat(40) } };
      if (merged && suffix === `/git/commits/${merged}`) return { sha: merged, parents: mergeParents ?? [{ sha: 'b'.repeat(40) }, { sha: head }] };
      if (suffix.startsWith('/git/trees/')) return { truncated: false, tree: [{ path: 'AGENTS.md', type: 'blob', mode: '100644', sha: blob('old') }] };
      if (suffix === '/pulls/7/merge' && options.method === 'PUT') {
        merges.push(options.body);
        if (mergeFailure) { const error = new Error(`git_http_${mergeFailure}`); if (mergeFailure !== 'network') error.status = mergeFailure; throw error; }
        return { merged: true, sha: 'd'.repeat(40) };
      }
      throw new Error(`unexpected ${suffix}`);
    },
    verifyCommit: async (_git, commit, base, baseTree, patch) => {
      assert.equal(base, 'b'.repeat(40)); assert.equal(baseTree.tree.length, 1);
      if (commit !== 'c'.repeat(40) || patch[0].content !== bytes) throw new Error('unauthorized_tree_change');
    },
  };
  const targets = [{ repository_id: '1001', repository: 'synthetic/r1', pull_request_number: 7, commit: 'c'.repeat(40), plan,
    contents: { 'AGENTS.md': Buffer.from(bytes).toString('base64') } }];
  return { executor, targets, merges };
}

test('mergeAll merges a verified unit, each guarded by its verified head', async () => {
  const { executor, targets, merges } = pullFakes();
  const batch = new CampaignBatch({ hub: async () => null, executor });
  const merged = await batch.mergeAll({ targets });
  assert.deepEqual(merged.map((row) => [row.repository_id, row.state, row.merge_commit]), [['1001', 'merged', 'd'.repeat(40)]]);
  assert.deepEqual(merges, [{ sha: 'c'.repeat(40), merge_method: 'merge' }]);
});

test('mergeAll returns a retained per-target receipt when the host refuses or the response is lost', async () => {
  for (const [failure, state] of [[409, 'refused'], ['network', 'unknown']]) {
    const { executor, targets, merges } = pullFakes({ mergeFailure: failure });
    const receipts = await new CampaignBatch({ hub: async () => null, executor }).mergeAll({ targets });
    assert.deepEqual(receipts.map((row) => [row.repository_id, row.state, row.merged, row.merge_commit]), [['1001', state, false, undefined]]);
    assert.match(receipts[0].reason, state === 'refused' ? /review the pull request again/ : /rerun merge-reviewed/);
    assert.equal(merges.length, 1, 'exactly one guarded attempt, never a blind retry');
  }
});

test('a rerun reconciles a PR already merged at the verified head without another merge write', async () => {
  const { executor, targets, merges } = pullFakes({ merged: 'd'.repeat(40) });
  const receipts = await new CampaignBatch({ hub: async () => null, executor }).mergeAll({ targets });
  assert.deepEqual(receipts.map((row) => [row.state, row.reconciled, row.merge_commit]), [['merged', true, 'd'.repeat(40)]]);
  assert.equal(merges.length, 0);
  // verify-prs alone (no merge decision) still requires an open PR.
  await assert.rejects(new CampaignBatch({ hub: async () => null, executor }).verifyPulls({ targets }), /pull_request_not_open/);
});

test('an already-merged PR at a different head, or whose merge commit lacks the head, refuses the unit', async () => {
  const moved = pullFakes({ merged: 'd'.repeat(40), head: 'f'.repeat(40) });
  await assert.rejects(new CampaignBatch({ hub: async () => null, executor: moved.executor }).mergeAll({ targets: moved.targets }), /pull_request_head_changed/);
  const orphan = pullFakes({ merged: 'd'.repeat(40), mergeParents: [{ sha: 'b'.repeat(40) }] });
  await assert.rejects(new CampaignBatch({ hub: async () => null, executor: orphan.executor }).mergeAll({ targets: orphan.targets }), /merge_commit_unverified/);
  assert.equal(moved.merges.length + orphan.merges.length, 0);
});

test('mergeAll refuses a unit whose PR head or reviewed bytes changed before any merge', async () => {
  const moved = pullFakes({ head: 'f'.repeat(40) });
  await assert.rejects(new CampaignBatch({ hub: async () => null, executor: moved.executor }).mergeAll({ targets: moved.targets }), /pull_request_head_changed/);
  assert.equal(moved.merges.length, 0);
  const tampered = pullFakes();
  tampered.targets[0].contents['AGENTS.md'] = Buffer.from('Other bytes\n').toString('base64');
  await assert.rejects(new CampaignBatch({ hub: async () => null, executor: tampered.executor }).mergeAll({ targets: tampered.targets }), /patch_hash_mismatch/);
  assert.equal(tampered.merges.length, 0);
});

test('planAll refuses a fixed target that was not selected', async () => {
  const targets = [target(1)];
  const { hub, executor } = fakes(targets);
  const python = await stubPython({ 1001: 'k' });
  const batch = new CampaignBatch({ hub, executor, python: python.path });
  await assert.rejects(batch.planAll({ campaignId, selected: [], outDir: python.dir, name: 'n', version: 1, client: 'codex' }), /target_not_selected/);
});

test('executeAll stops after base movement and keeps uncertain receipts uncertain', async () => {
  const calls = [];
  const executor = { execute: async ({ bindingId }) => {
    calls.push(bindingId);
    if (bindingId === 'b1') return { state: 'pr_open', reason: '' };
    if (bindingId === 'b2') throw new Error('lost');
    return { state: 'unknown', reason: 'Base moved; review a new plan before further writes.' };
  } };
  const batch = new CampaignBatch({ hub: async () => null, executor });
  const receipts = await batch.executeAll({ campaignId, targets: ['b1', 'b2', 'b3', 'b4'].map((bindingId) => ({ bindingId, repository: 'synthetic/r', contents: {} })) });
  assert.deepEqual(calls, ['b1', 'b2', 'b3']);
  assert.deepEqual(receipts.map((row) => row.state), ['pr_open', 'unknown', 'unknown', 'not_attempted']);
});

// Stub SDK whose restore refuses a checkout marked `<project>.dirty` with the given error code.
async function conflictPython() {
  const dir = await mkdtemp(join(tmpdir(), 'j5-conflict-'));
  const path = join(dir, 'python');
  await writeFile(path, `#!/usr/bin/env node
const { existsSync, readFileSync } = require('node:fs');
const args = process.argv.slice(2);
if (args[1] === '-m') {
  const project = args[args.indexOf('--project') + 1];
  if (args[3] === 'restore' && existsSync(project + '.dirty')) { console.log(JSON.stringify({ error: { code: readFileSync(project + '.dirty', 'utf8') } })); process.exit(5); }
  console.log('{}'); process.exit(0);
}
const repo = args[3];
console.log(JSON.stringify({ plan: { desired_version_id: '${desired}', plan_digest: 'sha256:' + repo.padStart(64, '0'), local_review_required: true },
  contents: {}, review_group: { key: 'sha256:reverse', delta: {} }, review_exception: null }));
`);
  await chmod(path, 0o755);
  return { dir, path };
}

test('a locally edited managed checkout becomes its own reviewed exception; the rest of the reverse plan proceeds', async () => {
  const targets = [target(1), target(2), target(3)];
  const { hub, executor, recorded } = fakes(targets);
  const python = await conflictPython();
  const selected = targets.map((row, i) => ({ repository_id: row.repository.repository_id, repository: `synthetic/repo-${i + 1}`, project: join(python.dir, `p${i}`) }));
  const restoreLocks = Object.fromEntries(selected.map((row) => [row.repository_id, join(python.dir, `${row.repository_id}-lock.json`)]));
  await writeFile(selected[0].project + '.dirty', 'local_modifications');
  const batch = new CampaignBatch({ hub, executor, python: python.path });
  const request = { campaignId, selected, mode: 'restore', restoreLocks, name: 'n', version: 1, client: 'codex', outDir: python.dir };
  const review = await batch.planAll(request);
  assert.deepEqual(review.exceptions.map((row) => [row.repository_id, row.kind, row.observed.plan_written]), [['1001', 'local_conflict', false]]);
  assert.deepEqual(review.decisions.find((row) => row.binding_id === targets[0].binding_id), { binding_id: targets[0].binding_id, plan_digest: null,
    decision: 'exclude', reason: review.exceptions[0].reason });
  assert.deepEqual(review.groups.map((group) => group.members.map((row) => row.repository_id)), [['1002', '1003']]);
  assert.equal(recorded.length, 2, 'no plan is recorded for the conflicted checkout');
  assert.ok(!review.patches.has(targets[0].binding_id));
  // Any other SDK refusal still stops the campaign plan: only a local edit is an exception.
  await writeFile(selected[0].project + '.dirty', 'invalid_lock');
  await assert.rejects(batch.planAll(request), /sdk_unexpected_exit/);
  // After the operator resolved it, exactly that target is re-planned; nothing else is re-recorded.
  await writeFile(selected[0].project + '.dirty', '');
  const { rm } = await import('node:fs/promises'); await rm(selected[0].project + '.dirty');
  const before = recorded.length;
  const fresh = await batch.planAll({ ...request, only: ['1001'] });
  assert.equal(recorded.length, before + 1);
  assert.deepEqual(fresh.decisions.map((row) => [row.binding_id, row.decision]), [[targets[0].binding_id, 'include']]);
  assert.deepEqual(fresh.groups.map((group) => group.members.map((row) => row.repository_id)), [['1001']]);
  await assert.rejects(batch.planAll({ ...request, only: ['9999'] }), /plan_subset_not_fixed_targets/);
  await assert.rejects(batch.planAll({ ...request, only: [] }), /invalid_plan_subset/);
});
