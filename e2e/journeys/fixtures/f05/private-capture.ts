import { type MoneyScope } from './contract.ts';
import { PRIVATE_PROJECTION, REQUIRED_MIGRATIONS, requirePrivateProjection, privateLedgerQuery,
  privateAccountQuery, privateSchemaQuery, assertPrivateSchema, idempotencyConstraintQuery } from './private-plan.ts';
import { decodePrivateAccount, decodePrivateEntry } from './private-values.ts';
import { fail, list, record } from './validation.ts';

/** Dedicated pool lease, NOT StateProbe.pg/pool.query (which can switch sessions).
 * Caller supplies an admitted test-only pg.Pool; no DSN, environment discovery,
 * default client, authentication, network guard or runtime permission is created.
 * The real integrated runner must call requireF05Runtime before provisioning or
 * connecting. This source adapter is available to isolated controls, not admission. */
export interface PrivatePool {
  options: { host?: unknown; database?: unknown; connectionString?: unknown; stream?: unknown; ssl?: unknown };
  connect(): Promise<{
    query(text: string, values?: unknown[]): Promise<{ rows: unknown[] }>;
    release(destroy?: boolean): void;
  }>;
}
export const READ_ONLY_BEGIN = 'BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY';
export const SNAPSHOT_QUERY = `SELECT pg_catalog.current_setting('transaction_read_only') AS read_only,
  pg_catalog.current_setting('transaction_isolation') AS isolation,
  pg_catalog.txid_current_snapshot()::text AS snapshot,
  pg_catalog.pg_backend_pid()::text AS backend_pid,
  pg_catalog.current_database() AS database_name`;

/** Negative control ONLY: the predicate can never update a row. No arbitrary
 * SQL or direct balance-writing seam is offered. READ ONLY must refuse even
 * this zero-row update; success fails the capture and rolls back everything. */
export function READ_ONLY_WRITE_CONTROL(schema: string): string {
  if (!/^[a-z_][a-z0-9_]{0,62}$/.test(schema)) fail('SCHEMA');
  return `UPDATE "${schema}"."usage_managedcreditaccount" SET granted_total_micros = granted_total_micros WHERE false`;
}
function transactionSnapshot(rows: unknown, database: string) {
  const all = list(rows, 1); if (all.length !== 1) fail('SNAPSHOT');
  const r = record(all[0], ['read_only', 'isolation', 'snapshot', 'backend_pid', 'database_name']);
  if (r.read_only !== 'on' || r.isolation !== 'repeatable read' || r.database_name !== database
    || typeof r.snapshot !== 'string' || !/^[0-9]+:[0-9]+:(?:[0-9]+(?:,[0-9]+)*)?$/.test(r.snapshot)
    || typeof r.backend_pid !== 'string' || !/^[1-9][0-9]*$/.test(r.backend_pid)) fail('SNAPSHOT');
  return Object.freeze({ ...r });
}
function localDatabase(pool: PrivatePool): string {
  const o = pool.options;
  // Numeric loopback only: no DNS rebinding, service/env fallback or custom stream.
  if (!o || !['127.0.0.1', '::1'].includes(o.host as string)
    || typeof o.database !== 'string' || !/^e2e_[a-z0-9_]{1,48}$/.test(o.database)
    || o.connectionString !== undefined || o.stream !== undefined
    || (o.ssl !== undefined && o.ssl !== false)) fail('PRIVATE-CONNECTION');
  return o.database;
}
function fixedError(error: unknown): never {
  if (error instanceof Error && /^F05-[A-Z-]+$/.test(error.message)) throw error;
  return fail('PRIVATE-READ');
}

export async function capturePrivateLedger(scope: MoneyScope, projection: unknown, pool: PrivatePool) {
  requirePrivateProjection(scope, projection);
  const database = localDatabase(pool);
  let client: Awaited<ReturnType<PrivatePool['connect']>>;
  try { client = await pool.connect(); } catch { return fail('PRIVATE-CONNECTION'); }
  let destroy = false;
  try {
    await client.query(READ_ONLY_BEGIN);
    // Transaction-local timeouts, rolled back with the snapshot; no persistent config.
    await client.query("SET LOCAL statement_timeout = '5000ms'");
    await client.query("SET LOCAL lock_timeout = '1000ms'");
    const before = transactionSnapshot((await client.query(SNAPSHOT_QUERY)).rows, database);
    await client.query('SAVEPOINT f05_read_only_control');
    let refused = false;
    try { await client.query(READ_ONLY_WRITE_CONTROL(scope.databaseSchema)); }
    catch (error) {
      if (!error || typeof error !== 'object' || !('code' in error) || error.code !== '25006') throw error;
      refused = true;
    }
    if (!refused) fail('READ-ONLY-WRITE-ALLOWED');
    await client.query('ROLLBACK TO SAVEPOINT f05_read_only_control');
    await client.query('RELEASE SAVEPOINT f05_read_only_control');
    const schema = privateSchemaQuery(scope);
    assertPrivateSchema((await client.query(schema.text, [...schema.values])).rows);
    const constraint = idempotencyConstraintQuery(scope);
    const constraints = list((await client.query(constraint.text, constraint.values)).rows, 1);
    if (constraints.length !== 1 || record(constraints[0], ['idempotency_unique']).idempotency_unique !== true) fail('IDEMPOTENCY-CONSTRAINT');
    const migrations = (await client.query(`SELECT name FROM "${scope.databaseSchema}"."django_migrations"
      WHERE app = 'usage' AND name >= '0030_' ORDER BY name`)).rows;
    const names = list(migrations, 20).map(r => record(r, ['name']).name);
    if (names.length !== REQUIRED_MIGRATIONS.length || REQUIRED_MIGRATIONS.some((name, i) => names[i] !== name)) fail('MIGRATIONS');
    const accountQuery = privateAccountQuery(scope);
    const account = decodePrivateAccount(scope, (await client.query(accountQuery.text, [...accountQuery.values])).rows);
    const query = privateLedgerQuery(scope, PRIVATE_PROJECTION);
    const rows = list((await client.query(query.text, [...query.values])).rows); // 10001 fails; never truncate.
    const entries = Object.freeze(rows.map(r => decodePrivateEntry(scope, r)));
    const ids = new Set(), keys = new Set();
    for (const r of entries) {
      if (ids.has(r.id) || keys.has(r.idempotency_key)) fail('PRIVATE-DUPLICATE');
      ids.add(r.id); keys.add(r.idempotency_key);
    }
    const after = transactionSnapshot((await client.query(SNAPSHOT_QUERY)).rows, database);
    if (JSON.stringify(before) !== JSON.stringify(after)) fail('SNAPSHOT-DRIFT');
    return Object.freeze({ schema: 'f05-private-capture/v1', sourceHead: scope.candidateHead,
      authority: 'PRIVATE_LEDGER_SNAPSHOT', productAccepted: false, moneyReconciled: false,
      organizationId: scope.organizationId, databaseSchema: scope.databaseSchema,
      requestIds: scope.requestIds, snapshot: before, writeControl: 'REFUSED_25006',
      cohort: 'ALL_ORG_HISTORY_WITHIN_THIS_SNAPSHOT', account, entries,
      // Legacy direct request join only. No suffix parsing, compacted attempt slots
      // or fabricated R14 ID. Full org rows remain available and are not suppressed.
      requestEntries: Object.freeze(entries.filter(r => scope.requestIds.includes(r.request_id as string))),
      unbound: Object.freeze(['release', 'hold', 'terminal_void', 'person_id', 'immutable_financial_id',
        'complete_dispatch_journal', 'commit_before_ack_observation']),
    });
  } catch (error) {
    destroy = true;
    return fixedError(error);
  } finally {
    try { await client.query('ROLLBACK'); }
    catch { destroy = true; fail('ROLLBACK'); }
    finally { client.release(destroy); }
  }
}
