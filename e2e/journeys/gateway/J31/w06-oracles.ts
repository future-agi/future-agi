import { expect } from '@playwright/test';
import type { StateProbe } from '../../../lib/state-probe';
import type { GatewayAdmin } from '../../../lib/gateway-admin';
import { readConfigVersions, readCreditAccount } from '../_shared/postgres-config';
import { readGatewayCredits } from '../_shared/gateway-state';
import type { RequestRecord } from '../_shared/postgres-requests';
import { requireUuid } from '../_shared/identifiers';

// Independent integer oracle for one fixed-unit, single managed attempt only.
// Provenance: internal/plugins/cost/micros.go:usdToMicros, scaleBps,
// billedAndLanded and formatMicros; request_billing.go:writeRequestBilling.
// It does not import a product pricing function and does not read expected
// prices from a response. The admitted snapshot must pin exact decimal USD,
// quantity, no extra token/fee component and the A08-required 5% markup.
// Multi-attempt/token-mixed cards are NOT qualified by this helper.
const MICROS_PER_USD = 1_000_000n;
const INT64_MAX = 9_223_372_036_854_775_807n;

function decimal(value: string): { units: bigint; scale: bigint } {
  if (typeof value !== 'string' || value.length > 40 ||
      !/^(0|[1-9]\d*)(?:\.\d{1,12})?$/.test(value)) {
    throw new Error('J31 oracle needs an exact nonnegative decimal USD string, at most 12 places');
  }
  const [whole, fraction = ''] = value.split('.');
  return { units: BigInt(whole + fraction), scale: 10n ** BigInt(fraction.length) };
}
function roundHalfUp(numerator: bigint, denominator: bigint): bigint {
  return (2n * numerator + denominator) / (2n * denominator);
}
function bounded(value: bigint): bigint {
  if (value < 0n || value > INT64_MAX) throw new Error('J31 oracle amount exceeds the admitted int64 range');
  return value;
}

export function managedUnitCost(unitPriceUsd: string, quantity: number) {
  if (!Number.isSafeInteger(quantity) || quantity < 1) throw new Error('J31 quantity must be a positive safe integer');
  const price = decimal(unitPriceUsd);
  const upstreamMicros = bounded(roundHalfUp(price.units * BigInt(quantity) * MICROS_PER_USD, price.scale));
  const billedMicros = bounded(roundHalfUp(upstreamMicros * 10_500n, 10_000n));
  return { upstreamMicros, billedMicros, markupMicros: billedMicros - upstreamMicros };
}

export function usdFromMicros(value: bigint): string {
  bounded(value);
  return `${value / MICROS_PER_USD}.${(value % MICROS_PER_USD).toString().padStart(6, '0')}`;
}

// PG request_log.cost has six decimals; cost_upstream/cost_markup have eight.
// Permit trailing zero sub-micros, never truncate a nonzero fractional micro.
// Source: futureagi/agentcc/models/request_log.py:AgentccRequestLog fields.
export function exactMicros(value: string): bigint {
  const parsed = decimal(value);
  const numerator = parsed.units * MICROS_PER_USD;
  if (numerator % parsed.scale !== 0n) throw new Error('J31 observed cost has non-integral micros');
  return bounded(numerator / parsed.scale);
}

export type CostLog = Pick<RequestRecord,
  'request_id' | 'organization_id' | 'workspace_id' | 'model' | 'provider' | 'resolved_model' |
  'status_code' | 'is_stream' | 'is_error' | 'fallback_used' | 'deleted' | 'attempt_count' |
  'billing_mode' | 'cost_source' | 'catalog_snapshot_id' | 'served_upstream_provider' |
  'cost' | 'cost_upstream' | 'cost_markup'>;

// An author-owned expectation, NOT an invented product wire or runner interface.
// Every identity/price below must come from the admitted fixture/snapshot, never
// be copied out of the log under test. AUTHOR.json leaves that binding OWED.
export interface UnitExpectation {
  organizationId: string; workspaceId: string; requestId: string;
  requestedModel: string; servedModel: string; provider: string; servedUpstreamProvider: string;
  snapshotId: string; priceSourceRef: string; unitPriceUsd: string; quantity: number; stream: boolean;
}

/** A08 subassertion only: header, single request-log identity and exact money.
 * A09 ledger, A10 Redis, A07 outputs and the admitted price snapshot are NOT
 * fabricated here. Use readRequests on the minted org/cohort; that reader
 * retains duplicates/tombstones and does not project private body/header data.
 */
export function assertManagedUnitRequest(costHeader: string | null, logs: readonly CostLog[], expected: UnitExpectation): void {
  requireUuid(expected.organizationId);
  requireUuid(expected.workspaceId);
  for (const value of [expected.requestId, expected.requestedModel, expected.servedModel,
    expected.provider, expected.servedUpstreamProvider, expected.snapshotId, expected.priceSourceRef]) {
    if (typeof value !== 'string' || !value.length) throw new Error('J31 independent price/identity binding missing');
  }
  const oracle = managedUnitCost(expected.unitPriceUsd, expected.quantity);
  // response_evidence.go:setAgentccHeadersForProvider copies cost metadata;
  // request_billing.go renders it with six-decimal formatMicros, not a float.
  expect(costHeader, 'A08 exact x-agentcc-cost').toBe(usdFromMicros(oracle.billedMicros));
  expect(logs, 'A08 exactly one request log, including tombstones').toHaveLength(1);
  const log = logs[0];
  expect({ request_id: log.request_id, organization_id: log.organization_id, workspace_id: log.workspace_id,
    model: log.model, resolved_model: log.resolved_model, provider: log.provider,
    served_upstream_provider: log.served_upstream_provider, catalog_snapshot_id: log.catalog_snapshot_id,
    billing_mode: log.billing_mode, cost_source: log.cost_source, is_stream: log.is_stream,
    status_code: log.status_code, is_error: log.is_error, fallback_used: log.fallback_used,
    deleted: log.deleted, attempt_count: log.attempt_count }, 'A08 exact request identity and billing source').toEqual({
    request_id: expected.requestId, organization_id: expected.organizationId, workspace_id: expected.workspaceId,
    model: expected.requestedModel, resolved_model: expected.servedModel, provider: expected.provider,
    served_upstream_provider: expected.servedUpstreamProvider, catalog_snapshot_id: expected.snapshotId,
    billing_mode: 'managed', cost_source: 'catalog', is_stream: expected.stream,
    status_code: 200, is_error: false, fallback_used: false, deleted: false, attempt_count: 1,
  });
  expect(exactMicros(log.cost), 'A08 request cost in integer micros').toBe(oracle.billedMicros);
  if (log.cost_upstream === null || log.cost_markup === null) throw new Error('J31 v2 request money is absent');
  expect(exactMicros(log.cost_upstream), 'A08 upstream snapshot cost').toBe(oracle.upstreamMicros);
  expect(exactMicros(log.cost_markup), 'A08 exact request markup').toBe(oracle.markupMicros);
}

/** A04/A05/A06 exact-set subassertion. Preserve multiplicity; the independent
 * expected list may NOT be derived from actual. This compares the specified
 * surface's complete IDs, not a count or a filtered retained-only intersection.
 * Catalog listing and managed offer are different surfaces; caller pins which.
 */
export function assertExactModelIds(actual: readonly string[], expected: readonly string[]): void {
  if (!expected.length || new Set(expected).size !== expected.length ||
      [...actual, ...expected].some(id => typeof id !== 'string' || !id.length)) {
    throw new Error('J31 exact model comparison needs independent nonempty unique expected IDs');
  }
  expect([...actual].sort(), 'A05/A06 exact model IDs including multiplicity').toEqual([...expected].sort());
}

// Reuse only the foundation's real source-pinned READS. Do not treat gateway
// aggregate held_micros/outbox_pending as Redis request/settled-marker authority.
export async function readOrgBProjection(probe: Pick<StateProbe, 'pg'>, admin: GatewayAdmin, organizationId: string) {
  requireUuid(organizationId);
  const configVersions = await readConfigVersions(probe, organizationId);
  const creditAccount = await readCreditAccount(probe, organizationId);
  const gatewayCredit = await readGatewayCredits(admin, organizationId);
  if (!configVersions.length || creditAccount.length !== 1) {
    throw new Error('NOT_WALKED J31: org B baseline configuration/account not provisioned');
  }
  return { organizationId, configVersions, creditAccount, gatewayCredit };
}
export type OrgBProjection = Awaited<ReturnType<typeof readOrgBProjection>>;

/** Only the available A12 PG/config-version/aggregate projection; full catalog,
 * config-section equality, request visibility, Redis and ledger still OWED.
 * Snapshot B immediately before AND after every positive/refusal/control family.
 */
export function assertOrgBProjectionUnchanged(before: OrgBProjection, after: OrgBProjection): void {
  requireUuid(before.organizationId);
  requireUuid(after.organizationId);
  expect(after, 'A12 org B observable state unchanged').toEqual(before);
}
