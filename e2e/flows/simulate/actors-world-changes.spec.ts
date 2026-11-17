import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedHarnessEnvironment } from '../../lib/simulate-seed';

// Browser-side waits: one navigation's first paint, not a stage.
const UI_READY = 60_000;
// Paths from futureagi/simulate/views/harness_environment.py (actors), harness_job.py (scenarios) and
// futureagi/simulate/urls.py (v3/call-executions).
const ENVIRONMENTS = '/simulate/api/harness-environments';
const JOBS = '/simulate/api/harness-jobs';
const CALLS = '/simulate/v3/call-executions';
// The world actor e2e/lib/harness_environment_seed.py seeds: no channel, it only acts in the world.
const ACTOR = { id: 'claims_supervisor', name: 'Dana Brooks' };

interface Cast { actors: { id: string; name: string; role: string; channel?: string | null }[] }
interface Verdict { problems: { field: string | null; message: string }[]; conflicts: unknown[];
  suggested_id: string | null; over_by: number }
interface ScenarioPage { results: { scenario_key: string; actors: { id: string }[] }[] }
interface TrialChange { who: string; committed: number; committed_actions: string[]; read?: number; read_actions?: string[] }
interface CallDetail { trial_state: { state_changes: TrialChange[] } | null }

test('SIM-E2E-010: an actor with no channel is named on Scenarios, and World state reads a read apart from a write', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-010', area: 'simulate',
    userGoal: 'A developer sees every actor of the cast on the scenarios that include it, speaking or not, '
      + 'and a trial\'s World state says what the agent only read apart from what it committed',
    steps: ['seed a built, versioned Environment whose refunds include a world actor with no channel and one '
              + 'finished trial (e2e/lib/harness_environment_seed.py)',
            'read the cast (GET actors)', 'check an edit to the actor (POST actor-validation)',
            'read the scenarios (GET harness-jobs/<id>/scenarios)',
            'read the trial (GET v3/call-executions/<id>)', 'open the Actors tab and pick a side from Add actor',
            'open the Scenarios tab',
            'open the trial\'s World state'],
    backendChecks: ['the cast lists the actor with no channel',
                    'checking an edit to it finds nothing to fix and writes nothing',
                    'the refund scenario includes it by id',
                    'the trial\'s World state count 1 committed (issue_refund) and 1 read (lookup_order)'],
    browserChecks: ['Add actor offers the caller\'s side and the world, and opens Add actor on the side picked',
                    'the Scenarios table names the actor, never its raw id',
                    'World state reads "1 read" and "Committed: issue_refund"'],
  }),
}, async ({ page, actor }, testInfo) => {
  // One seed through the backend container (Django start-up), three API calls and two page loads.
  test.setTimeout(240_000);
  const seed = seedHarnessEnvironment({ organizationId: actor.organizationId, workspaceId: actor.workspaceId,
    refunds: 1, lookups: 1, actors: true, trial: true });
  await testInfo.attach('seeded', { contentType: 'application/json',
    body: JSON.stringify({ organizationId: actor.organizationId, workspaceId: actor.workspaceId, ...seed }) });

  await test.step('api: the cast lists the actor with no channel', async () => {
    const cast = await actor.api.get<Cast>(`${ENVIRONMENTS}/${seed.environmentId}/actors/`);
    const listed = cast.actors.find((one) => one.id === ACTOR.id);
    expect(listed).toMatchObject({ name: ACTOR.name });
    expect(listed?.channel).toBeFalsy();
  });

  await test.step('api: checking an edit to the actor finds nothing to fix and writes nothing', async () => {
    const verdict = await actor.api.post<Verdict>(`${ENVIRONMENTS}/${seed.environmentId}/actor-validation/`,
      { op: 'replace', actor_id: ACTOR.id, changes: { role: 'claims lead' } });
    expect(verdict).toMatchObject({ problems: [], conflicts: [], over_by: 0 });
    const cast = await actor.api.get<Cast>(`${ENVIRONMENTS}/${seed.environmentId}/actors/`);
    expect(cast.actors.find((one) => one.id === ACTOR.id)?.role).not.toBe('claims lead');
  });

  await test.step('browser: Add actor asks the side first and opens the form on it', async () => {
    await page.goto(`/dashboard/simulate/environments/${seed.environmentId}?tab=actors`);
    await page.getByRole('button', { name: 'Add actor' }).click({ timeout: UI_READY });
    await expect(page.getByRole('menuitem', { name: /On the caller's side/ })).toBeVisible();
    await page.getByRole('menuitem', { name: /In the world/ }).click();
    await expect(page.getByText('In the world · added to this environment\'s draft')).toBeVisible();
  });

  await test.step('api: the refund scenario includes it by id', async () => {
    const page1 = await actor.api.get<ScenarioPage>(`${JOBS}/${seed.environmentId}/scenarios/`);
    const refund = page1.results.find((one) => one.scenario_key === seed.refundKeys[0]);
    expect(refund?.actors.map((one) => one.id)).toEqual([ACTOR.id]);
  });

  await test.step('api: World state keeps a read apart from the committed write', async () => {
    const call = await actor.api.get<CallDetail>(`${CALLS}/${seed.callExecutionId}/`);
    const agent = call.trial_state?.state_changes.find((one) => one.who === 'agent');
    expect(agent).toMatchObject({ committed: 1, committed_actions: ['issue_refund'], read: 1,
      read_actions: ['lookup_order'] });
  });

  await test.step('browser: the Scenarios table names the actor', async () => {
    await page.goto(`/dashboard/simulate/environments/${seed.environmentId}?tab=scenarios`);
    await expect(page.getByText(`With ${ACTOR.name}`).first()).toBeVisible({ timeout: UI_READY });
    await expect(page.getByText(ACTOR.id, { exact: true })).toHaveCount(0);
  });

  await test.step('browser: World state reads the read apart from the write', async () => {
    // The run's page with its one trial open (`rowId`, useOpenCallParam.js).
    await page.goto(`/dashboard/simulate/environments/${seed.environmentId}/runs/${seed.runTestId}`
      + `/${seed.testExecutionId}?rowId=${seed.callExecutionId}`);
    await page.getByRole('tab', { name: 'World state' }).click({ timeout: UI_READY });
    await expect(page.getByText(/1 read/)).toBeVisible({ timeout: UI_READY });
    await expect(page.getByText('Committed: issue_refund')).toBeVisible({ timeout: UI_READY });
  });
});
