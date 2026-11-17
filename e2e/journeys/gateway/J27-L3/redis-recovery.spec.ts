import { expect, type TestType } from '@playwright/test';
import { assertionRow, attachSeededIds, journeyAnnotations, journeyTitle } from '../_shared/contract';
import type { RedisLifecycleFixture, RedisSelection } from '../_fixtures/redis-lifecycle';

export interface RedisRecoverySeed {
  selection: RedisSelection;
  heldIds: string[];
  settledIds: string[];
}
export type RedisRecoveryFixtures = {
  redisLifecycle: RedisLifecycleFixture;
  redisRecoverySeed: RedisRecoverySeed;
};

/** Source-authored L3-A06 row implementation. Intentionally an explicit registrar,
 * NOT a zero-test success or fallback to lib/fixtures's legacy org keys.
 * Consumer ABI currently has no callable injection/registration contract.
 * F01 owner must call this registrar with its E6 extended test and inventory the
 * two titles below, or reject the unbound invocation BEFORE fault execution.
 * F05/F09 supply real synthetic personal-key/funding seeds; none are auto-minted.
 * No source row is promoted from OWED until those bindings and native C03 exist.
 */
export function registerRedisRecoveryCases(test: TestType<RedisRecoveryFixtures, {}>) {
  for (const phase of ['baseline', 'restored'] as const) {
    test(journeyTitle('J27-L3', 'BOUND-REDIS-RECOVERY', phase), {
      tag: ['@journey-J27-L3', '@redis-lifecycle'],
      annotation: journeyAnnotations('J27-L3', ['L3-A06'], phase,
        phase === 'restored' ? { id: 'L3-C01-REAL-OUTAGE' } : undefined),
    }, async ({ redisLifecycle: fixture, redisRecoverySeed: seed }, info) => {
      const b = fixture.bound;
      // Recovery uses the reviewed contract deadline, not POLL.GATEWAY_SYNC.
      // Overall invocation/setup/stop/read budget remains a runner binding F19.
      await attachSeededIds(info, 'J27-L3', { organizationIds: [seed.selection.organizationId],
        holdIds: seed.selection.holdIds });
      expect(seed.heldIds.length).toBeGreaterThan(0);
      expect(seed.settledIds.length).toBeGreaterThan(0);
      expect([...seed.heldIds, ...seed.settledIds].sort()).toEqual([...seed.selection.holdIds].sort());
      const before = await fixture.read(seed.selection);
      expect(before.outcome).toBe('available');
      if (before.outcome !== 'available') throw new Error('L3-A06.baseline-unavailable');
      expect(before.state.account).not.toBeNull();
      expect(before.state.holds.map(h => h.holdId).sort()).toEqual([...seed.heldIds].sort());
      expect(before.state.markers.filter(m => m.value === '1').map(m => m.holdId).sort())
        .toEqual([...seed.settledIds].sort());
      // A03 is NOT declared: active managed/MCP post-stop requests remain owed.
      const stopped = await fixture.stopRedis();
      await test.step('L3-A06 prerequisite: actual Redis unavailable, never a fabricated empty state', async () => {
        const fault = await fixture.read(seed.selection);
        expect(fault.outcome).toBe('unavailable');
        expect(Object.hasOwn(fault, 'state')).toBe(false);
        await info.attach('redis-unavailability', { contentType: 'application/json',
          body: JSON.stringify({ row: 'L3-A06', phase, operationId: fault.operationId,
            observedMonoMs: fault.observedMonoMs, outcome: fault.outcome }) });
      });
      await assertionRow(info, 'J27-L3', 'L3-A06', phase, async () => {
        const { restored, preservation } = await fixture.restoreRedis();
        expect([restored.after.redis.containerId, restored.after.redis.volumeId,
          restored.after.redis.persistencePolicyId, restored.after.redis.imageId,
          restored.after.gateway.imageId]).toEqual([
          stopped.before.redis.containerId, stopped.before.redis.volumeId,
          stopped.before.redis.persistencePolicyId, stopped.before.redis.imageId,
          stopped.before.gateway.imageId]);
        expect(restored.after.redis.serverRunId).not.toBe(stopped.before.redis.serverRunId);
        expect(restored.after.redis.incarnationId).not.toBe(stopped.before.redis.incarnationId);
        expect(restored.endedMonoMs - restored.startedMonoMs).toBeLessThanOrEqual(b.budgets.REDIS_RECOVERY.timeoutMs);
        expect(preservation.commandCounts.map(c => [c.name, c.count]).sort())
          .toEqual([['FLUSHALL', 0], ['FLUSHDB', 0]]);
        expect([preservation.resetCount, preservation.reseedCount]).toEqual([0, 0]);
        const after = await fixture.read(seed.selection);
        expect(after.outcome).toBe('available');
        if (after.outcome !== 'available') throw new Error('L3-A06.restored-unavailable');
        expect(after.state.organizationId).toBe(seed.selection.organizationId);
        await info.attach('redis-recovery', { contentType: 'application/json', body: JSON.stringify({
          row: 'L3-A06', phase, stoppedOperationId: stopped.operationId,
          restoredOperationId: restored.operationId, afterReadOperationId: after.operationId,
          deadlineContractRef: b.budgets.REDIS_RECOVERY.contractRef,
          recoveredWithinMs: restored.endedMonoMs - restored.startedMonoMs,
          volumeId: restored.after.redis.volumeId, resetCount: preservation.resetCount,
          reseedCount: preservation.reseedCount }) });
      });
    });
  }
}
