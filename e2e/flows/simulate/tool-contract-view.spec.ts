import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedHarnessEnvironment } from '../../lib/simulate-seed';

// Browser-side waits: one navigation's first paint, not a stage.
const UI_READY = 60_000;
// Path from futureagi/simulate/views/harness_environment.py (environment detail, its contract section).
const ENVIRONMENTS = '/simulate/api/harness-environments';

interface Detail { contract: { tools: { name: string; returns?: Record<string, string> }[];
  door_reach: Record<string, boolean>; production_only: string[] } }

test('SIM-E2E-017: the Tools card shows each tool\'s inputs with units, what it returns, and a production-only tool as Unsupported', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-017', area: 'simulate',
    userGoal: 'A developer inspects each tool of the test world: its inputs with units, the fields it returns, '
      + 'whether it changes records, whether it is reset between trials, and which tools only production answers',
    steps: ['seed a built, versioned Environment whose contract records each tool\'s fields and one tool that '
              + 'reaches production (e2e/lib/harness_environment_seed.py)',
            'read the environment (GET harness-environments/<id>/)', 'open the Contract tab',
            'expand lookup_order', 'read issue_refund\'s Unsupported label'],
    backendChecks: ['the contract section names issue_refund production-only',
                    'lookup_order returns status and total'],
    browserChecks: ['lookup_order reads "Reads only" and its returned fields with units',
                    'issue_refund reads Unsupported, never Simulated'],
  }),
}, async ({ page, actor }, testInfo) => {
  test.setTimeout(240_000);
  const seed = seedHarnessEnvironment({ organizationId: actor.organizationId, workspaceId: actor.workspaceId,
    refunds: 1, lookups: 1, toolDetail: true, productionOnly: 'issue_refund' });
  await testInfo.attach('seeded', { contentType: 'application/json', body: JSON.stringify(seed) });

  await test.step('api: the contract names the production-only tool and each tool\'s returns', async () => {
    const detail = await actor.api.get<Detail>(`${ENVIRONMENTS}/${seed.environmentId}/`);
    expect(detail.contract.production_only).toEqual(['issue_refund']);
    const lookup = detail.contract.tools.find((one) => one.name === 'lookup_order');
    expect(lookup?.returns).toEqual({ status: 'string', total: 'integer, cents, USD' });
    expect(detail.contract.door_reach.lookup_order).toBe(true);
  });

  await test.step('browser: a tool\'s contract, and Unsupported for the production-only one', async () => {
    await page.goto(`/dashboard/simulate/environments/${seed.environmentId}?tab=contract`);
    await page.getByRole('button', { name: 'Show the contract of lookup_order' }).click({ timeout: UI_READY });
    await expect(page.getByText('status: string')).toBeVisible({ timeout: UI_READY });
    await expect(page.getByText('total: integer, cents, USD')).toBeVisible();
    await expect(page.getByText('Reads only')).toBeVisible();
    await expect(page.getByText('Reset to the seeded records before each trial').first()).toBeVisible();
    const refund = page.getByRole('button', { name: 'Show the contract of issue_refund' });
    await expect(refund.getByText('Unsupported', { exact: true })).toBeVisible();
    await expect(refund.getByText('Simulated', { exact: true })).toHaveCount(0);
  });
});
