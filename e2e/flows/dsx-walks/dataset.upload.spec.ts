import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

type Table = { result: { column_config: { id: string; name: string; data_type: string }[];
  table: ({ row_id: string } & Record<string, unknown>)[] } };
type Progress = { result: { dataset_id: string; dataset_name: string; processing_status: string;
  is_completed: boolean; is_failed: boolean } };
const UPLOAD = '/model-hub/develops/create-dataset-from-local-file/';
const UI_READY = 60_000;

// UploadFileModal.jsx, ImportPreviewPane.jsx, uploadBody.js, useFileUploadLifecycle.js.
// Completion envelope and tenant statuses follow create-upload.spec.ts at the pinned revision.
test('@DSX-WALK-IN-04: Upload a synthetic CSV and reopen its fully persisted values within the owning organization.', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DSX-WALK-IN-04', area: 'dsx-walks',
    userGoal: 'dataset.upload: complete a real CSV upload, not merely accept a queued job',
    steps: ['seed an organization-B control dataset through the served API',
      'preview a two-column CSV in organization A and import its three rows',
      'wait for worker completion, inspect exact persisted values and reload',
      'deny organization B access to the import progress and dataset table'],
    backendChecks: ['multipart upload carries new_dataset_name, model_type and the synthetic CSV file',
      'progress reports the created id and name with completed=true, failed=false and processing_status=completed',
      'table returns exact ordered text/integer columns and all three nonempty value pairs',
      'foreign progress GET returns 404 and table GET returns semantic 400; both owners retain their own data'],
  }),
}, async ({ browser, scopeActors: scopes }, testInfo) => {
  test.setTimeout(600_000);
  const a = scopes.ownerA;
  const b = scopes.ownerB;
  expect(a.organizationId).not.toBe(b.organizationId);
  const name = `dsx-upload-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const control = await scopes.send<{ result: { dataset_id: string } }>(b, 'POST',
    '/model-hub/develops/create-dataset-manually/', { dataset_name: `${name}-control`, model_type: 'GenerativeLLM',
      number_of_rows: 0, number_of_columns: 1, columns: [{ name: 'private_b', type: 'text', role: 'input' }] });
  expect(control.status).toBe(200);
  const otherId = control.body.result.dataset_id;
  await testInfo.attach('seeded-control', { contentType: 'application/json', body: JSON.stringify({
    datasetId: otherId, organizationId: b.organizationId, workspaceId: b.workspaceId }) });
  const csv = 'region,quantity\nsynthetic-west,2\nsynthetic-east,7\nsynthetic-north,3\n';
  const values = [['synthetic-west', '2'], ['synthetic-east', '7'], ['synthetic-north', '3']];
  const context = await scopes.openContext(browser, a);
  try {
    const origins = new Set([new URL(E2E.appUrl).origin, new URL(E2E.apiUrl).origin]);
    await context.route('**/*', route => origins.has(new URL(route.request().url()).origin)
      ? route.continue() : route.abort('blockedbyclient'));
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: 'New dataset', exact: true }).click();
    await page.getByText('Upload a file', { exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Upload a file', exact: true });
    await dialog.locator('input[type="file"]').setInputFiles({ name: 'dsx-regions.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) });
    await dialog.getByPlaceholder('Dataset name', { exact: true }).fill(name);
    const preview = dialog.getByRole('table', { name: 'File preview', exact: true });
    await expect(preview.locator('thead th')).toHaveText(['#', 'region', 'quantity']);
    await expect(preview.locator('tbody tr')).toHaveCount(3);
    for (const [index, pair] of values.entries()) {
      await expect(preview.locator('tbody tr').nth(index).locator('td')).toHaveText([String(index + 1), ...pair]);
    }
    const pending = page.waitForResponse(r => new URL(r.url()).pathname === UPLOAD && r.request().method() === 'POST');
    await dialog.getByRole('button', { name: 'Import 3 rows', exact: true }).click();
    const response = await pending;
    expect(response.status()).toBe(200);
    const multipart = response.request().postDataBuffer()!.toString('utf8');
    expect(multipart).toContain(`name="new_dataset_name"\r\n\r\n${name}`);
    expect(multipart).toContain('name="model_type"\r\n\r\nGenerativeLLM');
    expect(multipart).toContain('name="file"; filename="dsx-regions.csv"');
    expect(multipart).toContain(csv);
    const uploaded = await response.json() as { result: { dataset_id: string; dataset_name: string;
      estimated_rows: number; estimated_columns: number } };
    expect(uploaded.result).toMatchObject({ dataset_name: name, estimated_rows: 3, estimated_columns: 2 });
    const id = uploaded.result.dataset_id;
    expect(id).toMatch(/^[0-9a-f-]{36}$/);
    await testInfo.attach('uploaded-identities', { contentType: 'application/json', body: JSON.stringify({
      owner: 'dataset.upload', datasetId: id, organizationId: a.organizationId, workspaceId: a.workspaceId,
      foreignOrganizationId: b.organizationId, otherDatasetId: otherId }) });
    const progressPath = `/model-hub/develops/dataset-creation-progress/${id}/`;
    await expect.poll(async () => {
      const progress = await scopes.send<Progress>(a, 'GET', progressPath);
      expect(progress.status).toBe(200);
      expect(progress.body.result.is_failed).toBe(false);
      return progress.body.result;
    }, POLL.ASYNC_JOB).toMatchObject({ dataset_id: id, dataset_name: name,
      processing_status: 'completed', is_completed: true, is_failed: false });
    await expect(dialog).toBeHidden();
    await expect(page).toHaveURL(new RegExp(`/dashboard/develop/${id}(?:[/?]|$)`));
    const tablePath = `/model-hub/develops/${id}/get-dataset-table/`;
    const read = async () => {
      const result = await scopes.send<Table>(a, 'GET', tablePath, undefined, { current_page_index: 0, page_size: 25 });
      expect(result.status).toBe(200);
      return result.body.result;
    };
    const stored = await read();
    expect(stored.column_config.map(({ name, data_type }) => ({ name, data_type }))).toEqual([
      { name: 'region', data_type: 'text' }, { name: 'quantity', data_type: 'integer' },
    ]);
    expect(stored.table).toHaveLength(3);
    expect(stored.table.map(row => stored.column_config.map(column =>
      (row[column.id] as { cell_value: string }).cell_value))).toEqual(values);
    await page.reload({ waitUntil: 'domcontentloaded' });
    for (const [index, column] of stored.column_config.entries()) {
      await expect(page.locator(`.ag-header-cell[col-id="${column.id}"]`).getByText(column.name, { exact: true })).toBeVisible();
      await expect(page.locator(`.ag-row [col-id="${column.id}"]`)).toHaveText(values.map(pair => pair[index]));
    }
    expect((await scopes.send(b, 'GET', progressPath)).status).toBe(404);
    const denied = await scopes.send<{ message: string }>(b, 'GET', tablePath, undefined, { current_page_index: 0, page_size: 25 });
    expect(denied.status).toBe(400);
    expect(denied.body.message).toBe('The specified dataset does not exist or has been deleted.');
    expect(await read()).toEqual(stored);
    const ownB = await scopes.send<Table>(b, 'GET', `/model-hub/develops/${otherId}/get-dataset-table/`, undefined,
      { current_page_index: 0, page_size: 25 });
    expect(ownB.status).toBe(200);
    expect(ownB.body.result.table).toEqual([]);
    expect(ownB.body.result.column_config.map(column => column.name)).toEqual(['private_b']);
    await testInfo.attach('persisted-identities', { contentType: 'application/json', body: JSON.stringify({
      datasetId: id, rowIds: stored.table.map(row => row.row_id), columnIds: stored.column_config.map(column => column.id) }) });
  } finally {
    await context.close();
  }
});
