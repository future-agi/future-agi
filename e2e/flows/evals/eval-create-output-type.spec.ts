import { randomUUID } from 'node:crypto';
import type { Page, Request } from '@playwright/test';
import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { inspectManagedMock, registerMockModel } from '../../lib/managed-mock';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// Public wires, pinned from model_hub/urls.py and model_hub/serializers/contracts.py.
const CREATE_TEMPLATE = '/model-hub/eval-templates/create-v2/';
const templatePath = (id: string, action: string) => `/model-hub/eval-templates/${id}/${action}/`;
const datasetPath = (id: string, action: string) => `/model-hub/develops/${id}/${action}/`;
const CREATE_DATASET = '/model-hub/develops/create-empty-dataset/';
// The judge reply is the managed loopback echo of the rendered prompt (labelled STANDIN).
const PASS_FAIL_PROMPT = 'Reply with exactly this JSON: {"result":"{{verdict}}","explanation":"verdict {{verdict}}"}';
const SCORING_PROMPT = 'Reply with exactly this JSON: {"result":{{score}},"explanation":"score {{score}}"}';
const UI_READY = 60_000;
// EXPECTATION-ONLY fallibility anchors, never used to build a seed or request.
const EXPECTED_SCORING_TYPE = 'percentage'; // OUT_NEG_STORAGE: what the Scoring radio saves
const EXPECTED_HIGH_SCORE = '0.8'; // OUT_NEG_EXECUTION: the judged score cell of the first row
const EXPECTED_FOREIGN = [400, 403, 404]; // OUT_NEG_TENANCY: another organization's evaluation or dataset
// The output types each create form offers: the Create page and the dataset drawer's Create New Eval.
const OFFERED_OUTPUT_TYPES = ['Pass/fail', 'Scoring', 'Choices'];

type Envelope<T> = { result: T };
type Binding = { id: string; name: string; status: string; column_id: string | null; pass_threshold: number | null };
type Cell = { row_id: string; column_id: string; value: string | null; status: string; source: string };
type Kind = 'verdict' | 'scoring';
type Tenant = { actor: ScopeActor; tag: string; datasetId: string; rows: [string, string];
  columns: Record<'verdict' | 'score', string>; names: Record<Kind, string>; templates: Record<Kind, string>;
  bindings: Record<Kind, string> };
type TemplateRow = { name: string; output_type_normalized: string; pass_threshold: number | null;
  choice_scores: unknown; visible_ui: boolean; organization_id: string; workspace_id: string };

test('EVAL-E2E-015: each create form offers Pass/fail, Scoring and Choices, and the Pass/fail and Scoring types chosen there reach judged dataset cells', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-015', area: 'evals',
    userGoal: 'Choose an evaluation\'s output type (Pass/fail, Scoring or Choices) and pass threshold when I create it on the Create page or from a dataset\'s Create New Eval, run it on a dataset and read each row\'s judged result, for my organization only',
    steps: [
      'in each of two organizations, register the loopback judge STANDIN model and a dataset with verdict and score inputs on two rows',
      'on the Create page, see the offered output types Pass/fail, Scoring and Choices, save a Pass/fail evaluation and attach it to the dataset through the public API',
      'from the dataset\'s Evaluate drawer, choose Create New Eval, see the same offered output types, pick Scoring, remove the seeded score-mapping row, move the pass threshold to 60% and choose Save & Add Evaluation',
      'run both evaluations from the Evaluate drawer and read their cells from the public dataset table and PostgreSQL',
      'as the other organization, try to read either evaluation, run it on the first organization\'s dataset and read that dataset\'s table',
    ],
    backendChecks: [
      'each publish request carries the chosen output type and pass threshold; PostgreSQL stores them, with no choice mapping, under the actor organization and workspace',
      'the drawer\'s eval list reports the 60% pass rule for the Scoring evaluation',
      'each native run completes: the Pass/fail cells read Passed and Failed and the Scoring cells hold the judged numbers, with the judge reasons beside them',
      'the other organization cannot read or run either evaluation or read the dataset table, and nothing changes',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Provision/setup 120 + two tenants * (10 UI60 + 2 EVAL90 + CDC180) + headroom.
  test.setTimeout(1_800_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const ui = expect.configure({ timeout: UI_READY });
  const attach = (name: string, value: unknown) => testInfo.attach(name, {
    contentType: 'application/json', body: JSON.stringify(value, (_key, item) =>
      typeof item === 'string' && /^[a-f0-9]{64}$/.test(item) ? `sha256:${item}` : item),
  });
  const routing = inspectManagedMock({ evalBackground: true });
  await attach('external-judge-STANDIN', { label: 'Loopback echo judge STANDIN, not live-model quality proof', routing });
  const assertStack = () => expect(inspectManagedMock({ evalBackground: true })).toEqual(routing);
  const models: Record<string, string> = {};
  for (const owner of scopes.owners) models[owner.organizationId] = (await registerMockModel(owner, probe, assertStack)).model;
  const call = async <T,>(actor: ScopeActor, method: string, path: string, body?: unknown, params?: Record<string, string>) => {
    const response = await scopes.send<T>(actor, method, path, body, params);
    expect(response.status, `${method} ${path}: ${JSON.stringify(response.body).slice(0, 400)}`).toBe(200);
    return response.body;
  };
  const list = async (t: Tenant) => (await call<Envelope<{ evals: Binding[] }>>(t.actor, 'GET',
    datasetPath(t.datasetId, 'get_evals_list'), undefined, { eval_type: 'user' })).result.evals;
  const cells = (datasetId: string) => probe.pg<Cell>(
    `SELECT c.row_id::text AS row_id, c.column_id::text AS column_id, c.value, c.status, col.source
     FROM model_hub_cell c JOIN model_hub_column col ON col.id = c.column_id
     WHERE c.dataset_id = $1 AND NOT c.deleted AND NOT col.deleted AND col.source IN ('evaluation', 'evaluation_reason')
     ORDER BY col.source, c.column_id, c.row_id`, [datasetId]);
  const template = async (id: string) => (await probe.pg<TemplateRow>(
    `SELECT name, output_type_normalized, pass_threshold, choice_scores, visible_ui,
       organization_id::text AS organization_id, workspace_id::text AS workspace_id
     FROM model_hub_evaltemplate WHERE id = $1`, [id]))[0];
  const bindingRows = (datasetId: string) => probe.pg<{ id: string; name: string; template_id: string;
    organization_id: string; workspace_id: string }>(
    `SELECT id::text AS id, name, template_id::text AS template_id, organization_id::text AS organization_id,
       workspace_id::text AS workspace_id FROM model_hub_userevalmetric WHERE dataset_id = $1 AND NOT deleted ORDER BY name`,
    [datasetId]);
  function scoped(t: Tenant, request: Request) {
    expect(request.headers()['x-organization-id']).toBe(t.actor.organizationId);
    expect(request.headers()['x-workspace-id']).toBe(t.actor.workspaceId);
  }

  async function seed(actor: ScopeActor, tag: string): Promise<Tenant> {
    const base = `e2e-ev15-${tag}-${suffix}`;
    const d = await call<Envelope<{ dataset_id: string }>>(actor, 'POST', CREATE_DATASET, { new_dataset_name: base });
    const datasetId = d.result.dataset_id;
    for (const name of ['verdict', 'score'])
      await call(actor, 'POST', datasetPath(datasetId, 'add_static_column'), { new_column_name: name, column_type: 'text' });
    const rows: [string, string] = [randomUUID(), randomUUID()];
    await call(actor, 'POST', datasetPath(datasetId, 'add_rows'), { rows: [
      { id: rows[0], cells: [{ column_name: 'verdict', value: 'Pass' }, { column_name: 'score', value: '0.8' }] },
      { id: rows[1], cells: [{ column_name: 'verdict', value: 'Fail' }, { column_name: 'score', value: '0.3' }] },
    ] });
    const columns = Object.fromEntries((await probe.pg<{ id: string; name: string }>(
      "SELECT id::text AS id, name FROM model_hub_column WHERE dataset_id=$1 AND NOT deleted AND name IN ('verdict','score')",
      [datasetId])).map(c => [c.name, c.id])) as Tenant['columns'];
    expect(Object.keys(columns).sort()).toEqual(['score', 'verdict']);
    return { actor, tag, datasetId, rows, columns, names: { verdict: `${base}-verdict`, scoring: `${base}-scoring` },
      templates: { verdict: '', scoring: '' }, bindings: { verdict: '', scoring: '' } };
  }

  async function open(actor: ScopeActor): Promise<Page> {
    const context = await scopes.openContext(browser, actor);
    await context.route(url => /^https?:$/.test(url.protocol) && !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname),
      route => route.abort());
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    return page;
  }
  const drafted = (page: Page) => page.waitForResponse(r => new URL(r.url()).pathname === CREATE_TEMPLATE
    && r.request().method() === 'POST', { timeout: UI_READY });
  async function describeJudge(page: Page, t: Tenant, prompt: string) {
    await page.getByRole('tab', { name: 'LLM-As-A-Judge', exact: true }).click();
    await page.locator('.ql-editor[contenteditable="true"]').first().fill(prompt);
    await page.getByText('Select model', { exact: true }).first().click();
    await page.getByPlaceholder('Search models...').fill(models[t.actor.organizationId]);
    await page.getByRole('menuitem').filter({ has: page.getByText(models[t.actor.organizationId], { exact: true }) })
      .first().click();
  }
  async function stored(t: Tenant, kind: Kind, outputType: string, threshold: number) {
    const row = await template(t.templates[kind]);
    expect(row).toMatchObject({ name: t.names[kind], visible_ui: true, organization_id: t.actor.organizationId,
      workspace_id: t.actor.workspaceId, choice_scores: null });
    expect(row.output_type_normalized, kind === 'scoring' ? 'OUT_NEG_STORAGE' : undefined).toBe(outputType);
    expect(Number(row.pass_threshold)).toBeCloseTo(threshold, 5);
    return row;
  }
  async function offersOutputTypes(page: Page) {
    for (const name of OFFERED_OUTPUT_TYPES) await ui(page.getByRole('radio', { name, exact: true })).toBeEnabled();
  }
  const publishOf = (page: Page, id: string) => page.waitForResponse(r => new URL(r.url()).pathname === templatePath(id, 'update')
    && r.request().method() === 'PUT' && r.request().postDataJSON()?.publish === true, { timeout: UI_READY });

  async function verdictOnCreatePage(t: Tenant) {
    const page = await open(t.actor);
    const draft = drafted(page);
    await page.goto('/dashboard/evaluations/create', { waitUntil: 'domcontentloaded' });
    const drafting = await draft;
    expect(drafting.status()).toBe(200);
    t.templates.verdict = (await drafting.json() as Envelope<{ id: string }>).result.id;
    await page.getByPlaceholder('e.g. hallucination_detector').fill(t.names.verdict);
    await describeJudge(page, t, PASS_FAIL_PROMPT);
    await ui(page.getByRole('radio', { name: 'Pass/fail', exact: true })).toBeChecked();
    await offersOutputTypes(page);
    const published = publishOf(page, t.templates.verdict);
    await page.getByRole('button', { name: 'Save Evaluation', exact: true }).click();
    const response = await published;
    expect(response.status()).toBe(200);
    scoped(t, response.request());
    expect(response.request().postDataJSON()).toMatchObject({ name: t.names.verdict, output_type: 'pass_fail',
      model: models[t.actor.organizationId], choice_scores: null, publish: true });
    await page.waitForURL(`**/dashboard/evaluations/${t.templates.verdict}`, { timeout: UI_READY });
    await stored(t, 'verdict', 'pass_fail', 0.5);
    await page.context().close();
    await call(t.actor, 'POST', datasetPath(t.datasetId, 'add_user_eval'), {
      name: t.names.verdict, template_id: t.templates.verdict, model: models[t.actor.organizationId], run: false,
      config: { mapping: { verdict: t.columns.verdict }, config: {} },
    });
  }

  async function openEvaluate(page: Page) {
    const more = page.locator('[data-action-bar="grid"]').getByRole('button', { name: 'More actions', exact: true });
    const evaluate = page.getByRole('menuitem', { name: /^Evaluate/ });
    await expect(async () => {
      if (await evaluate.isVisible()) await page.keyboard.press('Escape');
      await more.click();
      await expect(evaluate).toBeEnabled({ timeout: 2_000 });
    }).toPass({ timeout: UI_READY });
    await evaluate.click();
  }
  const evalRow = (page: Page, name: string) => page.getByTestId('saved-eval-row').filter({ visible: true })
    .filter({ has: page.getByText(name, { exact: true }) });

  async function scoringFromDataset(t: Tenant) {
    const page = await open(t.actor);
    await page.goto(`/dashboard/develop/${t.datasetId}`, { waitUntil: 'domcontentloaded' });
    await openEvaluate(page);
    await ui(evalRow(page, t.names.verdict)).toHaveCount(1);
    await page.getByText(/^(?:Evals \(\d+\)|\d+ of \d+ selected)$/).locator('..')
      .getByRole('button', { name: 'Add', exact: true }).click();
    const draft = drafted(page);
    await page.getByRole('button', { name: 'Create New Eval', exact: true }).click();
    const drafting = await draft;
    expect(drafting.status()).toBe(200);
    t.templates.scoring = (await drafting.json() as Envelope<{ id: string }>).result.id;
    await describeJudge(page, t, SCORING_PROMPT);
    await page.getByPlaceholder('e.g. hallucination_detector').fill(t.names.scoring);
    await offersOutputTypes(page);
    await page.getByRole('radio', { name: 'Scoring', exact: true }).check();
    // Picking Scoring seeds one score-mapping row; a judged-number Scoring eval saves none.
    const seeded = page.getByPlaceholder('Choice name', { exact: true });
    await ui(seeded).toHaveValue('Choice 1');
    await seeded.locator('xpath=..').locator('xpath=..').locator('xpath=..').getByRole('button').click();
    await ui(seeded).toHaveCount(0);
    const threshold = page.getByText('Pass Threshold', { exact: true }).locator('xpath=..').getByRole('slider');
    await ui(threshold).toHaveAttribute('aria-valuenow', '50');
    await threshold.focus();
    for (let i = 0; i < 10; i += 1) await threshold.press('ArrowRight');
    await ui(threshold).toHaveAttribute('aria-valuenow', '60');
    await ui(page.getByText('Map all variables to enable testing & saving', { exact: true })).toHaveCount(0);
    const published = publishOf(page, t.templates.scoring);
    const added = page.waitForResponse(r => new URL(r.url()).pathname === datasetPath(t.datasetId, 'add_user_eval')
      && r.request().method() === 'POST', { timeout: UI_READY });
    await page.getByRole('button', { name: 'Save & Add Evaluation', exact: true }).click();
    const response = await published;
    expect(response.status()).toBe(200);
    scoped(t, response.request());
    expect(response.request().postDataJSON()).toMatchObject({ name: t.names.scoring, output_type: 'percentage',
      model: models[t.actor.organizationId], pass_threshold: 0.6, choice_scores: null, publish: true });
    const attached = await added;
    expect(attached.status()).toBe(200);
    scoped(t, attached.request());
    expect(attached.request().postDataJSON()).toMatchObject({ name: t.names.scoring, template_id: t.templates.scoring,
      config: { mapping: { score: t.columns.score } } });
    await stored(t, 'scoring', EXPECTED_SCORING_TYPE, 0.6);
    await page.context().close();
  }

  async function runBoth(t: Tenant) {
    const rows = await bindingRows(t.datasetId);
    expect(rows.map(r => r.name)).toEqual([t.names.scoring, t.names.verdict].sort());
    for (const r of rows) {
      expect(r).toMatchObject({ organization_id: t.actor.organizationId, workspace_id: t.actor.workspaceId });
      const kind: Kind = r.name === t.names.verdict ? 'verdict' : 'scoring';
      expect(r.template_id).toBe(t.templates[kind]);
      t.bindings[kind] = r.id;
    }
    const listed = Object.fromEntries((await list(t)).map(b => [b.id, b]));
    expect(Number(listed[t.bindings.scoring].pass_threshold)).toBeCloseTo(0.6, 5);
    const page = await open(t.actor);
    await page.goto(`/dashboard/develop/${t.datasetId}`, { waitUntil: 'domcontentloaded' });
    await openEvaluate(page);
    for (const kind of ['verdict', 'scoring'] as const) {
      const row = evalRow(page, t.names[kind]);
      await ui(row).toHaveCount(1);
      await assertStack();
      const runPath = datasetPath(t.datasetId, 'start_evals_process');
      const started = page.waitForResponse(r => new URL(r.url()).pathname === runPath && r.request().method() === 'POST');
      await row.getByRole('button', { name: 'Run', exact: true }).click();
      await ui(row.getByText('Run this evaluation on all rows?', { exact: true })).toBeVisible();
      await row.getByRole('button', { name: 'Run', exact: true }).filter({ hasText: 'Run' }).click();
      const response = await started;
      expect(response.status()).toBe(200);
      expect(response.request().postDataJSON()).toEqual({ user_eval_ids: [t.bindings[kind]] });
      await expect.poll(async () => (await list(t)).find(b => b.id === t.bindings[kind])?.status, POLL.EVAL_RESULT)
        .toBe('Completed');
    }
    const after = Object.fromEntries((await list(t)).map(b => [b.id, b]));
    const judged = await cells(t.datasetId);
    const cell = (kind: Kind, rowId: string) => judged.find(c => c.source === 'evaluation' && c.row_id === rowId
      && c.column_id === after[t.bindings[kind]].column_id);
    expect([cell('verdict', t.rows[0])?.value, cell('verdict', t.rows[1])?.value]).toEqual(['Passed', 'Failed']);
    expect(cell('scoring', t.rows[0])?.value, 'OUT_NEG_EXECUTION').toBe(EXPECTED_HIGH_SCORE);
    expect(cell('scoring', t.rows[1])?.value).toBe('0.3');
    for (const kind of ['verdict', 'scoring'] as const)
      for (const rowId of t.rows) expect(cell(kind, rowId)?.status).toBe('pass');
    expect(judged.filter(c => c.source === 'evaluation_reason').map(c => c.value).sort())
      .toEqual(['score 0.3', 'score 0.8', 'verdict Fail', 'verdict Pass']);
    const table = await call<Envelope<{ table: Array<Record<string, unknown> & { row_id: string }> }>>(t.actor, 'GET',
      datasetPath(t.datasetId, 'get-dataset-table'));
    for (const kind of ['verdict', 'scoring'] as const) {
      for (const rowId of t.rows) {
        const tableRow = table.result.table.find(r => r.row_id === rowId);
        expect(tableRow?.[after[t.bindings[kind]].column_id!]).toMatchObject({ cell_value: cell(kind, rowId)!.value });
      }
    }
    await attach(`executed-${t.tag}`, { cells: judged, bindings: after });
    await page.context().close();
  }

  const a = await seed(scopes.ownerA, 'a');
  const b = await seed(scopes.ownerB, 'b');
  await attach('seeded-identities', [a, b].map(({ actor, ...rest }) => ({ ...rest,
    organizationId: actor.organizationId, workspaceId: actor.workspaceId })));
  for (const t of [a, b]) {
    await test.step(`Pass/fail on the Create page (${t.tag})`, () => verdictOnCreatePage(t));
    await test.step(`Scoring from the dataset's Create New Eval (${t.tag})`, () => scoringFromDataset(t));
    await test.step(`run both from the dataset (${t.tag})`, () => runBoth(t));
  }

  await test.step('the other organization cannot read, run or read the results', async () => {
    for (const [own, foreign] of [[a, b], [b, a]] as const) {
      const before = await cells(foreign.datasetId);
      const probes: [string, string, unknown?][] = [
        ['GET', templatePath(foreign.templates.verdict, 'detail')],
        ['GET', templatePath(foreign.templates.scoring, 'detail')],
        ['POST', datasetPath(foreign.datasetId, 'start_evals_process'),
          { user_eval_ids: [foreign.bindings.verdict, foreign.bindings.scoring] }],
        ['GET', datasetPath(foreign.datasetId, 'get-dataset-table')],
      ];
      for (const [method, path, body] of probes) {
        const response = await scopes.send(own.actor, method, path, body);
        expect(EXPECTED_FOREIGN, `OUT_NEG_TENANCY ${method} ${path} ${response.status}`).toContain(response.status);
        expect(JSON.stringify(response.body)).not.toContain(foreign.names.scoring);
      }
      expect(await cells(foreign.datasetId)).toEqual(before);
    }
  });
  await attach('tenant-refusals', scopes.receipts);
});
