import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

// Browser-side waits: one navigation's first paint, not a stage. The local
// stack slows several-fold beside other workers, so not the 10s expect default.
const UI_READY = 60_000;

const SIMULATE = '/dashboard/simulate';
const ENVIRONMENTS = `${SIMULATE}/environments`;

// Pinned off the pages' <Helmet> titles: AgentDefinitions.jsx, Scenarios.jsx,
// Personas.jsx, RunTests.jsx, and environments/PrebuiltEnvironments.jsx and
// environments/UseTemplate.jsx for the templates.

// Each Prebuilt template URL and its page title. The template id comes from
// frontend/src/api/simulate-environments/_fixtures/prebuiltEnvironments.js.
const TEMPLATE_PAGES = [
  { path: `${ENVIRONMENTS}/templates`, title: 'Prebuilt Environments | Future AGI' },
  { path: `${ENVIRONMENTS}/templates/env-voice-support`, title: 'Use template | Future AGI' },
];

// The Simulate menu for an org created before the cutoff, in order
// (frontend/src/layouts/dashboard/config-navigation.jsx, Simulate section).
const SIMULATE_MENU = ['Environment', 'Agent Definition', 'Scenarios', 'Personas', 'Run Simulation'];

// Each non-Environment menu item and the page it must open.
const LEGACY_PAGES = [
  { item: 'Agent Definition', path: `${SIMULATE}/agent-definitions`, title: 'Agent Definitions' },
  { item: 'Scenarios', path: `${SIMULATE}/scenarios`, title: 'Scenarios' },
  { item: 'Personas', path: `${SIMULATE}/personas`, title: 'Personas' },
  { item: 'Run Simulation', path: `${SIMULATE}/test`, title: 'Run Tests | Dashboard' },
];

interface UserInfo {
  organization: { id: string };
  restrict_simulate_to_environments?: boolean;
}

function exactPath(path: string) {
  return new RegExp(`${path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`);
}

test('SIM-E2E-001: Simulate opens on Environments and an org from before the cutoff keeps every page', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-001', area: 'simulate',
    userGoal: 'A developer opening Simulate lands on Environments, old Harness, Simulator Agent and '
      + 'template links go somewhere sensible, and an org from before the cutoff still reaches every '
      + 'Simulate page (the restricted side for new orgs is not claimed: the harness cannot create an '
      + 'org dated after a cutoff)',
    steps: ['open /dashboard/simulate', 'open the old Harness list, new and job links',
            'open the old Simulator Agent link', 'open the Prebuilt template links',
            'read the Simulate menu', 'open Agent Definition, Scenarios, Personas and Run Simulation from the menu'],
    backendChecks: ["user-info for the actor's org does not report restrict_simulate_to_environments: true "
                    + '(false, or absent on a backend from before the flag)'],
    browserChecks: ['/dashboard/simulate lands on Environments',
                    'harness lands on Environments, harness/new on the Build tab, harness/<id> on environments/<id>',
                    'simulator-agent lands on Environments',
                    'both template URLs stay on their URL and open their page by title',
                    'the Simulate menu is exactly Environment, Agent Definition, Scenarios, Personas, Run Simulation',
                    'each of those four items opens its page by URL and title, never /404'],
  }),
}, async ({ page, actor }, testInfo) => {
  // 12 browser actions, each its own UI_READY step: 12 × 60s = 720s, plus
  // 60s headroom so a slow run fails on the step that ran out.
  test.setTimeout(780_000);
  const jobId = `e2e-sim1-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  await testInfo.attach('seeded', {
    body: JSON.stringify({ organizationId: actor.organizationId, jobId }),
    contentType: 'application/json',
  });

  await test.step('api: the actor org is not restricted to Environments', async () => {
    // The request the app makes at boot (auth-provider.jsx initialize).
    const info = await actor.api.get<UserInfo>('/accounts/user-info/');
    expect(info.organization.id).toBe(actor.organizationId);
    expect(info.restrict_simulate_to_environments).not.toBe(true);
  });

  await test.step('ui: /dashboard/simulate opens Environments', async () => {
    await page.goto(SIMULATE);
    await expect(page).toHaveURL(exactPath(ENVIRONMENTS), { timeout: UI_READY });
  }, { timeout: UI_READY });

  const redirects: [string, string][] = [
    [`${SIMULATE}/harness`, ENVIRONMENTS],
    [`${SIMULATE}/harness/new`, `${ENVIRONMENTS}?tab=build`],
    [`${SIMULATE}/harness/${jobId}`, `${ENVIRONMENTS}/${jobId}`],
    [`${SIMULATE}/simulator-agent`, ENVIRONMENTS],
  ];
  for (const [from, to] of redirects) {
    await test.step(`ui: ${from} lands on ${to}`, async () => {
      await page.goto(from);
      await expect(page).toHaveURL(exactPath(to), { timeout: UI_READY });
    }, { timeout: UI_READY });
  }

  for (const template of TEMPLATE_PAGES) {
    await test.step(`ui: ${template.path} opens its template page`, async () => {
      await page.goto(template.path);
      await expect(page).toHaveURL(exactPath(template.path), { timeout: UI_READY });
      await expect(page).toHaveTitle(template.title, { timeout: UI_READY });
    }, { timeout: UI_READY });
  }

  const menu = page.locator('nav a[href^="/dashboard/simulate/"]');
  await test.step('ui: the Simulate menu lists every Simulate page', async () => {
    await page.goto(ENVIRONMENTS);
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
