import { randomUUID } from 'node:crypto';
import { test, expect } from '@playwright/test';
import { attachSeededIds } from '../_shared/contract';
import {
  REFERENCE_MONEY, moneyViolations, knownUsageReplayViolations,
  type MoneyAmounts, type MoneyBoundary,
} from './money-oracle';

// Data-only detector controls, not J12 product walks. Deliberately NOT tagged
// @journey-J12 or titled GW-JOURNEY-J12: foundation F01 must never join these
// identities to A02-A10 as executed product coverage. No actor/browser/request
// fixture is requested; no org/system key is provisioned by lib/fixtures.
// Provenance for every number: PACKET.json fixture_contract (F/H/U), not a
// response capture. Real C02/C04 mutants, usage barriers, Redis/private ledger
// probes, original-v5 mapping and baseline/red/restored product runs remain OWED.
const FULL_HOLD: MoneyAmounts = { available: '900', openHolds: '0', netSettled: '100' };
const FENCED: MoneyAmounts = { available: '900', openHolds: '100', netSettled: '0' };
const KNOWN: MoneyAmounts = { available: '960', openHolds: '0', netSettled: '40' };
type OraclePhase = 'baseline' | 'red' | 'restored';

function annotation(control: string, cell: string, phase: OraclePhase, relatedRows: string[]) {
  return {
    type: 'j12-oracle-control',
    description: JSON.stringify({ schema: 'j12-oracle-control/v1', control, cell, phase,
      relatedRows, coverage: 'DETECTOR_ONLY', productRowsCovered: [], mutation: 'input-data-only',
      walkedStatus: 'NOT_WALKED', productAccepted: false }),
  };
}

// A wrong failure (including a teardown/transport failure) cannot qualify an
// expected red. This supplements, never replaces, runner-side native reconciliation.
test.afterEach(async ({}, info) => {
  const record = info.annotations.find(a => a.type === 'j12-oracle-control');
  if (!record) return;
  const cell = JSON.parse(record.description!);
  if (cell.phase !== 'red') return;
  const message = cell.control === 'C02'
    ? 'J12 oracle: missing usage must retain full-H settlement or fenced H'
    : 'J12 oracle: repeated observations must retain exact known usage';
  const exact = info.errors.length === 1 && info.errors[0].message?.includes(message) === true;
  if (!exact) {
    info.expectedStatus = 'passed';
    expect(exact, 'J12 oracle red failed outside its intended assertion').toBe(true);
  }
});

const missingUsageCells: {
  id: string; green: MoneyAmounts; red: MoneyAmounts; greenFence: boolean;
  redFence: boolean; expectedViolations: string[];
}[] = [
  { id: 'released-obligation', green: FULL_HOLD, greenFence: false,
    red: { available: '1000', openHolds: '0', netSettled: '0' }, redFence: false,
    expectedViolations: ['UNPROTECTED_OBLIGATION'] },
  { id: 'partial-terminal-undercharge', green: FULL_HOLD, greenFence: false,
    red: KNOWN, redFence: false, expectedViolations: ['UNPROTECTED_OBLIGATION'] },
  { id: 'split-partial-obligation', green: FENCED, greenFence: true,
    red: { available: '900', openHolds: '60', netSettled: '40' }, redFence: true,
    expectedViolations: ['UNPROTECTED_OBLIGATION'] },
  { id: 'hold-without-fence', green: FENCED, greenFence: true,
    red: FENCED, redFence: false, expectedViolations: ['UNPROTECTED_OBLIGATION'] },
  { id: 'one-unit-conservation-loss', green: FULL_HOLD, greenFence: false,
    red: { available: '901', openHolds: '0', netSettled: '100' }, redFence: false,
    expectedViolations: ['CONSERVATION', 'UNPROTECTED_OBLIGATION'] },
];

for (const cell of missingUsageCells) {
  for (const phase of ['baseline', 'red', 'restored'] as const) {
    test(`J12-ORACLE.C02.${cell.id} [${phase}]`, {
      tag: ['@j12-oracle-only'],
      annotation: annotation('C02', cell.id, phase, ['A02', 'A03', 'A04', 'A05', 'A07', 'A10']),
    }, async ({}, testInfo) => {
      await attachSeededIds(testInfo, 'J12', { requestIds: [randomUUID()] });
      await testInfo.attach('oracle-scope', { contentType: 'application/json',
        body: JSON.stringify({ scope: 'synthetic arithmetic input only', candidateMutated: false,
          productExecuted: false, evidence: null }) });
      const input = { ...(phase === 'red' ? cell.red : cell.green) };
      const fence = phase === 'red' ? cell.redFence : cell.greenFence;
      const observed = moneyViolations(REFERENCE_MONEY, input, 'missing-usage', fence);
      await test.step('detector control reaches the named invariant, not setup failure', async () => {
        expect(observed).toEqual(phase === 'red' ? cell.expectedViolations : []);
      });
      await test.step('same missing-usage arithmetic assertion in all three phases', async () => {
        if (phase === 'red') test.fail(true, 'Expected only the named arithmetic assertion to fail');
        expect(observed, 'J12 oracle: missing usage must retain full-H settlement or fenced H').toEqual([]);
      });
    });
  }
}

const replayCells: { id: string; red: MoneyAmounts[]; expectedViolations: string[] }[] = [
  { id: 'duplicate-debit', red: [KNOWN, { available: '920', openHolds: '0', netSettled: '80' }],
    expectedViolations: ['1:KNOWN_USAGE_STATE'] },
  { id: 'duplicate-refund', red: [KNOWN, { available: '1000', openHolds: '0', netSettled: '0' }],
    expectedViolations: ['1:KNOWN_USAGE_STATE'] },
  { id: 'missing-replay-observations', red: [], expectedViolations: ['NO_REPLAY_OBSERVATIONS'] },
];

for (const cell of replayCells) {
  for (const phase of ['baseline', 'red', 'restored'] as const) {
    test(`J12-ORACLE.C04.${cell.id} [${phase}]`, {
      tag: ['@j12-oracle-only'],
      annotation: annotation('C04', cell.id, phase, ['A03', 'A06', 'A07', 'A09', 'A10']),
    }, async ({}, testInfo) => {
      await attachSeededIds(testInfo, 'J12', { requestIds: [randomUUID()] });
      await testInfo.attach('oracle-scope', { contentType: 'application/json',
        body: JSON.stringify({ scope: 'synthetic arithmetic input only', realWorkerDelivery: false,
          productExecuted: false, evidence: null }) });
      const inputs = (phase === 'red' ? cell.red : [KNOWN, KNOWN, KNOWN]).map(value => ({ ...value }));
      const observed = knownUsageReplayViolations(REFERENCE_MONEY, inputs);
      await test.step('detector control reaches the named invariant, not setup failure', async () => {
        expect(observed).toEqual(phase === 'red' ? cell.expectedViolations : []);
      });
      await test.step('same known-usage arithmetic assertion in all three phases', async () => {
        if (phase === 'red') test.fail(true, 'Expected only the named replay arithmetic assertion to fail');
        expect(observed, 'J12 oracle: repeated observations must retain exact known usage').toEqual([]);
      });
    });
  }
}

// These boundary/parser checks are also supplementary, never packet row coverage.
// No settlement timing is simulated and no arbitrary poll budget is invented.
const boundaries: { id: string; amounts: MoneyAmounts; boundary: MoneyBoundary; fence: boolean }[] = [
  { id: 'funded', amounts: { available: '1000', openHolds: '0', netSettled: '0' }, boundary: 'funded', fence: false },
  { id: 'reservation', amounts: FENCED, boundary: 'reserved', fence: false },
  { id: 'full-hold', amounts: FULL_HOLD, boundary: 'missing-usage', fence: false },
  { id: 'fenced-hold', amounts: FENCED, boundary: 'missing-usage', fence: true },
  { id: 'known-usage', amounts: KNOWN, boundary: 'known-usage', fence: false },
];
for (const cell of boundaries) {
  test(`J12-ORACLE.boundary.${cell.id}`, { tag: ['@j12-oracle-only'] }, async () => {
    await test.step('packet F/H/U exact arithmetic', async () => {
      expect(moneyViolations(REFERENCE_MONEY, cell.amounts, cell.boundary, cell.fence)).toEqual([]);
    });
  });
}

test('J12-ORACLE.integer-units', { tag: ['@j12-oracle-only'] }, async () => {
  await test.step('reject floats, exponent notation, partial values and signed zero', async () => {
    for (const available of ['960.0', '9.6e2', '960units', '-0', '-1', '', '0960']) {
      expect(moneyViolations(REFERENCE_MONEY, { ...KNOWN, available }, 'known-usage', false))
        .toEqual(['NON_INTEGER_UNITS']);
    }
  });
  await test.step('retain a one-unit defect beyond JavaScript safe integer range', async () => {
    const fixture = { funded: '9007199254741993', reserved: '100', knownUsage: '40' };
    expect(moneyViolations(fixture,
      { available: '9007199254741953', openHolds: '0', netSettled: '40' }, 'known-usage', false)).toEqual([]);
    expect(moneyViolations(fixture,
      { available: '9007199254741954', openHolds: '0', netSettled: '40' }, 'known-usage', false))
      .toEqual(['CONSERVATION', 'KNOWN_USAGE_STATE']);
  });
});
