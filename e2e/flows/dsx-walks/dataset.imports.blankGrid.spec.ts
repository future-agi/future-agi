import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

type Created = { result: { dataset_id: string; rows_created: number; columns_created: number } };
type Table = { result: { column_config: { id: string; name: string; data_type: string; role: string }[];
  table: ({ row_id: string } & Record<string, unknown>)[] } };
const CREATE = '/model-hub/develops/create-dataset-manually/';
const UI_READY = 60_000;

// ManuallyCreateDataset.jsx + BlankGridColumns.jsx; fixture/status contracts from blank-grid.spec.ts.
test('@DSX-WALK-IN-02: Create a named blank grid with typed columns and reopen its persisted rows.', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DSX-WALK-IN-02', area: 'dsx-walks',
    userGoal: 'dataset.imports.blankGrid: create a typed blank grid without losing names, roles or tenant isolation',
    steps: ['seed a same-named dataset in organization B through the served API',
      'create organization A’s blank grid with three typed columns and two blank rows in the UI',
      'reload and inspect exact headers and blank cells', 'refuse cross-organization table reads and row additions'],
    backendChecks: ['manual creation persists exactly text/input, integer/expected and json/metadata columns',
      'table API returns two stable row ids with null cells for A and no rows for B',
      'foreign reads return semantic 400 and append returns 404, leaving both datasets unchanged'],
  }),
}, async ({ browser, scopeActors: scopes }, testInfo) => {
  test.setTimeout(600_000);
  const a = scopes.ownerA;
  const b = scopes.ownerB;
  expect(a.organizationId).not.toBe(b.organizationId);
  const name = `dsx-grid-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const columns = [
    { name: 'question', type: 'text', role: 'input' },
    { name: 'answer', type: 'integer', role: 'expected' },
    { name: 'context', type: 'json', role: 'metadata' },
  ];
  const seeded = await scopes.send<Created>(b, 'POST', CREATE, { dataset_name: name, model_type: 'GenerativeLLM',
    number_of_rows: 0, number_of_columns: 3, columns });
  expect(seeded.status).toBe(200);
  expect(seeded.body.result).toMatchObject({ rows_created: 0, columns_created: 3 });
  const otherId = seeded.body.result.dataset_id;
  await testInfo.attach('seeded-organization-b', { contentType: 'application/json', body: JSON.stringify({
    datasetId: otherId, organizationId: b.organizationId, workspaceId: b.workspaceId }) });
  const context = await scopes.openContext(browser, a);
  try {
    const origins = new Set([new URL(E2E.appUrl).origin, new URL(E2E.apiUrl).origin]);
    await context.route('**/*', route => origins.has(new URL(route.request().url()).origin)
      ? route.continue() : route.abort('blockedbyclient'));
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: 'New dataset', exact: true }).click();
    await page.getByText('Start with a blank grid', { exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Start with a blank grid', exact: true });
    await dialog.getByRole('textbox', { name: /^Name/ }).fill(name);
    await dialog.getByRole('button', { name: 'Add column', exact: true }).click();
    const labels = ['Text', 'Integer', 'JSON'];
    const roles = ['Input', 'Expected', 'Metadata'];
    for (const [index, column] of columns.entries()) {
      await dialog.getByRole('textbox', { name: `Column ${index + 1} name`, exact: true }).fill(column.name);
      await dialog.getByRole('combobox', { name: `Column ${index + 1} type`, exact: true }).click();
      await page.getByRole('option', { name: labels[index], exact: true }).click();
      await dialog.getByRole('combobox', { name: `Column ${index + 1} role`, exact: true }).click();
      await page.getByRole('option', { name: roles[index], exact: true }).click();
    }
    await dialog.getByRole('textbox', { name: 'Column 3 name', exact: true }).fill('question');
    await expect(dialog.getByRole('button', { name: 'Create dataset', exact: true })).toBeDisabled();
    await dialog.getByRole('textbox', { name: 'Column 3 name', exact: true }).fill('context');
    await dialog.getByRole('spinbutton', { name: 'Blank rows', exact: true }).fill('2');
    const pending = page.waitForResponse(r => new URL(r.url()).pathname === CREATE && r.request().method() === 'POST');
    await dialog.getByRole('button', { name: 'Create dataset', exact: true }).click();
    const response = await pending;
    expect(response.request().postDataJSON()).toEqual({ dataset_name: name, model_type: 'GenerativeLLM',
      number_of_rows: 2, number_of_columns: 3, columns });
    expect(response.status()).toBe(200);
    const created = await response.json() as Created;
    expect(created.result).toMatchObject({ rows_created: 2, columns_created: 3 });
    const id = created.result.dataset_id;
    expect(id).not.toBe(otherId);
    await testInfo.attach('created-organization-a', { contentType: 'application/json', body: JSON.stringify({
      owner: 'dataset.imports.blankGrid', datasetId: id, organizationId: a.organizationId, workspaceId: a.workspaceId }) });
    await expect(page).toHaveURL(new RegExp(`/dashboard/develop/${id}(?:[/?]|$)`));
    const tablePath = `/model-hub/develops/${id}/get-dataset-table/`;
    const read = async () => {
      const result = await scopes.send<Table>(a, 'GET', tablePath, undefined, { current_page_index: 0, page_size: 25 });
      expect(result.status).toBe(200);
      return result.body.result;
    };
    const stored = await read();
    expect(stored.column_config.map(({ name, data_type, role }) => ({ name, type: data_type, role }))).toEqual(columns);
    expect(stored.table).toHaveLength(2);
    expect(new Set(stored.table.map(row => row.row_id)).size).toBe(2);
    await page.reload({ waitUntil: 'domcontentloaded' });
    for (const column of stored.column_config) {
      await expect(page.locator(`.ag-header-cell[col-id="${column.id}"]`).getByText(column.name, { exact: true })).toBeVisible();
      expect(stored.table.map(row => (row[column.id] as { cell_value: unknown }).cell_value)).toEqual([null, null]);
      await expect(page.locator(`.ag-row [col-id="${column.id}"]`))
        .toHaveText(column.data_type === 'integer' ? ['', ''] : ['Empty', 'Empty']);
    }
    const denied = await scopes.send<{ message: string }>(b, 'GET', tablePath, undefined, { current_page_index: 0, page_size: 25 });
    expect(denied.status).toBe(400);
    expect(denied.body.message).toBe('The specified dataset does not exist or has been deleted.');
    expect((await scopes.send(b, 'POST', `/model-hub/develops/${id}/add_empty_rows/`, { num_rows: 1 })).status).toBe(404);
    expect(await read()).toEqual(stored);
    const ownB = await scopes.send<Table>(b, 'GET', `/model-hub/develops/${otherId}/get-dataset-table/`, undefined,
      { current_page_index: 0, page_size: 25 });
    expect(ownB.status).toBe(200);
    expect(ownB.body.result.table).toEqual([]);
    expect(ownB.body.result.column_config.map(({ name, data_type, role }) => ({ name, type: data_type, role }))).toEqual(columns);
    await testInfo.attach('persisted-identities', { contentType: 'application/json', body: JSON.stringify({
      datasetId: id, rowIds: stored.table.map(row => row.row_id), columnIds: stored.column_config.map(column => column.id),
      otherDatasetId: otherId }) });
  } finally {
    await context.close();
  }
});
