import { readRequests, readAttempts } from '../../_shared/postgres-requests';
import { readGatewayCredits } from '../../_shared/gateway-state';
import { requireCohort, requireId, requireUuid } from '../../_shared/identifiers';

export const PROJECTION_SOURCE = 'da4c81cc623f909ce38f53e01b557ee8374d5adb';
export const MAIN_OWNED = Object.freeze([
  'durable_debit', 'durable_release', 'durable_settlement', 'ledger_idempotency_key',
  'ledger_snapshot', 'ledger_provider', 'ledger_cost_source', 'ledger_markup',
  'immutable_financial_id', 'complete_financial_journal',
] as const);
export const REDIS_OWED = 'F02_ACCEPTED_READ_ONLY_ENTRY_POINT' as const;

type Probe = Parameters<typeof readRequests>[0];
type Admin = Parameters<typeof readGatewayCredits>[0];
export interface ProjectionScope {
  readonly candidateHead: string;
  readonly databaseSchema: string;
  readonly organizationId: string;
  readonly requestIds: readonly string[];
}

// Public model columns only. The foundation's readers also select other known
// fields (including staff-only landed costs); narrow BEFORE executing SQL, not
// merely before attaching results. Reuse their cohort predicates and ordering.
const COLUMNS = {
  agentcc_request_log: [
    'id::text', 'request_id', 'organization_id::text', 'model', 'provider',
    'cost::text', 'cost_upstream::text', 'cost_markup::text', 'billing_mode',
    'cost_source', 'usage_estimated', 'served_upstream_provider',
    'catalog_snapshot_id', 'attempt_count', 'deleted',
  ],
  agentcc_request_attempt: [
    'attempt_id', 'request_id', '"index"', 'kind', 'outcome', 'provider', 'model',
    'upstream_cost_micros::text', 'billed_cost_micros::text', 'cost_source',
    'billable', 'usage_estimated',
  ],
} as const;

/** Schema is the main F05 MoneyScope's admitted databaseSchema, never an assumed
 * public schema or search_path. This adapter is private to the two pinned shared
 * readers; a changed query shape fails closed. No connection or SQL is supplied
 * by this module at import time. Fixture admission still owns DB/tenant access.
 */
function publicProbe(probe: Probe, schema: string): Probe {
  if (typeof schema !== 'string' || !/^[a-z_][a-z0-9_]{0,62}$/.test(schema)) throw new Error('F05_SCHEMA');
  return {
    pg: (sql, values) => {
      const match = /^SELECT\s+([\s\S]+?)\s+FROM (agentcc_request_log|agentcc_request_attempt)\s+WHERE ([\s\S]+)$/.exec(sql.trim());
      if (!match) throw new Error('F05_SHARED_QUERY_CHANGED');
      const table = match[2] as keyof typeof COLUMNS;
      const tail = table === 'agentcc_request_log'
        ? 'organization_id = $1 AND request_id = ANY($2::text[]) ORDER BY request_id, created_at, id'
        : 'organization_id = $1 AND request_id = ANY($2::text[]) ORDER BY request_id, "index", attempt_id';
      if (match[3].replace(/\s+/g, ' ').trim() !== tail) throw new Error('F05_SHARED_QUERY_CHANGED');
      const source = match[1].split(',').map(column => column.trim());
      const columns = COLUMNS[table];
      if (columns.some(column => source.filter(found => found === column).length !== 1)) {
        throw new Error('F05_SHARED_COLUMNS_CHANGED');
      }
      return probe.pg(`SELECT ${columns.join(', ')} FROM "${schema}"."${table}" WHERE ${tail}`, values);
    },
  };
}

function text(value: unknown, max = 255): string {
  if (typeof value !== 'string' || value.length > max || /[\r\n\u0000]/.test(value)) {
    throw new Error('F05_TEXT');
  }
  return value;
}
function nullableText(value: unknown, max = 255): string | null {
  return value === null ? null : text(value, max);
}
function flag(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new Error('F05_BOOLEAN');
  return value;
}
function count(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) {
    throw new Error('F05_COUNT');
  }
  return value;
}
function micros(value: unknown): string | null {
  if (value === null) return null;
  if (typeof value !== 'string' || !/^-?(0|[1-9][0-9]{0,18})$/.test(value)) {
    throw new Error('F05_INTEGER_MICROS');
  }
  const n = BigInt(value);
  if (n < -9223372036854775808n || n > 9223372036854775807n) throw new Error('F05_INT64');
  return value;
}

/** Preserve sub-micro log precision, NULL and signed amounts. usdE8 is an exact
 * integer count of 10^-8 USD (one hundredth of a micro), NOT a debit. A consumer
 * compares micros * 100n to BigInt(usdE8); it must not round a mismatch away.
 */
function money(value: unknown, scale: 6 | 8) {
  if (value === null) return null;
  if (typeof value !== 'string') throw new Error('F05_DECIMAL_STRING');
  const match = /^(-?)(0|[1-9][0-9]{0,7})(?:\.([0-9]{1,8}))?$/.exec(value);
  if (!match || (match[3]?.length ?? 0) > scale || match[2].length > (scale === 6 ? 6 : 8)) {
    throw new Error('F05_DECIMAL_SCALE');
  }
  const atoms = BigInt(match[2]) * 100000000n + BigInt((match[3] ?? '').padEnd(8, '0'));
  return { usd: value, usdE8: ((match[1] ? -1n : 1n) * atoms).toString() };
}

/** Read only PUBLIC LOG evidence, not the EE ledger. Call at an admitted
 * quiescent barrier; these two SELECTs are not an atomic money snapshot. Keep
 * missing rows, tombstones and multiplicity observable. No deduplication, zero
 * fallback, ledger mock, retries, wait, grants, settlement, ACK or Redis commands.
 * Candidate sources: request_log.py:19-109,120-171; request_attempt.py:48-92;
 * serializers/request_log.py:126-180 at PROJECTION_SOURCE.
 */
export async function readLedgerProjection(probe: Probe, scope: ProjectionScope) {
  if (scope.candidateHead !== PROJECTION_SOURCE) throw new Error('F05_CANDIDATE_UNBOUND');
  const org = requireUuid(scope.organizationId).toLowerCase();
  const requestIds = requireCohort(scope.requestIds);
  const cohort = new Set(requestIds);
  const databaseSchema = scope.databaseSchema;
  const reader = publicProbe(probe, databaseSchema);
  const requests = await readRequests(reader, org, requestIds);
  const attempts = await readAttempts(reader, org, requestIds);
  const requestRows = requests.map(row => {
    if (requireUuid(row.organization_id).toLowerCase() !== org || !cohort.has(row.request_id)) {
      throw new Error('F05_REQUEST_SCOPE');
    }
    if (row.cost === null) throw new Error('F05_REQUIRED_BILLED_COST');
    return {
      rowId: requireUuid(row.id), requestId: requireId(row.request_id),
      model: text(row.model), provider: text(row.provider),
      servedUpstreamProvider: nullableText(row.served_upstream_provider, 64),
      billingMode: nullableText(row.billing_mode, 16),
      billed: money(row.cost, 6), upstream: money(row.cost_upstream, 8),
      markup: money(row.cost_markup, 8), costSource: nullableText(row.cost_source, 32),
      catalogSnapshotId: nullableText(row.catalog_snapshot_id, 32),
      usageEstimated: row.usage_estimated === null ? null : flag(row.usage_estimated),
      attemptCount: count(row.attempt_count), deleted: flag(row.deleted),
    };
  });
  const attemptRows = attempts.map(row => {
    if (!cohort.has(row.request_id)) throw new Error('F05_ATTEMPT_SCOPE');
    return {
      // hook_attempts.go and models/attempt.go:198-225,284-298 on the pinned
      // candidate use COMPACTABLE slots. Never derive financialId from these.
      requestId: requireId(row.request_id), attemptId: text(row.attempt_id, 300),
      index: count(row.index), kind: text(row.kind, 32), outcome: text(row.outcome, 16),
      provider: text(row.provider), model: text(row.model),
      upstreamMicros: micros(row.upstream_cost_micros), billedMicros: micros(row.billed_cost_micros),
      costSource: text(row.cost_source, 32), billable: flag(row.billable),
      usageEstimated: flag(row.usage_estimated),
    };
  });
  const observed = new Set(requestRows.map(row => row.requestId));
  return {
    schema: 'f05-public-log-capture/v1', sourceHead: PROJECTION_SOURCE,
    authority: 'PUBLIC_LOG_ONLY', productAccepted: false, moneyReconciled: false,
    organizationId: org, requestIds,
    tables: {
      requests: `"${databaseSchema}"."agentcc_request_log"`,
      attempts: `"${databaseSchema}"."agentcc_request_attempt"`,
    },
    requests: requestRows, attempts: attemptRows,
    missingRequestIds: requestIds.filter(id => !observed.has(id)),
    mainOwned: MAIN_OWNED, redisReadBinding: REDIS_OWED,
  } as const;
}

/** Separate aggregate observation; never infer per-request settlement/absence,
 * durable account existence, or commit-before-ACK from this admin response.
 * Same synthetic organization and admitted local admin fixture only.
 */
export async function readHotPathAccount(admin: Admin, organizationId: string) {
  return readGatewayCredits(admin, requireUuid(organizationId).toLowerCase());
}
