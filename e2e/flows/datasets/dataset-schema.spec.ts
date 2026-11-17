import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { E2E } from '../../lib/env';

// ManualDatasetCreateRequestSerializer, DatasetUpdateCellValueRequestSerializer.
const CREATE = '/model-hub/develops/create-dataset-manually/';
// AddRowsFromFileRequestSerializer; the upload is synthetic CSV, never external media.
const IMPORT = '/model-hub/develops/add_rows_from_file/';
// SchemaDrawerBody and DevelopDataRightSection supply these accessible controls.
const UI_READY = 60_000;
type Column = { id: string; name: string };
type Row = { row_id: string; [key: string]: unknown };
type Schema = { declared: Record<string, unknown>; enforce: string; preset: string };
type Table = { result: { table: Row[]; column_config: Column[]; dataset_config: { schema?: Schema } } };

test('DATA-E2E-015: declared schemas reject invalid dataset writes without partial persistence', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-015', area: 'datasets',
    userGoal: 'Declare and enforce a dataset schema without accepting invalid edits or partially importing rows, while keeping other tenants isolated',
    steps: [
      'seed two synthetic organizations and populate three rows with a uniquely named categorical value through public APIs',
      'open Schema, declare inferred fields, choose Reject and confirm',
      'reload the saved declaration and reject invalid edits, additions and imports without partial persistence',
      'persist valid cell edits, added rows and CSV imports',
      'refuse foreign-tenant, wrong-workspace and viewer writes without changing owner data',
    ],
    backendChecks: [
      'UI-confirmed schema and enforcement persist in the scoped dataset and real table response',
      'rejected cell edits and mixed-validity additions/imports preserve exact rows, cells and columns, while valid writes persist',
      'foreign-tenant, wrong-workspace and viewer write attempts are refused without changing owner data',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Thirteen UI_READY budgets plus 120 seconds for synchronous setup and readbacks.
  test.setTimeout(900_000);
  const a = scopes.ownerA;
  const b = scopes.ownerB;
  const name = `e2e-schema15-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const dataset = (await a.api.post<{ result: { dataset_id: string } }>(CREATE,
    { dataset_name: name, number_of_rows: 3, number_of_columns: 1 })).result.dataset_id;
  const foreign = (await b.api.post<{ result: { dataset_id: string } }>(CREATE,
    { dataset_name: `${name}-foreign`, number_of_rows: 1, number_of_columns: 1 })).result.dataset_id;
  const tablePath = `/model-hub/develops/${dataset}/get-dataset-table/`;
  const schemaPath = `/model-hub/datasets/${dataset}/schema/`;
  const editPath = `/model-hub/develops/${dataset}/update_cell_value/`;
  const addPath = `/model-hub/develops/${dataset}/add_rows/`;
  const read = () => a.api.get<Table>(tablePath, { page_size: 500, current_page_index: 0 });
  const initial = (await read()).result;
  const column = initial.column_config[0];
  const rowIds = initial.table.map(row => row.row_id);
  const allowed = `${name}-allowed`;
  for (const row_id of rowIds) await a.api.post(editPath, { row_id, column_id: column.id, new_value: allowed });
  await testInfo.attach('schema-identities', { contentType: 'application/json', body: JSON.stringify({
    dataset, foreign, column, rowIds, allowed, organizationId: a.organizationId, workspaceId: a.workspaceId,
  }) });
  const context = await scopes.openContext(browser, a);
  await context.route('**/*', route => ['localhost', '127.0.0.1'].includes(new URL(route.request().url()).hostname)
    ? route.continue() : route.abort('blockedbyclient'));
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  await page.goto(`/dashboard/develop/${dataset}`, { waitUntil: 'domcontentloaded' });
  await expect(page.locator(`.ag-center-cols-container .ag-row [col-id="${column.id}"]`)).toHaveText([allowed, allowed, allowed], { timeout: UI_READY });
  await page.getByRole('button', { name: 'More actions', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Schema', exact: true }).click();
  const declaration = page.waitForResponse(response => new URL(response.url()).pathname === schemaPath
    && response.request().method() === 'PUT', { timeout: UI_READY });
  await page.getByRole('button', { name: 'Declare', exact: true }).click();
  expect((await declaration).status()).toBe(200);
  const definition = { type: 'object', required: [column.name], properties: { [column.name]: { type: 'string', enum: [allowed] } }, additionalProperties: false };
  const previewDefinition = { ...definition, properties: { [column.name]: { type: 'string', enum: [`${name}-mismatch`] } } };
  await page.getByRole('textbox', { name: 'Declared schema', exact: true }).fill(JSON.stringify(previewDefinition));
  const previewSaved = page.waitForResponse(response => new URL(response.url()).pathname === schemaPath
    && response.request().method() === 'PUT', { timeout: UI_READY });
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  expect((await previewSaved).status()).toBe(200);
  await page.getByRole('button', { name: 'Preview mismatches', exact: true }).click();
  const previewEnabled = page.waitForResponse(response => new URL(response.url()).pathname === schemaPath
    && response.request().method() === 'PUT', { timeout: UI_READY });
  await page.getByRole('button', { name: 'Enable preview', exact: true }).click();
  expect((await previewEnabled).status()).toBe(200);
  await page.getByRole('button', { name: 'View all', exact: true }).click();
  const mismatches = page.getByRole('dialog', { name: 'Sampled rows that do not match', exact: true });
  await expect(mismatches.locator('pre')).toHaveText(Array(3).fill(JSON.stringify({ [column.name]: allowed }, null, 2)));
  await mismatches.getByRole('button', { name: 'Done', exact: true }).click();
  await page.getByRole('textbox', { name: 'Declared schema', exact: true }).fill(JSON.stringify(definition));
  const definitionSaved = page.waitForResponse(response => new URL(response.url()).pathname === schemaPath
    && response.request().method() === 'PUT', { timeout: UI_READY });
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  expect((await definitionSaved).status()).toBe(200);
  await page.getByRole('button', { name: "Reject edits and imports that don't match", exact: true }).click();
  const rejectionMode = page.waitForResponse(response => new URL(response.url()).pathname === schemaPath
    && response.request().method() === 'PUT', { timeout: UI_READY });
  await page.getByRole('button', { name: 'Reject from now on', exact: true }).click();
  expect((await rejectionMode).status()).toBe(200);
  const schema = (await a.api.get<{ result: Schema }>(schemaPath)).result;
  const expected = {
    declared: definition,
    enforce: process.env.F52_PERTURB === 'save' ? 'off' : 'reject', preset: 'inferred',
  };
  expect(schema).toEqual(expected);
  expect((await read()).result.dataset_config.schema).toEqual(expected);
  expect(await probe.pg<{ organization_id: string; workspace_id: string; schema: Schema }>(
    "SELECT organization_id, workspace_id, dataset_config->'schema' AS schema FROM model_hub_dataset WHERE id=$1", [dataset]))
    .toEqual([{ organization_id: a.organizationId, workspace_id: a.workspaceId, schema: expected }]);
  await page.reload({ waitUntil: 'domcontentloaded' });
  // More actions keeps the row state it opened with, so open it only after the rows render.
  await expect(page.locator(`.ag-center-cols-container .ag-row [col-id="${column.id}"]`)).toHaveText([allowed, allowed, allowed], { timeout: UI_READY });
  await page.getByRole('button', { name: 'More actions', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Schema', exact: true }).click();
  await expect(page.getByRole('button', { name: "Reject edits and imports that don't match", exact: true }))
    .toHaveAttribute('aria-pressed', 'true', { timeout: UI_READY });
  // Three independent tables make an import-created column or early valid row observable.
  const stored = async () => ({
    rows: await probe.pg('SELECT * FROM model_hub_row WHERE dataset_id=$1 ORDER BY id', [dataset]),
    cells: await probe.pg('SELECT * FROM model_hub_cell WHERE dataset_id=$1 ORDER BY id', [dataset]),
    columns: await probe.pg('SELECT * FROM model_hub_column WHERE dataset_id=$1 ORDER BY id', [dataset]),
  });
  const before = await stored();
  await page.getByRole('button', { name: 'Close', exact: true }).click();
  // common.js uses agLargeTextCellEditor; losing focus commits the real request.
  await page.locator(`.ag-row[row-id="${rowIds[0]}"] [col-id="${column.id}"]`).dblclick();
  await page.locator('.ag-popup-editor textarea').fill(`${name}-invalid`);
  const rejectedEdit = page.waitForResponse(response => new URL(response.url()).pathname === editPath
    && response.request().method() === 'POST', { timeout: UI_READY });
  await page.locator(`.ag-header-cell[col-id="${column.id}"]`).click();
  expect((await rejectedEdit).status()).toBe(400);
  expect(await stored()).toEqual(before);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.locator(`.ag-center-cols-container .ag-row [col-id="${column.id}"]`)).toHaveText([allowed, allowed, allowed], { timeout: UI_READY });
  const invalidEdit = await scopes.send(a, 'POST', editPath,
    { row_id: rowIds[0], column_id: column.id, new_value: `${name}-invalid` });
  expect(invalidEdit.status).toBe(process.env.F52_PERTURB === 'write' ? 200 : 400);
  expect(await stored()).toEqual(before);
  const batch = (values: string[]) => ({ rows: values.map(value => ({ cells: [{ column_name: column.name, value }] })) });
  expect((await scopes.send(a, 'POST', addPath, batch([allowed, `${name}-invalid`]))).status).toBe(400);
  expect(await stored()).toEqual(before);
  const upload = (csv: string) => context.request.post(`${E2E.apiUrl}${IMPORT}`, {
    headers: scopes.headers(a), maxRedirects: 0,
    multipart: { dataset_id: dataset, file: { name: 'schema.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) } },
  });
  expect((await upload(`${column.name},new_column\n${allowed},first\n${name}-invalid,second\n`)).status()).toBe(400);
  expect(await stored()).toEqual(before);
  expect((await scopes.send(a, 'POST', editPath, { row_id: rowIds[0], column_id: column.id, new_value: allowed })).status).toBe(200);
  expect((await scopes.send(a, 'POST', addPath, batch([allowed, allowed]))).status).toBe(200);
  expect((await upload(`${column.name}\n${allowed}\n`)).status()).toBe(200);
  const finalRows = (await read()).result.table;
  expect(finalRows.map(row => (row[column.id] as { cell_value: string }).cell_value)).toEqual(Array(6).fill(allowed));
  const ownedState = await stored();
  for (const actor of [b, scopes.withWorkspace(a, scopes.emptyWorkspace.id), scopes.viewer]) {
    const response = await scopes.send(actor, 'PUT', schemaPath, { ...schema, enforce: 'off' });
    expect(process.env.F52_PERTURB === 'scope' ? [200] : [403, 404]).toContain(response.status);
    const edited = await scopes.send(actor, 'POST', editPath, { row_id: rowIds[0], column_id: column.id, new_value: allowed });
    expect([400, 403, 404]).toContain(edited.status);
    // AddDataRowsView refuses an id its own lookup cannot resolve (its catch-all currently answers 500);
    // the viewer resolves the dataset, so the declared schema's write scope refuses it.
    const added = await scopes.send(actor, 'POST', addPath, batch([allowed]));
    expect(actor === scopes.viewer ? [403] : [404, 500]).toContain(added.status);
  }
  expect((await scopes.send(b, 'GET', schemaPath)).status).toBe(404);
  expect((await b.api.get<{ result: { declared: unknown } }>(`/model-hub/datasets/${foreign}/schema/`)).result.declared).toEqual({});
  expect((await a.api.get<{ result: Schema }>(schemaPath)).result).toEqual(schema);
  expect(await stored()).toEqual(ownedState);
  await testInfo.attach('schema-readback', { contentType: 'application/json', body: JSON.stringify({ schema, finalRows, rowIds }) });
  await context.close();
});
