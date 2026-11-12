import { test, expect } from '../../lib/fixtures';
import { uploadFixture } from '../../lib/upload';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// develop/AddDatasetDrawer/UploadFileModal.jsx posts the strict
// CreateDatasetFromLocalFileRequest form here (the DATA-E2E-002 upload contract).
const UPLOAD = '/model-hub/develops/create-dataset-from-local-file/';
// DataTab/common.js onCellValueChangedWrapper: POST {column_id, row_id, new_value}.
const updatePath = (datasetId: string) => `/model-hub/develops/${datasetId}/update_cell_value/`;
// GetDatasetTableView (model_hub/views/develop_dataset.py process_row) reads
// Postgres; DatasetTableQuerySerializer accepts these paging params.
const tablePath = (datasetId: string) => `/model-hub/develops/${datasetId}/get-dataset-table/`;
const TABLE_PARAMS = { current_page_index: 0, page_size: 10 };
const UI_READY = 60_000;
// Repeated echo POSTs followed the previous response by 6-11 ms;
// holding the next editor open this long spans many round trips.
const HOLD_MS = 2_100;
type Payload = { column_id: string; row_id: string; new_value: string };
type TableRow = { row_id: string } & Record<string, unknown>;
type TablePage = { result: { table: TableRow[] } };

test('DATA-E2E-003: a committed dataset cell edit is written exactly once', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-003', area: 'datasets',
    userGoal: 'Edit a dataset cell, move on with Tab or Enter, and have each edit saved exactly once',
    steps: ['upload a synthetic CSV through New dataset > Upload a file',
      'double-click a text cell, type a new value and press Tab',
      'keep the next cell editor open after the save is acknowledged',
      'press Escape, then edit the same cell again and commit it with Enter',
      'reload the dataset and read the final value'],
    backendChecks: ['the uploaded dataset, its columns and the edited row belong to the actor scope',
      'each commit sends exactly one update_cell_value request with the exact payload, also while the editor Tab opened stays open',
      'get-dataset-table and Postgres hold the exact committed value for the edited row and column after each commit'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // ASYNC_JOB 60 + 2 × CDC_VISIBLE 360 + 8 × UI_READY 480 = 900.
  test.setTimeout(900_000);
  page.setDefaultTimeout(UI_READY);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const datasetName = `e2e-data3-${suffix}`;
  const tabValue = `e2e-data3-tab-${suffix}`;
  const enterValue = `e2e-data3-enter-${suffix}`;

  // DevelopView.jsx "New dataset" and the importOptions.js "Upload a file" door.
  await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'New dataset', exact: true }).first().click();
  await page.getByText('Upload a file', { exact: true }).click();
  const uploadDialog = page.getByRole('dialog', { name: 'Upload a file' });
  // A filled name is kept; an empty one is replaced by the file's name.
  await uploadDialog.getByPlaceholder('Dataset name').fill(datasetName);
  await uploadFixture(uploadDialog.locator('input[type="file"]'), 'catalog-regions.csv');
  const uploaded = page.waitForResponse(r => new URL(r.url()).pathname === UPLOAD
    && r.request().method() === 'POST');
  await uploadDialog.getByRole('button', { name: 'Import 3 rows', exact: true }).click();
  const uploadResponse = await uploaded;
  expect(uploadResponse.status()).toBe(200);
  const datasetId: string = (await uploadResponse.json()).result.dataset_id;

  // Every update_cell_value request the page sends, not only the answered ones.
  const writes: Payload[] = [];
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === updatePath(datasetId))
      writes.push(request.postDataJSON() as Payload);
  });

  let regionId = '';
  let edited = { id: '', row_id: '' };
  await test.step('backend check 1: the uploaded dataset and edited row belong to the actor scope', async () => {
    await expect.poll(async () => (await probe.pg<{ n: string }>(
      'SELECT count(*) AS n FROM model_hub_cell WHERE dataset_id = $1 AND NOT deleted',
      [datasetId]))[0].n, POLL.ASYNC_JOB).toBe('6');
    expect(await probe.pg<{ name: string; organization_id: string; workspace_id: string }>(
      'SELECT name, organization_id, workspace_id FROM model_hub_dataset WHERE id = $1 AND NOT deleted', [datasetId]))
      .toEqual([{ name: datasetName, organization_id: actor.organizationId, workspace_id: actor.workspaceId }]);
    const columns = await probe.pg<{ id: string; name: string; data_type: string }>(
      'SELECT id, name, data_type FROM model_hub_column WHERE dataset_id = $1 AND NOT deleted ORDER BY name', [datasetId]);
    expect(columns.map(c => ({ name: c.name, data_type: c.data_type })))
      .toEqual([{ name: 'quantity', data_type: 'integer' }, { name: 'region', data_type: 'text' }]);
    regionId = columns.find(c => c.name === 'region')!.id;
    const cells = await probe.pg<{ id: string; row_id: string; value: string }>(
      'SELECT id, row_id, value FROM model_hub_cell WHERE dataset_id = $1 AND column_id = $2 AND NOT deleted ORDER BY row_id::text',
      [datasetId, regionId]);
    expect(cells.map(c => c.value).sort()).toEqual(['catalog-east', 'catalog-west', 'catalog-west']);
    // The middle CSV row: Tab from it always has a next editable cell.
    edited = cells.find(c => c.value === 'catalog-east')!;
    await testInfo.attach('seeded-ids', { contentType: 'application/json', body: JSON.stringify({
      datasetId, datasetName, regionId, editedCell: edited, columns, tabValue, enterValue,
      organizationId: actor.organizationId, workspaceId: actor.workspaceId }) });
  });

  const expectedWrite = (value: string): Payload => ({ column_id: regionId, row_id: edited.row_id, new_value: value });
  const storedValue = async () => {
    const table = await actor.api.get<TablePage>(tablePath(datasetId), TABLE_PARAMS);
    const row = table.result.table.find(r => r.row_id === edited.row_id)!;
    return (row[regionId] as { cell_value: string }).cell_value;
  };
  const expectStored = async (expected: string) => {
    await expect.poll(storedValue, POLL.CDC_VISIBLE).toBe(expected);
    expect(await probe.pg<{ id: string; row_id: string; value: string }>(
      'SELECT id, row_id, value FROM model_hub_cell WHERE id = $1 AND NOT deleted', [edited.id]))
      .toEqual([{ id: edited.id, row_id: edited.row_id, value: expected }]);
  };
  const acknowledged = () => page.waitForResponse(r => r.request().method() === 'POST'
    && new URL(r.url()).pathname === updatePath(datasetId));

  await expect(page).toHaveURL(new RegExp(`/dashboard/develop/${datasetId}`), { timeout: UI_READY });
  // DevelopDataV2 getRowId is row_id; common.js edits text in agLargeTextCellEditor (popup).
  const regionCell = page.locator(`.ag-row[row-id="${edited.row_id}"] [col-id="${regionId}"]`);
  const popupEditors = page.locator('.ag-popup-editor');
  await expect(regionCell).toHaveText('catalog-east', { timeout: UI_READY });

  await test.step('backend check 2: a Tab commit is written once while the next editor stays open', async () => {
    await regionCell.dblclick();
    await page.locator('.ag-popup-editor textarea').fill(tabValue);
    const ack = acknowledged();
    await page.keyboard.press('Tab');
    expect((await ack).status()).toBe(200);
    // AG Grid moves Tab to the next editable cell and opens its editor.
    await expect(page.locator('.ag-cell-popup-editing')).toHaveCount(1, { timeout: UI_READY });
    await expect(regionCell).not.toHaveClass(/ag-cell-popup-editing/, { timeout: UI_READY });
    // Observation window after the acknowledgement: an echo would be sent here.
    await page.waitForTimeout(HOLD_MS);
    expect(writes).toEqual([expectedWrite(tabValue)]);
    // The window held: the editor Tab opened is still the one open editor.
    await expect(page.locator('.ag-cell-popup-editing')).toHaveCount(1, { timeout: UI_READY });
    await expect(popupEditors).toHaveCount(1, { timeout: UI_READY });
  });
  await test.step('backend check 3: the Tab value is stored exactly', async () => {
    await expectStored(tabValue);
    await testInfo.attach('tab-commit', { contentType: 'application/json',
      body: JSON.stringify({ writes, stored: await storedValue() }) });
  });

  await test.step('backend check 2: an Enter commit of the same cell is written once', async () => {
    await page.keyboard.press('Escape');
    await expect(popupEditors).toHaveCount(0, { timeout: UI_READY });
    expect(writes).toEqual([expectedWrite(tabValue)]);
    await regionCell.dblclick();
    await page.locator('.ag-popup-editor textarea').fill(enterValue);
    const ack = acknowledged();
    await page.keyboard.press('Enter');
    expect((await ack).status()).toBe(200);
    await expect(popupEditors).toHaveCount(0, { timeout: UI_READY });
    await page.waitForTimeout(HOLD_MS);
    expect(writes).toEqual([expectedWrite(tabValue), expectedWrite(enterValue)]);
  });
  await test.step('backend check 3: the Enter value is stored exactly and shown after reload', async () => {
    await expectStored(enterValue);
    await testInfo.attach('enter-commit', { contentType: 'application/json',
      body: JSON.stringify({ writes, stored: await storedValue() }) });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(regionCell).toHaveText(enterValue, { timeout: UI_READY });
  });
});
