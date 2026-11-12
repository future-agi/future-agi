import { randomUUID } from 'node:crypto';
import { test, expect } from '@playwright/test';
import { attachSeededIds, type Phase } from '../_shared/contract';
import type { CreditAccount } from '../_shared/postgres-config';
import { CREDIT_KINDS, type CreditEntry, type CreditStatement } from './credit-readers';
import { assertByokObserved, assertExactEntries, assertObservedAccountUnchanged,
  assertPacketCostEquality, assertUnestimatedStatement } from './credit-oracles';

// DATA-ONLY detector tests. Not a browser walk, product negative, runner fault
// qualification, or complete packet row. Do not put GW-JOURNEY-J11. or the
// @journey-J11 tag on these: the full-row selection contract must not select
// these synthetic controls as W01/W02/CLOUD coverage. F01/F07 must explicitly
// bind them as oracle-only controls, separately from live baseline/red/restore.
// They are authored, not executed in this parse-only stage.

type Fixture = ReturnType<typeof fixture>;
function fixture() {
  const requestIds = [randomUUID(), randomUUID(), randomUUID()];
  const byokId = randomUUID();
  const modelA = `e2e-oracle-a-${randomUUID()}`;
  const modelB = `e2e-oracle-b-${randomUUID()}`;
  const now = new Date().toISOString();
  const common = { occurred_at: now, provider: '', cost_source: '', stripe_ref_present: false };
  const entries: CreditEntry[] = [{ ...common, id: randomUUID(), kind: 'grant',
    amount_micros: '1000000', request_id: '', model: '', markup_micros: null, markup_bps: null }];
  requestIds.forEach((id, i) => entries.push({ ...common, id: randomUUID(), kind: 'spend',
    amount_micros: String(-(i + 1) * 10), request_id: id, model: i < 2 ? modelA : modelB,
    markup_micros: '1', markup_bps: 500 }));
  const statement: CreditStatement = { month: now.slice(0, 7), currency: 'USD',
    opening_micros: '0', closing_micros: '999940',
    credits_by_kind: Object.fromEntries(CREDIT_KINDS.map(k => [k, k === 'grant' ? '1000000' : '0'])) as
      CreditStatement['credits_by_kind'],
    spend_by_model: [
      { model: modelA, count: 2, spend_micros: '30', markup_micros: '2', estimated: false },
      { model: modelB, count: 1, spend_micros: '30', markup_micros: '1', estimated: false },
    ] };
  const account: CreditAccount = { granted_total_micros: '1000000', settled_spend_micros: '60',
    version: '1', gateway_acked_version: '1' };
  return { requestIds, byokId, entries, statement, account };
}

interface OracleCase {
  id: string; control: string; rows: string[]; failure: string;
  check: (seed: Fixture, red: boolean) => void;
}
const cases: OracleCase[] = [
  { id: 'money-one-micro', control: 'C-MONEY', rows: ['W02-04'],
    failure: 'J11-ORACLE statement closing', check(seed, red) {
      const observed = structuredClone(seed.statement);
      if (red) observed.closing_micros = '999941';
      assertUnestimatedStatement(seed.entries, observed, seed.entries.map(row => row.id));
    } },
  { id: 'money-duplicate-debit', control: 'C-MONEY', rows: ['W01-07', 'W02-04'],
    failure: 'J11-ORACLE entries exact multiplicity', check(seed, red) {
      const observed = structuredClone(seed.entries);
      if (red) observed.push({ ...seed.entries[1], id: randomUUID() });
      assertExactEntries(observed, seed.entries);
    } },
  { id: 'money-half-up-boundary', control: 'C-MONEY', rows: ['W01-05'],
    failure: 'J11-ORACLE half-up billed micros', check(_seed, red) {
      // 10 * 1.05 = 10.5 micros, round HALF UP to 11, not truncation to 10.
      // Arithmetic only: not a bound catalogue/price or 7/7 upstream fixture.
      assertPacketCostEquality(10n, red ? 10n : 11n, 1n, '0.000001');
    } },
  { id: 'money-packet-header', control: 'C-MONEY', rows: ['W01-05'],
    failure: 'J11-ORACLE packet header equality', check(_seed, red) {
      // Candidate currently describes billed-header semantics. Keep the packet
      // equality unchanged; AUTHOR.json records the unresolved contradiction.
      assertPacketCostEquality(10n, 11n, 1n, red ? '0.000011' : '0.000001');
    } },
  { id: 'byok-markup', control: 'C-BYOK', rows: ['W01-08'],
    failure: 'J11-ORACLE BYOK markup', check(seed, red) {
      assertByokObserved(seed.byokId, { request_id: seed.byokId, billing_mode: 'byok',
        cost_markup: red ? '0.000001' : '0.000000' }, seed.entries, seed.account, seed.account);
    } },
  { id: 'byok-debit', control: 'C-BYOK', rows: ['W01-08'],
    failure: 'J11-ORACLE BYOK no public debit', check(seed, red) {
      const observed = structuredClone(seed.entries);
      if (red) observed.push({ ...seed.entries[1], id: randomUUID(), request_id: seed.byokId });
      assertByokObserved(seed.byokId, { request_id: seed.byokId, billing_mode: 'byok',
        cost_markup: '0.000000' }, observed, seed.account, seed.account);
    } },
  { id: 'byok-balance', control: 'C-BYOK', rows: ['W01-08'],
    failure: 'J11-ORACLE account unchanged', check(seed, red) {
      const after = { ...seed.account, settled_spend_micros: red ? '61' : '60' };
      assertByokObserved(seed.byokId, { request_id: seed.byokId, billing_mode: 'byok',
        cost_markup: '0.000000' }, seed.entries, seed.account, after);
    } },
  { id: 'readonly-version', control: 'C-READONLY', rows: ['W02-03'],
    failure: 'J11-ORACLE account unchanged', check(seed, red) {
      assertObservedAccountUnchanged(seed.account, { ...seed.account, version: red ? '2' : '1' });
    } },
];

// A setup error or wrong assertion is not an oracle kill. Reset expected-failure
// handling before surfacing a mismatched kill, so afterEach itself cannot turn
// an unexpected pass into an accepted red. The admitted reporter still owns
// native error/inventory reconciliation; this local check is not F07 acceptance.
test.afterEach(async ({}, info) => {
  const red = info.annotations.find(a => a.type === 'j11-oracle-intended-failure');
  if (!red) return;
  const exactKill = info.errors.length === 1 &&
    info.errors[0].message?.includes(red.description!) === true;
  if (!exactKill) {
    // test.fail(false) is a no-op, not an undo of the red's test.fail(true).
    info.expectedStatus = 'passed';
    expect(exactKill, 'J11 oracle red failed outside its intended assertion').toBe(true);
  }
});

for (const item of cases) {
  for (const phase of ['baseline', 'red', 'restored'] as const satisfies readonly Phase[]) {
    test(`J11-ORACLE.${item.id} [${phase}]`, {
      tag: ['@journey-J11-oracle'],
      annotation: [
        { type: 'journey-oracle-partial', description: JSON.stringify({ packet: 'J11',
          rows: item.rows, control: item.control, phase, coverage: 'data-only-partial',
          productRowStatus: 'NOT_WALKED', productAccepted: false }) },
        ...(phase === 'red' ? [{ type: 'j11-oracle-intended-failure', description: item.failure }] : []),
      ],
    }, async ({}, info) => {
      const seed = fixture();
      await attachSeededIds(info, 'J11', { requestIds: [...seed.requestIds, seed.byokId],
        settlementIds: seed.entries.map(row => row.id) });
      await info.attach('j11-oracle-scope', { contentType: 'application/json', body: JSON.stringify({
        packet: 'J11', control: item.control, phase, dataOnly: true, productRowsWalked: 0,
      }) });
      await test.step(`${item.control}/${phase}/${item.id} (partial detector only)`, async () => {
        // Setup and attachments occur before marking the intended oracle red.
        if (phase === 'red') test.fail(true, item.failure);
        item.check(seed, phase === 'red');
      });
    });
  }
}
