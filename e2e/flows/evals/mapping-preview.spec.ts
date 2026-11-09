import { randomUUID } from 'node:crypto';
import type { BrowserContext } from '@playwright/test';
import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// model_hub/urls.py; request fields from model_hub/serializers/contracts.py.
const CREATE_DATASET = '/model-hub/develops/create-empty-dataset/';
const CREATE_TEMPLATE = '/model-hub/eval-templates/create-v2/';
// EvalMappingPreviewRequest and EvalMappingPreviewResponse in views/eval_mapping_preview.py.
const PREVIEW = '/model-hub/eval-mapping-preview/';
const UI_READY = 60_000;
// Independent expectation-only perturbation anchors, never used to seed inputs.
const EXPECTED_VALUE_SUFFIX = '';
const EXPECTED_FOREIGN_STATUS = 404;
const EXPECTED_NEW_BINDINGS = 0;

type Envelope<T> = { status: boolean; result: T };
type Input = { column: string | null; value: unknown; status: string; note: string | null };
type Preview = Envelope<{ version_number: number; rows: { row_id: string; inputs_used: Record<string, Input> }[] }>;
type Tenant = { actor: ScopeActor; datasetId: string; templateId: string; columnId: string;
  rowId: string; name: string; value: string };

const loopback = (url: string) => {
  const parsed = new URL(url);
  return !/^https?:$/.test(parsed.protocol) || ['localhost', '127.0.0.1', '[::1]'].includes(parsed.hostname);
};

test('EVAL-E2E-007: preview the saved inputs an evaluation reads without executing it', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-007', area: 'evals',
    userGoal: 'Inspect the saved sample inputs for a new experiment evaluation without running the evaluation or reading another tenant\'s data',
    steps: [
      'create a uniquely named dataset, saved row and evaluation through the public API in each of two organizations',
      'open New experiment, choose Evaluations and add the saved evaluation through its picker',
      'choose Data and inspect the saved question and the not-yet-available run output',
      'repeat the read-only request as each tenant and reject foreign datasets, rows, columns and evaluations',
      'read the original saved row again and confirm previewing created no evaluation bindings or results',
    ],
    backendChecks: [
      'the actual browser preview request carries the current dataset, saved row and evaluation and returns the persisted question exactly',
      'each tenant can preview its own saved input while foreign dataset, row, column and template identifiers are refused',
      'the public row readback and persisted source cells are unchanged, no evaluation bindings or result cells are created and the browser never posts an execution request',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Provisioning 120 s + API seeding 60 s + eight UI_READY actions + final reads 60 s.
  test.setTimeout(720_000);
  const ui = expect.configure({ timeout: UI_READY });
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const attach = (name: string, data: unknown) => testInfo.attach(name, {
    contentType: 'application/json', body: JSON.stringify(data, null, 2),
  });
  const call = async <T,>(actor: ScopeActor, method: string, path: string, body?: unknown) => {
    const result = await scopes.send<T>(actor, method, path, body);
    expect(result.status, `${method} ${path}: ${JSON.stringify(result.body).slice(0, 300)}`).toBe(200);
    return result.body;
  };
  const source = (t: Tenant) => probe.pg<{ id: string; value: string }>(
    'SELECT id, value FROM model_hub_cell WHERE dataset_id = $1 AND row_id = $2 AND NOT deleted ORDER BY id',
    [t.datasetId, t.rowId]);
  const bindings = (t: Tenant) => probe.pg<{ n: string }>(
    'SELECT count(*)::text AS n FROM model_hub_userevalmetric WHERE dataset_id = $1', [t.datasetId]);
  // GetRowDataView.post / DatasetRowDataRequestSerializer: read-only POST with row_id.
  const rowRead = (t: Tenant) => call(t.actor, 'POST',
    `/model-hub/develops/${t.datasetId}/get-row-data/`, { row_id: t.rowId });
  const bodyOf = (t: Tenant) => ({ dataset_id: t.datasetId, template_id: t.templateId,
    row_ids: [t.rowId], config: { mapping: { question: t.columnId, output: 'output' } } });

  async function seed(actor: ScopeActor, label: string): Promise<Tenant> {
    const name = `e2e-map-${label}-${suffix}`;
    const dataset = await call<Envelope<{ dataset_id: string }>>(actor, 'POST', CREATE_DATASET,
      { new_dataset_name: `${name}-data` });
    const datasetId = dataset.result.dataset_id;
    await call(actor, 'POST', `/model-hub/develops/${datasetId}/add_static_column/`,
      { new_column_name: 'question', column_type: 'text' });
    const rowId = randomUUID();
    const value = `e2e-input-${label}-${suffix}`;
    // DatasetAddRowsRequestSerializer; use the public writer, never SQL seeding.
    await call(actor, 'POST', `/model-hub/develops/${datasetId}/add_rows/`,
      { rows: [{ id: rowId, cells: [{ column_name: 'question', value }] }] });
    const columns = await probe.pg<{ id: string }>(
      'SELECT id FROM model_hub_column WHERE dataset_id = $1 AND name = $2 AND NOT deleted',
      [datasetId, 'question']);
    expect(columns).toHaveLength(1);
    // This model is never executed. EvalTemplateCreateV2RequestSerializer saves a version.
    const template = await call<Envelope<{ id: string }>>(actor, 'POST', CREATE_TEMPLATE, {
      name, eval_type: 'llm', instructions: 'Compare {{question}} with {{output}}',
      model: 'gpt-4o-mini', output_type: 'percentage', pass_threshold: 0.5,
      check_internet: false, error_localizer_enabled: false,
    });
    return { actor, datasetId, rowId, columnId: columns[0].id, templateId: template.result.id, name, value };
  }

  const a = await seed(scopes.ownerA, 'a');
  const b = await seed(scopes.ownerB, 'b');
  expect(a.actor.organizationId).not.toBe(b.actor.organizationId);
  await attach('seeded-identities', [a, b].map(({ actor, ...t }) => ({ ...t,
    organizationId: actor.organizationId, workspaceId: actor.workspaceId })));
  const before = await source(a);
  const beforeRead = await rowRead(a);
  const beforeBindings = Number((await bindings(a))[0].n);
  expect(before.map(cell => cell.value)).toEqual([a.value]);

  const blocked = new Set<string>();
  const executionRequests: string[] = [];
  const context: BrowserContext = await scopes.openContext(browser, a.actor);
  await context.route(url => !loopback(url.toString()), route => {
    blocked.add(new URL(route.request().url()).host);
    return route.abort();
  });
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  page.on('request', request => {
    const pathname = new URL(request.url()).pathname;
    if (request.method() === 'POST' && (pathname === '/model-hub/eval-playground/'
      || pathname === '/model-hub/experiments/v2/' || pathname.includes('/run_user_eval/'))) {
      executionRequests.push(pathname);
    }
  });

  await test.step('open the experiment and add the saved evaluation', async () => {
    await page.goto(`/dashboard/develop/${a.datasetId}`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: 'New experiment', exact: true }).click();
    // StepperForExperiment.jsx: the native clickable step headers.
    await page.getByTestId('experiment-step-evaluations').click();
    await page.getByRole('button', { name: 'Add evaluation', exact: true }).click();
    // useEvalPickerData debounces search_text; do not click a row from the previous query.
    const searched = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === `/model-hub/develops/${a.datasetId}/get_evals_list/`
        && url.searchParams.get('search_text') === a.name && response.request().method() === 'GET';
    }, { timeout: UI_READY });
    await page.getByPlaceholder('Search evaluations...').fill(a.name);
    expect((await searched).status()).toBe(200);
    await ui(page.getByRole('progressbar', { name: 'Searching evaluations' })).toHaveCount(0);
    const row = page.getByRole('row').filter({ has: page.getByText(a.name, { exact: true }) });
    await ui(row).toHaveCount(1);
    await row.getByRole('button', { name: 'Add', exact: true }).click();
    const card = page.getByTestId(`eval-card-${a.name}`);
    await ui(card).toBeVisible();
    // EvalCard's unclaimed mapping preview must not leak through the table claim.
    await ui(card.getByText('Produced by each run', { exact: true })).toHaveCount(0);
    await ui(card.getByText(a.value, { exact: true })).toHaveCount(0);
  });

  await test.step('the browser preview equals the persisted input without a judge output', async () => {
    const responsePromise = page.waitForResponse(response => new URL(response.url()).pathname === PREVIEW
      && response.request().method() === 'POST', { timeout: UI_READY });
    await page.getByTestId('experiment-step-data').click();
    const response = await responsePromise;
    expect(response.status()).toBe(200);
    const sent = response.request().postDataJSON();
    expect(sent).toMatchObject(bodyOf(a));
    expect(response.request().headers()['x-organization-id']).toBe(a.actor.organizationId);
    expect(response.request().headers()['x-workspace-id']).toBe(a.actor.workspaceId);
    const body = await response.json() as Preview;
    expect(body.result.rows).toEqual([{ row_id: a.rowId, inputs_used: {
      question: { column: 'question', value: `${a.value}${EXPECTED_VALUE_SUFFIX}`, status: 'ok', note: null },
      output: { column: 'run output', value: null, status: 'pending', note: 'Available after the run' },
    } }]);
    const preview = page.getByTestId('reads-on-sample');
    await ui(preview.getByText('question ← question', { exact: true })).toBeVisible();
    await ui(preview.getByText(`= ${a.value}`, { exact: true })).toBeVisible();
    await ui(preview.getByText('output ← run output', { exact: true })).toBeVisible();
    await ui(preview.getByText('· Available after the run', { exact: true })).toBeVisible();
    await attach('browser-preview', { sent, body });
  });

  await test.step('both tenants have positive controls and refuse foreign identifiers', async () => {
    for (const t of [a, b]) {
      const own = await call<Preview>(t.actor, 'POST', PREVIEW, bodyOf(t));
      expect(own.result.rows[0].inputs_used.question.value).toBe(t.value);
    }
    const attempts = [
      bodyOf(a),
      { ...bodyOf(b), template_id: a.templateId },
      { ...bodyOf(b), row_ids: [a.rowId] },
      { ...bodyOf(b), config: { mapping: { question: a.columnId } } },
    ];
    const denials = [];
    for (const body of attempts) {
      const denied = await scopes.send(scopes.ownerB, 'POST', PREVIEW, body);
      denials.push(denied);
      expect(denied.status).toBe(EXPECTED_FOREIGN_STATUS);
      expect(JSON.stringify(denied.body)).not.toContain(a.value);
    }
    await attach('foreign-refusals', denials);
  });

  await test.step('previewing leaves the saved source and evaluation state unchanged', async () => {
    expect(await source(a)).toEqual(before);
    expect(await rowRead(a)).toEqual(beforeRead);
    expect(Number((await bindings(a))[0].n) - beforeBindings).toBe(EXPECTED_NEW_BINDINGS);
    expect(executionRequests).toEqual([]);
    await attach('unchanged-source', { before, after: await source(a), bindings: await bindings(a), executionRequests });
  });
  await attach('off-host-requests-blocked', [...blocked].sort());
});
