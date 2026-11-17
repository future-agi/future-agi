import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned source: 818b068e64b476b2a694706df2fc84bed6df790e.
// DatasetTransferDialog.jsx / datasetTransferApi.js; DATA-E2E-046 fixture.
// Scope is the shipped copy contract and move refusal, NOT proof of working moves.
const CREATE = '/model-hub/develops/create-dataset-manually/';
const LIST = '/model-hub/develops/get-datasets/';
const transfer = (id: string) => `/model-hub/datasets/${id}/transfer/`;
const table = (id: string) => `/model-hub/develops/${id}/get-dataset-table/`;
const UI_READY = 60_000;
type Created = { result: { dataset_id: string } };
type Listed = { result: { datasets: { id: string; name: string }[] } };
type Transfer = { status: boolean; result: {
  mode: string; dry_run: boolean; source_dataset_id: string; dataset_id: string | null;
  dataset_name: string; from_workspace: { id: string; name: string };
  to_workspace: { id: string; name: string };
  plan: { rows: number; columns: number; columns_left_out: number; leaves_folder: boolean };
} };

test('@DSX-WALK-ORG-51: Copy a dataset after a write-free preview without exposing it to another tenant.', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DSX-WALK-ORG-51', area: 'dsx-walks',
    userGoal: 'Preview and copy rows into an authorized workspace; keep the source and reject foreign transfers and unsupported moves',
    steps: ['seed a two-row dataset and a foreign dataset through the manual-create API',
      'open Duplicate, select a sibling workspace, preview and copy under a new name',
      'read the destination and source and attempt foreign reads, copies and moves'],
    backendChecks: ['preview returns the exact two-row one-column plan without adding datasets, rows or cells',
      'copy has the same input columns, ordered rows and synthetic cell value in the target workspace',
      'source remains unchanged; moves return 409; foreign transfers return 404 and foreign table reads return 400'],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  test.setTimeout(600_000);
  const a = scopes.ownerA, b = scopes.ownerB;
  const target = scopes.emptyWorkspace;
  const destination = scopes.withWorkspace(a, target.id);
  const stem = `e2e-dsx-org51-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const name = `${stem}-source`, copyName = `${stem}-copy`;
  const make = async (actor: typeof a, dataset_name: string) => {
    const res = await scopes.send<Created>(actor, 'POST', CREATE, {
      dataset_name, model_type: 'GenerativeLLM', number_of_rows: 2, number_of_columns: 1,
      columns: [{ name: 'input', type: 'text', role: 'input' }],
    });
    expect(res.status).toBe(200);
    return res.body.result.dataset_id;
  };
  const sourceId = await make(a, name);
  const foreignId = await make(b, `${stem}-foreign`);
  await testInfo.attach('seeded-ids', { contentType: 'application/json', body: JSON.stringify({
    sourceId, foreignId, organizationA: a.organizationId, organizationB: b.organizationId,
    sourceWorkspace: a.workspaceId, destinationWorkspace: target.id,
  }) });
  const [cell] = await probe.pg<{ row_id: string; column_id: string }>(
    `SELECT c.row_id, c.column_id FROM model_hub_cell c JOIN model_hub_row r ON r.id=c.row_id
     WHERE c.dataset_id=$1 AND NOT c.deleted ORDER BY r."order" LIMIT 1`, [sourceId]);
  expect(cell).toBeDefined();
  const marker = `${stem}-value`;
  expect((await scopes.send(a, 'POST', `/model-hub/develops/${sourceId}/update_cell_value/`,
    { row_id: cell.row_id, column_id: cell.column_id, new_value: marker })).status).toBe(200);
  const shape = async (id: string) => ({
    rows: await probe.pg<{ order: number }>('SELECT "order" FROM model_hub_row WHERE dataset_id=$1 AND NOT deleted ORDER BY "order"', [id]),
    columns: await probe.pg<{ name: string; source: string }>('SELECT name, source FROM model_hub_column WHERE dataset_id=$1 AND NOT deleted ORDER BY name', [id]),
    values: await probe.pg<{ value: string }>('SELECT value FROM model_hub_cell WHERE dataset_id=$1 AND NOT deleted AND value IS NOT NULL ORDER BY value', [id]),
  });
  const census = () => probe.pg<{ datasets: number; rows: number; cells: number }>(
    `SELECT (SELECT count(*)::int FROM model_hub_dataset WHERE organization_id=ANY($1::uuid[]) AND NOT deleted) AS datasets,
       (SELECT count(*)::int FROM model_hub_row r JOIN model_hub_dataset d ON d.id=r.dataset_id WHERE d.organization_id=ANY($1::uuid[]) AND NOT r.deleted) AS rows,
       (SELECT count(*)::int FROM model_hub_cell c JOIN model_hub_dataset d ON d.id=c.dataset_id WHERE d.organization_id=ANY($1::uuid[]) AND NOT c.deleted) AS cells`,
    [[a.organizationId, b.organizationId]]);
  const list = async (actor: typeof a, search: string) =>
    (await actor.api.get<Listed>(LIST, { search_text: search, page_size: 100 })).result.datasets;
  const sourceShape = await shape(sourceId);
  expect(sourceShape.rows).toHaveLength(2);
  expect(sourceShape.values).toEqual([{ value: marker }]);
  const before = await census();
  const context = await scopes.openContext(browser, a);
  await context.route('**/*', route => ['localhost', '127.0.0.1'].includes(new URL(route.request().url()).hostname)
    ? route.continue() : route.abort('blockedbyclient'));
  try {
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
    const searched = page.waitForResponse(r => {
      const url = new URL(r.url());
      return url.pathname === LIST && url.searchParams.get('search_text') === name;
    });
    await page.getByRole('textbox', { name: 'Search datasets', exact: true }).fill(name);
    expect((await searched).status()).toBe(200);
    await page.getByRole('button', { name: `${name} actions`, exact: true }).click();
    await page.getByRole('menuitem', { name: /^Duplicate/ }).click();
    const dialog = page.getByRole('dialog').filter({ hasText: `Duplicate “${name}”` });
    await expect(dialog).toBeVisible();
    await dialog.getByRole('combobox', { name: 'To workspace' }).click();
    await page.getByRole('option', { name: target.name, exact: true }).click();
    const responseFor = (dryRun: boolean) => page.waitForResponse(r =>
      new URL(r.url()).pathname === transfer(sourceId) && r.request().method() === 'POST'
      && r.request().postDataJSON().dry_run === dryRun);
    const previewing = responseFor(true);
    await dialog.getByRole('button', { name: 'Check first', exact: true }).click();
    const preview = await previewing;
    expect(preview.status()).toBe(200);
    expect(preview.request().postDataJSON()).toEqual({ to_workspace_id: target.id, mode: 'copy', dry_run: true });
    const plan = (await preview.json() as Transfer).result;
    expect(plan).toMatchObject({ mode: 'copy', dry_run: true, source_dataset_id: sourceId,
      dataset_id: null, dataset_name: `Copy of ${name}`, from_workspace: { id: a.workspaceId },
      to_workspace: { id: target.id, name: target.name },
      plan: { rows: 2, columns: 1, columns_left_out: 0, leaves_folder: false } });
    await expect(dialog.getByTestId('transfer-plan')).toHaveText(
      `Will copy 2 rows · 1 column into ${target.name} as “Copy of ${name}”.`);
    expect(await census()).toEqual(before);
    await dialog.getByRole('textbox', { name: 'Name', exact: true }).fill(copyName);
    const copying = responseFor(false);
    await dialog.getByRole('button', { name: 'Copy', exact: true }).click();
    const copied = await copying;
    expect(copied.status()).toBe(200);
    expect(copied.request().postDataJSON()).toEqual({ to_workspace_id: target.id, mode: 'copy', dry_run: false, new_dataset_name: copyName });
    const result = (await copied.json() as Transfer).result;
    expect(result).toMatchObject({ mode: 'copy', dry_run: false, source_dataset_id: sourceId, dataset_name: copyName, to_workspace: { id: target.id } });
    expect(result.dataset_id).toEqual(expect.any(String));
    const copyId = result.dataset_id!;
    expect(copyId).not.toBe(sourceId);
    expect(await shape(copyId)).toEqual(sourceShape);
    expect(await shape(sourceId)).toEqual(sourceShape);
    expect((await list(destination, copyName)).map(r => ({ id: r.id, name: r.name }))).toEqual([{ id: copyId, name: copyName }]);
    expect(await list(a, copyName)).toEqual([]);
    await expect(page.getByText(`Copied to ${target.name}`, { exact: true })).toBeVisible();
    const after = await census();
    for (const dry_run of [true, false]) {
      const move = await scopes.send<{ result: string }>(a, 'POST', transfer(sourceId), { to_workspace_id: target.id, mode: 'move', dry_run });
      expect(move.status).toBe(409);
      expect(move.body.result).toBe("Datasets can't be moved between workspaces. Copy it instead.");
      for (const [actor, id, workspace] of [[b, sourceId, b.workspaceId], [a, sourceId, b.workspaceId], [a, foreignId, target.id]] as const)
        expect((await scopes.send(actor, 'POST', transfer(id), { to_workspace_id: workspace, mode: 'copy', dry_run })).status).toBe(404);
    }
    expect((await scopes.send(b, 'GET', table(sourceId))).status).toBe(400);
    expect((await scopes.send(b, 'GET', table(copyId))).status).toBe(400);
    expect(await list(b, name)).toEqual([]);
    expect(await list(b, copyName)).toEqual([]);
    expect(await census()).toEqual(after);
    expect(await shape(sourceId)).toEqual(sourceShape);
    await testInfo.attach('copied-id', { contentType: 'application/json', body: JSON.stringify({ sourceId, copyId, foreignId }) });
  } finally { await context.close(); }
});
