import { randomUUID } from 'node:crypto';
import { test, expect } from '@playwright/test';
import { compileRedisRead, decodeRedisRead } from './redis-probe';

// Tests FIRST; data-only consumer contract controls, NOT packet coverage.
// No actor, socket, subprocess, browser or Redis mutation. Execute only later
// through the accepted runner. Unknown/missing fields must never become zero.
function sample() {
  const org = randomUUID();
  const hold = `hold-${randomUUID()}`;
  const scope = `scope-${randomUUID()}`;
  const selection = { organizationId: org, holdIds: [hold], scopeIds: [scope] };
  // Redis RESP arrays, independent of the compiler/decoder implementation.
  const raw: unknown[] = [
    ['9007199254740993', '2', '7', '10'], [hold, '10'], [hold, '1900000000000'],
    [[hold, null, -2]], [4, 1, 0, 0],
    [[scope, '100', '7', '10', [hold, '10'], [hold, '1900000000000'], 0,
      [30000, 30000, 30000, 30000, 30000, -2]]],
  ];
  return { selection, raw };
}

test('F02 read plan pins exact org keys and never requests credentials', async () => {
  const { selection } = sample();
  const plan = compileRedisRead(selection, 'local:');
  expect(plan.keys).toEqual([
    `mc:{${selection.organizationId}}:acct`, `mc:{${selection.organizationId}}:holds`,
    `mc:{${selection.organizationId}}:holdexp`, `mc:{${selection.organizationId}}:outbox`,
    `mc:{${selection.organizationId}}:settled:${selection.holdIds[0]}`,
    ...['limit', 'spent', 'held', 'holds', 'holdexp', 'stopped'].map(suffix =>
      `local:abud:{${selection.organizationId}}:${selection.scopeIds[0]}:${suffix}`),
  ]);
  expect(plan.argv).toEqual(['1', '1', selection.holdIds[0], selection.scopeIds[0]]);
  expect(plan.command).toBe('EVAL_RO');
  expect(() => compileRedisRead({ ...selection, holdIds: ['bad}key'] }, '')).toThrow('F02.id');
  expect(() => compileRedisRead({ ...selection, holdIds: [] }, '')).toThrow('F02.cohort');
  expect(() => compileRedisRead(selection, 'bad{prefix')).toThrow('F02.prefix');
});

test('F02 lossless amounts, retained history distinct from pending, explicit missing marker', async () => {
  const { selection, raw } = sample();
  const result = decodeRedisRead(selection, raw);
  expect(result.account).toEqual({ grantedMicros: '9007199254740993', grantedVersion: '2', spentMicros: '7', heldMicros: '10' });
  expect(result.outbox).toEqual({ retained: 4, groupPresent: true, pending: 0, lag: 0, backlog: 0 });
  expect(result.markers).toEqual([{ holdId: selection.holdIds[0], value: null, pttlMs: -2 }]);
  expect(result.scopes[0].outstandingHoldCount).toBe(1);
  expect(result.scopes[0].closedState).toBe('UNSUPPORTED_AT_SOURCE_PIN');
});

for (const [name, mutate, error] of [
  ['fractional-money', (r: any[]) => { r[0][2] = '1.5'; }, 'F02.integer'],
  ['partial-account', (r: any[]) => { r[0][2] = null; }, 'F02.account'],
  ['duplicate-hold', (r: any[]) => { r[1].push(...r[1]); }, 'F02.duplicate'],
  ['unpaired-expiry', (r: any[]) => { r[2] = []; }, 'F02.hold-expiry'],
  ['missing-marker', (r: any[]) => { r[3] = []; }, 'F02.markers'],
  ['duplicate-scope', (r: any[]) => { r[5].push(r[5][0]); }, 'F02.scopes'],
  ['wrong-marker', (r: any[]) => { r[3][0][1] = 'unexpected'; }, 'F02.marker'],
  ['missing-marker-positive-ttl', (r: any[]) => { r[3][0][2] = 10; }, 'F02.marker'],
  ['negative-counter', (r: any[]) => { r[4][2] = -1; }, 'F02.counter'],
] as const) {
  test(`F02 decoder rejects ${name}; untouched original restores`, async () => {
    const { selection, raw } = sample();
    const original = structuredClone(raw);
    const corrupt = structuredClone(raw);
    mutate(corrupt);
    expect(() => decodeRedisRead(selection, corrupt)).toThrow(error);
    expect(raw).toEqual(original);
    expect(decodeRedisRead(selection, raw).holds).toEqual([{ holdId: selection.holdIds[0], micros: '10', expiresAtMs: '1900000000000' }]);
  });
}

test('F02 absent consumer group and unknown lag do not assert drained', async () => {
  const { selection, raw } = sample();
  raw[4] = [4, 0, null, null];
  expect(decodeRedisRead(selection, raw).outbox).toEqual({ retained: 4, groupPresent: false, pending: null, lag: null, backlog: 4 });
  raw[4] = [4, 1, 0, null];
  expect(decodeRedisRead(selection, raw).outbox.backlog).toBeNull();
});
