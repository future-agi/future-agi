import { test, expect } from '../../lib/fixtures';
import { AGENT_SOURCE_DIR } from '../../lib/agent-source';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedHarnessEnvironment } from '../../lib/simulate-seed';
import { POLL } from '../../lib/state-probe';

// Browser-side waits: one navigation's first paint or one synchronous request, not a stage.
const UI_READY = 60_000;
// frontend/src/sections/simulate/environments/buildDrawer/buildDraft.js BUILD_PARAM and buildDrawerCatalog.js
// (item id "upload"): the address the Build environment drawer keeps for its folder upload. Its form
// (ConnectCodeForm.jsx) holds Browser sites once the folder's agent is found. The GitHub way scans the
// repository first, which needs GitHub; the stack has none.
const UPLOAD_BUILD = '/dashboard/simulate/environments?build=upload';
// frontend/src/api/harness/harness.js sourcesPath.
const SOURCES_PATH = '/simulate/api/harness-jobs/sources/';
// e2e/stack/mock-daytona/server.mjs: the one agent every scan finds.
const FOUND_AGENT = 'support_agent';
const ENVIRONMENTS = '/dashboard/simulate/environments';
// frontend/src/api/harness/harness.js preflightPath.
const PREFLIGHT_PATH = '/simulate/api/harness-jobs/preflight/';
// futureagi/simulate/views/harness_environment.py list; its envelope is HarnessEnvironmentListResponseSerializer.
const LIST_PATH = '/simulate/api/harness-environments/';
// futureagi/simulate/services/browser_sites.py: the refusal's sentence for one wrong site.
const ONE_WRONG_SITE = (position: number) => 'Allowed sites must be public host names, such as example.com, '
  + `with no scheme, port, path or address. Check site ${position}.`;
// futureagi/simulate/services/harness_environment.py agent_type, as AgentTypeCell labels it (agentTypes.js MODALITY).
const BROWSER = 'Browser';
const CHAT = 'Chat';

interface Refusal { error: string; message: string; positions: number[] }
interface ListPage { count: number; results: { id: string; name: string; agent_type: string }[] }

// Agent reading runs on worker-reading and mock-daytona, which only the distributed E2E stack starts.
// Skipped when the suite is listed, not while it runs, so the standalone shards and their listing agree.
const distributedOnly = process.env.E2E_STACK === 'standalone' ? test.skip : test;

distributedOnly('SIM-E2E-045: a wrong browser site is refused by its position at preflight, before anything is built', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-045', area: 'simulate',
    userGoal: 'A user building an environment for a browser agent names the sites its browser may open, and a '
      + 'wrong site is refused by its position, before anything is built',
    steps: ['open Environments with the Build environment drawer on folder upload', 'choose the agent folder and '
              + 'find its agent', 'pick the agent found and go on to its form', 'in Browser sites, type a good host '
              + 'in capitals and a URL after it', 'run preflight', 'read the preflight row'],
    backendChecks: ['the preflight request carries browser.allowed_sites as the two typed entries, trimmed and '
                      + 'lower-cased, in order',
                    'the server answers 400 browser_sites_invalid with positions [2]'],
    browserChecks: ['the preflight row reads "Preflight couldn\'t run" and the refusal naming site 2'],
  }),
}, async ({ page, actor }, testInfo) => {
  // Every wait: the upload (UI_READY) + the found agent (UI_READY + POLL.ASYNC_JOB, the scan) + Browser sites,
  // the preflight request and the preflight row (UI_READY each) = 60 + 120 + 3 x 60 = 360 s, + 60 s headroom.
  test.setTimeout(420_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const good = `e2e-sites-${suffix}.example.com`;
  const typed = `${good.toUpperCase()}, https://${good}/path`;

  await test.step('ui: upload the agent folder and find its agent', async () => {
    await page.goto(UPLOAD_BUILD, { waitUntil: 'domcontentloaded' });
    const uploaded = page.waitForResponse(
      (r) => r.url().endsWith(SOURCES_PATH) && r.request().method() === 'POST', { timeout: UI_READY });
    await page.locator('input[type="file"][webkitdirectory]').setInputFiles(AGENT_SOURCE_DIR);
    expect((await uploaded).status()).toBe(201);
    await page.getByRole('button', { name: 'Find agents' }).click();
    await page.getByText(FOUND_AGENT, { exact: true }).click({ timeout: UI_READY + POLL.ASYNC_JOB.timeout });
    await page.getByRole('button', { name: 'Next' }).click();
  });

  await test.step('ui: type the browser sites', async () => {
    await page.getByRole('textbox', { name: 'Browser sites' }).fill(typed, { timeout: UI_READY });
    await testInfo.attach('minted', { contentType: 'application/json',
      body: JSON.stringify({ good, typed, organizationId: actor.organizationId }) });
  });

  let refusal: Refusal | undefined;
  await test.step('api: preflight sends the sites as typed and is refused at site 2', async () => {
    const sent = page.waitForResponse(
      (r) => r.url().endsWith(PREFLIGHT_PATH) && r.request().method() === 'POST', { timeout: UI_READY });
    await page.getByRole('button', { name: 'Run preflight' }).click();
    const response = await sent;
    const body = await response.text();
    await testInfo.attach('preflight', { contentType: 'application/json', body });
    expect(response.request().postDataJSON().browser).toEqual({ allowed_sites: [good, `https://${good}/path`] });
    expect(response.status(), body).toBe(400);
    refusal = JSON.parse(body);
    expect(refusal?.error).toBe('browser_sites_invalid');
    expect(refusal?.positions).toEqual([2]);
  });

  await test.step('ui: the preflight row names the wrong site', async () => {
    await expect(page.getByText('Preflight couldn\'t run', { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByText(ONE_WRONG_SITE(2), { exact: true })).toBeVisible();
  });
});

test('SIM-E2E-046: environments built for browser agents read Browser and are what the Browser filter lists', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-046', area: 'simulate',
    userGoal: 'A user finds the environments built for browser agents by their Browser label and the Browser filter',
    steps: ['seed two built environments: one whose agent contract needs a browser, one chat '
              + '(e2e/lib/harness_environment_seed.py)', 'open Environments', 'search each one\'s name and read '
              + 'its agent type', 'search the browser one\'s name', 'choose Agent type › Browser and read the rows',
            'search the chat one\'s name and read the rows'],
    backendChecks: ['GET harness-environments/?agent_type=browser lists the browser environment with agent_type '
                      + 'browser, and not the chat one'],
    browserChecks: ['the browser environment\'s row reads Browser and the chat one\'s reads Chat',
                    'with Browser chosen, the browser environment is listed and the chat one is not'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Every wait: 2 seeds (container execs, about 20 s each) + step 1's search and row count for each name
  // (4 x UI_READY) + step 2's row count, filtered answer, row count, filtered answer and row count (5 x UI_READY)
  // = 40 + 540 s, + 60 s headroom.
  test.setTimeout(640_000);
  const browserSeed = seedHarnessEnvironment({ organizationId: actor.organizationId, workspaceId: actor.workspaceId,
    environmentNeeds: ['browser'] });
  const chatSeed = seedHarnessEnvironment({ organizationId: actor.organizationId, workspaceId: actor.workspaceId });
  // The seeder names each environment from a suffix it mints itself; read the names it gave.
  const nameOf = async (id: string) => (await probe.pg<{ name: string }>(
    'SELECT name FROM simulate_hosted_harness_job WHERE id = $1', [id]))[0].name;
  const browserEnv = await nameOf(browserSeed.environmentId);
  const chatEnv = await nameOf(chatSeed.environmentId);
  await testInfo.attach('seeded', { contentType: 'application/json',
    body: JSON.stringify({ browserSeed, chatSeed, browserEnv, chatEnv }) });

  const search = page.getByPlaceholder('Search by name, agent or status');
  const rowsNamed = (name: string) => page.getByRole('row').filter({ hasText: name });

  await test.step('ui: each environment reads its agent type', async () => {
    await page.goto(ENVIRONMENTS);
    for (const [name, label] of [[browserEnv, BROWSER], [chatEnv, CHAT]]) {
      await search.fill(name, { timeout: UI_READY });
      await expect(rowsNamed(name)).toHaveCount(1, { timeout: UI_READY });
      await expect(rowsNamed(name).getByText(label, { exact: true })).toBeVisible();
    }
  });

  await test.step('ui: the Browser filter lists the browser environment and not the chat one', async () => {
    // The filtered answer for one name. Each is awaited, so a row count is the filter's and not a list still
    // loading. Each filter and name pair is asked for once: the app keeps a list answer fresh for 5 s
    // (app.jsx QueryClient staleTime) and asks again only after that.
    const filtered = (name: string) => page.waitForResponse((r) => {
      const url = new URL(r.url());
      return url.pathname.endsWith(LIST_PATH) && url.searchParams.get('agent_type') === 'browser'
        && url.searchParams.get('search') === name;
    }, { timeout: UI_READY });
    await search.fill(browserEnv);
    await expect(rowsNamed(browserEnv)).toHaveCount(1, { timeout: UI_READY });
    const forBrowser = filtered(browserEnv);
    // The Agent type select's display carries the aria-label (EnvironmentFilters.jsx FilterSelect).
    await page.getByRole('combobox', { name: 'Agent type', exact: true }).click();
    await page.getByRole('option', { name: BROWSER, exact: true }).click();
    expect(((await (await forBrowser).json()) as ListPage).results.map((one) => one.id))
      .toEqual([browserSeed.environmentId]);
    await expect(rowsNamed(browserEnv)).toHaveCount(1, { timeout: UI_READY });
    const forChat = filtered(chatEnv);
    await search.fill(chatEnv);
    expect(((await (await forChat).json()) as ListPage).results).toEqual([]);
    await expect(rowsNamed(browserEnv)).toHaveCount(0, { timeout: UI_READY });
    await expect(rowsNamed(chatEnv)).toHaveCount(0);
  });

  await test.step('api: the list the page calls filters the same way', async () => {
    const listed = async (name: string) => (await actor.api.get<ListPage>(LIST_PATH,
      { agent_type: 'browser', search: name })).results.map((one) => ({ id: one.id, agent_type: one.agent_type }));
    expect(await listed(browserEnv)).toEqual([{ id: browserSeed.environmentId, agent_type: 'browser' }]);
    expect(await listed(chatEnv)).toEqual([]);
  });
});
