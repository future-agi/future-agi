import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedHarnessEnvironment } from '../../lib/simulate-seed';

// Browser-side waits: one navigation's first paint, or a run request's answer, not a stage.
const UI_READY = 60_000;
// The header's tag (frontend/.../runs/detail/RunReadinessTag.jsx): one measured row not Supported, tool routing
// Blocked as production_only (the same report SIM-E2E-030 pins), and the refund it holds back.
const TAG = 'Started with 1 limit';
const HELD = '1 of 2 cases held back';

test('SIM-E2E-024: a run started with a case held back says so in its header, and a run from before says nothing', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-024', area: 'simulate',
    userGoal: 'A developer who starts a run on a pairing that holds a case back reads, on the run itself, the readiness '
      + 'it started under and the cases it held back; a run started before runs kept readiness shows no tag',
    steps: ['seed a built, certified Environment of 1 refund and 1 lookup whose contract routes issue_refund to '
              + 'production, with one finished run from before readiness was kept (e2e/lib/harness_environment_seed.py)',
            'open the Environment and click Run simulation', 'run both scenarios from the run dialog',
            'land on the new run and open its readiness tag', 'open the finished run from before'],
    backendChecks: ['the new run job keeps the readiness it was created with: answer blocked, and its selection holds '
                      + 'back exactly the refund, by tool_routing'],
    browserChecks: [`the new run's header reads "${TAG}" and its popover "${HELD}"`,
                    'the run from before has no readiness tag'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // One seed through the backend container (Django start-up), one run request and three page loads.
  test.setTimeout(240_000);
  const seed = seedHarnessEnvironment({ organizationId: actor.organizationId, workspaceId: actor.workspaceId,
    refunds: 1, lookups: 1, productionOnly: 'issue_refund', trial: true });
  await testInfo.attach('seeded', { contentType: 'application/json',
    body: JSON.stringify({ organizationId: actor.organizationId, workspaceId: actor.workspaceId, ...seed }) });

  const runId = await test.step('browser: run both scenarios from the run dialog', async () => {
    await page.goto(`/dashboard/simulate/environments/${seed.environmentId}`);
    await page.getByRole('button', { name: 'Run simulation' }).first().click({ timeout: UI_READY });
    const dialog = page.getByRole('dialog');
    const started = page.waitForResponse(
      (response) => response.url().includes(`/harness-environments/${seed.environmentId}/run/`)
        && response.request().method() === 'POST',
      { timeout: UI_READY });
    await dialog.getByRole('button', { name: 'Run', exact: true }).click({ timeout: UI_READY });
    const response = await started;
    expect(response.status()).toBe(202);
    const body = (await response.json()) as { job_id: string; test_execution_id: string };
    await testInfo.attach('run', { contentType: 'application/json', body: JSON.stringify(body) });
    await expect(page).toHaveURL(new RegExp(`/runs/[0-9a-f-]+/${body.test_execution_id}`), { timeout: UI_READY });
    return body.job_id;
  });

  await test.step('storage: the run keeps the readiness it started under', async () => {
    const [run] = await probe.pg<{ kind: string; held: string; by: string }>(
      `SELECT capability_snapshot->'answer'->>'kind' AS kind,
              payload->'metadata'->'readiness_selection'->'held_back'->>'keys' AS held,
              payload->'metadata'->'readiness_selection'->'held_back'->>'by' AS by
         FROM simulate_hosted_harness_job WHERE id = $1`, [runId]);
    expect(run.kind).toBe('blocked');
    expect(JSON.parse(run.held)).toEqual(seed.refundKeys);
    expect(Object.keys(JSON.parse(run.by))).toEqual(['tool_routing']);
  });

  await test.step('browser: the run header says what it started with', async () => {
    await page.getByRole('button', { name: TAG }).click({ timeout: UI_READY });
    await expect(page.getByText(HELD)).toBeVisible({ timeout: UI_READY });
  });

  await test.step('browser: a run from before readiness was kept shows no tag', async () => {
    await page.goto(`/dashboard/simulate/environments/${seed.environmentId}/runs/${seed.runTestId}`
      + `/${seed.testExecutionId}`);
    await expect(page.getByRole('button', { name: 'Back' })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByRole('button', { name: /^(Started with|Ready when started)/ })).toHaveCount(0);
  });
});
