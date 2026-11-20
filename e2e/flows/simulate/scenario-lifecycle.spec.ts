import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedHarnessEnvironment } from '../../lib/simulate-seed';

// Paths from futureagi/simulate/views/harness_job.py (scenarios list) and
// futureagi/simulate/views/harness_environment.py (capabilities/cases).
const JOBS = '/simulate/api/harness-jobs';
const ENVIRONMENTS = '/simulate/api/harness-environments';
const UI_READY = 60_000;
const PROVED = { format: 2, holds: true, proved: true, assumed: [], blocked_reason: null };
const KEPT = {
  format: 2, holds: true, proved: false, kept_as_written: true, assumed: ['lookup_order'],
  solution_steps: 1, blocked_reason: 'solution_assumed',
};

interface Lifecycle { state: string; reason: string | null; reason_text: string | null; warning: string | null }
interface ScenarioRow { scenario_key: string; lifecycle: Lifecycle | null }
interface ScenarioList {
  results: ScenarioRow[];
  lifecycle_counts: Record<string, number>;
}
interface CasePage { items: { key: string; by: string[] }[]; next: string | null }

test('SIM-E2E-037: each scenario shows its lifecycle and only validated cases run', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-037', area: 'simulate',
    userGoal: 'A developer sees which scenarios are proved, kept as written, not validated or archived, '
      + 'and knows which ones a run will hold back',
    steps: ['seed an Environment of four cases: one proved, one kept as written, one with no proof, one archived '
              + '(e2e/lib/harness_environment_seed.py proofs and archived options)',
            'read the Scenarios list with its lifecycle', 'read the archived cases through the filter',
            'read the cases readiness holds back (GET capabilities/cases?bucket=blocked)', 'open the Scenarios tab'],
    backendChecks: ['the list reads Active, Kept as written and Not proved "Not validated on these versions yet."',
                    'the archived case appears only under lifecycle=archived',
                    'lifecycle_counts reads 1 proved, 1 kept as written, 1 not proved',
                    'readiness holds back the unproved case for not_validated and the archived one for scenario_archived'],
    browserChecks: ['the counts line reads "1 proved · 1 kept as written · 1 not proved"',
                    'the rows carry Active, Kept as written and Not proved badges'],
  }),
}, async ({ actor, page }, testInfo) => {
  test.setTimeout(240_000);
  // Cases are named by position ("@n"), since their keys are made by the seed.
  const seed = seedHarnessEnvironment({
    organizationId: actor.organizationId, workspaceId: actor.workspaceId, refunds: 2, lookups: 2,
    proofs: { '@0': PROVED, '@1': KEPT }, archived: ['@3'],
  });
  await testInfo.attach('seeded', { contentType: 'application/json', body: JSON.stringify(seed) });
  const [p, k, u, a] = seed.scenarioKeys;
  const list = (params: Record<string, string> = {}) =>
    actor.api.get<ScenarioList>(`${JOBS}/${seed.environmentId}/scenarios/`, params);

  await test.step('api: each case reads its lifecycle', async () => {
    const page1 = await list();
    const byKey = Object.fromEntries(page1.results.map((row) => [row.scenario_key, row.lifecycle]));
    expect(byKey[p]).toMatchObject({ state: 'active', warning: null });
    expect(byKey[k]).toMatchObject({ state: 'kept_as_written', warning: 'solution_assumed' });
    expect(byKey[u]).toMatchObject({ state: 'draft', reason: 'not_validated', reason_text: 'Not validated on these versions yet.' });
    expect(byKey[a]).toBeUndefined();
    expect(page1.lifecycle_counts).toMatchObject({ proved: 1, kept_as_written: 1, not_proved: 1, archived: 1 });
  });

  await test.step('api: the archived case is listed only under its filter', async () => {
    const onlyArchived = await list({ lifecycle: 'archived' });
    expect(onlyArchived.results.map((row) => [row.scenario_key, row.lifecycle?.state])).toEqual([[a, 'archived']]);
  });

  await test.step('api: readiness holds back the unvalidated and the archived case', async () => {
    const blocked = await actor.api.get<CasePage>(`${ENVIRONMENTS}/${seed.environmentId}/capabilities/cases/`,
      { bucket: 'blocked' });
    expect(Object.fromEntries(blocked.items.map((item) => [item.key, item.by])))
      .toEqual({ [u]: ['not_validated'], [a]: ['scenario_archived'] });
  });

  await test.step('ui: the Scenarios tab shows the counts and the badges', async () => {
    await page.goto(`/dashboard/simulate/environments/${seed.environmentId}?tab=scenarios`);
    await expect(page.getByTestId('scenario-proof-counts'))
      .toHaveText('1 proved · 1 kept as written · 1 not proved', { timeout: UI_READY });
    await expect(page.getByTestId('lifecycle-badge').filter({ hasText: 'Active' })).toHaveCount(1);
    await expect(page.getByTestId('lifecycle-badge').filter({ hasText: 'Kept as written' })).toHaveCount(1);
    await expect(page.getByTestId('lifecycle-badge').filter({ hasText: 'Not proved' })).toHaveCount(1);
  }, { timeout: UI_READY });
});
