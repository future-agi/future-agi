import test from 'node:test';
import assert from 'node:assert/strict';
import { chmod, mkdtemp, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { CampaignBatch } from '../../../bin/lib/context-campaign-batch.mjs';
import { reviewUnits } from '../../benchmarks/context-hub/drivers/j5.mjs';

const campaignId = '00000000-0000-4000-8000-000000000001';
const desired = '00000000-0000-4000-8000-0000000000aa';
const target = (n, extra = {}) => ({ binding_id: `00000000-0000-4000-8000-00000000010${n}`, binding_revision: 1,
  desired_version_id: desired, selector: { label: 'production' }, plan_digest: null, repository: { repository_id: String(1000 + n) }, ...extra });

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
