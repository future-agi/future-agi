import { isDeepStrictEqual } from 'node:util';
import { ensure, syntheticId } from './redis-probe';
import { PORT_SCHEMA, SOURCE_REF, type Binding, type Identity, type Operation,
  type Preservation, type Role, type StackObservation, type Target } from './consumer-port';

export function equal(actual: unknown, expected: unknown, code: string) {
  ensure(isDeepStrictEqual(actual, expected), code);
}
export function clock(value: unknown): asserts value is number {
  ensure(typeof value === 'number' && Number.isSafeInteger(value) && value >= 0, 'F03.clock');
}
function identity(value: Identity) {
  ensure(/^[a-f0-9]{64}$/.test(value.containerId), 'F03.container-id');
  ensure(/^sha256:[a-f0-9]{64}$/.test(value.imageId), 'F03.image-id');
  syntheticId(value.incarnationId);
  ensure(typeof value.running === 'boolean', 'F03.running');
}
export function target(binding: Binding, role: Role): Target {
  return { invocationId: binding.invocationId, stackId: binding.stackId,
    leaseId: binding.leaseId, containerId: binding.before[role].containerId,
    imageId: binding.before[role].imageId };
}

/** The runtime resolves topology; comparisons here defend against accidental
 * wrong-ID use, not forged native receipts. Full source/LM/image/migration/parts
 * and accepted runner joins are F19, deliberately not asserted from booleans.
 */
export function validateObservation(binding: Binding, observation: StackObservation) {
  for (const name of ['invocationId', 'stackId', 'leaseId'] as const) {
    syntheticId(observation[name]);
    equal(observation[name], binding[name], `F03.${name}`);
  }
  clock(observation.observedMonoMs); clock(observation.leaseExpiresMonoMs);
  ensure(observation.leaseExpiresMonoMs > observation.observedMonoMs, 'F03.expired-lease');
  equal([observation.exclusive, observation.workers, observation.overlappingInvocationIds,
    observation.autoscalingEnabled], [true, 1, [], false], 'F03.exclusivity');
  equal(observation.servingGatewayIds, [binding.before.gateway.containerId], 'F03.serving-set');
  for (const role of ['redis', 'gateway'] as const) {
    identity(observation[role]);
    equal([observation[role].containerId, observation[role].imageId],
      [binding.before[role].containerId, binding.before[role].imageId], 'F03.identity-drift');
  }
  syntheticId(observation.redis.volumeId); syntheticId(observation.redis.persistencePolicyId);
  equal([observation.redis.volumeId, observation.redis.persistencePolicyId],
    [binding.before.redis.volumeId, binding.before.redis.persistencePolicyId], 'F03.persistence-drift');
  if (observation.redis.running) {
    ensure(typeof observation.redis.serverRunId === 'string' && /^[a-f0-9]{40}$/.test(observation.redis.serverRunId), 'F03.redis-server');
  } else equal(observation.redis.serverRunId, null, 'F03.stopped-server');
}
export function validateBinding(b: Binding) {
  equal([b.schema, b.sourceRef, b.sourceClean], [PORT_SCHEMA, SOURCE_REF, true], 'F03.source-binding');
  ensure(b.browser === 'chromium' || b.browser === 'firefox', 'F03.browser');
  equal(b.effectiveConfig.managedRequireRedis, true, 'F03.require-redis');
  syntheticId(b.effectiveConfig.attestationId);
  equal(b.effectiveConfig.redisDependencyContainerId, b.before.redis.containerId, 'F03.dependency');
  ensure(b.before.gateway.containerId !== b.before.redis.containerId, 'F03.role-collision');
  ensure(Array.isArray(b.redisClientContainerIds) && b.redisClientContainerIds.length > 0 &&
    b.redisClientContainerIds.length <= 32 && new Set(b.redisClientContainerIds).size === b.redisClientContainerIds.length &&
    b.redisClientContainerIds.includes(b.before.gateway.containerId) &&
    b.redisClientContainerIds.every(id => /^[a-f0-9]{64}$/.test(id)), 'F03.redis-client-inventory');
  const url = new URL(b.effectiveConfig.redisLoopbackEndpoint);
  ensure(url.protocol === 'redis:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname) &&
    !!url.port && !url.username && !url.password && !url.search && !url.hash &&
    (!url.pathname || url.pathname === '/'), 'F03.loopback');
  ensure(Number.isSafeInteger(b.effectiveConfig.redisDatabase) && b.effectiveConfig.redisDatabase >= 0, 'F03.database');
  ensure(/^(?:[A-Za-z0-9_.:-]{1,96})?$/.test(b.effectiveConfig.agentBudgetPrefix), 'F02.prefix');
  for (const deadline of Object.values(b.budgets)) {
    ensure(Number.isSafeInteger(deadline.timeoutMs) && deadline.timeoutMs > 0, 'F03.deadline');
    syntheticId(deadline.contractRef);
  }
  equal(Object.keys(b.budgets).sort(), ['GATEWAY_DRAIN', 'GATEWAY_RECOVERY', 'REDIS_RECOVERY'], 'F03.budgets');
  validateObservation(b, b.before);
  ensure(b.before.gateway.running && b.before.redis.running, 'F03.healthy-baseline');
}
export function validateOperation(b: Binding, op: Operation, role: Role, previousEnd: number, success = true) {
  syntheticId(op.operationId); syntheticId(op.originalReceiptId);
  equal(op.target, target(b, role), 'F03.operation-target');
  clock(op.startedMonoMs); clock(op.endedMonoMs);
  ensure(op.startedMonoMs >= previousEnd && op.endedMonoMs >= op.startedMonoMs, 'F03.operation-order');
  if (success) equal(op.rawWait, { exitCode: 0, signal: null }, 'F03.raw-wait');
}
export function validatePreservation(p: Preservation, from: string, to: string, incarnations: string[]) {
  equal([p.fromOperationId, p.toOperationId, p.coverage],
    [from, to, 'complete-native-command-and-fixture-ledger'], 'F03.preservation-coverage');
  equal([...p.redisIncarnationIds].sort(), [...new Set(incarnations)].sort(), 'F03.incarnation-coverage');
  equal([...p.commandCounts].sort((a, b) => a.name.localeCompare(b.name)),
    [{ name: 'FLUSHALL', count: 0 }, { name: 'FLUSHDB', count: 0 }], 'F03.flush');
  equal([p.resetCount, p.reseedCount], [0, 0], 'F03.no-reset-reseed');
}
