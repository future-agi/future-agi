import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { POLL } from '../../lib/state-probe';

// ManualDatasetCreateRequestSerializer, ExtractJsonColumnRequestSerializer,
// DatasetUpdateCellValueRequestSerializer and RerunOperationRequestSerializer.
const CREATE = '/model-hub/develops/create-dataset-manually/';
// The live grid header's accessible menu name, columnActionMenuItems and RerunScopeField.
const UI_READY = 60_000;
type Cell = { cell_value: string | null; status: string };
type Row = { row_id: string; [key: string]: unknown };
type Table = { result: { column_config: { id: string; name: string }[]; table: Row[] } };
type Stored = { row_id: string; value: string | null; value_infos: unknown; status: string; updated_at: Date };

test('DATA-E2E-009: rerun only failed or changed generated cells without rewriting other rows', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-009', area: 'datasets',
    userGoal: 'Repair failed generated cells or refresh changed inputs while preserving every other output',
    steps: [
      'create two isolated datasets and generate JSON extraction outputs with one invalid input',
      'repair that input, open Configure Dynamic Column, choose Only failed cells and Save',
      'change a different input, choose Only rows whose inputs changed and Save',
      'save the changed scope again when no inputs changed',
    ],
    backendChecks: [
      'generated outputs and input fingerprints persist under the owning dataset and match API readback',
      'failed and changed Save requests contain the selected scope and leave unselected output values, metadata and timestamps unchanged',
      'a no-change rerun performs no output writes and foreign-tenant and wrong-workspace reruns are refused without writes',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Four ASYNC_JOB waits (240s) plus eight UI_READY actions (480s) and setup (60s).
  test.setTimeout(780_000);
  const a = scopes.ownerA;
  const b = scopes.ownerB;
  const name = `e2e-data9-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const dataset = (await a.api.post<{ result: { dataset_id: string } }>(CREATE,
    { dataset_name: name, number_of_rows: 2, number_of_columns: 1 })).result.dataset_id;
  const foreign = (await b.api.post<{ result: { dataset_id: string } }>(CREATE,
    { dataset_name: `${name}-foreign`, number_of_rows: 1, number_of_columns: 1 })).result.dataset_id;
  const readPath = `/model-hub/develops/${dataset}/get-dataset-table/`;
  const read = async () => (await a.api.get<Table>(readPath,
    { page_size: 500, current_page_index: 0, filters: '[]' })).result;
  const initial = await read();
  const input = initial.column_config[0];
  const rowIds = initial.table.map(row => row.row_id);
  expect(rowIds).toHaveLength(2);
  const write = (index: number, value: string) => a.api.post(`/model-hub/develops/${dataset}/update_cell_value/`,
    { row_id: rowIds[index], column_id: input.id, new_value: value });
  await write(0, 'not-json');
  await write(1, JSON.stringify({ answer: `${name}-stable` }));
  const outputName = `${name}-output`;
  const output = (await a.api.post<{ result: { new_column_id: string } }>(
    `/model-hub/develops/${dataset}/extract-json-column/`,
    { column_id: input.id, json_key: 'answer', new_column_name: outputName, concurrency: 1 })).result.new_column_id;
  const rerunPath = `/model-hub/columns/${output}/rerun-operation/`;
  const cells = async () => (await read()).table.map(row => row[output] as Cell);
  const stored = () => probe.pg<Stored>(
    'SELECT row_id, value, value_infos, status, updated_at FROM model_hub_cell WHERE dataset_id=$1 AND column_id=$2 AND NOT deleted ORDER BY row_id',
    [dataset, output]);
  const row = (items: Stored[], index: number) => items.find(item => item.row_id === rowIds[index]);
  await testInfo.attach('selective-rerun-identities', { contentType: 'application/json', body: JSON.stringify({
    dataset, foreign, input, output, outputName, rowIds, organizationId: a.organizationId, workspaceId: a.workspaceId,
  }) });
  await expect.poll(async () => (await cells()).map(cell => cell?.status), POLL.ASYNC_JOB).toEqual(['error', 'pass']);
  expect(await probe.pg('SELECT organization_id, workspace_id FROM model_hub_dataset WHERE id=$1', [dataset]))
    .toEqual([{ organization_id: a.organizationId, workspace_id: a.workspaceId }]);
  await expect.poll(async () => (await probe.pg<{ row_id: string }>(
    'SELECT row_id FROM model_hub_generatedinputsnapshot WHERE column_id=$1 ORDER BY row_id', [output])).map(item => item.row_id),
    POLL.ASYNC_JOB).toEqual([...rowIds].sort());

  const context = await scopes.openContext(browser, a);
  await context.route('**/*', route => ['localhost', '127.0.0.1'].includes(new URL(route.request().url()).hostname)
    ? route.continue() : route.abort('blockedbyclient'));
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  const save = async (scope: 'failed' | 'changed') => {
    await page.goto(`/dashboard/develop/${dataset}`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: `${outputName} column menu`, exact: true }).click();
    await page.getByRole('menuitem', { name: 'Configure Dynamic Column', exact: true }).click();
    await page.getByRole('radio', { name: scope === 'failed' ? 'Only failed cells' : 'Only rows whose inputs changed', exact: true }).check();
    const [saved] = await Promise.all([
      page.waitForResponse(res => new URL(res.url()).pathname === rerunPath && res.request().method() === 'POST'),
      page.getByRole('button', { name: 'Update Column', exact: true }).click(),
    ]);
    expect(saved.status()).toBe(200);
    expect(saved.request().postDataJSON().scope).toBe(scope);
    await expect(page.getByRole('radiogroup', { name: 'Rows to re-run on save' })).toHaveCount(0);
  };

  const beforeFailed = await stored();
  await write(0, JSON.stringify({ answer: `${name}-repaired` }));
  await save('failed');
  await expect.poll(async () => (await cells()).map(cell => [cell?.cell_value, cell?.status]), POLL.ASYNC_JOB)
    .toEqual([[`${name}-repaired`, 'pass'], [`${name}-stable`, 'pass']]);
  await expect(page.locator(`.ag-center-cols-container .ag-row [col-id="${output}"]`))
    .toHaveText([`${name}-repaired`, `${name}-stable`], { timeout: UI_READY });
  const afterFailed = await stored();
  expect(row(afterFailed, 1)).toEqual(row(beforeFailed, 1));

  await write(1, JSON.stringify({ answer: `${name}-changed` }));
  await save('changed');
  await expect.poll(async () => (await cells()).map(cell => [cell?.cell_value, cell?.status]), POLL.ASYNC_JOB)
    .toEqual([[`${name}-repaired`, 'pass'], [`${name}-changed`, 'pass']]);
  await expect(page.locator(`.ag-center-cols-container .ag-row [col-id="${output}"]`))
    .toHaveText([`${name}-repaired`, `${name}-changed`], { timeout: UI_READY });
  const afterChanged = await stored();
  expect(row(afterChanged, 0)).toEqual(row(afterFailed, 0));
  await save('changed');
  expect(await stored()).toEqual(afterChanged);
  for (const actor of [b, scopes.withWorkspace(a, scopes.emptyWorkspace.id)]) {
    const denied = await scopes.send(actor, 'POST', rerunPath, { operation_type: 'extract_json', scope: 'all' });
    expect([403, 404]).toContain(denied.status);
    expect(await stored()).toEqual(afterChanged);
  }
  expect((await b.api.get<Table>(`/model-hub/develops/${foreign}/get-dataset-table/`)).result.table).toHaveLength(1);
  await testInfo.attach('persisted-selective-results', { contentType: 'application/json', body: JSON.stringify({
    beforeFailed, afterFailed, afterChanged,
  }) });
  await context.close();
});
