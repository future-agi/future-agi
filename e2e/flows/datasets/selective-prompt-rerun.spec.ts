import { readFileSync } from 'node:fs';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { MOCK_BASE, MOCK_MODEL, gatewayFile, registerMockModel, validateMockRouting } from '../../lib/managed-mock';
import type { TestActor } from '../../lib/provisioning';

// ManualDatasetCreateRequestSerializer, DatasetUpdateCellValueRequestSerializer,
// AddRunPromptSerializer and EditRunPromptColumnSerializer.
const CREATE = '/model-hub/develops/create-dataset-manually/';
const ADD_PROMPT = '/model-hub/develops/add_run_prompt_column/';
const EDIT_PROMPT = '/model-hub/develops/edit_run_prompt_column/';
// The live grid header's accessible menu name, columnActionMenuItems, RerunScopeField and RunPrompt.jsx.
const SCOPE_LABEL = { all: 'Re-run all rows', changed: 'Only rows whose inputs changed' } as const;
const UI_READY = 60_000;
// Prompt generations run on the Temporal tasks_l queue through the product runner.
const PROMPT_RUN = { timeout: 180_000, intervals: [2000, 5000] };
type Cell = { cell_value: string | null; status: string };
type Row = { row_id: string; [key: string]: unknown };
type Table = { result: { column_config: { id: string; name: string }[]; table: Row[] } };
type Stored = { row_id: string; value: string | null; value_infos: unknown; status: string; updated_at: Date };
type Print = { row_id: string; fingerprint: string };

test('DATA-E2E-011: rerun only changed Run Prompt rows through the real prompt runner', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-011', area: 'datasets',
    userGoal: 'Refresh Run Prompt outputs for changed inputs only while every other generated output stays as it was',
    steps: [
      'create two isolated datasets and a Run Prompt column answered by the labelled loopback model STANDIN',
      'open Configure Run, keep Re-run all rows and choose Update and Run',
      'change one input, open Configure Run, choose Only rows whose inputs changed and choose Update and Run',
      'save the changed scope again when no inputs changed',
    ],
    backendChecks: [
      'prompt outputs and input fingerprints persist under the owning dataset through the real prompt queue and runner and match API readback',
      'the changed Save request carries its scope, recomputes only the changed row and leaves the other output value, metadata and timestamp unchanged',
      'a no-change Save performs no output writes, and foreign-tenant and wrong-workspace edits are refused without writes',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Four PROMPT_RUN waits (720s), four UI_READY page loads (240s) and setup (60s).
  test.setTimeout(1_020_000);
  const a = scopes.ownerA;
  const b = scopes.ownerB;
  const name = `e2e-data11-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  // STANDIN: the harness loopback mock-llm answers every model call through the
  // local agentcc-gateway with `echo: <last user message>`. No external model is used.
  const model = await registerMockModel(a as unknown as TestActor, probe,
    () => validateMockRouting(readFileSync(gatewayFile, 'utf8')));
  const dataset = (await a.api.post<{ result: { dataset_id: string } }>(CREATE,
    { dataset_name: name, number_of_rows: 2, number_of_columns: 1 })).result.dataset_id;
  const foreign = (await b.api.post<{ result: { dataset_id: string } }>(CREATE,
    { dataset_name: `${name}-foreign`, number_of_rows: 1, number_of_columns: 1 })).result.dataset_id;
  const readPath = `/model-hub/develops/${dataset}/get-dataset-table/`;
  const read = async () => (await a.api.get<Table>(readPath,
    { page_size: 500, current_page_index: 0, filters: '[]' })).result;
  const initial = await read();
  const input = initial.column_config[0];
  const rowIds = initial.table.map(row => row.row_id);
  expect(rowIds).toHaveLength(2);
  const write = (index: number, value: string) => a.api.post(`/model-hub/develops/${dataset}/update_cell_value/`,
    { row_id: rowIds[index], column_id: input.id, new_value: value });
  const [alpha, beta, gamma] = ['alpha', 'beta', 'gamma'].map(word => `${name}-${word}`);
  await write(0, alpha);
  await write(1, beta);
  const outputName = `${name}-answer`;
  await a.api.post(ADD_PROMPT, { dataset_id: dataset, name: outputName, config: {
    model: MOCK_MODEL, output_format: 'string', concurrency: 1,
    messages: [
      { role: 'system', content: [{ type: 'text', text: 'Repeat the input.' }] },
      { role: 'user', content: [{ type: 'text', text: `{{${input.id}}}` }] },
    ],
    run_prompt_config: { modelName: MOCK_MODEL, providers: 'openai', model_type: 'llm' },
  } });
  const columns = await probe.pg<{ id: string; source: string; source_id: string }>(
    'SELECT id, source, source_id FROM model_hub_column WHERE dataset_id=$1 AND name=$2 AND NOT deleted',
    [dataset, outputName]);
  expect(columns).toHaveLength(1);
  expect(columns[0].source).toBe('run_prompt');
  const output = columns[0].id;
  const prompter = async () => (await probe.pg<{ organization_id: string; dataset_id: string; model: string; status: string }>(
    'SELECT organization_id, dataset_id, model, status FROM model_hub_runprompter WHERE id=$1', [columns[0].source_id]))[0];
  const cells = async () => (await read()).table.map(row => row[output] as Cell);
  const values = async () => (await cells()).map(cell => [cell?.cell_value, cell?.status]);
  const stored = () => probe.pg<Stored>(
    'SELECT row_id, value, value_infos, status, updated_at FROM model_hub_cell WHERE dataset_id=$1 AND column_id=$2 AND NOT deleted ORDER BY row_id',
    [dataset, output]);
  const prints = () => probe.pg<Print>(
    'SELECT row_id, fingerprint FROM model_hub_generatedinputsnapshot WHERE column_id=$1 ORDER BY row_id', [output]);
  const row = (items: Stored[], index: number) => items.find(item => item.row_id === rowIds[index]);
  const print = (items: Print[], index: number) => items.find(item => item.row_id === rowIds[index])?.fingerprint;
  await testInfo.attach('selective-prompt-identities', { contentType: 'application/json', body: JSON.stringify({
    dataset, foreign, input, output, outputName, rowIds, prompter: columns[0].source_id, model: { id: model.id,
      name: model.model, route: MOCK_BASE }, organizationId: a.organizationId, workspaceId: a.workspaceId,
  }) });

  await expect.poll(async () => (await cells()).map(cell => cell?.status), PROMPT_RUN).toEqual(['pass', 'pass']);
  await expect.poll(async () => (await prompter()).status, PROMPT_RUN).toBe('Completed');
  // The mock serializes the populated content parts; only it can produce this text.
  const spellings = (text: string) => [`echo: ${JSON.stringify([{ type: 'text', text }])}`, `echo: ${text}`];
  const form = spellings(alpha).indexOf(String((await cells())[0]?.cell_value));
  expect(form, 'generated output is not the STANDIN reply to the populated prompt').toBeGreaterThanOrEqual(0);
  const echo = (text: string) => spellings(text)[form];
  expect(await values()).toEqual([[echo(alpha), 'pass'], [echo(beta), 'pass']]);
  expect(await probe.pg('SELECT organization_id, workspace_id FROM model_hub_dataset WHERE id=$1', [dataset]))
    .toEqual([{ organization_id: a.organizationId, workspace_id: a.workspaceId }]);
  expect(await prompter()).toEqual({ organization_id: a.organizationId, dataset_id: dataset, model: MOCK_MODEL,
    status: 'Completed' });
  expect((await prints()).map(item => item.row_id)).toEqual([...rowIds].sort());
  await model.assertReady();

  const context = await scopes.openContext(browser, a);
  await context.route('**/*', route => ['localhost', '127.0.0.1'].includes(new URL(route.request().url()).hostname)
    ? route.continue() : route.abort('blockedbyclient'));
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  const grid = page.locator(`.ag-center-cols-container .ag-row [col-id="${output}"]`);
  const save = async (scope: keyof typeof SCOPE_LABEL, message: string) => {
    await expect.poll(async () => (await prompter()).status, PROMPT_RUN).toBe('Completed');
    await page.goto(`/dashboard/develop/${dataset}`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: `${outputName} column menu`, exact: true }).click();
    await page.getByRole('menuitem', { name: 'Configure Run', exact: true }).click();
    await page.getByRole('radio', { name: SCOPE_LABEL[scope], exact: true }).check();
    const [saved] = await Promise.all([
      page.waitForResponse(res => new URL(res.url()).pathname === EDIT_PROMPT && res.request().method() === 'POST'),
      page.getByRole('button', { name: 'Update and Run', exact: true }).click(),
    ]);
    expect(saved.status()).toBe(200);
    expect(saved.request().postDataJSON()).toMatchObject({ dataset_id: dataset, column_id: output, scope });
    expect((await saved.json()).result).toBe(message);
    await expect(page.getByRole('radiogroup', { name: 'Rows to re-run on save' })).toHaveCount(0);
  };

  const beforeAll = await stored();
  const rewritten = async () => (await stored()).map(item =>
    item.updated_at > beforeAll.find(previous => previous.row_id === item.row_id)!.updated_at);
  await save('all', 'Run prompt column updated successfully');
  await expect.poll(rewritten, PROMPT_RUN).toEqual([true, true]);
  await expect.poll(async () => (await prompter()).status, PROMPT_RUN).toBe('Completed');
  expect(await values()).toEqual([[echo(alpha), 'pass'], [echo(beta), 'pass']]);
  const afterAll = await stored();
  const allPrints = await prints();
  expect(allPrints.map(item => item.row_id)).toEqual([...rowIds].sort());

  await write(1, gamma);
  await save('changed', 'Run prompt column updated successfully');
  await expect.poll(values, PROMPT_RUN).toEqual([[echo(alpha), 'pass'], [echo(gamma), 'pass']]);
  await expect.poll(async () => (await prompter()).status, PROMPT_RUN).toBe('Completed');
  await page.goto(`/dashboard/develop/${dataset}`, { waitUntil: 'domcontentloaded' });
  await expect(grid).toContainText([echo(alpha), echo(gamma)], { timeout: UI_READY });
  const afterChanged = await stored();
  const changedPrints = await prints();
  expect(row(afterChanged, 0)).toEqual(row(afterAll, 0));
  expect(row(afterChanged, 1)!.updated_at > row(afterAll, 1)!.updated_at).toBe(true);
  expect(print(changedPrints, 0)).toBe(print(allPrints, 0));
  expect(print(changedPrints, 1)).not.toBe(print(allPrints, 1));

  await save('changed', 'No rows need rerunning');
  expect(await stored()).toEqual(afterChanged);
  expect(await prints()).toEqual(changedPrints);
  expect((await prompter()).status).toBe('Completed');
  for (const actor of [b, scopes.withWorkspace(a, scopes.emptyWorkspace.id)]) {
    const denied = await scopes.send(actor, 'POST', EDIT_PROMPT,
      { dataset_id: dataset, column_id: output, name: outputName, config: {}, scope: 'all' });
    expect([403, 404]).toContain(denied.status);
    expect(await stored()).toEqual(afterChanged);
    expect(await prints()).toEqual(changedPrints);
    expect((await prompter()).status).toBe('Completed');
  }
  expect((await b.api.get<Table>(`/model-hub/develops/${foreign}/get-dataset-table/`)).result.table).toHaveLength(1);
  await testInfo.attach('persisted-selective-prompt-results', { contentType: 'application/json', body: JSON.stringify({
    beforeAll, afterAll, allPrints, afterChanged, changedPrints,
  }) });
  await context.close();
});
