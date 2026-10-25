import { test, expect, request } from '@playwright/test';
import { freshWindowWith, gatewayNow, parseGatewayDate } from '../lib/clock';

// The clock's own tests: offline with an injected gateway clock and a fake sleep that
// advances it; one live case against the running gateway.

function fakeGateway(start: string) {
  let at = Date.parse(start);
  const waits: number[] = [];
  return {
    waits,
    now: async () => new Date(at),
    sleep: async (ms: number) => { waits.push(ms); at += ms; },
  };
}

for (const [start, waits, second] of [
  ['2026-10-22T12:00:59Z', [6000], 5], // past the window: into the next minute's 5th second
  ['2026-10-22T12:00:10Z', [], 10], // inside: at once
  ['2026-10-22T12:00:02Z', [3000], 5], // too early: up to the 5th second
  ['2026-10-22T12:00:35Z', [], 35], // the window's last second still leaves 25 s
  ['2026-10-22T12:00:36Z', [29000], 5],
] as [string, number[], number][]) {
  test(`gateway clock offline: at ${start.slice(11, 19)} the window waits ${waits.join('+') || 'nothing'}`, async () => {
    const g = fakeGateway(start);
    expect(await freshWindowWith(g.now, g.sleep)).toEqual({ second });
    expect(g.waits).toEqual(waits);
  });
}

test('gateway clock offline: a read that lands early is waited out again, then a stuck clock throws', async () => {
  const reads = ['2026-10-22T12:00:59Z', '2026-10-22T12:01:04Z', '2026-10-22T12:01:05Z'];
  const waits: number[] = [];
  const now = async () => new Date(reads.shift()!);
  expect(await freshWindowWith(now, async (ms) => { waits.push(ms); })).toEqual({ second: 5 });
  expect(waits).toEqual([6000, 1000]);
  const stuck = async () => new Date('2026-10-22T12:00:59Z');
  await expect(freshWindowWith(stuck, async () => {})).rejects.toThrow('still outside [5, 35]');
});

test('gateway clock offline: no Date header, or a malformed one, throws instead of using the host clock', () => {
  expect(parseGatewayDate('Thu, 22 Oct 2026 12:00:59 GMT').toISOString()).toBe('2026-10-22T12:00:59.000Z');
  for (const header of [undefined, null, '', '2026-10-22T12:00:59Z', 'Thu, 22 Oct 2026 12:00:59 UTC',
    'Thu, 22 Oct 2026 12:00 GMT']) {
    expect(() => parseGatewayDate(header), String(header)).toThrow('gateway clock: no usable Date header');
  }
});

test('gateway clock live: two reads 2 s apart differ by 1-3 s', async () => {
  const ctx = await request.newContext();
  try {
    const first = await gatewayNow(ctx);
    await new Promise((r) => setTimeout(r, 2000));
    const second = await gatewayNow(ctx);
    const apart = (second.getTime() - first.getTime()) / 1000;
    expect(apart).toBeGreaterThanOrEqual(1);
    expect(apart).toBeLessThanOrEqual(3);
  } finally {
    await ctx.dispose();
  }
});
