import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedHarnessEnvironment } from '../../lib/simulate-seed';

// Browser-side waits: one navigation's first paint, not a stage.
const UI_READY = 60_000;
// The world actor e2e/lib/harness_environment_seed.py seeds: no channel, it only acts in the world.
const ACTOR = 'Dana Brooks';

const trialPage = (seed: { environmentId: string; runTestId?: string; testExecutionId?: string;
  callExecutionId?: string }) => `/dashboard/simulate/environments/${seed.environmentId}/runs/${seed.runTestId}`
  + `/${seed.testExecutionId}?rowId=${seed.callExecutionId}`;

test('SIM-E2E-019: World state shows a faulted call apart from a commit, and an actor\'s change made during the call', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-019', area: 'simulate',
    userGoal: 'A developer reads what each trial did to the world: a call the scenario\'s test condition faulted, '
      + 'counted apart from what was committed, and what a world actor decided and committed during the call',
    steps: ['seed a trial whose refund was faulted by its test condition (e2e/lib/harness_environment_seed.py)',
            'open its World state', 'seed a trial where the world actor approved during the call',
            'open its World state'],
    backendChecks: ['seeded through the backend container; the World state record is the one ingestion keeps'],
    browserChecks: ['the faulted trial reads 1 faulted and No change, never Committed: issue_refund',
                    'the actor trial names Dana Brooks, her committed approvals.put and her decision'],
  }),
}, async ({ page, actor }) => {
  test.setTimeout(360_000);
  const base = { organizationId: actor.organizationId, workspaceId: actor.workspaceId, refunds: 1, lookups: 1,
    actors: true };

  await test.step('browser: a faulted call is counted apart and nothing reads committed', async () => {
    const seed = seedHarnessEnvironment({ ...base, trial: 'fault' });
    await page.goto(trialPage(seed));
    await page.getByRole('tab', { name: 'World state' }).click({ timeout: UI_READY });
    await expect(page.getByText(/1 faulted/)).toBeVisible({ timeout: UI_READY });
    await expect(page.getByText('No change: it committed nothing')).toBeVisible();
    await expect(page.getByText('Committed: issue_refund')).toHaveCount(0);
    await expect(page.getByText('Started from the seeded records, as every trial does')).toBeVisible();
  });

  await test.step('browser: the actor\'s decision and its committed change during the call', async () => {
    const seed = seedHarnessEnvironment({ ...base, trial: 'actor' });
    await page.goto(trialPage(seed));
    await page.getByRole('tab', { name: 'World state' }).click({ timeout: UI_READY });
    await expect(page.getByText(ACTOR).first()).toBeVisible({ timeout: UI_READY });
    await expect(page.getByText('Committed: approvals.put')).toBeVisible();
    await expect(page.getByText('What the actors chose')).toBeVisible();
  });
});
