import { randomUUID } from 'node:crypto';
import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

const LIST = '/model-hub/develops/get-datasets/';
const tablePath = (id: string) => `/model-hub/develops/${id}/get-dataset-table/`;
const GONE = 'The specified dataset does not exist or has been deleted.';
type List = { result: { datasets: { id: string; name: string }[] } };

test('@DSX-WALK-EVAL-88: recover from missing and foreign dataset links without revealing another organization’s data', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DSX-WALK-EVAL-88', area: 'dsx-walks',
    userGoal: 'Follow a dataset link, understand when it is unavailable and return only to datasets in my own organization',
    steps: ['seed one private dataset per organization through the served API',
      'open a random missing dataset and then the other organization’s dataset',
      'read the exact unavailable message and use Back to Datasets',
      'open the owned dataset successfully and verify the scoped backend list and table'],
    backendChecks: ['the availability GET for missing and foreign ids returns exactly 400 with DATASET_NOT_FOUND text',
      'the browser sends its actor organization and workspace headers',
      'owned dataset GET returns 200; the list contains exactly the owned id and name and never the foreign dataset'],
    browserChecks: ['both unavailable links show Dataset not found with exactly one Back to Datasets link',
      'the owned dataset header is visible and has no not-found page'],
  }),
}, async ({ browser, scopeActors: scopes }, testInfo) => {
  test.setTimeout(600_000);
  const ui = expect.configure({ timeout: 60_000 });
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const datasets = [];
  for (const [tag, actor] of [['a', scopes.ownerA], ['b', scopes.ownerB]] as const) {
    const name = `dsx-not-found-${tag}-${suffix}`;
    const created = await scopes.send<{ result: { dataset_id: string } }>(actor, 'POST',
      '/model-hub/develops/create-empty-dataset/', { new_dataset_name: name });
    expect(created.status).toBe(200);
    datasets.push({ actor, name, id: created.body.result.dataset_id, missingId: randomUUID() });
  }
  await testInfo.attach('seeded-identities', { contentType: 'application/json', body: JSON.stringify(
    datasets.map(({ actor, ...ids }) => ({ ...ids, organizationId: actor.organizationId, workspaceId: actor.workspaceId }))) });
  for (const own of datasets) {
    const foreign = datasets.find(d => d.id !== own.id)!;
    const context = await scopes.openContext(browser, own.actor);
    await context.route(url => ['http:', 'https:'].includes(url.protocol) &&
      ![new URL(E2E.appUrl).origin, new URL(E2E.apiUrl).origin].includes(url.origin), route => route.abort());
    try {
      for (const id of [own.missingId, foreign.id]) {
        const page = await context.newPage();
        page.setDefaultTimeout(60_000);
        const read = page.waitForResponse(r => new URL(r.url()).pathname === tablePath(id) &&
          new URL(r.url()).searchParams.get('column_config_only') === 'true' && r.request().method() === 'GET');
        await page.goto(`/dashboard/develop/${id}`, { waitUntil: 'domcontentloaded' });
        const response = await read;
        expect(response.status()).toBe(400);
        expect(response.request().headers()['x-organization-id']).toBe(own.actor.organizationId);
        expect(response.request().headers()['x-workspace-id']).toBe(own.actor.workspaceId);
        const body = JSON.stringify(await response.json());
        expect(body).toContain(GONE);
        expect(body).not.toContain(foreign.name);
        // DatasetNotFound.jsx: the actual page, title, body and return link.
        const missing = page.getByTestId('dataset-not-found');
        await ui(missing.getByText('Dataset not found', { exact: true })).toBeVisible();
        await ui(missing.getByText('This dataset is unavailable. Check the link and your workspace access, or return to Datasets.', { exact: true })).toBeVisible();
        await ui(missing.locator('a, button')).toHaveText(['Back to Datasets']);
        await ui(page).toHaveTitle(/^Dataset not found/);
        await ui(page.getByText(foreign.name, { exact: true })).toHaveCount(0);
        const back = missing.getByRole('link', { name: 'Back to Datasets', exact: true });
        await ui(back).toHaveAttribute('href', '/dashboard/develop');
        await back.click();
        await expect.poll(() => new URL(page.url()).pathname, { timeout: 60_000 }).toBe('/dashboard/develop');
        await ui(page.getByText(own.name, { exact: true })).toBeVisible();
        await ui(page.getByText(foreign.name, { exact: true })).toHaveCount(0);
        await page.close();
      }
      const page = await context.newPage();
      page.setDefaultTimeout(60_000);
      const read = page.waitForResponse(r => new URL(r.url()).pathname === tablePath(own.id) && r.request().method() === 'GET');
      await page.goto(`/dashboard/develop/${own.id}`, { waitUntil: 'domcontentloaded' });
      expect((await read).status()).toBe(200);
      // DatasetHeaderBar.jsx: the shipped dataset name control.
      await ui(page.getByLabel(`Dataset ${own.name}. Click to copy the name.`, { exact: true })).toBeVisible();
      await ui(page.getByTestId('dataset-not-found')).toHaveCount(0);
    } finally { await context.close(); }
    const ownRead = await scopes.send(own.actor, 'GET', tablePath(own.id), undefined, { column_config_only: 'true' });
    expect(ownRead.status).toBe(200);
    const denied = await scopes.send(own.actor, 'GET', tablePath(foreign.id), undefined, { column_config_only: 'true' });
    expect(denied.status).toBe(400);
    expect(JSON.stringify(denied.body)).toContain(GONE);
    expect(JSON.stringify(denied.body)).not.toContain(foreign.name);
    const list = await scopes.send<List>(own.actor, 'GET', LIST, undefined, { page: 0, page_size: 25 });
    expect(list.status).toBe(200);
    expect(list.body.result.datasets.map(({ id, name }) => ({ id, name }))).toEqual([{ id: own.id, name: own.name }]);
    await testInfo.attach(`scope-${own.id}`, { contentType: 'application/json', body: JSON.stringify({
      datasetId: own.id, foreignDatasetId: foreign.id, ownStatus: ownRead.status,
      foreignStatus: denied.status, datasets: list.body.result.datasets.map(({ id, name }) => ({ id, name })),
    }) });
  }
});
