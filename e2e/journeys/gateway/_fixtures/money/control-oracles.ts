import { expect } from '@playwright/test';
import type { AttemptRecord, RequestRecord } from '../../_shared/postgres-requests';
import { requireCohort } from '../../_shared/identifiers';

// Safe projected fields pinned by _shared/postgres-requests.ts at da4c81cc62:
// futureagi/agentcc/models/request_{log,attempt}.py; 1-based attempt ID/order
// from agentcc-gateway/internal/models/attempt.go:AddAttempt. These detectors
// cannot prove private monetary effects or that any product request ran.
export type AttemptProjection = Pick<AttemptRecord, 'request_id' | 'attempt_id' | 'index' |
  'kind' | 'outcome' | 'provider' | 'model' | 'status_code'>;
export type UsageProjection = Pick<RequestRecord, 'request_id' | 'model' | 'input_tokens' |
  'output_tokens' | 'total_tokens' | 'usage_estimated'>;

export function assertAttemptInventory(actual: readonly AttemptProjection[], expected: readonly AttemptProjection[]) {
  requireCohort(expected.map(row => row.attempt_id));
  // Exact order and multiplicity, NOT a sorted set (reversal must fail).
  const project = (rows: readonly AttemptProjection[]) => rows.map(row => ({ request_id: row.request_id,
    attempt_id: row.attempt_id, index: row.index, kind: row.kind, outcome: row.outcome,
    provider: row.provider, model: row.model, status_code: row.status_code }));
  expect(project(actual), 'MONEY-CONTROL attempts exact order').toEqual(project(expected));
}
export function assertUsageInventory(actual: readonly UsageProjection[], expected: readonly UsageProjection[]) {
  requireCohort(expected.map(row => row.request_id));
  const project = (rows: readonly UsageProjection[]) => rows.map(row => ({ request_id: row.request_id,
    model: row.model, input_tokens: row.input_tokens, output_tokens: row.output_tokens,
    total_tokens: row.total_tokens, usage_estimated: row.usage_estimated }))
    .sort((a, b) => a.request_id.localeCompare(b.request_id));
  expect(project(actual), 'MONEY-CONTROL usage exact inventory').toEqual(project(expected));
}
/** Fixture call counter only. Exact HTTP semantic refusal and ledger absence
 * remain separate mandatory assertions; this function does not prove either. */
export function assertNoDispatch(count: number) {
  expect(count, 'MONEY-CONTROL refused request no dispatch').toBe(0);
}

// Synthetic identity projection for detector fallibility, NOT a private schema,
// query or accepted F05 adapter. Never deserialize a ledger response into this
// until the money owner supplies and reviews its REAL mapping.
export interface MoneyIdentity { id: string; requestId: string; effectMicros: string }
export function assertMoneyIdentityInventory(actual: readonly MoneyIdentity[], expected: readonly MoneyIdentity[]) {
  requireCohort(expected.map(row => row.id));
  const order = (rows: readonly MoneyIdentity[]) => rows.map(row => ({ id: row.id,
    requestId: row.requestId, effectMicros: row.effectMicros })).sort((a, b) => a.id.localeCompare(b.id));
  expect(order(actual), 'MONEY-CONTROL money exact identities').toEqual(order(expected));
}
