import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { exactPath } from '../../lib/url';

// Browser-side waits: one navigation's first paint, not a stage. The local
// stack slows several-fold beside other workers, so not the 10s expect default.
const UI_READY = 60_000;

const SIMULATE = '/dashboard/simulate';
const ENVIRONMENTS = `${SIMULATE}/environments`;

// Pinned off the template pages' <Helmet> titles:
// environments/PrebuiltEnvironments.jsx and environments/UseTemplate.jsx.

// Each Prebuilt template URL and its page title. The template id comes from
// frontend/src/api/simulate-environments/_fixtures/prebuiltEnvironments.js.
const TEMPLATE_PAGES = [
  { path: `${ENVIRONMENTS}/templates`, title: 'Prebuilt Environments | Future AGI' },
  { path: `${ENVIRONMENTS}/templates/env-voice-support`, title: 'Use template | Future AGI' },
];

// The Simulate menu of an org created after the cutoff, the ordinary actor
// (frontend/src/layouts/dashboard/config-navigation.jsx, Simulate section).
const NEW_ORG_MENU = ['Environment'];

// Pages the router does not register for that org (any agent id will do).
const HIDDEN_PAGES = [
  `${SIMULATE}/scenarios`,
  `${SIMULATE}/test`,
  `${SIMULATE}/agent-definitions/00000000-0000-0000-0000-000000000000`,
];

test('SIM-E2E-001: Simulate opens on Environments, and an org from after the cutoff sees only Environments', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-001', area: 'simulate',
    userGoal: 'A developer opening Simulate lands on Environments, old Harness, Simulator Agent, Personas and template links go somewhere sensible, Personas opens as a tab on Environments, and a new org sees only Environment in the menu and gets not found from direct Scenarios, Run Simulation and Agent Definition links',
    steps: ['open /dashboard/simulate', 'open the old Harness list, new and job links',
            'open the old Simulator Agent link', 'open the old Personas link', 'open the Personas tab',
            'open the Prebuilt template links', 'read the Simulate menu',
            'open the old Agent Definition link', 'open the Scenarios, Run Simulation and Agent Definition links directly'],
    browserChecks: ['/dashboard/simulate lands on Environments',
                    'harness lands on Environments, harness/new on the Build tab, harness/<id> on environments/<id>',
                    'simulator-agent lands on Environments',
                    'personas lands on the Environments Personas tab',
                    'the Personas tab is selected and shows the persona search',
                    'both template URLs stay on their URL and open their page by title',
                    'agent-definitions lands on Connected agents',
                    'the Simulate menu is exactly Environment',
                    '/dashboard/simulate/scenarios, /test and /agent-definitions/<id> land on /404'],
  }),
}, async ({ page, actor }, testInfo) => {
  // 14 browser actions, each its own UI_READY step: 14 × 60s = 840s, plus
  // 60s headroom so a slow run fails on the step that ran out.
  test.setTimeout(900_000);
  const jobId = `e2e-sim1-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  await testInfo.attach('seeded', {
    body: JSON.stringify({ organizationId: actor.organizationId, jobId }),
    contentType: 'application/json',
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
    [`${SIMULATE}/personas`, `${ENVIRONMENTS}?tab=personas`],
    [`${SIMULATE}/agent-definitions`, '/dashboard/agents/connected'],
  ];
  for (const [from, to] of redirects) {
    await test.step(`ui: ${from} lands on ${to}`, async () => {
      await page.goto(from);
      await expect(page).toHaveURL(exactPath(to), { timeout: UI_READY });
    }, { timeout: UI_READY });
  }

  await test.step('ui: the Personas tab lists personas', async () => {
    await page.goto(`${ENVIRONMENTS}?tab=personas`);
    await expect(page.getByRole('tab', { name: 'Personas' }))
      .toHaveAttribute('aria-selected', 'true', { timeout: UI_READY });
    // PersonaListView's search field.
    await expect(page.getByPlaceholder('Search personas')).toBeVisible({ timeout: UI_READY });
  }, { timeout: UI_READY });

  for (const template of TEMPLATE_PAGES) {
    await test.step(`ui: ${template.path} opens its template page`, async () => {
      await page.goto(template.path);
      await expect(page).toHaveURL(exactPath(template.path), { timeout: UI_READY });
      await expect(page).toHaveTitle(template.title, { timeout: UI_READY });
    }, { timeout: UI_READY });
  }

  const menu = page.locator('nav a[href^="/dashboard/simulate/"]');
  await test.step('ui: the Simulate menu is Environment only', async () => {
    await page.goto(ENVIRONMENTS);
    await expect(menu).toHaveText(NEW_ORG_MENU, { timeout: UI_READY });
  }, { timeout: UI_READY });

  for (const hidden of HIDDEN_PAGES) {
    await test.step(`ui: a direct ${hidden} link lands on /404`, async () => {
      await page.goto(hidden);
      await expect(page).toHaveURL(exactPath('/404'), { timeout: UI_READY });
    }, { timeout: UI_READY });
  }
});
