import type { Page, Request } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import type { ScopeActor, ScopeActors } from '../../lib/scope-actors';
import type { StateProbe } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// model_hub/urls.py and ManualDatasetCreateRequestSerializer.
const CREATE = '/model-hub/develops/create-dataset-manually/';
const path = (id: string, action: string) => `/model-hub/develops/${id}/${action}/`;
const UI_READY = 60_000;
type Schema = { name: string; type: string; role: string };
type StoredColumn = Schema & { id: string };
type Table = { result: { column_config: { id: string; name: string; data_type: string; role: string }[];
  table: { row_id: string; [key: string]: unknown }[] } };

async function create(page: Page, name: string, columns: Schema[], rows: number) {
  // DevelopView.jsx, importOptions.js, ManuallyCreateDataset.jsx and BlankGridColumns.jsx.
  await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'New dataset', exact: true }).click();
  await expect(page.getByText('From production traces', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Task suite', { exact: true })).toHaveCount(0);
  await page.getByText('Start with a blank grid', { exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Start with a blank grid', exact: true });
  await expect(dialog).toBeVisible({ timeout: UI_READY });
  await dialog.getByRole('textbox', { name: /^Name/ }).fill(name);
  await expect(dialog.getByRole('spinbutton', { name: 'Blank rows' })).toHaveValue('0');
  await dialog.getByRole('button', { name: 'Add column', exact: true }).click();
  for (const [index, column] of columns.entries()) {
    await dialog.getByRole('textbox', { name: `Column ${index + 1} name`, exact: true }).fill(column.name);
    await dialog.getByRole('combobox', { name: `Column ${index + 1} type`, exact: true }).click();
    const label = ({ text: 'Text', integer: 'Integer', json: 'JSON' } as Record<string, string>)[column.type];
    await page.getByRole('option', { name: label, exact: true }).click();
    await dialog.getByRole('combobox', { name: `Column ${index + 1} role`, exact: true }).click();
    await page.getByRole('option', { name: ({ input: 'Input', expected: 'Expected', metadata: 'Metadata' } as Record<string, string>)[column.role], exact: true }).click();
  }
  // A duplicate must not silently remove a requested column.
  await dialog.getByRole('textbox', { name: 'Column 3 name', exact: true }).fill(columns[0].name);
  await expect(dialog.getByRole('button', { name: 'Create dataset', exact: true })).toBeDisabled();
  await dialog.getByRole('textbox', { name: 'Column 3 name', exact: true }).fill(columns[2].name);
  await dialog.getByRole('spinbutton', { name: 'Blank rows', exact: true }).fill(String(rows));
  const pending = page.waitForResponse(response => new URL(response.url()).pathname === CREATE &&
    response.request().method() === 'POST', { timeout: UI_READY });
  let submissions = 0;
  const countSubmission = (request: Request) => {
    if (new URL(request.url()).pathname === CREATE && request.method() === 'POST') submissions += 1;
  };
  page.on('request', countSubmission);
  if (rows === 0) await dialog.getByRole('textbox', { name: /^Name/ }).press('Enter');
  else await dialog.getByRole('button', { name: 'Create dataset', exact: true }).click();
  const response = await pending;
  page.off('request', countSubmission);
  expect(submissions).toBe(1);
  expect(response.request().postDataJSON()).toMatchObject({ dataset_name: name, number_of_rows: rows, columns });
  expect(response.status(), JSON.stringify(await response.json())).toBe(200);
  const { result } = await response.json();
  expect(result.rows_created).toBe(rows);
  expect(result.columns_created).toBe(columns.length);
  await expect(page).toHaveURL(new RegExp(`/dashboard/develop/${result.dataset_id}(?:[/?]|$)`), { timeout: UI_READY });
  return result.dataset_id as string;
}

async function readSchema(probe: StateProbe, id: string) {
  return probe.pg<StoredColumn>(
    `SELECT c.id, c.name, c.data_type AS type, d.column_config->c.id::text->>'role' AS role
     FROM model_hub_dataset d JOIN model_hub_column c ON c.dataset_id = d.id
     WHERE d.id = $1 AND NOT c.deleted ORDER BY array_position(d.column_order, c.id::text)`, [id]);
}

async function readback(page: Page, scopes: ScopeActors, probe: StateProbe, actor: ScopeActor,
  id: string, name: string, columns: Schema[], count: number, perturb?: string) {
  expect(await probe.pg('SELECT name, organization_id, workspace_id FROM model_hub_dataset WHERE id = $1', [id]))
    .toEqual([{ name, organization_id: actor.organizationId, workspace_id: actor.workspaceId }]);
  const stored = await readSchema(probe, id);
  expect(stored.map(({ name, type, role }) => ({ name, type, role }))).toEqual(
    perturb === 'schema' ? columns.map(column => ({ ...column, name: `${column.name}-wrong` })) : columns);
  const result = await scopes.send<Table>(actor, 'GET', path(id, 'get-dataset-table'), undefined,
    { current_page_index: 0, page_size: 25 });
  expect(result.status).toBe(200);
  expect(result.body.result.column_config.map(({ id, name, data_type, role }) => ({ id, name, type: data_type, role }))).toEqual(stored);
  const rows = result.body.result.table;
  expect(rows).toHaveLength(perturb === 'rows' ? count + 1 : count);
  expect(await probe.pg('SELECT id FROM model_hub_row WHERE dataset_id = $1 AND NOT deleted ORDER BY "order"', [id]))
    .toEqual(rows.map(row => ({ id: row.row_id })));
  for (const column of stored) {
    expect(rows.map(row => (row[column.id] as { cell_value: string | null }).cell_value)).toEqual(Array(count).fill(null));
    await expect(page.locator(`.ag-header-cell[col-id="${column.id}"]`).getByText(column.name, { exact: true }))
      .toBeVisible({ timeout: UI_READY });
    // FloatIntegerCellRenderer returns an empty string for null; text/JSON use CellChrome's Empty label.
    const emptyLabel = column.type === 'integer' ? '' : 'Empty';
    await expect(page.locator(`.ag-row [col-id="${column.id}"]`)).toHaveText(Array(count).fill(emptyLabel), { timeout: UI_READY });
  }
  return stored;
}

test('DATA-E2E-007: blank grids retain named typed columns and roles after reopening', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-007', area: 'datasets',
    userGoal: 'Create a blank dataset with named, typed columns and roles and reopen it without losing its schema',
    steps: [
      'open New dataset and configure a named blank grid in each of two independent organizations',
      'choose text, integer and JSON columns with Input, Expected and Metadata roles and confirm creation',
      'reload each dataset and inspect its exact schema and blank rows',
      'reject invalid schemas without partial datasets and refuse foreign-organization and sibling-workspace access',
    ],
    backendChecks: [
      'exact ordered column names, types and roles persist under the submitting organization and workspace',
      'the table API and reloaded grid expose the same columns and blank row identities',
      'invalid schemas leave no dataset and cross-scope reads and writes are refused without mutations',
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe }, testInfo) => {
  // Scope provisioning 120 + two actors * six UI_READY steps 720 + validation/isolation 120.
  test.setTimeout(960_000);
  const perturb = process.env.E2E_BLANK_PERTURB;
  if (perturb && !['schema', 'rows', 'isolation'].includes(perturb)) throw new Error('Unknown blank-grid perturbation');
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  for (const [label, actor, foreign] of [
    ['a', scopeActors.ownerA, scopeActors.ownerB], ['b', scopeActors.ownerB, scopeActors.ownerA],
  ] as const) {
    const context = await scopeActors.openContext(browser, actor);
    try {
      await context.route('**/*', route => {
        const url = new URL(route.request().url());
        return url.hostname === 'localhost' || ['data:', 'blob:'].includes(url.protocol)
          ? route.continue() : route.abort('blockedbyclient');
      });
      const page = await context.newPage();
      page.setDefaultTimeout(UI_READY);
      const name = `e2e-blank-${label}-${suffix}`;
      const columns: Schema[] = [
        { name: `question_${label}`, type: 'text', role: 'input' },
        { name: `answer_${label}`, type: 'integer', role: 'expected' },
        { name: `context_${label}`, type: 'json', role: 'metadata' },
      ];
      const rowCount = label === 'a' ? 2 : 0;
      const id = await create(page, name, columns, rowCount);
      await testInfo.attach(`created-${label}`, { contentType: 'application/json', body: JSON.stringify({
        id, name, columns, organizationId: actor.organizationId, workspaceId: actor.workspaceId,
      }) });
      await readback(page, scopeActors, scopeProbe, actor, id, name, columns, rowCount);
      await page.reload({ waitUntil: 'domcontentloaded' });
      const stored = await readback(page, scopeActors, scopeProbe, actor, id, name, columns, rowCount, perturb);
      await testInfo.attach(`reloaded-${label}`, { contentType: 'image/png', body: await page.screenshot({ fullPage: true }) });
      const invalidName = `${name}-invalid`;
      const invalid = await scopeActors.send(actor, 'POST', CREATE,
        { dataset_name: invalidName, columns: [{ name: 'must-not-persist', type: 'unsupported' }] });
      expect(invalid.status).toBe(400);
      expect(await scopeProbe.pg('SELECT id FROM model_hub_dataset WHERE name = $1 AND organization_id = $2',
        [invalidName, actor.organizationId])).toEqual([]);
      const denied = [foreign];
      if (label === 'a') denied.push(scopeActors.withWorkspace(actor, scopeActors.emptyWorkspace.id));
      for (const outsider of denied) {
        const response = await scopeActors.send(outsider, 'GET', path(id, 'get-dataset-table'), undefined,
          { current_page_index: 0, page_size: 25 });
        // GetDatasetTableView returns a semantic DATASET_NOT_FOUND 400, not a pagination error.
        expect(response.status).toBe(400);
        expect(response.body).toMatchObject({ message: 'The specified dataset does not exist or has been deleted.' });
        expect((await scopeActors.send(outsider, 'POST', path(id, 'add_empty_rows'), { num_rows: 1 })).status)
          .toBe(perturb === 'isolation' ? 200 : 404);
      }
      expect(await readSchema(scopeProbe, id)).toEqual(stored);
      await readback(page, scopeActors, scopeProbe, actor, id, name, columns, rowCount);
    } finally {
      await context.close();
    }
  }
});
