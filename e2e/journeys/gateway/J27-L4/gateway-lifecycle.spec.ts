import { expect, type TestType } from '@playwright/test';
import { attachSeededIds, journeyTitle } from '../_shared/contract';
import type { RedisLifecycleFixture, LiveWorkload } from '../_fixtures/redis-lifecycle';

type Fixtures = { redisLifecycle: RedisLifecycleFixture; liveGatewayWorkload: LiveWorkload };

/** E6 registrar request; no conditional test skip or alternate runner.
 * F04/F09 must supply actual live-stream/durable-hold fixture, not a synthetic
 * object or MockUpstream's pre-response delay. F03 runtime/HOSTED-OPS must bind
 * real close/drain/revalidate/restart semantics. Public SOURCE_REF exposes only
 * SIGTERM -> Server.Shutdown (cmd/agentcc/main.go:573-616 and
 * internal/server/server.go:850-898), NOT a separate close or revalidate API.
 * The injected port is an explicit runner request, not invented product routes.
 *
 * These source steps support A01/A04/A08 but do not complete them: authoritative
 * run/outcome/parts and full restored money proof are still F04/F05/F09/F19.
 * EE-served/absence is F16. All original ROW_OBLIGATIONS remain unchanged.
 */
export function registerGatewayLifecycleSupport(test: TestType<Fixtures, {}>) {
  for (const phase of ['baseline', 'restored'] as const) {
    test(journeyTitle('J27-L4', 'BOUND-GATEWAY-LIFECYCLE-SUPPORT', phase), {
      tag: ['@journey-J27-L4', '@lifecycle-support-only'],
      annotation: ['L4-A01', 'L4-A04', 'L4-A08'].map(row => ({
        type: 'journey-fixture-support', description: JSON.stringify({ packet: 'J27-L4', row,
          phase, fixture: 'redis-lifecycle', fullRowCovered: false, status: 'OWED',
          productStatus: 'NOT_WALKED' }),
      })),
    }, async ({ redisLifecycle: fixture, liveGatewayWorkload: live }, info) => {
      await attachSeededIds(info, 'J27-L4', { requestIds: [live.requestId], runIds: [live.runId], holdIds: [live.holdId] });
      const before = fixture.bound.before;
      expect(live.servingContainerId).toBe(before.gateway.containerId);
      expect(live.streamEventCount).toBeGreaterThan(0);
      expect(live.durableHoldObserved).toBe(true);
      const result = await test.step('L4-A04 support: real close, bounded drain, revalidate, restart, ready',
        () => fixture.restartGateway(live));
      expect(result.stages.map(s => s.stage)).toEqual(['close', 'drain', 'revalidate', 'restart', 'ready']);
      const [close, drain, revalidate, restart, ready] = result.stages;
      await test.step('L4-A01 support: exact serving incarnation and unchanged immutable image', async () => {
        expect(restart.target.containerId).toBe(live.servingContainerId);
        expect(restart.after.gateway.incarnationId).not.toBe(before.gateway.incarnationId);
        expect(restart.after.gateway.imageId).toBe(before.gateway.imageId);
      });
      await test.step('L4-A04 support: full pre-close cohort, no drift and chained native operation IDs', async () => {
        expect(drain.accountedRuns.map(r => r.runId).sort()).toEqual([...close.preCloseRunIds].sort());
        expect([drain.previousOperationId, revalidate.previousOperationId, restart.previousOperationId,
          ready.previousOperationId]).toEqual([close.operationId, drain.operationId, revalidate.operationId, restart.operationId]);
        expect(revalidate.authoritativeStateUnchanged).toBe(true);
        expect(drain.endedMonoMs - close.endedMonoMs).toBeLessThanOrEqual(fixture.bound.budgets.GATEWAY_DRAIN.timeoutMs);
      });
      await test.step('L4-A08 support: configured readiness and sync precede reopening', async () => {
        expect(result.stages.map(s => s.admission)).toEqual(['closed', 'closed', 'closed', 'closed', 'open']);
        expect(ready.readiness?.path).toBe('/readyz');
        expect(ready.readiness?.status).toBe(200);
        expect(ready.readiness?.syncComplete).toBe(true);
      });
      await info.attach('gateway-lifecycle-support', { contentType: 'application/json', body: JSON.stringify({
        packet: 'J27-L4', phase, fullRowCovered: false,
        operations: result.stages.map(s => ({ stage: s.stage, operationId: s.operationId,
          originalReceiptId: s.originalReceiptId, startedMonoMs: s.startedMonoMs, endedMonoMs: s.endedMonoMs })),
        streamId: live.streamId, runId: live.runId, holdId: live.holdId,
        noRuntimeAcceptanceClaim: true }) });
    });
  }
}
