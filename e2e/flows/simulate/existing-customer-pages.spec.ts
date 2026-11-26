import { randomUUID } from 'node:crypto';
import type { Page } from '@playwright/test';
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

// A Run Simulation run's page (test/:testId/:executionId, routed only for an
// org that is not restricted to Environments: routes/sections/dashboard.jsx)
// holds the Debug failures button, gated by useFixMyAgentBlocked
// (sections/test-detail/common.js). The stack has no simulation runner, so it
// cannot hold a finished run: stub only the run's four reads, as SIM-E2E-007
// does. The page, the guard and the tooltip are the product's own.
const MIN_CONNECTED_CALLS = 15;
const BLOCKED_REASON = `You need at least ${MIN_CONNECTED_CALLS} connected calls to debug failures.`;

type Json = Record<string, unknown>;
type FinishedRun = { testId: string; executionId: string; connectedCalls: number };

const endsWith = (suffix: string) => (url: URL) => url.pathname.endsWith(suffix);

async function stubFinishedRun(page: Page, run: FinishedRun) {
  // GET /simulate/run-tests/<id>/ (RunTestDetailView): only what the run page
  // reads to pick its tabs (TestRunDetailView.jsx) and its header.
  const runTest: Json = {
    id: run.testId,
    name: `e2e-sim2-${run.connectedCalls}`,
    source_type: 'agent_definition',
    agent_version: { configuration_snapshot: { agent_type: 'voice' } },
    scenarios: [],
    created_at: '2026-11-14T09:00:00Z',
  };
  await page.route(endsWith(`/simulate/run-tests/${run.testId}/`), (route) => route.fulfill({ json: runTest }));
  // GET /simulate/run-tests/<id>/executions/ (RunTestExecutionsView): this run is
  // the latest, so the page shows no "newer execution" banner.
  await page.route(endsWith(`/simulate/run-tests/${run.testId}/executions/`), (route) =>
    route.fulfill({ json: { count: 1, next: null, previous: null, results: [{ id: run.executionId }] } }),
  );
  // GET /simulate/test-executions/<id>/ (TestExecutionDetailView, the no-calls
  // branch in simulate/views/run_test.py): the grid reads `status` from it.
  await page.route(endsWith(`/simulate/test-executions/${run.executionId}/`), (route) =>
    route.fulfill({
      json: {
        count: 0, next: null, previous: null, results: [], total_pages: 0, current_page: 1,
        column_order: [], error_messages: [], status: 'completed',
      },
    }),
  );
  // GET /simulate/test-executions/<id>/kpis/ (RunTestKPIsView kpi_data): the count
  // the guard reads is `connected_calls`. Both runs report 14 completed calls,
  // so the 15-call run opens only because the guard reads connected, not completed.
  await page.route(endsWith(`/simulate/test-executions/${run.executionId}/kpis/`), (route) =>
    route.fulfill({
      json: {
        agent_type: 'voice', total_calls: 20, completed_calls: MIN_CONNECTED_CALLS - 1,
        calls_attempted: 20, connected_calls: run.connectedCalls,
        calls_connected_percentage: (run.connectedCalls / 20) * 100, scenario_graphs: {},
      },
    }),
  );
  // GET /simulate/test-executions/<id>/optimiser-analysis/ (TestExecutionOptimiserAnalysisView):
  // the one read the Debug failures drawer makes when it opens (SuggestionsPage.jsx). This is
  // get_latest_optimiser_result's answer for a run with no analysis yet
  // (simulate/utils/agent_optimiser.py), in the success_response envelope.
  await page.route(endsWith(`/simulate/test-executions/${run.executionId}/optimiser-analysis/`), (route) =>
    route.fulfill({ json: { status: true, result: { response: null, status: 'completed' } } }),
  );
}

// The Debug failures drawer's header subtitle (FixMyAgentHeader.jsx) and its text for a run
// with no analysis yet (FixMyAgentSections.jsx).
const DRAWER_SUBTITLE = 'The top issues in this run, and how to fix them.';
const DRAWER_NO_ANALYSIS = 'There are no suggestions yet, click the refresh button to get suggestions';

async function openFinishedRun(page: Page, run: FinishedRun) {
  await stubFinishedRun(page, run);
  // The guard has both inputs once the grid has read the run's status and the KPIs have landed.
  const read = (suffix: string) =>
    page.waitForResponse((r) => new URL(r.url()).pathname.endsWith(suffix), { timeout: UI_READY });
  const executionRead = read(`/simulate/test-executions/${run.executionId}/`);
  const kpisRead = read(`/simulate/test-executions/${run.executionId}/kpis/`);
  await page.goto(`${SIMULATE}/test/${run.testId}/${run.executionId}/call-details`);
  await Promise.all([executionRead, kpisRead]);
}

test('SIM-E2E-002: an org from before the Simulate cutoff keeps Scenarios and Run Simulation', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-002', area: 'simulate',
    userGoal: 'An existing customer, whose org predates the cutoff, still reaches every Simulate page from the menu, '
      + 'and can debug the failures of a finished Run Simulation run once it has 15 connected calls',
    steps: ['read the Simulate menu', 'open Scenarios and Run Simulation from the menu',
            'open a finished Run Simulation run with 14 connected calls and point at Debug failures',
            'open a finished Run Simulation run with 15 connected calls and 14 completed calls',
            'click Debug failures'],
    backendChecks: ["user-info for the actor's org reports restrict_simulate_to_environments: false"],
    browserChecks: ['the Simulate menu is exactly Environment, Scenarios, Run Simulation',
                    'each of those two items opens its page by URL and title, never /404',
                    'at 14 connected calls Debug failures is disabled, and its tooltip reads exactly '
                      + '"You need at least 15 connected calls to debug failures."',
                    'at 15 connected calls and 14 completed calls Debug failures is enabled',
                    'clicking it opens the Debug failures drawer, with its subtitle and its no-analysis-yet text'],
  }),
}, async ({ page, actor }, testInfo) => {
  // 7 browser actions × 60s, plus 60s headroom.
  test.setTimeout(480_000);
  await testInfo.attach('existing-customer', { contentType: 'application/json', body: JSON.stringify({ email: actor.email, organizationId: actor.organizationId, workspaceId: actor.workspaceId }) });
  const belowMinimum: FinishedRun = { testId: randomUUID(), executionId: randomUUID(), connectedCalls: MIN_CONNECTED_CALLS - 1 };
  const atMinimum: FinishedRun = { testId: randomUUID(), executionId: randomUUID(), connectedCalls: MIN_CONNECTED_CALLS };
  await testInfo.attach('stubbed-runs', { contentType: 'application/json', body: JSON.stringify({ belowMinimum, atMinimum }) });

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

  const debugFailures = page.getByRole('button', { name: 'Debug failures', exact: true });
  await test.step('ui: a finished run with 14 connected calls blocks Debug failures', async () => {
    await openFinishedRun(page, belowMinimum);
    await expect(debugFailures).toBeDisabled({ timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('ui: the blocked Debug failures says why', async () => {
    // A disabled MUI button takes no pointer events, so point at the Box the
    // tooltip wraps it in. Re-point until the guard has its reason: while the
    // KPIs load it is disabled with an empty one, and no tooltip shows.
    const anchor = debugFailures.locator('..');
    await expect(async () => {
      await page.mouse.move(0, 0);
      await anchor.hover();
      await expect(page.getByRole('tooltip')).toHaveText(BLOCKED_REASON, { timeout: 2_000 });
    }).toPass({ timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('ui: a finished run with 15 connected calls opens Debug failures', async () => {
    await openFinishedRun(page, atMinimum);
    await expect(debugFailures).toBeEnabled({ timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('ui: clicking the enabled Debug failures opens its drawer', async () => {
    const analysisRead = page.waitForResponse(
      (r) => new URL(r.url()).pathname.endsWith(`/simulate/test-executions/${atMinimum.executionId}/optimiser-analysis/`),
      { timeout: UI_READY },
    );
    await debugFailures.click();
    await analysisRead;
    await expect(page.getByText(DRAWER_SUBTITLE, { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByText(DRAWER_NO_ANALYSIS, { exact: true })).toBeVisible({ timeout: UI_READY });
  }, { timeout: UI_READY });
});
