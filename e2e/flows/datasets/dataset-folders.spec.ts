import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// ManualDatasetCreateRequestSerializer and the dataset folder public serializers.
const CREATE = '/model-hub/develops/create-dataset-manually/';
const FOLDERS = '/model-hub/dataset-folders/';
const MOVE = `${FOLDERS}move-datasets/`;
const LIST = '/model-hub/develops/get-datasets/';
const folderUrl = (id: string) => `${FOLDERS}${id}/`;
// DevelopView, FolderRail, and the real folder dialogs expose these accessible names.
const UI_READY = 60_000;
type Created = { result: { dataset_id: string } };
type Folder = { id: string; path: string; count: number; total: number };
type Inventory = { result: { folders: Folder[]; all_count: number; no_folder_count: number } };
type Listed = { result: { datasets: { id: string; folder: string; folder_id: string | null }[]; total_count: number } };

test('DATA-E2E-020: workspace folders organize datasets without widening access', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-020', area: 'datasets',
    userGoal: 'Organize datasets into persistent folders by selection or drag and keep other workspaces private',
    steps: [
      'create datasets in two synthetic organizations and create an empty nested folder in the browser',
      'move a selected dataset through Organize and compare the folder filter with persisted API state',
      'drag another dataset into the folder and reload',
      'rename the folder and move its parent while preserving dataset membership',
      'refuse viewer writes and foreign organization or sibling workspace targets',
      'remove the folder subtree while retaining every dataset',
    ],
    backendChecks: [
      'empty nested folders persist with the caller organization and workspace',
      'Organize writes the folder foreign key and folder filtering matches the exact UI and API set before pagination',
      'drag writes the second dataset membership and survives reload',
      'rename and parent move preserve folder identities and relocate the entire subtree',
      'viewer and cross-scope mutations are refused without partial writes or metadata and agent activation',
      'folder removal clears memberships but never deletes datasets',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Synthetic provisioning 120s + 12 UI_READY stages (720s) + synchronous API/PG reads 60s.
  test.setTimeout(900_000);
  const stem = `e2e-folders-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const path = `${stem}/incoming`;
  const renamed = `${stem}/triage`;
  const archive = `${stem}-archive`;
  const a = scopes.ownerA;
  const b = scopes.ownerB;
  const ids: string[] = [];
  const names = [`${stem}-alpha`, `${stem}-beta`, `${stem}-outside`];
  for (const name of names) {
    const response = await scopes.send<Created>(a, 'POST', CREATE, { dataset_name: name, number_of_rows: 1, number_of_columns: 1 });
    expect(response.status).toBe(200);
    ids.push(response.body.result.dataset_id);
  }
  const foreign = await scopes.send<Created>(b, 'POST', CREATE, { dataset_name: `${stem}-foreign`, number_of_rows: 1, number_of_columns: 1 });
  expect(foreign.status).toBe(200);
  const foreignId = foreign.body.result.dataset_id;
  await testInfo.attach('folder-identities', { contentType: 'application/json', body: JSON.stringify({ ids, names, foreignId, organization: a.organizationId, workspace: a.workspaceId, path, renamed, archive }) });
  const stored = (id: string) => probe.pg<{ folder_id: string | null; deleted: boolean }>('SELECT folder_id, deleted FROM model_hub_dataset WHERE id = $1 AND organization_id = $2', [id, a.organizationId]);
  const context = await scopes.openContext(browser, a);
  await context.route('**/*', route => ['localhost', '127.0.0.1'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort('blockedbyclient'));
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'New folder', exact: true }).click();
  await page.getByRole('textbox', { name: 'Folder path', exact: true }).fill(`${stem}//incoming`);
  const invalidCreate = page.waitForResponse(r => new URL(r.url()).pathname === FOLDERS && r.request().method() === 'POST', { timeout: UI_READY });
  await page.getByRole('button', { name: 'Save folder', exact: true }).click();
  expect((await invalidCreate).status()).toBe(400);
  await expect(page.getByRole('alert')).toHaveText('path: Use up to 10 nonempty folder names, at most 80 characters each.', { timeout: UI_READY });
  await page.getByRole('textbox', { name: 'Folder path', exact: true }).fill(path);
  const creating = page.waitForResponse(r => new URL(r.url()).pathname === FOLDERS && r.request().method() === 'POST', { timeout: UI_READY });
  await page.getByRole('button', { name: 'Save folder', exact: true }).click();
  const created = await creating;
  expect(created.status()).toBe(201);
  expect(created.request().postDataJSON()).toEqual({ path });
  const folder: Folder = (await created.json()).result;
  const inventory = (await a.api.get<Inventory>(FOLDERS)).result;
  const parent = inventory.folders.find(f => f.path === stem)!;
  await testInfo.attach('persisted-folder-ids', { contentType: 'application/json', body: JSON.stringify({ folder, parent }) });
  await test.step('backend check 1: empty folder persistence', async () => {
    expect(inventory.folders.map(f => ({ path: f.path, count: f.count, total: f.total }))).toEqual([{ path: stem, count: 0, total: 0 }, { path, count: 0, total: 0 }]);
    expect(await probe.pg('SELECT path, organization_id, workspace_id FROM model_hub_datasetfolder WHERE id = $1', [folder.id])).toEqual([{ path, organization_id: a.organizationId, workspace_id: a.workspaceId }]);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('navigation', { name: 'Folders' }).getByText('incoming', { exact: true })).toBeVisible({ timeout: UI_READY });
  });

  await page.getByRole('row').filter({ hasText: names[0] }).getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Organize', exact: true }).click();
  await expect(page.getByRole('menuitem', { name: /^Metadata/ })).toHaveCount(0);
  await expect(page.getByRole('menuitem', { name: /^Agents/ })).toHaveCount(0);
  await page.getByRole('menuitem', { name: 'Move to folder…', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Move to folder', exact: true });
  await dialog.getByText(path, { exact: true }).click();
  const moving = page.waitForResponse(r => new URL(r.url()).pathname === MOVE && r.request().method() === 'POST', { timeout: UI_READY });
  await dialog.getByRole('button', { name: 'Move', exact: true }).click();
  const moved = await moving;
  expect(moved.status()).toBe(200);
  expect(moved.request().postDataJSON()).toEqual({ dataset_ids: [ids[0]], folder_id: folder.id });
  await test.step('backend check 2: organize and folder filter', async () => {
    expect(await stored(ids[0])).toEqual([{ folder_id: folder.id, deleted: false }]);
    const filtering = page.waitForResponse(r => new URL(r.url()).pathname === LIST && new URL(r.url()).searchParams.get('folder_path') === path, { timeout: UI_READY });
    await page.getByRole('navigation', { name: 'Folders' }).getByText('incoming', { exact: true }).click();
    expect((await filtering).status()).toBe(200);
    await expect(page.getByRole('button', { name: /^Drag e2e-folders-/ })).toHaveCount(1, { timeout: UI_READY });
    await expect(page.getByRole('button', { name: `Drag ${names[0]} to a folder`, exact: true })).toBeVisible();
    const listed = await a.api.get<Listed>(LIST, { folder_path: stem, page_size: 1 });
    expect(listed.result.datasets.map(row => ({ id: row.id, folder_id: row.folder_id, folder: row.folder }))).toEqual([{ id: ids[0], folder_id: folder.id, folder: path }]);
    expect(listed.result.total_count).toBe(1);
    expect((await a.api.get<Listed>(LIST, { folder_path: stem, page_size: 1, page: 1 })).result.datasets).toEqual([]);
    expect((await a.api.get<Listed>(LIST, { folder_path: stem, search_text: names[1] })).result.datasets).toEqual([]);
  });

  await page.getByRole('navigation', { name: 'Folders' }).getByText('All datasets', { exact: true }).click();
  const handle = page.getByRole('button', { name: `Drag ${names[1]} to a folder`, exact: true });
  await expect(handle).toBeVisible({ timeout: UI_READY });
  // Existing product drop hook in FolderDnd.jsx; @dnd-kit uses pointer movement, not HTML5 drag events.
  const target = page.getByRole('navigation', { name: 'Folders' }).locator(`[data-drop-path="${path}"]`);
  const from = (await handle.boundingBox())!;
  const to = (await target.boundingBox())!;
  const dragged = page.waitForResponse(r => new URL(r.url()).pathname === MOVE && r.request().method() === 'POST', { timeout: UI_READY });
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(from.x + from.width / 2 + 8, from.y + from.height / 2, { steps: 4 });
  await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 16 });
  await page.mouse.up();
  expect((await dragged).status()).toBe(200);
  await test.step('backend check 3: persisted drag', async () => {
    expect(await stored(ids[1])).toEqual([{ folder_id: folder.id, deleted: false }]);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.getByRole('navigation', { name: 'Folders' }).getByText('incoming', { exact: true }).click();
    await expect(page.getByRole('button', { name: /^Drag e2e-folders-/ })).toHaveCount(2, { timeout: UI_READY });
    expect((await a.api.get<Listed>(LIST, { folder_path: path })).result.datasets.map(row => row.id).sort()).toEqual(ids.slice(0, 2).sort());
  });

  await page.getByRole('button', { name: `Folder actions for ${path}`, exact: true }).click();
  await page.getByRole('menuitem', { name: 'Rename folder…', exact: true }).click();
  await page.getByRole('textbox', { name: 'Folder path', exact: true }).fill(renamed);
  const renaming = page.waitForResponse(r => new URL(r.url()).pathname === folderUrl(folder.id) && r.request().method() === 'PATCH', { timeout: UI_READY });
  await page.getByRole('button', { name: 'Save folder', exact: true }).click();
  expect((await renaming).status()).toBe(200);
  await page.getByRole('button', { name: `Folder actions for ${stem}`, exact: true }).click();
  await page.getByRole('menuitem', { name: 'Move folder to…', exact: true }).click();
  await page.getByRole('textbox', { name: 'Folder path', exact: true }).fill(archive);
  const relocating = page.waitForResponse(r => new URL(r.url()).pathname === folderUrl(parent.id) && r.request().method() === 'PATCH', { timeout: UI_READY });
  await page.getByRole('button', { name: 'Save folder', exact: true }).click();
  expect((await relocating).status()).toBe(200);
  await test.step('backend check 4: stable subtree rename and move', async () => {
    expect((await a.api.get<Inventory>(FOLDERS)).result.folders.map(f => ({ id: f.id, path: f.path, total: f.total }))).toEqual([{ id: parent.id, path: archive, total: 2 }, { id: folder.id, path: `${archive}/triage`, total: 2 }]);
    expect(await stored(ids[0])).toEqual([{ folder_id: folder.id, deleted: false }]);
    expect((await a.api.get<Listed>(LIST, { folder_path: stem })).result.datasets).toEqual([]);
    // The selected subtree follows its stable identity; no manual re-selection.
    await expect(page.getByRole('button', { name: /^Drag e2e-folders-/ })).toHaveCount(2, { timeout: UI_READY });
  });

  await test.step('backend check 5: access and tenant gates', async () => {
    const otherFolder = await scopes.send<{ result: Folder }>(b, 'POST', FOLDERS, { path: archive });
    expect(otherFolder.status).toBe(201);
    expect((await scopes.send(b, 'POST', MOVE, { dataset_ids: [foreignId], folder_id: otherFolder.body.result.id })).status).toBe(200);
    for (const other of [b, scopes.withWorkspace(a, scopes.emptyWorkspace.id)]) {
      expect((await scopes.send(other, 'GET', folderUrl(folder.id))).status).toBe(404);
      expect((await scopes.send(other, 'PATCH', folderUrl(parent.id), { path: 'stolen' })).status).toBe(404);
      expect((await scopes.send(other, 'DELETE', folderUrl(parent.id))).status).toBe(404);
      expect((await scopes.send(other, 'POST', MOVE, { dataset_ids: [ids[0]], folder_id: null })).status).toBe(404);
    }
    expect((await scopes.send(a, 'POST', MOVE, { dataset_ids: [ids[0]], folder_id: otherFolder.body.result.id })).status).toBe(404);
    expect((await scopes.send(a, 'POST', MOVE, { dataset_ids: [ids[0], foreignId], folder_id: null })).status).toBe(404);
    for (const [method, url, body] of [
      ['POST', FOLDERS, { path: 'viewer-forbidden' }],
      ['PATCH', folderUrl(folder.id), { path: 'viewer-forbidden' }],
      ['DELETE', folderUrl(folder.id), undefined],
      ['POST', MOVE, { dataset_ids: [ids[0]], folder_id: null }],
    ] as const) expect((await scopes.send(scopes.viewer, method, url, body)).status).toBe(403);
    expect(await stored(ids[0])).toEqual([{ folder_id: folder.id, deleted: false }]);
    expect((await b.api.get<Listed>(LIST)).result.datasets.map(row => ({ id: row.id, folder_id: row.folder_id }))).toEqual([{ id: foreignId, folder_id: otherFolder.body.result.id }]);
    const viewerContext = await scopes.openContext(browser, scopes.viewer);
    await viewerContext.route('**/*', route => ['localhost', '127.0.0.1'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort('blockedbyclient'));
    const viewerPage = await viewerContext.newPage();
    await viewerPage.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
    await expect(viewerPage.getByRole('button', { name: 'New folder', exact: true })).toBeDisabled({ timeout: UI_READY });
    await expect(viewerPage.getByRole('button', { name: /^Folder actions for/ })).toHaveCount(0);
    await expect(viewerPage.getByRole('button', { name: /^Drag e2e-folders-/ })).toHaveCount(0);
    await viewerContext.close();
  });

  await page.getByRole('button', { name: `Folder actions for ${archive}`, exact: true }).click();
  await page.getByRole('menuitem', { name: 'Remove folder', exact: true }).click();
  const removing = page.waitForResponse(r => new URL(r.url()).pathname === folderUrl(parent.id) && r.request().method() === 'DELETE', { timeout: UI_READY });
  await page.getByRole('dialog', { name: 'Remove folder', exact: true }).getByRole('button', { name: 'Remove folder', exact: true }).click();
  expect((await removing).status()).toBe(200);
  await test.step('backend check 6: remove folders without data loss', async () => {
    expect((await a.api.get<Inventory>(FOLDERS)).result.folders).toEqual([]);
    for (const id of ids) expect(await stored(id)).toEqual([{ folder_id: null, deleted: false }]);
    await page.getByRole('navigation', { name: 'Folders' }).getByText('No folder', { exact: true }).click();
    await expect(page.getByRole('button', { name: /^Drag e2e-folders-/ })).toHaveCount(3, { timeout: UI_READY });
    await page.reload({ waitUntil: 'domcontentloaded' });
    expect((await a.api.get<Listed>(LIST, { folder_path: '' })).result.datasets.map(row => row.id).sort()).toEqual([...ids].sort());
  });
  await testInfo.attach('folders-page', { contentType: 'image/png', body: await page.screenshot() });
  await context.close();
});
