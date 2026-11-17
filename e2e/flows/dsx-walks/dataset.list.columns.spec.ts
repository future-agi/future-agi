import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// 818b068e64b476b2a694706df2fc84bed6df790e: datasetListColumns.jsx,
// DatasetListCells.jsx, RelativeTime.jsx, ViewMenu.jsx and ColumnsMenu.jsx.
const CREATE = '/model-hub/develops/create-dataset-manually/';
const LIST = '/model-hub/develops/get-datasets/';
const table = (id: string) => `/model-hub/develops/${id}/get-dataset-table/`;
const UI_READY = 60_000;
type Created = { result: { dataset_id: string } };
type Row = { id: string; name: string; created_by: string; created_by_name: string; source: string; updated_at: string };
type Listed = { result: { datasets: Row[]; total_count: number } };
type Stored = { id: string; name: string; user_id: string; owner_name: string; source: string; updated_at: string; organization_id: string; workspace_id: string };

test('@DSX-WALK-ORG-52: Show exact dataset metadata and retain Updated sorting without listing another tenant’s datasets.', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DSX-WALK-ORG-52', area: 'dsx-walks',
    userGoal: 'Inspect Owner, Source and Updated columns and retain the server-side Updated sort after reload',
    steps: ['create two datasets in each tenant and update each older dataset through the served API',
      'show Owner, Source and Updated, sort in both directions and reload in each tenant',
      'search for the other tenant’s names and try its table endpoint'],
    backendChecks: ['list owner ids, owner names, source and timestamps match stored dataset records',
      'ascending and descending sorting happens before pagination and matches the visible rows',
      'foreign list searches are empty and direct foreign table reads return 400; viewing does not mutate records'],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  test.setTimeout(900_000);
  const a = scopes.ownerA, b = scopes.ownerB;
  const stem = `e2e-dsx-org52-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const seeds: { id: string; name: string; organizationId: string }[] = [];
  for (const [actor, label] of [[a, 'a-old'], [a, 'a-new'], [b, 'b-old'], [b, 'b-new']] as const) {
    const name = `${stem}-${label}`;
    const created = await scopes.send<Created>(actor, 'POST', CREATE, {
      dataset_name: name, model_type: 'GenerativeLLM', number_of_rows: 1, number_of_columns: 1,
      columns: [{ name: 'input', type: 'text', role: 'input' }],
    });
    expect(created.status).toBe(200);
    seeds.push({ id: created.body.result.dataset_id, name, organizationId: actor.organizationId });
  }
  await testInfo.attach('seeded-ids', { contentType: 'application/json', body: JSON.stringify(seeds) });
  const [aOld, aNew, bOld, bNew] = seeds;
  for (const [actor, row] of [[a, aOld], [b, bOld]] as const)
    expect((await scopes.send(actor, 'PUT', `/model-hub/develops/${row.id}/edit_dataset_behavior/`,
      { description: `${stem}-updated` })).status).toBe(200);
  const storage = () => probe.pg<Stored>(
    `SELECT d.id, d.name, d.user_id, u.name AS owner_name, d.source, d.updated_at, d.organization_id, d.workspace_id
     FROM model_hub_dataset d LEFT JOIN accounts_user u ON u.id=d.user_id
     WHERE d.id=ANY($1::uuid[]) AND NOT d.deleted ORDER BY d.name`, [seeds.map(s => s.id)]);
  const stored = await storage();
  expect(stored).toHaveLength(4);
  for (const [actor, older, newer, foreign] of [[a, aOld, aNew, bOld], [b, bOld, bNew, aOld]] as const) {
    const context = await scopes.openContext(browser, actor);
    await context.route('**/*', route => ['localhost', '127.0.0.1'].includes(new URL(route.request().url()).hostname)
      ? route.continue() : route.abort('blockedbyclient'));
    try {
      const page = await context.newPage();
      page.setDefaultTimeout(UI_READY);
      await page.setViewportSize({ width: 1600, height: 1000 });
      await page.goto(`/dashboard/develop?q=${encodeURIComponent(stem)}`, { waitUntil: 'domcontentloaded' });
      await page.getByRole('button', { name: 'View options', exact: true }).click();
      await page.getByRole('button', { name: 'Edit…', exact: true }).click();
      for (const label of ['Owner', 'Source', 'Updated'])
        await page.getByRole('checkbox', { name: `Show ${label}`, exact: true }).check();
      await page.keyboard.press('Escape');
      await page.keyboard.press('Escape');
      const grid = page.getByRole('grid', { name: 'Datasets', exact: true });
      const names = grid.locator('.MuiDataGrid-row [data-field="name"] a');
      const listed = await actor.api.get<Listed>(LIST, { search_text: stem });
      expect(listed.result.total_count).toBe(2);
      expect(listed.result.datasets.map(r => r.id).sort()).toEqual([older.id, newer.id].sort());
      for (const seed of [older, newer]) {
        const db = stored.find(r => r.id === seed.id)!;
        const api = listed.result.datasets.find(r => r.id === seed.id)!;
        expect(api).toMatchObject({ id: seed.id, name: seed.name, created_by: actor.userId,
          created_by_name: db.owner_name, source: 'build' });
        expect(db).toMatchObject({ user_id: actor.userId, organization_id: actor.organizationId, workspace_id: actor.workspaceId, source: 'build' });
        expect(new Date(api.updated_at).toISOString()).toBe(new Date(db.updated_at).toISOString());
        const visible = grid.locator(`.MuiDataGrid-row[data-id="${seed.id}"]`);
        await expect(visible.locator('[data-field="owner"]')).toHaveText(db.owner_name);
        await expect(visible.locator('[data-field="source"]')).toHaveText('Build');
        // RelativeTime renders an actual <time dateTime={iso}>; avoid unstable '1m ago' assertions.
        await expect(visible.locator('[data-field="updatedAt"] time')).toBeVisible();
        await expect(visible.locator('[data-field="updatedAt"] time')).toHaveAttribute('datetime', new Date(api.updated_at).toISOString());
      }
      const updated = grid.locator('[role="columnheader"][data-field="updatedAt"]');
      for (const [direction, expected] of [['ascending', [newer.name, older.name]], ['descending', [older.name, newer.name]]] as const) {
        const sort = JSON.stringify([{ column_id: 'updated_at', type: direction }]);
        const response = page.waitForResponse(r => {
          const url = new URL(r.url());
          return url.pathname === LIST && url.searchParams.get('search_text') === stem && url.searchParams.get('sort') === sort;
        });
        await updated.click();
        const read = await response;
        expect(read.status()).toBe(200);
        expect((await read.json() as Listed).result.datasets.map(r => r.name)).toEqual([...expected]);
        await expect(names).toHaveText([...expected]);
        for (let index = 0; index < 2; index++) {
          const paged = await actor.api.get<Listed>(LIST, { search_text: stem, sort, page: index, page_size: 1 });
          expect(paged.result.datasets.map(r => r.name)).toEqual([expected[index]]);
        }
      }
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(names).toHaveText([older.name, newer.name]);
      expect(new URL(page.url()).searchParams.get('sort')).toBe('updated_at:desc');
      for (const field of ['owner', 'source', 'updatedAt'])
        await expect(grid.locator(`[role="columnheader"][data-field="${field}"]`)).toBeVisible();
      expect((await actor.api.get<Listed>(LIST, { search_text: foreign.name })).result.datasets).toEqual([]);
      expect((await scopes.send(actor, 'GET', table(foreign.id))).status).toBe(400);
      expect((await scopes.send(actor, 'GET', table(older.id))).status).toBe(200);
      expect(await storage()).toEqual(stored);
    } finally { await context.close(); }
  }
});
