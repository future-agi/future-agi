import { randomUUID } from 'node:crypto';
import { PORT_SCHEMA, SOURCE_REF, type Binding, type GatewayStage, type Identity,
  type LiveWorkload, type Operation, type RedisAction, type RedisLifecyclePort,
  type StackObservation, type Target } from './consumer-port';

/** UNIT-ONLY scripted capability. Never exported by index.ts, never supplied by
 * a live consumer or described as a native receipt. No I/O or product fixture.
 * Control tests mutate COPIES of these synthetic receipts, not captured logs.
 */
export function scriptedPort() {
  const nonce = randomUUID();
  const identity = (c: string, i: string, label: string): Identity => ({
    containerId: c.repeat(64), imageId: `sha256:${i.repeat(64)}`,
    incarnationId: `${nonce}-${label}`, running: true,
  });
  const gateway = identity('a', 'b', 'gateway');
  let state: StackObservation = {
    invocationId: nonce, stackId: `${nonce}-stack`, leaseId: `${nonce}-lease`,
    observedMonoMs: 100, leaseExpiresMonoMs: 100000,
    exclusive: true, workers: 1, overlappingInvocationIds: [],
    servingGatewayIds: [gateway.containerId], autoscalingEnabled: false, gateway,
    redis: { ...identity('c', 'd', 'redis'), volumeId: `${nonce}-volume`,
      persistencePolicyId: `${nonce}-policy`, serverRunId: 'e'.repeat(40) },
  };
  const initial = structuredClone(state);
  const calls: string[] = [];
  const b: Binding = { schema: PORT_SCHEMA, sourceRef: SOURCE_REF, sourceClean: true,
    invocationId: nonce, stackId: state.stackId, leaseId: state.leaseId,
    browser: 'chromium', redisClientContainerIds: [gateway.containerId], before: initial,
    effectiveConfig: { attestationId: `${nonce}-config`, managedRequireRedis: true,
      agentBudgetPrefix: '', redisDatabase: 0, redisLoopbackEndpoint: 'redis://127.0.0.1:26379',
      redisDependencyContainerId: state.redis.containerId },
    budgets: { REDIS_RECOVERY: { timeoutMs: 500, contractRef: `${nonce}-redis-bound` },
      GATEWAY_DRAIN: { timeoutMs: 500, contractRef: `${nonce}-drain-bound` },
      GATEWAY_RECOVERY: { timeoutMs: 500, contractRef: `${nonce}-gateway-bound` } },
  };
  const operation = (target: Target, label: string): Operation => ({
    operationId: `${nonce}-${calls.length}-${label}`, target,
    startedMonoMs: state.observedMonoMs + 1, endedMonoMs: state.observedMonoMs + 4,
    rawWait: { exitCode: 0, signal: null }, originalReceiptId: `${nonce}-${calls.length}-synthetic`,
  });
  let runId = '';
  const stage = (target: Target, name: GatewayStage['stage'], previous: string): GatewayStage => {
    calls.push(name);
    const before = structuredClone(state);
    const op = operation(target, name);
    if (name === 'restart') state.gateway.incarnationId = `${nonce}-gateway-restarted`;
    const after = { ...structuredClone(state), observedMonoMs: op.startedMonoMs + 1 };
    state = { ...structuredClone(after), observedMonoMs: op.endedMonoMs };
    return { ...op, stage: name, previousOperationId: previous, outcome: 'completed',
      before, after, admission: name === 'ready' ? 'open' : 'closed',
      admissionOpenedMonoMs: name === 'ready' ? after.observedMonoMs : null,
      preCloseRunIds: [runId], accountedRuns: name === 'close' ? [] : [{ runId, outcome: 'terminal' }],
      authoritativeStateUnchanged: true,
      readiness: name === 'ready' ? { path: '/readyz', status: 200, syncComplete: true,
        observedMonoMs: after.observedMonoMs } : null };
  };
  const redisAction = (target: Target, name: RedisAction['action']): RedisAction => {
    calls.push(name);
    const before = structuredClone(state);
    const op = operation(target, name);
    state.redis.running = name === 'redis.start';
    state.redis.serverRunId = name === 'redis.start' ? 'f'.repeat(40) : null;
    if (name === 'redis.start') state.redis.incarnationId = `${nonce}-redis-restarted`;
    const after = { ...structuredClone(state), observedMonoMs: op.startedMonoMs + 1 };
    state = { ...structuredClone(after), observedMonoMs: op.endedMonoMs };
    return { ...op, action: name, before, after, namespaceConnectivity: [{
      containerId: gateway.containerId, outcome: name === 'redis.stop' ? 'unreachable' : 'connected',
      observedMonoMs: op.endedMonoMs - 1 }] };
  };
  const port: RedisLifecyclePort = {
    schema: PORT_SCHEMA,
    binding: async () => structuredClone(b),
    inspect: async () => { state.observedMonoMs++; return structuredClone(state); },
    readRedis: async t => {
      calls.push('read');
      const op = operation(t, 'read');
      state.observedMonoMs = op.endedMonoMs;
      if (!state.redis.running) return { operation: op, containerId: state.redis.containerId,
        database: 0, outcome: 'unavailable', reason: 'connection-refused' };
      throw new Error('UNIT_READ_PLAN_NOT_SCRIPTED');
    },
    stopRedis: async t => redisAction(t, 'redis.stop'),
    startRedis: async t => redisAction(t, 'redis.start'),
    preservation: async (_t, from, to) => ({ fromOperationId: from, toOperationId: to,
      coverage: 'complete-native-command-and-fixture-ledger',
      redisIncarnationIds: [...new Set([initial.redis.incarnationId, state.redis.incarnationId])],
      commandCounts: [{ name: 'FLUSHDB', count: 0 }, { name: 'FLUSHALL', count: 0 }],
      resetCount: 0, reseedCount: 0 }),
    closeGateway: async (t, live) => { runId = live.runId; return stage(t, 'close', runId); },
    drainGateway: async (t, previous) => stage(t, 'drain', previous),
    revalidateGateway: async (t, previous) => stage(t, 'revalidate', previous),
    restartGateway: async (t, previous) => stage(t, 'restart', previous),
    awaitGatewayReady: async (t, previous) => stage(t, 'ready', previous),
  };
  const live: LiveWorkload = { requestId: `${nonce}-request`, runId: `${nonce}-run`,
    streamId: `${nonce}-stream`, holdId: `${nonce}-hold`, servingContainerId: gateway.containerId,
    observedMonoMs: 100, streamEventCount: 1, durableHoldObserved: true };
  return { port, calls, live, initial };
}
