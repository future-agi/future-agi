import { compileRedisRead, decodeRedisRead, ensure, syntheticId, type RedisSelection } from './redis-probe';
import { clock, equal, target, validateBinding, validateObservation, validateOperation, validatePreservation } from './binding';
import { PORT_SCHEMA, type Binding, type GatewayStage, type LiveWorkload,
  type RedisAction, type RedisLifecyclePort, type StackObservation } from './consumer-port';

/** No I/O implementation: all privileged actions use the runner's injected,
 * invocation-scoped E6 fixture capability. Absent capability fails setup; never
 * construct Docker/Redis/socket clients or silently use the old harness.
 * The accepted runner owns failure recovery and retains the exclusive lease
 * until bound service restoration, including when this consumer assertion fails.
 */
export async function createRedisLifecycle(port: RedisLifecyclePort) {
  ensure(port?.schema === PORT_SCHEMA && typeof port.binding === 'function', 'F03.missing-consumer-port');
  const b = structuredClone(await port.binding());
  validateBinding(b);
  return new RedisLifecycleFixture(port, b);
}
export class RedisLifecycleFixture {
  private readonly port: RedisLifecyclePort;
  private readonly binding: Binding;
  private lastEnd: number;
  private active = false;
  private stopped: RedisAction | undefined;
  private poisoned = false;
  private readonly operations = new Set<string>();
  constructor(port: RedisLifecyclePort, binding: Binding) {
    validateBinding(binding);
    ensure(port?.schema === PORT_SCHEMA && ['binding', 'inspect', 'readRedis', 'stopRedis', 'startRedis',
      'preservation', 'closeGateway', 'drainGateway', 'revalidateGateway', 'restartGateway', 'awaitGatewayReady']
      .every(name => typeof port[name as keyof RedisLifecyclePort] === 'function'), 'F03.missing-declared-hook');
    this.port = port;
    this.binding = structuredClone(binding);
    this.lastEnd = binding.before.observedMonoMs;
  }
  // Fresh copy prevents a test changing its binding after initial validation.
  get bound(): Binding { return structuredClone(this.binding); }
  private async inspect(role: 'redis' | 'gateway'): Promise<StackObservation> {
    const observation = await this.port.inspect(target(this.binding, role));
    validateObservation(this.binding, observation);
    ensure(observation.observedMonoMs >= this.lastEnd, 'F03.stale-inspect');
    return observation;
  }
  private claim(operationId: string) {
    syntheticId(operationId);
    ensure(!this.operations.has(operationId), 'F03.replayed-operation');
    this.operations.add(operationId);
  }
  private async exclusive<T>(action: () => Promise<T>): Promise<T> {
    ensure(!this.active && !this.poisoned, 'F03.concurrent-or-poisoned');
    this.active = true;
    try { return await action(); }
    catch (error) {
      // After any uncertain action, only the runner's recovery path may proceed.
      // Do not reuse a receipt or guess whether a privileged mutation happened.
      this.poisoned = true;
      throw error;
    } finally { this.active = false; }
  }
  async read(selection: RedisSelection) {
    return this.exclusive(async () => {
      const plan = compileRedisRead(selection, this.binding.effectiveConfig.agentBudgetPrefix);
      const before = await this.inspect('redis');
      const receipt = await this.port.readRedis(target(this.binding, 'redis'), plan);
      validateOperation(this.binding, receipt.operation, 'redis', before.observedMonoMs);
      ensure(receipt.operation.endedMonoMs < before.leaseExpiresMonoMs, 'F03.read-after-lease');
      this.claim(receipt.operation.operationId);
      equal([receipt.containerId, receipt.database],
        [before.redis.containerId, this.binding.effectiveConfig.redisDatabase], 'F02.read-binding');
      this.lastEnd = receipt.operation.endedMonoMs;
      if (receipt.outcome === 'unavailable') {
        ensure(['connection-refused', 'connection-reset', 'connect-timeout'].includes(receipt.reason), 'F02.unavailable-reason');
        return { outcome: 'unavailable' as const, operationId: receipt.operation.operationId,
          reason: receipt.reason, observedMonoMs: this.lastEnd };
      }
      equal(receipt.outcome, 'available', 'F02.outcome');
      equal(receipt.atomic, true, 'F02.non-atomic');
      ensure(before.redis.running, 'F02.available-while-stopped');
      equal(receipt.serverRunId, before.redis.serverRunId, 'F02.server-binding');
      return { outcome: 'available' as const, operationId: receipt.operation.operationId,
        observedMonoMs: this.lastEnd, state: decodeRedisRead(selection, receipt.resp) };
    });
  }
  private checkRedisAction(receipt: RedisAction, action: RedisAction['action'], before: StackObservation) {
    validateOperation(this.binding, receipt, 'redis', before.observedMonoMs);
    this.claim(receipt.operationId);
    equal(receipt.action, action, 'F03.redis-action');
    validateObservation(this.binding, receipt.before);
    validateObservation(this.binding, receipt.after);
    equal([receipt.before.redis, receipt.before.gateway], [before.redis, before.gateway], 'F03.pre-action-drift');
    ensure(receipt.before.observedMonoMs >= before.observedMonoMs &&
      receipt.before.observedMonoMs <= receipt.startedMonoMs &&
      receipt.after.observedMonoMs >= receipt.startedMonoMs &&
      receipt.after.observedMonoMs <= receipt.endedMonoMs &&
      receipt.endedMonoMs < receipt.after.leaseExpiresMonoMs, 'F03.inspect-order');
    equal(receipt.after.gateway, before.gateway, 'F03.gateway-changed-during-redis-fault');
    ensure(receipt.after.gateway.running, 'F03.gateway-not-serving');
    equal(receipt.namespaceConnectivity.map(c => c.containerId).sort(),
      [...this.binding.redisClientContainerIds].sort(), 'F03.namespace-inventory');
    for (const c of receipt.namespaceConnectivity) {
      clock(c.observedMonoMs);
      ensure(c.observedMonoMs >= receipt.after.observedMonoMs && c.observedMonoMs <= receipt.endedMonoMs, 'F03.namespace-order');
      equal(c.outcome, action === 'redis.stop' ? 'unreachable' : 'connected', 'F03.namespace-connectivity');
    }
    equal(receipt.after.redis.running, action === 'redis.start', 'F03.redis-state');
    this.lastEnd = receipt.endedMonoMs;
  }
  async stopRedis() {
    return this.exclusive(async () => {
      ensure(!this.stopped, 'F03.already-stopped');
      const before = await this.inspect('redis');
      ensure(before.redis.running && before.gateway.running, 'F03.healthy-baseline');
      const receipt = await this.port.stopRedis(target(this.binding, 'redis'));
      this.checkRedisAction(receipt, 'redis.stop', before);
      this.stopped = structuredClone(receipt);
      return receipt;
    });
  }
  async restoreRedis() {
    return this.exclusive(async () => {
      ensure(this.stopped, 'F03.no-bound-stop');
      const stopped = this.stopped;
      const before = await this.inspect('redis');
      equal(before.redis, stopped.after.redis, 'F03.stopped-state-drift');
      equal(before.gateway, stopped.after.gateway, 'F03.gateway-outage-drift');
      const receipt = await this.port.startRedis(target(this.binding, 'redis'), stopped.operationId,
        this.binding.budgets.REDIS_RECOVERY);
      this.checkRedisAction(receipt, 'redis.start', before);
      ensure(receipt.endedMonoMs - receipt.startedMonoMs <= this.binding.budgets.REDIS_RECOVERY.timeoutMs, 'F03.redis-recovery-deadline');
      ensure(receipt.after.redis.incarnationId !== stopped.before.redis.incarnationId &&
        receipt.after.redis.serverRunId !== stopped.before.redis.serverRunId, 'F03.redis-not-restarted');
      const preservation = await this.port.preservation(target(this.binding, 'redis'), stopped.operationId, receipt.operationId);
      validatePreservation(preservation, stopped.operationId, receipt.operationId,
        [stopped.before.redis.incarnationId, receipt.after.redis.incarnationId]);
      this.stopped = undefined;
      return { stopped, restored: receipt, preservation };
    });
  }
  /** Gateway half only. EE and image roll-forward remain separate runner/main
   * requests. Stage inputs come from F04's real active stream/hold fixture, never
   * MockUpstream's pre-response delay or a made-up run record.
   */
  async restartGateway(live: LiveWorkload) {
    return this.exclusive(async () => {
      ensure(!this.stopped, 'F03.redis-fault-active');
      for (const id of [live.requestId, live.runId, live.streamId, live.holdId]) syntheticId(id);
      clock(live.observedMonoMs);
      ensure(Number.isSafeInteger(live.streamEventCount) && live.streamEventCount > 0 &&
        live.durableHoldObserved === true, 'F03.no-live-workload');
      const before = await this.inspect('gateway');
      ensure(before.gateway.running && before.redis.running, 'F03.healthy-baseline');
      equal(live.servingContainerId, before.gateway.containerId, 'F03.wrong-serving-workload');
      ensure(live.observedMonoMs >= this.binding.before.observedMonoMs &&
        live.observedMonoMs <= before.observedMonoMs, 'F03.live-order');
      const t = target(this.binding, 'gateway');
      const stages: GatewayStage[] = [];
      const check = (s: GatewayStage, name: GatewayStage['stage'], previous: string) => {
        validateOperation(this.binding, s, 'gateway', stages.at(-1)?.endedMonoMs ?? before.observedMonoMs);
        this.claim(s.operationId);
        equal([s.stage, s.previousOperationId, s.outcome], [name, previous, 'completed'], 'F03.stage-refused-or-unordered');
        validateObservation(this.binding, s.before); validateObservation(this.binding, s.after);
        equal(s.before.redis, before.redis, 'F03.redis-changed-during-gateway');
        equal(s.after.redis, before.redis, 'F03.redis-changed-during-gateway');
        const prior = stages.at(-1);
        equal(s.before.gateway, prior?.after.gateway ?? before.gateway, 'F03.pre-stage-drift');
        ensure(s.before.observedMonoMs >= (prior?.endedMonoMs ?? before.observedMonoMs) &&
          s.before.observedMonoMs <= s.startedMonoMs && s.after.observedMonoMs >= s.startedMonoMs &&
          s.after.observedMonoMs <= s.endedMonoMs && s.endedMonoMs < s.after.leaseExpiresMonoMs, 'F03.stage-inspect-order');
        equal(s.admission, name === 'ready' ? 'open' : 'closed', 'F03.admission');
        if (name === 'ready') {
          ensure(s.readiness !== null, 'F03.missing-readiness');
          equal([s.readiness.path, s.readiness.status, s.readiness.syncComplete], ['/readyz', 200, true], 'F03.readiness');
          clock(s.readiness.observedMonoMs);
          clock(s.admissionOpenedMonoMs);
          ensure(s.readiness.observedMonoMs >= s.startedMonoMs &&
            s.readiness.observedMonoMs <= s.admissionOpenedMonoMs &&
            s.admissionOpenedMonoMs <= s.after.observedMonoMs, 'F03.readiness-order');
        } else equal([s.readiness, s.admissionOpenedMonoMs], [null, null], 'F03.unexpected-readiness');
        ensure(s.preCloseRunIds.length > 0 && new Set(s.preCloseRunIds).size === s.preCloseRunIds.length &&
          s.preCloseRunIds.includes(live.runId), 'F03.pre-close-cohort');
        s.preCloseRunIds.forEach(syntheticId);
        if (stages.length) equal([...s.preCloseRunIds].sort(), [...stages[0].preCloseRunIds].sort(), 'F03.run-cohort-drift');
        if (name !== 'close') {
          equal(s.accountedRuns.map(r => r.runId).sort(), [...s.preCloseRunIds].sort(), 'F03.unaccounted-drain');
          ensure(s.accountedRuns.every(r => ['durable-handoff', 'terminal'].includes(r.outcome)), 'F03.run-outcome');
        }
        if (['revalidate', 'restart', 'ready'].includes(name)) equal(s.authoritativeStateUnchanged, true, 'F03.state-drift');
        if (['close', 'drain', 'revalidate'].includes(name)) equal(s.after.gateway, before.gateway, 'F03.early-restart');
        stages.push(s); this.lastEnd = s.endedMonoMs;
      };
      const close = await this.port.closeGateway(t, live);
      check(close, 'close', live.runId);
      const drain = await this.port.drainGateway(t, close.operationId, this.binding.budgets.GATEWAY_DRAIN);
      check(drain, 'drain', close.operationId);
      ensure(drain.endedMonoMs - close.endedMonoMs <= this.binding.budgets.GATEWAY_DRAIN.timeoutMs, 'F03.drain-deadline');
      const revalidated = await this.port.revalidateGateway(t, drain.operationId);
      check(revalidated, 'revalidate', drain.operationId);
      const restart = await this.port.restartGateway(t, revalidated.operationId);
      check(restart, 'restart', revalidated.operationId);
      ensure(restart.after.gateway.running && restart.after.gateway.incarnationId !== before.gateway.incarnationId, 'F03.gateway-not-restarted');
      const ready = await this.port.awaitGatewayReady(t, restart.operationId, this.binding.budgets.GATEWAY_RECOVERY);
      check(ready, 'ready', restart.operationId);
      equal(ready.after.gateway, restart.after.gateway, 'F03.ready-incarnation');
      ensure(ready.endedMonoMs - restart.startedMonoMs <= this.binding.budgets.GATEWAY_RECOVERY.timeoutMs, 'F03.gateway-recovery-deadline');
      const preservation = await this.port.preservation(t, close.operationId, ready.operationId);
      validatePreservation(preservation, close.operationId, ready.operationId, [before.redis.incarnationId]);
      return { stages, preservation };
    });
  }
}
