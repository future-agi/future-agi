import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  F05_CANDIDATE, accountQuery, assertAccount, assertAccountUnchanged,
  grantPlan, moneyScope, privateLedgerQuery, reconcileStatement,
  requireF05Runtime, usdToMicros,
} from './index.ts';

// Oracle fixtures only. No DB, browser, server, credentials or payment provider.
const id = (n: number) => `00000000-0000-4000-8000-${n.toString().padStart(12, '0')}`;
const input = () => ({
  candidateHead: F05_CANDIDATE.head, runId: id(1), organizationId: id(2),
  personId: id(3), apiKeyId: id(4), keyType: 'user',
  fixtureKind: 'test-owned-loopback', apiOrigin: 'http://127.0.0.1:8000',
  databaseSchema: 'public', requestIds: [id(5), id(6)],
});
const account = () => ({
  organization_id: id(2), granted_total_micros: '10000000', settled_spend_micros: '2000000',
  version: '3', gateway_acked_version: '2', updated_at: '2026-10-01T00:00:00.000000Z',
});
const entry = (n: number, kind: string, amount: string, when = '2026-10-10T00:00:00.000000Z') => ({
  id: id(n), organization_id: id(2), occurred_at: when, kind,
  amount_micros: amount, model: kind === 'spend' ? 'e2e-f05-model' : '',
  markup_micros: kind === 'spend' ? '1' : null, usage_estimated: false,
});
const statementCase = () => ({
  organizationId: id(2), month: '2026-10', currency: 'USD',
  expectedEntryIds: [id(10), id(11), id(12), id(13), id(14)],
  entries: [
    entry(10, 'grant', '10000000', '2026-09-30T23:59:59.999999Z'),
    entry(11, 'spend', '-2000000'), entry(12, 'refund', '300000'),
    entry(13, 'usage_debit', '-400000'), entry(14, 'spend', '-1'),
  ],
  statement: {
    opening_balance_micros: '10000000', closing_balance_micros: '7899999',
    credits_by_kind: {
      topup: '0', grant: '0', refund: '300000', adjustment: '0', expiry: '0',
      topup_reversal: '0', dispute_hold: '0', dispute_release: '0',
    },
    spend_by_model: [{ model: 'e2e-f05-model', estimated: false,
      count: 2, spend_micros: '2000001', markup_micros: '2' }],
  },
});
const refused = (fn: () => unknown) => assert.throws(fn, /^Error: F05-[A-Z-]+$/);

test('F05-01 runtime refuses early candidate and arbitrary replacements', () => {
  assert.equal(F05_CANDIDATE.runtimeReady, false);
  assert.throws(() => requireF05Runtime(F05_CANDIDATE.head), /F05-PRIVATE-PROJECTION-UNBOUND/);
  assert.throws(() => requireF05Runtime('unreviewed-head'), /F05-CANDIDATE-UNBOUND/);
});

test('F05-02 scope is detached, frozen, exact and person-owned', () => {
  const raw = input();
  const scope = moneyScope(raw);
  raw.requestIds.push(id(7));
  assert.deepEqual(scope.requestIds, [id(5), id(6)]);
  assert.ok(Object.isFrozen(scope));
  assert.ok(Object.isFrozen(scope.requestIds));
  refused(() => moneyScope({ ...input(), keyType: 'system' }));
  refused(() => moneyScope({ ...input(), keyType: 'org' }));
  refused(() => moneyScope({ ...input(), personId: null }));
  refused(() => moneyScope({ ...input(), autoMint: true }));
});

test('F05-03 network and SQL identifier misuse is refused before any connection', () => {
  for (const apiOrigin of ['https://example.invalid', 'http://127.1', 'http://localhost/path',
    'http://user:pass@localhost', 'http://127.0.0.1:99999']) {
    refused(() => moneyScope({ ...input(), apiOrigin }));
  }
  for (const databaseSchema of ['public; UPDATE x', 'public.x', '"public"', '']) {
    refused(() => moneyScope({ ...input(), databaseSchema }));
  }
  refused(() => moneyScope({ ...input(), requestIds: [id(5), id(5)] }));
  refused(() => moneyScope({ ...input(), requestIds: [] }));
});

test('F05-04 reject credential/direct-write/signed-envelope inputs without echoing them', () => {
  // Synthetic sentinel, not a live credential. Structural refusal is NOT a secret detector.
  for (const field of ['api_key', 'secret_key', 'Authorization', 'balance', 'sql', 'signature']) {
    const value = 'synthetic-sensitive-sentinel';
    try { moneyScope({ ...input(), [field]: value }); assert.fail('accepted misuse'); }
    catch (error) { assert.match((error as Error).message, /^F05-/); assert.ok(!String(error).includes(value)); }
  }
});

test('F05-05 getters are refused without invocation', () => {
  let calls = 0;
  const raw = input();
  Object.defineProperty(raw, 'personId', { enumerable: true, get() { calls++; return id(3); } });
  refused(() => moneyScope(raw));
  assert.equal(calls, 0);
});

test('F05-06 account SQL is schema-qualified, scoped and read-only with int64 text', () => {
  const scope = moneyScope(input());
  const query = accountQuery(scope);
  assert.match(query.text, /FROM "public"\."usage_managedcreditaccount"/);
  assert.match(query.text, /WHERE organization_id = \$1::uuid/);
  assert.match(query.text, /updated_at/);
  assert.match(query.text, /gateway_acked_version::text/);
  assert.deepEqual(query.values, [id(2)]);
  assert.doesNotMatch(query.text, /\b(INSERT|UPDATE|DELETE|TRUNCATE|CALL|COPY)\b|SELECT\s+\*/i);
  refused(() => accountQuery({ ...scope } as typeof scope));
});

test('F05-07 account absence, stale acknowledgement and corruption are not zero/success', () => {
  const scope = moneyScope(input());
  assert.equal(assertAccount(scope, [account()]).pushState, 'pending');
  assert.equal(assertAccount(scope, [{ ...account(), gateway_acked_version: '3' }]).pushState, 'acked');
  for (const rows of [[], [account(), account()], [{ ...account(), organization_id: id(99) }],
    [{ ...account(), gateway_acked_version: '4' }], [{ ...account(), version: 3 }],
    [{ ...account(), settled_spend_micros: '-1' }], [{ ...account(), updated_at: null }],
    [{ ...account(), secret_key: 'synthetic-sensitive-sentinel' }]]) {
    refused(() => assertAccount(scope, rows));
  }
});

test('F05-08 no-write witness includes updated_at and both version fields', () => {
  const scope = moneyScope(input());
  assertAccountUnchanged(scope, [account()], [account()]);
  for (const change of [{ updated_at: '2026-10-01T00:00:00.000001Z' },
    { gateway_acked_version: '3' }, { version: '4' }, { granted_total_micros: '10000001' }]) {
    refused(() => assertAccountUnchanged(scope, [account()], [{ ...account(), ...change }]));
  }
});

test('F05-09 private request SQL cannot be invented or enabled by a signed-looking object', () => {
  const scope = moneyScope(input());
  assert.throws(() => privateLedgerQuery(scope), /F05-PRIVATE-PROJECTION-UNBOUND/);
  for (const signature of ['synthetic-original', 'synthetic-tampered']) {
    refused(() => privateLedgerQuery({ ...scope, signature, runtimeReady: true } as typeof scope));
  }
});

test('F05-10 grant plan binds the ledger service route, exact micros and replay identity', () => {
  const scope = moneyScope(input());
  const plan = grantPlan(scope, { amountMicros: '1234567', grantId: id(7) });
  assert.equal(plan.method, 'POST');
  assert.equal(plan.path, '/agentcc/managed-credits/grant/');
  assert.deepEqual(plan.body, { org_id: id(2), amount_usd: '1.234567',
    note: 'e2e-f05 synthetic credit; not payment fulfilment',
    idempotency_key: `e2e-f05-${id(1)}-${id(7)}` });
  assert.equal(plan.authority, 'isolated-staff-session-required');
  assert.equal(plan.dispatchable, false);
  assert.deepEqual(grantPlan(scope, { amountMicros: '1234567', grantId: id(7) }), plan);
  assert.notEqual(grantPlan(moneyScope({ ...input(), runId: id(8) }),
    { amountMicros: '1234567', grantId: id(7) }).body.idempotency_key, plan.body.idempotency_key);
});

test('F05-11 grants refuse direct balances, any API key, provider payload and unsafe amounts', () => {
  const scope = moneyScope(input());
  for (const amountMicros of ['0', '-1', '1.5', '01', 1, '1000000000000001']) {
    refused(() => grantPlan(scope, { amountMicros, grantId: id(7) }));
  }
  for (const field of ['balance', 'sql', 'api_key', 'admin_token', 'signature', 'card_number']) {
    refused(() => grantPlan(scope, { amountMicros: '1', grantId: id(7), [field]: 'synthetic' }));
  }
});

test('F05-12 decimal conversions never round through JS numbers', () => {
  assert.equal(usdToMicros('9007199254.740993'), '9007199254740993');
  assert.equal(usdToMicros('-0.000001'), '-1');
  for (const value of [0.1, '0.0000001', '1e2', 'NaN', '+1.00', '01.00', '-0.000000']) {
    refused(() => usdToMicros(value));
  }
});

test('F05-13 full statement includes opening history, refunds, platform spend and one micro', () => {
  const result = reconcileStatement(moneyScope(input()), statementCase());
  assert.equal(result.closingBalanceMicros, '7899999');
  assert.equal(result.entryCount, 5);
  assert.equal(result.kind, 'oracle-only-not-financial-acceptance');
});

test('F05-14 missing, duplicate, foreign and paginated captures fail', () => {
  const scope = moneyScope(input());
  let c = statementCase(); c.entries.pop(); refused(() => reconcileStatement(scope, c));
  c = statementCase(); c.entries.push(c.entries[0]); refused(() => reconcileStatement(scope, c));
  c = statementCase(); c.entries[0].organization_id = id(99); refused(() => reconcileStatement(scope, c));
  c = statementCase(); c.expectedEntryIds.push(id(99)); refused(() => reconcileStatement(scope, c));
  refused(() => reconcileStatement(scope, { ...statementCase(), next_cursor: 'synthetic' }));
  refused(() => reconcileStatement(scope, { ...statementCase(), organizationId: id(99) }));
});

test('F05-15 each statement total and grouping attribute is independently checked', () => {
  const scope = moneyScope(input());
  for (const field of ['opening_balance_micros', 'closing_balance_micros'] as const) {
    const c = statementCase(); c.statement[field] = '0'; refused(() => reconcileStatement(scope, c));
  }
  for (const field of ['spend_micros', 'markup_micros', 'model'] as const) {
    const c = statementCase(); c.statement.spend_by_model[0][field] = '0';
    refused(() => reconcileStatement(scope, c));
  }
  let c = statementCase(); c.statement.spend_by_model[0].count = 1; refused(() => reconcileStatement(scope, c));
  c = statementCase(); c.statement.spend_by_model[0].estimated = true; refused(() => reconcileStatement(scope, c));
  c = statementCase(); c.statement.credits_by_kind.refund = '0'; refused(() => reconcileStatement(scope, c));
  c = statementCase(); c.statement.spend_by_model.push(c.statement.spend_by_model[0]);
  refused(() => reconcileStatement(scope, c));
});

test('F05-16 UTC half-open window, sign and unknown-kind controls', () => {
  const scope = moneyScope(input());
  for (const occurred_at of ['2026-11-01T00:00:00.000000Z', '2026-10-01T00:00:00+01:00', '2026-02-30T00:00:00.000000Z']) {
    const c = statementCase(); c.entries[0].occurred_at = occurred_at; refused(() => reconcileStatement(scope, c));
  }
  for (const kind of ['void', 'unknown']) {
    const c = statementCase(); c.entries[1].kind = kind; refused(() => reconcileStatement(scope, c));
  }
  const c = statementCase(); c.entries[1].amount_micros = '2000000'; refused(() => reconcileStatement(scope, c));
  refused(() => reconcileStatement(scope, { ...statementCase(), month: '2026-13' }));
});

test('F05-17 statement rejects secret-bearing or signed captures without interpreting them', () => {
  const scope = moneyScope(input());
  refused(() => reconcileStatement(scope, { ...statementCase(), signature: 'synthetic-tampered' }));
  const c = statementCase();
  Object.assign(c.entries[0], { upstream_key: 'synthetic-sensitive-sentinel' });
  refused(() => reconcileStatement(scope, c));
});

test('F05-18 exact bigint totals and estimated/nonestimated groups remain distinct', () => {
  const scope = moneyScope(input());
  const c = statementCase();
  c.entries[0].amount_micros = '9007199254740993';
  c.statement.opening_balance_micros = '9007199254740993';
  c.statement.closing_balance_micros = '9007199252640992';
  c.entries[4].usage_estimated = true;
  c.statement.spend_by_model = [
    { model: 'e2e-f05-model', estimated: false, count: 1, spend_micros: '2000000', markup_micros: '1' },
    { model: 'e2e-f05-model', estimated: true, count: 1, spend_micros: '1', markup_micros: '1' },
  ];
  assert.equal(reconcileStatement(scope, c).closingBalanceMicros, '9007199252640992');
});
