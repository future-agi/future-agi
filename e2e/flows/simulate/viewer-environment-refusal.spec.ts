import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedEnvironment } from '../../lib/environment-seed';

// Coverage this flow does not claim, and what covers it instead:
// - The other simulation writes a Viewer is refused: simulate/tests/test_viewer_role_checks.py
//   tries a Viewer write on every labelled route.
// - Viewer refusals on evidence imports: SIM-E2E-036.

// One browser action's first-paint budget (see README "UI_READY").
const UI_READY = 60_000;
// Pinned from frontend/src/sections/simulate/environments/workspace/workspace.constants.js
// (WORKSPACE_COPY.run and WORKSPACE_COPY.viewer.run) and WorkspaceHeader.jsx's rename label.
const RUN = 'Run simulation';
const RUN_REASON = "You don't have permission to start runs.";
const RENAME = 'Rename environment';
// The run route and its Viewer label, pinned from simulate/views/harness_environment.py
// (`@viewer_denied("start a paid run")` on HarnessEnvironmentViewSet.run).
const run = (env: string) => `/simulate/api/harness-environments/${env}/run/`;
const RUN_ACTION = 'start a paid run';
// tfc/permissions/utils.py::viewer_denied_message, with the code from tfc/utils/api_errors.py.
const REFUSED = `Viewers can't ${RUN_ACTION}. Ask a workspace admin for the Member role.`;

interface Refusal { code: string; detail: string; action?: string }

test(
  'SIM-E2E-037: a Viewer opens a built environment, finds Run and Rename locked with the reason, and is refused a run',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'SIM-E2E-037',
      area: 'simulate',
      userGoal:
        "A Viewer of a workspace opens one of its built environments, sees that they can't start a run or rename it and why, and the API refuses the run they try anyway without starting anything",
      steps: [
        "seed a built, versioned environment in the owner's workspace (lib/environment-seed.ts)",
        'as a Viewer of that workspace, open the environment',
        'hover Run simulation',
        'look at Rename',
        'as the same Viewer, start a run through the API',
      ],
      browserChecks: [
        'the environment opens on its workspace with its name',
        `Run simulation is disabled and its tooltip says "${RUN_REASON}"`,
        'Rename environment is disabled',
      ],
      backendChecks: [
        `the run answers 403 with code viewer_read_only, action "${RUN_ACTION}" and its reason`,
        'simulate_hosted_harness_job has no run job for the environment afterwards',
      ],
    }),
  },
  async ({ browser, scopeActors, scopeProbe }, testInfo) => {
    // Seeding + three UI_READY browser steps + one API call + headroom.
    test.setTimeout(300_000);
    const owner = scopeActors.ownerA;
    const viewer = scopeActors.viewer;
    const name = `e2e-sim37-${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const environment = seedEnvironment({
      organizationId: owner.organizationId,
      workspaceId: owner.workspaceId,
      name,
    });
    await testInfo.attach('seeded', { contentType: 'application/json', body: JSON.stringify(environment) });

    const runJobs = async () =>
      (await scopeProbe.pg<{ total: string }>(
        'SELECT count(*)::text AS total FROM simulate_hosted_harness_job WHERE environment_id=$1',
        [environment.environmentId])).at(0)?.total;
    expect(await runJobs()).toBe('0');

    const context = await scopeActors.openContext(browser, viewer);
    const page = await context.newPage();

    await test.step('ui: the Viewer opens the environment', async () => {
      await page.goto(`/dashboard/simulate/environments/${environment.environmentId}`);
      await expect(page.getByText(name).first()).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });

    await test.step('ui: Run simulation is locked and says why', async () => {
      const button = page.getByRole('button', { name: RUN });
      await expect(button).toBeDisabled({ timeout: UI_READY });
      await button.locator('xpath=..').hover();
      await expect(page.getByRole('tooltip')).toHaveText(RUN_REASON, { timeout: UI_READY });
    }, { timeout: UI_READY });

    await test.step('ui: Rename is locked', async () => {
      await expect(page.getByRole('button', { name: RENAME })).toBeDisabled({ timeout: UI_READY });
    }, { timeout: UI_READY });

    const refused = await scopeActors.send<Refusal>(viewer, 'POST', run(environment.environmentId), {
      scenario_ids: environment.scenarioKeys,
      trials: 1,
    });
    expect(refused.status).toBe(403);
    expect(refused.body).toMatchObject({ code: 'viewer_read_only', action: RUN_ACTION, detail: REFUSED });
    expect(await runJobs()).toBe('0');

    await context.close();
  },
);
