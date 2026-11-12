import type { BrowserContext, Page } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import type { ScopeActor, ScopeActors } from '../../lib/scope-actors';
import type { StateProbe } from '../../lib/state-probe';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// model_hub/urls.py, ManualDatasetCreateRequestSerializer and DatasetAddRowsRequestSerializer.
const CREATE = '/model-hub/develops/create-dataset-manually/';
const path = (id: string, action: string) => `/model-hub/develops/${id}/${action}/`;
// SyntheticDataSerializer and the flow-local labelled chat/embedding STANDIN.
const MODEL = 'gpt-4o-mini';
const COLUMNS = ['question', 'context', 'citation'];
const UI_READY = 60_000;
type Table = { result: { table: { row_id: string; [key: string]: any }[]; column_config: { id: string; name: string }[] } };
type Fixture = { id: string; kb: string; file: string; name: string; selected: string; row: string; passage: string; citation: string; original: string[]; questionColumn: string };

async function seed(scopes: ScopeActors, actor: ScopeActor, context: BrowserContext, probe: StateProbe, name: string): Promise<Fixture> {
  const model = await scopes.send(actor, 'POST', '/model-hub/custom_models/create/', {
    model_provider: 'openai', model_name: MODEL, input_token_cost: 0, output_token_cost: 0,
    config_json: { key: 'synthetic-standin-only', api_base: 'http://localhost:18083/v1' },
  });
  expect(model.status).toBe(200);
  const created = await scopes.send<{ result: { dataset_id: string } }>(actor, 'POST', CREATE, {
    dataset_name: name, number_of_rows: 1,
    columns: COLUMNS.map(column => ({ name: column, type: 'text', role: column === 'question' ? 'input' : 'metadata' })),
  });
  expect(created.status).toBe(200);
  const id = created.body.result.dataset_id;
  const selected = `selected-${name}`;
  const added = await scopes.send(actor, 'POST', path(id, 'add_rows'), {
    rows: [selected, `unselected-${name}`].map(question => ({ cells: [{ column_name: 'question', value: question }] })),
  });
  expect(added.status).toBe(200);
  const table = await scopes.send<Table>(actor, 'GET', path(id, 'get-dataset-table'), undefined, { current_page_index: 0, page_size: 25 });
  expect(table.status).toBe(200);
  const original = table.body.result.table.map(row => row.row_id);
  const questionColumn = table.body.result.column_config.find(column => column.name === 'question')!.id;
  expect(table.body.result.table.map(row => row[questionColumn].cell_value)).toEqual([null, selected, `unselected-${name}`]);
  // LegacyKnowledgeBaseMutationRequestSerializer plus CreateKnowledgeBaseView's multipart file input.
  const passage = `Refunds for ${name} take exactly seven days.`;
  const upload = await context.request.post(`${E2E.apiUrl}/model-hub/knowledge-base/`, {
    headers: scopes.headers(actor), multipart: { name: `${name}-kb`,
      file: { name: 'support.txt', mimeType: 'text/plain', buffer: Buffer.from(passage) } },
  });
  const uploaded = await upload.json();
  expect(upload.status(), JSON.stringify(uploaded)).toBe(200);
  const kb = uploaded.result.kb_id;
  const file = uploaded.result.file_ids[0];
  await expect.poll(async () => {
    const rows = await probe.pg<{ status: string; last_error: string | null }>('SELECT status, last_error FROM model_hub_knowledgebasefile WHERE id=$1', [kb]);
    if (rows[0]?.last_error) throw new Error(rows[0].last_error);
    return rows[0]?.status;
  }, POLL.ASYNC_JOB).toBe('Completed');
  // KBIndexer.process_content writes these real syn rows; model embeddings alone are substituted.
  const chunks = await probe.ch<{ keys: string[]; values: string[] }>(
    'SELECT metadata.key AS keys, metadata.value AS values FROM syn FINAL WHERE eval_id={kb:UUID} AND deleted=0', { kb });
  expect(chunks).toHaveLength(1);
  const metadata = Object.fromEntries(chunks[0].keys.map((key, index) => [key, chunks[0].values[index]]));
  expect(metadata.organization_id).toBe(actor.organizationId);
  expect(metadata.file_id).toBe(file);
  expect(metadata.chunk_text).toBe(passage);
  const citation = `kb:${kb}/file:${file}/chunk:${metadata.chunk_id}`;
  return { id, kb, file, name, selected, row: original[1], passage, citation, original, questionColumn };
}

async function checkRows(scopes: ScopeActors, probe: StateProbe, actor: ScopeActor, data: Fixture, batches: number, perturb?: string) {
  const response = await scopes.send<Table>(actor, 'GET', path(data.id, 'get-dataset-table'), undefined, { current_page_index: 0, page_size: 25 });
  expect(response.status).toBe(200);
  const { table, column_config: columns } = response.body.result;
  const col = (name: string) => columns.find(column => column.name === name)!.id;
  const selected = perturb === 'sources' ? `wrong-${data.selected}` : data.selected;
  const generated = [`STANDIN | ${selected} | ${data.name}`, data.passage, perturb === 'citation' ? 'wrong-citation' : data.citation];
  const expected = [[null, null, null], [data.selected, "", ""], [`unselected-${data.name}`, "", ""], ...Array.from({ length: batches }, () => generated)];
  expect(table.map(row => COLUMNS.map(name => row[col(name)].cell_value))).toEqual(expected);
  expect(table.slice(0, 3).map(row => row.row_id)).toEqual(data.original);
  const cells = await probe.pg<{ row_id: string; name: string; value: string | null }>(
    'SELECT r.id AS row_id, col.name, c.value FROM model_hub_row r JOIN model_hub_cell c ON c.row_id=r.id JOIN model_hub_column col ON col.id=c.column_id WHERE r.dataset_id=$1 AND NOT r.deleted AND NOT c.deleted ORDER BY r."order"', [data.id]);
  const persisted = new Map<string, Record<string, string | null>>();
  for (const cell of cells) persisted.set(cell.row_id, { ...persisted.get(cell.row_id), [cell.name]: cell.value });
  expect([...persisted].map(([id, values]) => ({ id, values: COLUMNS.map(column => values[column] ?? null) })))
    .toEqual(table.map((row, index) => ({ id: row.row_id, values: expected[index] })));
  expect(await probe.pg('SELECT organization_id, workspace_id FROM model_hub_dataset WHERE id=$1', [data.id]))
    .toEqual([{ organization_id: actor.organizationId, workspace_id: actor.workspaceId }]);
  return table.map(row => row.row_id);
}

async function openSelection(page: Page, data: Fixture) {
  // DevelopDataV2.jsx getRowId uses row_id; AG Grid's selection checkbox is in the pinned row.
  await page.locator(`.ag-pinned-left-cols-container .ag-row[row-id="${data.row}"]`).getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Generate more like these (1)', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Generate rows', exact: true });
  await expect(dialog.getByText('Using only 1 selected row as examples. Maximum 50.', { exact: true })).toBeVisible();
  await expect(dialog.getByText('Loading configuration…')).toHaveCount(0, { timeout: UI_READY });
  return dialog;
}

test('DATA-E2E-037: selected examples generate persisted knowledge-grounded rows', {
  tag: ['@flow'], annotation: flowAnnotation({ id: 'DATA-E2E-037', area: 'datasets',
    userGoal: 'Generate and persist rows like selected examples, optionally grounded in an owned knowledge base, without including other rows or changing existing data',
    steps: ['provision two synthetic organizations and upload each knowledge base through the public API',
      'select one saved row, open Generate more like these, choose an owned knowledge base and confirm Generate and save',
      'reload and regenerate with the saved source IDs, then read exact persisted rows and citations',
      'reject foreign dataset, row and knowledge-base IDs and sibling workspace requests'],
    backendChecks: ['only selected examples and scoped indexed passages reach the model',
      'generated rows and exact source citations agree in API, PostgreSQL and reloaded UI; saved source IDs replay',
      'foreign organization and sibling workspace references are refused without writes'],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Provision 120 + two actors * (ASYNC_JOB 60 + eight UI_READY 480) seconds.
  test.setTimeout(1_200_000);
  const perturb = process.env.E2E_MORE_PERTURB;
  if (perturb && !['sources', 'citation', 'isolation'].includes(perturb)) throw new Error('Unknown perturbation');
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const contexts = await Promise.all([scopes.openContext(browser, scopes.ownerA), scopes.openContext(browser, scopes.ownerB)]);
  try {
    for (const context of contexts) await context.route('**/*', route => {
      const url = new URL(route.request().url());
      return url.hostname === 'localhost' || ['data:', 'blob:'].includes(url.protocol) ? route.continue() : route.abort('blockedbyclient');
    });
    const fixtures = [];
    for (const [index, actor] of [scopes.ownerA, scopes.ownerB].entries()) {
      fixtures.push(await seed(scopes, actor, contexts[index], probe, `e2e-more-${index}-${suffix}`));
    }
    await testInfo.attach('source-identities', { contentType: 'application/json', body: JSON.stringify({ fixtures,
      organizations: [scopes.ownerA.organizationId, scopes.ownerB.organizationId], modelBoundary: 'chat and embeddings: labelled STANDIN' }) });
    for (const [index, actor] of [scopes.ownerA, scopes.ownerB].entries()) {
      const data = fixtures[index];
      const foreign = fixtures[1 - index];
      const page = await contexts[index].newPage();
      page.setDefaultTimeout(UI_READY);
      const prototypeRequests: string[] = [];
      page.on('request', request => { if (request.url().includes('/imports/')) prototypeRequests.push(request.url()); });
      await page.goto(`/dashboard/develop/${data.id}?tab=data`);
      let dialog = await openSelection(page, data);
      await dialog.getByRole('spinbutton', { name: 'How many' }).fill('1');
      await dialog.getByRole('textbox', { name: 'Description', exact: true }).fill(data.name);
      await dialog.getByRole('combobox', { name: 'Knowledge base', exact: true }).click();
      await expect(page.getByRole('option', { name: `${foreign.name}-kb`, exact: true })).toHaveCount(0);
      await page.getByRole('option', { name: `${data.name}-kb`, exact: true }).click();
      await dialog.getByRole('textbox', { name: 'Search Model', exact: true }).click();
      await page.getByLabel('Search Model', { exact: true }).fill(MODEL);
      await page.getByText(MODEL, { exact: true }).last().click();
      const pending = page.waitForResponse(r => new URL(r.url()).pathname === path(data.id, 'add_synthetic_data') && r.request().method() === 'POST');
      await dialog.getByRole('button', { name: 'Generate and save', exact: true }).click();
      const first = await pending;
      expect(first.status(), JSON.stringify(await first.json())).toBe(200);
      const config = first.request().postDataJSON();
      expect(config.reference_row_ids).toEqual([data.row]);
      expect(config.kb_id).toBe(data.kb);
      await expect(dialog.getByText('1 generated rows saved. Existing rows are unchanged.', { exact: true })).toBeVisible();
      await checkRows(scopes, probe, actor, data, 1, perturb);
      await dialog.getByRole('button', { name: 'Close', exact: true }).click();
      await page.reload();
      dialog = await openSelection(page, data);
      const saved = await scopes.send<{ result: { data: unknown } }>(actor, 'GET', path(data.id, 'synthetic-config'), undefined, { generation_mode: 'append' });
      expect(saved.body.result.data).toEqual(config);
      await dialog.getByRole('textbox', { name: 'Description', exact: true }).fill('unsaved');
      const repeat = page.waitForResponse(r => new URL(r.url()).pathname === path(data.id, 'update-synthetic-config') && r.request().method() === 'PUT');
      await dialog.getByRole('button', { name: 'Re-generate with saved configuration' }).click();
      const second = await repeat;
      expect(second.status(), JSON.stringify(await second.json())).toBe(200);
      expect(second.request().postDataJSON()).toEqual({ ...config, regenerate: true });
      await expect(dialog.getByText('1 generated rows saved. Existing rows are unchanged.', { exact: true })).toBeVisible();
      await dialog.getByRole('button', { name: 'Close', exact: true }).click();
      await page.reload();
      const identities = await checkRows(scopes, probe, actor, data, 2);
      await expect(page.locator(`.ag-row [col-id="${data.questionColumn}"]`)).toHaveText([
        'Empty', data.selected, `unselected-${data.name}`, ...Array(2).fill(`STANDIN | ${data.selected} | ${data.name}`),
      ], { timeout: UI_READY });
      await testInfo.attach(`grounded-${index}`, { contentType: 'image/png', body: await page.screenshot({ fullPage: true }) });
      for (const patch of [{ kb_id: foreign.kb }, { reference_row_ids: [foreign.row] }]) {
        expect((await scopes.send(actor, 'POST', path(data.id, 'add_synthetic_data'), { ...config, ...patch })).status)
          .toBe(perturb === 'isolation' ? 200 : 404);
      }
      const outsiders = [index === 0 ? scopes.ownerB : scopes.ownerA];
      if (index === 0) outsiders.push(scopes.withWorkspace(actor, scopes.emptyWorkspace.id));
      for (const outsider of outsiders) {
        expect((await scopes.send(outsider, 'POST', path(data.id, 'add_synthetic_data'), config)).status).toBe(404);
        expect((await scopes.send(outsider, 'PUT', path(data.id, 'update-synthetic-config'), { ...config, regenerate: true })).status).toBe(404);
      }
      expect(await checkRows(scopes, probe, actor, data, 2)).toEqual(identities);
      expect(prototypeRequests).toEqual([]);
    }
  } finally { await Promise.all(contexts.map(context => context.close())); }
});
