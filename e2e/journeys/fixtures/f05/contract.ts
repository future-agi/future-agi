import { fail, list, loopback, record, uuid } from './validation.ts';

export const F05_CANDIDATE = Object.freeze({
  head: 'a0372df842d35c91c0c6fa9a98a96c6199ccd436',
  runtimeReady: false,
  projectionBinding: 'SOURCE-MAPPED-PARTIAL-REVIEW-OWED',
  financialReviewer: 'UNAPPOINTED: main WALKS-D2 must name independent PORT-708J-W02 G10/G12 reviewer',
});

// Public consumer requirements, NOT private model columns or an accepted capture.
// Producer must map these semantics to its real source and publish a reviewed
// non-secret projection before any SQL for request money can be emitted.
export const F05_PROJECTION_REQUIREMENTS = Object.freeze({
  version: 'f05-projection-requirements/v1',
  status: 'PROPOSED-NOT-ADMITTED',
  request: Object.freeze([
    'organization/person/api-key/run/request attribution',
    'estimate/hold/debit/release/settlement/refund integer micros',
    'idempotency identity and exactly-once terminal count',
    'catalog_snapshot_id and policy_version at dispatch',
    'provider and cost_source at settlement',
    'markup_micros and markup_bps at settlement',
    'real worker commit-before-ack/replay observations',
  ]),
  account: Object.freeze(['updated_at', 'version', 'gateway_acked_version', 'push outcome/time']),
  statement: Object.freeze(['complete opening history and month cohort', 'all credit kinds',
    'spend grouped by model AND usage_estimated', 'platform usage_debit in closing balance']),
});

export interface MoneyScope {
  readonly candidateHead: string;
  readonly runId: string;
  readonly organizationId: string;
  readonly personId: string;
  readonly apiKeyId: string;
  readonly keyType: 'user';
  readonly fixtureKind: 'test-owned-loopback';
  readonly apiOrigin: string;
  readonly databaseSchema: string;
  readonly requestIds: readonly string[];
}
const scopes = new WeakSet<object>();

/** Planning scope only: caller-supplied IDs do not establish actual ownership. */
export function moneyScope(input: unknown): MoneyScope {
  const r = record(input, ['candidateHead', 'runId', 'organizationId', 'personId', 'apiKeyId',
    'keyType', 'fixtureKind', 'apiOrigin', 'databaseSchema', 'requestIds']);
  if (r.candidateHead !== F05_CANDIDATE.head) return fail('CANDIDATE-UNBOUND');
  if (r.keyType !== 'user' || r.fixtureKind !== 'test-owned-loopback') return fail('IDENTITY');
  if (typeof r.databaseSchema !== 'string' || !/^[a-z_][a-z0-9_]{0,62}$/.test(r.databaseSchema)) return fail('SCHEMA');
  const requests = list(r.requestIds, 200).map(uuid);
  if (!requests.length || new Set(requests).size !== requests.length) return fail('COHORT');
  const scope: MoneyScope = Object.freeze({
    candidateHead: F05_CANDIDATE.head, runId: uuid(r.runId), organizationId: uuid(r.organizationId),
    personId: uuid(r.personId), apiKeyId: uuid(r.apiKeyId), keyType: 'user',
    fixtureKind: 'test-owned-loopback', apiOrigin: loopback(r.apiOrigin),
    databaseSchema: r.databaseSchema, requestIds: Object.freeze(requests),
  });
  scopes.add(scope);
  return scope;
}

export function requireScope(scope: MoneyScope): void {
  if (!scope || typeof scope !== 'object' || !scopes.has(scope)) fail('SCOPE');
}

/** No callbacks, overrides or environment flags can turn an early tuple ready. */
export function requireF05Runtime(candidateHead: unknown): never {
  if (candidateHead !== F05_CANDIDATE.head) return fail('CANDIDATE-UNBOUND');
  return fail('PRIVATE-PROJECTION-UNBOUND');
}

// Optional explicit source-projection binding opens only a fixed SELECT plan.
// The legacy one-argument call remains closed; runtime admission above is not
// changed. Missing release/void/person/R14 authority is never fabricated.
export { privateLedgerQuery } from './private-plan.ts';
