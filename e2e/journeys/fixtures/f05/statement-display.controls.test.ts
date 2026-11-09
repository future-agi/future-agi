import assert from 'node:assert/strict';
import { test } from 'node:test';
import { F05_CANDIDATE, moneyScope } from './contract.ts';
import { decodePrivateEntry } from './private-values.ts';
import { privateStatement, assertStatementDisplay } from './private-statement.ts';

// Synthetic data-only controls; NOT_RUN until a guarded integrated venue.
const id = (n: number) => `00000000-0000-4000-8000-${n.toString().padStart(12, '0')}`;
const s = moneyScope({ candidateHead: F05_CANDIDATE.head, runId: id(1), organizationId: id(2),
  personId: id(3), apiKeyId: id(4), keyType: 'user', fixtureKind: 'test-owned-loopback',
  apiOrigin: 'http://127.0.0.1:8000', databaseSchema: 'f05_test', requestIds: [id(5)] });
const row = (n: number, kind: string, amount: string, at: string) => decodePrivateEntry(s, {
  id: id(n), organization_id: id(2), kind, amount_micros: amount, idempotency_key: `e2e-f05-${n}`,
  request_id: kind === 'spend' ? id(5) : '', model: kind === 'spend' ? 'e2e-f05-model' : '',
  provider: '', served_upstream_provider: '', upstream_cost_micros: null, markup_micros: null,
  markup_bps: null, cost_source: '', usage_estimated: false, catalog_snapshot_id: '', policy_version: '',
  api_key_id: '', agent_run_id: '', agent_id: '', workspace_id: null, source_kind: '', source_ref: '',
  price_catalog_version: '', related_entry_id: null, occurred_at: at, created_at: at,
});
const rows = () => [
  row(10, 'grant', '1000000000000000001', '2026-09-30T00:00:00.000000Z'),
  row(11, 'spend', '-1000001', '2026-10-01T00:00:00.000000Z'),
  row(12, 'usage_debit', '-1000000', '2026-10-01T00:00:00.000000Z'),
  row(13, 'refund', '1000000', '2026-10-01T00:00:00.000000Z'),
];
const publicStatement = () => ({ month: '2026-10', currency: 'USD', opening_balance: 1000000000000,
  closing_balance: 999999999999, credits_by_kind: { topup: 0, grant: 0, refund: 1,
    adjustment: 0, expiry: 0, topup_reversal: 0, dispute_hold: 0, dispute_release: 0 },
  spend_by_model: [{ model: 'e2e-f05-model', estimated: false, count: 1, spend_usd: 1.000001, markup_usd: 0 }] });

test('F05P-16 statement exact totals use private history, public numbers only display', () => {
  const p = privateStatement(s, rows(), '2026-10');
  assert.equal(p.openingMicros, '1000000000000000001');
  assert.equal(p.closingMicros, '999999999999000000');
  const result = assertStatementDisplay(p, publicStatement(), 2);
  assert.equal(result.authority, 'DISPLAY_ONLY'); assert.equal(result.moneyReconciled, false);
  assert.throws(() => assertStatementDisplay({ ...p }, publicStatement(), 2), /F05-PRIVATE-EXACT-SOURCE/);
});

test('F05P-17 statement display controls preserve model/estimate groups and all credit kinds', () => {
  const p = privateStatement(s, rows(), '2026-10');
  for (const field of ['opening_balance', 'closing_balance'] as const) {
    const pub = publicStatement(); pub[field] += 0.02;
    assert.throws(() => assertStatementDisplay(p, pub, 2), /F05-PUBLIC-DISPLAY/);
  }
  const pub = publicStatement(); pub.spend_by_model[0].estimated = true;
  assert.throws(() => assertStatementDisplay(p, pub, 2), /F05-DISPLAY-GROUP/);
  const credit = publicStatement(); credit.credits_by_kind.refund = 0;
  assert.throws(() => assertStatementDisplay(p, credit, 2), /F05-PUBLIC-DISPLAY/);
});

test('F05P-18 private statement rejects public rows, duplicate entries and future-window leaks', () => {
  assert.throws(() => privateStatement(s, [{ amount_usd: 1 }], '2026-10'), /F05-/);
  const r = rows(); assert.throws(() => privateStatement(s, [...r, r[0]], '2026-10'), /F05-/);
  assert.throws(() => privateStatement(s, rows(), '2026-13'), /F05-/);
  // Full capture may include future history: the month projection excludes it.
  const p = privateStatement(s, [...rows(), row(14, 'grant', '999', '2026-11-01T00:00:00.000000Z')], '2026-10');
  assert.equal(p.entryCount, 4); assert.equal(p.closingMicros, '999999999999000000');
});
