import { randomUUID } from 'node:crypto';
import { test, expect } from '@playwright/test';
import { attachSeededIds, journeyTitle } from '../_shared/contract';
import { reconciliationDefects, type OracleInput, type OracleFailure } from './reconciliation-oracle';

/**
 * Synthetic, data-only detector tests; NOT the live Redis journey.
 * No actor/page/probe: lib/fixtures would provision legacy org keys, contrary to
 * L3's personally owned credential contract. These tests have no service I/O.
 * All ten complete assertion rows, live C01/C03/C04, and C02 replay over actual
 * captures remain OWED in obligations.ts and AUTHOR.json. Do not count the
 * custom annotations below as journey-assertion / journey-row coverage.
 */
const CONTROL = 'L3-C02-ORACLE-FALLIBILITY';

function syntheticInput(): OracleInput {
  const nonce = `l3-oracle-${randomUUID()}`;
  const id = (label: string) => `${nonce}-${label}`;
  const settled = id('M-SETTLED');
  const held = id('M-HELD');
  const denied = id('M-DENIED');
  const mcpNew = id('MCP-NEW');
  const mcpExisting = id('MCP-ESTABLISHED');
  const request_ids = [settled, held, denied, mcpNew, mcpExisting];
  const holds_before = [
    { hold_id: id('H-SETTLED'), request_id: settled },
    { hold_id: id('H-HELD'), request_id: held },
  ];
  const credentials_before = [
    { credential_record_id: id('C-LIVE'), credential_epoch: 1, revocation_state: 'live' as const },
    { credential_record_id: id('C-REVOKED'), credential_epoch: 1, revocation_state: 'revoked' as const },
    { credential_record_id: id('C-OLD'), credential_epoch: 1, revocation_state: 'superseded' as const },
    { credential_record_id: id('C-CURRENT'), credential_epoch: 2, revocation_state: 'live' as const },
  ];
  const zero = (request_id: string) => ({ request_id, before_count: 0, fault_count: 0, after_count: 0 });
  return {
    request_ids, managed_outage_request_ids: [denied], mcp_outage_request_ids: [mcpNew, mcpExisting],
    billable_request_ids: [settled, held], holds_before, credentials_before,
    managed_deliveries: [zero(denied)], mcp_deliveries: [zero(mcpNew), zero(mcpExisting)],
    hold_terminals: holds_before.map(h => ({ ...h, terminal_state: 'settled' })),
    debits_after: request_ids.map(request_id => ({ request_id, debit_count: [settled, held].includes(request_id) ? 1 : 0 })),
    credentials_after: structuredClone(credentials_before),
  };
}

interface Defect {
  id: string;
  expected: OracleFailure;
  alter: (derived: OracleInput) => OracleInput;
}
const defects: readonly Defect[] = [
  {
    id: 'MANAGED-DELIVERY', expected: { row: 'L3-A04', code: 'post_stop_delivery' },
    alter: d => ({ ...d, managed_deliveries: d.managed_deliveries.map((r, i) =>
      i === 0 ? { ...r, fault_count: 1, after_count: 1 } : r) }),
  },
  {
    id: 'MCP-DELIVERY', expected: { row: 'L3-A05', code: 'post_stop_delivery' },
    alter: d => ({ ...d, mcp_deliveries: d.mcp_deliveries.map((r, i) =>
      i === 0 ? { ...r, fault_count: 1, after_count: 1 } : r) }),
  },
  {
    id: 'MISSING-HOLD', expected: { row: 'L3-A07', code: 'hold_inventory' },
    alter: d => ({ ...d, hold_terminals: d.hold_terminals.slice(0, 1) }),
  },
  {
    id: 'DUPLICATE-DEBIT', expected: { row: 'L3-A07', code: 'debit_multiplicity' },
    alter: d => ({ ...d, debits_after: d.debits_after.map(r =>
      r.request_id === d.billable_request_ids[0] ? { ...r, debit_count: 2 } : r) }),
  },
  {
    id: 'REVIVED-EPOCH', expected: { row: 'L3-A08', code: 'credential_changed' },
    alter: d => ({ ...d, credentials_after: d.credentials_after.map(r =>
      r.revocation_state === 'superseded' ? { ...r, revocation_state: 'live' } : r) }),
  },
  {
    id: 'REVIVED-REVOKED', expected: { row: 'L3-A08', code: 'credential_changed' },
    alter: d => ({ ...d, credentials_after: d.credentials_after.map(r =>
      r.revocation_state === 'revoked' ? { ...r, revocation_state: 'live' } : r) }),
  },
];

// Labels link detector predicates to the packet rows without declaring complete
// product coverage. L3-C02 requires real-capture-derived inputs in a later run.
const annotation = (row: OracleFailure['row']) => ({
  type: 'journey-oracle-support',
  description: JSON.stringify({ schema: 'j27-l3-oracle-support/v1', packet: 'J27-L3',
    control: CONTROL, row, coverage: 'synthetic-detector-only', completesRows: [],
    productStatus: 'NOT_WALKED' }),
});

for (const defect of defects) {
  test(journeyTitle('J27-L3', `L3-C02-UNIT-${defect.id}`, 'baseline'), {
    tag: ['@journey-J27-L3', '@oracle-unit-only'], annotation: annotation(defect.expected.row),
  }, async ({}, testInfo) => {
    const original = syntheticInput();
    const preserved = structuredClone(original);
    await attachSeededIds(testInfo, 'J27-L3', {
      requestIds: original.request_ids,
      holdIds: original.holds_before.map(h => h.hold_id),
      credentialRecordIds: original.credentials_before.map(c => c.credential_record_id),
    });
    await testInfo.attach('oracle-input-kind', { contentType: 'application/json', body: JSON.stringify({
      kind: 'derived-synthetic-unit-records', capturedProductEvidence: false,
      actualFixtureSeeding: false, productStatus: 'NOT_WALKED',
    }) });
    await test.step(`${CONTROL}/${defect.expected.row}/synthetic-green-before`, async () => {
      expect(reconciliationDefects(original)).toEqual([]);
    });
    await test.step(`${CONTROL}/${defect.expected.row}/${defect.id}/synthetic-red`, async () => {
      const derived = defect.alter(structuredClone(original));
      expect(derived).not.toEqual(original);
      // EXACT failing row AND cause; another failure cannot qualify the red.
      expect(reconciliationDefects(derived)).toEqual([defect.expected]);
    });
    await test.step(`${CONTROL}/${defect.expected.row}/untouched-synthetic-restored`, async () => {
      expect(original).toEqual(preserved);
      expect(reconciliationDefects(original)).toEqual([]);
      expect(original).toEqual(preserved);
    });
  });
}

// Additional unit sentinels defend exact-set/multiplicity handling. These are
// tests of this pure comparator, never substitutes for real negative controls.
const incomplete: readonly Defect[] = [
  { id: 'MISSING-SINK', expected: { row: 'L3-A04', code: 'delivery_inventory' },
    alter: d => ({ ...d, managed_deliveries: [] }) },
  { id: 'EXTRA-SINK-ROW', expected: { row: 'L3-A05', code: 'delivery_inventory' },
    alter: d => ({ ...d, mcp_deliveries: [...d.mcp_deliveries, { ...d.mcp_deliveries[0] }] }) },
  { id: 'DUPLICATE-HOLD-ROW', expected: { row: 'L3-A07', code: 'hold_inventory' },
    alter: d => ({ ...d, hold_terminals: [...d.hold_terminals, { ...d.hold_terminals[0] }] }) },
  { id: 'MISSING-DEBIT-ROW', expected: { row: 'L3-A07', code: 'debit_inventory' },
    alter: d => ({ ...d, debits_after: d.debits_after.slice(1) }) },
  { id: 'MISSING-EPOCH-ROW', expected: { row: 'L3-A08', code: 'credential_inventory' },
    alter: d => ({ ...d, credentials_after: d.credentials_after.slice(1) }) },
  { id: 'EMPTY-REQUEST-INVENTORY', expected: { row: 'L3-A09', code: 'invalid_fixture_inventory' },
    alter: d => ({ ...d, request_ids: [] }) },
  { id: 'MALFORMED-COUNTER', expected: { row: 'L3-A04', code: 'post_stop_delivery' },
    alter: d => ({ ...d, managed_deliveries: [{ ...d.managed_deliveries[0], fault_count: Number.NaN }] }) },
];
for (const defect of incomplete) {
  test(journeyTitle('J27-L3', `L3-C02-UNIT-${defect.id}`, 'baseline'), {
    tag: ['@journey-J27-L3', '@oracle-unit-only'], annotation: annotation(defect.expected.row),
  }, async ({}, testInfo) => {
    const original = syntheticInput();
    const preserved = structuredClone(original);
    await attachSeededIds(testInfo, 'J27-L3', { requestIds: original.request_ids,
      holdIds: original.holds_before.map(h => h.hold_id),
      credentialRecordIds: original.credentials_before.map(c => c.credential_record_id) });
    await test.step(`${CONTROL}/${defect.expected.row}/${defect.id}/synthetic-red`, async () => {
      expect(reconciliationDefects(defect.alter(structuredClone(original)))).toEqual([defect.expected]);
    });
    await test.step(`${CONTROL}/${defect.expected.row}/untouched-synthetic-restored`, async () => {
      expect(original).toEqual(preserved);
      expect(reconciliationDefects(original)).toEqual([]);
      expect(original).toEqual(preserved);
    });
  });
}
