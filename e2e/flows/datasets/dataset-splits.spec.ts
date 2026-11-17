import fs from 'node:fs/promises';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// ManualDatasetCreateRequestSerializer and DatasetUpdateCellValueRequestSerializer.
const CREATE = '/model-hub/develops/create-dataset-manually/';
// SplitFilterButton, AssignSplitsMenu, RowHeaderPills and TrainingSetSections.
const UI_READY = 60_000;
type Column = { id: string; name: string };
type Row = { row_id: string; row_meta?: { split_ids?: string[] }; [key: string]: unknown };
type Table = { result: { table: Row[]; column_config: Column[] } };
type Split = { id: string; name: string; row_count: number };

test('DATA-E2E-008: persist row splits and export current training examples', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-008', area: 'datasets',
    userGoal: 'Organize current rows into persisted splits, filter them, and download a training set without requiring dataset versions',
    steps: [
      'seed distinct datasets in two synthetic organizations with unique input and expected-answer values',
      'create a train split in Manage splits and assign one row through the row drawer',
      'reload and filter to that split, requiring only the assigned row',
      'map input and expected-answer columns and download the current training set',
      'refuse cross-tenant and wrong-workspace membership writes and reads without changing stored membership',
      'replace persisted membership and reopen training export without reloading the page',
    ],
    backendChecks: [
      'created split and membership are stored against the owner dataset and row in PostgreSQL',
      'real table filtering and row-membership API readback contain exactly the selected row and split',
      'downloaded JSONL contains exactly the seeded user and assistant values and makes no version request',
      'foreign-tenant and wrong-workspace operations are refused and the original membership is unchanged',
      'reopened training export rereads current membership and downloads only the replacement row',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Eleven UI_READY budgets plus synchronous seed and database checks.
  test.setTimeout(720_000);
  const a = scopes.ownerA;
  const b = scopes.ownerB;
  const name = `e2e-split8-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const dataset = (await a.api.post<{ result: { dataset_id: string } }>(CREATE,
    { dataset_name: name, number_of_rows: 2, number_of_columns: 2 })).result.dataset_id;
  const foreign = (await b.api.post<{ result: { dataset_id: string } }>(CREATE,
    { dataset_name: `${name}-foreign`, number_of_rows: 1, number_of_columns: 1 })).result.dataset_id;
  const tablePath = `/model-hub/develops/${dataset}/get-dataset-table/`;
  const splitsPath = `/model-hub/datasets/${dataset}/splits/`;
  const initial = (await a.api.get<Table>(tablePath)).result;
  const [input, expected] = initial.column_config;
  const rowIds = initial.table.map(row => row.row_id);
  const values = [`${name}-input`, `${name}-expected`];
  for (const [i, column] of [input, expected].entries()) {
    await a.api.post(`/model-hub/develops/${dataset}/update_cell_value/`,
      { row_id: rowIds[0], column_id: column.id, new_value: values[i] });
    await a.api.post(`/model-hub/develops/${dataset}/update_cell_value/`,
      { row_id: rowIds[1], column_id: column.id, new_value: `${values[i]}-excluded` });
  }
  await testInfo.attach('split-identities', { contentType: 'application/json', body: JSON.stringify({
    dataset, foreign, rowIds, input, expected, values, organizationId: a.organizationId, workspaceId: a.workspaceId,
  }) });
  const context = await scopes.openContext(browser, a);
  await context.route('**/*', route => ['localhost', '127.0.0.1'].includes(new URL(route.request().url()).hostname)
    ? route.continue() : route.abort('blockedbyclient'));
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  const versions: string[] = [];
  page.on('request', req => { if (/\/versions\//.test(new URL(req.url()).pathname)) versions.push(req.url()); });
  await page.goto(`/dashboard/develop/${dataset}`, { waitUntil: 'domcontentloaded' });
  await expect(page.locator(`.ag-center-cols-container .ag-row [col-id="${input.id}"]`)).toHaveText([values[0], `${values[0]}-excluded`], { timeout: UI_READY });
  await page.getByRole('button', { name: 'All splits', exact: true }).click();
  await page.getByRole('button', { name: 'Manage splits…', exact: true }).click();
  await page.getByPlaceholder('New split name, e.g. edge-cases').fill('train');
  const createdResponse = page.waitForResponse(response => new URL(response.url()).pathname === splitsPath
    && response.request().method() === 'POST', { timeout: UI_READY });
  await page.getByRole('button', { name: 'Create split', exact: true }).click();
  const created = await createdResponse;
  expect(created.status()).toBe(200);
  const split = (await created.json()).result as Split;
  await testInfo.attach('created-split', { contentType: 'application/json', body: JSON.stringify(split) });
  await page.getByRole('button', { name: 'Done', exact: true }).click();
  await page.goto(`/dashboard/develop/${dataset}?row=${rowIds[0]}`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Assign splits', exact: true }).click();
  await expect(page.getByTestId('split-menu-subtitle')).toHaveText('Changes are saved immediately');
  const saved = page.waitForResponse(response => new URL(response.url()).pathname === `${splitsPath}membership/`
    && response.request().method() === 'POST', { timeout: UI_READY });
  await page.getByRole('menuitem').filter({ hasText: /^train/ }).click();
  expect((await saved).status()).toBe(200);
  expect(await probe.pg<{ dataset_id: string; row_id: string; name: string }>(
    'SELECT s.dataset_id, m.row_id, s.name FROM model_hub_datasetsplit s JOIN model_hub_datasetsplitmembership m ON m.split_id=s.id WHERE s.id=$1', [split.id]))
    .toEqual([{ dataset_id: dataset, row_id: rowIds[0], name: 'train' }]);
  const membership = () => a.api.get<{ result: { row_id: string; split_ids: string[] } }>(`${splitsPath}membership/`, { row_id: rowIds[0] });
  expect((await membership()).result).toEqual({ row_id: rowIds[0], split_ids: [split.id] });
  await page.goto(`/dashboard/develop/${dataset}?splits=train`, { waitUntil: 'domcontentloaded' });
  await expect(page.locator(`.ag-center-cols-container .ag-row [col-id="${input.id}"]`)).toHaveText([values[0]], { timeout: UI_READY });
  const filters = [{ column_id: 'system:splits', filter_config: { filter_type: 'categorical', filter_op: 'in', filter_value: [split.id] } }];
  const filtered = (await a.api.get<Table>(tablePath, { filters: JSON.stringify(filters) })).result.table;
  expect(filtered.map(row => row.row_id)).toEqual([rowIds[0]]);
  expect(filtered[0].row_meta?.split_ids).toEqual([split.id]);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.locator(`.ag-center-cols-container .ag-row [col-id="${input.id}"]`)).toHaveText([values[0]], { timeout: UI_READY });
  await page.getByRole('button', { name: 'train', exact: true }).click();
  await page.getByRole('button', { name: 'Manage splits…', exact: true }).click();
  await page.getByTestId('training-set-open').click();
  const dialog = page.getByTestId('training-set-dialog');
  await expect(dialog.getByText('Current saved rows', { exact: true })).toBeVisible();
  await expect(dialog.getByTestId('training-set-version')).toHaveCount(0);
  // MessagesSection permits explicit mappings when manually-created columns have no roles.
  const edit = dialog.getByTestId('training-set-roles-edit');
  if (await edit.isVisible()) await edit.click();
  await dialog.getByRole('combobox', { name: 'User message', exact: true }).click();
  await page.getByRole('option', { name: input.name, exact: true }).click();
  await dialog.getByRole('combobox', { name: 'Assistant reply', exact: true }).click();
  await page.getByRole('option', { name: expected.name, exact: true }).click();
  await expect(dialog.getByTestId('training-set-download')).toBeEnabled({ timeout: UI_READY });
  const downloaded = page.waitForEvent('download');
  await dialog.getByTestId('training-set-download').click();
  const file = await downloaded;
  const content = await fs.readFile((await file.path())!, 'utf8');
  expect(content.trim().split('\n').map(line => JSON.parse(line))).toEqual([
    { messages: [{ role: 'user', content: values[0] }, { role: 'assistant', content: values[1] }] },
  ]);
  await testInfo.attach('training-jsonl', { body: content, contentType: 'application/x-ndjson' });
  expect(versions).toEqual([]);
  const ownForeign = await b.api.post<{ result: Split }>(`/model-hub/datasets/${foreign}/splits/`, { name: 'foreign' });
  expect(ownForeign.result.name).toBe('foreign');
  for (const actor of [b, scopes.withWorkspace(a, scopes.emptyWorkspace.id)]) {
    for (const [method, target, body] of [
      ['GET', splitsPath, undefined],
      ['POST', `${splitsPath}membership/`, { row_ids: [rowIds[0]], add: [split.id], remove: [] }],
      ['DELETE', `${splitsPath}${split.id}/`, undefined],
    ] as const) {
      const response = await scopes.send(actor, method, target, body);
      expect([403, 404]).toContain(response.status);
    }
  }
  expect((await membership()).result.split_ids).toEqual([split.id]);
  // Keep the SPA/query client alive: a second export must not reuse removed rows.
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await a.api.post(`${splitsPath}membership/`, { row_ids: [rowIds[0]], add: [], remove: [split.id] });
  await a.api.post(`${splitsPath}membership/`, { row_ids: [rowIds[1]], add: [split.id], remove: [] });
  expect((await membership()).result.split_ids).toEqual([]);
  expect(await probe.pg<{ row_id: string }>(
    'SELECT row_id FROM model_hub_datasetsplitmembership WHERE split_id=$1', [split.id]))
    .toEqual([{ row_id: rowIds[1] }]);
  const refreshed = page.waitForResponse(response => new URL(response.url()).pathname === tablePath
    && response.request().method() === 'GET', { timeout: UI_READY });
  await page.getByTestId('training-set-open').click();
  expect((await refreshed).status()).toBe(200);
  if (await edit.isVisible()) await edit.click();
  await dialog.getByRole('combobox', { name: 'User message', exact: true }).click();
  await page.getByRole('option', { name: input.name, exact: true }).click();
  await dialog.getByRole('combobox', { name: 'Assistant reply', exact: true }).click();
  await page.getByRole('option', { name: expected.name, exact: true }).click();
  await expect(dialog.getByTestId('training-set-download')).toBeEnabled({ timeout: UI_READY });
  const replacementDownload = page.waitForEvent('download');
  await dialog.getByTestId('training-set-download').click();
  const replacementFile = await replacementDownload;
  const replacementContent = await fs.readFile((await replacementFile.path())!, 'utf8');
  expect(replacementContent.trim().split('\n').map(line => JSON.parse(line))).toEqual([
    { messages: [{ role: 'user', content: `${values[0]}-excluded` }, { role: 'assistant', content: `${values[1]}-excluded` }] },
  ]);
  await testInfo.attach('refreshed-training-jsonl', { body: replacementContent, contentType: 'application/x-ndjson' });
  expect(versions).toEqual([]);
  await context.close();
});
