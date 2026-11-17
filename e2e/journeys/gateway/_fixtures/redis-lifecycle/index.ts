import type { TestInfo } from '@playwright/test';
import { createRedisLifecycle, type RedisLifecycleFixture } from './fixture';
import type { RedisLifecyclePort } from './consumer-port';

export { createRedisLifecycle, RedisLifecycleFixture } from './fixture';
export { compileRedisRead, decodeRedisRead } from './redis-probe';
export type { RedisSelection, RedisState } from './redis-probe';
export type { Binding, RedisLifecyclePort, LiveWorkload } from './consumer-port';

/** Proposed E6 fixture export. Runner supplies the authenticated per-invocation
 * capability; this module does not load an env path/global object or implement
 * a helper endpoint. Register as acceptedTest.extend({redisLifecycle: ...}).
 * Missing/failed injection must fail the invocation, not omit its specs.
 * The runner must independently retain the lease and restore stopped services
 * even after a consumer error/timeout; this export does not own stack teardown.
 */
export function redisLifecycleFixture(
  capabilityFor: (info: TestInfo) => Promise<RedisLifecyclePort>,
) {
  return async (
    _args: {}, use: (fixture: RedisLifecycleFixture) => Promise<void>, info: TestInfo,
  ) => {
    const fixture = await createRedisLifecycle(await capabilityFor(info));
    const b = fixture.bound;
    // Only allowlisted IDs/status; never attach native raw inspect/config or port.
    await info.attach('redis-lifecycle-binding', { contentType: 'application/json',
      body: JSON.stringify({ schema: 'gateway-redis-lifecycle-binding/v1', sourceRef: b.sourceRef,
        invocationId: b.invocationId, stackId: b.stackId, leaseId: b.leaseId, browser: b.browser,
        gatewayContainerId: b.before.gateway.containerId, redisContainerId: b.before.redis.containerId,
        volumeId: b.before.redis.volumeId, configAttestationId: b.effectiveConfig.attestationId }) });
    await use(fixture);
  };
}
