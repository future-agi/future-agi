import { randomUUID } from 'node:crypto';
import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

type Envelope<T> = { result: T };
type Table = { column_config: { id: string; name: string }[];
  table: ({ row_id: string } & Record<string, unknown>)[] };
const datasetPath = (id: string, action: string) => `/model-hub/develops/${id}/${action}/`;
const detailPath = (id: string) => `/model-hub/eval-templates/${id}/detail/`;

test('@DSX-WALK-EVAL-84: inspect an experiment’s evaluation mappings without executing a judge or reading another tenant’s data', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DSX-WALK-EVAL-84', area: 'dsx-walks',
    userGoal: 'See which saved column and future run output each evaluation input uses before starting an experiment',
    steps: ['create one dataset row and one saved evaluation in each of two organizations',
      'open New experiment and add the saved evaluation from the Evaluations step',
      'read the exact input, source and sample-value cells in the mapping grid',
      'read back unchanged source cells and an empty bound-evaluations list',
      'deny the other organization’s evaluation and dataset reads'],
    backendChecks: ['the dataset table returns the seeded row id and exact question value',
      'passive mapping display posts no eval-playground, execute or experiment-create request',
      'no dataset evaluation bindings are created',
      'foreign evaluation detail is 404 and foreign dataset table is 400 DATASET_NOT_FOUND'],
  }),
}, async ({ browser, scopeActors: scopes }, testInfo) => {
  test.setTimeout(600_000);
  const ui = expect.configure({ timeout: 60_000 });
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  async function call<T>(actor: ScopeActor, method: string, path: string, body?: unknown, params?: Record<string, string>) {
    const response = await scopes.send<T>(actor, method, path, body, params);
    expect(response.status, `${method} ${path}`).toBe(200);
    return response.body;
  }
  async function seed(actor: ScopeActor, tag: string) {
    const name = `dsx-mapping-${tag}-${suffix}`;
    const dataset = await call<Envelope<{ dataset_id: string }>>(actor, 'POST',
      '/model-hub/develops/create-empty-dataset/', { new_dataset_name: name });
    const datasetId = dataset.result.dataset_id;
    // PasteRowsDialog.jsx:162,175,200 supplies these column/read/row bodies.
    await call(actor, 'POST', datasetPath(datasetId, 'add_static_column'), { new_column_name: 'question', column_type: 'text' });
    const rowId = randomUUID();
    const value = `Synthetic saved question ${tag} ${suffix}`;
    await call(actor, 'POST', datasetPath(datasetId, 'add_rows'), {
      rows: [{ id: rowId, cells: [{ column_name: 'question', value }] }],
    });
    const columns = await call<Envelope<Table>>(actor, 'GET', datasetPath(datasetId, 'get-dataset-table'), undefined,
      { column_config_only: 'true' });
    const question = columns.result.column_config.filter(c => c.name === 'question');
    expect(question).toHaveLength(1);
    // EvalCreatePage.jsx create-v2 and LLM update payload. This model is never run.
    const template = await call<Envelope<{ id: string }>>(actor, 'POST', '/model-hub/eval-templates/create-v2/', {
      name, eval_type: 'llm', instructions: 'Compare {{question}} with {{output}}',
      model: 'gpt-4o-mini', output_type: 'percentage', pass_threshold: 0.5,
      check_internet: false, error_localizer_enabled: false,
    });
    return { actor, name, datasetId, rowId, value, columnId: question[0].id, templateId: template.result.id };
  }
  const tenants = [await seed(scopes.ownerA, 'a'), await seed(scopes.ownerB, 'b')];
  await testInfo.attach('seeded-identities', { contentType: 'application/json', body: JSON.stringify(
    tenants.map(({ actor, ...ids }) => ({ ...ids, organizationId: actor.organizationId, workspaceId: actor.workspaceId }))) });
  for (const t of tenants) {
    const before = await call<Envelope<Table>>(t.actor, 'GET', datasetPath(t.datasetId, 'get-dataset-table'));
    expect(before.result.table).toHaveLength(1);
    expect(before.result.table[0]).toMatchObject({ row_id: t.rowId, [t.columnId]: { cell_value: t.value } });
    const context = await scopes.openContext(browser, t.actor);
    await context.route(url => ['http:', 'https:'].includes(url.protocol) &&
      ![new URL(E2E.appUrl).origin, new URL(E2E.apiUrl).origin].includes(url.origin), route => route.abort());
    try {
      const page = await context.newPage();
      page.setDefaultTimeout(60_000);
      const executions: string[] = [];
      page.on('request', request => {
        const path = new URL(request.url()).pathname;
        if (request.method() === 'POST' && (/eval-playground|execute|start_evals_process|run_user_eval/.test(path) ||
          path === '/model-hub/experiments/v2/')) executions.push(path);
      });
      await page.goto(`/dashboard/develop/${t.datasetId}`, { waitUntil: 'domcontentloaded' });
      await page.getByRole('button', { name: 'New experiment', exact: true }).click();
      await page.getByTestId('experiment-step-evaluations').click();
      await page.getByRole('button', { name: 'Add evaluation', exact: true }).click();
      const searched = page.waitForResponse(response => {
        const url = new URL(response.url());
        return url.pathname === datasetPath(t.datasetId, 'get_evals_list') &&
          url.searchParams.get('search_text') === t.name && response.request().method() === 'GET';
      });
      await page.getByPlaceholder('Search evaluations...', { exact: true }).fill(t.name);
      const searchResponse = await searched;
      expect(searchResponse.status()).toBe(200);
      expect(searchResponse.request().headers()['x-organization-id']).toBe(t.actor.organizationId);
      await ui(page.getByRole('progressbar', { name: 'Searching evaluations', exact: true })).toHaveCount(0);
      const row = page.getByRole('row').filter({ has: page.getByText(t.name, { exact: true }) });
      await ui(row).toHaveCount(1);
      await row.getByRole('button', { name: 'Add', exact: true }).click();
      // EvaluationStepExperimentCreation.jsx renders three direct children per mapping,
      // not an HTML table. These are the actual shipped sample/source labels.
      const card = page.getByTestId(`eval-card-${t.name}`);
      await ui(card).toBeVisible();
      const output = card.getByText('Produced by each run', { exact: true });
      await ui(output).toBeVisible();
      await ui(card.getByText(t.value, { exact: true })).toBeVisible();
      const cells = output.locator('..').locator(':scope > *');
      await ui(cells).toHaveCount(6);
      const values = await cells.allTextContents();
      expect([values.slice(0, 3), values.slice(3, 6)].sort(([left], [right]) => left.localeCompare(right))).toEqual([
        ['output', 'Run output', 'Produced by each run'], ['question', 'question', t.value],
      ]);
      const after = await call<Envelope<Table>>(t.actor, 'GET', datasetPath(t.datasetId, 'get-dataset-table'));
      expect(after.result.table).toEqual(before.result.table);
      const bindings = await call<Envelope<{ evals: unknown[] }>>(t.actor, 'GET', datasetPath(t.datasetId, 'get_evals_list'),
        undefined, { eval_type: 'user' });
      expect(bindings.result.evals).toEqual([]);
      expect(executions).toEqual([]);
      await testInfo.attach(`passive-mapping-${t.datasetId}`, { contentType: 'application/json', body: JSON.stringify({
        datasetId: t.datasetId, templateId: t.templateId, rowId: t.rowId, cells: values, executions,
      }) });
    } finally { await context.close(); }
  }
  for (const [own, foreign] of [[tenants[0], tenants[1]], [tenants[1], tenants[0]]]) {
    expect((await scopes.send(own.actor, 'GET', detailPath(own.templateId))).status).toBe(200);
    const template = await scopes.send(own.actor, 'GET', detailPath(foreign.templateId));
    expect(template.status).toBe(404);
    expect(JSON.stringify(template.body)).not.toContain(foreign.name);
    const dataset = await scopes.send(own.actor, 'GET', datasetPath(foreign.datasetId, 'get-dataset-table'));
    expect(dataset.status).toBe(400);
    expect(JSON.stringify(dataset.body)).toContain('The specified dataset does not exist or has been deleted.');
    expect(JSON.stringify(dataset.body)).not.toContain(foreign.value);
  }
});
