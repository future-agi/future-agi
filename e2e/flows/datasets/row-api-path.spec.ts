import { readFileSync } from 'node:fs';
import { request as pwRequest, type Browser, type BrowserContext, type Page } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import type { ScopeActor, ScopeActors } from '../../lib/scope-actors';
import type { StateProbe } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { importApiRows, type ImportedRow } from './row-api-import';

// owed-733-rerun: collector credentials stay in the harness Actor abstraction;
// rerun both journeys on the personal-key composition before enablement.
// DatasetRowReadView (model_hub/views/dataset_row_read.py), as the generated contract names it.
const TEMPLATE = '/model-hub/datasets/{dataset_id}/rows/{row_id}/';
// RowIdChip.jsx -> CopyId.jsx menu items; rowApiPath.js gates the API items.
const UI_READY = 60_000;
// The candidate build either ships the dataset.rowApiPath claim (default) or not.
const CLAIMED = (process.env.E2E_DSX_ROW_API_PATH ?? 'claimed') !== 'unclaimed';

type Column = { id: string; name: string };
type Cell = { cell_id: string; cell_value: string | null };
type Current = { row_id: string; order: number; row_meta?: Record<string, unknown> } & Record<string, unknown>;
type RowValues = { row_id: string; order: number; row_meta: Record<string, unknown>; cells: Record<string, Cell> };
type RowRead = { status: boolean; result: RowValues };

// get-row-data's `current` in the row read's shape: the same values, cells keyed by column id.
function asRowRead(current: Current): RowValues {
  const { row_id, order, row_meta, ...cells } = current;
  return { row_id, order, row_meta: row_meta ?? {}, cells: cells as Record<string, Cell> };
}

function contractedGet(): void {
  // e2e/flows/datasets -> repo root: the checked-in generated contract.
  const swagger = JSON.parse(readFileSync(new URL('../../../api_contracts/openapi/swagger.json', import.meta.url), 'utf8'));
  const operations = swagger.paths[TEMPLATE] ?? {};
  expect(Object.keys(operations).filter(key => key !== 'parameters')).toEqual(['get']);
  expect(operations.get.responses['200'].schema.$ref).toBe('#/definitions/DatasetRowReadResponse');
  const result = swagger.definitions.DatasetRowReadResult;
  expect(result.required).toEqual(expect.arrayContaining(['row_id', 'order', 'row_meta', 'cells']));
  expect(result.properties.cells.additionalProperties.$ref).toBe('#/definitions/DatasetRowReadCell');
}

const rowPath = (dataset: string, row: string) =>
  TEMPLATE.replace('{dataset_id}', dataset).replace('{row_id}', row);

async function readBack(scopes: ScopeActors, probe: StateProbe, a: ScopeActor,
  dataset: string, row: string, column: Column, value: string, path: string, expected: ImportedRow) {
  const read = await getRow(scopes, a, path);
  expect(read.status).toBe(200);
  expect(read.body.status).toBe(true);
  expect(read.body.result.row_id).toBe(row);
  expect(read.body.result.row_meta.source).toEqual(expected.source);
  for (const [id, cellValue] of Object.entries(expected.cells)) {
    expect(read.body.result.cells[id].cell_value).toBe(cellValue);
    expect(await probe.pg<{ id: string; value: string }>(
      `SELECT id::text AS id, value FROM model_hub_cell
       WHERE dataset_id=$1::uuid AND row_id=$2::uuid AND column_id=$3::uuid AND NOT deleted`,
      [dataset, row, id])).toEqual([{ id: read.body.result.cells[id].cell_id, value: cellValue }]);
  }
  // model_hub/models/row_sources.py: persisted provenance, not inferred from cells.
  expect(await probe.pg(
    `SELECT dataset_id::text, row_id::text, kind, project_id::text, trace_id, span_id, session_id,
       organization_id::text, workspace_id::text FROM model_hub_row_source WHERE row_id=$1::uuid`, [row]))
    .toEqual([{ dataset_id: dataset, row_id: row, kind: 'trace', project_id: expected.source.project_id,
      trace_id: expected.source.trace_id, span_id: expected.source.span_id, session_id: null,
      organization_id: a.organizationId, workspace_id: a.workspaceId }]);
  const cell = read.body.result.cells[column.id];
  expect(cell.cell_value).toBe(value);
  // The values get-row-data returns for the same row.
  const current = (await a.api.post<{ result: { current: Current } }>(
    `/model-hub/develops/${dataset}/get-row-data/`, { row_id: row })).result.current;
  expect(read.body.result).toEqual(asRowRead(current));
  expect(await probe.pg<{ row_id: string; organization_id: string; workspace_id: string; cell_id: string; value: string }>(
    `SELECT r.id::text AS row_id, d.organization_id::text AS organization_id, d.workspace_id::text AS workspace_id,
       c.id::text AS cell_id, c.value
     FROM model_hub_row r JOIN model_hub_dataset d ON d.id = r.dataset_id
     JOIN model_hub_cell c ON c.row_id = r.id AND c.column_id = $3::uuid AND NOT c.deleted
     WHERE r.id = $1::uuid AND r.dataset_id = $2::uuid AND NOT r.deleted AND NOT d.deleted`,
    [row, dataset, column.id]))
    .toEqual([{ row_id: row, organization_id: a.organizationId, workspace_id: a.workspaceId,
      cell_id: cell.cell_id, value }]);
  return read.body.result;
}

async function refused(scopes: ScopeActors, path: string, values: string[], ...actors: ScopeActor[]) {
  for (const actor of actors) {
    const response = await scopes.send<unknown>(actor, 'GET', path);
    expect(response.status).toBe(404);
    for (const value of values) expect(JSON.stringify(response.body)).not.toContain(value);
  }
}

async function rowView(scopes: ScopeActors, browser: Browser,
  a: ScopeActor, dataset: string, row: string): Promise<{ context: BrowserContext; page: Page }> {
  const context = await scopes.openContext(browser, a);
  // Runtime traffic stays on fixture loopback. External requests are blocked, never mocked.
  await context.route('**/*', route => ['localhost', '127.0.0.1'].includes(new URL(route.request().url()).hostname)
    ? route.continue() : route.abort('blockedbyclient'));
  // Record what the page copies (CopyButton.copyText -> navigator.clipboard.writeText).
  await context.addInitScript(() => {
    const copied: string[] = [];
    Object.defineProperty(window, '__copied', { value: copied });
    Object.defineProperty(navigator, 'clipboard', { configurable: true,
      value: { writeText: async (text: string) => { copied.push(String(text)); } } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  const opened = page.waitForResponse(r => new URL(r.url()).pathname === `/model-hub/develops/${dataset}/get-row-data/`
    && r.request().method() === 'POST', { timeout: UI_READY });
  await page.goto(`/dashboard/develop/${dataset}?row=${row}`, { waitUntil: 'domcontentloaded' });
  expect((await opened).status()).toBe(200);
  return { context, page };
}

const copied = (page: Page) => page.evaluate(() => [...((window as unknown as { __copied: string[] }).__copied)]);

async function copyFromMenu(page: Page, row: string, item: string): Promise<string> {
  const before = (await copied(page)).length;
  await page.getByRole('button', { name: `Copy ID ${row}`, exact: true }).click({ button: 'right' });
  await page.getByRole('menuitem', { name: item, exact: true }).click();
  await expect.poll(async () => (await copied(page)).length, { timeout: UI_READY }).toBe(before + 1);
  return (await copied(page))[before];
}

test('DATA-E2E-083: copy a row API path from Row view and read the row through it', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-083', area: 'datasets',
    userGoal: 'Copy the public API path and API call of a dataset row from Row view and read exactly that row through it',
    steps: [
      'send two traces to the real collector and import their roots into a dataset, then open an imported row in Row view',
      'right-click the row id chip and copy its API path',
      'call the copied method and path with the signed-in session',
      'copy the row as an API call and check the personal key environment names and Keys page pointer, with no session authentication',
    ],
    backendChecks: [
      'collector spans have the minted root names and IO; copied GET and get-row-data agree with those cells and exact RowSource kind, project, trace, span, organization and workspace in Postgres',
      'another organization, also with the first organization’s headers, gets 404 with no cell values or source identifiers',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  test.setTimeout(600_000);
  const a = scopes.ownerA;
  const b = scopes.ownerB;
  const name = `e2e-data83-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const { dataset, column, rowIds, values, expectedRows } = await importApiRows(scopes, probe, testInfo, name);
  const privateValues = expectedRows.flatMap(r => [...Object.values(r.cells), r.source.trace_id, r.source.span_id]);
  const row = rowIds[1];
  await testInfo.attach('row-api-identities', { contentType: 'application/json', body: JSON.stringify({
    dataset, row, column, values, organizationId: a.organizationId, workspaceId: a.workspaceId,
    foreignOrganizationId: b.organizationId, claimed: CLAIMED }) });

  const { context, page } = await rowView(scopes, browser, a, dataset, row);
  await page.getByRole('button', { name: `Copy ID ${row}`, exact: true }).click({ button: 'right' });
  for (const item of ['Copy id', 'Copy API path', 'Copy as API call', 'Copy link'])
    await expect(page.getByRole('menuitem', { name: item, exact: true })).toBeVisible();
  await page.keyboard.press('Escape');

  const copiedPath = await copyFromMenu(page, row, 'Copy API path');
  const [method, path, ...rest] = copiedPath.split(' ');
  expect(rest).toEqual([]);
  expect(method).toBe('GET');
  expect(path).toBe(rowPath(dataset, row));
  contractedGet();

  // The copied request, sent by the page with its own signed-in session.
  const inPage = await page.evaluate(async ({ api, method, path }) => {
    const response = await fetch(`${api}${path}`, { method, headers: {
      Authorization: `Bearer ${localStorage.getItem('accessToken')}`,
      'X-Organization-Id': sessionStorage.getItem('organizationId') ?? '',
      'X-Workspace-Id': sessionStorage.getItem('workspaceId') ?? '' } });
    return { status: response.status, body: await response.json() };
  }, { api: E2E.apiUrl, method, path });
  expect(inPage.status).toBe(200);
  const values83 = await readBack(scopes, probe, a, dataset, row, column, values[1], path, expectedRows[1]);
  expect(inPage.body.result).toEqual(values83);
  expect(JSON.stringify(values83)).not.toContain(values[0]);

  const call = await copyFromMenu(page, row, 'Copy as API call');
  const [keysHint, ...command] = call.split('\n');
  expect(keysHint).toBe('# Create a personal API key pair at /dashboard/keys');
  const lines = command.join('\n').split(' \\\n  ');
  const target = lines[0].match(/^curl -X (\w+) "([^"]+)"$/);
  expect(target?.[1]).toBe('GET');
  expect(new URL(target![2]).pathname).toBe(path);
  expect(lines.slice(1)).toEqual(['-H "X-Api-Key: $FI_API_KEY"', '-H "X-Secret-Key: $FI_SECRET_KEY"']);
  expect(call).not.toMatch(/Bearer|Authorization|JWT|Cookie/i);
  expect(await copyFromMenu(page, row, 'Copy id')).toBe(row);
  await context.close();

  // Another organization: its own session, then with the first organization's ids in the headers.
  await refused(scopes, path, privateValues, b, { ...b, organizationId: a.organizationId, workspaceId: a.workspaceId });
  // The owner still reads it (the refusal is scoping, not a missing row).
  expect((await scopes.send<RowRead>(a, 'GET', path)).status).toBe(200);
  await testInfo.attach('row-api-copied', { contentType: 'application/json',
    body: JSON.stringify({ copiedPath, call }) });
});

test('DATA-E2E-084: a deleted or foreign row is refused, and Row view keeps id and link copy', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-084', area: 'datasets',
    userGoal: 'Read a dataset row through its public path until it is deleted, without another organization or workspace reaching it',
    steps: [
      'send two traces to the real collector and import their roots into a dataset',
      'read a row through its public path, then delete it and read again',
      'open another row in Row view and copy its id and link from the id chip',
    ],
    backendChecks: [
      'the owner read equals get-row-data and the collector IO, persisted cells and RowSource identity, then returns 404 after Postgres records deletion',
      'another organization, also with the first organization’s headers, and another workspace get 404 with no cell values or source identifiers',
    ],
    browserChecks: [
      'the id chip copies the row id and its link; its API items follow the build’s dataset.rowApiPath claim',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  test.setTimeout(600_000);
  const a = scopes.ownerA;
  const b = scopes.ownerB;
  const name = `e2e-data84-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const { dataset, column, rowIds, values, expectedRows } = await importApiRows(scopes, probe, testInfo, name);
  const privateValues = expectedRows.flatMap(r => [...Object.values(r.cells), r.source.trace_id, r.source.span_id]);
  const [doomed, kept] = rowIds;
  await testInfo.attach('row-api-identities', { contentType: 'application/json', body: JSON.stringify({
    dataset, rowIds, column, values, organizationId: a.organizationId, workspaceId: a.workspaceId,
    foreignOrganizationId: b.organizationId, emptyWorkspaceId: scopes.emptyWorkspace.id, claimed: CLAIMED }) });

  const doomedPath = rowPath(dataset, doomed);
  await readBack(scopes, probe, a, dataset, doomed, column, values[0], doomedPath, expectedRows[0]);
  contractedGet();
  const removed = await scopes.send<{ status: boolean }>(a, 'DELETE', `/model-hub/develops/${dataset}/delete_row/`,
    { row_ids: [doomed] });
  expect(removed.status).toBe(200);
  expect(await probe.pg<{ deleted: boolean }>('SELECT deleted FROM model_hub_row WHERE id = $1::uuid', [doomed]))
    .toEqual([{ deleted: true }]);
  await refused(scopes, doomedPath, privateValues, a);

  const keptPath = rowPath(dataset, kept);
  await readBack(scopes, probe, a, dataset, kept, column, values[1], keptPath, expectedRows[1]);
  await refused(scopes, keptPath, privateValues, b, { ...b, organizationId: a.organizationId, workspaceId: a.workspaceId },

    scopes.withWorkspace(a, scopes.emptyWorkspace.id));

  const { context, page } = await rowView(scopes, browser, a, dataset, kept);
  await page.getByRole('button', { name: `Copy ID ${kept}`, exact: true }).click({ button: 'right' });
  await expect(page.getByRole('menuitem', { name: 'Copy id', exact: true })).toBeVisible();
  await expect(page.getByRole('menuitem', { name: 'Copy link', exact: true })).toBeVisible();
  for (const item of ['Copy API path', 'Copy as API call'])
    await expect(page.getByRole('menuitem', { name: item, exact: true })).toHaveCount(CLAIMED ? 1 : 0);
  await page.keyboard.press('Escape');
  expect(await copyFromMenu(page, kept, 'Copy id')).toBe(kept);
  const link = await copyFromMenu(page, kept, 'Copy link');
  expect(new URL(link).searchParams.get('row')).toBe(kept);
  expect(new URL(link).pathname).toBe(`/dashboard/develop/${dataset}`);
  await context.close();
});

// The owner's row read, status first: a response that is not JSON (a route the
// backend does not serve) fails at the status check instead of while parsing.
async function getRow(scopes: ScopeActors, actor: ScopeActor, path: string): Promise<{ status: number; body: RowRead }> {
  const context = await pwRequest.newContext({ baseURL: E2E.apiUrl });
  try {
    const response = await context.get(path, { headers: scopes.headers(actor), maxRedirects: 0 });
    const text = await response.text();
    let body: unknown = text;
    try { body = JSON.parse(text); } catch { body = { status: false, text } }
    return { status: response.status(), body: body as RowRead };
  } finally {
    await context.dispose();
  }
}
