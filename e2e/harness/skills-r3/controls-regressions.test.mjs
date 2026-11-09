import test from 'node:test';
import assert from 'node:assert/strict';
import { Barriers, OwnedResources } from '../../lib/skills-r3/controls.mjs';

const never = () => new Promise(() => {});

test('closing before worker arrival rejects every arrival observer', { timeout: 1_000 }, async () => {
  const barriers = new Barriers();
  const first = assert.rejects(barriers.reached('never-arrived'), /Fixture teardown/);
  const second = assert.rejects(barriers.reached('never-arrived'), /Fixture teardown/);
  const other = assert.rejects(barriers.reached('another-worker'), /Fixture teardown/);
  barriers.close();
  barriers.close();
  await Promise.all([first, second, other]);
  assert.throws(() => barriers.reached('after-close'), /closed/);
  await assert.rejects(barriers.wait('after-close'), /closed/);
});

test('close preserves an already-observed arrival while rejecting its release waiter', async () => {
  const barriers = new Barriers();
  const waiting = assert.rejects(barriers.wait('arrived'), /Fixture teardown/);
  const arrival = barriers.reached('arrived');
  await arrival;
  barriers.close();
  await waiting;
  await arrival;
});

for (const stalled of ['cancel', 'settle', 'terminate', 'destroyed']) {
  test(`stalled ${stalled} cannot prevent destruction checks for any owned resource`, { timeout: 1_000 }, async () => {
    const resources = new OwnedResources('bounded-probes', { probeTimeoutMs: 10 });
    const calls = new Map();
    for (const id of ['healthy-before', 'stalled', 'healthy-after']) {
      calls.set(id, []);
      const callbacks = Object.fromEntries(['cancel', 'settle', 'terminate', 'destroyed'].map((step) => [step, () => {
        calls.get(id).push(step);
        return id === 'stalled' && step === stalled ? never() : true;
      }]));
      resources.register({ id, ownerCapability: resources.ownerCapability, ...callbacks });
    }
    const result = await resources.teardown();
    assert.deepEqual(result.receipts.map((r) => r.id), ['healthy-after', 'stalled', 'healthy-before']);
    for (const sequence of calls.values()) assert.deepEqual(sequence, ['cancel', 'settle', 'terminate', 'destroyed']);
    for (const receipt of result.receipts) {
      assert.equal(receipt.cancellation, receipt.id === 'stalled' && stalled === 'cancel' ? 'unknown' : 'requested');
      assert.equal(receipt.settlement, receipt.id === 'stalled' && stalled === 'settle' ? 'unknown' : 'settled');
      assert.equal(receipt.destruction, receipt.id === 'stalled' && stalled === 'destroyed' ? 'unknown' : 'confirmed');
    }
    assert.equal(result.qualified, stalled !== 'settle' && stalled !== 'destroyed');
  });
}

test('all forty unavailable resources get all probes within a population-independent bound', { timeout: 1_000 }, async () => {
  const resources = new OwnedResources('population-outage', { probeTimeoutMs: 10 });
  const calls = new Map();
  for (let index = 0; index < 40; index++) {
    const id = `guest-${index}`;
    calls.set(id, []);
    resources.register({ id, ownerCapability: resources.ownerCapability,
      ...Object.fromEntries(['cancel', 'settle', 'terminate', 'destroyed'].map((step) => [step, () => {
        calls.get(id).push(step);
        return never();
      }])),
    });
  }
  const result = await resources.teardown();
  assert.equal(result.receipts.length, 40);
  assert.equal(result.qualified, false);
  for (const sequence of calls.values()) assert.deepEqual(sequence, ['cancel', 'settle', 'terminate', 'destroyed']);
  for (const receipt of result.receipts) assert.deepEqual(receipt, {
    id: receipt.id, cancellation: 'unknown', settlement: 'unknown', destruction: 'unknown',
  });
  await assert.rejects(resources.teardown(), /already/);
});

test('late probes cannot revise an uncertainty receipt or cause unhandled rejection', { timeout: 1_000 }, async () => {
  const resources = new OwnedResources('late-probes', { probeTimeoutMs: 10 });
  let resolveSettlement; let rejectCancellation;
  resources.register({ id: 'guest', ownerCapability: resources.ownerCapability,
    cancel: () => new Promise((_, reject) => { rejectCancellation = reject; }),
    settle: () => new Promise((resolve) => { resolveSettlement = resolve; }),
    terminate: () => { throw new Error('transport unavailable'); },
    destroyed: () => true,
  });
  const result = await resources.teardown();
  rejectCancellation(new Error('late transport failure'));
  resolveSettlement(true);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(result.qualified, false);
  assert.deepEqual(result.receipts, [{ id: 'guest', cancellation: 'unknown', settlement: 'unknown', destruction: 'confirmed' }]);
});

test('probe budgets cannot be unbounded or exceed the journey teardown budget', () => {
  for (const probeTimeoutMs of [0, -1, NaN, Infinity, 0.5, 5_001]) {
    assert.throws(() => new OwnedResources('invalid-budget', { probeTimeoutMs }), /Probe timeout/);
  }
});
