import { test, expect } from '../../lib/scope-actors';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';
import { CREATE, MODEL, STANDIN, resultCells, seedScope } from '../datasets/experiment-scope-seed';

// SelectionBar -> askFalconAboutRows -> openFalconAnalysis -> ChatInput.
// The pinned action PREFILLS; it does not send or generate a summary.
// This flow deliberately does not press Send and does not claim a model answer.
const UI_READY = 60_000;
type Run = { run_id: string; label: string; dataset_id: string; experiment_id: string; status: string };
type Results = { result: { total: number; rows: { example_id: string }[];
  scope: { complete: boolean }; pagination: { has_more: boolean } } };

test('@DSX-WALK-RES-02: Hand selected result identities to Falcon without sending row contents or exposing another tenant’s results', {
  tag: ['@flow'],
  annotation: flowAnnotation({ id: 'DSX-WALK-RES-02', area: 'dsx-walks',
    userGoal: 'Open the labelled Falcon selection action with an editable question and exact row/run references',
    steps: ['seed and finish a one-row prompt experiment through served APIs and managed mock-llm',
      'select its result row and choose Rule-based summary of this row (Falcon)',
      'inspect the exact prefilled question and context without sending it',
      'verify another organization cannot read the referenced run or result'],
    backendChecks: ['the result read contains the one selected example of the seeded run',
      'output is the exact mock-llm echo and its evaluation is Passed',
      'opening Falcon sends no conversation write or websocket chat frame and does not mutate result cells',
      'foreign results, run header and experiment detail reads return 404'],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe, request }, testInfo) => {
  test.setTimeout(720_000);
  const health = await request.get(`${STANDIN}/health`);
  expect(health.status()).toBe(200);
  expect(await health.json()).toEqual({ status: 'healthy' });
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const a = scopes.ownerA;
  const seed = await seedScope(a, probe, suffix, scopes);
  const name = `dsx-falcon-${suffix}`;
  const templates = await probe.pg<{ template_id: string }>(
    'SELECT template_id::text FROM model_hub_userevalmetric WHERE dataset_id=$1 AND name=$2 AND NOT deleted',
    [seed.datasetId, seed.evalName]);
  expect(templates).toHaveLength(1);
  const made = await scopes.send<{ result: string }>(a, 'POST', CREATE, {
    name, dataset_id: seed.datasetId, experiment_type: 'llm', column_id: null, row_ids: [seed.rowIds[0]],
    prompt_config: [{ name, prompt_id: seed.promptId, prompt_version: seed.promptVersion, model: MODEL,
      model_params: { max_tokens: 1000 } }],
    user_eval_metrics: [{ template_id: templates[0].template_id, name: `falcon-score-${suffix}`, model: MODEL,
      config: { mapping: { output: seed.columnId } } }],
  });
  expect(made.status).toBe(200);
  const match = /experiment_id=([0-9a-f-]{36})/.exec(made.body.result);
  expect(match).not.toBeNull();
  const id = match![1];
  await testInfo.attach('falcon-seeded-ids', { contentType: 'application/json', body: JSON.stringify({
    ...seed, experimentId: id, organizationId: a.organizationId, otherOrganizationId: scopes.ownerB.organizationId,
  }) });
  await expect.poll(async () => (await resultCells(probe, id)).map(cell => ({ source: cell.source,
    value: cell.value, status: cell.status })), POLL.ASYNC_JOB).toEqual([
    { source: 'experiment', value: `echo: [{"type":"text","text":"SCOPE:${suffix}"}]`, status: 'pass' },
    { source: 'experiment_evaluation', value: 'Passed', status: 'pass' },
  ]);
  const catalogPath = `/model-hub/datasets/${seed.datasetId}/runs/`;
  const catalog = () => a.api.get<{ result: { results: Run[]; count: number; has_more: boolean } }>(catalogPath, { limit: 500, offset: 0 });
  await expect.poll(async () => (await catalog()).result.results.map(r => r.experiment_id), POLL.ASYNC_JOB).toEqual([id]);
  const run = (await catalog()).result.results[0];
  expect(run).toMatchObject({ dataset_id: seed.datasetId, experiment_id: id, status: 'completed' });
  const resultsPath = `/model-hub/datasets/${seed.datasetId}/results/`;
  const params = { runs: run.run_id, page: 0, page_size: 1000 };
  const read = () => a.api.get<Results>(resultsPath, params);
  const result = (await read()).result;
  expect(result.total).toBe(1);
  expect(result.rows).toHaveLength(1);
  expect(result.scope.complete).toBe(true);
  expect(result.pagination.has_more).toBe(false);
  const example = result.rows[0].example_id;
  const before = await resultCells(probe, id);
  await testInfo.attach('falcon-selection-ids', { contentType: 'application/json', body: JSON.stringify({
    datasetId: seed.datasetId, experimentId: id, runId: run.run_id, exampleId: example,
  }) });
  const context = await scopes.openContext(browser, a);
  await context.route('**/*', route => ['localhost', '127.0.0.1'].includes(new URL(route.request().url()).hostname)
    ? route.continue() : route.abort('blockedbyclient'));
  try {
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    const sends: string[] = [];
    page.on('request', req => {
      const path = new URL(req.url()).pathname;
      if (path.startsWith('/falcon-ai/conversations/') && ['POST', 'PUT', 'PATCH', 'DELETE'].includes(req.method()))
        sends.push(`${req.method()} ${path}`);
    });
    page.on('websocket', socket => socket.on('framesent', frame => {
      const text = typeof frame.payload === 'string' ? frame.payload : frame.payload.toString('utf8');
      try { if (JSON.parse(text).type === 'chat') sends.push('websocket chat'); } catch { /* non-JSON heartbeat */ }
    }));
    await page.goto(`/dashboard/develop/experiment/${id}/data?datasetId=${seed.datasetId}`, { waitUntil: 'domcontentloaded' });
    // ResultsGrid pins the selection column left: output and checkbox live in
    // separate row fragments sharing row-id=data.example_id. Use the native checkbox.
    const grid = page.locator('[data-results-grid-root]');
    const row = grid.getByRole('row').filter({ hasText: `echo: [{"type":"text","text":"SCOPE:${suffix}"}]` }).first();
    await expect(row).toBeVisible();
    await expect(row).toHaveAttribute('row-id', example);
    const rowId = await row.getAttribute('row-id');
    const checkbox = grid.locator(`[row-id="${rowId}"] [col-id="ag-Grid-SelectionColumn"]`).getByRole('checkbox');
    await expect(checkbox).toHaveCount(1);
    await checkbox.check();
    await expect(checkbox).toBeChecked();
    const selection = page.getByTestId('results-selection-bar');
    await expect(selection.getByTestId('selection-count')).toContainText('1 selected');
    const action = 'Rule-based summary of this row (Falcon)';
    const direct = selection.getByRole('button', { name: action, exact: true });
    if (await direct.isVisible()) await direct.click();
    else {
      await selection.getByRole('button', { name: 'More actions', exact: true }).click();
      await page.getByRole('menuitem', { name: action, exact: true }).click();
    }
    const location = new URL(page.url());
    const expected = ['What happened on this row?', '', 'Comparison context:',
      `- dataset_id: ${seed.datasetId}`, `- experiment_id: ${id}`, `- runs: ${run.label}=${run.run_id}`,
      '- baseline: none', `- filters: ${JSON.stringify([{ type: 'rows', ids: [example], label: `1 row from ${run.label}` }])}`,
      `- analysis_url: ${location.pathname}${location.search}`].join('\n');
    const input = page.getByPlaceholder('Ask anything...', { exact: true });
    // The sidebar mounts only when the served falcon_ai capability is allowed
    // (layouts/dashboard/index.jsx). Read it explicitly: a failed or missing
    // read is never treated as a denial.
    const served = await scopes.send<{ features?: Record<string, { allowed?: boolean; reason?: string | null }> }>(
      a, 'GET', '/api/capabilities/');
    expect(served.status).toBe(200);
    const falcon = served.body.features?.falcon_ai;
    expect(typeof falcon?.allowed).toBe('boolean');
    await testInfo.attach('falcon-capability', { contentType: 'application/json', body: JSON.stringify({ allowed: falcon?.allowed }) });
    if (falcon?.allowed) {
      await expect(input).toBeVisible();
      await expect(input).toHaveValue(expected);
      expect(await input.inputValue()).not.toContain(`SCOPE:${suffix}`);
      expect(await input.inputValue()).not.toContain(`source-${seed.rowIds[0]}`);
    } else {
      // A blocked contextual open explains availability instead of doing nothing.
      const dialog = page.getByRole('dialog', { name: 'Falcon AI', exact: true });
      await expect(dialog).toBeVisible();
      await expect(dialog.getByText(falcon?.reason || 'Falcon AI is not available in this workspace.', { exact: true })).toBeVisible();
      await expect(input).toHaveCount(0);
    }
    expect(sends).toEqual([]);
    expect(await resultCells(probe, id)).toEqual(before);
    expect((await read()).result.rows.map(r => r.example_id)).toEqual([example]);
    expect(sends).toEqual([]);
  } finally { await context.close(); }
  expect((await scopes.send(scopes.ownerB, 'GET', resultsPath, undefined, params)).status).toBe(404);
  expect((await scopes.send(scopes.ownerB, 'GET', `/model-hub/experiment-runs/${run.run_id}/`)).status).toBe(404);
  expect((await scopes.send(scopes.ownerB, 'GET', `${CREATE}${id}/`)).status).toBe(404);
  expect((await read()).result.rows.map(r => r.example_id)).toEqual([example]);
  expect(await resultCells(probe, id)).toEqual(before);
});
