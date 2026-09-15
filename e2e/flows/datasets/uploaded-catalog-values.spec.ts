import { test, expect } from '../../lib/fixtures';
import { uploadFixture } from '../../lib/upload';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// UploadFileModal.jsx and CreateDatasetFromLocalFileRequestSerializer.
const UPLOAD_PATH = '/model-hub/develops/create-dataset-from-local-file/';
// useDatasetColumnValues / useDashboardFilterValues in useDashboards.js.
const VALUES_PATH = '/tracer/dashboard/filter_values/';
const UI_READY = 60_000;

test('DATA-E2E-001: uploaded dataset values can be discovered and filtered', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-001', area: 'datasets',
    userGoal: 'Upload a dataset and select a real column value to narrow its rows',
    steps: ['upload a synthetic CSV using the dataset form', 'wait for imported cells',
      'open the region value picker', 'select a region and verify its rows',
      'refresh, reselect the region and verify the same filtered result'],
    backendChecks: ['uploaded dataset, columns and cells belong to the actor scope',
      'imported region cells reach ClickHouse with their exact dataset, column and row IDs',
      'the dataset value endpoint returns exactly the two imported region choices',
      'selecting a region yields exactly the corresponding UI and API rows'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // ASYNC_JOB 60 + CDC_VISIBLE 180 + 4 × UI_READY 240 + upload/navigation 120.
  test.setTimeout(600_000);
  page.setDefaultTimeout(UI_READY);
  const name = `e2e-data1-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Add Dataset', exact: true }).first().click({ timeout: UI_READY });
  await page.getByText('Upload a file (JSON, CSV)', { exact: true }).click({ timeout: UI_READY });
  const dialog = page.getByRole('dialog').filter({ has: page.getByText('Upload a File', { exact: true }) });
  await uploadFixture(dialog.locator('input[type="file"]'), 'catalog-regions.csv');
  await dialog.getByPlaceholder('Dataset Name').fill(name);
  const uploaded = page.waitForResponse(r => r.url().endsWith(UPLOAD_PATH)
    && r.request().method() === 'POST', { timeout: UI_READY });
  await dialog.getByRole('button', { name: 'Upload', exact: true }).click();
  const response = await uploaded;
  expect(response.status()).toBe(200);
  const body = await response.json();
  const datasetId: string = body.result.dataset_id;
  await testInfo.attach('uploaded-dataset', {
    body: JSON.stringify({ datasetId, name, workspaceId: actor.workspaceId }),
    contentType: 'application/json',
  });

  await expect.poll(async () => (await probe.pg<{ n: string }>(
    'SELECT count(*) AS n FROM model_hub_cell WHERE dataset_id = $1 AND NOT deleted',
    [datasetId]))[0].n, POLL.ASYNC_JOB).toBe('6');
  const datasets = await probe.pg<{ name: string; organization_id: string; workspace_id: string }>(
    'SELECT name, organization_id, workspace_id FROM model_hub_dataset WHERE id = $1 AND NOT deleted',
    [datasetId]);
  expect(datasets).toEqual([{ name, organization_id: actor.organizationId, workspace_id: actor.workspaceId }]);
  const columns = await probe.pg<{ id: string; name: string; data_type: string }>(
    'SELECT id, name, data_type FROM model_hub_column WHERE dataset_id = $1 AND NOT deleted ORDER BY name',
    [datasetId]);
  expect(columns.map(({ name: columnName, data_type }) => ({ name: columnName, data_type })))
    .toEqual([{ name: 'quantity', data_type: 'integer' }, { name: 'region', data_type: 'text' }]);
  const columnId = columns.find(c => c.name === 'region')!.id;
  const cells = await probe.pg<{ row_id: string; value: string }>(
    'SELECT row_id, value FROM model_hub_cell WHERE dataset_id = $1 AND column_id = $2 AND NOT deleted ORDER BY row_id',
    [datasetId, columnId]);
  expect(cells.map(c => c.value).sort()).toEqual(['catalog-east', 'catalog-west', 'catalog-west']);
  await testInfo.attach('imported-columns-and-cells', {
    body: JSON.stringify({ columns, cells }), contentType: 'application/json',
  });
  await expect.poll(() => probe.ch<{ row_id: string; value: string }>(
    // ClickHouse UUID ordering differs from PostgreSQL; compare the same lexical ID order.
    'SELECT row_id, value FROM model_hub_cell FINAL WHERE dataset_id = {d:UUID} AND column_id = {c:UUID} AND NOT deleted AND _peerdb_is_deleted = 0 ORDER BY toString(row_id)',
    { d: datasetId, c: columnId }), POLL.CDC_VISIBLE).toEqual(cells);

  const valueParams = { property_id: `dataset_column:${columnId}`, metric_name: columnId,
    metric_type: 'custom_column', dataset_id: datasetId, source: 'dataset_column', page_size: 50 };
  const values = await actor.api.post<{ result: { values: { value: string }[] } }>(VALUES_PATH, valueParams);
  await testInfo.attach('dataset-value-page', { body: JSON.stringify(values), contentType: 'application/json' });
  expect(values.result.values.map(v => v.value).sort()).toEqual(['catalog-east', 'catalog-west']);

  await expect(page).toHaveURL(new RegExp(`/dashboard/develop/${datasetId}`), { timeout: UI_READY });
  const regionCells = page.locator(`.ag-row [col-id="${columnId}"]`);
  await expect(regionCells).toHaveText(['catalog-west', 'catalog-east', 'catalog-west'], { timeout: UI_READY });
  // Dataset filters are unsaved in-memory state. Repeat the picker journey after
  // reload; persistence is not part of this catalog-discovery contract.
  for (const pass of ['initial', 'after-reload']) {
    if (pass === 'after-reload') {
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(regionCells).toHaveText(['catalog-west', 'catalog-east', 'catalog-west'], { timeout: UI_READY });
    }
    await page.getByRole('button', { name: 'Filter', exact: true }).click();
    await page.getByRole('button', { name: 'Property', exact: true }).first().click();
    await page.locator(`[data-filter-property-option="${columnId}"]`).click();
    await page.getByPlaceholder('Value', { exact: true }).click();
    await expect(page.getByRole('option')).toHaveText(['catalog-east', 'catalog-west'], { timeout: UI_READY });
    // Capture the actual dataset-grid request instead of inventing the filter wire format.
    const filtered = page.waitForResponse(r => r.url().includes(`/${datasetId}/get-dataset-table/`)
      && r.request().method() !== 'OPTIONS'
      && decodeURIComponent(r.url()).includes('catalog-west'), { timeout: UI_READY });
    await page.getByRole('option', { name: 'catalog-west', exact: true }).click();
    await page.keyboard.press('Escape');
    const filterResponse = await filtered;
    expect(filterResponse.status()).toBe(200);
    const table = await filterResponse.json();
    await testInfo.attach(`filtered-dataset-table-${pass}`, { body: JSON.stringify(table), contentType: 'application/json' });
    expect(table.result.table.map((r: { row_id: string }) => r.row_id).sort())
      .toEqual(cells.filter(c => c.value === 'catalog-west').map(c => c.row_id).sort());
    await expect(regionCells).toHaveText(['catalog-west', 'catalog-west'], { timeout: UI_READY });
  }
});
