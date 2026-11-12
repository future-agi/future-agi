import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { E2E } from '../../lib/env';

// ManualDatasetCreateRequestSerializer, UpdateCellValueView and DatasetTransferRequestSerializer.
const CREATE = '/model-hub/develops/create-dataset-manually/';
const LIST = '/model-hub/develops/get-datasets/';
const cellPath = (id: string) => `/model-hub/develops/${id}/update_cell_value/`;
const transferPath = (id: string) => `/model-hub/datasets/${id}/transfer/`;
// DevelopView, DatasetListCells and DatasetTransferDialog expose these accessible names.
const UI_READY = 60_000;
type Created = { result: { dataset_id: string } };
type Listed = { result: { datasets: { id: string; name: string }[] } };
type Stored = { id: string; workspace_id: string; organization_id: string; name: string };

test('DATA-E2E-046: datasets copy into another workspace after a write-free preview, and moves are refused', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-046', area: 'datasets',
    userGoal: 'Copy a dataset into another workspace I can add datasets to, checking first what will happen',
    steps: [
      'create synthetic datasets in two organizations and record a cell value',
      'open Duplicate from the list row, choose the sibling workspace and check the copy first',
      'copy the dataset under a new name and compare the persisted copy with its source',
      'open Duplicate for a second dataset and find no Move choice, then ask the API to move it',
      'refuse foreign organization, sibling scope and viewer transfers',
    ],
    backendChecks: [
      'the copy preview returns the exact plan and writes nothing',
      'the copy persists in the target workspace with the same rows, input columns and cell values while the source stays put',
      'a move preview and a move are refused with 409 and leave the dataset, its rows and its workspace unchanged',
      'foreign organization, sibling scope and viewer transfers are refused without writes',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Synthetic provisioning 120s + 10 UI_READY stages (600s) + synchronous API/PG reads 60s.
  test.setTimeout(780_000);
  // Execute the generated parser at runtime without typechecking unrelated Zod defaults.
  // The transfer response's TypeScript declaration is pinned by its contract regression.
  const validatorsPath = '../../../frontend/src/generated/api-contracts/api.zod.ts';
  const { ModelHubDatasetsTransferCreateResponse } = await import(validatorsPath);
  const stem = `e2e-transfer-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const a = scopes.ownerA;
  const b = scopes.ownerB;
  const target = scopes.emptyWorkspace;
  const inTarget = scopes.withWorkspace(a, target.id);
  const make = async (actor: typeof a, name: string, rows: number, columns: number) => {
    const created = await scopes.send<Created>(actor, 'POST', CREATE, { dataset_name: name, number_of_rows: rows, number_of_columns: columns });
    expect(created.status).toBe(200);
    return created.body.result.dataset_id;
  };
  const copyName = `${stem}-copy-source`;
  const moveName = `${stem}-move-source`;
  const copySource = await make(a, copyName, 2, 2);
  const moveSource = await make(a, moveName, 1, 1);
  const foreignId = await make(b, `${stem}-foreign`, 1, 1);
  const [cell] = await probe.pg<{ row_id: string; column_id: string }>(
    `SELECT c.row_id, c.column_id FROM model_hub_cell c JOIN model_hub_row r ON r.id = c.row_id
      WHERE c.dataset_id = $1 AND c.deleted = false ORDER BY r."order", c.column_id LIMIT 1`, [copySource]);
  const value = `${stem}-cell`;
  expect((await scopes.send(a, 'POST', cellPath(copySource), { column_id: cell.column_id, row_id: cell.row_id, new_value: value })).status).toBe(200);
  await testInfo.attach('transfer-identities', { contentType: 'application/json', body: JSON.stringify({ copySource, moveSource, foreignId, organization: a.organizationId, source: a.workspaceId, target: target.id }) });

  const stored = (id: string) => probe.pg<Stored>(
    'SELECT id, workspace_id, organization_id, name FROM model_hub_dataset WHERE id = $1 AND deleted = false', [id]);
  const named = (name: string) => probe.pg<Stored>(
    'SELECT id, workspace_id, organization_id, name FROM model_hub_dataset WHERE organization_id = $1 AND name = $2 AND deleted = false', [a.organizationId, name]);
  const census = async () => (await probe.pg<{ datasets: number; rows: number; cells: number }>(
    `SELECT (SELECT count(*)::int FROM model_hub_dataset WHERE organization_id = ANY($1::uuid[]) AND deleted = false) AS datasets,
            (SELECT count(*)::int FROM model_hub_row r JOIN model_hub_dataset d ON d.id = r.dataset_id WHERE d.organization_id = ANY($1::uuid[]) AND r.deleted = false) AS rows,
            (SELECT count(*)::int FROM model_hub_cell c JOIN model_hub_dataset d ON d.id = c.dataset_id WHERE d.organization_id = ANY($1::uuid[]) AND c.deleted = false) AS cells`,
    [[a.organizationId, b.organizationId]]))[0];
  const shape = async (id: string) => ({
    rows: (await probe.pg<{ order: number }>('SELECT "order" FROM model_hub_row WHERE dataset_id = $1 AND deleted = false ORDER BY "order"', [id])).map(r => r.order),
    columns: (await probe.pg<{ name: string; source: string }>('SELECT name, source FROM model_hub_column WHERE dataset_id = $1 AND deleted = false ORDER BY name', [id])),
    values: (await probe.pg<{ value: string | null }>('SELECT value FROM model_hub_cell WHERE dataset_id = $1 AND deleted = false AND value IS NOT NULL ORDER BY value', [id])).map(r => r.value),
  });
  const listed = async (actor: typeof a, search: string) =>
    (await actor.api.get<Listed>(LIST, { search_text: search, page_size: 100 })).result.datasets.map(row => row.id);
  const sourceShape = await shape(copySource);
  expect(sourceShape.values).toEqual([value]);
  const before = await census();

  const context = await scopes.openContext(browser, a);
  await context.route('**/*', route => ['localhost', '127.0.0.1'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort('blockedbyclient'));
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  const transferred = (id: string, dryRun: boolean) => page.waitForResponse(r =>
    new URL(r.url()).pathname === transferPath(id) && r.request().method() === 'POST' && r.request().postDataJSON()?.dry_run === dryRun, { timeout: UI_READY });
  const openDuplicate = async (name: string) => {
    // Let the searched list settle first: a re-rendered row closes its open menu.
    const searched = page.waitForResponse(r => {
      const url = new URL(r.url());
      return url.pathname === LIST && url.searchParams.get('search_text') === name;
    }, { timeout: UI_READY });
    await page.getByRole('textbox', { name: 'Search datasets', exact: true }).fill(name);
    expect((await searched).status()).toBe(200);
    const actions = page.getByRole('button', { name: `${name} actions`, exact: true });
    await expect(actions).toHaveCount(1, { timeout: UI_READY });
    const dialog = page.getByRole('dialog').filter({ hasText: `Duplicate “${name}”` });
    const duplicate = page.getByRole('menuitem', { name: /^Duplicate/ });
    await expect(async () => {
      if (!(await duplicate.isVisible())) await actions.click({ timeout: 5_000 });
      await duplicate.click({ timeout: 5_000 });
      await expect(dialog).toBeVisible({ timeout: 5_000 });
    }).toPass({ timeout: UI_READY });
    await expect(dialog.getByRole('radiogroup', { name: 'History' })).toHaveCount(0);
    await dialog.getByRole('combobox', { name: 'To workspace' }).click();
    await page.getByRole('option', { name: target.name, exact: true }).click();
    return dialog;
  };
  await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });

  let dialog = await openDuplicate(copyName);
  const preview = transferred(copySource, true);
  await dialog.getByRole('button', { name: 'Check first', exact: true }).click();
  let response = await preview;
  await test.step('backend check 1: the copy preview writes nothing', async () => {
    expect(response.status()).toBe(200);
    expect(response.request().postDataJSON()).toEqual({ to_workspace_id: target.id, mode: 'copy', dry_run: true });
    // Validate the real wire response, not only the Axios adapter's unchecked data.
    const payload = ModelHubDatasetsTransferCreateResponse.parse(await response.json());
    expect(payload.result.dataset_id).toBeNull();
    expect(payload.result.source_dataset_id).toBe(copySource);
    expect(payload.result.plan).toEqual({ rows: 2, columns: 2, columns_left_out: 0, leaves_folder: false });
    await testInfo.attach('copy-preview-response', { body: JSON.stringify(payload), contentType: 'application/json' });
    await expect(dialog.getByTestId('transfer-plan')).toHaveText(
      `Will copy 2 rows · 2 columns into ${target.name} as “Copy of ${copyName}”.`, { timeout: UI_READY });
    expect(await census()).toEqual(before);
  });
  await dialog.getByRole('textbox', { name: 'Name', exact: true }).fill(`${stem}-copied`);
  const copying = transferred(copySource, false);
  await dialog.getByRole('button', { name: 'Copy', exact: true }).click();
  response = await copying;
  await test.step('backend check 2: the copy persists in the target workspace', async () => {
    expect(response.status()).toBe(200);
    expect(response.request().postDataJSON()).toEqual({ to_workspace_id: target.id, mode: 'copy', new_dataset_name: `${stem}-copied`, dry_run: false });
    const [copy] = await named(`${stem}-copied`);
    expect(copy).toMatchObject({ workspace_id: target.id, organization_id: a.organizationId });
    expect(ModelHubDatasetsTransferCreateResponse.parse(await response.json()).result.dataset_id).toBe(copy.id);
    expect(await shape(copy.id)).toEqual(sourceShape);
    expect(await stored(copySource)).toEqual([expect.objectContaining({ workspace_id: a.workspaceId })]);
    expect(await listed(inTarget, `${stem}-copied`)).toEqual([copy.id]);
    expect(await listed(a, `${stem}-copied`)).toEqual([]);
    await expect(page.getByText(`Copied to ${target.name}`, { exact: true })).toBeVisible({ timeout: UI_READY });
  });

  dialog = await openDuplicate(moveName);
  await test.step('backend check 3: a move is not offered, and the API refuses it without writing', async () => {
    // Nothing coordinates the writers that reference a dataset's rows with a move, so every move is refused.
    await expect(dialog.getByRole('textbox', { name: 'Name', exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(dialog.getByRole('radio', { name: 'Move', exact: true })).toHaveCount(0, { timeout: UI_READY });
    await expect(dialog.getByRole('button', { name: 'Move', exact: true })).toHaveCount(0, { timeout: UI_READY });
    const moveCensus = await census();
    const moveRows = await shape(moveSource);
    for (const dry_run of [true, false]) {
      const refused = await scopes.send<{ result: string }>(a, 'POST', transferPath(moveSource), { to_workspace_id: target.id, mode: 'move', dry_run });
      expect(refused.status, `move dry_run=${dry_run}`).toBe(409);
      expect(refused.body.result).toBe("Datasets can't be moved between workspaces. Copy it instead.");
    }
    expect(await stored(moveSource)).toEqual([expect.objectContaining({ id: moveSource, workspace_id: a.workspaceId, name: moveName })]);
    expect(await shape(moveSource)).toEqual(moveRows);
    expect(await census()).toEqual(moveCensus);
    expect(await listed(a, moveName)).toEqual([moveSource]);
    expect(await listed(inTarget, moveName)).toEqual([]);
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(dialog).toHaveCount(0, { timeout: UI_READY });
  });

  await test.step('backend check 4: foreign, sibling-scope and viewer transfers are refused', async () => {
    const after = await census();
    const attempts: [typeof a, string, string, number][] = [
      [b, copySource, target.id, 404],
      [a, copySource, b.workspaceId, 404],
      [inTarget, copySource, a.workspaceId, 404],
      [a, foreignId, target.id, 404],
      [scopes.viewer, copySource, target.id, 403],
    ];
    for (const [actor, id, to, status] of attempts) {
      for (const mode of ['copy', 'move']) {
        for (const dry_run of [true, false]) {
          const denied = await scopes.send(actor, 'POST', transferPath(id), { to_workspace_id: to, mode, dry_run });
          expect(denied.status, `${mode} ${dry_run} ${id} -> ${to}`).toBe(status);
        }
      }
    }
    expect(await census()).toEqual(after);
    expect(await stored(copySource)).toEqual([expect.objectContaining({ workspace_id: a.workspaceId })]);
    expect(await stored(foreignId)).toEqual([expect.objectContaining({ workspace_id: b.workspaceId })]);
  });
  await testInfo.attach('dataset-transfer-page', { body: await page.screenshot(), contentType: 'image/png' });
  await testInfo.attach('dataset-transfer-origin', { body: JSON.stringify({ api: E2E.apiUrl, app: E2E.appUrl, browser: browser.browserType().name() }), contentType: 'application/json' });
  await context.close();
});
