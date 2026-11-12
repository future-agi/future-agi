import assert from 'node:assert/strict';
import { test } from 'node:test';
import { F05_CANDIDATE, moneyScope, privateLedgerQuery, grantPlan } from './index.ts';
import { PRIVATE_PROJECTION, requirePrivateFields, privateSchemaQuery, privateAccountQuery,
  idempotencyConstraintQuery, assertPrivateSchema, ENTRY_COLUMNS, ACCOUNT_COLUMNS } from './private-plan.ts';
import { decodePrivateEntry, exactPrivateMoney, assertReplayPayload,
  assertIdempotentReplay, assertWalletDisplay } from './private-values.ts';
import { capturePrivateLedger, READ_ONLY_BEGIN, SNAPSHOT_QUERY,
  READ_ONLY_WRITE_CONTROL } from './private-capture.ts';

// Future guarded run only: these are synthetic oracle/transport controls, NOT
// native PostgreSQL proof. The capture's write probe must also run on real PG.
const id = (n: number) => `00000000-0000-4000-8000-${n.toString().padStart(12, '0')}`;
const scope = () => moneyScope({ candidateHead: F05_CANDIDATE.head, runId: id(1),
  organizationId: id(2), personId: id(3), apiKeyId: id(4), keyType: 'user',
  fixtureKind: 'test-owned-loopback', apiOrigin: 'http://127.0.0.1:8000',
  databaseSchema: 'f05_test', requestIds: [id(5)] });
const row = () => ({
  id: id(10), organization_id: id(2), kind: 'spend', amount_micros: '-1000001',
  idempotency_key: `spend:${id(5)}`, request_id: id(5), model: 'e2e-f05-model',
  provider: 'e2e-f05-provider', served_upstream_provider: 'e2e-f05-provider',
  upstream_cost_micros: '952382', markup_micros: '47619', markup_bps: '500',
  cost_source: 'catalog', usage_estimated: false, catalog_snapshot_id: 'e2e-f05-snapshot',
  policy_version: 'e2e-f05-policy', api_key_id: id(4), agent_run_id: id(1),
  agent_id: 'e2e-f05-agent', workspace_id: id(6), source_kind: 'model_call', source_ref: id(5),
  price_catalog_version: 'e2e-f05-price', related_entry_id: null,
  occurred_at: '2026-10-30T00:00:00.000000Z', created_at: '2026-10-30T00:00:00.000001Z',
});
const schemaRows = () => [
  ...Object.entries(ENTRY_COLUMNS).map(([column_name, data_type]) =>
    ({ table_name: 'usage_managedcreditentry', column_name, data_type })),
  ...Object.entries(ACCOUNT_COLUMNS).map(([column_name, data_type]) =>
    ({ table_name: 'usage_managedcreditaccount', column_name, data_type })),
];
const account = () => ({ organization_id: id(2), granted_total_micros: '2000000',
  settled_spend_micros: '1000001', platform_spend_micros: '0', version: '1',
  gateway_acked_version: '1', entry_seq: '2', gateway_push_state: 'ok',
  gateway_push_state_at: '2026-10-30T00:00:00.000000Z', updated_at: '2026-10-30T00:00:00.000000Z' });
const snapshot = () => ({ read_only: 'on', isolation: 'repeatable read', snapshot: '100:100:',
  backend_pid: '100', database_name: 'e2e_f05' });
const payload = () => ({ request_id: id(5), idempotency_key: `spend:${id(5)}`,
  billed_micros: '1000001', upstream_micros: '952382', markup_micros: '47619',
  markup_bps: '500', provider: 'e2e-f05-provider', served_upstream_provider: 'e2e-f05-provider',
  cost_source: 'catalog', catalog_snapshot_id: 'e2e-f05-snapshot', policy_version: 'e2e-f05-policy',
  api_key_id: id(4), agent_run_id: id(1), agent_id: 'e2e-f05-agent', model: 'e2e-f05-model',
  usage_estimated: false });
const refused = (fn: () => unknown) => assert.throws(fn, /^Error: F05-[A-Z-]+$/);

function transport(change: { allowWrite?: boolean; missingColumn?: boolean; drift?: boolean;
  failRows?: boolean; publicFloat?: boolean } = {}) {
  const s = scope(), schemaQuery = privateSchemaQuery(s).text, constraintQuery = idempotencyConstraintQuery(s).text;
  const calls: string[] = []; let reads = 0; let released = false;
  const pool = {
    options: { host: '127.0.0.1', database: 'e2e_f05' },
    async connect() {
      return {
        async query(text: string, _values?: unknown[]) {
          calls.push(text);
          if (text === SNAPSHOT_QUERY) {
            const s = snapshot(); if (++reads > 1 && change.drift) s.snapshot = '101:101:';
            return { rows: [s] };
          }
          if (text.startsWith('UPDATE ')) {
            if (!change.allowWrite) throw Object.assign(new Error('synthetic'), { code: '25006' });
            return { rows: [] };
          }
          // The constraint SQL also joins pg_attribute; match complete query identities.
          if (text === constraintQuery) return { rows: [{ idempotency_unique: true }] };
          if (text === schemaQuery) {
            const rows = schemaRows(); if (change.missingColumn) rows.pop(); return { rows };
          }
          if (text.includes('"django_migrations"')) return { rows: [
            { name: '0030_managed_credit_ledger' }, { name: '0031_ledger_phase2a' },
            { name: '0032_ledger_opening_buckets' }, { name: '0033_backfill_topup_payment_intent' },
          ] };
          if (text.includes('FROM "f05_test"."usage_managedcreditaccount"')) return { rows: [account()] };
          if (text.includes('FROM "f05_test"."usage_managedcreditentry"')) {
            if (change.failRows) throw new Error('synthetic-sensitive-row');
            return { rows: [{ ...row(), ...(change.publicFloat ? { amount_micros: -1.000001 } : {}) }] };
          }
          return { rows: [] };
        },
        release() { released = true; },
      };
    },
  };
  return { pool, calls, released: () => released };
}

function assertCaptureReadStages(calls: string[], finalSnapshot: boolean) {
  const s = scope();
  const reads = [SNAPSHOT_QUERY, privateSchemaQuery(s).text, idempotencyConstraintQuery(s).text,
    privateAccountQuery(s).text, privateLedgerQuery(s, PRIVATE_PROJECTION).text];
  // A generic early F05 refusal is not evidence that the injected fault was reached.
  assert.deepEqual(calls.filter(q => reads.includes(q)),
    [...reads, ...(finalSnapshot ? [SNAPSHOT_QUERY] : [])]);
}

test('F05P-01 explicit projection is the only private query binding', () => {
  const s = scope(); refused(() => privateLedgerQuery(s));
  refused(() => privateLedgerQuery(s, { ...PRIVATE_PROJECTION }));
  const q = privateLedgerQuery(s, PRIVATE_PROJECTION);
  assert.match(q.text, /FROM "f05_test"\."usage_managedcreditentry"/);
  assert.match(q.text, /amount_micros::text/);
  assert.match(q.text, /WHERE organization_id = \$1::uuid/);
  assert.deepEqual(q.values, [id(2)]);
  assert.doesNotMatch(q.text, /SELECT\s+\*|json_build|row_to_json|stripe|upstream_key|note|attempts/i);
});

test('F05P-02 absent release, person and R14 mappings never masquerade as zero', () => {
  requirePrivateFields(['per_request_debit', 'idempotency_key', 'snapshot', 'provider', 'cost_source', 'markup']);
  for (const field of ['release', 'hold', 'terminal_void', 'person_id', 'immutable_financial_id', 'unknown']) {
    assert.throws(() => requirePrivateFields([field]), /F05-PRIVATE-FIELD-UNMAPPED/);
  }
});

test('F05P-03 schema drift rejects missing, renamed and type-changed columns', () => {
  assertPrivateSchema(schemaRows());
  let rows = schemaRows(); rows.pop(); refused(() => assertPrivateSchema(rows));
  rows = schemaRows(); rows[0].column_name = 'renamed'; refused(() => assertPrivateSchema(rows));
  rows = schemaRows(); rows[0].data_type = 'float8'; refused(() => assertPrivateSchema(rows));
  assert.match(privateSchemaQuery(scope()).text, /pg_catalog\.pg_attribute/);
});

test('F05P-04 integer micros stay exact beyond Number safe range; floats rejected', () => {
  const r = decodePrivateEntry(scope(), { ...row(), amount_micros: '-1000000000000000001' });
  assert.equal(exactPrivateMoney(r, 'amount_micros'), -1000000000000000001n);
  refused(() => decodePrivateEntry(scope(), { ...row(), amount_micros: -1000000000000.0 }));
  refused(() => exactPrivateMoney({ amount_usd: -1000000000000.0 }, 'amount_micros'));
  refused(() => exactPrivateMoney({ ...r }, 'amount_micros'));
  refused(() => exactPrivateMoney(r, 'amount_usd'));
});

test('F05P-05 private row rejects missing, extra, foreign and secret-bearing columns', () => {
  const r = row(); const { policy_version: _missing, ...rest } = r;
  refused(() => decodePrivateEntry(scope(), rest));
  refused(() => decodePrivateEntry(scope(), { ...r, financial_id: `${id(5)}.1` }));
  refused(() => decodePrivateEntry(scope(), { ...r, organization_id: id(99) }));
  refused(() => decodePrivateEntry(scope(), { ...r, upstream_key_id: 'synthetic-sensitive' }));
});

test('F05P-06 replay payload mismatch fails even when product returns the old row', () => {
  const r = decodePrivateEntry(scope(), row()); assertReplayPayload(r, payload());
  for (const change of [{ billed_micros: '1000002' }, { markup_micros: '47620' },
    { idempotency_key: 'spend:other' }, { provider: 'e2e-f05-other' },
    { policy_version: 'e2e-f05-other' }, { api_key_id: id(99) }]) {
    assert.throws(() => assertReplayPayload(r, { ...payload(), ...change }), /F05-REPLAY-MISMATCH/);
  }
});

test('F05P-07 replay preserves row identities and all projected immutable values', () => {
  const s = scope(), r = decodePrivateEntry(s, row());
  assertIdempotentReplay([r], [decodePrivateEntry(s, row())]);
  for (const change of [{ id: id(11) }, { amount_micros: '-1000002' },
    { catalog_snapshot_id: 'other' }, { agent_run_id: id(99) }]) {
    refused(() => assertIdempotentReplay([r], [decodePrivateEntry(s, { ...row(), ...change })]));
  }
  refused(() => assertIdempotentReplay([r], [r, r]));
});

test('F05P-08 public floats reconcile only at display precision, not micros', () => {
  const r = decodePrivateEntry(scope(), { ...row(), amount_micros: '-1000000000000000001' });
  const result = assertWalletDisplay([r], [{ id: id(10), request_id: id(5), kind: 'spend',
    amount_usd: -1000000000000.0, markup_usd: 0.047619 }], 2);
  assert.equal(result.authority, 'DISPLAY_ONLY'); assert.equal(result.moneyReconciled, false);
  assert.equal(exactPrivateMoney(r, 'amount_micros'), -1000000000000000001n);
  refused(() => assertWalletDisplay([r], [{ id: id(10), request_id: id(5), kind: 'spend',
    amount_usd: -1000000000000.02, markup_usd: 0.047619 }], 2));
});

test('F05P-09 public joins require exact entry/request/kind cohort and null parity', () => {
  const r = decodePrivateEntry(scope(), row());
  for (const change of [{ id: id(99) }, { request_id: id(99) }, { kind: 'grant' }, { markup_usd: null }]) {
    refused(() => assertWalletDisplay([r], [{ id: id(10), request_id: id(5), kind: 'spend',
      amount_usd: -1.000001, markup_usd: 0.047619, ...change }], 2));
  }
  refused(() => assertWalletDisplay([r], [], 2));
});

test('F05P-10 capture leases one connection, verifies snapshot and rolls back', async () => {
  const t = transport(); const c = await capturePrivateLedger(scope(), PRIVATE_PROJECTION, t.pool);
  assert.equal(t.calls[0], READ_ONLY_BEGIN);
  assert.equal(t.calls.filter(q => q === SNAPSHOT_QUERY).length, 2);
  assertCaptureReadStages(t.calls, true);
  assert.equal(t.calls.at(-1), 'ROLLBACK'); assert.ok(t.released());
  assert.deepEqual(c.account, account()); assert.deepEqual(c.snapshot, snapshot());
  assert.equal(c.entries.length, 1);
  assert.equal(c.entries[0].amount_micros, '-1000001');
  assert.equal(c.writeControl, 'REFUSED_25006'); assert.equal(c.moneyReconciled, false);
  assert.match(READ_ONLY_WRITE_CONTROL('f05_test'), /WHERE false$/);
});

test('F05P-11 attempted write must fail with native read-only SQLSTATE', async () => {
  const t = transport({ allowWrite: true });
  await assert.rejects(capturePrivateLedger(scope(), PRIVATE_PROJECTION, t.pool), /F05-READ-ONLY-WRITE-ALLOWED/);
  assert.equal(t.calls.at(-1), 'ROLLBACK'); assert.ok(t.released());
  assert.ok(!t.calls.some(q => q.includes('pg_catalog.pg_attribute')));
});

test('F05P-12 schema drift fails inside snapshot before selecting private rows', async () => {
  const t = transport({ missingColumn: true });
  await assert.rejects(capturePrivateLedger(scope(), PRIVATE_PROJECTION, t.pool), /F05-SCHEMA-DRIFT/);
  assert.ok(!t.calls.some(q => q.includes('FROM "f05_test"."usage_managedcreditentry"')));
  assert.equal(t.calls.at(-1), 'ROLLBACK'); assert.ok(t.released());
});

test('F05P-13 snapshot drift, query failure and float response never produce captures', async context => {
  const faults = [
    { name: 'snapshot drift', change: { drift: true }, message: 'F05-SNAPSHOT-DRIFT', finalSnapshot: true },
    { name: 'ledger query failure', change: { failRows: true }, message: 'F05-PRIVATE-READ', finalSnapshot: false },
    { name: 'float money response', change: { publicFloat: true }, message: 'F05-AMOUNT', finalSnapshot: false },
  ];
  for (const { name, change, message, finalSnapshot } of faults) {
    await context.test(name, async () => {
      const t = transport(change);
      await assert.rejects(capturePrivateLedger(scope(), PRIVATE_PROJECTION, t.pool), { name: 'Error', message });
      assertCaptureReadStages(t.calls, finalSnapshot);
      assert.equal(t.calls.at(-1), 'ROLLBACK'); assert.ok(t.released());
    });
  }
});

test('F05P-14 direct balance writes have no fixture input seam', () => {
  refused(() => grantPlan(scope(), { amountMicros: '1', grantId: id(9), balance: '1' }));
  refused(() => privateLedgerQuery(scope(), { ...PRIVATE_PROJECTION, sql: 'UPDATE balance' }));
  const plan = grantPlan(scope(), { amountMicros: '1', grantId: id(9) });
  assert.equal(plan.path, '/agentcc/managed-credits/grant/'); assert.equal(plan.dispatchable, false);
});

test('F05P-15 connection host/database and projection are refused before connect', async () => {
  for (const options of [{ host: 'example.invalid', database: 'e2e_f05' },
    { host: '127.0.0.1', database: 'production' },
    { host: '127.0.0.1', database: 'e2e_f05', connectionString: 'synthetic' }]) {
    let connected = false;
    const pool = { options, async connect(): Promise<never> { connected = true; throw new Error('must not connect'); } };
    await assert.rejects(capturePrivateLedger(scope(), PRIVATE_PROJECTION, pool), /F05-/);
    assert.equal(connected, false);
  }
});
