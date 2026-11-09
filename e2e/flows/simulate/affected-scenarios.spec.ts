import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedHarnessEnvironment } from '../../lib/simulate-seed';

// The readiness screen is TH-8877; until it ships this flow drives the API it will
// read. Paths from futureagi/simulate/views/harness_environment.py
// (HarnessEnvironmentViewSet: capabilities, capabilities/cases).
const ENVIRONMENTS = '/simulate/api/harness-environments';
// selection.PAGE_SIZE, futureagi/simulate/services/harness_readiness/selection.py.
const PAGE_SIZE = 50;
// One more refund than a page holds, so the blocked bucket has a second page.
const REFUNDS = 53;
const LOOKUPS = 2;
const BUCKETS = ['eligible', 'blocked', 'stale', 'untested'] as const;

interface Selection {
  requested: number;
  eligible: number; blocked: number; stale: number; untested: number;
  lists: Record<(typeof BUCKETS)[number], { keys: string[]; truncated: boolean }>;
}
interface Row { id: string; affected: { count: number; of: number } }
interface Report { selection: Selection; rows: Row[] }
interface CasePage { items: { key: string; number: number; by: string[] }[]; next: string | null }

test('SIM-E2E-004: the cases a pairing holds back are bucketed and paged by scenario number', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-004', area: 'simulate',
    userGoal: 'A developer selecting scenarios to run sees how many of them the pairing can run, '
      + 'how many it holds back and why, and pages through the held-back cases',
    steps: [`seed a built, certified Environment of ${REFUNDS} refund and ${LOOKUPS} lookup scenarios whose contract `
              + 'routes issue_refund to production (e2e/lib/harness_environment_seed.py)',
            'read the pairing\'s readiness for the whole suite (GET capabilities)',
            'page through the blocked cases (GET capabilities/cases?bucket=blocked, then cursor=next)',
            'read the eligible cases'],
    backendChecks: [`the buckets add up to requested: ${LOOKUPS} eligible and ${REFUNDS} blocked of ${REFUNDS + LOOKUPS}, `
                      + 'each list naming exactly the seeded keys',
                    `tool routing holds back ${REFUNDS} of ${REFUNDS + LOOKUPS}`,
                    `the blocked cases page ${PAGE_SIZE} at a time by scenario number: the first page ends on its last `
                      + 'number as next, the second holds the rest with no next, every case held by tool_routing',
                    'the eligible page is the two lookups, held by nothing',
                    'the page numbers are the stored suite members\' numbers'],
  }),
}, async ({ actor, probe }, testInfo) => {
  // One seed through the backend container (Django start-up) and four API calls.
  test.setTimeout(120_000);
  const seed = seedHarnessEnvironment({ organizationId: actor.organizationId, workspaceId: actor.workspaceId,
    refunds: REFUNDS, lookups: LOOKUPS, productionOnly: 'issue_refund' });
  await testInfo.attach('seeded', { contentType: 'application/json',
    body: JSON.stringify({ organizationId: actor.organizationId, workspaceId: actor.workspaceId, ...seed }) });
  const capabilities = `${ENVIRONMENTS}/${seed.environmentId}/capabilities/`;
  const cases = (params: Record<string, string>) => actor.api.get<CasePage>(`${capabilities}cases/`, params);

  await test.step('api: the buckets add up to what was requested', async () => {
    const { selection, rows } = await actor.api.get<Report>(capabilities);
    expect(BUCKETS.reduce((sum, bucket) => sum + selection[bucket], 0)).toBe(selection.requested);
    expect({ requested: selection.requested, ...Object.fromEntries(BUCKETS.map((b) => [b, selection[b]])) })
      .toEqual({ requested: REFUNDS + LOOKUPS, eligible: LOOKUPS, blocked: REFUNDS, stale: 0, untested: 0 });
    expect(selection.lists.blocked).toEqual({ keys: seed.refundKeys, truncated: false });
    expect(selection.lists.eligible).toEqual({ keys: seed.lookupKeys, truncated: false });
    expect(rows.find((row) => row.id === 'tool_routing')?.affected)
      .toMatchObject({ count: REFUNDS, of: REFUNDS + LOOKUPS });
  });

  await test.step('api: the blocked cases page by number', async () => {
    const first = await cases({ bucket: 'blocked' });
    expect(first.items.map((item) => [item.number, item.key]))
      .toEqual(seed.refundKeys.slice(0, PAGE_SIZE).map((key, index) => [index + 1, key]));
    expect(first.next).toBe(String(PAGE_SIZE));
    const second = await cases({ bucket: 'blocked', cursor: first.next as string });
    expect(second.items.map((item) => [item.number, item.key]))
      .toEqual(seed.refundKeys.slice(PAGE_SIZE).map((key, index) => [PAGE_SIZE + index + 1, key]));
    expect(second.next).toBeNull();
    expect(new Set([...first.items, ...second.items].map((item) => item.by.join()))).toEqual(new Set(['tool_routing']));
  });

  await test.step('api: the eligible cases are the lookups, held by nothing', async () => {
    const eligible = await cases({ bucket: 'eligible' });
    expect(eligible.items.map((item) => [item.number, item.key, item.by]))
      .toEqual(seed.lookupKeys.map((key, index) => [REFUNDS + index + 1, key, []]));
    expect(eligible.next).toBeNull();
  });

  await test.step('storage: the numbers are the suite members\' numbers', async () => {
    const members = await probe.pg<{ scenario_key: string; number: number }>(
      `SELECT scenario_key, number FROM simulate_harness_suite_member
        WHERE suite_version_id = $1 ORDER BY number`, [seed.suiteVersionId]);
    expect(members.map((member) => [member.number, member.scenario_key]))
      .toEqual(seed.scenarioKeys.map((key, index) => [index + 1, key]));
  });
});
