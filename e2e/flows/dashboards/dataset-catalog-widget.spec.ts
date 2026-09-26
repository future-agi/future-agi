import { test, expect } from '../../lib/fixtures';
import { uploadFixture } from '../../lib/upload';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// UploadFileModal.jsx / CreateDatasetFromLocalFileRequestSerializer.
const UPLOAD = '/model-hub/develops/create-dataset-from-local-file/';
// useDashboards.js: the editor's runPreviewQuery calls useDashboardQuery,
// not the separate widgets/preview endpoint. Save uses useCreateWidget.
const DASHBOARDS = '/tracer/dashboard/';
const METRICS = '/tracer/dashboard/metrics/';
const QUERY = '/tracer/dashboard/query/';
const UI_READY = 60_000; // README Writing a flow: browser first-paint ceiling.
// Independently calculated from fixtures/catalog-regions.csv, not server output.
const EXPECTED_SUM = 2 + 7 + 3;

type Cell = { id: string; row_id: string; value: string };
type Metric = { id: string; name: string; property_id: string; column_id: string;
  source: string; type: string; aggregation: string; display_name: string };
type QueryConfig = { metrics: Metric[]; filters: { column_id: string; source: string;
  filter_config: { filter_value: string[]; filter_op: string; col_type: string } }[];
  project_ids: string[]; breakdowns: unknown[]; time_range: Record<string, unknown> };
type QueryResult = { result: { query_complete: boolean; query_exact: boolean;
  metrics: { id: string; aggregation: string; series: { data: { value: number | null }[] }[] }[] } };
type Widget = { id: string; name: string; query_config: QueryConfig; chart_config: { chart_type: string } };

test('DASH-E2E-001: an imported numeric dataset column works in a saved widget', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DASH-E2E-001', area: 'dashboards',
    userGoal: 'Select a newly imported numeric dataset column and see its correct aggregate in a saved dashboard widget',
    steps: ['upload a synthetic CSV through the dataset form',
      'give the imported numeric column a unique name through its column menu',
      'create and name a dashboard through the UI',
      'select the new numeric metric, sum aggregation and dataset filter',
      'verify the preview, save the widget and reopen it'],
    backendChecks: [
      'the metric catalog identifies the imported numeric column under the actor dataset and workspace',
      'exact imported PG and CH cell identities produce the independently calculated sum in the real preview API and UI',
      'the saved widget retains its exact column and dataset scope in PG, API and reopened UI with the same aggregate',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(660_000); // ASYNC_JOB 60 + CDC_VISIBLE 180 + 6 UI_READY 360 + 60 headroom.
  page.setDefaultTimeout(UI_READY);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const datasetName = `e2e-dash1-data-${suffix}`;
  const dashboardName = `e2e-dash1-dashboard-${suffix}`;
  const columnName = `e2e-dash1-quantity-${suffix}`;
  const widgetName = `e2e-dash1-sum-${suffix}`;

  await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Add Dataset', exact: true }).first().click();
  await page.getByText('Upload a file (JSON, CSV)', { exact: true }).click();
  const uploadDialog = page.getByRole('dialog').filter({ has: page.getByText('Upload a File', { exact: true }) });
  await uploadFixture(uploadDialog.locator('input[type="file"]'), 'catalog-regions.csv');
  await uploadDialog.getByPlaceholder('Dataset Name').fill(datasetName);
  const uploaded = page.waitForResponse(r => new URL(r.url()).pathname === UPLOAD &&
    r.request().method() === 'POST', { timeout: UI_READY });
  await uploadDialog.getByRole('button', { name: 'Upload', exact: true }).click();
  const uploadResponse = await uploaded;
  expect(uploadResponse.status()).toBe(200);
  const datasetId = ((await uploadResponse.json()) as { result: { dataset_id: string } }).result.dataset_id;
  await testInfo.attach('dataset-id', { contentType: 'application/json',
    body: JSON.stringify({ datasetId, datasetName, organizationId: actor.organizationId, workspaceId: actor.workspaceId }) });
  await expect.poll(async () => (await probe.pg<{ n: string }>(
    'SELECT count(*) AS n FROM model_hub_cell WHERE dataset_id = $1 AND NOT deleted', [datasetId]))[0].n,
  POLL.ASYNC_JOB).toBe('6');
  const columns = await probe.pg<{ id: string; name: string; data_type: string }>(
    'SELECT id, name, data_type FROM model_hub_column WHERE dataset_id = $1 AND NOT deleted ORDER BY name', [datasetId]);
  expect(columns.map(c => ({ name: c.name, data_type: c.data_type })))
    .toEqual([{ name: 'quantity', data_type: 'integer' }, { name: 'region', data_type: 'text' }]);
  const columnId = columns.find(c => c.name === 'quantity')!.id;
  await testInfo.attach('column-ids', { body: JSON.stringify({ columns, columnId, columnName }), contentType: 'application/json' });

  // CustomDevelopDetailColumn's menu -> common.js Edit Column Name ->
  // EditColumnName.jsx PUT {new_column_name}; avoid a generic quantity locator.
  await expect(page).toHaveURL(new RegExp(`/dashboard/develop/${datasetId}`), { timeout: UI_READY });
  await page.locator(`.ag-header-cell[col-id="${columnId}"]`).getByRole('button').click();
  await page.getByRole('menuitem', { name: 'Edit Column Name', exact: true }).click();
  const renameDialog = page.getByRole('dialog').filter({ has: page.getByText('Edit Column Name', { exact: true }) });
  await renameDialog.getByPlaceholder('Enter column name').fill(columnName);
  const renamed = page.waitForResponse(r => new URL(r.url()).pathname ===
    `/model-hub/develops/${datasetId}/update_column_name/${columnId}/` && r.request().method() === 'PUT', { timeout: UI_READY });
  await renameDialog.getByRole('button', { name: 'Save', exact: true }).click();
  expect((await renamed).status()).toBe(200);

  await test.step('backend check 1: exact current dataset metric identity', async () => {
    const datasets = await probe.pg<{ id: string; name: string; organization_id: string; workspace_id: string }>(
      'SELECT id, name, organization_id, workspace_id FROM model_hub_dataset WHERE id = $1 AND NOT deleted', [datasetId]);
    expect(datasets).toEqual([{ id: datasetId, name: datasetName,
      organization_id: actor.organizationId, workspace_id: actor.workspaceId }]);
    const numeric = await probe.pg<{ id: string; dataset_id: string; name: string; data_type: string }>(
      'SELECT id, dataset_id, name, data_type FROM model_hub_column WHERE id = $1 AND NOT deleted', [columnId]);
    expect(numeric).toEqual([{ id: columnId, dataset_id: datasetId, name: columnName, data_type: 'integer' }]);
    // DashboardMetricsCatalogQuerySerializer has no dataset_id: discover by the
    // unique column name and assert its exact returned identity, not the first row.
    const catalog = await actor.api.post<{ result: { metrics: { property_id: string; name: string;
      display_name: string; source: string; data_type: string }[] } }>(METRICS,
    { source: 'datasets', category: 'custom_column', role: 'metric', search: columnName, cursor_mode: true, page_size: 25 });
    await testInfo.attach('numeric-catalog', { body: JSON.stringify(catalog), contentType: 'application/json' });
    expect(catalog.result.metrics.map(m => ({ id: m.property_id, name: m.name, label: m.display_name })))
      .toEqual([{ id: `dataset_column:${columnId}`, name: columnId, label: columnName }]);
  });

  const cells = await probe.pg<Cell>(
    'SELECT id, row_id, value FROM model_hub_cell WHERE dataset_id = $1 AND column_id = $2 AND NOT deleted ORDER BY row_id::text',
    [datasetId, columnId]);
  expect(cells.map(c => c.value).sort()).toEqual(['2', '3', '7']);
  await testInfo.attach('imported-cell-ids', { body: JSON.stringify(cells), contentType: 'application/json' });
  await expect.poll(() => probe.ch<Cell>(
    // CH UUID sort differs from PG: use lexical UUID order in both stores.
    `SELECT id, row_id, value FROM model_hub_cell FINAL
     WHERE dataset_id = {d:UUID} AND column_id = {c:UUID} AND NOT deleted AND _peerdb_is_deleted = 0
     ORDER BY toString(row_id)`, { d: datasetId, c: columnId }), POLL.CDC_VISIBLE).toEqual(cells);

  await page.goto('/dashboard/dashboards', { waitUntil: 'domcontentloaded' });
  // DashboardsListView creates Untitled immediately; DashboardDetailView InlineEdit
  // supplies the unique name through the public update endpoint.
  const created = page.waitForResponse(r => new URL(r.url()).pathname === DASHBOARDS &&
    r.request().method() === 'POST', { timeout: UI_READY });
  await page.getByRole('button', { name: 'Create Dashboard', exact: true }).click();
  const createdResponse = await created;
  expect(createdResponse.status()).toBe(200);
  const dashboardId = ((await createdResponse.json()) as { result: { id: string } }).result.id;
  await testInfo.attach('dashboard-id', { body: JSON.stringify({ dashboardId, dashboardName }), contentType: 'application/json' });
  await page.getByRole('heading', { name: 'Untitled', exact: true }).click();
  await page.getByPlaceholder('Untitled Dashboard').fill(dashboardName);
  const named = page.waitForResponse(r => new URL(r.url()).pathname === `${DASHBOARDS}${dashboardId}/` &&
    ['PUT', 'PATCH'].includes(r.request().method()), { timeout: UI_READY });
  await page.getByPlaceholder('Untitled Dashboard').press('Enter');
  expect((await named).status()).toBe(200);
  await page.getByRole('button', { name: 'Add Widget', exact: true }).first().click();
  await page.getByText('Untitled widget', { exact: true }).click();
  await page.getByPlaceholder('Untitled widget').fill(widgetName);
  await page.getByPlaceholder('Untitled widget').press('Enter');
  await page.getByRole('combobox').filter({ hasText: 'Line' }).click();
  await page.getByRole('option', { name: 'Metric', exact: true }).click();
  await page.getByText('Select Metric', { exact: true }).click();
  await page.getByPlaceholder('Search metrics...').fill(columnName);
  await page.locator(`p[title="${columnName}"]`).click();
  await page.getByText('Average', { exact: true }).click();
  await page.getByText('Sum', { exact: true }).click();
  await page.getByText('Filter', { exact: true }).click();
  // WidgetEditorView: the count label's parent is the category row; source chips
  // also say Datasets, so scope the category click to that existing anchor.
  await page.getByLabel(/^Datasets property count: /).locator('..')
    .getByText('Datasets', { exact: true }).click();
  await page.getByPlaceholder('Search filter attributes...').fill('Dataset');
  // WidgetCatalogOption: native output_type string and primary source datasets.
  await page.getByRole('button', { name: 'Dataset (string, Datasets)', exact: true }).click();
  await page.getByText('Select value...', { exact: true }).click();
  await page.getByPlaceholder('Search...', { exact: true }).fill(datasetName);
  // Dataset system filters use model_hub_dataset.name (dashboard.py), while
  // the metric and storage checks retain the immutable dataset/column IDs.
  const preview = page.waitForResponse(r => new URL(r.url()).pathname === QUERY && r.request().method() === 'POST' &&
    JSON.stringify(r.request().postDataJSON()).includes(datasetName), { timeout: UI_READY });
  await page.locator(`p[title="${datasetName}"]`).click();
  // FilterValuePickerPopup keeps checkbox choices local until Add calls onApply.
  await page.getByRole('button', { name: 'Add', exact: true }).click();
  const previewResponse = await preview;
  expect(previewResponse.status()).toBe(200);
  const previewConfig = previewResponse.request().postDataJSON() as QueryConfig;
  const previewBody = await previewResponse.json() as QueryResult;
  await testInfo.attach('preview-request-and-response', { contentType: 'application/json',
    body: JSON.stringify({ request: previewConfig, response: previewBody }) });

  await test.step('backend check 2: imported facts produce the independent sum', async () => {
    const aggregate = await probe.ch<{ total: number }>(
      `SELECT sum(toFloat64(value)) AS total FROM model_hub_cell FINAL
       WHERE dataset_id = {d:UUID} AND column_id = {c:UUID} AND NOT deleted AND _peerdb_is_deleted = 0`,
      { d: datasetId, c: columnId });
    expect(aggregate).toEqual([{ total: EXPECTED_SUM }]);
    expect(previewConfig.metrics).toHaveLength(1);
    expect(previewConfig.metrics[0]).toMatchObject({ id: columnId, column_id: columnId,
      property_id: `dataset_column:${columnId}`, source: 'datasets', type: 'custom_column', aggregation: 'sum' });
    expect(previewBody.result.query_complete).toBe(true);
    expect(previewBody.result.query_exact).toBe(true);
    expect(previewBody.result.metrics).toHaveLength(1);
    expect(previewBody.result.metrics[0].id).toBe(columnId);
    expect(previewBody.result.metrics[0].series).toHaveLength(1);
    expect(previewBody.result.metrics[0].series[0].data.reduce((sum, p) => sum + (p.value ?? 0), 0)).toBe(EXPECTED_SUM);
    await expect(page.getByRole('heading', { level: 2, name: '12.00', exact: true })).toBeVisible({ timeout: UI_READY });
    await testInfo.attach('preview-ui', { body: await page.screenshot(), contentType: 'image/png' });
  });

  const saved = page.waitForResponse(r => new URL(r.url()).pathname === `${DASHBOARDS}${dashboardId}/widgets/` &&
    r.request().method() === 'POST', { timeout: UI_READY });
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  const savedResponse = await saved;
  expect(savedResponse.status()).toBe(200);
  const saveRequest = savedResponse.request().postDataJSON() as Omit<Widget, 'id'>;
  const widget = ((await savedResponse.json()) as { result: Widget }).result;
  await testInfo.attach('saved-widget', { body: JSON.stringify({ request: saveRequest, widget }), contentType: 'application/json' });

  await test.step('backend check 3: saved identity, scope and aggregate survive reopening', async () => {
    expect(saveRequest.query_config.metrics).toEqual(previewConfig.metrics);
    expect(saveRequest.query_config.filters).toHaveLength(1);
    expect(saveRequest.query_config.filters[0]).toMatchObject({ column_id: 'dataset', source: 'datasets',
      filter_config: { filter_value: [datasetName], filter_op: 'in', col_type: 'SYSTEM_METRIC' } });
    const rows = await probe.pg<{ id: string; dashboard_id: string; name: string; query_config: QueryConfig }>(
      'SELECT id, dashboard_id, name, query_config FROM tracer_dashboardwidget WHERE id = $1 AND NOT deleted', [widget.id]);
    expect(rows).toEqual([{ id: widget.id, dashboard_id: dashboardId, name: widgetName, query_config: saveRequest.query_config }]);
    const detail = await actor.api.get<{ result: { id: string; name: string; workspace: string; widgets: Widget[] } }>(
      `${DASHBOARDS}${dashboardId}/`);
    expect(detail.result).toMatchObject({ id: dashboardId, name: dashboardName, workspace: actor.workspaceId });
    expect(detail.result.widgets.map(w => ({ id: w.id, name: w.name, query_config: w.query_config, chart_config: w.chart_config })))
      .toEqual([{ id: widget.id, name: widgetName, query_config: saveRequest.query_config, chart_config: saveRequest.chart_config }]);
    await expect(page).toHaveURL(new RegExp(`/dashboard/dashboards/${dashboardId}$`), { timeout: UI_READY });
    await page.reload({ waitUntil: 'domcontentloaded' });
    const tile = page.locator(`[data-widget-id="${widget.id}"]`);
    await expect(tile.getByText('12.00', { exact: true })).toBeVisible({ timeout: UI_READY });
    const reopened = page.waitForResponse(r => new URL(r.url()).pathname === QUERY && r.request().method() === 'POST' &&
      JSON.stringify(r.request().postDataJSON()).includes(columnId), { timeout: UI_READY });
    await tile.getByText(widgetName, { exact: true }).click();
    const reopenedResponse = await reopened;
    expect(reopenedResponse.status()).toBe(200);
    const reopenedConfig = reopenedResponse.request().postDataJSON() as QueryConfig;
    expect(reopenedConfig.metrics).toEqual(saveRequest.query_config.metrics);
    expect(reopenedConfig.filters).toEqual(saveRequest.query_config.filters);
    const reopenedBody = await reopenedResponse.json() as QueryResult;
    expect(reopenedBody.result.metrics[0].series[0].data.reduce((sum, p) => sum + (p.value ?? 0), 0)).toBe(EXPECTED_SUM);
    await expect(page.getByRole('heading', { level: 2, name: '12.00', exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByText(columnName, { exact: true }).first()).toBeVisible({ timeout: UI_READY });
    await testInfo.attach('reopened-query', { body: JSON.stringify({ request: reopenedConfig, response: reopenedBody }), contentType: 'application/json' });
    await testInfo.attach('reopened-ui', { body: await page.screenshot(), contentType: 'image/png' });
  });
});
