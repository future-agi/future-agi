import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { E2E } from '../../lib/env';

// ManualDatasetCreateRequestSerializer and model_hub/urls.py.
const CREATE = '/model-hub/develops/create-dataset-manually/';
// DatasetListQuerySerializer and DatasetDetailsResponseSerializer.
const LIST = '/model-hub/develops/get-datasets/';
const settings = (id: string) => `/model-hub/develops/${id}/edit_dataset_behavior/`;
// DatasetSettingsDialog and DatasetHeaderBar use these accessible names.
const UI_READY = 60_000;
type Details = { id: string; name: string; description: string; tags: string[] };
type Created = { result: { dataset_id: string } };
type Listed = { result: { datasets: Details[]; total_count: number } };

test('DATA-E2E-004: dataset settings persist from the list and header within their tenant', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-004', area: 'datasets',
    userGoal: 'Edit dataset details from either entry point and retain them without exposing another workspace or organization',
    steps: [
      'create synthetic datasets in two organizations and 101 newer datasets ahead of the target',
      'find the target in the list and edit its name, description and tags',
      'open its header settings, cancel an edit, then clear description and tags',
      'reload the dataset and reopen settings to read the cleared details',
      'try the same detail reads and writes from another organization and a sibling workspace',
    ],
    backendChecks: [
      'confirmed list edits persist exact name, description and tags in the scoped Postgres dataset row',
      'detail and list API readback agree after header clear and reload, even outside the first 100 list items',
      'foreign organization and sibling workspace reads and writes are refused and both tenants retain their own details',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // 300s for public seeding + 8 UI_READY (480s) + 60s API/headroom.
  test.setTimeout(840_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const original = `e2e-data4-${suffix}`;
  const renamed = `${original}-renamed`;
  const description = `Regression examples ${suffix}`;
  const foreignName = `e2e-data4-foreign-${suffix}`;
  const a = scopes.ownerA;
  const b = scopes.ownerB;
  const created = await scopes.send<Created>(a, 'POST', CREATE, { dataset_name: original, number_of_rows: 1, number_of_columns: 1 });
  expect(created.status).toBe(200);
  const id = created.body.result.dataset_id;
  const foreign = await scopes.send<Created>(b, 'POST', CREATE, { dataset_name: foreignName, number_of_rows: 1, number_of_columns: 1 });
  expect(foreign.status).toBe(200);
  const foreignId = foreign.body.result.dataset_id;
  expect((await scopes.send(b, 'PUT', settings(foreignId), { description: 'Private foreign details', tags: ['private'] })).status).toBe(200);
  const fillers: string[] = [];
  for (let n = 0; n < 101; n += 1) {
    const filler = await scopes.send<Created>(a, 'POST', CREATE, { dataset_name: `e2e-data4-newer-${suffix}-${n}`, number_of_rows: 1, number_of_columns: 1 });
    expect(filler.status).toBe(200);
    fillers.push(filler.body.result.dataset_id);
  }
  await testInfo.attach('dataset-settings-identities', { contentType: 'application/json', body: JSON.stringify({ id, foreignId, original, renamed, description, fillers, organizationId: a.organizationId, workspaceId: a.workspaceId }) });
  const firstPage = await a.api.get<Listed>(LIST, { page: 0, page_size: 100 });
  expect(firstPage.result.datasets.map(row => row.id)).not.toContain(id);
  expect(firstPage.result.total_count).toBe(102);

  const context = await scopes.openContext(browser, a);
  // Block external resources, not product requests: every API uses the real loopback stack.
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    return url.hostname === 'localhost' || url.hostname === '127.0.0.1'
      ? route.continue() : route.abort('blockedbyclient');
  });
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
  await page.getByRole('textbox', { name: 'Search datasets', exact: true }).fill(original);
  await page.getByRole('button', { name: `${original} actions`, exact: true }).click();
  await expect(page.getByRole('menuitem', { name: /^Metadata/ })).toHaveCount(0);
  await page.getByRole('menuitem', { name: /^Edit details/ }).click();
  const dialog = page.getByRole('dialog', { name: 'Dataset settings', exact: true });
  await expect(dialog.getByRole('textbox', { name: /Dataset name/ })).toHaveValue(original, { timeout: UI_READY });
  await expect(dialog.getByText('Access', { exact: true })).toHaveCount(0);
  await dialog.getByRole('textbox', { name: /Dataset name/ }).fill(renamed);
  await dialog.getByRole('textbox', { name: 'Description', exact: true }).fill(description);
  await dialog.getByRole('combobox', { name: 'Tags', exact: true }).fill('golden');
  const saved = page.waitForResponse(r => new URL(r.url()).pathname === settings(id) && r.request().method() === 'PUT', { timeout: UI_READY });
  await dialog.getByRole('button', { name: 'Save changes', exact: true }).click();
  const saveResponse = await saved;
  expect(saveResponse.status()).toBe(200);
  expect(saveResponse.request().postDataJSON()).toEqual({ dataset_name: renamed, description, tags: ['golden'] });
  await expect(dialog).toHaveCount(0, { timeout: UI_READY });

  await test.step('backend check 1: exact persisted scoped details', async () => {
    const stored = { id, name: renamed, description, tags: ['golden'] };
    expect(await probe.pg<{ id: string; name: string; description: string; tags: string[]; organization_id: string; workspace_id: string }>(
      'SELECT id, name, description, tags, organization_id, workspace_id FROM model_hub_dataset WHERE id = $1 AND NOT deleted', [id]))
      .toEqual([{ ...stored, organization_id: a.organizationId, workspace_id: a.workspaceId }]);
    expect((await a.api.get<{ result: Details }>(settings(id))).result).toEqual(stored);
    const list = await a.api.get<Listed>(LIST, { search_text: renamed, page: 0, page_size: 25 });
    expect(list.result.datasets.map(({ id: rowId, name, description: d, tags }) => ({ id: rowId, name, description: d, tags }))).toEqual([stored]);
  });

  await page.goto(`/dashboard/develop/${id}?tab=data`, { waitUntil: 'domcontentloaded' });
  const more = page.getByRole('button', { name: 'More dataset actions', exact: true });
  await expect(more).toBeVisible({ timeout: UI_READY });
  // Sharing and access controls are separate surfaces that stay hidden.
  await expect(page.getByRole('button', { name: /^Share/ })).toHaveCount(0);
  await more.click();
  await expect(page.getByRole('menuitem', { name: /^Share/ })).toHaveCount(0);
  // The menu row's name includes its secondary line.
  await page.getByRole('menuitem', { name: /^Dataset settings/ }).click();
  await expect(dialog.getByRole('textbox', { name: 'Description', exact: true })).toHaveValue(description, { timeout: UI_READY });
  await dialog.getByRole('textbox', { name: 'Description', exact: true }).fill('Cancelled edit');
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(dialog).toHaveCount(0, { timeout: UI_READY });
  expect((await a.api.get<{ result: Details }>(settings(id))).result.description).toBe(description);
  await more.click();
  await page.getByRole('menuitem', { name: /^Dataset settings/ }).click();
  await expect(dialog.getByRole('textbox', { name: 'Description', exact: true })).toHaveValue(description, { timeout: UI_READY });
  await dialog.getByRole('textbox', { name: 'Description', exact: true }).fill('');
  // The Tags clear button is visible only while the field has focus.
  await dialog.getByRole('combobox', { name: 'Tags', exact: true }).click();
  await dialog.getByRole('button', { name: 'Clear', exact: true }).click();
  const cleared = page.waitForResponse(r => new URL(r.url()).pathname === settings(id) && r.request().method() === 'PUT', { timeout: UI_READY });
  await dialog.getByRole('button', { name: 'Save changes', exact: true }).click();
  const clearResponse = await cleared;
  expect(clearResponse.status()).toBe(200);
  expect(clearResponse.request().postDataJSON()).toEqual({ dataset_name: renamed, description: '', tags: [] });
  await expect(dialog).toHaveCount(0, { timeout: UI_READY });
  await page.reload({ waitUntil: 'domcontentloaded' });

  await test.step('backend check 2: detail and list readback after clearing', async () => {
    const expected = { id, name: renamed, description: '', tags: [] };
    expect((await a.api.get<{ result: Details }>(settings(id))).result).toEqual(expected);
    const list = await a.api.get<Listed>(LIST, { search_text: renamed, page: 0, page_size: 25 });
    expect(list.result.datasets.map(({ id: rowId, name, description: d, tags }) => ({ id: rowId, name, description: d, tags }))).toEqual([expected]);
    expect(await probe.pg<{ description: string; tags: string[] }>('SELECT description, tags FROM model_hub_dataset WHERE id = $1', [id])).toEqual([{ description: '', tags: [] }]);
  });

  await more.click();
  await page.getByRole('menuitem', { name: /^Dataset settings/ }).click();
  await expect(dialog.getByRole('textbox', { name: 'Description', exact: true })).toHaveValue('', { timeout: UI_READY });
  await expect(dialog.getByRole('textbox', { name: /Dataset name/ })).toHaveValue(renamed);
  await expect(dialog.getByText('golden', { exact: true })).toHaveCount(0);

  await test.step('backend check 3: cross-tenant and sibling-workspace refusal', async () => {
    for (const other of [b, scopes.withWorkspace(a, scopes.emptyWorkspace.id)]) {
      const deniedRead = await scopes.send(other, 'GET', settings(id));
      expect(deniedRead.status).toBe(404);
      expect((await scopes.send(other, 'PUT', settings(id), { description: 'Foreign write', tags: ['foreign'] })).status).toBe(404);
      expect((await other.api.get<Listed>(LIST, { search_text: renamed })).result.datasets).toEqual([]);
    }
    expect((await scopes.send(a, 'GET', settings(foreignId))).status).toBe(404);
    expect((await scopes.send(a, 'PUT', settings(foreignId), { description: 'Foreign write', tags: [] })).status).toBe(404);
    expect((await a.api.get<{ result: Details }>(settings(id))).result).toEqual({ id, name: renamed, description: '', tags: [] });
    expect((await b.api.get<{ result: Details }>(settings(foreignId))).result).toEqual({ id: foreignId, name: foreignName, description: 'Private foreign details', tags: ['private'] });
    const invalid = await scopes.send(a, 'PUT', settings(id), { dataset_name: 'must-not-save', description: 'x'.repeat(501) });
    expect(invalid.status).toBe(400);
    expect((await a.api.get<{ result: Details }>(settings(id))).result).toEqual({ id, name: renamed, description: '', tags: [] });
  });
  await testInfo.attach('dataset-settings-page', { body: await page.screenshot(), contentType: 'image/png' });
  await testInfo.attach('dataset-settings-origin', { body: JSON.stringify({ api: E2E.apiUrl, app: E2E.appUrl, browser: browser.browserType().name() }), contentType: 'application/json' });
  await context.close();
});
