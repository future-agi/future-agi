import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// ManualDatasetCreateRequestSerializer and DatasetSettingsRequestSerializer.
const CREATE = '/model-hub/develops/create-dataset-manually/';
const editPath = (id: string) => `/model-hub/develops/${id}/edit_dataset_behavior/`;
// DatasetListQuerySerializer and DatasetListItemSerializer.
const LIST = '/model-hub/develops/get-datasets/';
const tablePath = (id: string) => `/model-hub/develops/${id}/get-dataset-table/`;
const UI_READY = 60_000;
type Created = { result: { dataset_id: string } };
type Row = { id: string; name: string; created_by: string; created_by_name: string; source: string; updated_at: string };
type Listed = { result: { datasets: Row[]; total_count: number } };
type Stored = { id: string; name: string; user_id: string; owner_name: string; source: string; updated_at: string; organization_id: string; workspace_id: string };

test('DATA-E2E-024: dataset metadata columns and updated sorting retain the authorized rows', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-024', area: 'datasets',
    userGoal: 'Inspect dataset owners and sources and keep my Updated sort after reload without seeing another tenant’s data',
    steps: [
      'create two datasets per organization and one in a sibling workspace, then update the older datasets',
      'open View columns and show Owner, Source and Updated while deferred experiment and version columns remain absent',
      'sort Updated ascending and descending and compare the exact visible rows with the list API and stored metadata',
      'reload and retain the selected columns and Updated order in each organization',
      'refuse foreign dataset reads and exclude foreign and sibling-workspace rows from list searches',
    ],
    backendChecks: [
      'both tenants list exactly their own persisted dataset owners, sources and update timestamps and the UI cells match',
      'Updated sorting is sent to the real list endpoint, orders before pagination and survives reload without modifying dataset state',
      'foreign organization and sibling workspace reads are refused and their rows never enter the list response',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // API seeding 120s + 2 tenants x 6 UI_READY (720s) + synchronous PG/API checks 60s.
  test.setTimeout(900_000);
  const prefix = `e2e-data24-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const a = scopes.ownerA, b = scopes.ownerB;
  const sibling = scopes.withWorkspace(a, scopes.emptyWorkspace.id);
  const seeded: { id: string; name: string; organization_id: string; workspace_id: string }[] = [];
  for (const [actor, label] of [[a, 'a-old'], [a, 'a-new'], [b, 'b-old'], [b, 'b-new'], [sibling, 'sibling']] as const) {
    const name = `${prefix}-${label}`;
    const created = await scopes.send<Created>(actor, 'POST', CREATE, { dataset_name: name, number_of_rows: 1, number_of_columns: 1 });
    expect(created.status).toBe(200);
    seeded.push({ id: created.body.result.dataset_id, name, organization_id: actor.organizationId, workspace_id: actor.workspaceId });
  }
  const [aOld, aNew, bOld, bNew, siblingRow] = seeded;
  for (const [actor, row] of [[a, aOld], [b, bOld]] as const) {
    const changed = await scopes.send(actor, 'PUT', editPath(row.id), { description: `${prefix}-updated` });
    expect(changed.status).toBe(200);
  }
  await testInfo.attach('list-column-identities', { contentType: 'application/json', body: JSON.stringify(seeded) });
  const storage = () => probe.pg<Stored>(
    'SELECT d.id, d.name, d.user_id, u.name AS owner_name, d.source, d.updated_at, d.organization_id, d.workspace_id FROM model_hub_dataset d LEFT JOIN accounts_user u ON d.user_id=u.id WHERE d.id = ANY($1::uuid[]) AND NOT d.deleted ORDER BY d.name',
    [seeded.map(row => row.id)]);
  const stored = await storage();
  const receipts: unknown[] = [];
  for (const [actor, older, newer, foreign] of [[a, aOld, aNew, bOld], [b, bOld, bNew, aOld]] as const) {
    const context = await scopes.openContext(browser, actor);
    await context.route('**/*', route => ['localhost', '127.0.0.1'].includes(new URL(route.request().url()).hostname)
      ? route.continue() : route.abort('blockedbyclient'));
    const page = await context.newPage();
    await page.setViewportSize({ width: 1600, height: 1000 });
    page.setDefaultTimeout(UI_READY);
    await page.goto(`/dashboard/develop?q=${encodeURIComponent(prefix)}`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: 'View options', exact: true }).click();
    await page.getByRole('button', { name: 'Edit…', exact: true }).click();
    for (const label of ['Version', 'Baseline', 'Best', 'Last experiment'])
      await expect(page.getByRole('checkbox', { name: `Show ${label}`, exact: true })).toHaveCount(0);
    for (const label of ['Owner', 'Source', 'Updated'])
      await page.getByRole('checkbox', { name: `Show ${label}`, exact: true }).check();
    await page.keyboard.press('Escape');
    await page.keyboard.press('Escape');
    const grid = page.getByRole('grid', { name: 'Datasets', exact: true });
    // DataTable maps its column ids to MUI data-field attributes; NameCell renders the name as a link.
    const names = grid.locator('.MuiDataGrid-row [data-field="name"] a');
    await test.step('backend check 1: exact stored metadata and UI cells', async () => {
      const own = stored.filter(row => row.organization_id === actor.organizationId && row.workspace_id === actor.workspaceId);
      expect(own.map(row => row.id).sort()).toEqual([older.id, newer.id].sort());
      const response = await actor.api.get<Listed>(LIST, { search_text: prefix });
      expect(response.result.datasets.map(row => row.id).sort()).toEqual(own.map(row => row.id).sort());
      for (const record of own) {
        const row = response.result.datasets.find(item => item.id === record.id)!;
        expect(row).toMatchObject({ created_by: actor.userId, created_by_name: record.owner_name, source: record.source });
        expect(new Date(row.updated_at).toISOString()).toBe(new Date(record.updated_at).toISOString());
        const visible = grid.locator(`.MuiDataGrid-row[data-id="${record.id}"]`);
        await expect(visible.locator('[data-field="owner"]')).toHaveText(record.owner_name, { timeout: UI_READY });
        expect(record.source).toBe('build');
        await expect(visible.locator('[data-field="source"]')).toHaveText('Build', { timeout: UI_READY });
        await expect(visible.locator('[data-field="updatedAt"]')).not.toHaveText('—', { timeout: UI_READY });
      }
    });
    await test.step('backend check 2: server ordering and reload', async () => {
      const header = grid.locator('[role="columnheader"][data-field="updatedAt"]');
      for (const [direction, expected] of [['ascending', [newer.name, older.name]], ['descending', [older.name, newer.name]]] as const) {
        const reading = page.waitForResponse(r => {
          const url = new URL(r.url());
          return url.pathname === LIST && url.searchParams.get('sort') === JSON.stringify([{ column_id: 'updated_at', type: direction }]);
        }, { timeout: UI_READY });
        await header.click();
        const read = await reading;
        expect(read.status()).toBe(200);
        expect((await read.json() as Listed).result.datasets.map(row => row.name)).toEqual([...expected]);
        await expect(names).toHaveText([...expected], { timeout: UI_READY });
        for (let index = 0; index < 2; index++) {
          const result = await actor.api.get<Listed>(LIST, { search_text: prefix, page: index, page_size: 1, sort: JSON.stringify([{ column_id: 'updated_at', type: direction }]) });
          expect(result.result.datasets.map(row => row.name)).toEqual([expected[index]]);
        }
        receipts.push({ path: LIST, sort: direction, status: read.status(), expected });
      }
      const reload = page.waitForResponse(r => new URL(r.url()).pathname === LIST, { timeout: UI_READY });
      await page.reload({ waitUntil: 'domcontentloaded' });
      const read = await reload;
      expect(JSON.parse(new URL(read.url()).searchParams.get('sort')!)).toEqual([{ column_id: 'updated_at', type: 'descending' }]);
      expect(new URL(page.url()).searchParams.get('sort')).toBe('updated_at:desc');
      await expect(names).toHaveText([older.name, newer.name], { timeout: UI_READY });
      for (const field of ['owner', 'source', 'updatedAt']) await expect(grid.locator(`[role="columnheader"][data-field="${field}"]`)).toBeVisible({ timeout: UI_READY });
      expect(await storage()).toEqual(stored);
    });
    await test.step('backend check 3: no foreign rows or direct reads', async () => {
      for (const name of [foreign.name, siblingRow.name])
        expect((await actor.api.get<Listed>(LIST, { search_text: name })).result.datasets).toEqual([]);
      for (const [principal, id] of [[actor, foreign.id], [actor, siblingRow.id], [sibling, older.id]] as const)
        expect((await scopes.send(principal, 'GET', tablePath(id))).status).toBe(400);
      expect((await scopes.send(actor, 'GET', tablePath(older.id))).status).toBe(200);
      expect(await storage()).toEqual(stored);
    });
    await testInfo.attach(`list-columns-${actor === a ? 'a' : 'b'}`, { contentType: 'image/png', body: await page.screenshot() });
    await context.close();
  }
  await testInfo.attach('list-column-requests', { contentType: 'application/json', body: JSON.stringify({ receipts, browser: browser.browserType().name() }) });
});
