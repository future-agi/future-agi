import type { Page } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import type { ScopeActor, ScopeActors } from '../../lib/scope-actors';
import type { StateProbe } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// model_hub/urls.py; ManualDatasetCreateRequestSerializer; SyntheticDataSerializer.
const CREATE = '/model-hub/develops/create-dataset-manually/';
const path = (id: string, action: string) => `/model-hub/develops/${id}/${action}/`;
// generation-standin.yml shares the backend's network namespace, not an external host.
const MODEL = 'gpt-4o-mini';
const UI_READY = 60_000;
type Config = { model: string; num_rows: number; dataset: { name?: string; description: string }; [key: string]: unknown };
type Table = { result: { table: { row_id: string; [key: string]: any }[]; column_config: { id: string; name: string }[] } };
// Stored as canonical dataset text: lowercase booleans and JSON documents; the first row is falsy or empty.
const COLUMNS = [['question', 'text'], ['approved', 'boolean'], ['attempts', 'integer'], ['details', 'json'], ['tags', 'array']] as const;
const stored = (description: string, i: number) => [`STANDIN | ${description} | question | ${i}`, i === 1 ? 'false' : 'true',
  String(i - 1), i === 1 ? '{}' : `{"nested": [1, true, null], "label": "${description}"}`, i === 1 ? '[]' : '["a", 2, false]'];

async function open(page: Page) {
  // AddRowDrawer.jsx and importOptions.js.
  await page.getByRole('button', { name: 'Add rows', exact: true }).click();
  await page.getByText('Generate rows', { exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Generate rows', exact: true });
  await expect(dialog.getByRole('button', { name: 'Generate and save' })).toBeVisible({ timeout: UI_READY });
  await expect(dialog.getByText('Loading configuration…')).toHaveCount(0, { timeout: UI_READY });
  await expect(dialog.getByText('Keep these fields as they are')).toHaveCount(0);
  await expect(dialog.getByText('a knowledge base', { exact: true })).toHaveCount(0);
  return dialog;
}

async function readback(scopes: ScopeActors, probe: StateProbe, actor: ScopeActor, id: string,
  description: string, batches: number, perturb?: string) {
  const response = await scopes.send<Table>(actor, 'GET', path(id, 'get-dataset-table'), undefined,
    { current_page_index: 0, page_size: 25 });
  expect(response.status).toBe(200);
  const { table, column_config: columns } = response.body.result;
  expect(columns.map(c => c.name)).toEqual(COLUMNS.map(([name]) => name));
  const ids = columns.map(c => c.id);
  const expected: (string | null)[][] = [COLUMNS.map(() => null), ...Array.from({ length: batches }, () => [1, 2].map(i => stored(description, i))).flat()];
  const values = table.map(row => ids.map(column => row[column].cell_value));
  expect(values).toEqual(perturb === 'values'
    ? expected.map(row => row.map((value, k) => k === 0 && value !== null ? `${value}-wrong` : value)) : expected);
  // Structured cells must parse back to the model's native values.
  for (const row of values.slice(1)) expect([JSON.parse(row[1]), typeof JSON.parse(row[2]), JSON.parse(row[3]), JSON.parse(row[4])])
    .toEqual(row[1] === 'false' ? [false, 'number', {}, []] : [true, 'number', { nested: [1, true, null], label: description }, ['a', 2, false]]);
  expect(await probe.pg('SELECT organization_id, workspace_id FROM model_hub_dataset WHERE id = $1', [id]))
    .toEqual([{ organization_id: actor.organizationId, workspace_id: actor.workspaceId }]);
  const cells = await probe.pg<{ id: string; column_id: string; value: string | null }>(
    'SELECT r.id, c.column_id, c.value FROM model_hub_row r JOIN model_hub_cell c ON c.row_id = r.id WHERE r.dataset_id = $1 AND NOT r.deleted ORDER BY r."order"', [id]);
  expect(cells).toHaveLength(table.length * ids.length);
  const byRow = new Map<string, Record<string, string | null>>();
  for (const cell of cells) byRow.set(cell.id, { ...byRow.get(cell.id), [cell.column_id]: cell.value });
  expect([...byRow].map(([row, saved]) => ({ id: row, values: ids.map(column => saved[column]) })))
    .toEqual(table.map((row, index) => ({ id: row.row_id, values: expected[index] })));
  return { rows: table.map(row => row.row_id), column: ids[0], expected: expected.map(row => row[0]) };
}

test('DATA-E2E-016: generated rows append safely and reuse the saved configuration', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-016', area: 'datasets',
    userGoal: 'Generate and save rows, then append another batch using the same saved configuration without replacing existing data',
    steps: ['configure a labelled loopback LLM STANDIN in each of two synthetic organizations and create an owned dataset',
      'open Add rows and Generate rows, enter a description and confirm Generate and save',
      'reload, reopen and confirm Re-generate with saved configuration',
      'read exact persisted rows and configuration and refuse cross-organization and sibling-workspace requests',
      'generate from a request that omits or blanks the dataset name, then replay its saved configuration'],
    backendChecks: ['exact generated values and row identities agree in API, PostgreSQL and reloaded UI',
      'saved configuration survives reload and regeneration preserves existing rows and columns',
      'foreign organization and sibling workspace requests are refused without writes',
      'a configuration saved without a dataset name is read back under the dataset\'s own name and replays'],
  }),
}, async ({ browser, scopeActors, scopeProbe }, testInfo) => {
  // Provisioning 120 + two actors * six UI_READY steps 720 + isolation/readback 120 seconds.
  test.setTimeout(960_000);
  const perturb = process.env.E2E_GENERATION_PERTURB;
  if (perturb && !['values', 'config', 'isolation'].includes(perturb)) throw new Error('Unknown perturbation');
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  for (const [label, actor, foreign] of [['a', scopeActors.ownerA, scopeActors.ownerB], ['b', scopeActors.ownerB, scopeActors.ownerA]] as const) {
    // Custom model API performs a real validation completion before saving these synthetic credentials.
    const model = await scopeActors.send(actor, 'POST', '/model-hub/custom_models/create/', {
      model_provider: 'openai', model_name: MODEL, input_token_cost: 0, output_token_cost: 0,
      config_json: { key: 'synthetic-standin-only', api_base: 'http://localhost:18082/v1' },
    });
    expect(model.status).toBe(200);
    const name = `e2e-generate-${label}-${suffix}`;
    const description = `refund-${label}-${suffix}`;
    const created = await scopeActors.send<{ result: { dataset_id: string } }>(actor, 'POST', CREATE,
      { dataset_name: name, number_of_rows: 1, columns: COLUMNS.map(([column, type]) => ({ name: column, type, role: column === 'question' ? 'input' : 'metadata' })) });
    expect(created.status).toBe(200);
    const id = created.body.result.dataset_id;
    await testInfo.attach(`generation-${label}`, { contentType: 'application/json', body: JSON.stringify({
      id, name, description, organizationId: actor.organizationId, workspaceId: actor.workspaceId, model: MODEL, llm: 'loopback STANDIN',
    }) });
    const context = await scopeActors.openContext(browser, actor);
    try {
      await context.route('**/*', route => {
        const url = new URL(route.request().url());
        return url.hostname === 'localhost' || ['data:', 'blob:'].includes(url.protocol) ? route.continue() : route.abort('blockedbyclient');
      });
      const page = await context.newPage();
      page.setDefaultTimeout(UI_READY);
      const prototypeRequests: string[] = [];
      page.on('request', request => { if (request.url().includes('/imports/')) prototypeRequests.push(request.url()); });
      await page.goto(`/dashboard/develop/${id}?tab=data`);
      let dialog = await open(page);
      await dialog.getByRole('spinbutton', { name: 'How many' }).fill('2');
      await dialog.getByRole('textbox', { name: 'Description', exact: true }).fill(description);
      await dialog.getByRole('textbox', { name: 'Objective', exact: true }).fill('Support');
      await dialog.getByRole('textbox', { name: 'Patterns', exact: true }).fill('Short');
      await dialog.getByRole('textbox', { name: 'Search Model', exact: true }).click();
      // The model popover marks its parent dialog aria-hidden while the search input remains usable.
      await page.getByLabel('Search Model', { exact: true }).fill(MODEL);
      await page.getByText(MODEL, { exact: true }).last().click();
      const generated = page.waitForResponse(r => new URL(r.url()).pathname === path(id, 'add_synthetic_data') && r.request().method() === 'POST');
      await dialog.getByRole('button', { name: 'Generate and save', exact: true }).click();
      const first = await generated;
      expect(first.status(), JSON.stringify(await first.json())).toBe(200);
      await expect(dialog.getByText('2 generated rows saved. Existing rows are unchanged.', { exact: true })).toBeVisible();
      const firstRows = await readback(scopeActors, scopeProbe, actor, id, description, 1);
      await dialog.getByRole('button', { name: 'Close', exact: true }).click();
      await page.reload();
      dialog = await open(page);
      await expect(dialog.getByRole('textbox', { name: 'Description', exact: true })).toHaveValue(description);
      const saved = await scopeActors.send<{ result: { data: Config } }>(actor, 'GET', path(id, 'synthetic-config'), undefined, { generation_mode: 'append' });
      expect(saved.status).toBe(200);
      expect(saved.body.result.data.model).toBe(perturb === 'config' ? 'incorrect-model' : MODEL);
      expect(saved.body.result.data).toEqual(first.request().postDataJSON());
      // Unsaved edits must not silently change the repeat action's configuration.
      await dialog.getByRole('textbox', { name: 'Description', exact: true }).fill('unsaved-description');
      const regenerated = page.waitForResponse(r => new URL(r.url()).pathname === path(id, 'update-synthetic-config') && r.request().method() === 'PUT');
      await dialog.getByRole('button', { name: 'Re-generate with saved configuration', exact: true }).click();
      const second = await regenerated;
      expect(second.status(), JSON.stringify(await second.json())).toBe(200);
      expect(second.request().postDataJSON()).toEqual({ ...saved.body.result.data, regenerate: true });
      await expect(dialog.getByText('2 generated rows saved. Existing rows are unchanged.', { exact: true })).toBeVisible();
      await dialog.getByRole('button', { name: 'Close', exact: true }).click();
      await page.reload();
      const finalRows = await readback(scopeActors, scopeProbe, actor, id, description, 2, perturb);
      expect(finalRows.rows.slice(0, 3)).toEqual(firstRows.rows);
      expect(finalRows.column).toBe(firstRows.column);
      await expect(page.locator(`.ag-row [col-id="${finalRows.column}"]`)).toHaveText(finalRows.expected.map(v => v ?? 'Empty'), { timeout: UI_READY });
      await testInfo.attach(`generated-${label}`, { contentType: 'image/png', body: await page.screenshot({ fullPage: true }) });
      const outsiders = [foreign];
      if (label === 'a') outsiders.push(scopeActors.withWorkspace(actor, scopeActors.emptyWorkspace.id));
      for (const outsider of outsiders) {
        expect((await scopeActors.send(outsider, 'GET', path(id, 'synthetic-config'), undefined, { generation_mode: 'append' })).status).toBe(404);
        expect((await scopeActors.send(outsider, 'POST', path(id, 'add_synthetic_data'), saved.body.result.data)).status).toBe(perturb === 'isolation' ? 200 : 404);
        expect((await scopeActors.send(outsider, 'PUT', path(id, 'update-synthetic-config'), { ...saved.body.result.data, regenerate: true })).status).toBe(404);
      }
      expect((await readback(scopeActors, scopeProbe, actor, id, description, 2)).rows).toEqual(finalRows.rows);
      // Contract-valid requests may omit or blank the name; the saved configuration names this dataset.
      const requirements = { ...saved.body.result.data.dataset };
      if (label === 'a') delete requirements.name; else requirements.name = '';
      const unnamed = await scopeActors.send(actor, 'POST', path(id, 'add_synthetic_data'), { ...saved.body.result.data, dataset: requirements });
      expect(unnamed.status, JSON.stringify(unnamed.body)).toBe(200);
      const replay = await scopeActors.send<{ result: { data: Config } }>(actor, 'GET', path(id, 'synthetic-config'), undefined, { generation_mode: 'append' });
      expect(replay.status).toBe(200);
      const repeat = await scopeActors.send(actor, 'PUT', path(id, 'update-synthetic-config'), { ...replay.body.result.data, regenerate: true });
      expect(repeat.status, JSON.stringify(repeat.body)).toBe(200);
      expect(replay.body.result.data.dataset.name).toBe(name);
      expect(replay.body.result.data).toEqual(saved.body.result.data);
      expect((await readback(scopeActors, scopeProbe, actor, id, description, 4)).rows.slice(0, 5)).toEqual(finalRows.rows);
      expect(prototypeRequests).toEqual([]);
    } finally { await context.close(); }
  }
});
