import { requireScope, type MoneyScope } from './contract.ts';
import { fail, list, record } from './validation.ts';

/** Query preparation only, not independent review or runtime admission.
 * The one-argument legacy privateLedgerQuery remains closed. Only this exact
 * in-process descriptor permits the fixed, source-bound SELECT below. */
export const PRIVATE_PROJECTION = Object.freeze({
  version: 'f05-private-projection/v1',
  candidateHead: 'a0372df842d35c91c0c6fa9a98a96c6199ccd436',
  publicContractSha256: '0a68726266f903edc8a92d5ad36922197d06ba09ac9814a4d67a17bf45839aaf',
  publicReaderHead: '8034fcdfc25d6cbb1e416e4febc89ef2cfa372b1',
  review: 'OWED', execution: 'NOT_RUN', runtimeReady: false,
});

// Actual column types from ee/usage/models/managed_credits.py and migrations
// 0030/0031 at candidateHead. No default db_table override; apps.py:8-14 fixes
// app label usage. Deployed namespace is an explicit scope, never search_path.
// See private-mapping.json for semantic, model:line and migration citations.
export const ENTRY_COLUMNS = Object.freeze({
  id: 'uuid', organization_id: 'uuid', kind: 'varchar', amount_micros: 'int8',
  idempotency_key: 'varchar', request_id: 'varchar', model: 'varchar',
  provider: 'varchar', served_upstream_provider: 'varchar', upstream_cost_micros: 'int8',
  markup_micros: 'int8', markup_bps: 'int4', cost_source: 'varchar', usage_estimated: 'bool',
  catalog_snapshot_id: 'varchar', policy_version: 'varchar', api_key_id: 'varchar',
  agent_run_id: 'varchar', agent_id: 'varchar', workspace_id: 'uuid', source_kind: 'varchar',
  source_ref: 'varchar', price_catalog_version: 'varchar', related_entry_id: 'uuid',
  occurred_at: 'timestamptz', created_at: 'timestamptz',
});
export const ACCOUNT_COLUMNS = Object.freeze({
  organization_id: 'uuid', granted_total_micros: 'int8', settled_spend_micros: 'int8',
  platform_spend_micros: 'int8', version: 'int8', gateway_acked_version: 'int8', entry_seq: 'int8',
  gateway_push_state: 'varchar', gateway_push_state_at: 'timestamptz', updated_at: 'timestamptz',
});
export const REQUIRED_MIGRATIONS = Object.freeze([
  '0030_managed_credit_ledger', '0031_ledger_phase2a',
  '0032_ledger_opening_buckets', '0033_backfill_topup_payment_intent',
]);
const MAPPED = new Set(['per_request_debit', 'settlement', 'idempotency_key', 'snapshot',
  'provider', 'cost_source', 'markup', 'request_identity', 'api_key_id', 'agent_run_id',
  'agent_id', 'workspace_id', 'refund', 'account']);

/** 'settlement' here means committed spend row ONLY, never terminal-void/ACK.
 * Release/hold/person/complete-dispatch identity have NO column at this tuple. */
export function requirePrivateFields(fields: unknown): void {
  const names = list(fields, 32);
  if (!names.length) fail('PRIVATE-FIELD-UNMAPPED');
  for (const name of names) if (typeof name !== 'string' || !MAPPED.has(name)) fail('PRIVATE-FIELD-UNMAPPED');
}
export function requirePrivateProjection(scope: MoneyScope, projection: unknown): void {
  requireScope(scope);
  if (projection !== PRIVATE_PROJECTION) fail('PRIVATE-PROJECTION-UNBOUND');
  if (scope.candidateHead !== PRIVATE_PROJECTION.candidateHead) fail('CANDIDATE-UNBOUND');
}

function selectColumns(columns: Record<string, string>): string {
  return Object.entries(columns).map(([name, type]) => {
    if (type === 'timestamptz') return `pg_catalog.to_char("${name}" AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS "${name}"`;
    if (['int8', 'int4', 'uuid'].includes(type)) return `${name}::text`;
    return `"${name}"`;
  }).join(', ');
}

/** Full isolated-org history (not just request debits), preserving all rows and
 * all credit kinds for opening balances. 10001 is an overflow sentinel, not a
 * successful truncated capture. No attempts JSON, notes or provider secrets. */
export function privateLedgerQuery(scope: MoneyScope, projection?: unknown) {
  requirePrivateProjection(scope, projection);
  return Object.freeze({
    text: `SELECT ${selectColumns(ENTRY_COLUMNS)} FROM "${scope.databaseSchema}"."usage_managedcreditentry"
      WHERE organization_id = $1::uuid ORDER BY occurred_at, id LIMIT 10001`,
    values: Object.freeze([scope.organizationId]),
  });
}
export function privateAccountQuery(scope: MoneyScope) {
  requireScope(scope);
  return Object.freeze({
    text: `SELECT ${selectColumns(ACCOUNT_COLUMNS)} FROM "${scope.databaseSchema}"."usage_managedcreditaccount"
      WHERE organization_id = $1::uuid`,
    values: Object.freeze([scope.organizationId]),
  });
}
export function privateSchemaQuery(scope: MoneyScope) {
  requireScope(scope);
  return Object.freeze({
    text: `SELECT c.relname AS table_name, a.attname AS column_name, t.typname AS data_type
      FROM pg_catalog.pg_attribute a
      JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
      JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_catalog.pg_type t ON t.oid = a.atttypid
      WHERE n.nspname = $1 AND c.relname IN ('usage_managedcreditentry', 'usage_managedcreditaccount')
        AND c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped
      ORDER BY c.relname, a.attnum`,
    values: Object.freeze([scope.databaseSchema]),
  });
}
export function assertPrivateSchema(rows: unknown): void {
  const wanted = new Map<string, string>();
  for (const [table, columns] of Object.entries({ usage_managedcreditentry: ENTRY_COLUMNS,
    usage_managedcreditaccount: ACCOUNT_COLUMNS })) {
    for (const [name, type] of Object.entries(columns)) wanted.set(`${table}.${name}`, type);
  }
  const seen = new Set<string>();
  for (const raw of list(rows, 512)) {
    const r = record(raw, ['table_name', 'column_name', 'data_type']);
    if (typeof r.table_name !== 'string' || typeof r.column_name !== 'string') fail('SCHEMA-DRIFT');
    const key = `${r.table_name}.${r.column_name}`;
    if (wanted.has(key)) {
      if (seen.has(key) || r.data_type !== wanted.get(key)) fail('SCHEMA-DRIFT');
      seen.add(key);
    }
    // Real tables have other columns. They are not selected or emitted.
  }
  if (seen.size !== wanted.size) fail('SCHEMA-DRIFT');
}

export function idempotencyConstraintQuery(scope: MoneyScope) {
  requireScope(scope);
  return {
    text: `SELECT EXISTS (
      SELECT 1 FROM pg_catalog.pg_constraint k
      JOIN pg_catalog.pg_class c ON c.oid = k.conrelid
      JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attname = 'idempotency_key'
      WHERE n.nspname = $1 AND c.relname = 'usage_managedcreditentry'
        AND k.contype = 'u' AND k.convalidated AND NOT k.condeferrable
        AND k.conkey = ARRAY[a.attnum]::smallint[]
    ) AS idempotency_unique`, values: [scope.databaseSchema],
  };
}
