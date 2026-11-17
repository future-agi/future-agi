import { randomUUID } from 'node:crypto';
import { test, expect } from '@playwright/test';
import { attachSeededIds } from '../_shared/contract';
import { assertAttemptInventory, assertUsageInventory, assertNoDispatch,
  type AttemptProjection, type UsageProjection } from '../_fixtures/money/control-oracles';

// F07 supplements the existing credit-oracles controls. These are detector-only
// input mutations, NOT full W01 rows, live fallback regression or ten-route IP
// coverage. No @journey-J11/GW-JOURNEY title: cannot satisfy product inventory.
const cases = [
  { id: 'missing-usage', control: 'C-USAGE', rows: ['W01-04', 'W01-06'], failure: 'MONEY-CONTROL usage exact inventory' },
  { id: 'duplicate-usage-row', control: 'C-USAGE', rows: ['W01-06'], failure: 'MONEY-CONTROL usage exact inventory' },
  { id: 'wrong-native-count', control: 'C-USAGE', rows: ['W01-04', 'W01-06'], failure: 'MONEY-CONTROL usage exact inventory' },
  { id: 'foreign-request', control: 'C-ISOLATION', rows: ['W01-09'], failure: 'MONEY-CONTROL usage exact inventory' },
  { id: 'reversed-attempts', control: 'C-FALLBACK', rows: ['W01-04', 'W01-06'], failure: 'MONEY-CONTROL attempts exact order' },
  { id: 'missing-fallback', control: 'C-FALLBACK', rows: ['W01-04', 'W01-06'], failure: 'MONEY-CONTROL attempts exact order' },
  { id: 'duplicate-attempt', control: 'C-FALLBACK', rows: ['W01-06'], failure: 'MONEY-CONTROL attempts exact order' },
  { id: 'refusal-dispatch', control: 'C-REFUSAL', rows: ['W01-02'], failure: 'MONEY-CONTROL refused request no dispatch' },
] as const;

test.afterEach(async ({}, info) => {
  const intended = info.annotations.find(a => a.type === 'money-intended-failure');
  if (!intended) return;
  const exact = info.errors.length === 1 && info.errors[0].message?.includes(intended.description!) === true;
  if (!exact) { info.expectedStatus = 'passed'; expect(exact, 'wrong oracle failure is not a control kill').toBe(true); }
});
for (const cell of cases) for (const phase of ['baseline', 'red', 'restored'] as const) {
  test(`J11-ORACLE.${cell.id} [${phase}]`, {
    tag: ['@journey-J11-oracle'], annotation: [
      { type: 'journey-oracle-partial', description: JSON.stringify({ packet: 'J11', control: cell.control,
        rows: cell.rows, phase, coverage: 'data-only-partial', productRowStatus: 'NOT_WALKED' }) },
      ...(phase === 'red' ? [{ type: 'money-intended-failure', description: cell.failure }] : []),
    ],
  }, async ({}, info) => {
    const requestId = randomUUID();
    const foreignId = randomUUID();
    const model = `fixture-${randomUUID()}`;
    const provider = `fixture-${randomUUID()}`;
    const usages: UsageProjection[] = [{ request_id: requestId, model, input_tokens: 7, output_tokens: 7,
      total_tokens: 14, usage_estimated: false }];
    // Product attempts are 1-based (models/attempt.go:AddAttempt), unlike the
    // programmable provider's explicitly 0-based arrival indices.
    const attempts: AttemptProjection[] = [
      { request_id: requestId, attempt_id: `${requestId}.1`, index: 1, kind: 'primary', outcome: 'error',
        provider, model, status_code: 503 },
      { request_id: requestId, attempt_id: `${requestId}.2`, index: 2, kind: 'request_fallback', outcome: 'success',
        provider: `${provider}-fallback`, model, status_code: 200 },
    ];
    const observedUsage = structuredClone(usages);
    const observedAttempts = structuredClone(attempts);
    const red = phase === 'red';
    if (red) switch (cell.id) {
      case 'missing-usage': observedUsage.length = 0; break;
      case 'duplicate-usage-row': observedUsage.push({ ...usages[0] }); break;
      case 'wrong-native-count': observedUsage[0].output_tokens = 0; break;
      case 'foreign-request': observedUsage[0].request_id = foreignId; break;
      case 'reversed-attempts': observedAttempts.reverse(); break;
      case 'missing-fallback': observedAttempts.pop(); break;
      case 'duplicate-attempt': observedAttempts.push({ ...attempts[1] }); break;
    }
    await attachSeededIds(info, 'J11', { requestIds: [requestId, foreignId] });
    await info.attach('money-control-fixture', { contentType: 'application/json', body: JSON.stringify({
      cell: cell.id, phase, control: cell.control, mutatedInputOnly: true, productRowsCovered: [],
    }) });
    await test.step(`${cell.control}/${cell.id}: unchanged detector`, async () => {
      if (red) test.fail(true, cell.failure);
      if (cell.control === 'C-FALLBACK') assertAttemptInventory(observedAttempts, attempts);
      else if (cell.control === 'C-REFUSAL') assertNoDispatch(red ? 1 : 0);
      else assertUsageInventory(observedUsage, usages);
    });
  });
}
