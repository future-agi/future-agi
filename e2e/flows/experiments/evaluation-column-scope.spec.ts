import { randomUUID } from 'node:crypto';
import type { Browser, TestInfo } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import type { ScopeActor, ScopeActors } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { registerMockModel } from '../../lib/managed-mock';
import { POLL } from '../../lib/state-probe';
import { relabelColumns } from './evaluation-column-scope-fixture';

const UI_READY = 60_000;
// ManuallyCreateDatasetView: Column 1..n, one empty cell per row and column.
const DATASETS = '/model-hub/develops/create-dataset-manually/';
// EvalTemplateCreateV2RequestSerializer (model_hub/serializers/contracts.py).
const TEMPLATES = '/model-hub/eval-templates/create-v2/';
// PromptTemplateViewSet.create_draft, as in EXP-E2E-001.
const PROMPTS = '/model-hub/prompt-templates/create-draft/';
// ExperimentCreateV2Serializer. ExperimentsTableV2View.post pre-creates the
// per-run evaluation and reason columns in the snapshot before it answers.
const EXPERIMENTS = '/model-hub/experiments/v2/';
// DeleteEvalsView. Body as EvaluationDrawer/DeleteEval.jsx sends it for an
// experiment: {delete_column: true, experiment_id}.
const deletePath = (datasetId: string, evalId: string) =>
  `/model-hub/develops/${datasetId}/delete_user_eval/${evalId}/`;
// EditAndRunUserEvalView, UserEvalUpdateRequestSerializer. Body as
// Evaluation/EditEvaluation.jsx sends it: run false, name, config, experiment_id.
const editPath = (datasetId: string, evalId: string) =>
  `/model-hub/develops/${datasetId}/edit_and_run_user_eval/${evalId}/`;
// GetDatasetTableView; the dataset page reads the same endpoint. Outside an
// experiment snapshot it lists no experiment, experiment_evaluation or
// experiment_evaluation_tags column, so a stray evaluation column is checked in
// Postgres and its reason column in the API and on the page as well.
const tablePath = (datasetId: string) => `/model-hub/develops/${datasetId}/get-dataset-table/`;
const TABLE_PARAMS = { current_page_index: 0, page_size: 10 };
// model_hub/models/choices.py SourceChoices.
const EXPERIMENT_EVALUATION = 'experiment_evaluation';
const EVALUATION_REASON = 'evaluation_reason';
const SETTLED_MS = POLL.EVAL_RESULT.timeout + POLL.ASYNC_JOB.timeout;

type TableBody = { result: { table: ({ row_id: string } & Record<string, unknown>)[];
  column_config: { id: string; name: string }[] } };
type ColumnRow = { id: string; dataset_id: string; name: string; source: string; source_id: string | null;
  deleted: boolean };
type CellRow = { id: string; column_id: string; value: string | null; status: string; deleted: boolean };
type DatasetRow = { id: string; organization_id: string; workspace_id: string; column_order: string[];
  column_config: Record<string, unknown> | null };
type MetricRow = { id: string; name: string; dataset_id: string; config: { mapping: Record<string, string>;
  config?: Record<string, unknown> }; deleted: boolean };
interface PageView { loaded: string[]; shown: string[] }
interface Stray { actor: ScopeActor; datasetId: string; evaluationId: string; reasonId: string; resavedId: string;
  plainId: string;
  values: Record<string, string> }

test('EXP-E2E-007: re-saving and removing experiment evaluations changes only that experiment', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EXP-E2E-007', area: 'experiments',
    userGoal: 'Re-save one evaluation of an experiment and remove another, and leave every other dataset, in this and in another organization, exactly as it was',
    steps: [
      'seed two organizations; in the first, a one-row dataset, two evaluations, a saved prompt version and an experiment that uses both evaluations',
      'seed one dataset in each organization whose evaluation and reason columns carry the experiment\'s evaluation ids in their source id',
      'open both datasets in the browser and read the columns the page loads and shows',
      'try to re-save the second evaluation and to remove the first as the other organization',
      're-save the second evaluation of the experiment, then remove the first together with its columns',
      'reopen both datasets in the browser and read the columns again',
    ],
    backendChecks: [
      'the other organization\'s requests are refused and change no evaluation, column or cell',
      'the other organization\'s dataset keeps every column, cell value, column order and column config, in the API, in Postgres and on its page',
      'the unrelated dataset of the same organization keeps every column, cell value, column order and column config, in the API, in Postgres and on its page',
      'the experiment snapshot loses exactly the removed evaluation\'s columns from its table, column order and column config, and keeps the other evaluation',
      're-saving creates no reason column outside the experiment snapshot',
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe }, testInfo) => {
  // Provisioning + settle (150 s) + 4 page loads x UI_READY + API work + headroom.
  test.setTimeout(900_000);
  const { ownerA, ownerB } = scopeActors;
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const prefix = `e2e-exp7-${suffix}`;
  const attach = (name: string, body: unknown) =>
    testInfo.attach(name, { contentType: 'application/json', body: JSON.stringify(body, null, 2) });
  const pg = scopeProbe.pg.bind(scopeProbe);

  const ownerAccount = scopeActors.owners.find(o => o.organizationId === ownerA.organizationId);
  if (!ownerAccount) throw new Error('owner A provisioning account missing');
  const mock = await registerMockModel(ownerAccount, scopeProbe);

  const createDataset = async (actor: ScopeActor, name: string, columns: number) => {
    const created = await actor.api.post<{ result: { dataset_id: string } }>(DATASETS,
      { dataset_name: name, number_of_rows: 1, number_of_columns: columns });
    const datasetId = created.result.dataset_id;
    expect(await pg<{ organization_id: string; workspace_id: string }>(
      'SELECT organization_id, workspace_id FROM model_hub_dataset WHERE id=$1 AND NOT deleted', [datasetId]))
      .toEqual([{ organization_id: actor.organizationId, workspace_id: actor.workspaceId }]);
    const ids = await pg<{ id: string; name: string }>(
      'SELECT id, name FROM model_hub_column WHERE dataset_id=$1 AND NOT deleted ORDER BY name', [datasetId]);
    expect(ids.map(c => c.name)).toEqual(Array.from({ length: columns }, (_, i) => `Column ${i + 1}`));
    return { datasetId, columnIds: ids.map(c => c.id) };
  };
  const datasetState = async (datasetId: string) => ({
    dataset: (await pg<DatasetRow>(
      'SELECT id, organization_id, workspace_id, column_order, column_config FROM model_hub_dataset WHERE id=$1',
      [datasetId]))[0],
    columns: await pg<ColumnRow>(
      'SELECT id, dataset_id, name, source, source_id, deleted FROM model_hub_column WHERE dataset_id=$1 ORDER BY id',
      [datasetId]),
    cells: await pg<CellRow>(
      'SELECT id, column_id, value, status, deleted FROM model_hub_cell WHERE dataset_id=$1 ORDER BY id', [datasetId]),
  });
  const tableOf = (actor: ScopeActor, datasetId: string) => actor.api.get<TableBody>(tablePath(datasetId), TABLE_PARAMS);
  const tableView = (body: TableBody) => ({
    columns: body.result.column_config.map(c => c.id),
    values: Object.fromEntries(body.result.column_config.map(c => [c.id,
      body.result.table.map(row => (row[c.id] as { cell_value: string | null } | undefined)?.cell_value ?? null)])),
  });

  // 1. Seed organization A: dataset, two evaluations, a saved prompt version, the experiment.
  const source = await createDataset(ownerA, `${prefix}-source`, 1);
  const templates: string[] = [];
  for (const key of ['a', 'b']) {
    const created = await ownerA.api.post<{ result: { id: string } }>(TEMPLATES, {
      name: `${prefix}-${key}`, eval_type: 'llm', model: mock.model, output_type: 'pass_fail',
      instructions: 'Answer Passed when {{output}} is not empty.', pass_threshold: 0.5,
      check_internet: false, error_localizer_enabled: false,
    });
    templates.push(created.result.id);
  }
  const prompt = await ownerA.api.post<{ result: { id: string } }>(PROMPTS, {
    name: `${prefix}-prompt`, is_draft: false, variable_names: {}, prompt_config: [{
      messages: [{ role: 'user', content: [{ type: 'text', text: `${prefix} input` }] }],
      configuration: { model: mock.model, model_detail: { value: mock.model }, temperature: 0, max_tokens: 16 },
    }],
  });
  const [version] = await pg<{ id: string }>(
    `SELECT v.id FROM model_hub_promptversion v JOIN model_hub_prompttemplate p ON p.id=v.original_template_id
     WHERE p.id=$1 AND p.organization_id=$2 AND v.template_version='v1' AND NOT v.deleted`,
    [prompt.result.id, ownerA.organizationId]);
  expect(version).toBeDefined();
  const metricNames = [`exp7-a-${suffix}`, `exp7-b-${suffix}`];
  const experimentName = `${prefix}-experiment`;
  const createdExperiment = await scopeActors.send(ownerA, 'POST', EXPERIMENTS, {
    name: experimentName, dataset_id: source.datasetId, experiment_type: 'llm',
    prompt_config: [{ name: `${prefix}-run`, prompt_id: prompt.result.id, prompt_version: version.id,
      model: mock.model, model_params: { temperature: 0, max_tokens: 16 } }],
    user_eval_metrics: templates.map((templateId, i) => ({ template_id: templateId, name: metricNames[i],
      model: mock.model, config: { mapping: { output: source.columnIds[0] } } })),
  });
  expect(createdExperiment.status).toBe(200);
  const [experiment] = await pg<{ id: string; snapshot_dataset_id: string }>(
    `SELECT e.id, e.snapshot_dataset_id FROM model_hub_experimentstable e JOIN model_hub_dataset d ON d.id=e.dataset_id
     WHERE e.name=$1 AND e.dataset_id=$2 AND d.organization_id=$3 AND NOT e.deleted`,
    [experimentName, source.datasetId, ownerA.organizationId]);
  expect(experiment?.snapshot_dataset_id).toBeTruthy();
  const snapshotId = experiment.snapshot_dataset_id;
  const edts = (await pg<{ id: string }>(
    'SELECT id FROM model_hub_experimentdatasettable WHERE experiment_id=$1 AND NOT deleted', [experiment.id])).map(e => e.id);
  expect(edts).toHaveLength(1);
  const metrics = await pg<MetricRow>(
    'SELECT id, name, dataset_id, config, deleted FROM model_hub_userevalmetric WHERE source_id=$1 ORDER BY name',
    [experiment.id]);
  expect(metrics.map(m => [m.name, m.dataset_id, m.deleted])).toEqual(metricNames.map(n => [n, snapshotId, false]));
  const [target, kept] = metrics;

  // The experiment run is asynchronous; let it settle so it cannot write while the flow reads.
  const settleDeadline = Date.now() + SETTLED_MS;
  let status = '';
  while (Date.now() < settleDeadline) {
    [{ status }] = await pg<{ status: string }>('SELECT status FROM model_hub_experimentstable WHERE id=$1', [experiment.id]);
    if (!['NotStarted', 'Queued', 'Running'].includes(status)) break;
    await new Promise(resolve => setTimeout(resolve, POLL.EVAL_RESULT.intervals[1]));
  }
  await attach('experiment-run-status', { status });
  expect(['NotStarted', 'Queued', 'Running'], 'the experiment run settled').not.toContain(status);

  const suffixOf = (metricId: string) => `-sourceid-${metricId}`;
  const evaluationColumns = async (metricId: string) => (await pg<ColumnRow>(
    `SELECT id, dataset_id, name, source, source_id, deleted FROM model_hub_column
     WHERE dataset_id=$1 AND source IN ($2, $3) AND source_id LIKE $4 AND NOT deleted ORDER BY id`,
    [snapshotId, EXPERIMENT_EVALUATION, EVALUATION_REASON, `%${suffixOf(metricId)}`]));
  const targetColumns = await evaluationColumns(target.id);
  const keptColumns = await evaluationColumns(kept.id);
  for (const columns of [targetColumns, keptColumns]) {
    // The run's evaluation column (source id "<run>-<output column>-sourceid-<evaluation>")
    // and its reason column ("<evaluation column>-sourceid-<evaluation>").
    const perRun = columns.filter(c => c.source === EXPERIMENT_EVALUATION && c.source_id!.startsWith(`${edts[0]}-`));
    expect(perRun).toHaveLength(1);
    expect(columns.filter(c => c.source === EVALUATION_REASON && c.source_id!.startsWith(`${perRun[0].id}-`)))
      .toHaveLength(1);
  }
  await attach('seeded-experiment', { experiment, edts, metrics, targetColumns, keptColumns,
    sourceDatasetId: source.datasetId, templates, promptId: prompt.result.id, versionId: version.id,
    mockModel: mock.id, organizationId: ownerA.organizationId, workspaceId: ownerA.workspaceId });

  // 2. Columns elsewhere that carry the evaluations' ids: the removed one's evaluation and
  // reason columns, the re-saved one's evaluation column. Organization B's dataset first,
  // then A's unrelated dataset, so A's are the newest columns that match the removed one.
  const strays: Stray[] = [];
  for (const [actor, label] of [[ownerB, 'other-org'], [ownerA, 'same-org']] as const) {
    const created = await createDataset(actor, `${prefix}-${label}`, 4);
    const [evaluationId, reasonId, resavedId, plainId] = created.columnIds;
    const values = { [evaluationId]: `${prefix}-${label}-score`, [reasonId]: `${prefix}-${label}-reason`,
      [resavedId]: `${prefix}-${label}-resaved` };
    await relabelColumns([
      { organizationId: actor.organizationId, datasetId: created.datasetId, columnId: evaluationId,
        name: `${label} evaluation`, source: EXPERIMENT_EVALUATION,
        sourceId: `${randomUUID()}-${randomUUID()}${suffixOf(target.id)}`, cellValue: values[evaluationId] },
      { organizationId: actor.organizationId, datasetId: created.datasetId, columnId: reasonId,
        name: `${label} reason`, source: EVALUATION_REASON,
        sourceId: `${randomUUID()}${suffixOf(target.id)}`, cellValue: values[reasonId] },
      { organizationId: actor.organizationId, datasetId: created.datasetId, columnId: resavedId,
        name: `${label} re-saved evaluation`, source: EXPERIMENT_EVALUATION,
        sourceId: `${randomUUID()}-${randomUUID()}${suffixOf(kept.id)}`, cellValue: values[resavedId] },
    ]);
    strays.push({ actor, datasetId: created.datasetId, evaluationId, reasonId, resavedId, plainId, values });
  }
  const [otherOrg, sameOrg] = strays;
  const before = new Map<string, Awaited<ReturnType<typeof datasetState>>>();
  const beforeTables = new Map<string, ReturnType<typeof tableView>>();
  for (const stray of strays) {
    before.set(stray.datasetId, await datasetState(stray.datasetId));
    const view = tableView(await tableOf(stray.actor, stray.datasetId));
    expect([...view.columns].sort()).toEqual([stray.reasonId, stray.plainId].sort());
    expect(view.values[stray.plainId]).toHaveLength(1);
    beforeTables.set(stray.datasetId, view);
  }
  const snapshotBefore = await datasetState(snapshotId);
  const snapshotViewBefore = tableView(await tableOf(ownerA, snapshotId));
  await attach('seeded-strays', { strays: strays.map(({ actor, ...s }) => ({ ...s, organizationId: actor.organizationId,
    workspaceId: actor.workspaceId })), before: Object.fromEntries(before), beforeTables: Object.fromEntries(beforeTables),
    snapshotBefore, snapshotViewBefore });

  // 3. What each organization's dataset page loads and shows.
  const pageColumns = async (actor: ScopeActor, stray: Stray, label: string) =>
    readPage(browser, scopeActors, actor, stray, label, testInfo);
  const pageBefore = new Map<string, PageView>();
  for (const [stray, label] of [[otherOrg, 'other-org-before'], [sameOrg, 'same-org-before']] as const) {
    const view = await test.step(`UI: ${label}`, () => pageColumns(stray.actor, stray, label), { timeout: UI_READY * 3 });
    expect([...view.loaded].sort()).toEqual([...beforeTables.get(stray.datasetId)!.columns].sort());
    pageBefore.set(stray.datasetId, view);
  }

  // 4. Organization B cannot re-save or remove organization A's evaluation.
  const editBody = { run: false, name: kept.name, experiment_id: experiment.id,
    config: { mapping: kept.config.mapping, config: kept.config.config ?? {} } };
  await test.step('API: the other organization is refused', async () => {
    const refusedEdit = await scopeActors.send(ownerB, 'POST', editPath(snapshotId, kept.id), editBody);
    const refusedDelete = await scopeActors.send(ownerB, 'DELETE', deletePath(snapshotId, target.id),
      { delete_column: true, experiment_id: experiment.id });
    await attach('other-org-refusals', { refusedEdit, refusedDelete });
    expect([403, 404]).toContain(refusedEdit.status);
    expect([403, 404]).toContain(refusedDelete.status);
    expect(await pg('SELECT id, deleted FROM model_hub_userevalmetric WHERE id IN ($1, $2) ORDER BY name',
      [target.id, kept.id])).toEqual([{ id: target.id, deleted: false }, { id: kept.id, deleted: false }]);
    expect(await evaluationColumns(target.id)).toEqual(targetColumns);
    expect(await evaluationColumns(kept.id)).toEqual(keptColumns);
    for (const stray of strays) expect(await datasetState(stray.datasetId)).toEqual(before.get(stray.datasetId));
  });

  // 5. Organization A re-saves the second evaluation, then removes the first.
  await test.step('API: re-save one evaluation and remove the other', async () => {
    const edited = await scopeActors.send(ownerA, 'POST', editPath(snapshotId, kept.id), editBody);
    await attach('edit-response', edited);
    expect(edited.status).toBe(200);
    const removed = await scopeActors.send(ownerA, 'DELETE', deletePath(snapshotId, target.id),
      { delete_column: true, experiment_id: experiment.id });
    await attach('delete-response', removed);
    expect(removed.status).toBe(200);
    expect(await pg('SELECT id, deleted FROM model_hub_userevalmetric WHERE id IN ($1, $2) ORDER BY name',
      [target.id, kept.id])).toEqual([{ id: target.id, deleted: true }, { id: kept.id, deleted: false }]);
  });

  // 6. Read back. Soft checks so one run reports every check that fails.
  const after = new Map<string, Awaited<ReturnType<typeof datasetState>>>();
  for (const [stray, label] of [[otherOrg, 'other-org'], [sameOrg, 'same-org']] as const) {
    const state = await datasetState(stray.datasetId);
    after.set(stray.datasetId, state);
    const view = tableView(await tableOf(stray.actor, stray.datasetId));
    await attach(`after-${label}`, { state, view });
    expect.soft(view, `${label}: API table`).toEqual(beforeTables.get(stray.datasetId));
    expect.soft(state.columns, `${label}: columns`).toEqual(before.get(stray.datasetId)!.columns);
    expect.soft(state.cells, `${label}: cells`).toEqual(before.get(stray.datasetId)!.cells);
    expect.soft(state.dataset, `${label}: column order and config`).toEqual(before.get(stray.datasetId)!.dataset);
  }
  const removedIds = targetColumns.map(c => c.id);
  const snapshot = await datasetState(snapshotId);
  const snapshotView = tableView(await tableOf(ownerA, snapshotId));
  await attach('after-snapshot', { snapshot, snapshotView, removedIds });
  expect.soft(snapshot.columns.filter(c => removedIds.includes(c.id)).map(c => c.deleted), 'snapshot: removed columns')
    .toEqual(removedIds.map(() => true));
  expect.soft(snapshot.cells.filter(c => removedIds.includes(c.column_id) && !c.deleted), 'snapshot: removed cells')
    .toEqual([]);
  expect.soft(snapshot.dataset.column_order.filter(id => removedIds.includes(id)), 'snapshot: column order pruned')
    .toEqual([]);
  expect.soft(Object.keys(snapshot.dataset.column_config ?? {}).filter(id => removedIds.includes(id)),
    'snapshot: column config pruned').toEqual([]);
  expect.soft(snapshotView.columns.filter(id => removedIds.includes(id)), 'snapshot: API table pruned').toEqual([]);
  for (const column of keptColumns) {
    expect.soft(snapshot.columns.find(c => c.id === column.id)?.deleted, 'snapshot: kept evaluation column').toBe(false);
    if (snapshotBefore.dataset.column_order.includes(column.id))
      expect.soft(snapshot.dataset.column_order, 'snapshot: kept evaluation in column order').toContain(column.id);
    if (snapshotViewBefore.columns.includes(column.id))
      expect.soft(snapshotView.columns, 'snapshot: kept evaluation in API table').toContain(column.id);
  }
  const reasonsOutside = await pg<{ id: string; dataset_id: string }>(
    `SELECT id, dataset_id FROM model_hub_column WHERE source=$1 AND source_id LIKE $2 AND dataset_id<>$3 ORDER BY id`,
    [EVALUATION_REASON, `%${suffixOf(kept.id)}`, snapshotId]);
  expect.soft(reasonsOutside, 're-save: reason columns outside the snapshot').toEqual([]);

  for (const [stray, label] of [[otherOrg, 'other-org-after'], [sameOrg, 'same-org-after']] as const) {
    const view = await test.step(`UI: ${label}`, () => pageColumns(stray.actor, stray, label), { timeout: UI_READY * 3 });
    expect.soft([...view.loaded].sort(), `${label}: page columns`).toEqual([...pageBefore.get(stray.datasetId)!.loaded].sort());
    expect.soft(view.shown, `${label}: page headers`).toEqual(pageBefore.get(stray.datasetId)!.shown);
  }
});

// Opens the dataset page as the actor. Returns the column ids of the table the
// page itself loads and the seeded column headers the grid shows.
async function readPage(browser: Browser, scopes: ScopeActors, actor: ScopeActor, stray: Stray, label: string,
  testInfo: TestInfo): Promise<PageView> {
  const context = await scopes.openContext(browser, actor);
  // Safety veto only: the page talks to the local stack and nothing else.
  await context.route('**/*', route => new URL(route.request().url()).hostname === 'localhost'
    ? route.continue() : route.abort('blockedbyclient'));
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  try {
    const loaded = page.waitForResponse(async response => {
      if (new URL(response.url()).pathname !== tablePath(stray.datasetId) || response.status() !== 200) return false;
      const body = await response.json().catch(() => null) as TableBody | null;
      return Array.isArray(body?.result?.column_config) && body!.result.column_config.length > 0;
    }, { timeout: UI_READY });
    await page.goto(`/dashboard/develop/${stray.datasetId}`, { waitUntil: 'domcontentloaded' });
    const body = await (await loaded).json() as TableBody;
    const ids = body.result.column_config.map(c => c.id);
    // AG Grid header cells carry the column id (DATA-E2E-003 addresses cells the same way).
    // Every seeded column the page loads is shown; the plain column always is.
    expect(ids).toContain(stray.plainId);
    for (const id of [stray.reasonId, stray.plainId].filter(id => ids.includes(id))) {
      await expect(page.locator(`.ag-header-cell[col-id="${id}"]`)).toBeVisible({ timeout: UI_READY });
    }
    const seeded = [stray.evaluationId, stray.reasonId, stray.resavedId, stray.plainId];
    const shown = (await page.locator('.ag-header-cell[col-id]').evaluateAll(cells =>
      cells.map(cell => cell.getAttribute('col-id') ?? ''))).filter(id => seeded.includes(id)).sort();
    await testInfo.attach(`page-${label}`, { contentType: 'application/json',
      body: JSON.stringify({ url: page.url(), loaded: ids, shown }) });
    return { loaded: ids, shown };
  } catch (error) {
    await testInfo.attach(`page-${label}-at-failure`, { contentType: 'text/plain',
      body: `${page.url()}\n${await page.locator('body').innerText().catch(() => '')}` });
    await testInfo.attach(`page-${label}-at-failure-image`, { contentType: 'image/png', body: await page.screenshot() });
    throw error;
  } finally {
    await context.close();
  }
}
