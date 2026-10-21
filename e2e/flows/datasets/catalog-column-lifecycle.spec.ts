import { test, expect } from '../../lib/fixtures';
import { uploadFixture } from '../../lib/upload';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// UploadFileModal.jsx / CreateDatasetFromLocalFileRequestSerializer.
const UPLOAD = '/model-hub/develops/create-dataset-from-local-file/';
// useDashboards.js and DashboardViewSet._filter_values_dataset_column.
const VALUES = '/tracer/dashboard/filter_values/';
const METRICS = '/tracer/dashboard/metrics/';
// DeleteDataset.jsx sends {dataset_ids:[id]} through the native confirmation.
const DELETE_DATASET = '/model-hub/develops/delete_dataset/';
const UI_READY = 60_000;
type Cell = { id: string; row_id: string; value: string };
type ValuePage = { result: { values: { value: string | number }[] } };
type MetricPage = { result: { metrics: { property_id: string; name: string; display_name: string }[] } };

test('DATA-E2E-002: dataset discovery follows cell edits and column deletion', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-002', area: 'datasets',
    userGoal: 'Edit dataset cells and columns and discover only their current authorized values without manual catalog repair',
    steps: ['upload a synthetic CSV through the dataset form',
      'edit a cell and rename its column through the table',
      'inspect the renamed property and select its new value',
      'delete the column through its menu',
      'delete the synthetic dataset through Configure and check discovery again'],
    backendChecks: ['scoped cell and column edits preserve their exact immutable identities',
      'latest edited ClickHouse cells match the exact Postgres identities and values',
      'current property label, value choices and filtered UI/API rows agree after editing',
      'removed columns and datasets cannot be rediscovered through metadata or value requests'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // ASYNC_JOB 60 + CDC_VISIBLE 180 + 6 UI_READY 360 + 60 navigation headroom.
  test.setTimeout(660_000);
  page.setDefaultTimeout(UI_READY);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const datasetName = `e2e-data2-${suffix}`;
  const columnName = `e2e-data2-region-${suffix}`;
  const newValue = `catalog-north-${suffix}`;

  await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Add Dataset', exact: true }).first().click();
  await page.getByText('Upload a file (JSON, CSV)', { exact: true }).click();
  const uploadDialog = page.getByRole('dialog').filter({ has: page.getByText('Upload a File', { exact: true }) });
  await uploadFixture(uploadDialog.locator('input[type="file"]'), 'catalog-regions.csv');
  await uploadDialog.getByPlaceholder('Dataset Name').fill(datasetName);
  const uploaded = page.waitForResponse(r => new URL(r.url()).pathname === UPLOAD
    && r.request().method() === 'POST', { timeout: UI_READY });
  await uploadDialog.getByRole('button', { name: 'Upload', exact: true }).click();
  const uploadResponse = await uploaded;
  expect(uploadResponse.status()).toBe(200);
  const datasetId: string = (await uploadResponse.json()).result.dataset_id;
  await testInfo.attach('uploaded-dataset', { contentType: 'application/json',
    body: JSON.stringify({ datasetId, datasetName, columnName, newValue,
      organizationId: actor.organizationId, workspaceId: actor.workspaceId }) });
  await expect.poll(async () => (await probe.pg<{ n: string }>(
    'SELECT count(*) AS n FROM model_hub_cell WHERE dataset_id = $1 AND NOT deleted',
    [datasetId]))[0].n, POLL.ASYNC_JOB).toBe('6');
  const columns = await probe.pg<{ id: string; name: string; data_type: string }>(
    'SELECT id, name, data_type FROM model_hub_column WHERE dataset_id = $1 AND NOT deleted ORDER BY name', [datasetId]);
  expect(columns.map(c => ({ name: c.name, data_type: c.data_type })))
    .toEqual([{ name: 'quantity', data_type: 'integer' }, { name: 'region', data_type: 'text' }]);
  const columnId = columns.find(c => c.name === 'region')!.id;
  const quantityId = columns.find(c => c.name === 'quantity')!.id;
  const beforeCells = await probe.pg<Cell>(
    'SELECT id, row_id, value FROM model_hub_cell WHERE dataset_id = $1 AND column_id = $2 AND NOT deleted ORDER BY row_id::text',
    [datasetId, columnId]);
  expect(beforeCells.map(c => c.value).sort()).toEqual(['catalog-east', 'catalog-west', 'catalog-west']);
  const editedCell = beforeCells.find(c => c.value === 'catalog-east')!;
  const expectedCells = beforeCells.map(c => ({ ...c, value: c.id === editedCell.id ? newValue : c.value }));
  await testInfo.attach('column-cell-identities', { contentType: 'application/json',
    body: JSON.stringify({ columns, beforeCells, editedCell, expectedCells }) });

  await expect(page).toHaveURL(new RegExp(`/dashboard/develop/${datasetId}`), { timeout: UI_READY });
  const regionCells = page.locator(`.ag-row [col-id="${columnId}"]`);
  await expect(regionCells).toHaveText(['catalog-west', 'catalog-east', 'catalog-west'], { timeout: UI_READY });
  // DevelopDataV2 getRowId uses row_id; common.js uses agLargeTextCellEditor
  // and update_cell_value POST {column_id,row_id,new_value} on committed edits.
  await page.locator(`.ag-row[row-id="${editedCell.row_id}"] [col-id="${columnId}"]`).dblclick();
  await page.locator('.ag-popup-editor textarea').fill(newValue);
  const edited = page.waitForResponse(r => new URL(r.url()).pathname ===
    `/model-hub/develops/${datasetId}/update_cell_value/` && r.request().method() === 'POST', { timeout: UI_READY });
  // DevelopDataV2 sets stopEditingWhenCellsLoseFocus; clicking the header commits.
  await page.locator(`.ag-header-cell[col-id="${columnId}"]`).click();
  const editResponse = await edited;
  expect(editResponse.status()).toBe(200);
  expect(editResponse.request().postDataJSON()).toEqual({ column_id: columnId, row_id: editedCell.row_id, new_value: newValue });

  // EditColumnName.jsx / common.js menu: preserve the ID through a native rename.
  await page.locator(`.ag-header-cell[col-id="${columnId}"]`).getByRole('button').click();
  await page.getByRole('menuitem', { name: 'Edit Column Name', exact: true }).click();
  const renameDialog = page.getByRole('dialog').filter({ has: page.getByText('Edit Column Name', { exact: true }) });
  await renameDialog.getByPlaceholder('Enter column name').fill(columnName);
  const renamed = page.waitForResponse(r => new URL(r.url()).pathname ===
    `/model-hub/develops/${datasetId}/update_column_name/${columnId}/` && r.request().method() === 'PUT', { timeout: UI_READY });
  await renameDialog.getByRole('button', { name: 'Save', exact: true }).click();
  expect((await renamed).status()).toBe(200);

  await test.step('backend check 1: scoped edits preserve identities', async () => {
    expect(await probe.pg<{ name: string; organization_id: string; workspace_id: string }>(
      'SELECT name, organization_id, workspace_id FROM model_hub_dataset WHERE id = $1 AND NOT deleted', [datasetId]))
      .toEqual([{ name: datasetName, organization_id: actor.organizationId, workspace_id: actor.workspaceId }]);
    expect(await probe.pg<{ id: string; dataset_id: string; name: string; data_type: string }>(
      'SELECT id, dataset_id, name, data_type FROM model_hub_column WHERE id = $1 AND NOT deleted', [columnId]))
      .toEqual([{ id: columnId, dataset_id: datasetId, name: columnName, data_type: 'text' }]);
    await expect.poll(() => probe.pg<Cell>(
      'SELECT id, row_id, value FROM model_hub_cell WHERE dataset_id = $1 AND column_id = $2 AND NOT deleted ORDER BY row_id::text',
      [datasetId, columnId]), POLL.ASYNC_JOB).toEqual(expectedCells);
  });
  await test.step('backend check 2: latest edited cells reach ClickHouse', async () => {
    await expect.poll(() => probe.ch<Cell>(
      `SELECT id, row_id, value FROM model_hub_cell FINAL
       WHERE dataset_id = {d:UUID} AND column_id = {c:UUID} AND NOT deleted AND _peerdb_is_deleted = 0
       ORDER BY toString(row_id)`, { d: datasetId, c: columnId }), POLL.CDC_VISIBLE).toEqual(expectedCells);
  });

  const valueParams = { property_id: `dataset_column:${columnId}`, metric_name: columnId,
    metric_type: 'custom_column', dataset_id: datasetId, source: 'dataset_column', page_size: 50 };
  // DashboardMetricsCatalogQuerySerializer + source_adapters: text columns are
  // filter candidates, not numeric metrics. Search this run's unique label.
  const catalogParams = { source: 'datasets', category: 'custom_column', role: 'dimension',
    search: columnName, cursor_mode: true, page_size: 25 };
  await test.step('backend check 3: current discovery and filtered rows agree', async () => {
    const catalog = await actor.api.post<MetricPage>(METRICS, catalogParams);
    expect(catalog.result.metrics.map(m => ({ id: m.property_id, name: m.name, label: m.display_name })))
      .toEqual([{ id: `dataset_column:${columnId}`, name: columnId, label: columnName }]);
    const values = await actor.api.post<ValuePage>(VALUES, valueParams);
    expect(values.result.values.map(v => v.value).sort()).toEqual([newValue, 'catalog-west'].sort());
    await testInfo.attach('current-discovery', { body: JSON.stringify({ catalog, values }), contentType: 'application/json' });
    await expect(regionCells).toHaveText(['catalog-west', newValue, 'catalog-west'], { timeout: UI_READY });
    await page.getByRole('button', { name: 'Filter', exact: true }).click();
    await page.getByRole('button', { name: 'Property', exact: true }).first().click();
    const property = page.locator(`[data-filter-property-option="${columnId}"]`);
    // The option also renders a dataset badge; assert its exact label child.
    await expect(property.getByText(columnName, { exact: true })).toBeVisible({ timeout: UI_READY });
    await property.click();
    await page.getByPlaceholder('Value', { exact: true }).click();
    await expect(page.getByRole('option')).toHaveText([newValue, 'catalog-west'].sort(), { timeout: UI_READY });
    const filtered = page.waitForResponse(r => r.url().includes(`/${datasetId}/get-dataset-table/`)
      && r.request().method() !== 'OPTIONS' && decodeURIComponent(r.url()).includes(newValue), { timeout: UI_READY });
    await page.getByRole('option', { name: newValue, exact: true }).click();
    await page.keyboard.press('Escape');
    const filteredResponse = await filtered;
    expect(filteredResponse.status()).toBe(200);
    const table = await filteredResponse.json();
    expect(table.result.table.map((r: { row_id: string }) => r.row_id)).toEqual([editedCell.row_id]);
    await expect(regionCells).toHaveText([newValue], { timeout: UI_READY });
    await testInfo.attach('filtered-edited-row', { body: JSON.stringify(table), contentType: 'application/json' });
  });

  await test.step('backend check 4: removed columns and parents stay undiscoverable', async () => {
    // Dataset filters are unsaved; reload resets them before testing removal.
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(regionCells).toHaveText(['catalog-west', newValue, 'catalog-west'], { timeout: UI_READY });
    await page.locator(`.ag-header-cell[col-id="${columnId}"]`).getByRole('button').click();
    await page.getByRole('menuitem', { name: 'Delete Column', exact: true }).click();
    const deleteColumnDialog = page.getByRole('dialog').filter({
      has: page.getByText('Are you sure you want to delete this column?', { exact: true }) });
    const removed = page.waitForResponse(r => new URL(r.url()).pathname ===
      `/model-hub/develops/${datasetId}/delete_column/${columnId}/` && r.request().method() === 'DELETE', { timeout: UI_READY });
    await deleteColumnDialog.getByRole('button', { name: 'Delete', exact: true }).click();
    expect((await removed).status()).toBe(200);
    expect(await probe.pg<{ deleted: boolean }>('SELECT deleted FROM model_hub_column WHERE id = $1', [columnId]))
      .toEqual([{ deleted: true }]);
    await expect(page.locator(`.ag-header-cell[col-id="${columnId}"]`)).toHaveCount(0, { timeout: UI_READY });
    expect((await actor.api.post<MetricPage>(METRICS, catalogParams)).result.metrics).toEqual([]);
    expect((await actor.api.post<ValuePage>(VALUES, valueParams)).result.values).toEqual([]);

    const remainingParams = { ...valueParams, property_id: `dataset_column:${quantityId}`, metric_name: quantityId };
    expect((await actor.api.post<ValuePage>(VALUES, remainingParams)).result.values.map(v => v.value).sort())
      .toEqual(['2', '3', '7']); // Native dataset values preserve serialized cell strings.
    // Source adapters explicitly support UUID search. A surviving-column witness
    // proves parent removal, independently of the region column already removed.
    const remainingCatalog = { ...catalogParams, role: 'metric', search: quantityId };
    expect((await actor.api.post<MetricPage>(METRICS, remainingCatalog)).result.metrics.map(m => m.property_id))
      .toEqual([`dataset_column:${quantityId}`]);
    await page.getByRole('button', { name: 'Configure', exact: true }).click();
    const configureDialog = page.getByRole('dialog').filter({ has: page.getByText('Configure Dataset', { exact: true }) });
    await configureDialog.getByRole('button', { name: 'Delete', exact: true }).click();
    const deleteDatasetDialog = page.getByRole('dialog').filter({ has: page.getByText('Delete Dataset', { exact: true }) });
    const parentRemoved = page.waitForResponse(r => new URL(r.url()).pathname === DELETE_DATASET
      && r.request().method() === 'DELETE', { timeout: UI_READY });
    await deleteDatasetDialog.getByRole('button', { name: 'Delete', exact: true }).click();
    const parentResponse = await parentRemoved;
    expect(parentResponse.status()).toBe(200);
    expect(parentResponse.request().postDataJSON()).toEqual({ dataset_ids: [datasetId] });
    expect(await probe.pg<{ deleted: boolean }>('SELECT deleted FROM model_hub_dataset WHERE id = $1', [datasetId]))
      .toEqual([{ deleted: true }]);
    await expect(page).toHaveURL(/\/dashboard\/develop\/?$/, { timeout: UI_READY });
    for (const params of [valueParams, remainingParams]) {
      expect((await actor.api.post<ValuePage>(VALUES, params)).result.values).toEqual([]);
    }
    expect((await actor.api.post<MetricPage>(METRICS, catalogParams)).result.metrics).toEqual([]);
    expect((await actor.api.post<MetricPage>(METRICS, remainingCatalog)).result.metrics).toEqual([]);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByText(datasetName, { exact: true })).toHaveCount(0, { timeout: UI_READY });
  });
});
