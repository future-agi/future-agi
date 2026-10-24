import { test, expect } from '@playwright/test';
import pg from 'pg';
import { E2E } from '../lib/env';
import { StackLock, StackLockTimeout, lockSql } from '../lib/stack-lock';

// The stack lock's live test, against the running stack's Postgres (lib/stack-lock.ts). Each
// test mints its own lock name, so it never contends with a flow's lock.
const mint = (tag: string) => `harness-${tag}-${process.pid}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
const settled = async (p: Promise<unknown>, ms: number) =>
  Promise.race([p.then(() => 'settled', () => 'settled'), new Promise((r) => setTimeout(() => r('pending'), ms))]);

test('stack lock live: a second holder waits until the first releases', async () => {
  const name = mint('order');
  const events: string[] = [];
  const first = await StackLock.acquire(name, { timeoutMs: 5_000 });
  events.push('first acquired');
  const second = StackLock.acquire(name, { timeoutMs: 20_000 }).then((l) => { events.push('second acquired'); return l; });
  const whileHeld = await settled(second, 1_500);
  events.push('first released');
  await first.release();
  const lock = await second;
  await lock.release();
  expect({ whileHeld, events }).toEqual({ whileHeld: 'pending', events: ['first acquired', 'first released', 'second acquired'] });
});

test('stack lock live: a holder that waits past its budget is refused, naming the lock', async () => {
  const name = mint('timeout');
  const first = await StackLock.acquire(name, { timeoutMs: 5_000 });
  try {
    const refused = await StackLock.acquire(name, { timeoutMs: 1_000 }).then(
      async (l) => { await l.release(); return null; }, (e: unknown) => e);
    expect({ isTimeout: refused instanceof StackLockTimeout, message: (refused as Error | null)?.message?.replace(/\d+ ms/, '<n> ms') })
      .toEqual({ isTimeout: true, message: `stack lock ${name} not acquired within <n> ms (another worker holds it)` });
  } finally {
    await first.release();
  }
});

test('stack lock live: a session that ends without unlocking frees the lock (a worker that dies)', async () => {
  const name = mint('crash');
  const raw = new pg.Client({ connectionString: E2E.pgUrl });
  await raw.connect();
  const took = (await raw.query<{ locked: boolean }>(lockSql.tryLock, lockSql.params(name))).rows[0]?.locked;
  const whileRawHolds = await StackLock.acquire(name, { timeoutMs: 600 }).then(
    async (l) => { await l.release(); return 'acquired'; }, () => 'refused');
  await raw.end();
  const after = await StackLock.acquire(name, { timeoutMs: 10_000 });
  await after.release();
  expect({ took, whileRawHolds, afterWaitedUnder: after.waitedMs < 10_000 })
    .toEqual({ took: true, whileRawHolds: 'refused', afterWaitedUnder: true });
});

test('stack lock live: two names do not block each other, and release is idempotent', async () => {
  const a = await StackLock.acquire(mint('a'), { timeoutMs: 5_000 });
  const b = await StackLock.acquire(mint('b'), { timeoutMs: 1_000 }).then((l) => l, () => null);
  await a.release();
  await a.release();
  await b?.release();
  expect({ bAcquired: b !== null }).toEqual({ bAcquired: true });
});
