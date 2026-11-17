import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { E2E } from '../../lib/env';

// ManualDatasetCreateRequestSerializer and model_hub/urls.py.
const CREATE = '/model-hub/develops/create-dataset-manually/';
// useDevelopDatasetList uses GetDatasetsNamesView, not the paged list endpoint.
const NAMES = '/model-hub/develops/get-datasets-names/';
// GetDatasetTableView and DatasetHeaderBar's onSelect destination.
const tablePath = (id: string) => `/model-hub/develops/${id}/get-dataset-table/`;
const datasetPage = (id: string) => `/dashboard/develop/${id}?tab=data`;
const UI_READY = 60_000;
type Created = { result: { dataset_id: string } };
type Named = { dataset_id: string; name: string; row_count: number };
type Names = { result: { datasets: Named[] } };
type Stored = { id: string; name: string; organization_id: string; workspace_id: string };

test('DATA-E2E-010: the dataset switcher searches and opens only authorized datasets', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-010', area: 'datasets',
    userGoal: 'Find another dataset in the header and open its persisted rows without exposing another tenant or workspace',
    steps: [
      'create distinct source and destination datasets in two organizations and a sibling workspace',
      'open the source header, search for the destination and compare the exact menu and API result',
      'switch datasets, read the destination rows and reload the same destination',
      'search for absent and foreign names and see no choices',
      'repeat switching as the second organization and reject direct cross-tenant and sibling-workspace reads',
    ],
    backendChecks: [
      'the real names requests and exact picker choices match only the requested search in the active tenant and workspace',
      'switching and reload read the selected dataset table while the exact persisted dataset identities remain unchanged',
      'foreign organization and sibling workspace table reads are refused while each owner can still read its own rows',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Public seeding 180s + six UI_READY chains (360s) + synchronous PG/API checks 60s.
  test.setTimeout(600_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const prefix = `e2e-data6-${suffix}`;
  const a = scopes.ownerA;
  const b = scopes.ownerB;
  const sibling = scopes.withWorkspace(a, scopes.emptyWorkspace.id);
  const fixtures: (Stored & { rows: number })[] = [];
  for (const [actor, label, rows] of [[a, 'a-source', 1], [a, 'a-target', 2], [b, 'b-source', 1], [b, 'b-target', 3], [sibling, 'sibling', 1]] as const) {
    const name = `${prefix}-${label}`;
    const created = await scopes.send<Created>(actor, 'POST', CREATE, { dataset_name: name, number_of_rows: rows, number_of_columns: 1 });
    expect(created.status).toBe(200);
    fixtures.push({ id: created.body.result.dataset_id, name, rows, organization_id: actor.organizationId, workspace_id: actor.workspaceId });
  }
  await testInfo.attach('dataset-switcher-identities', { contentType: 'application/json', body: JSON.stringify(fixtures) });
  const [aSource, aTarget, bSource, bTarget, siblingDataset] = fixtures;
  const receipts: { path: string; method: string; status: number }[] = [];
  const storage = () => probe.pg<Stored>(
    'SELECT id, name, organization_id, workspace_id FROM model_hub_dataset WHERE id = ANY($1::uuid[]) AND NOT deleted ORDER BY name',
    [fixtures.map(row => row.id)]);
  const expectedStorage = fixtures.map(({ rows: _rows, ...row }) => row).sort((x, y) => x.name.localeCompare(y.name));
  expect(await storage()).toEqual(expectedStorage);

  for (const [actor, source, target, foreign] of [[a, aSource, aTarget, bTarget], [b, bSource, bTarget, aTarget]] as const) {
    const context = await scopes.openContext(browser, actor);
    await context.route('**/*', route => {
      const host = new URL(route.request().url()).hostname;
      return host === 'localhost' || host === '127.0.0.1' ? route.continue() : route.abort('blockedbyclient');
    });
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    const writes: string[] = [];
    page.on('request', req => {
      const path = new URL(req.url()).pathname;
      if (path.startsWith('/model-hub/develops/') && ['POST', 'PUT', 'PATCH', 'DELETE'].includes(req.method())) writes.push(`${req.method()} ${path}`);
    });
    await page.goto(datasetPage(source.id), { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(source.name, { timeout: UI_READY });
    await page.getByRole('button', { name: 'Switch dataset', exact: true }).click();
    const search = page.getByPlaceholder('Search datasets', { exact: true });
    const response = page.waitForResponse(r => {
      const url = new URL(r.url());
      return url.pathname === NAMES && url.searchParams.get('search_text') === target.name;
    }, { timeout: UI_READY });
    await search.fill(target.name);

    await test.step('backend check 1: exact scoped search and picker', async () => {
      const read = await response;
      expect(read.status()).toBe(200);
      expect(read.request().method()).toBe('GET');
      const names = await read.json() as Names;
      expect(names.result.datasets.map(({ dataset_id, name, row_count }) => ({ dataset_id, name, row_count })))
        .toEqual([{ dataset_id: target.id, name: target.name, row_count: target.rows }]);
      await expect(page.getByRole('menuitem')).toHaveText([target.name], { timeout: UI_READY });
      const replay = await actor.api.get<Names>(NAMES, { search_text: target.name });
      expect(replay.result.datasets).toEqual(names.result.datasets);
      const all = await actor.api.get<Names>(NAMES, { search_text: prefix });
      expect(all.result.datasets.map(row => row.dataset_id).sort()).toEqual([source.id, target.id].sort());
      receipts.push({ path: NAMES, method: 'GET', status: read.status() });
    });

    await test.step('backend check 2: selected table, reload and immutable storage', async () => {
      // DataTab's grid uses page_size=30; column-only and one-row header reads share this route.
      const gridResponse = (url: string) => {
        const parsed = new URL(url);
        return parsed.pathname === tablePath(target.id) && parsed.searchParams.get('page_size') === '30' && parsed.searchParams.get('column_config_only') !== 'true';
      };
      const tableRead = page.waitForResponse(r => gridResponse(r.url()), { timeout: UI_READY });
      await page.getByRole('menuitem', { name: target.name, exact: true }).click();
      const read = await tableRead;
      expect(read.status()).toBe(200);
      expect(read.request().method()).toBe('GET');
      const data = await read.json();
      expect(data.result.table).toHaveLength(target.rows);
      await expect(page).toHaveURL(`${E2E.appUrl}${datasetPage(target.id)}`, { timeout: UI_READY });
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(target.name, { timeout: UI_READY });
      const reloaded = page.waitForResponse(r => gridResponse(r.url()), { timeout: UI_READY });
      await page.reload({ waitUntil: 'domcontentloaded' });
      expect((await reloaded).status()).toBe(200);
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(target.name, { timeout: UI_READY });
      expect(await storage()).toEqual(expectedStorage);
      expect(writes).toEqual([]);
      receipts.push({ path: tablePath(target.id), method: 'GET', status: read.status() });
    });

    await page.getByRole('button', { name: 'Switch dataset', exact: true }).click();
    for (const missing of [foreign.name, siblingDataset.name, `${prefix}-does-not-exist`]) {
      const emptyResponse = page.waitForResponse(r => {
        const url = new URL(r.url());
        return url.pathname === NAMES && url.searchParams.get('search_text') === missing;
      }, { timeout: UI_READY });
      await search.fill(missing);
      const read = await emptyResponse;
      expect(read.status()).toBe(200);
      expect((await read.json()).result.datasets).toEqual([]);
      await expect(page.getByRole('menuitem')).toHaveCount(0, { timeout: UI_READY });
      await expect(page.getByText('No datasets found', { exact: true })).toBeVisible({ timeout: UI_READY });
    }
    await testInfo.attach(`dataset-switcher-${actor === a ? 'a' : 'b'}-page`, { contentType: 'image/png', body: await page.screenshot() });
    await context.close();
  }

  await test.step('backend check 3: cross-tenant and sibling-workspace refusal', async () => {
    for (const [actor, forbiddenId] of [[a, bTarget.id], [b, aTarget.id], [sibling, aTarget.id], [a, siblingDataset.id]] as const) {
      const denied = await scopes.send(actor, 'GET', tablePath(forbiddenId));
      // GetDatasetTableView reports DATASET_NOT_FOUND through bad_request.
      expect(denied.status).toBe(400);
      receipts.push({ path: tablePath(forbiddenId), method: 'GET', status: denied.status });
    }
    for (const [actor, own] of [[a, aTarget], [b, bTarget], [sibling, siblingDataset]] as const) {
      const allowed = await scopes.send<{ result: { table: unknown[] } }>(actor, 'GET', tablePath(own.id));
      expect(allowed.status).toBe(200);
      expect(allowed.body.result.table).toHaveLength(own.rows);
    }
    expect(await storage()).toEqual(expectedStorage);
  });
  await testInfo.attach('dataset-switcher-requests', { contentType: 'application/json', body: JSON.stringify({ receipts, app: E2E.appUrl, api: E2E.apiUrl, browser: browser.browserType().name() }) });
});
