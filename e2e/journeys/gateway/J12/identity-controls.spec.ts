import { randomUUID } from 'node:crypto';
import { test, expect } from '@playwright/test';
import { attachSeededIds } from '../_shared/contract';
import { assertMoneyIdentityInventory, assertAttemptInventory,
  type MoneyIdentity, type AttemptProjection } from '../_fixtures/money/control-oracles';

// Supplements C02/C04 arithmetic: equal balances can hide a dropped or compensated
// duplicate financial effect. Synthetic identities ONLY, not F05 schema/capture.
// C03's specific 95b6881f9c scratch regression is still owed. These controls never
// call a private ledger, replay a worker, claim row PASS or stand in for v5.
const cases = [
  { id: 'dropped-debit', control: 'C02', rows: ['A02', 'A03', 'A04', 'A05', 'A07', 'A10'], failure: 'MONEY-CONTROL money exact identities' },
  { id: 'duplicate-debit', control: 'C04', rows: ['A03', 'A06', 'A07', 'A09', 'A10'], failure: 'MONEY-CONTROL money exact identities' },
  { id: 'compensated-duplicate', control: 'C04', rows: ['A09', 'A10'], failure: 'MONEY-CONTROL money exact identities' },
  { id: 'wrong-request', control: 'C04', rows: ['A10'], failure: 'MONEY-CONTROL money exact identities' },
  { id: 'retry-fallback-order', control: 'C03', rows: ['A06'], failure: 'MONEY-CONTROL attempts exact order' },
] as const;
test.afterEach(async ({}, info) => {
  const intended = info.annotations.find(a => a.type === 'money-intended-failure');
  if (!intended) return;
  const exact = info.errors.length === 1 && info.errors[0].message?.includes(intended.description!) === true;
  if (!exact) { info.expectedStatus = 'passed'; expect(exact, 'wrong oracle failure is not a control kill').toBe(true); }
});
for (const cell of cases) for (const phase of ['baseline', 'red', 'restored'] as const) {
  test(`J12-ORACLE.${cell.id} [${phase}]`, {
    tag: ['@j12-oracle-only'], annotation: [
      { type: 'j12-oracle-control', description: JSON.stringify({ packet: 'J12', control: cell.control,
        relatedRows: cell.rows, phase, coverage: 'DETECTOR_ONLY', productRowsCovered: [], walkedStatus: 'NOT_WALKED' }) },
      ...(phase === 'red' ? [{ type: 'money-intended-failure', description: cell.failure }] : []),
    ],
  }, async ({}, info) => {
    const requestId = randomUUID(); const foreignId = randomUUID();
    const expected: MoneyIdentity[] = [{ id: randomUUID(), requestId, effectMicros: '-40' }];
    const actual = structuredClone(expected);
    const provider = `fixture-${randomUUID()}`; const model = `fixture-${randomUUID()}`;
    const attempts: AttemptProjection[] = [
      { attempt_id: `${requestId}.1`, request_id: requestId, index: 1, kind: 'primary', outcome: 'error', provider, model, status_code: 503 },
      { attempt_id: `${requestId}.2`, request_id: requestId, index: 2, kind: 'retry', outcome: 'error', provider, model, status_code: 503 },
      { attempt_id: `${requestId}.3`, request_id: requestId, index: 3, kind: 'request_fallback', outcome: 'success', provider: `${provider}-fallback`, model, status_code: 200 },
    ];
    const observedAttempts = structuredClone(attempts);
    if (phase === 'red') switch (cell.id) {
      case 'dropped-debit': actual.length = 0; break;
      case 'duplicate-debit': actual.push({ ...expected[0], id: randomUUID() }); break;
      case 'compensated-duplicate': actual.push({ ...expected[0], id: randomUUID() },
        { id: randomUUID(), requestId, effectMicros: '40' }); break;
      case 'wrong-request': actual[0].requestId = foreignId; break;
      case 'retry-fallback-order': observedAttempts.reverse(); break;
    }
    await attachSeededIds(info, 'J12', { requestIds: [requestId, foreignId],
      settlementIds: [...new Set([...expected, ...actual].map(row => row.id))] });
    // Deduplication above is only the attachment ID vocabulary; the assertion
    // receives the untouched actual array and detects repeated IDs/rows as well.
    await info.attach('money-control-fixture', { contentType: 'application/json', body: JSON.stringify({
      cell: cell.id, phase, mutation: 'input-data-only', workerDeliveries: 0, productRowsCovered: [],
    }) });
    await test.step(`${cell.control}/${cell.id}: unchanged detector`, async () => {
      if (phase === 'red') test.fail(true, cell.failure);
      if (cell.id === 'retry-fallback-order') assertAttemptInventory(observedAttempts, attempts);
      else assertMoneyIdentityInventory(actual, expected);
    });
  });
}
