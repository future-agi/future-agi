import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned off the source:
// - frontend/src/sections/gateway/shell/page-status.js HIDDEN_GATEWAY_PAGES: the
//   capability matrix redirects to Models and policies to Guardrails; a hidden page is
//   in no navigation. MCP tools is no longer hidden on this build (its page and the
//   Integrations page are in the rail). routes/sections/dashboard.jsx wraps
//   each hidden route in gatewayPage(), whose GatewayRedirect (shell/GatewayRedirect.jsx)
//   keeps the query and the hash.
// - the AI Gateway rail (shell/gateway-rail.js RAIL, shell/GatewayRail.jsx: a
//   navigation named "AI Gateway", one group per subheader, one link per item), less
//   the hidden pages' items; a group left with no item is dropped.
// - whether the deployment offers managed models: GET /agentcc/managed-models/
//   `available` (agentcc/views/managed_models.py list). A deployment that offers none
//   has no catalog: the rail has no Catalog item (layouts/dashboard/config-navigation.jsx
//   noManagedModels), the capability matrix opens the gateway overview
//   (shell/CapabilitiesRedirect.jsx) and /models opens /models/auto
//   (models/ModelsSection.jsx); the managed models strip is nowhere and the console
//   never reads the managed catalog.
const GW = '/dashboard/gateway';
// Where the capability matrix lands: the Catalog (Models) where managed models are
// offered, the gateway overview where none are.
const CAPABILITIES_LANDING = (managed: boolean) => (managed ? `${GW}/models` : GW);
const redirects = (managed: boolean): [from: string, to: string][] => [
  [`${GW}/capabilities`, CAPABILITIES_LANDING(managed)],
  [`${GW}/policies`, `${GW}/guardrails`],
  [`${GW}/policies/access`, `${GW}/guardrails`],
];
// The rail, in order, with the hidden pages' items left out (Policies, so its group
// is named "Guardrails"). Integrations and MCP tools sit next to Providers;
// Catalog is absent when no managed models are offered.
const RAIL_GROUPS = ['Models', 'Providers, Tools & Keys', 'Guardrails', 'Observability', 'Settings'];
const railLinks = (managed: boolean) => [
  'Overview', ...(managed ? ['Catalog'] : []), 'Auto routing', 'Providers', 'Integrations', 'MCP tools', 'API keys', 'Fallbacks',
  'Guardrails', 'Logs', 'Analytics', 'Monitoring', 'Alerts', 'Sessions', 'Runs', 'Custom properties',
  'Webhooks', 'Gateway settings', 'Budgets',
];
// ManagedModelsCard.jsx at 3c20e189ea: an OFF org has a notice, not an opt-in control.
const OFF_NOTICE = 'Future AGI credits are off for this organization.';
// ManagedModelsCard.jsx current/legacy names only, excluding CatalogFacets.jsx's
// Access filter named "Future AGI credits (<count>)".
const CREDITS_CONTROL_NAME = /^Future AGI credits(?:: one key for every model)?$/;
const MANAGED_STATUS_PATH = '/agentcc/managed-models/';
const CATALOG_PATH = '/agentcc/managed-models/catalog/';
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// scopeActors provisioning 120 + three redirects and the rail at one page load each
// (4 x STEP_BUDGET 75 = 300) + the managed models strip and three credit pages
// 4 x 75 (300) = 720 s; the ceiling stays 840 s, as before MCP left the redirects.
// Previous 840s ceiling + one independent onboarding observation step (75s).
const FLOW_TIMEOUT = 915_000;

test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-021: hidden gateway pages redirect to the nearest working page and leave the rail',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-021',
      area: 'gateway',
      userGoal:
        'An organization owner never lands on a gateway page or control that cannot work, with managed models offered or not: old capability and policy links redirect, Integrations and MCP tools remain available, Catalog is absent where no managed models are offered',
      steps: [
        'read whether the deployment offers managed models',
        'open the capability matrix, policies and a policies tab by URL, each with a query and a hash',
        'read the AI Gateway rail and the fresh OFF organization\'s Add a provider onboarding link',
        'open Models and check catalogue availability',
      ],
      backendChecks: [
        'where no managed models are offered, the console sends no request to /agentcc/managed-models/catalog/ either',
      ],
      browserChecks: [
        'each hidden URL lands on its redirect target with the query and hash kept: capabilities on Models (the gateway overview where no managed models are offered), policies on Guardrails',
        'the rail lists Integrations and MCP tools next to Providers, with no Policies or unavailable Catalog',
        'no Future AGI credits model strip where no Future AGI models are offered',
        'an available but OFF organization has the neutral credits notice and no credits switch',
        'fresh OFF onboarding links Add a provider to /dashboard/gateway/providers, not Open the catalog or a credits opt-in offer',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    const owner = scopes.ownerA;
    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    const catalogCalls: string[] = [];
    page.on('request', (r) => {
      const path = new URL(r.url()).pathname;
      if (path.endsWith(CATALOG_PATH)) catalogCalls.push(`${r.method()} ${r.url()}`);
    });
    const marker = `e2e-gw21-${testInfo.workerIndex}-${Date.now().toString(36)}`;
    // The shape this deployment must show: with managed models offered, or with none.
    const status = (await owner.api.get<{ result: { available: boolean; enabled: boolean } }>(MANAGED_STATUS_PATH)).result;
    expect(status.enabled, 'fresh organization remains managed OFF; no providers are seeded').toBe(false);
    const managed = status.available === true;
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ organizationId: owner.organizationId, marker, managedModelsOffered: managed,
        note: 'no seeding beyond scopeActors: an owner of a fresh organization' }, null, 2),
    });

    try {
      for (const [from, to] of redirects(managed)) {
        await test.step(`UI: ${from} opens ${to}, query and hash kept`, async () => {
          await page.goto(`${from}?from=${marker}#keep`, { waitUntil: 'domcontentloaded' });
          await expect(page).toHaveURL(
            (url) => url.pathname === to && url.searchParams.get('from') === marker && url.hash === '#keep',
            { timeout: UI_READY });
        }, { timeout: STEP_BUDGET });
      }

      await test.step('UI: the rail offers no hidden page', async () => {
        await page.goto(GW, { waitUntil: 'domcontentloaded' });
        const rail = page.getByRole('navigation', { name: 'AI Gateway' });
        await expect(rail.getByRole('link', { name: 'Overview', exact: true })).toBeVisible({ timeout: UI_READY });
        expect(await rail.getByRole('group').evaluateAll((groups) =>
          groups.map((g) => g.getAttribute('aria-label')).filter((label) => label))).toEqual(RAIL_GROUPS);
        await expect(rail.getByRole('link')).toHaveText(railLinks(managed), { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      await test.step('UI: fresh OFF onboarding points to the organization\'s own provider', async () => {
        // GettingStarted.jsx / onboarding/steps.js: no provider was seeded, and
        // the already-loaded Overview must offer Add a provider, not opt-in.
        const gettingStarted = page.getByTestId('overview-getting-started');
        await expect(gettingStarted.getByText('Get started', { exact: true })).toBeVisible({ timeout: UI_READY });
        const first = gettingStarted.getByRole('listitem').first();
        await expect(first.getByRole('link', { name: 'Add a provider', exact: true }))
          .toHaveAttribute('href', `${GW}/providers`, { timeout: UI_READY });
        await expect(gettingStarted.getByRole('link', { name: 'Open the catalog', exact: true })).toHaveCount(0, { timeout: UI_READY });
        await expect(gettingStarted.getByText(/Turn on Future AGI credits/)).toHaveCount(0, { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      if (managed) {
        await test.step('UI: Models retains its catalog with a neutral organization-OFF notice', async () => {
          await page.goto(`${GW}/models`, { waitUntil: 'domcontentloaded' });
          await expect(page.getByRole('alert').filter({ hasText: OFF_NOTICE })).toHaveText(OFF_NOTICE, { timeout: UI_READY });
          await expect(page.getByRole('checkbox', { name: CREDITS_CONTROL_NAME, includeHidden: true })).toHaveCount(0, { timeout: UI_READY });
          await expect(page.getByRole('switch', { name: CREDITS_CONTROL_NAME, includeHidden: true })).toHaveCount(0, { timeout: UI_READY });
        }, { timeout: STEP_BUDGET });
      } else {
        await test.step('UI: Models opens Auto routing, with no managed models strip', async () => {
          await page.goto(`${GW}/models`, { waitUntil: 'domcontentloaded' });
          await expect(page).toHaveURL((url) => url.pathname === `${GW}/models/auto`, { timeout: UI_READY });
          await expect(page.getByRole('heading', { level: 1, name: 'Auto routing' })).toBeVisible({ timeout: UI_READY });
          await expect(page.getByRole('checkbox', { name: CREDITS_CONTROL_NAME, includeHidden: true })).toHaveCount(0, { timeout: UI_READY });
          await expect(page.getByRole('switch', { name: CREDITS_CONTROL_NAME, includeHidden: true })).toHaveCount(0, { timeout: UI_READY });
        }, { timeout: STEP_BUDGET });
      }

      await testInfo.attach('catalog-requests', { contentType: 'application/json', body: JSON.stringify(catalogCalls) });
      if (!managed) expect(catalogCalls).toEqual([]);
    } finally {
      await context.close();
    }
  },
);
