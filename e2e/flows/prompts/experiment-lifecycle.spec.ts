import { test, expect } from '../../lib/scope-actors';
import { inspectManagedMock, registerMockModel, MOCK_MODEL } from '../../lib/managed-mock';
import { uploadFixture } from '../../lib/upload';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// model_hub/urls.py, ExperimentCreateV2Serializer and FeedbackSerializer.
const EXPERIMENTS = '/model-hub/experiments/v2/';
const RERUN = `${EXPERIMENTS}re-run/`;
const DELETE = `${EXPERIMENTS}delete/`;
// UploadFileModal.jsx and CreateDatasetFromLocalFileRequestSerializer.
const UPLOAD = '/model-hub/develops/create-dataset-from-local-file/';
const UI_READY = 60_000;
interface Detail { id: string; status: string; snapshot_dataset_id: string; prompt_configs: { id: string }[] }

test('PROMPT-E2E-006: inspect, give feedback, stop, rerun and delete an experiment', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'PROMPT-E2E-006', area: 'prompts',
    userGoal: 'Reach an experiment through the dataset list, save row feedback and manage its execution without touching another organization',
    steps: [
      'upload a synthetic dataset and seed a prompt experiment through the public APIs',
      'open the focused-grid palette and list-row New experiment doors, then Open experiments and the experiment row',
      'inspect an evaluation, save feedback, close and reopen it',
      'check foreign-organization refusal for each read and lifecycle write',
      'rerun from the experiment list, stop it, then rerun to completion',
      'select the experiment by its identity and confirm deletion',
    ],
    backendChecks: [
      'the experiment produces three exact loopback-stand-in outputs on its own snapshot',
      'the browser feedback POST persists the chosen metric, column, row, value and explanation and reads them back',
      'browser rerun and stop requests yield Queued or Running, Cancelled, then Completed with retained configuration identity',
      'the browser DELETE soft-deletes exactly the selected experiment and removes it from its list and detail',
      'foreign experiment, row, feedback, stop, rerun and delete requests are refused without changing owner state',
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe }, testInfo) => {
  // Six ASYNC_JOB + sixteen UI_READY + 120s setup headroom; all storage is PG.
  test.setTimeout(1_440_000);
  const receipt = inspectManagedMock();
  await testInfo.attach('loopback-LLM-STANDIN', { body: JSON.stringify(receipt), contentType: 'application/json' });
  const owner = scopeActors.ownerA;
  const foreign = scopeActors.ownerB;
  const model = await registerMockModel(scopeActors.owners[0], scopeProbe, async () => expect(inspectManagedMock()).toEqual(receipt));
  const prefix = `e2e-lifecycle-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  // PromptTemplateViewSet.create_draft and PromptHistoryExecutionViewSet.list.
  const prompt = await owner.api.post<{ result: { id: string } }>('/model-hub/prompt-templates/create-draft/', {
    name: `${prefix}-prompt`, is_draft: false,
    prompt_config: [{ messages: [{ role: 'user', content: prefix }], configuration: { model: MOCK_MODEL, temperature: 0, max_tokens: 128, tools: [] } }],
  });
  const versions = await owner.api.get<{ results: { id: string }[] }>('/model-hub/prompt-history-executions/', { template_id: prompt.result.id });
  expect(versions.results).toHaveLength(1);
  // EvalTemplateCreateV2Serializer: the managed model echoes this JSON-bearing input.
  const evaluator = await owner.api.post<{ result: { id: string } }>('/model-hub/eval-templates/create-v2/', {
    name: `${prefix}-judge`, eval_type: 'llm', model: MOCK_MODEL, output_type: 'deterministic',
    instructions: `Reply with exactly this JSON: {"result":"Pass","explanation":"${prefix}-verdict"}\nMapped input: {{output}}`,
    pass_threshold: 0.5, choice_scores: { Pass: 1, Fail: 0 }, check_internet: false,
    error_localizer_enabled: false, template_format: 'mustache',
  });
  const context = await scopeActors.openContext(browser, owner);
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (['localhost', '127.0.0.1'].includes(url.hostname) || ['data:', 'blob:'].includes(url.protocol)) await route.continue();
    else await route.abort('blockedbyclient');
  });
  try {
    await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
    await page.getByText('New dataset', { exact: true }).click();
    await page.getByText('Upload a file', { exact: true }).click();
    const upload = page.getByRole('dialog').filter({ has: page.getByText('Upload a file', { exact: true }) });
    await uploadFixture(upload.locator('input[type="file"]'), 'catalog-regions.csv');
    await upload.getByPlaceholder('Dataset name').fill(`${prefix}-data`);
    const uploaded = page.waitForResponse(r => new URL(r.url()).pathname === UPLOAD && r.request().method() === 'POST', { timeout: UI_READY });
    await upload.getByRole('button', { name: 'Import 3 rows', exact: true }).click();
    const uploadResponse = await uploaded;
    expect(uploadResponse.status()).toBe(200);
    const datasetId = (await uploadResponse.json()).result.dataset_id as string;
    await expect(page).toHaveURL(new RegExp(`/develop/${datasetId}([?]|$)`), { timeout: UI_READY });
    // DatasetHeaderBar registers this verb; the shortcut belongs only to a focused grid.
    const dataRows = page.locator('.ag-center-cols-container .ag-row');
    await expect(dataRows).toHaveCount(3, { timeout: UI_READY });
    // Press on a loaded row, not an initial server-side placeholder that can be replaced after focus.
    await dataRows.first().getByRole('gridcell').first().press('Control+k');
    await expect(page.getByRole('dialog')).toHaveCount(1, { timeout: UI_READY });
    await page.locator('[data-command="dataset.newExperiment"]').click();
    await expect(page.getByRole('textbox', { name: /^Experiment name/ })).toBeVisible({ timeout: UI_READY });
    await page.getByRole('button', { name: 'Close', exact: true }).click();
    await model.assertReady();
    const created = await owner.api.post<{ result: string }>(EXPERIMENTS, {
      name: `${prefix}-experiment`, dataset_id: datasetId, experiment_type: 'llm',
      prompt_config: [{ name: 'Prompt', prompt_id: prompt.result.id, prompt_version: versions.results[0].id, model: MOCK_MODEL, model_params: { temperature: 0, max_tokens: 128 } }],
      user_eval_metrics: [{ name: 'output-check', template_id: evaluator.result.id, model: MOCK_MODEL, config: { mapping: { output: 'output' } } }],
    });
    const experimentId = /experiment_id=([0-9a-f-]{36})/.exec(created.result)?.[1];
    expect(experimentId).toBeTruthy();
    const path = `${EXPERIMENTS}${experimentId}/`;
    const detail = async () => (await owner.api.get<{ result: Detail }>(path)).result;
    await testInfo.attach('seeded-identities', { body: JSON.stringify({ ownerOrg: owner.organizationId, foreignOrg: foreign.organizationId, datasetId, experimentId, promptId: prompt.result.id, evaluatorId: evaluator.result.id }), contentType: 'application/json' });
    await expect.poll(async () => (await detail()).status, POLL.ASYNC_JOB).toBe('Completed');
    const original = await detail();
    const outputCells = () => scopeProbe.pg<{ id: string; value: string }>(
      `SELECT c.id,c.value FROM model_hub_cell c JOIN model_hub_column col ON col.id=c.column_id
       WHERE c.dataset_id=$1 AND col.source='experiment' AND NOT col.deleted AND NOT c.deleted ORDER BY c.id`, [original.snapshot_dataset_id]);
    const outputs = await outputCells();
    expect(outputs.map(cell => cell.value)).toEqual(Array(3).fill(`echo: ${JSON.stringify([{ type: 'text', text: prefix }])}`));
    await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: `${prefix}-data actions`, exact: true }).click();
    await page.getByRole('menuitem', { name: /^New experiment/ }).click();
    await expect(page.getByRole('textbox', { name: /^Experiment name/ })).toBeVisible({ timeout: UI_READY });
    await page.getByRole('button', { name: 'Close', exact: true }).click();
    await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: `${prefix}-data actions`, exact: true }).click();
    await page.getByRole('menuitem', { name: 'Open experiments', exact: true }).click();
    const list = page.getByTestId('classic-experiments-list');
    const experimentRow = list.getByRole('row').filter({ hasText: `${prefix}-experiment` });
    await expect(experimentRow).toHaveCount(1, { timeout: UI_READY });
    await experimentRow.getByText(`${prefix}-experiment`, { exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/experiment/${experimentId}/data`), { timeout: UI_READY });
    const rows = await scopeProbe.pg<{ id: string }>('SELECT id FROM model_hub_row WHERE dataset_id=$1 AND NOT deleted ORDER BY id', [original.snapshot_dataset_id]);
    expect(rows).toHaveLength(3);
    const rowId = rows[0].id;
    const openFeedback = async () => {
      await page.locator(`.ag-row[row-id="${rowId}"] .ag-cell`).filter({ hasText: `echo: ${JSON.stringify([{ type: 'text', text: prefix }])}` }).click();
      await page.getByRole('button', { name: 'Show Descriptions', exact: true }).click();
      await page.getByRole('button', { name: 'View Detail', exact: true }).click();
      await page.getByRole('button', { name: 'add-feedback', exact: true }).click();
    };
    await openFeedback();
    const feedback = page.getByRole('dialog', { name: 'Experiment feedback', exact: true });
    await feedback.getByLabel('Feedback value', { exact: false }).fill('Pass');
    await feedback.getByLabel('Explanation', { exact: false }).fill(`${prefix}-feedback`);
    const feedbackPath = `${path}feedback/`;
    const feedbackSaved = page.waitForResponse(r => new URL(r.url()).pathname === feedbackPath && r.request().method() === 'POST', { timeout: UI_READY });
    await feedback.getByRole('button', { name: 'Save feedback', exact: true }).click();
    const feedbackResponse = await feedbackSaved;
    expect(feedbackResponse.status()).toBe(200);
    const feedbackId = (await feedbackResponse.json()).result.id as string;
    const body = feedbackResponse.request().postDataJSON();
    expect(body).toMatchObject({ source: 'experiment', row_id: rowId, value: 'Pass', explanation: `${prefix}-feedback` });
    const persisted = await scopeProbe.pg<{ id: string; source_id: string; row_id: string; user_eval_metric_id: string; value: string; explanation: string }>(
      'SELECT id,source_id,row_id,user_eval_metric_id,value,explanation FROM model_hub_feedback WHERE id=$1 AND organization_id=$2 AND NOT deleted', [feedbackId, owner.organizationId]);
    expect(persisted).toEqual([{ id: feedbackId, source_id: body.source_id, row_id: rowId, user_eval_metric_id: body.user_eval_metric, value: 'Pass', explanation: `${prefix}-feedback` }]);
    await expect(feedback.getByRole('paragraph').filter({ hasText: new RegExp(`^${prefix}-feedback$`) })).toHaveText(`${prefix}-feedback`, { timeout: UI_READY });
    await feedback.getByRole('button', { name: 'Close', exact: true }).click();
    await page.reload({ waitUntil: 'domcontentloaded' });
    await openFeedback();
    await expect(feedback.getByRole('paragraph').filter({ hasText: new RegExp(`^${prefix}-feedback$`) })).toHaveText(`${prefix}-feedback`, { timeout: UI_READY });
    const feedbackDetails = `${feedbackPath}get-feedback-details/`;
    const params = { user_eval_metric_id: body.user_eval_metric, row_id: rowId };
    expect((await scopeActors.send(foreign, 'GET', path)).status).toBe(404);
    expect((await scopeActors.send(foreign, 'GET', `${path}rows/${rowId}/`)).status).toBe(404);
    expect((await scopeActors.send(foreign, 'POST', feedbackPath, body)).status).toBe(400);
    expect((await scopeActors.send(foreign, 'GET', feedbackDetails, undefined, params)).status).toBe(400);
    expect((await scopeActors.send(foreign, 'POST', `${path}stop/`, {})).status).toBe(404);
    expect((await scopeActors.send(foreign, 'POST', RERUN, { experiment_ids: [experimentId] })).status).toBe(404);
    expect((await scopeActors.send(foreign, 'DELETE', DELETE, { experiment_ids: [experimentId] })).status).toBe(404);
    expect(await detail()).toEqual(original);
    expect(await outputCells()).toEqual(outputs);
    const retained = await owner.api.get<{ result: { feedback: { id: string; value: string; comment: string }[] } }>(feedbackDetails, params);
    expect(retained.result.feedback.map(item => ({ id: item.id, value: item.value, comment: item.comment }))).toEqual([{ id: feedbackId, value: 'Pass', comment: `${prefix}-feedback` }]);
    await page.goto(`/dashboard/develop/${datasetId}?tab=experiments`, { waitUntil: 'domcontentloaded' });
    const rerun = async () => {
      const response = page.waitForResponse(r => new URL(r.url()).pathname === RERUN && r.request().method() === 'POST', { timeout: UI_READY });
      await experimentRow.getByRole('button', { name: 'Rerun Experiment', exact: true }).click();
      const result = await response;
      expect(result.status()).toBe(200);
      expect(result.request().postDataJSON()).toEqual({ experiment_ids: [experimentId] });
    };
    await rerun();
    const stopped = page.waitForResponse(r => new URL(r.url()).pathname === `${path}stop/` && r.request().method() === 'POST', { timeout: UI_READY });
    await experimentRow.getByRole('button', { name: 'Stop Experiment', exact: true }).click();
    expect((await stopped).status()).toBe(200);
    await expect.poll(async () => (await detail()).status, POLL.ASYNC_JOB).toBe('Cancelled');
    await expect(experimentRow.getByText('Cancelled', { exact: true })).toBeVisible({ timeout: UI_READY });
    await rerun();
    await expect.poll(async () => (await detail()).status, POLL.ASYNC_JOB).toBe('Completed');
    expect((await detail()).prompt_configs.map(config => config.id)).toEqual(original.prompt_configs.map(config => config.id));
    expect((await outputCells()).map(cell => cell.value)).toEqual(outputs.map(cell => cell.value));
    await experimentRow.getByRole('checkbox').check();
    await page.getByRole('button', { name: 'Delete', exact: true }).click();
    const deleted = page.waitForResponse(r => new URL(r.url()).pathname === DELETE && r.request().method() === 'DELETE', { timeout: UI_READY });
    await page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click();
    const deleteResponse = await deleted;
    expect(deleteResponse.status()).toBe(200);
    expect(deleteResponse.request().postDataJSON()).toEqual({ experiment_ids: [experimentId] });
    expect(await scopeProbe.pg('SELECT id,deleted FROM model_hub_experimentstable WHERE id=$1', [experimentId])).toEqual([{ id: experimentId, deleted: true }]);
    expect((await scopeActors.send(owner, 'GET', path)).status).toBe(404);
    const remaining = await owner.api.get<{ results: { id: string }[] }>(`${EXPERIMENTS}list/`, { dataset_id: datasetId });
    expect(remaining.results).toEqual([]);
    await expect(page.getByText('No experiments on this dataset yet', { exact: true })).toBeVisible({ timeout: UI_READY });
    await testInfo.attach('lifecycle-evidence', { body: JSON.stringify({ experimentId, rowId, feedbackId, persisted, requests: scopeActors.evidence() }), contentType: 'application/json' });
  } finally {
    await testInfo.attach('last-page-text', { body: await page.locator('body').innerText(), contentType: 'text/plain' });
    await context.close();
  }
});
