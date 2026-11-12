import { randomUUID } from 'node:crypto';
import type { Page } from '@playwright/test';
import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// model_hub/urls.py and DatasetCreateEmptyRequestSerializer.
const CREATE_DATASET = '/model-hub/develops/create-empty-dataset/';
// useDatasetListQuery.js: datasetListQueryConfig and realDatasetListParams.
const LIST = '/model-hub/develops/get-datasets/';
// DataTab/DatasetNotFound.jsx useDatasetAvailability reads getDatasetDetail.
const datasetTable = (id: string) => `/model-hub/develops/${id}/get-dataset-table/`;
// utils/axios.js getExperimentDetails.
const experimentDetail = (id: string) => `/model-hub/experiments/v2/${id}/`;
// DataTab/DatasetNotFound.jsx: the page's hook, title and sentence.
const NOT_FOUND = '[data-testid="dataset-not-found"]';
const TITLE = 'Dataset not found';
const BODY = 'This dataset is unavailable. Check the link and your workspace access, or return to Datasets.';
// tfc/utils/error_codes.py DATASET_NOT_FOUND, as DatasetNotFound.jsx pins it.
const GONE_MESSAGE = 'The specified dataset does not exist or has been deleted.';
// experiment-detail/ExperimentNotFound.jsx title.
const EXPERIMENT_TITLE = 'Experiment not found';
// DevelopBar/DatasetHeaderBar.jsx: the header's name control.
const headerName = (name: string) => `Dataset ${name}. Click to copy the name.`;
const UI_READY = 60_000;

// EXPECTATION-ONLY fallibility anchors. Never interpolate these into a request
// body or a seeded value: each one is read by assertions only.
const EXPECTED_ACTIONS = ['Back to Datasets'];
const EXPECTED_FOREIGN_STATUS = 400;
const EXPECTED_NAME_SUFFIX = '';

type Envelope<T> = { status: boolean; result: T };
type Dataset = { actor: ScopeActor; id: string; name: string };
type List = Envelope<{ datasets: { id: string; name: string }[] }>;

// Only loopback runtime traffic; dependency installation is outside the browser.
const offHost = (url: URL) => /^https?:$/.test(url.protocol)
  && !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);

test('EVAL-E2E-079: a missing or another organization\'s dataset link opens a not-found page that leads only to Datasets', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-079', area: 'evals',
    userGoal: 'Open a dataset link I cannot use and get a clear not-found page that leads me back to my own datasets, without seeing another organization\'s data',
    steps: [
      'create a uniquely named dataset in each of two synthetic organizations through the public API',
      'open the other organization\'s dataset link and read the not-found page and its one action',
      'choose Back to Datasets and read the organization\'s own saved dataset from the real list',
      'open a dataset link whose id does not exist and read the same not-found page',
      'open the organization\'s own dataset link and see its dataset page instead of the not-found page',
      'open an unknown experiment link, require the classic page to read its scoped API and show Experiment not found, then return through All datasets',
      'repeat in the second organization, verify the refusals through the API and read back both saved datasets',
    ],
    backendChecks: [
      'the browser\'s own read of a foreign or unknown dataset carries the organization\'s scope headers and the real backend answers 400 DATASET_NOT_FOUND without the foreign dataset\'s name',
      'each tenant reads its own dataset table and receives 400 DATASET_NOT_FOUND for the other tenant\'s dataset, an unknown experiment id answers 404, and Postgres keeps both datasets unchanged with their exact names and tenant identities',
    ],
    browserChecks: [
      'the not-found page shows its title and sentence with exactly one action, Back to Datasets, and never the foreign dataset\'s name',
      'the organization\'s own dataset opens its page; classic experiment routes remain visible, and an unknown id shows Experiment not found after its scoped 404 rather than hiding the page',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Provision 120 s + seed/readback 60 s + two tenants x five UI_READY steps (600 s) + headroom 120 s.
  test.setTimeout(900_000);
  const ui = expect.configure({ timeout: UI_READY });
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const attach = (name: string, value: unknown) => testInfo.attach(name, {
    contentType: 'application/json', body: JSON.stringify(value, null, 2),
  });
  const datasets: Dataset[] = [];
  for (const [label, actor] of [['a', scopes.ownerA], ['b', scopes.ownerB]] as const) {
    const name = `e2e-not-found-${label}-${suffix}`;
    const created = await scopes.send<Envelope<{ dataset_id: string }>>(actor, 'POST', CREATE_DATASET,
      { new_dataset_name: name });
    expect(created.status).toBe(200);
    datasets.push({ actor, id: created.body.result.dataset_id, name });
  }
  await attach('seeded-identities', datasets.map(({ actor, ...d }) => ({ ...d,
    organizationId: actor.organizationId, workspaceId: actor.workspaceId })));
  const persisted = () => probe.pg<{ id: string; name: string; organization_id: string; workspace_id: string }>(
    'SELECT id, name, organization_id, workspace_id FROM model_hub_dataset WHERE id = ANY($1::uuid[]) AND NOT deleted ORDER BY id',
    [datasets.map(d => d.id)]);
  const before = await persisted();
  expect(before).toHaveLength(2);

  const notFoundActions = (page: Page) => page.locator(NOT_FOUND).locator('a, button')
    .evaluateAll(nodes => nodes.map(node => (node.textContent ?? '').trim()));

  // The page's own read of a dataset it cannot list, and what the page shows for it.
  const expectNotFound = async (page: Page, d: Dataset, id: string, foreignName?: string) => {
    const read = page.waitForResponse(response => new URL(response.url()).pathname === datasetTable(id)
      && new URL(response.url()).searchParams.get('column_config_only') === 'true'
      && response.request().method() === 'GET', { timeout: UI_READY });
    await page.goto(`/dashboard/develop/${id}`, { waitUntil: 'domcontentloaded' });
    const response = await read;
    const scope = response.request().headers();
    expect(scope['x-organization-id']).toBe(d.actor.organizationId);
    expect(scope['x-workspace-id']).toBe(d.actor.workspaceId);
    expect(response.status()).toBe(EXPECTED_FOREIGN_STATUS);
    const body = JSON.stringify(await response.json());
    expect(body).toContain(GONE_MESSAGE);
    if (foreignName) expect(body).not.toContain(foreignName);
    const page404 = page.locator(NOT_FOUND);
    await ui(page404).toBeVisible();
    await ui(page404.getByText(TITLE, { exact: true })).toBeVisible();
    await ui(page404.getByText(BODY, { exact: true })).toBeVisible();
    await expect.poll(() => notFoundActions(page), { timeout: UI_READY }).toEqual(EXPECTED_ACTIONS);
    await ui(page).toHaveTitle(/^Dataset not found/);
    if (foreignName) await ui(page.getByText(foreignName)).toHaveCount(0);
  };

  for (const d of datasets) {
    const foreign = datasets.find(other => other.id !== d.id)!;
    const context = await scopes.openContext(browser, d.actor);
    const blocked = new Set<string>();
    await context.route(offHost, route => {
      blocked.add(new URL(route.request().url()).host);
      return route.abort();
    });
    let page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    // Each typed link opens in a new tab, as a pasted link would: Firefox
    // aborts a page.goto (NS_BINDING_ABORTED) issued while the dataset page
    // it leaves is still loading its lazy routes.
    const newTab = async () => {
      const previous = page;
      page = await context.newPage();
      page.setDefaultTimeout(UI_READY);
      await previous.close();
      return page;
    };

    await test.step(`another organization's dataset link (${d.name})`, async () => {
      await expectNotFound(page, d, foreign.id, foreign.name);
    });

    await test.step('Back to Datasets lists the organization\'s own dataset', async () => {
      const listed = page.waitForResponse(response => new URL(response.url()).pathname === LIST
        && response.request().method() === 'GET' && response.status() === 200
        && !new URL(response.url()).searchParams.has('search_text'), { timeout: UI_READY });
      await page.locator(NOT_FOUND).getByRole('link', { name: 'Back to Datasets' }).click();
      await expect.poll(() => new URL(page.url()).pathname, { timeout: UI_READY }).toBe('/dashboard/develop');
      const response = await listed;
      const scope = response.request().headers();
      expect(scope['x-organization-id']).toBe(d.actor.organizationId);
      expect(scope['x-workspace-id']).toBe(d.actor.workspaceId);
      const body = await response.json() as List;
      expect(body.result.datasets.map(row => row.name)).toEqual([`${d.name}${EXPECTED_NAME_SUFFIX}`]);
      await ui(page.getByText(d.name, { exact: true })).toBeVisible();
      await ui(page.getByText(foreign.name, { exact: true })).toHaveCount(0);
    });

    await test.step('a dataset id that does not exist', async () => {
      await expectNotFound(await newTab(), d, randomUUID());
    });

    await test.step('the organization\'s own dataset opens its page', async () => {
      await newTab();
      const read = page.waitForResponse(response => new URL(response.url()).pathname === datasetTable(d.id)
        && response.request().method() === 'GET' && response.status() === 200, { timeout: UI_READY });
      await page.goto(`/dashboard/develop/${d.id}`, { waitUntil: 'domcontentloaded' });
      const response = await read;
      expect(response.request().headers()['x-organization-id']).toBe(d.actor.organizationId);
      await ui(page.getByLabel(headerName(d.name), { exact: true })).toBeVisible();
      await ui(page.locator(NOT_FOUND)).toHaveCount(0);
    });

    await test.step('an unknown experiment keeps its classic not-found page visible', async () => {
      await newTab();
      const experimentId = randomUUID();
      const read = page.waitForResponse(response => new URL(response.url()).pathname === experimentDetail(experimentId)
        && response.request().method() === 'GET', { timeout: UI_READY });
      await page.goto(`/dashboard/develop/experiment/${experimentId}`, { waitUntil: 'domcontentloaded' });
      const response = await read;
      expect(response.status()).toBe(404);
      expect(response.request().headers()['x-organization-id']).toBe(d.actor.organizationId);
      expect(response.request().headers()['x-workspace-id']).toBe(d.actor.workspaceId);
      await expect.poll(() => new URL(page.url()).pathname, { timeout: UI_READY })
        .toBe(`/dashboard/develop/experiment/${experimentId}/data`);
      const missing = page.getByTestId('experiment-not-found');
      await ui(missing).toBeVisible();
      await ui(missing.getByText(EXPERIMENT_TITLE, { exact: true })).toBeVisible();
      await ui(missing.getByText(`No experiment has the id ${experimentId}. It may have been deleted, or the link is wrong.`, { exact: true })).toBeVisible();
      await ui(page).toHaveTitle(/^Experiment not found/);
      const listed = page.waitForResponse(result => new URL(result.url()).pathname === LIST
        && result.request().method() === 'GET' && result.status() === 200, { timeout: UI_READY });
      await missing.getByRole('link', { name: 'All datasets', exact: true }).click();
      await expect.poll(() => new URL(page.url()).pathname, { timeout: UI_READY }).toBe('/dashboard/develop');
      await listed;
      await ui(page.getByText(d.name, { exact: true })).toBeVisible();
      await ui(page.getByText(foreign.name, { exact: true })).toHaveCount(0);
    });
    await attach(`off-host-blocked-${d.id}`, [...blocked].sort());
    await context.close();
  }

  await test.step('persisted datasets and tenant refusals', async () => {
    for (const d of datasets) {
      const foreign = datasets.find(other => other.id !== d.id)!;
      const own = await scopes.send<Envelope<unknown>>(d.actor, 'GET', datasetTable(d.id), undefined,
        { column_config_only: 'true' });
      expect(own.status).toBe(200);
      const denied = await scopes.send<Envelope<unknown>>(d.actor, 'GET', datasetTable(foreign.id), undefined,
        { column_config_only: 'true' });
      expect(denied.status).toBe(EXPECTED_FOREIGN_STATUS);
      expect(JSON.stringify(denied.body)).toContain(GONE_MESSAGE);
      expect(JSON.stringify(denied.body)).not.toContain(foreign.name);
      const list = await scopes.send<List>(d.actor, 'GET', LIST, undefined, { page: 0, page_size: 25 });
      expect(list.status).toBe(200);
      expect(list.body.result.datasets.map(row => ({ id: row.id, name: row.name })))
        .toEqual([{ id: d.id, name: `${d.name}${EXPECTED_NAME_SUFFIX}` }]);
      expect((await scopes.send(d.actor, 'GET', experimentDetail(randomUUID()))).status).toBe(404);
    }
    expect(await persisted()).toEqual(before);
    for (const d of datasets) expect(before.find(row => row.id === d.id)).toEqual({
      id: d.id, name: `${d.name}${EXPECTED_NAME_SUFFIX}`,
      organization_id: d.actor.organizationId, workspace_id: d.actor.workspaceId,
    });
    await attach('persisted-after-not-found', await persisted());
  });
});
