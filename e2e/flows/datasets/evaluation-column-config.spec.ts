import { appendFileSync } from 'node:fs';
import type { Browser, TestInfo } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import type { ScopeActor, ScopeActors } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { POLL } from '../../lib/state-probe';
import { relabelAsExperimentEvaluation } from './evaluation-column-config-fixture';

const UI_READY = 60_000;
// ManuallyCreateDatasetView: Column 1..n, one empty cell per row and column.
const DATASETS = '/model-hub/develops/create-dataset-manually/';
// DeleteRowView: {row_ids}. The experiment snapshot then has no row to run.
const deleteRowsPath = (datasetId: string) => `/model-hub/develops/${datasetId}/delete_row/`;
// EvalTemplateCreateV2RequestSerializer (model_hub/serializers/contracts.py). A code
// evaluation is stored as written and calls no model.
const TEMPLATES = '/model-hub/eval-templates/create-v2/';
// PromptTemplateViewSet.create_draft, as in EXP-E2E-007.
const PROMPTS = '/model-hub/prompt-templates/create-draft/';
// ExperimentCreateV2Serializer. ExperimentsTableV2View.post pre-creates the run's
// evaluation column "<table>-<output column>-sourceid-<evaluation>" before it answers.
const EXPERIMENTS = '/model-hub/experiments/v2/';
// ColumnConfigView (model_hub/views/develop_dataset.py). No page of the app requests
// it: the generated client (modelHubColumnConfigRead) has no caller, the dataset
// grid's column editors read /model-hub/columns/<id>/operation-config/, and the
// experiment pages are not shown. The signed-in page sends it the way the app's
// own client does: its stored access token and organization and workspace ids.
const configPath = (columnId: string) => `/model-hub/column-config/${columnId}/`;
// GetDatasetTableView; the dataset page reads it on load.
const tablePath = (datasetId: string) => `/model-hub/develops/${datasetId}/get-dataset-table/`;
// model_hub/models/choices.py SourceChoices.
const EXPERIMENT = 'experiment';
const EXPERIMENT_EVALUATION = 'experiment_evaluation';
// An optional per-event record of refused browser requests (host and port only).
const EGRESS_EVENTS = process.env.E2E_EGRESS_EVENTS;
// The run has no row to process; let it settle so it cannot write while the flow reads.
const SETTLED_MS = POLL.EVAL_RESULT.timeout + POLL.ASYNC_JOB.timeout;

type ColumnRow = { id: string; dataset_id: string; name: string; source: string; source_id: string | null;
  deleted: boolean };
type ConfigBody = { status?: boolean; result?: Record<string, unknown> };
interface ConfigRead { status: number; observedStatus: number; body: ConfigBody | null; text: string }

test('DATA-E2E-099: an experiment evaluation column reads back its own evaluation config, and nothing for a copy of its id', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-099', area: 'datasets',
    userGoal: 'Read the configuration of an experiment evaluation column from the signed-in app, and get nothing for a column elsewhere that only carries its ids',
    steps: [
      'seed two organizations; in the first, a dataset with one column and no rows, a code evaluation, a saved prompt version and an experiment that uses the evaluation',
      'seed one dataset in each organization with a column that carries the experiment evaluation column\'s source id',
      'open the first organization\'s dataset page and request the experiment evaluation column\'s config from the signed-in page',
      'open the other organization\'s dataset page and request the same config and its own column\'s config',
      'request the config of the first organization\'s unrelated column that carries the id',
    ],
    backendChecks: [
      'the experiment evaluation column is stored with the source id "<table>-<output column>-sourceid-<evaluation>"',
      'its config answers 200 with the evaluation template, template config, description, mapping config and status and the experiment table name and stored prompt config, equal to the stored rows',
      'the other organization gets 404 for the column and for its own column carrying the id, with no field of the first organization\'s evaluation or table',
      'the first organization\'s unrelated column carrying the id gets 404',
      'no request changes the evaluation, the experiment table or any of the columns',
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe }, testInfo) => {
  // Provisioning + settle (150 s) + 4 page reads x 3 UI_READY + API work + headroom.
  test.setTimeout(1_080_000);
  const { ownerA, ownerB } = scopeActors;
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const prefix = `e2e-data99-${suffix}`;
  const attach = (name: string, body: unknown) =>
    testInfo.attach(name, { contentType: 'application/json', body: JSON.stringify(body, null, 2) });
  const pg = scopeProbe.pg.bind(scopeProbe);

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
    const rows = await pg<{ id: string }>('SELECT id FROM model_hub_row WHERE dataset_id=$1 AND NOT deleted', [datasetId]);
    expect(rows).toHaveLength(1);
    return { datasetId, columnIds: ids.map(c => c.id), rowId: rows[0].id };
  };
  const columnsOf = (datasetId: string) => pg<ColumnRow>(
    'SELECT id, dataset_id, name, source, source_id, deleted FROM model_hub_column WHERE dataset_id=$1 ORDER BY id',
    [datasetId]);

  // 1. Organization A: a one-column dataset whose only row is removed, a code
  // evaluation, a saved prompt version and the experiment.
  const source = await createDataset(ownerA, `${prefix}-source`, 1);
  const removed = await scopeActors.send(ownerA, 'DELETE', deleteRowsPath(source.datasetId), { row_ids: [source.rowId] });
  expect(removed.status).toBe(200);
  expect(await pg('SELECT id FROM model_hub_row WHERE dataset_id=$1 AND NOT deleted', [source.datasetId])).toEqual([]);
  const template = await ownerA.api.post<{ result: { id: string } }>(TEMPLATES, {
    name: `${prefix}-code`, eval_type: 'code', code_language: 'python', output_type: 'pass_fail',
    code: 'def evaluate(output, **kwargs):\n    return bool(output)\n', pass_threshold: 0.5,
    check_internet: false, error_localizer_enabled: false,
  });
  const prompt = await ownerA.api.post<{ result: { id: string } }>(PROMPTS, {
    name: `${prefix}-prompt`, is_draft: false, variable_names: {}, prompt_config: [{
      messages: [{ role: 'user', content: [{ type: 'text', text: `${prefix} input` }] }],
      configuration: { model: 'gpt-4o-mini', model_detail: { value: 'gpt-4o-mini' }, temperature: 0, max_tokens: 16 },
    }],
  });
  const [version] = await pg<{ id: string }>(
    `SELECT v.id FROM model_hub_promptversion v JOIN model_hub_prompttemplate p ON p.id=v.original_template_id
     WHERE p.id=$1 AND p.organization_id=$2 AND v.template_version='v1' AND NOT v.deleted`,
    [prompt.result.id, ownerA.organizationId]);
  expect(version).toBeDefined();
  const metricName = `data99-${suffix}`;
  const experimentName = `${prefix}-experiment`;
  const createdExperiment = await scopeActors.send(ownerA, 'POST', EXPERIMENTS, {
    name: experimentName, dataset_id: source.datasetId, experiment_type: 'llm',
    prompt_config: [{ name: `${prefix}-run`, prompt_id: prompt.result.id, prompt_version: version.id,
      model: 'gpt-4o-mini', model_params: { temperature: 0, max_tokens: 16 } }],
    user_eval_metrics: [{ template_id: template.result.id, name: metricName,
      config: { mapping: { output: source.columnIds[0] } } }],
  });
  expect(createdExperiment.status).toBe(200);
  const [experiment] = await pg<{ id: string; snapshot_dataset_id: string }>(
    `SELECT e.id, e.snapshot_dataset_id FROM model_hub_experimentstable e JOIN model_hub_dataset d ON d.id=e.dataset_id
     WHERE e.name=$1 AND e.dataset_id=$2 AND d.organization_id=$3 AND NOT e.deleted`,
    [experimentName, source.datasetId, ownerA.organizationId]);
  expect(experiment?.snapshot_dataset_id).toBeTruthy();
  const snapshotId = experiment.snapshot_dataset_id;
  const [table] = await pg<{ id: string }>(
    'SELECT id FROM model_hub_experimentdatasettable WHERE experiment_id=$1 AND NOT deleted', [experiment.id]);
  const [metric] = await pg<{ id: string }>(
    'SELECT id FROM model_hub_userevalmetric WHERE source_id=$1 AND name=$2 AND dataset_id=$3 AND NOT deleted',
    [experiment.id, metricName, snapshotId]);
  const [output] = await pg<{ id: string }>(
    'SELECT id FROM model_hub_column WHERE dataset_id=$1 AND source=$2 AND source_id=$3 AND NOT deleted',
    [snapshotId, EXPERIMENT, table.id]);
  const evaluations = await pg<ColumnRow>(
    `SELECT id, dataset_id, name, source, source_id, deleted FROM model_hub_column
     WHERE dataset_id=$1 AND source=$2 AND NOT deleted`, [snapshotId, EXPERIMENT_EVALUATION]);
  expect(evaluations).toHaveLength(1);
  const [evaluation] = evaluations;
  const evaluationSourceId = `${table.id}-${output.id}-sourceid-${metric.id}`;
  expect(evaluation.source_id, 'the stored output-qualified evaluation source id').toBe(evaluationSourceId);
  const settleDeadline = Date.now() + SETTLED_MS;
  let status = '';
  while (Date.now() < settleDeadline) {
    [{ status }] = await pg<{ status: string }>('SELECT status FROM model_hub_experimentstable WHERE id=$1', [experiment.id]);
    if (!['NotStarted', 'Queued', 'Running'].includes(status)) break;
    await new Promise(resolve => setTimeout(resolve, POLL.ASYNC_JOB.intervals[0]));
  }
  expect(['NotStarted', 'Queued', 'Running'], 'the experiment run settled').not.toContain(status);
  await attach('seeded-experiment', { experiment, status, tableId: table.id, metricId: metric.id, outputId: output.id,
    evaluation, sourceDatasetId: source.datasetId, templateId: template.result.id, promptId: prompt.result.id,
    versionId: version.id, organizationId: ownerA.organizationId, workspaceId: ownerA.workspaceId });

  // 2. A column carrying the evaluation's source id in organization B's dataset and in
  // an unrelated dataset of organization A.
  const strays: { actor: ScopeActor; datasetId: string; columnId: string; plainId: string; label: string }[] = [];
  for (const [actor, label] of [[ownerB, 'other-org'], [ownerA, 'same-org']] as const) {
    const created = await createDataset(actor, `${prefix}-${label}`, 2);
    const [columnId, plainId] = created.columnIds;
    await relabelAsExperimentEvaluation({ organizationId: actor.organizationId, datasetId: created.datasetId,
      columnId, name: `${label} evaluation copy`, sourceId: evaluationSourceId });
    strays.push({ actor, datasetId: created.datasetId, columnId, plainId, label });
  }
  const [otherOrg, sameOrg] = strays;

  // The stored rows the config is read from, and every row a read could change.
  const stored = async () => ({
    metric: await pg<Record<string, unknown>>(
      `SELECT m.id, m.name, m.config, m.status, m.template_id, m.dataset_id, m.organization_id, m.deleted,
              t.config AS template_config, t.description
       FROM model_hub_userevalmetric m JOIN model_hub_evaltemplate t ON t.id=m.template_id WHERE m.id=$1`,
      [metric.id]),
    table: await pg<Record<string, unknown>>(
      'SELECT id, name, prompt_config, experiment_id, deleted FROM model_hub_experimentdatasettable WHERE id=$1',
      [table.id]),
    evaluation: await pg<ColumnRow>(
      'SELECT id, dataset_id, name, source, source_id, deleted FROM model_hub_column WHERE id=$1', [evaluation.id]),
    otherOrg: await columnsOf(otherOrg.datasetId),
    sameOrg: await columnsOf(sameOrg.datasetId),
  });
  const before = await stored();
  await attach('seeded-strays', { strays: strays.map(({ actor, ...s }) => ({ ...s,
    organizationId: actor.organizationId, workspaceId: actor.workspaceId })), before });
  const [metricRow] = before.metric;
  const [tableRow] = before.table;
  const expected = {
    name: evaluation.name,
    template: metricRow.template_id,
    template_config: metricRow.template_config,
    description: metricRow.description,
    config: metricRow.config,
    status: metricRow.status,
    experiment_dataset: tableRow.name,
    experiment_dataset_config: tableRow.prompt_config,
  };
  const read = (actor: ScopeActor, pageDatasetId: string, columnId: string, label: string) =>
    test.step(`browser: ${label}`, () => readConfig(browser, scopeActors, actor, pageDatasetId, columnId, label, testInfo),
      { timeout: UI_READY * 3 });
  const foreignFields = [String(metricRow.name), String(tableRow.name), String(metricRow.template_id)];
  const leaks = (result: ConfigRead) => foreignFields.filter(value => result.text.includes(value));

  // 3. Organization A reads its experiment evaluation column's config.
  const own = await read(ownerA, source.datasetId, evaluation.id, 'own-evaluation');
  expect(own.status, 'the experiment evaluation column config renders').toBe(200);
  expect(own.observedStatus).toBe(200);
  expect(own.body?.status).toBe(true);
  expect(own.body?.result, 'the config equals the stored evaluation and experiment table').toEqual(expected);

  // 4. Organization B: the same column, then its own column carrying the id.
  const crossRead = await read(ownerB, otherOrg.datasetId, evaluation.id, 'other-org-reads-evaluation');
  expect(crossRead.status, 'the other organization cannot read the column').toBe(404);
  expect(leaks(crossRead)).toEqual([]);
  const otherCopy = await read(ownerB, otherOrg.datasetId, otherOrg.columnId, 'other-org-copy');
  expect(otherCopy.status, 'a column carrying the ids in another organization resolves nothing').toBe(404);
  expect(leaks(otherCopy)).toEqual([]);

  // 5. Organization A: its unrelated dataset's column carrying the id.
  const sameCopy = await read(ownerA, sameOrg.datasetId, sameOrg.columnId, 'same-org-copy');
  expect(sameCopy.status, 'a column carrying the ids in an unrelated dataset resolves nothing').toBe(404);
  expect(leaks(sameCopy)).toEqual([]);

  const after = await stored();
  await attach('after', after);
  expect(after, 'no read changed a stored row').toEqual(before);
});

// Opens the actor's dataset page, then requests the column config from that
// signed-in page. Returns the status the page got and the status the browser
// observed on the network.
async function readConfig(browser: Browser, scopes: ScopeActors, actor: ScopeActor, pageDatasetId: string,
  columnId: string, label: string, testInfo: TestInfo): Promise<ConfigRead> {
  const context = await scopes.openContext(browser, actor);
  const refused: { host: string; port: string }[] = [];
  const network: { method: string; host: string; path: string; status?: number }[] = [];
  const consoleErrors: string[] = [];
  // Safety veto: the page talks to the local stack and nothing else.
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.hostname === 'localhost') return route.continue();
    const event = { host: url.hostname, port: url.port || (url.protocol === 'https:' ? '443' : '80') };
    refused.push(event);
    if (EGRESS_EVENTS) {
      appendFileSync(EGRESS_EVENTS, `${JSON.stringify({ ts: new Date().toISOString(), layer: 'browser',
        kind: 'route-abort', ...event, label })}\n`);
    }
    return route.abort('blockedbyclient');
  });
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(message.text().slice(0, 300));
  });
  page.on('response', response => {
    const url = new URL(response.url());
    network.push({ method: response.request().method(), host: url.host, path: url.pathname, status: response.status() });
  });
  try {
    const loaded = page.waitForResponse(response => new URL(response.url()).pathname === tablePath(pageDatasetId)
      && response.request().method() === 'GET' && response.status() === 200, { timeout: UI_READY });
    await page.goto(`/dashboard/develop/${pageDatasetId}`, { waitUntil: 'domcontentloaded' });
    await loaded;
    const path = configPath(columnId);
    const observed = page.waitForResponse(response => new URL(response.url()).pathname === path
      && response.request().method() === 'GET', { timeout: UI_READY });
    const result = await page.evaluate(async url => {
      const headers: Record<string, string> = { Authorization: `Bearer ${localStorage.getItem('accessToken') ?? ''}` };
      const organizationId = sessionStorage.getItem('organizationId');
      const workspaceId = sessionStorage.getItem('workspaceId');
      if (organizationId) headers['X-Organization-Id'] = organizationId;
      if (workspaceId) headers['X-Workspace-Id'] = workspaceId;
      const response = await fetch(url, { headers });
      return { status: response.status, text: await response.text() };
    }, `${E2E.apiUrl}${path}`);
    const response = await observed;
    let body: ConfigBody | null = null;
    try { body = JSON.parse(result.text) as ConfigBody; } catch { body = null; }
    await testInfo.attach(`config-${label}`, { contentType: 'application/json', body: JSON.stringify({
      page: page.url(), path, status: result.status, observedStatus: response.status(),
      body: body ?? result.text.slice(0, 300) }, null, 2) });
    return { status: result.status, observedStatus: response.status(), body, text: result.text };
  } catch (error) {
    await testInfo.attach(`page-${label}-at-failure`, { contentType: 'text/plain',
      body: `${page.url()}\n${await page.locator('body').innerText().catch(() => '')}` });
    await testInfo.attach(`page-${label}-at-failure-image`, { contentType: 'image/png', body: await page.screenshot() });
    throw error;
  } finally {
    await testInfo.attach(`browser-network-${label}`, { contentType: 'application/json',
      body: JSON.stringify({ refused, requests: network }, null, 2) });
    await testInfo.attach(`browser-console-${label}`, { contentType: 'application/json',
      body: JSON.stringify(consoleErrors, null, 2) });
    await context.close();
  }
}
