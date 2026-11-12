import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// ManualDatasetCreateRequestSerializer and DatasetUpdateCellValueRequestSerializer.
const CREATE = '/model-hub/develops/create-dataset-manually/';
// DatasetViewQuerySerializer / DatasetViewWriteSerializer and dataset_views.py.
const VIEWS = '/model-hub/r3/saved-views/';
// ViewMenu.jsx, SaveViewDialog and AG Grid's existing col-id/row-id hooks.
const UI_READY = 60_000;
type Column = { id: string; name: string };
type Table = { result: { column_config: Column[]; table: { row_id: string; [key: string]: unknown }[] } };
type View = { id: string; name: string; state: Record<string, any>; shared: boolean;
  mine: boolean; can_delete: boolean; created_at: string; created_by: string };
type List = { result: { results: View[] } };

test('DATA-E2E-049: save and restore private and shared grid configurations', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-049', area: 'datasets',
    userGoal: 'Save a grid configuration and restore its filters, sort, columns and density without exposing private views or another tenant’s data',
    steps: [
      'create synthetic datasets in two organizations and open a linked filtered and sorted grid layout',
      'save a private view through the dialog and reopen it in a fresh browser context',
      'apply the saved view and verify exact filtered row order, hidden column and density',
      'save a shared copy and inspect it as another workspace member',
      'delete the private view and use Undo to restore the same record',
    ],
    backendChecks: [
      'UI-created views and delete/restore retain exact configuration, author and dataset organization/workspace in Postgres and the real API',
      'restored UI and real table requests return exactly the filtered rows in saved order with the saved layout and density',
      'private views are excluded for other members and foreign organization/workspace and unauthorized shared writes are refused',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Eight UI_READY budgets (480s) plus synchronous setup/readbacks (120s).
  test.setTimeout(600_000);
  const a = scopes.ownerA;
  const b = scopes.ownerB;
  const name = `e2e-data49-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const dataset = (await a.api.post<{ result: { dataset_id: string } }>(CREATE,
    { dataset_name: name, number_of_rows: 3, number_of_columns: 2 })).result.dataset_id;
  const tablePath = `/model-hub/develops/${dataset}/get-dataset-table/`;
  const initial = (await a.api.get<Table>(tablePath)).result;
  const [column, other] = initial.column_config;
  const values = [`${name}-keep-a`, `${name}-drop-m`, `${name}-keep-z`];
  const rowIds = initial.table.map(row => row.row_id);
  for (const [i, row] of rowIds.entries()) {
    await a.api.post(`/model-hub/develops/${dataset}/update_cell_value/`,
      { row_id: row, column_id: column.id, new_value: values[i] });
  }
  const foreign = (await b.api.post<{ result: { dataset_id: string } }>(CREATE,
    { dataset_name: `${name}-foreign`, number_of_rows: 1, number_of_columns: 1 })).result.dataset_id;
  // gridViewLink.js: presentation-only state; the real grid still reads real scoped rows.
  const filter = { id: `${name}-filter`, columnId: column.id, filterConfig: {
    filterType: 'text', filterOp: 'contains', filterValue: 'keep',
  }, _meta: { parentProperty: '' } };
  const state = { q: '', density: 'tall', filters: [filter],
    sort: [{ colId: column.id, sort: 'desc' }],
    layout: { columns: { visible: { [other.id]: false } } } };
  await testInfo.attach('saved-view-identities', { contentType: 'application/json', body: JSON.stringify({
    dataset, foreign, name, column, other, rowIds, values, state,
    organizationId: a.organizationId, workspaceId: a.workspaceId, authorId: a.userId,
  }) });
  const list = (actor = a) => actor.api.get<List>(VIEWS, { surface: 'grid', scope: dataset });
  const context = await scopes.openContext(browser, a);
  await context.route('**/*', route => ['localhost', '127.0.0.1'].includes(new URL(route.request().url()).hostname)
    ? route.continue() : route.abort('blockedbyclient'));
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  const route = `/dashboard/develop/${dataset}`;
  await page.goto(`${route}?grid_view=${encodeURIComponent(JSON.stringify(state))}`, { waitUntil: 'domcontentloaded' });
  const cells = page.locator(`.ag-row [col-id="${column.id}"]`);
  await expect(cells).toHaveText([values[2], values[0]], { timeout: UI_READY });
  await expect(page.locator(`.ag-row [col-id="${other.id}"]`)).toHaveCount(0);
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'View options', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Tall (6 lines)', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: 'Save as…', exact: true }).click();
  const dialog = page.getByRole('dialog').filter({ has: page.getByText('Save this view', { exact: true }) });
  const privateName = `${name}-private`;
  await dialog.getByRole('textbox', { name: 'Name', exact: true }).fill(privateName);
  const savedResponse = page.waitForResponse(r => new URL(r.url()).pathname === VIEWS && r.request().method() === 'POST', { timeout: UI_READY });
  await dialog.getByRole('button', { name: 'Save view', exact: true }).click();
  const saved = await savedResponse;
  expect(saved.status()).toBe(200);
  const privateView: View = (await saved.json()).result;
  expect(privateView.state).toMatchObject(state);
  expect(privateView.shared).toBe(false);
  const posted = saved.request().postDataJSON();
  expect(privateView.state).toEqual(posted.state);
  expect((await list()).result.results.map(v => v.id)).toEqual([privateView.id]);
  expect((await list(scopes.member)).result.results).toEqual([]);
  expect(await probe.pg<{ dataset_id: string; created_by_id: string; organization_id: string; workspace_id: string; state: unknown }>(
    `SELECT v.dataset_id, v.created_by_id, d.organization_id, d.workspace_id, v.state
     FROM model_hub_dataset_saved_view v JOIN model_hub_dataset d ON d.id=v.dataset_id
     WHERE v.id=$1 AND NOT v.deleted`, [privateView.id]))
    .toEqual([{ dataset_id: dataset, created_by_id: a.userId, organization_id: a.organizationId, workspace_id: a.workspaceId, state: posted.state }]);
  await context.close();

  // Fresh browser storage ensures server persistence, not local layout preferences, supplies the view.
  const fresh = await scopes.openContext(browser, a);
  await fresh.route('**/*', r => ['localhost', '127.0.0.1'].includes(new URL(r.request().url()).hostname)
    ? r.continue() : r.abort('blockedbyclient'));
  const reopened = await fresh.newPage();
  reopened.setDefaultTimeout(UI_READY);
  await reopened.goto(route, { waitUntil: 'domcontentloaded' });
  await expect(reopened.locator(`.ag-row [col-id="${column.id}"]`)).toHaveText(values, { timeout: UI_READY });
  await reopened.keyboard.press('Escape');
  await reopened.getByRole('button', { name: 'View options', exact: true }).click();
  await reopened.getByRole('button', { name: privateName, exact: true }).click();
  await expect(reopened.locator(`.ag-row [col-id="${column.id}"]`)).toHaveText([values[2], values[0]], { timeout: UI_READY });
  await expect(reopened.locator(`.ag-row [col-id="${other.id}"]`)).toHaveCount(0);
  // Same table path and predicate used by DevelopDataV2 / buildApiFilterFromPanelRow.
  const filtered = (await a.api.get<Table>(tablePath, { filters: JSON.stringify([{ column_id: column.id,
    filter_config: { filter_type: 'text', filter_op: 'contains', filter_value: 'keep' } }]),
    sort: JSON.stringify([{ column_id: column.id, type: 'descending' }]) })).result.table;
  expect(filtered.map(row => row.row_id)).toEqual([rowIds[2], rowIds[0]]);
  await reopened.keyboard.press('Escape');
  await reopened.getByRole('button', { name: 'View options', exact: true }).click();
  await expect(reopened.getByRole('button', { name: 'Tall (6 lines)', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await reopened.getByRole('button', { name: 'Save as…', exact: true }).click();
  const sharedDialog = reopened.getByRole('dialog').filter({ has: reopened.getByText('Save this view', { exact: true }) });
  const sharedName = `${name}-shared`;
  await sharedDialog.getByRole('textbox', { name: 'Name', exact: true }).fill(sharedName);
  await sharedDialog.getByRole('checkbox', { name: 'Share with everyone in this workspace', exact: true }).check();
  const sharedResponse = reopened.waitForResponse(r => new URL(r.url()).pathname === VIEWS && r.request().method() === 'POST', { timeout: UI_READY });
  await sharedDialog.getByRole('button', { name: 'Save view', exact: true }).click();
  const sharedResult = await sharedResponse;
  expect(sharedResult.status()).toBe(200);
  const shared: View = (await sharedResult.json()).result;
  const memberViews = (await list(scopes.member)).result.results;
  expect(memberViews.map(v => ({ id: v.id, shared: v.shared, mine: v.mine, can_delete: v.can_delete })))
    .toEqual([{ id: shared.id, shared: true, mine: false, can_delete: false }]);
  expect(memberViews[0].state).toEqual(privateView.state);
  const memberContext = await scopes.openContext(browser, scopes.member);
  await memberContext.route('**/*', r => ['localhost', '127.0.0.1'].includes(new URL(r.request().url()).hostname)
    ? r.continue() : r.abort('blockedbyclient'));
  const memberPage = await memberContext.newPage();
  memberPage.setDefaultTimeout(UI_READY);
  await memberPage.goto(route, { waitUntil: 'domcontentloaded' });
  await memberPage.keyboard.press('Escape');
  await memberPage.getByRole('button', { name: 'View options', exact: true }).click();
  await expect(memberPage.getByRole('button', { name: privateName, exact: true })).toHaveCount(0);
  await memberPage.getByRole('button', { name: new RegExp(`^${sharedName}`) }).click();
  await expect(memberPage.locator(`.ag-row [col-id="${column.id}"]`)).toHaveText([values[2], values[0]], { timeout: UI_READY });
  await memberContext.close();

  for (const actor of [b, scopes.withWorkspace(a, scopes.emptyWorkspace.id)]) {
    const response = await scopes.send(actor, 'GET', VIEWS, undefined, { surface: 'grid', scope: dataset });
    expect([403, 404]).toContain(response.status);
    expect(JSON.stringify(response.body)).not.toContain(privateName);
    expect((await scopes.send(actor, 'DELETE', `${VIEWS}${shared.id}/`)).status).toBe(404);
    expect((await scopes.send(actor, 'POST', VIEWS, { surface: 'grid', scope: dataset, restore_id: privateView.id })).status).toBe(404);
  }
  expect((await scopes.send(scopes.viewer, 'POST', VIEWS,
    { surface: 'grid', scope: dataset, name: `${name}-forbidden`, state, shared: true })).status).toBe(403);
  expect((await scopes.send(scopes.member, 'DELETE', `${VIEWS}${shared.id}/`)).status).toBe(403);
  expect((await b.api.get<List>(VIEWS, { surface: 'grid', scope: foreign })).result.results).toEqual([]);

  await reopened.keyboard.press('Escape');
  await reopened.getByRole('button', { name: 'View options', exact: true }).click();
  const removedResponse = reopened.waitForResponse(r => new URL(r.url()).pathname === `${VIEWS}${privateView.id}/` && r.request().method() === 'DELETE', { timeout: UI_READY });
  await reopened.getByRole('button', { name: `Delete ${privateName}`, exact: true }).click();
  expect((await removedResponse).status()).toBe(200);
  expect((await list()).result.results.map(v => v.id)).toEqual([shared.id]);
  const restoredResponse = reopened.waitForResponse(r => new URL(r.url()).pathname === VIEWS && r.request().method() === 'POST', { timeout: UI_READY });
  await reopened.keyboard.press('Escape');
  await reopened.getByRole('button', { name: 'Undo', exact: true }).click();
  const restored = await restoredResponse;
  expect(restored.status()).toBe(200);
  expect((await restored.json()).result).toEqual(privateView);
  expect(await probe.pg<{ id: string; deleted: boolean; state: unknown }>(
    'SELECT id, deleted, state FROM model_hub_dataset_saved_view WHERE id=$1', [privateView.id]))
    .toEqual([{ id: privateView.id, deleted: false, state: privateView.state }]);
  await testInfo.attach('persisted-saved-views', { contentType: 'application/json', body: JSON.stringify((await list()).result.results) });
  await fresh.close();
});
