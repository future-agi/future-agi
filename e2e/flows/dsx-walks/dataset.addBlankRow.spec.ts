import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

type Table = { result: { column_config: { id: string; name: string; data_type: string; role: string }[];
  table: ({ row_id: string } & Record<string, unknown>)[] } };
const CREATE = '/model-hub/develops/create-dataset-manually/';
const UI_READY = 60_000;

// Sources pinned at 818b068e: ManuallyCreateDataset.jsx; DevelopDataRightSection.jsx
// (classic addBlank); CommandPalette.jsx; blank-grid.spec.ts (scope/status contract).
test('@DSX-WALK-IN-01: Add one blank row through the command palette and retain it after reload.', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DSX-WALK-IN-01', area: 'dsx-walks',
    userGoal: 'dataset.addBlankRow: persist a single blank row immediately in the classic grid',
    steps: ['seed an empty typed dataset through the served manual-create API',
      'invoke Add row from the command palette and reload the grid',
      'open the persisted row and deny the other organization read and append access'],
    backendChecks: ['add_empty_rows receives exactly num_rows=1',
      'the table retains exactly one row with null cells and the original typed column',
      'foreign table read returns semantic 400 and foreign append returns 404 without changing the owner row'],
  }),
}, async ({ browser, scopeActors: scopes }, testInfo) => {
  test.setTimeout(480_000);
  const owner = scopes.ownerA;
  const foreign = scopes.ownerB;
  expect(owner.organizationId).not.toBe(foreign.organizationId);
  const name = `dsx-blank-row-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const columns = [{ name: 'question', type: 'text', role: 'input' }];
  const seeded = await scopes.send<{ result: { dataset_id: string; rows_created: number; columns_created: number } }>(
    owner, 'POST', CREATE, { dataset_name: name, model_type: 'GenerativeLLM',
      number_of_rows: 0, number_of_columns: 1, columns });
  expect(seeded.status).toBe(200);
  expect(seeded.body.result).toMatchObject({ rows_created: 0, columns_created: 1 });
  const id = seeded.body.result.dataset_id;
  const tablePath = `/model-hub/develops/${id}/get-dataset-table/`;
  const addPath = `/model-hub/develops/${id}/add_empty_rows/`;
  const read = async () => {
    const response = await scopes.send<Table>(owner, 'GET', tablePath, undefined, { current_page_index: 0, page_size: 25 });
    expect(response.status).toBe(200);
    return response.body.result;
  };
  await testInfo.attach('seeded-identities', { contentType: 'application/json', body: JSON.stringify({
    owner: 'dataset.addBlankRow', datasetId: id, organizationId: owner.organizationId,
    workspaceId: owner.workspaceId, foreignOrganizationId: foreign.organizationId }) });
  expect((await read()).table).toEqual([]);
  const context = await scopes.openContext(browser, owner);
  try {
    const origins = new Set([new URL(E2E.appUrl).origin, new URL(E2E.apiUrl).origin]);
    await context.route('**/*', route => origins.has(new URL(route.request().url()).origin)
      ? route.continue() : route.abort('blockedbyclient'));
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    await page.goto(`/dashboard/develop/${id}`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: 'Search commands', exact: true }).click();
    await page.getByRole('combobox', { name: 'Command', exact: true }).fill('Add row');
    const pending = page.waitForResponse(r => new URL(r.url()).pathname === addPath && r.request().method() === 'POST');
    await page.locator('[role="option"][data-command="grid.addRow"]').click();
    const added = await pending;
    expect(added.request().postDataJSON()).toEqual({ num_rows: 1 });
    expect(added.status()).toBe(200);
    await expect(page.getByText('Added a blank row', { exact: true })).toBeVisible();
    await expect.poll(async () => (await read()).table.length, { timeout: UI_READY }).toBe(1);
    const saved = await read();
    expect(saved.column_config.map(({ name, data_type, role }) => ({ name, type: data_type, role }))).toEqual(columns);
    const column = saved.column_config[0];
    const row = saved.table[0];
    expect(row[column.id]).toMatchObject({ cell_value: null });
    await testInfo.attach('persisted-row', { contentType: 'application/json', body: JSON.stringify({
      datasetId: id, rowId: row.row_id, columnId: column.id }) });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.locator(`.ag-row [col-id="${column.id}"]`)).toHaveText(['Empty']);
    expect((await read()).table).toEqual(saved.table);
    await page.goto(`/dashboard/develop/${id}?row=${row.row_id}`, { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('button', { name: `Copy ID ${row.row_id}`, exact: true })).toBeVisible();
    const denied = await scopes.send<{ message: string }>(foreign, 'GET', tablePath, undefined,
      { current_page_index: 0, page_size: 25 });
    expect(denied.status).toBe(400);
    expect(denied.body.message).toBe('The specified dataset does not exist or has been deleted.');
    expect((await scopes.send(foreign, 'POST', addPath, { num_rows: 1 })).status).toBe(404);
    expect((await read()).table).toEqual(saved.table);
  } finally {
    await context.close();
  }
});
