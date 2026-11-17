import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { E2E } from '../../lib/env';

// DatasetHeaderBar.jsx -> DatasetSelectDropDown.jsx -> useDevelopDatasetList.
// The actual picker calls get-datasets-names, NOT the census's paged get-datasets.
// The pinned picker has no paging control; this flow does not claim paging coverage.
const CREATE = '/model-hub/develops/create-dataset-manually/';
const NAMES = '/model-hub/develops/get-datasets-names/';
const table = (id: string) => `/model-hub/develops/${id}/get-dataset-table/`;
const route = (id: string) => `/dashboard/develop/${id}?tab=data`;
const UI_READY = 60_000;
type Created = { result: { dataset_id: string } };
type Names = { result: { datasets: { dataset_id: string; name: string; row_count: number }[] } };
type Table = { result: { table: { row_id: string; order: number }[] } };

test('@DSX-WALK-ORG-53: Search and keyboard-select a dataset while keeping each tenant’s picker and table isolated.', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DSX-WALK-ORG-53', area: 'dsx-walks',
    userGoal: 'Find a dataset by name, activate its menu item with Enter and reload its own persisted rows',
    steps: ['seed source and target datasets in two tenants', 'search the header picker and activate the exact target with Enter',
      'reload the selected dataset and search for a foreign name in each tenant'],
    backendChecks: ['names API returns exactly the selected id, name and seeded row count',
      'navigation and reload return the selected dataset’s exact row ids and order',
      'foreign names searches are empty and foreign table reads return 400 while own reads remain available'],
  }),
}, async ({ browser, scopeActors: scopes }, testInfo) => {
  test.setTimeout(780_000);
  const a = scopes.ownerA, b = scopes.ownerB;
  const stem = `e2e-dsx-org53-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const seeds: { id: string; name: string; rows: number; organizationId: string }[] = [];
  for (const [actor, label, rows] of [[a, 'a-source', 1], [a, 'a-target', 2], [b, 'b-source', 1], [b, 'b-target', 3]] as const) {
    const name = `${stem}-${label}`;
    const created = await scopes.send<Created>(actor, 'POST', CREATE, {
      dataset_name: name, model_type: 'GenerativeLLM', number_of_rows: rows, number_of_columns: 1,
      columns: [{ name: 'input', type: 'text', role: 'input' }],
    });
    expect(created.status).toBe(200);
    seeds.push({ id: created.body.result.dataset_id, name, rows, organizationId: actor.organizationId });
  }
  await testInfo.attach('seeded-ids', { contentType: 'application/json', body: JSON.stringify(seeds) });
  const [aSource, aTarget, bSource, bTarget] = seeds;
  for (const [actor, source, target, foreign] of [[a, aSource, aTarget, bTarget], [b, bSource, bTarget, aTarget]] as const) {
    const persisted = await actor.api.get<Table>(table(target.id), { current_page_index: 0, page_size: 30 });
    const rows = persisted.result.table.map(({ row_id, order }) => ({ row_id, order }));
    expect(rows).toHaveLength(target.rows);
    expect(new Set(rows.map(r => r.row_id)).size).toBe(target.rows);
    const context = await scopes.openContext(browser, actor);
    await context.route('**/*', r => ['localhost', '127.0.0.1'].includes(new URL(r.request().url()).hostname)
      ? r.continue() : r.abort('blockedbyclient'));
    try {
      const page = await context.newPage();
      page.setDefaultTimeout(UI_READY);
      await page.goto(route(source.id), { waitUntil: 'domcontentloaded' });
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(source.name);
      await page.getByRole('button', { name: 'Switch dataset', exact: true }).click();
      const search = page.getByPlaceholder('Search datasets', { exact: true });
      const searched = page.waitForResponse(r => {
        const url = new URL(r.url());
        return url.pathname === NAMES && url.searchParams.get('search_text') === target.name;
      });
      await search.fill(target.name);
      const response = await searched;
      expect(response.status()).toBe(200);
      expect((await response.json() as Names).result.datasets.map(({ dataset_id, name, row_count }) => ({ dataset_id, name, row_count })))
        .toEqual([{ dataset_id: target.id, name: target.name, row_count: target.rows }]);
      await expect(page.getByRole('menuitem')).toHaveText([target.name]);
      const own = await actor.api.get<Names>(NAMES, { search_text: stem });
      expect(own.result.datasets.map(r => r.dataset_id).sort()).toEqual([source.id, target.id].sort());
      const gridResponse = (url: string) => {
        const parsed = new URL(url);
        return parsed.pathname === table(target.id) && parsed.searchParams.get('page_size') === '30'
          && parsed.searchParams.get('column_config_only') !== 'true';
      };
      const tableRead = page.waitForResponse(r => gridResponse(r.url()));
      // MUI MenuItem handles Enter via ButtonBase. This tests activation, not arrow-key traversal.
      await page.getByRole('menuitem', { name: target.name, exact: true }).press('Enter');
      const read = await tableRead;
      expect(read.status()).toBe(200);
      expect((await read.json() as Table).result.table.map(({ row_id, order }) => ({ row_id, order }))).toEqual(rows);
      await expect(page).toHaveURL(`${E2E.appUrl}${route(target.id)}`);
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(target.name);
      const reloading = page.waitForResponse(r => gridResponse(r.url()));
      await page.reload({ waitUntil: 'domcontentloaded' });
      const reloaded = await reloading;
      expect(reloaded.status()).toBe(200);
      expect((await reloaded.json() as Table).result.table.map(({ row_id, order }) => ({ row_id, order }))).toEqual(rows);
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(target.name);
      await page.getByRole('button', { name: 'Switch dataset', exact: true }).click();
      const foreignSearch = page.waitForResponse(r => {
        const url = new URL(r.url());
        return url.pathname === NAMES && url.searchParams.get('search_text') === foreign.name;
      });
      await search.fill(foreign.name);
      const deniedList = await foreignSearch;
      expect(deniedList.status()).toBe(200);
      expect((await deniedList.json() as Names).result.datasets).toEqual([]);
      await expect(page.getByRole('menuitem')).toHaveCount(0);
      await expect(page.getByText('No datasets found', { exact: true })).toBeVisible();
      expect((await scopes.send(actor, 'GET', table(foreign.id))).status).toBe(400);
      expect((await actor.api.get<Names>(NAMES, { search_text: foreign.name })).result.datasets).toEqual([]);
      expect((await actor.api.get<Table>(table(target.id), { current_page_index: 0, page_size: 30 })).result.table
        .map(({ row_id, order }) => ({ row_id, order }))).toEqual(rows);
    } finally { await context.close(); }
  }
});
