import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { ActionLedger, summarizeLedger, verifyLedger } from '../../lib/skills-r3/ledger.mjs';

const action = { dimension: 'A', primitive: 'command', actor: 'engineer', step: 'inventory', phase: 'cold', target: 'library' };

test('counts failed attempts and explicit retries, not arbitrary stage counts', async () => {
  const ledger = new ActionLedger({ runId: 'run-1', job: 'J1', mode: 'selftest' });
  await assert.rejects(ledger.measure(action, () => { throw new Error('secret-that-must-not-be-recorded'); }));
  await ledger.measure(action, () => {});
  for (const [dimension, primitive] of [['S', 'transition'], ['H', 'handoff'], ['R', 'reconcile']]) await ledger.measure({ ...action, dimension, primitive }, () => {});
  const summary = summarizeLedger(ledger.events());
  assert.deepEqual(summary.counts, { A: 2, S: 1, H: 1, R: 1 });
  assert.deepEqual(summary.phases.cold, summary.counts);
  assert.equal(summary.failed_actions, 1);
  assert.equal(summary.human_active_ms, null);
  assert.ok(!JSON.stringify(ledger.events()).includes('secret-that'));
  ledger.close();
});

test('review exposes items and exceptions without treating grouped approval as human effort', async () => {
  const ledger = new ActionLedger({ runId: 'review', job: 'J4', mode: 'selftest' });
  await assert.rejects(ledger.measure({ ...action, primitive: 'review' }, () => {}), /exposed items/);
  await ledger.measure({ ...action, primitive: 'review', items: ['parent-1', 'parent-2'], exceptionItems: ['parent-2'] }, () => {});
  const summary = summarizeLedger(ledger.events());
  assert.equal(summary.counts.A, 1);
  assert.deepEqual(summary.review_exposures[0].items, ['parent-1', 'parent-2']);
  assert.equal(summary.human_active_ms, null);
  await assert.rejects(ledger.measure({ ...action, primitive: 'review', items: ['parent-1'], exceptionItems: ['other'] }, () => {}));
});

test('append-only journal survives an interrupted action; mutation and mixed run fail verification', async () => {
  const root = await mkdtemp(join(tmpdir(), 'l13-ledger-'));
  try {
    const path = join(root, 'run.jsonl');
    const ledger = new ActionLedger({ runId: 'persist', job: 'J1', mode: 'selftest', path });
    let release;
    const waiting = ledger.measure(action, () => new Promise((resolve) => { release = resolve; }));
    const incomplete = JSON.parse((await readFile(path, 'utf8')).trim());
    assert.equal(verifyLedger([incomplete]).complete, false);
    assert.throws(() => new ActionLedger({ runId: 'persist', job: 'J1', mode: 'selftest', path }), /EEXIST/);
    release(); await waiting; ledger.close();
    const events = (await readFile(path, 'utf8')).trim().split('\n').map(JSON.parse);
    assert.equal(verifyLedger(events).complete, true);
    events[0].target = 'tampered';
    assert.throws(() => verifyLedger(events), /chain mismatch/);
  } finally { await rm(root, { recursive: true }); }
});

test('only meaningful primitives, opaque identifiers and monotonic clocks are accepted', async () => {
  let clock = 10;
  const ledger = new ActionLedger({ runId: 'validation', job: 'J2', mode: 'selftest', clock: () => clock });
  await assert.rejects(ledger.measure({ ...action, primitive: 'entire-job' }, () => {}));
  await assert.rejects(ledger.measure({ ...action, target: 'Authorization: secret' }, () => {}));
  await assert.rejects(ledger.measure({ ...action, target: 'sk-synthetic-not-a-real-secret' }, () => {}));
  await assert.rejects(ledger.measure({ ...action, dimension: 'S' }, () => {}));
  await ledger.measure(action, () => {});
  clock = 9;
  assert.throws(() => ledger.fault('outage', 'scenario-1'), /backwards/);
});
