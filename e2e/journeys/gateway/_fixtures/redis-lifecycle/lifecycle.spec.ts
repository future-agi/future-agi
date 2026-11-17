import { randomUUID } from 'node:crypto';
import { test, expect } from '@playwright/test';
import { PORT_SCHEMA, SOURCE_REF, type Binding, type RedisLifecyclePort } from './consumer-port';
import { validateBinding, validatePreservation } from './binding';
import { createRedisLifecycle } from './fixture';
import { scriptedPort } from './scripted-port.spec-support';
import { attachSeededIds } from '../../_shared/contract';

// These are data-only admission/consumer controls, not live lifecycle evidence.
// No runtime method below performs I/O. They cannot qualify L3-C03 or L4-C03
// without the separately owed native base negative-control executions.
function binding(): Binding {
  const id = randomUUID();
  const redis = { containerId: 'a'.repeat(64), imageId: `sha256:${'b'.repeat(64)}`,
    incarnationId: `${id}-redis`, running: true, volumeId: `${id}-volume`,
    persistencePolicyId: `${id}-aof-policy`, serverRunId: 'c'.repeat(40) };
  const gateway = { containerId: 'd'.repeat(64), imageId: `sha256:${'e'.repeat(64)}`,
    incarnationId: `${id}-gateway`, running: true };
  return { schema: PORT_SCHEMA, sourceRef: SOURCE_REF, sourceClean: true,
    invocationId: id, stackId: `${id}-stack`, leaseId: `${id}-lease`, browser: 'chromium',
    redisClientContainerIds: [gateway.containerId],
    effectiveConfig: { attestationId: `${id}-config`, managedRequireRedis: true,
      agentBudgetPrefix: 'fixture:', redisDatabase: 0,
      redisLoopbackEndpoint: 'redis://127.0.0.1:26379', redisDependencyContainerId: redis.containerId },
    before: { invocationId: id, stackId: `${id}-stack`, leaseId: `${id}-lease`,
      observedMonoMs: 100, leaseExpiresMonoMs: 10000, exclusive: true, workers: 1,
      overlappingInvocationIds: [], servingGatewayIds: [gateway.containerId],
      autoscalingEnabled: false, gateway, redis },
    // Synthetic unit values, NOT a proposed product timeout/default.
    budgets: { REDIS_RECOVERY: { timeoutMs: 500, contractRef: `${id}-recovery` },
      GATEWAY_DRAIN: { timeoutMs: 500, contractRef: `${id}-drain` },
      GATEWAY_RECOVERY: { timeoutMs: 500, contractRef: `${id}-gateway-recovery` } } };
}
for (const [name, mutate, code] of [
  ['wrong-redis', (b: Binding) => { b.effectiveConfig.redisDependencyContainerId = 'f'.repeat(64); }, 'F03.dependency'],
  ['overlap', (b: Binding) => { b.before.overlappingInvocationIds.push('other'); }, 'F03.exclusivity'],
  ['expired-reservation', (b: Binding) => { b.before.leaseExpiresMonoMs = 99; }, 'F03.expired-lease'],
  ['nonlocal', (b: Binding) => { b.effectiveConfig.redisLoopbackEndpoint = 'redis://192.0.2.1:6379'; }, 'F03.loopback'],
  ['wrong-source', (b: any) => { b.sourceRef = '0'.repeat(40); }, 'F03.source-binding'],
  ['dirty-source', (b: any) => { b.sourceClean = false; }, 'F03.source-binding'],
  ['missing-require-redis', (b: any) => { delete b.effectiveConfig.managedRequireRedis; }, 'F03.require-redis'],
  ['hidden-standby', (b: Binding) => { b.before.servingGatewayIds.push('f'.repeat(64)); }, 'F03.serving-set'],
] as const) {
  test(`F03 ${name} refuses before mutation; pristine binding restores`, async () => {
    const original = binding();
    const bad = structuredClone(original);
    mutate(bad);
    let calls = 0;
    const port = { schema: PORT_SCHEMA, binding: async () => bad,
      stopRedis: async () => { calls++; throw new Error('MUST_NOT_CALL'); } } as unknown as RedisLifecyclePort;
    await expect(createRedisLifecycle(port)).rejects.toThrow(code);
    expect(calls).toBe(0);
    expect(() => validateBinding(original)).not.toThrow();
  });
}

test('F03 unavailable Redis is explicit; a reader error is not outage evidence', async () => {
  const { port } = scriptedPort();
  port.readRedis = async () => { throw new Error('reader-schema-failure'); };
  const fixture = await createRedisLifecycle(port);
  await expect(fixture.read({ organizationId: randomUUID(), holdIds: ['h'], scopeIds: [] }))
    .rejects.toThrow('reader-schema-failure');
});

test('F03 preservation requires both FLUSH names, native continuous coverage and no reseed', () => {
  const original = { fromOperationId: 'stop', toOperationId: 'start',
    coverage: 'complete-native-command-and-fixture-ledger' as const,
    redisIncarnationIds: ['before', 'after'], commandCounts: [
      { name: 'FLUSHDB' as const, count: 0 }, { name: 'FLUSHALL' as const, count: 0 }],
    resetCount: 0, reseedCount: 0 };
  const check = (p: typeof original) => validatePreservation(p, 'stop', 'start', ['before', 'after']);
  expect(() => check({ ...original, commandCounts: [original.commandCounts[0]] })).toThrow('F03.flush');
  expect(() => check({ ...original, reseedCount: 1 })).toThrow('F03.no-reset-reseed');
  expect(() => check({ ...original, redisIncarnationIds: ['after'] })).toThrow('F03.incarnation-coverage');
  expect(() => check(original)).not.toThrow();
});

test('F03 ID-bound stop, explicit unavailable read and same-volume restore', async ({}, info) => {
  const model = scriptedPort();
  const org = randomUUID();
  const hold = `hold-${randomUUID()}`;
  await attachSeededIds(info, 'J27-L3', { organizationIds: [org], holdIds: [hold] });
  const fixture = await createRedisLifecycle(model.port);
  await fixture.stopRedis();
  const fault = await fixture.read({ organizationId: org, holdIds: [hold], scopeIds: [] });
  expect(fault.outcome).toBe('unavailable');
  expect(Object.hasOwn(fault, 'state')).toBe(false);
  const result = await fixture.restoreRedis();
  expect(result.restored.after.redis.volumeId).toBe(model.initial.redis.volumeId);
  expect(result.restored.after.redis.incarnationId).not.toBe(model.initial.redis.incarnationId);
  expect(model.calls).toEqual(['redis.stop', 'read', 'redis.start']);
});

for (const defect of ['wrong-volume', 'failed-raw-wait', 'missing-namespace', 'gateway-restarted', 'late-recovery'] as const) {
  test(`F03 ${defect} receipt rejected; original synthetic lifecycle restores`, async () => {
    const model = scriptedPort();
    const start = model.port.startRedis;
    model.port.startRedis = async (...args) => {
      const receipt = await start(...args);
      if (defect === 'wrong-volume') receipt.after.redis.volumeId = 'other-volume';
      if (defect === 'failed-raw-wait') receipt.rawWait.exitCode = 1;
      if (defect === 'missing-namespace') receipt.namespaceConnectivity = [];
      if (defect === 'gateway-restarted') receipt.after.gateway.incarnationId = 'other-process';
      if (defect === 'late-recovery') receipt.endedMonoMs += 501;
      return receipt;
    };
    const fixture = await createRedisLifecycle(model.port);
    await fixture.stopRedis();
    const codes = { 'wrong-volume': 'F03.persistence-drift', 'failed-raw-wait': 'F03.raw-wait',
      'missing-namespace': 'F03.namespace-inventory', 'gateway-restarted': 'F03.gateway-changed-during-redis-fault',
      'late-recovery': 'F03.redis-recovery-deadline' };
    await expect(fixture.restoreRedis()).rejects.toThrow(codes[defect]);
    // New UNIT model; not an in-fault product reseed or repaired captured receipt.
    const clean = await createRedisLifecycle(scriptedPort().port);
    await clean.stopRedis();
    await expect(clean.restoreRedis()).resolves.toHaveProperty('preservation.reseedCount', 0);
  });
}

for (const defect of ['drain-refused', 'unaccounted-run', 'state-drift', 'wrong-predecessor', 'early-restart'] as const) {
  test(`F03 ${defect} refuses before gateway mutation; unchanged recipe restores`, async ({}, info) => {
    const model = scriptedPort();
    await attachSeededIds(info, 'J27-L4', { requestIds: [model.live.requestId],
      runIds: [model.live.runId], holdIds: [model.live.holdId] });
    const drain = model.port.drainGateway;
    const revalidate = model.port.revalidateGateway;
    model.port.drainGateway = async (...args) => {
      const receipt = await drain(...args);
      if (defect === 'drain-refused') receipt.outcome = 'refused';
      if (defect === 'unaccounted-run') receipt.accountedRuns = [];
      if (defect === 'wrong-predecessor') receipt.previousOperationId = 'other-operation';
      if (defect === 'early-restart') receipt.after.gateway.incarnationId = 'early-process';
      return receipt;
    };
    model.port.revalidateGateway = async (...args) => {
      const receipt = await revalidate(...args);
      if (defect === 'state-drift') receipt.authoritativeStateUnchanged = false;
      return receipt;
    };
    const codes = { 'drain-refused': 'F03.stage-refused-or-unordered', 'unaccounted-run': 'F03.unaccounted-drain',
      'state-drift': 'F03.state-drift', 'wrong-predecessor': 'F03.stage-refused-or-unordered',
      'early-restart': 'F03.early-restart' };
    const fixture = await createRedisLifecycle(model.port);
    await expect(fixture.restartGateway(model.live)).rejects.toThrow(codes[defect]);
    expect(model.calls).toEqual(defect === 'state-drift' ? ['close', 'drain', 'revalidate'] : ['close', 'drain']);
    const restored = scriptedPort();
    await (await createRedisLifecycle(restored.port)).restartGateway(restored.live);
    expect(restored.calls).toEqual(['close', 'drain', 'revalidate', 'restart', 'ready']);
  });
}
