import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { POLL } from '../../lib/state-probe';

// ManuallyCreateDatasetView / ManualDatasetCreateRequestSerializer.
const CREATE = '/model-hub/develops/create-dataset-manually/';
// EvalTemplateCreateV2RequestSerializer; only the external LLM is a STANDIN.
const TEMPLATES = '/model-hub/eval-templates/create-v2/';
// The isolated stack's gateway routes this model to its loopback-network echo LLM.
const MODEL = 'gpt-4o-mini';
const UI_READY = 60_000;
type Column = { id: string; name: string; source_type: string; data_type: string };
type Row = { row_id: string; [column: string]: unknown };
type Table = { result: { table: Row[]; column_config: Column[];
  metadata: { counts: { failed_rows: number; failed_by_eval: { column_id: string; name: string; count: number }[] } } } };
const failed = (ids?: string[]) => ({ column_id: ids ? 'system:row_failed_in' : 'system:row_failed',
  filter_config: { filter_type: ids ? 'categorical' : 'boolean', filter_op: ids ? 'in' : 'equals',
    filter_value: ids ?? true } });

test('DATA-E2E-048: failed evaluation filters preserve the exact row navigation scope', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-048', area: 'datasets',
    userGoal: 'Find rows with failed stored evaluations and inspect only those rows without crossing tenant boundaries',
    steps: ['create four uniquely named rows and two pass/fail evaluations through real APIs using a labelled external LLM STANDIN',
      'turn on Failed only and read the exact failed row set',
      'select one evaluator and open a filtered row',
      'navigate to the next failed row and reload the same filtered view',
      'turn the filter off and verify every row returns'],
    backendChecks: ['the dataset and completed evaluation cells persist with exact row IDs and organization/workspace scope',
      'table counts and filtered row sets agree with stored evaluation verdicts, including overlapping failures',
      'the row reader receives the selected evaluation filter and returns only matching adjacency after reload',
      'foreign organization/workspace dataset reads and foreign or invalid filter-column IDs are refused'],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // EVAL_RESULT 90 + 8 UI_READY 480 + setup and synchronous readback 150.
  test.setTimeout(720_000);
  const a = scopes.ownerA;
  const b = scopes.ownerB;
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const name = `e2e-data3-${suffix}`;
  const markers = [0, 1, 2, 3].map(i => `${name}-row-${i}`);
  const created = await a.api.post<{ result: { dataset_id: string } }>(CREATE,
    { dataset_name: name, number_of_rows: 4, number_of_columns: 3 });
  const dataset = created.result.dataset_id;
  const tablePath = `/model-hub/develops/${dataset}/get-dataset-table/`;
  const rowPath = `/model-hub/develops/${dataset}/get-row-data/`;
  const read = (filters: unknown[] = []) => a.api.get<Table>(tablePath,
    { page_size: 10, current_page_index: 0, filters: JSON.stringify(filters) });
  const initial = (await read()).result;
  const rowIds = initial.table.map(row => row.row_id);
  expect(rowIds).toHaveLength(4);
  const inputs = initial.column_config;
  expect(inputs).toHaveLength(3);
  await testInfo.attach('seeded-filter-identities', { contentType: 'application/json', body: JSON.stringify({
    dataset, name, rowIds, inputs, markers, organizationId: a.organizationId, workspaceId: a.workspaceId,
    externalSystem: 'STANDIN: isolated echo LLM; dataset writers, eval worker, filtering and persistence are real',
  }) });
  for (let i = 0; i < rowIds.length; i++) {
    const values = [JSON.stringify({ result: i === 0 || i === 2 ? 'Fail' : 'Pass', explanation: markers[i] }),
      JSON.stringify({ result: i === 1 || i === 2 ? 'Fail' : 'Pass', explanation: markers[i] }), markers[i]];
    for (let col = 0; col < inputs.length; col++) {
      // DatasetUpdateCellValueRequestSerializer: seed ordinary editable inputs, never eval outputs.
      await a.api.post(`/model-hub/develops/${dataset}/update_cell_value/`,
        { row_id: rowIds[i], column_id: inputs[col].id, new_value: values[col] });
    }
  }
  await a.api.post('/model-hub/custom_models/create/', {
    model_provider: 'openai', model_name: MODEL, input_token_cost: 0, output_token_cost: 0,
    config_json: { key: 'local-dev-only-shared-secret-replace-me', api_base: 'http://agentcc-gateway:8080/v1' },
  });
  const template = await a.api.post<{ result: { id: string } }>(TEMPLATES, {
    name: `e2e-data3-judge-${suffix}`, eval_type: 'llm', model: MODEL,
    instructions: '{{output}}', output_type: 'pass_fail', pass_threshold: 0.5,
  });
  const evalNames = [`quality-${suffix}`, `safety-${suffix}`];
  for (let index = 0; index < 2; index++) {
    // AddUserEvalView / UserEvalMutationRequestSerializer. The real scheduler and
    // worker produce the stored results; no SQL writes or response interception.
    await a.api.post(`/model-hub/develops/${dataset}/add_user_eval/`, {
      name: evalNames[index], template_id: template.result.id, model: MODEL, run: true,
      config: { mapping: { output: inputs[index].id }, reason_column: true }, error_localizer: false,
    });
  }
  await expect.poll(async () => {
    const cells = await probe.pg<{ n: string }>(
      `SELECT count(*) AS n FROM model_hub_cell c JOIN model_hub_column k ON c.column_id=k.id
       WHERE c.dataset_id=$1 AND k.source='evaluation' AND c.status='pass'
       AND lower(c.value) IN ('true','false','passed','failed') AND NOT c.deleted AND NOT k.deleted`, [dataset]);
    return cells[0].n;
  }, POLL.EVAL_RESULT).toBe('8');
  const stored = await probe.pg<{ row_id: string; name: string; value: string }>(
    `SELECT c.row_id, k.name, lower(c.value) AS value FROM model_hub_cell c JOIN model_hub_column k ON c.column_id=k.id
     WHERE c.dataset_id=$1 AND k.source='evaluation' AND NOT c.deleted ORDER BY k.name, c.row_id::text`, [dataset]);
  const verdict = (value: string) => ['false', 'failed'].includes(value);
  for (const [index, evalName] of evalNames.entries()) {
    expect(stored.filter(cell => cell.name === evalName && verdict(cell.value)).map(cell => cell.row_id).sort())
      .toEqual([rowIds[index], rowIds[2]].sort());
  }
  expect(await probe.pg<{ name: string; organization_id: string; workspace_id: string }>(
    'SELECT name, organization_id, workspace_id FROM model_hub_dataset WHERE id=$1 AND NOT deleted', [dataset]))
    .toEqual([{ name, organization_id: a.organizationId, workspace_id: a.workspaceId }]);
  const all = (await read()).result;
  const evalColumns = evalNames.map(evalName => all.column_config.find(column => column.name === evalName)!);
  expect(evalColumns.every(column => column?.data_type === 'boolean')).toBe(true);
  expect(all.metadata.counts.failed_rows).toBe(3);
  expect(all.metadata.counts.failed_by_eval).toEqual(evalColumns.map(column => ({ column_id: column.id, name: column.name, count: 2 })));
  expect((await read([failed()])).result.table.map(row => row.row_id)).toEqual(rowIds.slice(0, 3));
  expect((await read([failed([evalColumns[0].id])])).result.table.map(row => row.row_id)).toEqual([rowIds[0], rowIds[2]]);
  await testInfo.attach('persisted-evaluation-verdicts', { contentType: 'application/json', body: JSON.stringify({ stored, evalColumns }) });

  const foreign = await b.api.post<{ result: { dataset_id: string } }>(CREATE,
    { dataset_name: `${name}-foreign`, number_of_rows: 1, number_of_columns: 1 });
  const foreignTable = await b.api.get<Table>(`/model-hub/develops/${foreign.result.dataset_id}/get-dataset-table/`);
  const invalidIds = [foreignTable.result.column_config[0].id, inputs[0].id, '00000000-0000-4000-8000-000000000001'];
  for (const columnId of invalidIds) {
    const filters = [failed([columnId])];
    expect((await scopes.send(a, 'GET', tablePath, undefined, { filters: JSON.stringify(filters) })).status).toBe(400);
    expect((await scopes.send(a, 'POST', rowPath, { row_id: rowIds[0], filters })).status).toBe(400);
  }
  for (const denied of [b, scopes.withWorkspace(a, scopes.emptyWorkspace.id)]) {
    expect([400, 403, 404]).toContain((await scopes.send(denied, 'GET', tablePath)).status);
    expect([403, 404]).toContain((await scopes.send(denied, 'POST', rowPath,
      { row_id: rowIds[0], filters: [failed()] })).status);
  }
  expect((await read()).result.table.map(row => row.row_id)).toEqual(rowIds);

  const context = await scopes.openContext(browser, a);
  // Runtime traffic stays on fixture loopback. External asset/telemetry requests
  // are blocked, not mocked; API responses always come from the real backend.
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    return ['localhost', '127.0.0.1'].includes(url.hostname) ? route.continue() : route.abort('blockedbyclient');
  });
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  await page.goto(`/dashboard/develop/${dataset}`, { waitUntil: 'domcontentloaded' });
  const markerCells = page.locator(`.ag-center-cols-container .ag-row [col-id="${inputs[2].id}"]`);
  await expect(markerCells).toHaveText(markers, { timeout: UI_READY });
  const toggle = page.getByRole('button', { name: /^Failed only/ });
  const filtered = page.waitForResponse(response => new URL(response.url()).pathname === tablePath
    && decodeURIComponent(response.url()).includes('system:row_failed'), { timeout: UI_READY });
  await toggle.click();
  expect((await filtered).status()).toBe(200);
  await expect(markerCells).toHaveText(markers.slice(0, 3), { timeout: UI_READY });
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: 'Failures by eval', exact: true }).click();
  await page.getByRole('checkbox', { name: `Only rows failing ${evalNames[0]}`, exact: true }).click();
  await page.keyboard.press('Escape');
  await expect(markerCells).toHaveText([markers[0], markers[2]], { timeout: UI_READY });
  await expect(page).toHaveURL(new RegExp(`fevals=${evalColumns[0].id}`), { timeout: UI_READY });

  // DevelopDataV2 opens the focused row with Enter. RowView keeps the same
  // query filters; its next-row button is labelled by RowViewHeader.
  const opened = page.waitForResponse(response => new URL(response.url()).pathname === rowPath
    && response.request().method() === 'POST', { timeout: UI_READY });
  await markerCells.first().click();
  await page.keyboard.press('Enter');
  const rowResponse = await opened;
  expect(rowResponse.status()).toBe(200);
  expect(rowResponse.request().postDataJSON().filters).toContainEqual(failed([evalColumns[0].id]));
  expect((await rowResponse.json()).result.next.row_id).toEqual([rowIds[2]]);
  await expect(page).toHaveURL(new RegExp(`row=${rowIds[0]}`), { timeout: UI_READY });
  const next = page.waitForResponse(response => new URL(response.url()).pathname === rowPath
    && response.request().postDataJSON()?.row_id === rowIds[2], { timeout: UI_READY });
  await page.getByRole('button', { name: /Next row/i }).click();
  expect((await next).status()).toBe(200);
  await expect(page).toHaveURL(new RegExp(`row=${rowIds[2]}`), { timeout: UI_READY });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(markerCells).toHaveText([markers[0], markers[2]], { timeout: UI_READY });
  await page.keyboard.press('Escape');
  await toggle.click();
  await expect(markerCells).toHaveText(markers, { timeout: UI_READY });
  await testInfo.attach('filter-proof-url', { body: page.url(), contentType: 'text/plain' });
  await context.close();
});
