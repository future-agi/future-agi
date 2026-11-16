import { test, expect } from '../../lib/existing-customer';
import { flowAnnotation } from '../../lib/flow-meta';
import type { UserInfo } from '../../lib/provisioning';
import { exactPath } from '../../lib/url';

// One navigation's first paint, not a stage (as environments-only-routing.spec.ts).
const UI_READY = 60_000;
const SIMULATE = '/dashboard/simulate';

// The Simulate menu of an org created before the cutoff, in order
// (frontend/src/layouts/dashboard/config-navigation.jsx, Simulate section).
const SIMULATE_MENU = ['Environment', 'Scenarios', 'Run Simulation'];

// Each non-Environment menu item and the page it must open (Helmet titles:
// Scenarios.jsx, RunTests.jsx).
const LEGACY_PAGES = [
  { item: 'Scenarios', path: `${SIMULATE}/scenarios`, title: 'Scenarios' },
  { item: 'Run Simulation', path: `${SIMULATE}/test`, title: 'Run Tests | Dashboard' },
];


test('SIM-E2E-002: an org from before the Simulate cutoff keeps Scenarios and Run Simulation', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-002', area: 'simulate',
    userGoal: 'An existing customer, whose org predates the cutoff, still reaches every Simulate page from the menu',
    steps: ['read the Simulate menu', 'open Scenarios and Run Simulation from the menu'],
    backendChecks: ["user-info for the actor's org reports restrict_simulate_to_environments: false"],
    browserChecks: ['the Simulate menu is exactly Environment, Scenarios, Run Simulation',
                    'each of those two items opens its page by URL and title, never /404'],
  }),
}, async ({ page, actor }, testInfo) => {
  // 3 browser actions × 60s, plus 60s headroom.
  test.setTimeout(240_000);
  await testInfo.attach('existing-customer', { contentType: 'application/json', body: JSON.stringify({ email: actor.email, organizationId: actor.organizationId, workspaceId: actor.workspaceId }) });

  await test.step('api: the actor org is not restricted to Environments', async () => {
    const info = await actor.api.get<UserInfo>('/accounts/user-info/');
    expect(info.organization.id).toBe(actor.organizationId);
    expect(info.restrict_simulate_to_environments).toBe(false);
  });

  const menu = page.locator('nav a[href^="/dashboard/simulate/"]');
  await test.step('ui: the Simulate menu lists every Simulate page', async () => {
    await page.goto(`${SIMULATE}/environments`);
    await expect(menu).toHaveText(SIMULATE_MENU, { timeout: UI_READY });
  }, { timeout: UI_READY });

  for (const legacy of LEGACY_PAGES) {
    await test.step(`ui: ${legacy.item} opens ${legacy.path}`, async () => {
      await menu.filter({ hasText: legacy.item }).click();
      await expect(page).toHaveURL(exactPath(legacy.path), { timeout: UI_READY });
      await expect(page).toHaveTitle(legacy.title, { timeout: UI_READY });
    }, { timeout: UI_READY });
  }
});
