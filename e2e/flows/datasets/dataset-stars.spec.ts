import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { E2E } from '../../lib/env';

// ManualDatasetCreateRequestSerializer and model_hub/urls.py.
const CREATE = '/model-hub/develops/create-dataset-manually/';
// DatasetListQuerySerializer and DatasetStarView: personal state, no user in the body.
const LIST = '/model-hub/develops/get-datasets/';
const starPath = (id: string) => `/model-hub/datasets/${id}/star/`;
const settings = (id: string) => `/model-hub/develops/${id}/edit_dataset_behavior/`;
// DevelopView and DatasetStarToggle expose these controls by accessible name.
const UI_READY = 60_000;
type Metadata = Record<string, string | number | boolean>;
type Created = { result: { dataset_id: string } };
type Listed = { result: { datasets: { id: string; name: string; starred: boolean; metadata: Metadata }[]; total_count: number; total_pages: number } };
type Details = { result: { metadata: Metadata } };

test('DATA-E2E-006: personal dataset stars and typed metadata persist without widening access', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-006', area: 'datasets',
    userGoal: 'Keep personal favorites and save typed dataset metadata without changing another person’s stars or exposing another workspace',
    steps: [
      'create synthetic datasets in two organizations and newer unstarred alternatives',
      'star a dataset in the list and reload its persisted state',
      'select Starred and compare the exact UI and API results with pagination and search',
      'star and unstar as a second user in the same workspace without changing the first user’s star',
      'refuse foreign organization and sibling workspace star writes',
      'open Metadata with explicit server support, save typed rows, reopen and remove a key',
      'verify metadata merge, settings/list readback and unchanged state after foreign organization or workspace requests',
      'unstar through the row menu and confirm the filtered list is empty after reload',
    ],
    backendChecks: [
      'the browser star write persists exactly one personal dataset-star row and repeated POSTs are idempotent',
      'the Starred list filters before pagination, preserves search and matches the exact browser row set',
      'a same-workspace viewer has independent star state and cannot remove the owner’s personal star',
      'foreign organization and sibling workspace star writes are refused without changing either tenant’s stars',
      'typed metadata persists in Postgres and settings/list reads, with null deletion and omitted/empty patch preservation',
      'foreign organization and sibling workspace metadata reads/writes are refused, preserving both datasets',
      'browser unstar deletes the personal row and the Starred API remains empty after reload',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Existing star walk plus metadata dialogs, reloads and scoped API readback.
  test.setTimeout(900_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const name = `e2e-data5-${suffix}`;
  const a = scopes.ownerA;
  const b = scopes.ownerB;
  const viewer = scopes.viewer;
  const created = await scopes.send<Created>(a, 'POST', CREATE, { dataset_name: name, number_of_rows: 1, number_of_columns: 1 });
  expect(created.status).toBe(200);
  const id = created.body.result.dataset_id;
  const decoys: string[] = [];
  for (let n = 0; n < 3; n += 1) {
    const decoy = await scopes.send<Created>(a, 'POST', CREATE, { dataset_name: `${name}-newer-${n}`, number_of_rows: 1, number_of_columns: 1 });
    expect(decoy.status).toBe(200);
    decoys.push(decoy.body.result.dataset_id);
  }
  const foreign = await scopes.send<Created>(b, 'POST', CREATE, { dataset_name: `${name}-foreign`, number_of_rows: 1, number_of_columns: 1 });
  expect(foreign.status).toBe(200);
  const foreignId = foreign.body.result.dataset_id;
  await testInfo.attach('dataset-star-identities', { contentType: 'application/json', body: JSON.stringify({ id, foreignId, decoys, name, ownerId: a.userId, viewerId: viewer.userId, organizationId: a.organizationId, workspaceId: a.workspaceId }) });
  expect((await a.api.get<Listed>(LIST, { page_size: 1 })).result.datasets.map(row => row.id)).not.toContain(id);
  const storedStars = () => probe.pg<{ dataset_id: string; user_id: string }>(
    'SELECT dataset_id, user_id FROM model_hub_datasetstar WHERE dataset_id = $1 ORDER BY user_id', [id]);
  const storedMetadata = (datasetId: string) => probe.pg<{ metadata: Metadata }>(
    'SELECT metadata FROM model_hub_dataset WHERE id = $1', [datasetId]);

  // Require the real service answer; never patch capabilities or seed an override.
  const capabilities = await a.api.get<{ features: { dataset_metadata?: { allowed?: boolean } } }>('/api/capabilities/');
  expect(capabilities.features.dataset_metadata?.allowed).toBe(true);

  const context = await scopes.openContext(browser, a);
  await context.route('**/*', route => {
    const host = new URL(route.request().url()).hostname;
    return host === 'localhost' || host === '127.0.0.1' ? route.continue() : route.abort('blockedbyclient');
  });
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
  await page.getByRole('textbox', { name: 'Search datasets', exact: true }).fill(name);
  const toggle = page.getByRole('button', { name: `Star ${name}`, exact: true });
  await expect(toggle).toHaveAttribute('aria-pressed', 'false', { timeout: UI_READY });
  const posted = page.waitForResponse(r => new URL(r.url()).pathname === starPath(id) && r.request().method() === 'POST', { timeout: UI_READY });
  await toggle.click();
  expect((await posted).status()).toBe(200);

  await test.step('backend check 1: unique persisted personal star', async () => {
    expect(await storedStars()).toEqual([{ dataset_id: id, user_id: a.userId }]);
    for (let n = 0; n < 2; n += 1) expect((await scopes.send(a, 'POST', starPath(id))).status).toBe(200);
    expect(await storedStars()).toEqual([{ dataset_id: id, user_id: a.userId }]);
  });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(toggle).toHaveAttribute('aria-pressed', 'true', { timeout: UI_READY });
  // The star filter is the Folders rail's Starred row, named with its count
  // and marked aria-current once picked (frontend/src/sections/develop/organize/FolderRail.jsx).
  const filter = page.getByRole('navigation', { name: 'Folders', exact: true }).getByRole('button', { name: /^Starred\s*1$/ });
  await expect(filter).toBeVisible({ timeout: UI_READY });
  const filtered = page.waitForResponse(r => {
    const url = new URL(r.url());
    return url.pathname === LIST && url.searchParams.get('starred') === 'true';
  }, { timeout: UI_READY });
  await filter.click();
  expect((await filtered).status()).toBe(200);
  await expect(filter).toHaveAttribute('aria-current', 'true');

  await test.step('backend check 2: exact filtered UI/API set', async () => {
    await expect(page.getByRole('button', { name: /^Star e2e-data5-/ })).toHaveCount(1, { timeout: UI_READY });
    await expect(toggle).toHaveAttribute('aria-pressed', 'true');
    const selected = await a.api.get<Listed>(LIST, { starred: 'true', page_size: 1, search_text: name });
    expect(selected.result.datasets.map(row => ({ id: row.id, starred: row.starred }))).toEqual([{ id, starred: true }]);
    expect(selected.result.total_count).toBe(1);
    expect(selected.result.total_pages).toBe(1);
    expect((await a.api.get<Listed>(LIST, { starred: 'true', page_size: 1, page: 1 })).result.datasets).toEqual([]);
    expect((await a.api.get<Listed>(LIST, { starred: 'true', search_text: `${name}-newer` })).result.datasets).toEqual([]);
    expect((await a.api.get<Listed>(LIST, { starred: 'false' })).result.total_count).toBe(4);
  });

  await test.step('backend check 3: personal viewer state', async () => {
    expect((await viewer.api.get<Listed>(LIST, { starred: 'true' })).result.datasets).toEqual([]);
    expect((await viewer.api.get<Listed>(LIST, { search_text: name })).result.datasets.every(row => row.starred === false)).toBe(true);
    expect((await scopes.send(viewer, 'POST', starPath(id))).status).toBe(200);
    expect((await viewer.api.get<Listed>(LIST, { starred: 'true' })).result.datasets.map(row => row.id)).toEqual([id]);
    expect((await scopes.send(viewer, 'DELETE', starPath(id))).status).toBe(200);
    expect((await viewer.api.get<Listed>(LIST, { starred: 'true' })).result.datasets).toEqual([]);
    expect(await storedStars()).toEqual([{ dataset_id: id, user_id: a.userId }]);
    expect((await a.api.get<Listed>(LIST, { starred: 'true' })).result.datasets.map(row => row.id)).toEqual([id]);
    const sharedWrite = await scopes.send(viewer, 'PUT', `/model-hub/develops/${id}/edit_dataset_behavior/`, { description: 'must-not-save' });
    expect(sharedWrite.status).toBe(403);
  });

  await test.step('backend check 4: tenant and workspace denial', async () => {
    expect((await scopes.send(b, 'POST', starPath(foreignId))).status).toBe(200);
    for (const other of [b, scopes.withWorkspace(a, scopes.emptyWorkspace.id)]) {
      for (const method of ['POST', 'DELETE']) {
        const denied = await scopes.send(other, method, starPath(id));
        expect(denied.status).toBe(404);
      }
      expect((await other.api.get<Listed>(LIST, { search_text: name })).result.datasets.map(row => row.id)).not.toContain(id);
    }
    expect((await scopes.send(a, 'POST', starPath(foreignId))).status).toBe(404);
    expect((await scopes.send(a, 'DELETE', starPath(foreignId))).status).toBe(404);
    expect((await b.api.get<Listed>(LIST, { starred: 'true' })).result.datasets.map(row => row.id)).toEqual([foreignId]);
    expect(await storedStars()).toEqual([{ dataset_id: id, user_id: a.userId }]);
  });

  const openMetadata = async () => {
    await page.getByRole('button', { name: `${name} actions`, exact: true }).click();
    // Agents remain independently unavailable; making Metadata real does not
    // admit another organize action.
    const agents = page.getByRole('menuitem', { name: /^Agents/ });
    await expect(agents).toBeDisabled();
    await expect(agents).toHaveAccessibleDescription('Not available in this release');
    const metadata = page.getByRole('menuitem', { name: /^Metadata/ });
    await expect(metadata).toBeEnabled({ timeout: UI_READY });
    await metadata.click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText(`Metadata of ${name}`);
    return dialog;
  };
  const saveMetadata = async () => {
    const saved = page.waitForResponse(r => new URL(r.url()).pathname === settings(id) && r.request().method() === 'PUT', { timeout: UI_READY });
    await page.getByRole('button', { name: 'Save metadata', exact: true }).click();
    const response = await saved;
    expect(response.status()).toBe(200);
    await expect(page.getByRole('dialog')).toHaveCount(0);
    return response.request().postDataJSON();
  };
  const readMetadata = async (expected: Metadata) => {
    expect((await a.api.get<Details>(settings(id))).result.metadata).toEqual(expected);
    const listed = await a.api.get<Listed>(LIST, { starred: 'true', search_text: name });
    expect(listed.result.datasets.map(row => ({ id: row.id, metadata: row.metadata }))).toEqual([{ id, metadata: expected }]);
    expect(await storedMetadata(id)).toEqual([{ metadata: expected }]);
  };

  await test.step('metadata: typed rows persist and reopen after list reload', async () => {
    expect((await a.api.get<Details>(settings(id))).result.metadata).toEqual({});
    const dialog = await openMetadata();
    const pairs = [['code', '"2"'], ['score', '2.5'], ['ready', 'true'], ['keep', 'original'], ['remove', 'old']];
    for (let i = 0; i < pairs.length; i += 1) {
      if (i) await dialog.getByRole('button', { name: 'Add', exact: true }).click();
      await dialog.getByLabel(`Key ${i + 1}`, { exact: true }).fill(pairs[i][0]);
      await dialog.getByLabel(`Value ${i + 1}`, { exact: true }).fill(pairs[i][1]);
    }
    const initial = { code: '2', score: 2.5, ready: true, keep: 'original', remove: 'old' };
    expect(await saveMetadata()).toEqual({ metadata: initial });
    await readMetadata(initial);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(toggle).toHaveAttribute('aria-pressed', 'true', { timeout: UI_READY });
    const reopened = await openMetadata();
    // Metadata rows are sorted by key on reopen; numeric-looking text stays quoted.
    await expect(reopened.getByLabel('Key 1', { exact: true })).toHaveValue('code');
    await expect(reopened.getByLabel('Value 1', { exact: true })).toHaveValue('"2"');
    await expect(reopened.getByLabel('Value 3', { exact: true })).toHaveValue('true');
    await expect(reopened.getByLabel('Value 5', { exact: true })).toHaveValue('2.5');
    await reopened.getByLabel('Value 5', { exact: true }).fill('3');
    await reopened.getByRole('button', { name: 'Remove remove', exact: true }).click();
    expect(await saveMetadata()).toEqual({ metadata: { score: 3, remove: null } });
    await readMetadata({ code: '2', score: 3, ready: true, keep: 'original' });
  });

  const expectedMetadata = { code: '2', score: 3, ready: true, keep: 'original', reviewed: false };
  await test.step('metadata: merge and scoped refusal leave unrelated keys and tenants unchanged', async () => {
    expect((await scopes.send(a, 'PUT', settings(id), { metadata: { reviewed: false } })).status).toBe(200);
    for (const body of [{}, { metadata: {} }]) {
      expect((await scopes.send(a, 'PUT', settings(id), body)).status).toBe(200);
      await readMetadata(expectedMetadata);
    }
    const foreignBefore = (await b.api.get<Details>(settings(foreignId))).result.metadata;
    for (const other of [b, scopes.withWorkspace(a, scopes.emptyWorkspace.id)]) {
      expect((await scopes.send(other, 'GET', settings(id))).status).toBe(404);
      expect((await scopes.send(other, 'PUT', settings(id), { metadata: { keep: 'forbidden', score: null } })).status).toBe(404);
      expect((await other.api.get<Listed>(LIST, { search_text: name })).result.datasets.map(row => row.id)).not.toContain(id);
    }
    expect((await scopes.send(viewer, 'PUT', settings(id), { metadata: { keep: 'forbidden' } })).status).toBe(403);
    expect((await scopes.send(a, 'GET', settings(foreignId))).status).toBe(404);
    expect((await scopes.send(a, 'PUT', settings(foreignId), { metadata: { keep: 'forbidden' } })).status).toBe(404);
    await readMetadata(expectedMetadata);
    expect((await b.api.get<Details>(settings(foreignId))).result.metadata).toEqual(foreignBefore);
    expect(await storedMetadata(foreignId)).toEqual([{ metadata: foreignBefore }]);
    expect(await storedStars()).toEqual([{ dataset_id: id, user_id: a.userId }]);
  });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(filter).toHaveAttribute('aria-current', 'true');
  const finalDialog = await openMetadata();
  await expect(finalDialog.getByLabel('Key 4', { exact: true })).toHaveValue('reviewed');
  await expect(finalDialog.getByLabel('Value 4', { exact: true })).toHaveValue('false');
  await expect(finalDialog.getByRole('button', { name: 'Remove remove', exact: true })).toHaveCount(0);
  await finalDialog.getByRole('button', { name: 'Cancel', exact: true }).click();

  await page.getByRole('button', { name: `${name} actions`, exact: true }).click();
  const deleted = page.waitForResponse(r => new URL(r.url()).pathname === starPath(id) && r.request().method() === 'DELETE', { timeout: UI_READY });
  await page.getByRole('menuitem', { name: 'Unstar', exact: true }).click();
  expect((await deleted).status()).toBe(200);
  await test.step('backend check 5: persisted unstar and reload', async () => {
    expect(await storedStars()).toEqual([]);
    await expect(page.getByText('No starred datasets match', { exact: true })).toBeVisible({ timeout: UI_READY });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByText('No starred datasets match', { exact: true })).toBeVisible({ timeout: UI_READY });
    expect((await a.api.get<Listed>(LIST, { starred: 'true' })).result.datasets).toEqual([]);
    for (let n = 0; n < 2; n += 1) expect((await scopes.send(a, 'DELETE', starPath(id))).status).toBe(200);
    expect(await storedStars()).toEqual([]);
  });
  await testInfo.attach('dataset-stars-page', { body: await page.screenshot(), contentType: 'image/png' });
  await testInfo.attach('dataset-stars-origin', { body: JSON.stringify({ api: E2E.apiUrl, app: E2E.appUrl, browser: browser.browserType().name() }), contentType: 'application/json' });
  await context.close();
});
