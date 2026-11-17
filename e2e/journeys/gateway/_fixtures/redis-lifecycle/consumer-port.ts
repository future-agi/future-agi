/** Consumer-owned F02/F03 extension request, NOT an already accepted runner ABI.
 * E0/E6 `fixtures` slot authority: CONSUMER-ABI-REQUEST.json sha256
 * b78a171fea942f88d1900ae4e3388e40a11593397ca733441abe4759daf6e803.
 * That request does not define lifecycle or Redis methods. The runtime owner
 * must supply/qualify this port (or an explicit reviewed adapter) in its actual
 * CONSUMER-ABI.json before use. Never guess an HTTP route, Docker argv, global
 * symbol, environment binding or fallback. No executor is implemented here.
 *
 * Trust boundary: runner alone resolves IDs, connects on inspected loopback,
 * enforces the exclusive lease at EVERY operation and emits native originals.
 * Type shapes and consumer checks do NOT authenticate fabricated receipts.
 * Bind this module by the shipment root; do not ship a bulk digest manifest.
 */
import type { RedisReadPlan } from './redis-probe';

export const SOURCE_REF = 'da4c81cc623f909ce38f53e01b557ee8374d5adb';
export const PORT_SCHEMA = 'gateway-redis-lifecycle-consumer/v1';
export type Role = 'redis' | 'gateway';
export interface Deadline {
  timeoutMs: number;
  contractRef: string; // Accepted product/lifecycle contract, not an invented SLA.
}
export interface Identity {
  containerId: string; imageId: string; incarnationId: string;
  running: boolean;
}
export interface RedisIdentity extends Identity {
  volumeId: string;
  persistencePolicyId: string; // Value-free policy ID, never secret-config digest.
  serverRunId: string | null;
}
export interface StackObservation {
  invocationId: string; stackId: string; leaseId: string;
  observedMonoMs: number; leaseExpiresMonoMs: number;
  exclusive: true; workers: 1; overlappingInvocationIds: string[];
  servingGatewayIds: string[]; autoscalingEnabled: false;
  gateway: Identity; redis: RedisIdentity;
}
export interface Binding {
  schema: typeof PORT_SCHEMA;
  sourceRef: typeof SOURCE_REF;
  sourceClean: true;
  invocationId: string; stackId: string; leaseId: string;
  browser: 'chromium' | 'firefox';
  redisClientContainerIds: string[]; // Exact dependency graph, not just the gateway.
  effectiveConfig: {
    attestationId: string; managedRequireRedis: true;
    agentBudgetPrefix: string; redisDatabase: number;
    redisLoopbackEndpoint: string;
    redisDependencyContainerId: string;
  };
  before: StackObservation;
  budgets: { REDIS_RECOVERY: Deadline; GATEWAY_DRAIN: Deadline; GATEWAY_RECOVERY: Deadline };
}
export interface Target {
  invocationId: string; stackId: string; leaseId: string;
  containerId: string; imageId: string;
}
export interface Operation {
  operationId: string; target: Target;
  startedMonoMs: number; endedMonoMs: number;
  rawWait: { exitCode: number | null; signal: string | null };
  // Complete originals are retained by runner; consumer only carries their ID.
  originalReceiptId: string;
}
export interface Preservation {
  fromOperationId: string; toOperationId: string;
  coverage: 'complete-native-command-and-fixture-ledger';
  // Continuous across every incarnation; INFO commandstats alone resets at restart.
  redisIncarnationIds: string[];
  commandCounts: { name: 'FLUSHDB' | 'FLUSHALL'; count: number }[];
  resetCount: number; reseedCount: number;
}
export interface RedisAction extends Operation {
  action: 'redis.stop' | 'redis.start';
  before: StackObservation; after: StackObservation;
  namespaceConnectivity: { containerId: string; outcome: 'connected' | 'unreachable'; observedMonoMs: number }[];
}
export type RedisRead = {
  operation: Operation; containerId: string; database: number;
} & ({ outcome: 'available'; serverRunId: string; atomic: true; resp: unknown } |
  { outcome: 'unavailable'; reason: 'connection-refused' | 'connection-reset' | 'connect-timeout' });
export interface LiveWorkload {
  requestId: string; runId: string; streamId: string; holdId: string;
  servingContainerId: string; observedMonoMs: number;
  streamEventCount: number; durableHoldObserved: true;
}
export interface GatewayStage extends Operation {
  stage: 'close' | 'drain' | 'revalidate' | 'restart' | 'ready';
  previousOperationId: string;
  outcome: 'completed' | 'refused';
  before: StackObservation; after: StackObservation;
  admission: 'closed' | 'open';
  admissionOpenedMonoMs: number | null;
  preCloseRunIds: string[];
  accountedRuns: { runId: string; outcome: 'durable-handoff' | 'terminal' }[];
  authoritativeStateUnchanged: boolean;
  // /readyz is pinned at internal/server/server.go:readyHandler at SOURCE_REF.
  // Runner checks configured readiness AND sync before allowing open admission.
  readiness: null | { path: '/readyz'; status: 200; syncComplete: true; observedMonoMs: number };
}

export interface RedisLifecyclePort {
  schema: typeof PORT_SCHEMA;
  // Calls are runner capabilities, not pass-producing callbacks. Implementations
  // must reject wrong/expired leases, non-serving IDs, source drift and overlapping
  // schedules before I/O. Runner must retain stdout/stderr unfiltered separately.
  binding(): Promise<Binding>;
  inspect(target: Target): Promise<StackObservation>;
  readRedis(target: Target, plan: RedisReadPlan): Promise<RedisRead>;
  stopRedis(target: Target): Promise<RedisAction>;
  startRedis(target: Target, stoppedOperationId: string, recovery: Deadline): Promise<RedisAction>;
  preservation(target: Target, fromOperationId: string, toOperationId: string): Promise<Preservation>;
  closeGateway(target: Target, live: LiveWorkload): Promise<GatewayStage>;
  drainGateway(target: Target, closeOperationId: string, deadline: Deadline): Promise<GatewayStage>;
  revalidateGateway(target: Target, drainOperationId: string): Promise<GatewayStage>;
  // Must atomically recheck the attested state/parts/lease and revalidation token
  // before mutation, refusing drift. Consumer-side checks cannot close that race.
  restartGateway(target: Target, revalidationOperationId: string): Promise<GatewayStage>;
  awaitGatewayReady(target: Target, restartOperationId: string, deadline: Deadline): Promise<GatewayStage>;
}
