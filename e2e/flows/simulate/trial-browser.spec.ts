import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedHarnessEnvironment } from '../../lib/simulate-seed';

// Browser-side waits: one navigation's first paint, not a stage.
const UI_READY = 60_000;
// Paths from futureagi/simulate/urls.py (v3/call-executions and its browser/).
const CALLS = '/simulate/v3/call-executions';
// Copy from frontend/src/sections/simulate/environments/workspace/runs/detail/trialBrowser.constants.js and
// trialChanges.constants.js (blockedCall: "<host> ×<count>").
const BROWSER_TAB = 'Browser';
const BLOCKED_HEADING = 'Refused in the browser';

// Wire shapes from futureagi/simulate/selectors/browser_evidence.py (BrowserEvidence) and the call detail's
// browser_environment flag.
interface CallDetail { browser_environment: boolean }
interface Screenshot { when: string; url: string; tab: number | null; kept: boolean; picture_url: string | null }
interface Browser {
  state: string;
  screenshots: Screenshot[];
  blocked_source: string;
  blocked?: { host: string; what?: string; count: number }[];
}

const trialPage = (seed: { environmentId: string; runTestId?: string; testExecutionId?: string;
  callExecutionId?: string }) => `/dashboard/simulate/environments/${seed.environmentId}/runs/${seed.runTestId}`
  + `/${seed.testExecutionId}?rowId=${seed.callExecutionId}`;

test('SIM-E2E-040: A trial of a browser environment shows its browser\'s screenshot and the page it was refused', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-040', area: 'simulate',
    userGoal: 'A developer opens a trial of a browser environment and sees what its browser did: the screenshot '
      + 'it took, with the page\'s address, and the page it was refused; a trial of an environment with no browser '
      + 'has no Browser entry',
    steps: ['seed a browser environment and one finished trial with no world door: its screenshot and its '
              + 'world_state file stored (e2e/lib/harness_environment_seed.py, trial: browser)',
            'seed an environment with no browser and one finished trial',
            'read each trial (GET v3/call-executions/<id>)', 'read the browser trial\'s browser (GET .../browser/)',
            'open the browser trial and its Browser entry', 'open the other trial'],
    backendChecks: ['the browser trial is of a browser environment and the other is not',
                    'its browser lists one kept screenshot of the minted page',
                    'its refusals come from its file and name the minted host as a page'],
    browserChecks: ['the Browser entry captions the screenshot "Page loaded · Tab 1 · <minted page>" and shows '
                      + 'its picture',
                    'it lists the minted host under Pages blocked',
                    'the other trial has no Browser entry'],
  }),
}, async ({ page, actor }, testInfo) => {
  // Two seeds through the backend container (Django start-up each), three API calls and two page loads.
  test.setTimeout(300_000);
  const seed = seedHarnessEnvironment({ organizationId: actor.organizationId, workspaceId: actor.workspaceId,
    refunds: 1, lookups: 1, trial: 'browser' });
  const plain = seedHarnessEnvironment({ organizationId: actor.organizationId, workspaceId: actor.workspaceId,
    refunds: 1, lookups: 1, actors: true, trial: true });
  await testInfo.attach('seeded', { contentType: 'application/json',
    body: JSON.stringify({ organizationId: actor.organizationId, workspaceId: actor.workspaceId, seed, plain }) });
  const pageUrl = seed.browserPage as string;
  const refusedHost = seed.refusedHost as string;

  await test.step('api: only the browser trial is of a browser environment', async () => {
    const browserTrial = await actor.api.get<CallDetail>(`${CALLS}/${seed.callExecutionId}/`);
    expect(browserTrial.browser_environment).toBe(true);
    const plainTrial = await actor.api.get<CallDetail>(`${CALLS}/${plain.callExecutionId}/`);
    expect(plainTrial.browser_environment).toBe(false);
  });

  await test.step('api: the trial\'s browser lists its screenshot and the page it was refused', async () => {
    const browser = await actor.api.get<Browser>(`${CALLS}/${seed.callExecutionId}/browser/`);
    expect(browser.state).toBe('listed');
    expect(browser.screenshots.map((one) => [one.when, one.url, one.tab, one.kept])).toEqual([
      ['load', pageUrl, 1, true],
    ]);
    expect(browser.screenshots[0].picture_url).toBeTruthy();
    expect(browser.blocked_source).toBe('file');
    expect(browser.blocked?.map((one) => [one.host, one.what, one.count])).toEqual([
      [refusedHost, 'navigation', 1],
    ]);
  });

  await test.step('browser: the Browser entry shows the screenshot and the refused page', async () => {
    await page.goto(trialPage(seed));
    await page.getByRole('tab', { name: BROWSER_TAB }).click({ timeout: UI_READY });
    const panel = page.getByTestId('trial-browser');
    await expect(panel).toBeVisible({ timeout: UI_READY });
    const shots = panel.getByTestId('browser-screenshot');
    await expect(shots.locator('figcaption')).toHaveText([`Page loaded · Tab 1 · ${pageUrl}`]);
    await expect(shots.getByRole('img', { name: `Screenshot of ${pageUrl}` })).toHaveAttribute('src', /\S/);
    await expect(panel.getByText(BLOCKED_HEADING)).toBeVisible();
    await expect(panel.getByTestId('blocked-page')).toHaveText([`${refusedHost} ×1`]);
  });

  await test.step('browser: a trial of an environment with no browser has no Browser entry', async () => {
    await page.goto(trialPage(plain));
    // World state shows once the trial's detail is read; the Browser entry is decided by the same read.
    await expect(page.getByRole('tab', { name: 'World state' })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByRole('tab', { name: BROWSER_TAB })).toHaveCount(0);
  });
});
