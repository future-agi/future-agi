import assert from 'node:assert/strict';
import test from 'node:test';
import {
  DISPATCH_LIMIT_MS, boundaryDispatchError, probeAtBoundary, waitForWallClock,
} from '../lib/expiry-boundary.ts';

const TARGET = Date.UTC(2026, 10, 25, 6, 0, 0);
const BUDGET = 60_000;

// Controlled clock: no real timers. Each sleep advances monotonic time by the
// requested amount plus `overshootMs` (a late wake), and wall time by the same
// unless the wall clock is stalled.
function fakeClock({ wallStart, overshootMs = 0, stalled = false }) {
  let wall = wallStart;
  let mono = 0;
  const sleeps = [];
  return {
    sleeps,
    wallNow: () => wall,
    monotonicNow: () => mono,
    async sleep(ms) {
      assert.ok(ms > 0, `sleep(${ms}) must be positive`);
      sleeps.push(ms);
      mono += ms + overshootMs;
      if (!stalled) wall += ms + overshootMs;
    },
  };
}

async function crossing(options) {
  const clock = fakeClock({ wallStart: TARGET - 60_000, ...options });
  let probes = 0;
  const { dispatchedAt, result } = await probeAtBoundary(TARGET, BUDGET, async () => {
    probes += 1;
    return clock.wallNow();
  }, clock);
  assert.equal(probes, 1);
  assert.equal(result, dispatchedAt, 'probe runs with no clock advance after dispatch is recorded');
  return { clock, dispatchedAt, error: boundaryDispatchError(dispatchedAt, TARGET) };
}

test('an on-time wake dispatches at the stored instant and passes', async () => {
  const { clock, dispatchedAt, error } = await crossing({});
  assert.equal(dispatchedAt, TARGET);
  assert.equal(error, null);
  assert.deepEqual(clock.sleeps, [60_000], 'one wake for the remaining time, no polling');
});

test('a wake exactly at the limit passes; one millisecond later fails', async () => {
  const atLimit = await crossing({ overshootMs: DISPATCH_LIMIT_MS });
  assert.equal(atLimit.dispatchedAt - TARGET, DISPATCH_LIMIT_MS);
  assert.equal(atLimit.error, null);
  const late = await crossing({ overshootMs: DISPATCH_LIMIT_MS + 1 });
  assert.equal(late.dispatchedAt - TARGET, DISPATCH_LIMIT_MS + 1);
  assert.match(late.error, /1001 ms after expiry .* over the 1000 ms limit/);
});

test('an early wake rechecks the wall clock and sleeps again for the remainder', async () => {
  // The wall clock may advance less than the requested sleep (slewed or stepped
  // back); the probe must still not be dispatched before the stored instant.
  const readings = [TARGET - 2_000, TARGET - 300, TARGET];
  const sleeps = [];
  let mono = 0;
  const clock = {
    wallNow: () => readings[Math.min(sleeps.length, readings.length - 1)],
    monotonicNow: () => mono,
    async sleep(ms) { sleeps.push(ms); mono += ms; },
  };
  assert.equal(await waitForWallClock(TARGET, BUDGET, clock), TARGET);
  assert.deepEqual(sleeps, [2_000, 300]);
});

test('a stalled wall clock hits the monotonic cutoff inside the wait budget', async () => {
  const clock = fakeClock({ wallStart: TARGET - 5_000, stalled: true });
  let probes = 0;
  await assert.rejects(
    probeAtBoundary(TARGET, BUDGET, async () => { probes += 1; }, clock),
    /still before expiry .* after the 60000 ms wait budget/,
  );
  assert.equal(probes, 0, 'no probe is sent when the boundary was never observed');
  assert.ok(clock.monotonicNow() <= BUDGET, `waited ${clock.monotonicNow()} ms of monotonic time`);
  assert.equal(clock.sleeps.reduce((a, b) => a + b, 0), BUDGET);
});

test('a dispatch before the stored instant is reported, not accepted', () => {
  assert.match(boundaryDispatchError(TARGET - 1, TARGET), /1 ms before expiry/);
  assert.equal(boundaryDispatchError(TARGET, TARGET), null);
});
