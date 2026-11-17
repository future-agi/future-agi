import { test, expect } from '../lib/fixtures';
import { seedEnvironment } from '../lib/environment-seed';

// One browser action's first-paint budget (see README "UI_READY").
const UI_READY = 60_000;

// lib/environment-seed.ts stands in for a builder build. Prove what a flow relies on:
// the job is a completed environment with a default version pair, in the actor's
// workspace, and its Scenarios tab opens and lists exactly the seeded scenarios.
test('a seeded environment is built, versioned and opens on its Scenarios tab', async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(120_000);
  const name = `e2e-env-seed-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const seeded = seedEnvironment({ organizationId: actor.organizationId, workspaceId: actor.workspaceId, name });
  await testInfo.attach('environment-seed', { contentType: 'application/json', body: JSON.stringify(seeded) });

  const jobs = await probe.pg<{ state: string; name: string; workspace_id: string;
    environment_version_id: string | null; suite_version_id: string | null }>(
    `SELECT state, name, workspace_id::text, environment_version_id::text, suite_version_id::text
       FROM simulate_hosted_harness_job WHERE id = $1`, [seeded.environmentId]);
  expect(jobs).toEqual([{
    state: 'completed',
    name,
    workspace_id: actor.workspaceId,
    environment_version_id: seeded.environmentVersionId,
    suite_version_id: seeded.suiteVersionId,
  }]);

  await page.goto(`/dashboard/simulate/environments/${seeded.environmentId}?tab=scenarios`);
  await expect(page.getByText(name).first()).toBeVisible({ timeout: UI_READY });
  for (const scenario of seeded.scenarioNames) {
    await expect(page.getByRole('row').filter({ hasText: scenario })).toHaveCount(1, { timeout: UI_READY });
  }
});
