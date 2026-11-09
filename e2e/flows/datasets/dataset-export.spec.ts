import fs from 'node:fs/promises';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// ManualDatasetCreateRequestSerializer and DatasetUpdateCellValueRequestSerializer.
const CREATE = '/model-hub/develops/create-dataset-manually/';
// ExportDialog and DatasetListCells: existing download controls and row actions.
const DIALOG = 'dataset-export-dialog';
const UI_READY = 60_000;
type Column = { id: string; name: string };
type Row = { row_id: string; order: number; [key: string]: unknown };
type Table = { result: { column_config: Column[]; table: Row[] } };

test('DATA-E2E-005: download all current dataset rows from the grid and list', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-005', area: 'datasets',
    userGoal: 'Download all current dataset rows as CSV or JSON without exposing unbacked scopes or another tenant’s data',
    steps: [
      'create uniquely marked datasets in two organizations through public APIs',
      'open grid More and download every current row as CSV',
      'open the dataset list row menu and download every current row as JSON',
      'confirm version, split and filtered export choices are absent',
    ],
    backendChecks: [
      'stored dataset scope and API cell values exactly match the seeded identities and values',
      'browser downloads contain exactly every seeded row and value, and use unfiltered real table requests',
      'foreign organization and wrong-workspace exports are refused while each owner can read its own dataset',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Six UI_READY budgets (360s), plus synchronous setup/readbacks (60s).
  test.setTimeout(420_000);
  const a = scopes.ownerA;
  const b = scopes.ownerB;
  const name = `e2e-data5-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const values = [`${name}-first`, `${name}, quoted "second"\nnext line`, `${name}-last`];
  const dataset = (await a.api.post<{ result: { dataset_id: string } }>(CREATE,
    { dataset_name: name, number_of_rows: 3, number_of_columns: 1 })).result.dataset_id;
  const path = `/model-hub/develops/${dataset}/get-dataset-table/`;
  const read = () => a.api.get<Table>(path, { page_size: 500, current_page_index: 0, filters: '[]' });
  const initial = (await read()).result;
  const column = initial.column_config[0];
  const rowIds = initial.table.map(row => row.row_id);
  expect(rowIds).toHaveLength(3);
  for (const [i, row] of rowIds.entries()) {
    await a.api.post(`/model-hub/develops/${dataset}/update_cell_value/`,
      { row_id: row, column_id: column.id, new_value: values[i] });
  }
  const foreign = (await b.api.post<{ result: { dataset_id: string } }>(CREATE,
    { dataset_name: `${name}-foreign`, number_of_rows: 1, number_of_columns: 1 })).result.dataset_id;
  const foreignPath = `/model-hub/develops/${foreign}/get-dataset-table/`;
  await testInfo.attach('export-identities', { contentType: 'application/json', body: JSON.stringify({
    dataset, foreign, name, column, rowIds, values, organizationId: a.organizationId, workspaceId: a.workspaceId,
  }) });
  expect(await probe.pg<{ name: string; organization_id: string; workspace_id: string }>(
    'SELECT name, organization_id, workspace_id FROM model_hub_dataset WHERE id=$1 AND NOT deleted', [dataset]))
    .toEqual([{ name, organization_id: a.organizationId, workspace_id: a.workspaceId }]);
  const persisted = (await read()).result;
  expect(persisted.table.map(row => (row[column.id] as { cell_value: string }).cell_value)).toEqual(values);
  expect(persisted.table.map(row => row.row_id)).toEqual(rowIds);
  expect((await b.api.get<Table>(foreignPath)).result.table).toHaveLength(1);
  for (const [actor, target] of [[b, path], [a, foreignPath], [scopes.withWorkspace(a, scopes.emptyWorkspace.id), path]] as const) {
    const response = await scopes.send(actor, 'GET', target, undefined,
      { page_size: 500, current_page_index: 0, filters: '[]' });
    expect([400, 403, 404]).toContain(response.status);
    expect(JSON.stringify(response.body)).not.toContain(values[0]);
  }
  const context = await scopes.openContext(browser, a);
  await context.route('**/*', route => {
    const host = new URL(route.request().url()).hostname;
    return ['localhost', '127.0.0.1'].includes(host) ? route.continue() : route.abort('blockedbyclient');
  });
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  const unsupported: string[] = [];
  page.on('request', req => {
    if (/\/versions\//.test(new URL(req.url()).pathname)) unsupported.push(req.url());
  });
  await page.goto(`/dashboard/develop/${dataset}`, { waitUntil: 'domcontentloaded' });
  await expect(page.locator(`.ag-center-cols-container .ag-row [col-id="${column.id}"]`)).toHaveCount(3, { timeout: UI_READY });
  await page.getByRole('button', { name: 'More actions', exact: true }).click();
  const csvRequest = page.waitForResponse(response => new URL(response.url()).pathname === path
    && new URL(response.url()).searchParams.get('page_size') === '500', { timeout: UI_READY });
  await page.getByRole('menuitem', { name: 'Download…', exact: true }).click();
  const fetched = await csvRequest;
  expect(fetched.status()).toBe(200);
  const query = new URL(fetched.url()).searchParams;
  expect(query.get('filters')).toBe('[]');
  expect(query.has('search')).toBe(false);
  const dialog = page.getByTestId(DIALOG);
  await expect(dialog.getByRole('radio')).toHaveCount(2);
  await expect(dialog.getByText('All current rows', { exact: true })).toBeVisible();
  await expect(dialog.getByRole('combobox')).toHaveCount(0);
  const csvEvent = page.waitForEvent('download');
  await dialog.getByTestId('export-download').click();
  const csvDownload = await csvEvent;
  expect(csvDownload.suggestedFilename()).toMatch(/\.csv$/);
  const csv = await fs.readFile((await csvDownload.path())!, 'utf8');
  // RFC 4180 expectations are independent of the product writer implementation.
  const quote = (value: string) => /[",\r\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
  const expectedCsv = [`row_id,row,splits,${quote(column.name)}`,
    ...rowIds.map((id, i) => `${id},${persisted.table[i].order + 1},,${quote(values[i])}`)].join('\r\n') + '\r\n';
  expect(csv).toBe(expectedCsv);
  await testInfo.attach('downloaded-csv', { body: csv, contentType: 'text/csv' });

  await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: `${name} actions`, exact: true }).click();
  await page.getByRole('menuitem', { name: 'Download…', exact: true }).click();
  await expect(dialog.getByText('All current rows', { exact: true })).toBeVisible();
  await expect(dialog.getByRole('radio')).toHaveCount(2);
  await dialog.getByRole('radio', { name: /^JSON\s/ }).click();
  const jsonEvent = page.waitForEvent('download');
  await dialog.getByTestId('export-download').click();
  const jsonDownload = await jsonEvent;
  expect(jsonDownload.suggestedFilename()).toMatch(/\.json$/);
  const jsonText = await fs.readFile((await jsonDownload.path())!, 'utf8');
  const json = JSON.parse(jsonText);
  expect(json.meta.dataset_id).toBe(dataset);
  expect(json.meta.row_count).toBe(3);
  expect(json.rows.map((row: { row_id: string }) => row.row_id)).toEqual(rowIds);
  expect(json.rows.map((row: { fields: Record<string, string> }) => row.fields[column.name])).toEqual(values);
  expect(unsupported).toEqual([]);
  expect((await read()).result.table).toEqual(persisted.table);
  await testInfo.attach('downloaded-json', { body: jsonText, contentType: 'application/json' });
  await context.close();
});
