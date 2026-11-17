import type { Locator, Page, TestInfo } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { test, expect } from '../../lib/scope-actors';
import type { ScopeActor, ScopeActors } from '../../lib/scope-actors';
import type { StateProbe } from '../../lib/state-probe';
import { POLL } from '../../lib/state-probe';
import { uploadFixture } from '../../lib/upload';
import { flowAnnotation } from '../../lib/flow-meta';

// model_hub/urls.py: CreateDatasetFromLocalFileView, not UploadFileView.
const UPLOAD = '/model-hub/develops/create-dataset-from-local-file/';
// DatasetCreationProgressView and GetDatasetTableView in model_hub/urls.py.
const progressPath = (id: string) => `/model-hub/develops/dataset-creation-progress/${id}/`;
const tablePath = (id: string) => `/model-hub/develops/${id}/get-dataset-table/`;
const UI_READY = 60_000;

type Format = 'csv' | 'json' | 'jsonl';
type Value = string | null;
type FileCase = {
  format: Format;
  // The dialog's column order and the data type the worker infers.
  columns: { name: string; data_type: string }[];
  // Persisted values by row order, in column order; null is an empty cell.
  // JSON values are stored as json.dumps text; the preview shows the same
  // value as JSON.stringify text.
  cells: Value[][];
  // Columns read back from the rendered grid (plain text columns).
  grid: string[];
  skipped?: string;
};
// Expectations are written from the fixture files, never from a response.
const FILES: FileCase[] = [
  { // e2e/fixtures/catalog-regions.csv
    format: 'csv',
    columns: [{ name: 'region', data_type: 'text' }, { name: 'quantity', data_type: 'integer' }],
    cells: [['catalog-west', '2'], ['catalog-east', '7'], ['catalog-west', '3']],
    grid: ['region', 'quantity'],
  },
  { // create-upload-records.json: strings that look numeric stay text, numbers
    // and booleans keep their JSON spelling, nested objects stay JSON.
    format: 'json',
    columns: [
      { name: 'sku', data_type: 'text' }, { name: 'title', data_type: 'text' },
      { name: 'in_stock', data_type: 'boolean' }, { name: 'price', data_type: 'float' },
      { name: 'qty', data_type: 'integer' }, { name: 'restocked_at', data_type: 'datetime' },
      { name: 'dims', data_type: 'json' }, { name: 'note', data_type: 'text' },
    ],
    cells: [
      ['007', 'Desk lamp', 'true', '12.5', '2', '2026-05-01T10:00:00Z', '{"w": 20, "h": 45}', null],
      ['042', 'Café chair', 'false', '40', null, '2026-05-02T11:30:00Z', '{"w": 48, "h": 90}', 'ships flat'],
      ['100', 'Shelf', 'true', '0.75', '11', '2026-05-03T09:15:00Z', '{"w": 80, "h": 180}', null],
    ],
    grid: ['sku', 'title'],
  },
  { // create-upload-records.jsonl: line 3 is not an object and is skipped.
    format: 'jsonl',
    columns: [
      { name: 'ticket', data_type: 'text' }, { name: 'summary', data_type: 'text' },
      { name: 'urgent', data_type: 'boolean' }, { name: 'score', data_type: 'float' },
      { name: 'replies', data_type: 'integer' }, { name: 'labels', data_type: 'array' },
    ],
    cells: [
      ['0012', 'Refund not received', 'true', '0.5', '3', '["billing", "refund"]'],
      ['0013', 'Läuft nicht', 'false', '1', '0', '[]'],
      ['0014', 'Password reset', 'false', '0.25', null, '["account"]'],
    ],
    grid: ['ticket', 'summary'],
    skipped: 'Line 3: Not a JSON object',
  },
];
type Column = { id: string; name: string; data_type: string };
type Cell = { row_id: string; column_id: string; value: Value; order: number };
type TableRow = { row_id: string; [key: string]: unknown };
type Table = { result: { table: TableRow[] } };
type Progress = { result: { dataset_id: string; dataset_name: string;
  processing_status: string; is_completed: boolean; is_failed: boolean } };

const JSON_TYPES = new Set(['json', 'array']);
// ImportPreviewPane.jsx shows an empty cell as an en dash.
const previewText = (file: FileCase, position: number, value: Value) => value === null ? '–'
  : JSON_TYPES.has(file.columns[position].data_type) ? JSON.stringify(JSON.parse(value)) : value;

const columnValues = (file: FileCase, name: string) => {
  const position = file.columns.findIndex(column => column.name === name);
  return file.cells.map(row => row[position]);
};

async function chooseFile(input: Locator, format: Format) {
  if (format === 'csv') {
    await uploadFixture(input, 'catalog-regions.csv');
    return;
  }
  await input.setInputFiles(fileURLToPath(new URL(`./create-upload-records.${format}`, import.meta.url)),
    { timeout: UI_READY });
}

async function upload(page: Page, name: string, file: FileCase, testInfo: TestInfo) {
  // DevelopView.jsx, importOptions.js and UploadFileModal.jsx accessible controls.
  await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'New dataset', exact: true }).click();
  await page.getByText('Upload a file', { exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Upload a file', exact: true });
  await expect(dialog.getByText('CSV, JSON or JSONL, up to 25 MB.', { exact: false })).toBeVisible();
  await chooseFile(dialog.locator('input[type="file"]'), file.format);
  await dialog.getByPlaceholder('Dataset name', { exact: true }).fill(name);
  // ImportPreviewPane.jsx: what the dialog shows is what the worker must store.
  const preview = dialog.getByRole('table', { name: 'File preview', exact: true });
  await expect(preview).toBeVisible({ timeout: UI_READY });
  await expect(preview.locator('thead th')).toHaveText(['#', ...file.columns.map(column => column.name)]);
  const previewRows = preview.locator('tbody tr');
  await expect(previewRows).toHaveCount(file.cells.length);
  for (const [index, row] of file.cells.entries()) {
    await expect(previewRows.nth(index).locator('td'))
      .toHaveText([String(index + 1), ...row.map((value, position) => previewText(file, position, value))]);
  }
  const skipped = dialog.getByRole('alert').filter({ hasText: "can't be read and will be skipped" });
  if (file.skipped) {
    await expect(skipped).toContainText("1 line can't be read and will be skipped");
    await expect(skipped).toContainText(file.skipped);
  } else {
    await expect(skipped).toHaveCount(0);
  }
  // Basic upload must not expose unsupported role or transform controls.
  await expect(dialog.getByRole('combobox', { name: /^Role of / })).toHaveCount(0);
  await expect(dialog.getByLabel('Flatten nested objects', { exact: true })).toHaveCount(0);
  await expect(dialog.getByLabel('Read JSON inside text cells', { exact: true })).toHaveCount(0);
  const responsePromise = page.waitForResponse(response =>
    new URL(response.url()).pathname === UPLOAD && response.request().method() === 'POST',
  { timeout: UI_READY });
  await dialog.getByRole('button', { name: `Import ${file.cells.length} rows`, exact: true }).click();
  const response = await responsePromise;
  // LocalFileDatasetCreateStartedResponseSerializer.result.
  const body = await response.json();
  await testInfo.attach(`upload-${name}`, { contentType: 'application/json',
    body: JSON.stringify({ path: UPLOAD, status: response.status(), body }) });
  expect(response.status()).toBe(200);
  const { result } = body;
  expect(result.dataset_name).toBe(name);
  expect(result.estimated_rows).toBe(file.cells.length);
  expect(result.estimated_columns).toBe(file.columns.length);
  expect(result.dataset_id).toMatch(/^[0-9a-f-]{36}$/);
  return result.dataset_id as string;
}

async function checkStoredUpload(probe: StateProbe, actor: ScopeActor, id: string,
  name: string, file: FileCase, expectedOrganization: string) {
  await expect.poll(async () => {
    const progress = await actor.api.get<Progress>(progressPath(id));
    expect(progress.result.is_failed).toBe(false);
    return progress.result;
  }, POLL.ASYNC_JOB).toMatchObject({ dataset_id: id, dataset_name: name,
    processing_status: 'completed', is_completed: true, is_failed: false });
  const [dataset] = await probe.pg<{ name: string; organization_id: string; workspace_id: string;
    column_order: string[] }>(
    'SELECT name, organization_id, workspace_id, column_order FROM model_hub_dataset WHERE id = $1 AND NOT deleted', [id]);
  expect({ name: dataset.name, organization_id: dataset.organization_id, workspace_id: dataset.workspace_id })
    .toEqual({ name, organization_id: expectedOrganization, workspace_id: actor.workspaceId });
  const stored = await probe.pg<Column>(
    'SELECT id, name, data_type FROM model_hub_column WHERE dataset_id = $1 AND NOT deleted', [id]);
  expect(dataset.column_order).toHaveLength(stored.length);
  const columns = dataset.column_order.map(columnId => stored.find(column => column.id === columnId)!);
  expect(columns.map(column => ({ name: column?.name, data_type: column?.data_type })))
    .toEqual(file.columns);
  const cells = await probe.pg<Cell>(
    `SELECT c.row_id, c.column_id, c.value, r."order" FROM model_hub_cell c
     JOIN model_hub_column col ON col.id = c.column_id AND col.dataset_id = c.dataset_id
     JOIN model_hub_row r ON r.id = c.row_id AND r.dataset_id = c.dataset_id
     WHERE c.dataset_id = $1 AND NOT c.deleted AND NOT col.deleted AND NOT r.deleted
     ORDER BY r."order"`, [id]);
  expect(cells).toHaveLength(file.cells.length * file.columns.length);
  const orders = [...new Set(cells.map(cell => cell.order))];
  expect(orders).toEqual(file.cells.map((_, order) => order));
  expect(orders.map(order => columns.map(column =>
    cells.find(cell => cell.order === order && cell.column_id === column.id)?.value)))
    .toEqual(file.cells);
  return { columns, cells };
}

async function checkTable(page: Page, scopes: ScopeActors, actor: ScopeActor, id: string,
  file: FileCase, columns: Column[], cells: Cell[], perturb: boolean) {
  const response = await scopes.send<Table>(actor, 'GET', tablePath(id), undefined, { current_page_index: 0, page_size: 25 });
  expect(response.status).toBe(200);
  const expected = file.cells.map((row, order) => row.map((value, position) =>
    perturb && position === 0 ? `${value}-unexpected` : value));
  expect(response.body.result.table.map(row => ({
    row_id: row.row_id,
    values: columns.map(column => (row[column.id] as { cell_value: Value }).cell_value),
  }))).toEqual(expected.map((values, order) => ({
    row_id: cells.find(cell => cell.order === order)!.row_id, values,
  })));
  await expect(page).toHaveURL(new RegExp(`/dashboard/develop/${id}(?:[/?]|$)`), { timeout: UI_READY });
  for (const name of file.grid) {
    const column = columns.find(candidate => candidate.name === name)!;
    await expect(page.locator(`.ag-row [col-id="${column.id}"]`))
      .toHaveText(columnValues(file, name).map(value => value ?? ''), { timeout: UI_READY });
  }
}

test('DATA-E2E-012: a file upload persists exact rows within its organization and workspace', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-012', area: 'datasets',
    userGoal: 'Create datasets from a local CSV, JSON and JSONL file, reopen exactly the previewed rows and keep them private to their workspace',
    steps: [
      'open New dataset and upload the synthetic CSV, JSON and JSONL files in each of two independent organizations',
      'check each file preview, including the JSONL line that cannot be read, before importing',
      'wait for the real file worker to finish and inspect the imported columns and rows',
      'reload every dataset page and read the same exact values',
      'attempt to read the other organization and a sibling workspace through the real API',
    ],
    backendChecks: [
      'every completed upload persists the previewed columns, column order, data types and exact cells under its submitting organization and workspace',
      'JSON strings stay text, JSON numbers and booleans keep their JSON spelling, nested values stay JSON and the unreadable JSONL line is skipped',
      'the public table API and refreshed browser show exactly the persisted row identities and values',
      'foreign-organization and sibling-workspace progress/table reads are refused while owner reads still succeed',
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe }, testInfo) => {
  // Six uploads: 6 * (ASYNC_JOB 60 + 5 * UI_READY 60), plus 120 for scope provisioning.
  test.setTimeout(2_280_000);
  const perturb = process.env.E2E_UPLOAD_PERTURB;
  if (perturb && !['ownership', 'values', 'isolation'].includes(perturb)) {
    throw new Error('Unknown E2E_UPLOAD_PERTURB anchor');
  }
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const uploads: { actor: ScopeActor; label: string; id: string; name: string }[] = [];
  const blockedHosts = new Set<string>();
  for (const [label, actor] of [['a', scopeActors.ownerA], ['b', scopeActors.ownerB]] as const) {
    const context = await scopeActors.openContext(browser, actor);
    // Never let telemetry or a remote asset fallback contact external services.
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.hostname === 'localhost' || url.protocol === 'data:' || url.protocol === 'blob:') {
        await route.continue();
      } else {
        blockedHosts.add(url.hostname);
        await route.abort('blockedbyclient');
      }
    });
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    for (const file of FILES) {
      const name = `e2e-upload-${label}-${file.format}-${suffix}`;
      const id = await upload(page, name, file, testInfo);
      uploads.push({ actor, label, id, name });
      await testInfo.attach(`dataset-identity-${label}-${file.format}`, { contentType: 'application/json',
        body: JSON.stringify({ id, name, organizationId: actor.organizationId, workspaceId: actor.workspaceId }) });
      const { columns, cells } = await test.step(`verify ${file.format} upload ownership and stored values`, () =>
        checkStoredUpload(scopeProbe, actor, id, name, file,
          perturb === 'ownership' ? '00000000-0000-0000-0000-000000000000' : actor.organizationId));
      await testInfo.attach(`persisted-identities-${label}-${file.format}`, {
        contentType: 'application/json', body: JSON.stringify({ columns, cells }),
      });
      await test.step(`verify ${file.format} API readback and reload persistence`, async () => {
        await checkTable(page, scopeActors, actor, id, file, columns, cells, perturb === 'values');
        await page.reload({ waitUntil: 'domcontentloaded' });
        await checkTable(page, scopeActors, actor, id, file, columns, cells, false);
        await testInfo.attach(`reloaded-dataset-${label}-${file.format}`, {
          contentType: 'image/png', body: await page.screenshot({ fullPage: true }),
        });
      });
    }
  }
  expect(new Set(uploads.map(({ id }) => id)).size).toBe(uploads.length);
  await test.step('verify organization and workspace refusals with positive controls', async () => {
    const aInSibling = scopeActors.withWorkspace(scopeActors.ownerA, scopeActors.emptyWorkspace.id);
    const probes = uploads.flatMap(({ label, id }) => label === 'a'
      ? [{ actor: scopeActors.ownerB, id }, { actor: aInSibling, id }]
      : [{ actor: scopeActors.ownerA, id }]);
    for (const { actor, id } of probes) {
      const progress = await scopeActors.send(actor, 'GET', progressPath(id));
      expect(progress.status).toBe(perturb === 'isolation' ? 200 : 404);
      const table = await scopeActors.send(actor, 'GET', tablePath(id), undefined,
        { current_page_index: 0, page_size: 25 });
      // Use a valid query: a pagination validation error is not a tenant denial.
      // GetDatasetTableView and tfc/utils/error_codes.py: DATASET_NOT_FOUND.
      expect(table.status).toBe(400);
      expect(table.body).toMatchObject({
        message: 'The specified dataset does not exist or has been deleted.',
      });
    }
    for (const { actor, id } of uploads) {
      expect((await scopeActors.send(actor, 'GET', progressPath(id))).status).toBe(200);
      expect((await scopeActors.send(actor, 'GET', tablePath(id), undefined,
        { current_page_index: 0, page_size: 25 })).status).toBe(200);
    }
  });
  await testInfo.attach('blocked-external-hosts', {
    contentType: 'application/json', body: JSON.stringify([...blockedHosts].sort()),
  });
});
