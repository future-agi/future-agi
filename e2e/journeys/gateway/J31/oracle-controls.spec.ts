import { randomUUID } from 'node:crypto';
import { test, expect, type TestInfo } from '@playwright/test';
import { attachSeededIds } from '../_shared/contract';
import { assertExactModelIds, assertManagedUnitRequest, assertOrgBProjectionUnchanged,
  exactMicros, managedUnitCost, type CostLog, type OrgBProjection, type UnitExpectation } from './w06-oracles';

// ONLY data-only detector controls, not a W06 browser/product walk. Deliberately
// do not use GW-JOURNEY-J31.* / journeyAnnotations / assertionRow: those promise
// the COMPLETE packet row. All A01-A24 remain OWED in AUTHOR.json. A runner may
// not select these as W06.PREREQ/OPTIN/CONTROLS or use their green as WALKED_PASS.
// Source contracts exercised: w06-oracles.ts pins cost/micros.go,
// request_billing.go and AgentccRequestLog. No actor fixture, providers, page,
// browser route interception, database mutation, or product source is executed.
// C02 ledger mutations, C03 real router exposure/dispatch and C06 real RBAC drift
// still need the separately reviewed scratch/fixture hooks (FOUNDATION F07).

type Phase = 'baseline' | 'red' | 'restored';
function details(row: string, control: string, phase: Phase, assertion: string) {
  return { tag: ['@journey-J31-oracle-only'], annotation: [{
    type: 'journey-subassertion', description: JSON.stringify({
      schema: 'gateway-journey-detector/v1', packet: 'J31', row, control, phase,
      assertion, evidenceKind: 'data-only-detector', completePacketRow: false,
      productRowStatus: 'NOT_WALKED', expectedFailureRow: phase === 'red' ? row : null,
    }),
  }] };
}
async function evidence(info: TestInfo, row: string, control: string): Promise<void> {
  await info.attach('oracle-only-not-walked', { contentType: 'application/json', body: JSON.stringify({
    packet: 'J31', row, control, fixtureKind: 'synthetic-data-only-no-backend-records',
    walkedStatus: 'NOT_WALKED', productAccepted: false,
    note: 'Detector sensitivity is not a product control or an executed complete packet assertion.',
  }) });
}

function moneyFixture() {
  const expected: UnitExpectation = {
    organizationId: randomUUID(), workspaceId: randomUUID(), requestId: `e2e-c02-${randomUUID()}`,
    requestedModel: `synthetic/${randomUUID()}`, servedModel: `synthetic/${randomUUID()}`,
    provider: 'synthetic-route', servedUpstreamProvider: 'synthetic-upstream',
    snapshotId: randomUUID().replaceAll('-', ''), priceSourceRef: 'data-only:C02-fixed-price',
    unitPriceUsd: '0.040000', quantity: 2, stream: false,
  };
  // Independent hard-coded 2 * $0.04 = 80000 micros, 5% => 84000, markup 4000.
  // These are not outputs of managedUnitCost and are not actual model prices.
  const log: CostLog = {
    organization_id: expected.organizationId, workspace_id: expected.workspaceId,
    request_id: expected.requestId, model: expected.requestedModel, resolved_model: expected.servedModel,
    provider: expected.provider, served_upstream_provider: expected.servedUpstreamProvider,
    catalog_snapshot_id: expected.snapshotId, billing_mode: 'managed', cost_source: 'catalog',
    status_code: 200, is_stream: false, is_error: false, fallback_used: false, deleted: false,
    attempt_count: 1, cost: '0.084000', cost_upstream: '0.08000000', cost_markup: '0.00400000',
  };
  return { expected, costHeader: '0.084000', logs: [log] };
}

async function moneySeeds(info: TestInfo, fixture: ReturnType<typeof moneyFixture>): Promise<void> {
  await evidence(info, 'A08', 'C02');
  await attachSeededIds(info, 'J31', { organizationIds: [fixture.expected.organizationId],
    workspaceIds: [fixture.expected.workspaceId], requestIds: [fixture.expected.requestId] });
}

const MONEY_MUTATIONS = [
  { id: 'response-cost', assertion: 'A08 exact x-agentcc-cost', mutate: (f: ReturnType<typeof moneyFixture>) => { f.costHeader = '0.084001'; } },
  { id: 'unit-price', assertion: 'A08 exact x-agentcc-cost', mutate: (f: ReturnType<typeof moneyFixture>) => { f.expected.unitPriceUsd = '0.040001'; } },
  { id: 'request-cost', assertion: 'A08 request cost in integer micros', mutate: (f: ReturnType<typeof moneyFixture>) => { f.logs[0].cost = '0.084001'; } },
  { id: 'request-markup', assertion: 'A08 exact request markup', mutate: (f: ReturnType<typeof moneyFixture>) => { f.logs[0].cost_markup = '0.00400100'; } },
  { id: 'snapshot', assertion: 'A08 exact request identity and billing source', mutate: (f: ReturnType<typeof moneyFixture>) => { f.logs[0].catalog_snapshot_id = randomUUID().replaceAll('-', ''); } },
  { id: 'duplicate-request-log', assertion: 'A08 exactly one request log, including tombstones', mutate: (f: ReturnType<typeof moneyFixture>) => { f.logs.push({ ...f.logs[0] }); } },
];

test('J31-ORACLE.C02.money [baseline]', details('A08', 'C02', 'baseline', 'single-unit header/log'), async ({}, info) => {
  const fixture = moneyFixture();
  await moneySeeds(info, fixture);
  await test.step('J31/A08/C02 baseline detector only', async () => {
    assertManagedUnitRequest(fixture.costHeader, fixture.logs, fixture.expected);
  });
});

for (const mutation of MONEY_MUTATIONS) {
  for (const phase of ['red', 'restored'] as const) {
    test(`J31-ORACLE.C02.${mutation.id} [${phase}]`, details('A08', 'C02', phase, mutation.assertion), async ({}, info) => {
      const fixture = moneyFixture();
      await moneySeeds(info, fixture);
      // Establish valid fixture BEFORE marking expected failure. A setup error
      // cannot be the red. The second call is the identical unchanged detector.
      assertManagedUnitRequest(fixture.costHeader, fixture.logs, fixture.expected);
      if (phase === 'red') {
        mutation.mutate(fixture);
        test.fail(true, `Oracle-only C02 must fail ${mutation.assertion}; never product acceptance`);
      }
      await test.step(`J31/A08/C02/${mutation.id}/${phase}`, async () => {
        assertManagedUnitRequest(fixture.costHeader, fixture.logs, fixture.expected);
      });
    });
  }
}

test('J31-ORACLE.C02.integer-rounding [baseline]', details('A08', 'C02', 'baseline', 'half-up integer precision'), async ({}, info) => {
  await evidence(info, 'A08', 'C02');
  await test.step('J31/A08/C02 exact money boundaries, detector only', async () => {
    expect(managedUnitCost('0.040000', 2)).toEqual({ upstreamMicros: 80_000n, billedMicros: 84_000n, markupMicros: 4_000n });
    expect(managedUnitCost('0.0000025', 1)).toEqual({ upstreamMicros: 3n, billedMicros: 3n, markupMicros: 0n });
    expect(managedUnitCost('0.000010', 1)).toEqual({ upstreamMicros: 10n, billedMicros: 11n, markupMicros: 1n });
    expect(managedUnitCost('0.0000005', 3)).toEqual({ upstreamMicros: 2n, billedMicros: 2n, markupMicros: 0n });
    expect(exactMicros('0.08400000')).toBe(84_000n);
    expect(() => exactMicros('0.08400001')).toThrow('non-integral micros');
    expect(() => exactMicros('8.4e-2')).toThrow('exact nonnegative decimal');
  });
});

const SET_MUTATIONS = ['extra-router', 'same-count-substitution', 'duplicate'] as const;
for (const defect of SET_MUTATIONS) {
  for (const phase of ['red', 'restored'] as const) {
    test(`J31-ORACLE.C03.${defect} [${phase}]`, details('A05', 'C03', phase, 'exact model IDs including multiplicity'), async ({}, info) => {
      await evidence(info, 'A05', 'C03');
      // Minted detector values, explicitly NOT retained IDs or ruled aliases.
      const expected = [`synthetic/image-${randomUUID()}`, `synthetic/clip-${randomUUID()}`];
      const actual = [...expected];
      assertExactModelIds(actual, expected);
      if (phase === 'red') {
        if (defect === 'extra-router') actual.push(`synthetic/router-${randomUUID()}`);
        if (defect === 'same-count-substitution') actual[1] = `synthetic/router-${randomUUID()}`;
        if (defect === 'duplicate') actual[1] = actual[0];
        test.fail(true, 'Oracle-only C03 exact-set assertion must fail; no real router mutation performed');
      }
      await test.step(`J31/A05/C03/${defect}/${phase}`, async () => assertExactModelIds(actual, expected));
    });
  }
}

function orgBFixture(): OrgBProjection {
  const organizationId = randomUUID();
  const now = new Date().toISOString();
  return { organizationId,
    configVersions: [{ id: randomUUID(), organization_id: organizationId, workspace_id: null,
      version: 1, is_active: true, deleted: false, created_at: now, updated_at: now }],
    creditAccount: [{ granted_total_micros: '1000000', settled_spend_micros: '0', version: '1', gateway_acked_version: '1' }],
    gatewayCredit: { org_id: organizationId, granted_total_micros: '1000000', granted_version: '1',
      spent_micros: '0', held_micros: '0', available_micros: '1000000', outbox_pending: '0', ledger: 'redis' },
  };
}

for (const defect of ['credit-drift', 'config-drift'] as const) {
  for (const phase of ['red', 'restored'] as const) {
    test(`J31-ORACLE.C06.${defect} [${phase}]`, details('A12', 'C06', phase, 'org B observable state unchanged'), async ({}, info) => {
      await evidence(info, 'A12', 'C06');
      const before = orgBFixture();
      const after = structuredClone(before);
      await attachSeededIds(info, 'J31', { organizationIds: [before.organizationId] });
      assertOrgBProjectionUnchanged(before, after);
      if (phase === 'red') {
        if (defect === 'credit-drift') after.creditAccount[0].settled_spend_micros = '1';
        if (defect === 'config-drift') after.configVersions[0].version = 2;
        test.fail(true, 'Oracle-only C06 org B equality must fail; no real RBAC/state mutation performed');
      }
      await test.step(`J31/A12/C06/${defect}/${phase}`, async () => assertOrgBProjectionUnchanged(before, after));
    });
  }
}

// All red/restored tests require future independent review and execution on the
// single admitted runtime. Even an expected failure is not a qualified red until
// the reporter verifies the intended assertion, not setup/timeout/infrastructure.
// C01/C04/C05/C07/C08 have no accepted callable hook/capture on this head and are
// owed explicitly, not test.skip/fixme, empty tests, or synthetic product passes.
