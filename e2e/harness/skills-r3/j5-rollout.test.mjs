import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { ContextRollout, ownershipPacket, packetDigest } from '../../../bin/lib/context-rollout.mjs';

const sha = (n) => String(n).repeat(64).slice(0, 64);
const inventoried = (id, files, extra = {}) => ({ repository_id: id, state: 'inventoried', ownership: 'not_authorized',
  observation: { inventory_complete: true, lock_sha256: null, findings: Object.entries(files).map(([path, sha256]) => ({ path, sha256 })) }, ...extra });

test('ownership packet groups identical shared instructions and keeps personal files listed, not hidden', () => {
  const inventory = { target_count: 5, targets: [
    inventoried('1000', { 'AGENTS.md': sha(1), 'CLAUDE.local.md': sha(5), 'nested/AGENTS.md': sha(3) }),
    inventoried('1001', { 'AGENTS.md': sha(2), 'CLAUDE.local.md': sha(6), 'nested/AGENTS.md': sha(3) }),
    inventoried('1002', { 'AGENTS.md': sha(2), 'CLAUDE.local.md': sha(7), 'nested/AGENTS.md': sha(3) }),
    { repository_id: '1003', state: 'unavailable' },
    inventoried('1004', { 'AGENTS.md': sha(2) }, { observation: { inventory_complete: true, lock_sha256: sha(9), findings: [{ path: 'AGENTS.md', sha256: sha(2) }] } }),
  ] };
  const packet = ownershipPacket(inventory);
  // Different personal CLAUDE.local.md bytes do not split a group; a different shared AGENTS.md does.
  assert.deepEqual(packet.units.map((unit) => [unit.kind, unit.repository_ids]),
    [['group', ['1000']], ['group', ['1001', '1002']], ['held', ['1003']], ['inventory_exception', ['1004']]]);
  const group = packet.groups[1];
  assert.deepEqual(group.members.map((row) => row.retained.map((file) => [file.path, file.sha256])), [[['CLAUDE.local.md', sha(6)]], [['CLAUDE.local.md', sha(7)]]]);
  assert.deepEqual(group.shared.map((file) => file.path), ['AGENTS.md', 'nested/AGENTS.md']);
  // A different set of personal files is a different proposal, so it is a different group.
  const extra = structuredClone(inventory); extra.targets[2].observation.findings.push({ path: 'AGENTS.override.md', sha256: sha(8) });
  assert.equal(ownershipPacket(extra).groups.length, 3);
  const { units, packet_sha256: digest, ...body } = packet;
  assert.equal(packetDigest(body), digest);
  assert.throws(() => ownershipPacket({ target_count: 6, targets: inventory.targets }), /inventory_incomplete/);
});

const campaignId = '00000000-0000-4000-8000-000000000001';
const binding = (n) => `00000000-0000-4000-8000-00000000010${n}`;
function fakeHub({ campaign, targets }) {
  const calls = [];
  const hub = async (method, path, body) => {
    calls.push([method, path, body]);
    if (method === 'GET' && path.endsWith(`campaigns/${campaignId}/`)) return structuredClone(campaign);
    if (method === 'GET' && path.includes('/targets/')) return { results: structuredClone(targets), total_pages: 1 };
    if (method === 'POST' && path.endsWith('/authorize/')) { campaign.status = 'authorized'; campaign.revision += 1; return {}; }
    if (method === 'POST' && path.endsWith('/credentials/')) return { id: 'credential-1', secret: 'synthetic-secret' };
    throw new Error(`unexpected ${method} ${path}`);
  };
  return { hub, calls };
}
const review = () => ({
  groups: [{ key: 'sha256:same', members: [{ binding_id: binding(1), repository_id: '1001', plan_digest: 'sha256:p1' }, { binding_id: binding(2), repository_id: '1002', plan_digest: 'sha256:p2' }] }],
  exceptions: [{ binding_id: binding(3), repository_id: '1003', kind: 'pinned_selector', reason: 'Deliberate pin to version 1; no write authorized.', observed: {} },
    { binding_id: binding(4), repository_id: '1004', kind: 'ungrouped', reason: 'local_segment_changed', observed: {} }],
  decisions: [1, 2, 3, 4].map((n) => ({ binding_id: binding(n), plan_digest: n === 3 ? null : `sha256:p${n}`, decision: n === 3 ? 'exclude' : 'include', reason: 'r' })),
});
const state = () => ({ campaign: { status: 'draft', revision: 7, manifest_digest: 'sha256:manifest', target_count: 4 },
  targets: [1, 2, 3, 4].map((n) => ({ binding_id: binding(n), plan_digest: n === 3 ? null : `sha256:p${n}`, desired_version_id: 'v2' })) });

test('one approval authorizes the reviewed packet only with every exception acknowledged', async () => {
  const world = state(); const { hub, calls } = fakeHub(world);
  const rollout = new ContextRollout({ hub, executor: {} });
  const packet = await rollout.packet({ campaignId, review: review() });
  // Held exceptions are shown together; an unproven delta stays its own unit.
  assert.deepEqual(packet.units.map((unit) => [unit.kind, unit.repository_ids]), [['group', ['1001', '1002']], ['held', ['1003']], ['ungrouped', ['1004']]]);
  await assert.rejects(rollout.authorizeReviewed({ packet, acknowledgedExceptions: ['1003'] }), /exceptions_not_acknowledged/);
  await assert.rejects(rollout.authorizeReviewed({ packet, acknowledgedExceptions: ['1003', '1004', '1001'] }), /exceptions_not_acknowledged/);
  const altered = structuredClone(packet); altered.decisions[0].decision = 'exclude';
  await assert.rejects(rollout.authorizeReviewed({ packet: altered, acknowledgedExceptions: ['1003', '1004'] }), /review_packet_altered/);
  assert.equal(calls.filter(([method, path]) => method === 'POST' && path.endsWith('/authorize/')).length, 0, 'nothing authorized on refusal');
  const authorized = await rollout.authorizeReviewed({ packet, acknowledgedExceptions: ['1004', '1003'] });
  assert.equal(authorized.status, 'authorized');
  const [, , body] = calls.find(([method, path]) => method === 'POST' && path.endsWith('/authorize/'));
  assert.deepEqual(body, { revision: 7, expected_manifest_digest: 'sha256:manifest', decisions: packet.decisions, acknowledge_repository_writes: true });
});

test('a changed revision, manifest or plan after review refuses authorization', async () => {
  for (const mutate of [(w) => { w.campaign.revision = 8; }, (w) => { w.campaign.manifest_digest = 'sha256:other'; },
    (w) => { w.targets[1].plan_digest = 'sha256:changed'; }, (w) => { w.campaign.status = 'authorized'; }]) {
    const world = state(); const { hub, calls } = fakeHub(world);
    const rollout = new ContextRollout({ hub, executor: {} });
    const packet = await rollout.packet({ campaignId, review: review() });
    mutate(world);
    await assert.rejects(rollout.authorizeReviewed({ packet, acknowledgedExceptions: ['1003', '1004'] }), /review_stale/);
    assert.equal(calls.filter(([method, path]) => method === 'POST' && path.endsWith('/authorize/')).length, 0);
  }
});

test('the planning credential covers exactly the frozen releases plus releases already locked in the checkouts', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'j5-rollout-'));
  await mkdir(join(dir, 'a')); await mkdir(join(dir, 'b'));
  await writeFile(join(dir, 'a', '.futureagi-context.lock.json'), JSON.stringify({ version_id: 'v1' }));
  await writeFile(join(dir, 'restore.json'), JSON.stringify({ version_id: 'v0' }));
  const world = state(); const { hub, calls } = fakeHub(world);
  const rollout = new ContextRollout({ hub, executor: {}, env: {} });
  const credential = await rollout.planningCredential({ contextId: 'ctx', campaignId, restoreLocks: { 1001: join(dir, 'restore.json') },
    selected: [{ project: join(dir, 'a') }, { project: join(dir, 'b') }, { project: join(dir, 'missing') }] });
  assert.equal(credential.id, 'credential-1'); assert.equal(credential.secret, undefined, 'secret never returned');
  assert.equal(rollout.env.FI_CONTEXT_CREDENTIAL, 'synthetic-secret');
  const [, , body] = calls.find(([method, path]) => method === 'POST' && path.endsWith('/credentials/'));
  assert.deepEqual(body.grants, [{ kind: 'agent_context', resource_id: 'ctx', labels: [], version_ids: ['v0', 'v1', 'v2'] }]);
  assert.equal(body.purpose, 'consume');
});

test('one load check returns a receipt per target and never turns a failed probe into delivery', async () => {
  const rollout = new ContextRollout({ hub: async () => null });
  const probe = async ({ project }) => {
    if (project === 'broken') throw new Error('native_preimage_changed');
    return { source: 'model_boundary', discovery: 'observed', delivery: 'observed', requests: [{}], process_events: [] };
  };
  const receipts = await rollout.loadCheck({ host: { executable: '/x', sha256: sha(1) }, probe,
    targets: [{ repository_id: '1', project: 'ok', expected_instructions: 'a' }, { repository_id: '2', project: 'broken', expected_instructions: 'a' }] });
  assert.deepEqual(receipts.map((row) => [row.repository_id, row.delivery]), [['1', 'observed'], ['2', 'not_observed']]);
  assert.equal(receipts[1].error, 'native_preimage_changed');
  await assert.rejects(rollout.loadCheck({ host: {}, targets: [] }), /native_host_pin_required/);
});

test('the rollout CLI prints the grouped ownership packet and reads a stored planning credential', async () => {
  const { main, credentialEnv } = await import('../../../bin/context-campaign.mjs');
  const dir = await mkdtemp(join(tmpdir(), 'j5-cli-'));
  const inventory = { target_count: 2, targets: [inventoried('1000', { 'AGENTS.md': sha(1) }), { repository_id: '1001', state: 'unavailable' }] };
  await writeFile(join(dir, 'inventory.json'), JSON.stringify(inventory));
  const printed = []; const log = console.log; console.log = (line) => printed.push(line);
  let code;
  try { code = await main(['ownership-review', join(dir, 'inventory.json')], {}); } finally { console.log = log; }
  assert.equal(code, 0);
  assert.deepEqual(JSON.parse(printed[0]).units.map((unit) => unit.kind), ['group', 'held']);
  // Wrong arity never reaches a hub call.
  await assert.rejects(main(['status', campaignId, 'pause'], {}), /invalid_arguments/);
  await assert.rejects(main(['authorize-reviewed', join(dir, 'inventory.json')], {}), /invalid_arguments/);
  await writeFile(join(dir, 'credential'), 'synthetic-secret\n', { mode: 0o600 });
  assert.equal((await credentialEnv({ CONTEXT_CREDENTIAL_FILE: join(dir, 'credential') })).FI_CONTEXT_CREDENTIAL, 'synthetic-secret');
  assert.deepEqual(await credentialEnv({ A: '1' }), { A: '1' });
  await writeFile(join(dir, 'empty'), '\n');
  await assert.rejects(credentialEnv({ CONTEXT_CREDENTIAL_FILE: join(dir, 'empty') }), /empty_credential_file/);
});

// ---- One-step rollout paths (round 6): fewer commands, identical review authority ----

test('create and edit return the candidate review packet in the same step and never publish', async () => {
  const calls = [];
  const hub = async (method, path, body) => {
    calls.push([method, path]);
    if (method === 'POST' && path === '/model-hub/skills/contexts/') return { context_id: 'ctx', revision: 1 };
    if (method === 'POST' && path.endsWith('/draft/start/')) return { revision: 4 };
    if (method === 'PUT' && path.endsWith('/draft/')) return { revision: 5 };
    if (method === 'POST' && path.endsWith('/draft/head-review/')) return { draft_revision: body.revision, candidate_digest: 'sha256:c', expected_head_version: null, review_fingerprint: 'f' };
    throw new Error(`unexpected ${method} ${path}`);
  };
  const rollout = new ContextRollout({ hub, executor: {} });
  const created = await rollout.createContext({ name: 'n', files: [] });
  assert.equal(created.review.context_id, 'ctx'); assert.equal(created.review.draft_revision, 1); assert.match(created.review.packet_sha256, /^[0-9a-f]{64}$/);
  const edited = await rollout.editDraft({ contextId: 'ctx', fromVersion: 1, files: [] });
  assert.equal(edited.review.draft_revision, 5);
  assert.equal(calls.filter(([, path]) => /acknowledge-head|publish/.test(path)).length, 0, 'review packet only; nothing approved');
});

test('publish-and-start plans the rollout of exactly the published release and starts nothing if publishing is refused', async () => {
  const rollout = new ContextRollout({ hub: async () => null, executor: {} });
  const order = [];
  rollout.publishReviewed = async ({ review }) => { order.push(['publish', review.id]); return { number: 3, id: 'v3' }; };
  rollout.start = async (request) => { order.push(['start', request.version]); return { campaign_id: 'c' }; };
  const result = await rollout.publishAndStart({ review: { id: 'r' }, baseVersion: 2, start: { version: 2, contextName: 'n' } });
  assert.deepEqual(order, [['publish', 'r'], ['start', 3]]);
  assert.deepEqual(result, { version: { number: 3, id: 'v3' }, started: { campaign_id: 'c' } });
  await assert.rejects(rollout.publishAndStart({ review: { id: 'r' }, baseVersion: 2 }), /invalid_rollout_request/);
  rollout.publishReviewed = async () => { throw new Error('review_stale'); };
  order.length = 0;
  await assert.rejects(rollout.publishAndStart({ review: { id: 'r' }, baseVersion: 2, start: {} }), /review_stale/);
  assert.deepEqual(order, [], 'no rollout is started for an unpublished candidate');
});

test('authorize-and-open writes only the authorized include set and only after the same checks as authorizeReviewed', async () => {
  const opened = [];
  const executor = { execute: async ({ bindingId }) => { opened.push(bindingId); return { state: 'pr_open', pull_request_number: 1 }; } };
  const targets = [1, 2, 4].map((n) => ({ bindingId: binding(n), repository: 'synthetic/r', contents: {}, idempotencyKey: `k${n}` }));
  // A target outside the reviewed include set, or a missing one, refuses before authorization.
  for (const wrong of [[...targets, { ...targets[0], bindingId: binding(3) }], targets.slice(1)]) {
    const world = state(); const { hub, calls } = fakeHub(world);
    const rollout = new ContextRollout({ hub, executor });
    const packet = await rollout.packet({ campaignId, review: review() });
    await assert.rejects(rollout.authorizeAndOpen({ packet, acknowledgedExceptions: ['1003', '1004'], targets: wrong }), /targets_not_authorized_set/);
    assert.equal(calls.filter(([method, path]) => method === 'POST' && path.endsWith('/authorize/')).length, 0);
  }
  // Unacknowledged exceptions or a stale packet refuse with no Git write.
  const stale = state(); const staleHub = fakeHub(stale);
  const refused = new ContextRollout({ hub: staleHub.hub, executor });
  const stalePacket = await refused.packet({ campaignId, review: review() });
  await assert.rejects(refused.authorizeAndOpen({ packet: stalePacket, acknowledgedExceptions: ['1003'], targets }), /exceptions_not_acknowledged/);
  stale.campaign.revision = 9;
  await assert.rejects(refused.authorizeAndOpen({ packet: stalePacket, acknowledgedExceptions: ['1003', '1004'], targets }), /review_stale/);
  assert.deepEqual(opened, [], 'nothing opened without a current authorization');
  const world = state(); const { hub } = fakeHub(world);
  const rollout = new ContextRollout({ hub, executor });
  const packet = await rollout.packet({ campaignId, review: review() });
  const result = await rollout.authorizeAndOpen({ packet, acknowledgedExceptions: ['1004', '1003'], targets });
  assert.equal(result.campaign.status, 'authorized');
  assert.deepEqual(opened, [binding(1), binding(2), binding(4)]);
  assert.deepEqual(result.receipts.map((row) => row.state), ['pr_open', 'pr_open', 'pr_open']);
});

test('merge-reviewed installs only merged targets and load-checks only verified installations, with separate receipts', async () => {
  const rollout = new ContextRollout({ hub: async () => null, executor: {} });
  const seen = { install: null, load: null };
  rollout.batch = () => ({
    mergeAll: async ({ targets }) => targets.map((row) => (row.repository_id === '2'
      ? { repository_id: '2', state: 'refused', merged: false } : { repository_id: row.repository_id, state: 'merged', merged: true, merge_commit: 'm' })),
    executeAll: async ({ targets }) => targets.map(() => ({ state: 'merged', delivered: false })),
    applyAll: async ({ plans }) => plans.map((row) => ({ repository_id: row.repository_id, state: 'installed', delivery: 'not_observed', verified: row.repository_id !== '3' })),
  });
  const install = rollout.install.bind(rollout);
  rollout.install = async (request) => { seen.install = request.targets.map((row) => row.repository_id); return install(request); };
  rollout.loadCheck = async ({ targets }) => { seen.load = targets.map((row) => row.repository_id); return targets.map((row) => ({ repository_id: row.repository_id, delivery: 'observed' })); };
  const targets = ['1', '2', '3'].map((id) => ({ repository_id: id, repository: 'synthetic/r', pull_request_number: 1, commit: 'c', plan: {}, contents: {},
    binding_id: `b${id}`, plan_path: `p${id}`, project: `/p${id}`, expected_instructions: 'x' }));
  const result = await rollout.mergeReviewed({ campaignId, targets, host: { executable: '/x', sha256: sha(1) } });
  assert.deepEqual(result.merges.map((row) => row.state), ['merged', 'refused', 'merged']);
  assert.deepEqual(seen.install, ['1', '3'], 'a refused merge is never installed');
  assert.deepEqual(seen.load, ['1'], 'an unverified installation is never load-checked');
  assert.deepEqual(result.loads.map((row) => row.repository_id), ['1']);
  seen.load = null;
  const noHost = await rollout.mergeReviewed({ campaignId, targets, host: null });
  assert.deepEqual(noHost.loads, []); assert.equal(seen.load, null);
  await assert.rejects(rollout.mergeReviewed({ campaignId, targets: [] }), /invalid_targets/);
});

test('reconcile-and-resume resumes only on an exact reconciled PR and otherwise stays paused', async () => {
  for (const [state, resumed] of [['pr_open', true], ['unknown', false]]) {
    const campaign = { status: 'running', revision: 3 }; const actions = [];
    const hub = async (method, path, body) => {
      if (method === 'GET') return structuredClone(campaign);
      const action = path.split('/').at(-2); actions.push([action, body.reason]);
      campaign.status = action === 'pause' ? 'paused' : 'running'; campaign.revision += 1; return {};
    };
    const rollout = new ContextRollout({ hub, executor: {} });
    rollout.batch = () => ({ executeAll: async ({ targets }) => { assert.equal(campaign.status, 'paused', 'reconciled only while paused'); return targets.map(() => ({ state })); } });
    const result = await rollout.reconcileAndResume({ campaignId, target: { bindingId: binding(1) }, reason: 'Exact PR reconciled.' });
    assert.equal(result.observed.state, state);
    assert.deepEqual(actions.map(([action]) => action), resumed ? ['pause', 'resume'] : ['pause']);
    assert.equal(result.campaign.status, resumed ? 'running' : 'paused');
  }
  await assert.rejects(new ContextRollout({ hub: async () => null, executor: {} }).reconcileAndResume({ campaignId, target: {}, reason: ' ' }), /invalid_reconcile_request/);
});

test('one-step CLI commands refuse wrong arity before any hub, Git or SDK call', async () => {
  const { main } = await import('../../../bin/context-campaign.mjs');
  for (const args of [['adopt-review'], ['merge-install'], ['reconcile-resume', campaignId, 'x'], ['release-publish', 'a', 'b', 'c', 'd'], ['replan-resolved', 'a', 'b']]) {
    await assert.rejects(main(args, {}), /invalid_arguments/);
  }
  await assert.rejects(main(['adopt-review', '/nonexistent.json'], {}), /inventory_input_required/);
});

test('replan-resolved re-plans only surfaced local conflicts and returns one complete packet', async () => {
  const world = state(); world.targets[3].plan_digest = null;
  const { hub } = fakeHub(world);
  const rollout = new ContextRollout({ hub, executor: {} });
  const prior = { ...review(), patches: new Map([[binding(1), { p: 1 }], [binding(2), { p: 2 }]]) };
  prior.exceptions[1] = { binding_id: binding(4), repository_id: '1004', kind: 'local_conflict', reason: 'edited', observed: {} };
  prior.decisions[3] = { binding_id: binding(4), plan_digest: null, decision: 'exclude', reason: 'edited' };
  const request = { contextName: 'n', selected: [], mode: 'restore', version: 1, client: 'codex', restoreLocks: {}, outDir: '/tmp' };
  let calls = 0;
  rollout.batch = () => ({ planAll: async (args) => {
    calls += 1; assert.deepEqual(args.only, ['1004']); assert.equal(args.mode, 'restore');
    world.targets[3].plan_digest = 'sha256:p4'; world.campaign.revision += 1;
    return { revision: world.campaign.revision, groups: [{ key: 'sha256:same', members: [{ binding_id: binding(4), repository_id: '1004', plan_digest: 'sha256:p4' }] }],
      exceptions: [], decisions: [{ binding_id: binding(4), plan_digest: 'sha256:p4', decision: 'include', reason: 'r' }], patches: new Map([[binding(4), { p: 4 }]]) };
  } });
  // Refused before any planning: not a conflict the plan surfaced.
  await assert.rejects(rollout.replanResolved({ campaignId, review: prior, repositoryIds: ['1003'], request }), /not_a_surfaced_local_conflict/);
  assert.equal(calls, 0);
  const { review: merged, packet } = await rollout.replanResolved({ campaignId, review: prior, repositoryIds: ['1004'], request });
  assert.equal(calls, 1);
  assert.deepEqual(merged.groups[0].members.map((row) => row.repository_id), ['1001', '1002', '1004']);
  assert.deepEqual(merged.exceptions.map((row) => [row.repository_id, row.kind]), [['1003', 'pinned_selector']]);
  assert.equal(merged.decisions.length, 4); assert.equal(merged.patches.size, 3);
  assert.equal(packet.revision, world.campaign.revision);
  assert.deepEqual(packet.units.map((unit) => [unit.kind, unit.repository_ids]), [['group', ['1001', '1002', '1004']], ['held', ['1003']]]);
  assert.equal(packet.decisions.find((row) => row.binding_id === binding(4)).plan_digest, 'sha256:p4');
});
