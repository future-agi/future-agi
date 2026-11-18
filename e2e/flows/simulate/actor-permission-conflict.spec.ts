import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedHarnessEnvironment } from '../../lib/simulate-seed';

// Browser-side waits: one navigation's first paint, not a stage.
const UI_READY = 60_000;
// Paths from futureagi/simulate/views/harness_environment.py (actors, actor-validation).
const ENVIRONMENTS = '/simulate/api/harness-environments';
// The world actor e2e/lib/harness_environment_seed.py seeds: the only one who may take approvals.put.
const HOLDER = { id: 'claims_supervisor', name: 'Dana Brooks' };
const ACTION = 'approvals.put';
const NEW_ACTOR = 'Sam Ortiz';

interface Cast { actors: { id: string; name: string; permitted_actions: string[] }[] }
interface Verdict { problems: { field: string | null; message: string }[];
  conflicts: { actor: string; action: string; holder: string; holder_name: string }[];
  suggested_id: string | null; over_by: number }

test('SIM-E2E-020: giving an actor an action only another actor holds is flagged before saving', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-020', area: 'simulate',
    userGoal: 'A developer adding an actor sees, before saving, that an action they give it is held only by another '
      + 'actor, and saves only once they confirm both should have it',
    steps: ['seed a built, versioned Environment whose world actor alone may take approvals.put '
              + '(e2e/lib/harness_environment_seed.py)',
            'check a new actor given approvals.put (POST actor-validation)',
            'open the Actors tab and add an actor in the world',
            'name it and give it approvals.put',
            'tick Both should have it'],
    backendChecks: ['the check names Dana Brooks as the only holder of approvals.put and writes nothing'],
    browserChecks: ['the form says approvals.put is held only by Dana Brooks before anything is saved',
                    'Save to draft stays off until Both should have it is ticked'],
  }),
}, async ({ page, actor }, testInfo) => {
  // One seed through the backend container (Django start-up), two API calls and one page load.
  test.setTimeout(240_000);
  const seed = seedHarnessEnvironment({ organizationId: actor.organizationId, workspaceId: actor.workspaceId,
    refunds: 1, lookups: 1, actors: true });
  await testInfo.attach('seeded', { contentType: 'application/json',
    body: JSON.stringify({ organizationId: actor.organizationId, workspaceId: actor.workspaceId, ...seed }) });

  await test.step('api: the check names the only holder and writes nothing', async () => {
    const verdict = await actor.api.post<Verdict>(`${ENVIRONMENTS}/${seed.environmentId}/actor-validation/`,
      { op: 'create', changes: { side: 'world', name: NEW_ACTOR, permitted_actions: [ACTION] } });
    expect(verdict.conflicts).toEqual([
      expect.objectContaining({ action: ACTION, holder: HOLDER.id, holder_name: HOLDER.name }),
    ]);
    const cast = await actor.api.get<Cast>(`${ENVIRONMENTS}/${seed.environmentId}/actors/`);
    expect(cast.actors.map((one) => one.name)).not.toContain(NEW_ACTOR);
  });

  await test.step('browser: the conflict shows before saving and Save waits for the confirmation', async () => {
    await page.goto(`/dashboard/simulate/environments/${seed.environmentId}?tab=actors`);
    await page.getByRole('button', { name: 'Add actor' }).click({ timeout: UI_READY });
    await page.getByRole('menuitem', { name: /In the world/ }).click();
    await page.getByLabel('Name', { exact: true }).fill(NEW_ACTOR);
    await page.getByLabel('World actions they may take (optional)').click();
    await page.getByRole('option', { name: new RegExp(ACTION.replace('.', '\\.')) }).click();
    const conflict = page.getByTestId('actor-permission-conflict');
    await expect(conflict).toContainText(`${ACTION} is held only by ${HOLDER.name} today`, { timeout: UI_READY });
    const save = page.getByRole('button', { name: 'Save to draft' });
    await expect(save).toBeDisabled();
    await conflict.getByLabel('Both should have it').check();
    await expect(save).toBeEnabled({ timeout: UI_READY });
  });
});
