import { test, expect } from '../../lib/scope-actors';
import { inspectManagedMock, registerMockModel, MOCK_MODEL, MOCK_KEY } from '../../lib/managed-mock';
import { MockUpstream } from '../../lib/upstream';
import { uploadFixture } from '../../lib/upload';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// Current Results UI at 9009d0fa37ad3f213824bbe4347afe8bff5de877.
// Keep the original Pass/Fail evaluator: ScoreCard's choice editor submits Pass,
// not the pass_fail editor's Passed and not the numeric score 1.

// model_hub/urls.py, ExperimentCreateV2Serializer and FeedbackSerializer.
const EXPERIMENTS = '/model-hub/experiments/v2/';
const RERUN = `${EXPERIMENTS}re-run/`;
const DELETE = `${EXPERIMENTS}delete/`;
// UploadFileModal.jsx and CreateDatasetFromLocalFileRequestSerializer.
const UPLOAD = '/model-hub/develops/create-dataset-from-local-file/';
const UI_READY = 60_000;
interface Run { run_id: string; experiment_id: string; label: string; status: string; progress?: { status?: unknown; total?: unknown; done?: unknown } }
interface Detail { id: string; status: string; snapshot_dataset_id: string; prompt_configs: { id: string }[]; runs: Run[] }
interface Metric {
  key: string; name: string; output_type: string; choice_scores: Record<string, number>;
  uem_ids: Record<string, string>; feedback_column_ids: Record<string, string>;
}
interface ResultRow {
  example_id: string;
  results: Record<string, {
    feedback_row_id: string;
    scores: Record<string, { override?: { value: string; explanation: string } }>;
  }>;
}

test('PROMPT-E2E-006: inspect, give feedback, stop, resume, rerun and delete an experiment', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'PROMPT-E2E-006', area: 'prompts',
    userGoal: 'Reach an experiment through the dataset list, save row feedback and manage its execution without touching another organization',
    steps: [
      'upload a synthetic dataset and seed a prompt experiment through the public APIs',
      'open the focused-grid palette and list-row New experiment doors, then Open experiments and the experiment row',
      'inspect an evaluation, save feedback, close and reopen it',
      'check foreign-organization refusal for each read and lifecycle write',
      'rerun from the experiment list, stop with rows left, resume that same run from its experiment header to completion, then rerun to completion',
      'select the experiment by its identity and confirm deletion',
    ],
    backendChecks: [
      'the experiment produces three exact loopback-stand-in outputs on its own snapshot',
      'the browser feedback POST persists the chosen metric, column, row, value and explanation and reads them back',
      'browser rerun and stop yield Queued or Running then Cancelled; browser Resume completes the same run without a new attempt before fresh browser rerun, retaining configuration identity',
      'the browser DELETE soft-deletes exactly the selected experiment and removes it from its list and detail',
      'foreign experiment, row, feedback, stop, rerun and delete requests are refused without changing owner state',
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe }, testInfo) => {
  // Lifecycle ASYNC_JOB/UI_READY waits and setup headroom; all storage is PG.
  test.setTimeout(1_440_000);
  // Source-only diagnostic: Stop -> visible Stopped -> rows left -> Resume
  // same run -> Completed -> fresh rerun. API projections are not control phases.
  type RerunLabel = 'initial-rerun' | 'after-stop-rerun';
  // Fixed underscore-delimited parts preserve the two source codes without
  // the unchanged extractor masking a >=28-character literal. Never split raw input.
  type Code = readonly ['execution', 'key', 'conflict'] | readonly ['experiment', 'admission', 'cancelled'] | 'unavailable';
  type Stamp = { seq: number; elapsedMs: number | 'unavailable' };
  type Event = Stamp & {
    kind: 'rerun-enter' | 'pre-rerun' | 'rerun-response' | 'rerun-code' | 'detail' | 'stop-submit-boundary' |
      'stop-response' | 'stop-request-seen' | 'stop-receipt' | 'visible-stopped' |
      'remaining-work' | 'resume-submit' | 'resume-response' | 'resumed-completed';
    invocation?: RerunLabel; httpStatus?: number; serverCode?: Code;
    experimentStatus?: string; selectedRunApiStatus?: string; progressStatus?: string;
    identityMatch?: boolean | 'unavailable'; outcome?: 'passed' | 'failed';
  };
  const invocation = () => ({
    reached: false, preRerun: false, responseObserved: false,
    httpStatus: 'unavailable' as number | 'unavailable', serverCode: 'unavailable' as Code,
    codeReadState: 'not-started' as 'not-started' | 'pending' | 'observed' | 'failed',
  });
  const capture = {
    schema: 'prompt006-lifecycle-v3', selectedRunRole: 'selected-stop-target',
    serverCodeEncoding: 'underscore-parts',
    maxEvents: 32, maxBytes: 8192, maxPrettyLines: 300, maxLineChars: 300,
    captureFailed: false, truncated: false, omittedEventCount: 0,
    invocations: { 'initial-rerun': invocation(), 'after-stop-rerun': invocation() },
    boundaries: {
      selectedRunKnown: false, stopSubmit: false, stopResponse: false, stop202: false,
      stopRequestIdentityMatch: 'unavailable' as boolean | 'unavailable',
      stopReceipt: false, stopReceiptIdentityMatch: 'unavailable' as boolean | 'unavailable',
      visibleStoppedReached: false, visibleStoppedOutcome: 'not-reached' as 'not-reached' | 'passed' | 'failed',
    },
    resume: {
      countsValid: 'unavailable' as boolean | 'unavailable', rowsLeft: 'unavailable' as number | 'unavailable',
      submit: false, response: false, httpStatus: 'unavailable' as number | 'unavailable',
      identityMatch: 'unavailable' as boolean | 'unavailable',
      sameRunSet: 'unavailable' as boolean | 'unavailable', completed: false,
    },
    events: [] as Event[],
  };
  let captureClosed = false;
  const observe = (collect: () => void) => {
    if (captureClosed) return;
    try { collect(); } catch { capture.captureFailed = true; }
  };
  let started: number | undefined;
  let sequence = 0;
  let selectedRunId: string | undefined; // Internal comparison only; never serialized.
  observe(() => { started = performance.now(); });
  const record = (fields: Omit<Event, keyof Stamp>): Event => {
    const elapsed = started === undefined ? NaN : Math.floor(performance.now() - started);
    const validTime = Number.isSafeInteger(elapsed) && elapsed >= 0;
    if (!validTime) capture.captureFailed = true;
    const event: Event = { seq: ++sequence, elapsedMs: validTime ? elapsed : 'unavailable', ...fields };
    if (capture.events.length === capture.maxEvents) {
      capture.events.shift();
      capture.truncated = true;
      capture.omittedEventCount++;
    }
    capture.events.push(event);
    return event;
  };
  const fixed = <T extends string>(value: unknown, allowed: readonly T[]): T | 'unavailable' =>
    allowed.find(item => item === value) ?? 'unavailable';
  // StatusType (models/choices.py); ExperimentRun.Status (models/experiments.py);
  // progress_by_run (results_grid.py) plus detail's STOPPED display projection
  // (results_catalog.py:811-848). Preserve each vocabulary, including partial.
  const experimentStatuses = ['NotStarted', 'Queued', 'Running', 'Completed', 'Editing', 'Inactive',
    'Failed', 'PartialRun', 'ExperimentEvaluation', 'Uploading', 'PartialExtracted', 'Processing',
    'Deleting', 'PartialCompleted', 'OptimizationEvaluation', 'Error', 'Cancelled'] as const;
  const runStatuses = ['draft', 'queued', 'running', 'partial', 'completed', 'failed', 'cancelled'] as const;
  const progressStatuses = ['NotStarted', 'Queued', 'Running', 'Completed', 'Failed', 'Cancelled',
    'partial', 'PartialCompleted'] as const;
  const attachCapture = async () => {
    // Freeze before serialization; late optional body reads cannot rewrite the snapshot.
    captureClosed = true;
    try {
      // Only fixed ASCII strings, booleans and safe integers: this representation
      // also matches the unchanged extractor's Python json.dumps(indent=1).
      const fits = (body: string) => Buffer.byteLength(body, 'utf8') <= capture.maxBytes &&
        body.split('\n').length <= capture.maxPrettyLines &&
        body.split('\n').every(line => line.length <= capture.maxLineChars);
      let body = JSON.stringify(capture, null, 1);
      while (!fits(body) && capture.events.length) {
        capture.events.shift();
        capture.truncated = true;
        capture.omittedEventCount++;
        body = JSON.stringify(capture, null, 1);
      }
      // Fixed metadata fits alone; never emit an oversized fallback or raw data.
      if (!fits(body)) { capture.captureFailed = true; return; }
      await testInfo.attach('diag-prompt006-lifecycle', { body, contentType: 'application/json' });
    } catch { capture.captureFailed = true; } // Never replace the original outcome/cleanup.
  };
  const receipt = inspectManagedMock();
  await testInfo.attach('loopback-LLM-STANDIN', { body: JSON.stringify(receipt), contentType: 'application/json' });
  const owner = scopeActors.ownerA;
  const foreign = scopeActors.ownerB;
  const model = await registerMockModel(scopeActors.owners[0], scopeProbe, async () => expect(inspectManagedMock()).toEqual(receipt));
  const prefix = `e2e-lifecycle-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  // Keep generation pending while the rerun's Results header loads and Stop
  // confirms. The evaluator stays fast; the original, resumed and fresh runs
  // retain one prompt configuration and still use the existing polling budgets.
  const promptModel = 'gpt-4o-mini';
  const upstream = new MockUpstream();
  const promptBase = `${upstream.baseUrl(prefix, { latencyMs: 20_000 })}/v1`;
  await model.assertReady();
  await owner.api.post('/model-hub/custom_models/create/', {
    model_provider: 'openai', model_name: promptModel, input_token_cost: 0, output_token_cost: 0,
    config_json: { key: MOCK_KEY, api_base: promptBase },
  });
  // PromptTemplateViewSet.create_draft and PromptHistoryExecutionViewSet.list.
  const prompt = await owner.api.post<{ result: { id: string } }>('/model-hub/prompt-templates/create-draft/', {
    name: `${prefix}-prompt`, is_draft: false,
    prompt_config: [{ messages: [{ role: 'user', content: prefix }], configuration: { model: promptModel, temperature: 0, max_tokens: 128, tools: [] } }],
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
  let pendingRerun: { path: string; runId: string } | undefined;
  // One handler owns both the loopback guard and the one-shot rerun override.
  // Do not fall through from a page route into a second context handler.
  await context.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (!['localhost', '127.0.0.1'].includes(url.hostname) && !['data:', 'blob:'].includes(url.protocol))
      return route.abort('blockedbyclient');
    if (pendingRerun && url.pathname === pendingRerun.path) {
      expect(request.method()).toBe('POST');
      const body = request.postDataJSON();
      expect(body).toEqual({ source_ids: [pendingRerun.runId] });
      pendingRerun = undefined;
      return route.continue({ postData: JSON.stringify({ ...body, max_concurrent_rows: 1 }) });
    }
    return route.continue();
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
    // catalog-east occurs once in this three-row fixture; do not mask an
    // ambiguous or still-loading grid with first().
    const loadedCell = dataRows.getByRole('gridcell').filter({ hasText: /^catalog-east$/ });
    await expect(loadedCell).toHaveCount(1);
    await loadedCell.press('Control+Shift+P');
    await expect(page.getByRole('dialog')).toHaveCount(1, { timeout: UI_READY });
    await page.locator('[data-command="dataset.newExperiment"]').click();
    await expect(page.getByRole('textbox', { name: /^Experiment name/ })).toBeVisible({ timeout: UI_READY });
    await page.getByRole('button', { name: 'Close', exact: true }).click();
    await model.assertReady();
    const created = await owner.api.post<{ result: string }>(EXPERIMENTS, {
      name: `${prefix}-experiment`, dataset_id: datasetId, experiment_type: 'llm',
      prompt_config: [{ name: 'Prompt', prompt_id: prompt.result.id, prompt_version: versions.results[0].id, model: promptModel, model_params: { temperature: 0, max_tokens: 128 } }],
      user_eval_metrics: [{ name: 'output-check', template_id: evaluator.result.id, model: MOCK_MODEL, config: { mapping: { output: 'output' } } }],
    });
    const experimentId = /experiment_id=([0-9a-f-]{36})/.exec(created.result)?.[1];
    expect(experimentId).toBeTruthy();
    const path = `${EXPERIMENTS}${experimentId}/`;
    const detail = async () => {
      const result = (await owner.api.get<{ result: Detail }>(path)).result;
      observe(() => {
        // Observe existing reads only; the selected id never follows a later rerun.
        const matches = selectedRunId && Array.isArray(result?.runs)
          ? result.runs.filter(run => run.run_id === selectedRunId) : [];
        const selected = matches.length === 1 && matches[0].experiment_id === experimentId ? matches[0] : undefined;
        record({ kind: 'detail', experimentStatus: fixed(result?.status, experimentStatuses),
          identityMatch: selectedRunId === undefined || !Array.isArray(result?.runs) ? 'unavailable' : !!selected,
          selectedRunApiStatus: fixed(selected?.status, runStatuses),
          progressStatus: fixed(selected?.progress?.status, progressStatuses) });
      });
      return result;
    };
    await testInfo.attach('seeded-identities', { body: JSON.stringify({ ownerOrg: owner.organizationId, foreignOrg: foreign.organizationId, datasetId, experimentId, promptId: prompt.result.id, evaluatorId: evaluator.result.id }), contentType: 'application/json' });
    await expect.poll(async () => (await detail()).status, POLL.ASYNC_JOB).toBe('Completed');
    const original = await detail();
    expect(original.runs).toHaveLength(1);
    const originalRun = original.runs[0];
    expect(originalRun.experiment_id).toBe(experimentId);
    const runId = originalRun.run_id;
    const scopedRerunPath = `${path}rerun-cells/`;
    const runsPath = `/model-hub/datasets/${datasetId}/runs/`;
    const resultsPath = `/model-hub/datasets/${datasetId}/results/`;
    const catalog = await owner.api.get<{ result: { results: Run[]; metric_defs: Metric[] } }>(runsPath);
    expect(catalog.result.results.filter(run => run.experiment_id === experimentId).map(run => run.run_id)).toEqual([runId]);
    const metrics = catalog.result.metric_defs.filter(metric => metric.name === 'output-check');
    expect(metrics).toHaveLength(1);
    const metric = metrics[0];
    expect(metric.output_type).toBe('choice');
    expect(metric.choice_scores).toEqual({ Pass: 1, Fail: 0 });
    const metricId = metric.uem_ids[runId];
    const feedbackColumnId = metric.feedback_column_ids[runId];
    expect(metricId).toBeTruthy();
    expect(feedbackColumnId).toBeTruthy();
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
    const list = page.getByTestId('experiments-tab-groups');
    const nameLinkSelector = `a[data-row-link][href^="/dashboard/develop/experiment/${experimentId}/data?"][href*="&run=${runId}"]`;
    const rowForRun = (id: string) => list.getByRole('row').filter({
      has: page.locator(`a[data-row-link][href^="/dashboard/develop/experiment/${experimentId}/data?"][href*="&run=${id}"]`),
    });
    const experimentRow = rowForRun(runId);
    await expect(experimentRow).toHaveCount(1, { timeout: UI_READY });
    await experimentRow.getByRole('link').and(page.locator(nameLinkSelector)).click();
    await expect(page).toHaveURL(new RegExp(`/experiment/${experimentId}/data`), { timeout: UI_READY });
    const rows = await scopeProbe.pg<{ id: string }>('SELECT id FROM model_hub_row WHERE dataset_id=$1 AND NOT deleted ORDER BY id', [original.snapshot_dataset_id]);
    expect(rows).toHaveLength(3);
    const rowId = rows[0].id;
    // ResultsGrid keys by example_id, while Feedback writes snapshot row_id.
    // results_feedback.py:120-130,196-202 supplies the immutable binding.
    const resultRows = await owner.api.get<{ result: { total: number; rows: ResultRow[] } }>(resultsPath, { runs: runId, page: 0, page_size: 1000 });
    expect(resultRows.result.total).toBe(3);
    expect(resultRows.result.rows).toHaveLength(3);
    expect(resultRows.result.rows.map(row => row.results[runId].feedback_row_id).sort()).toEqual(rows.map(row => row.id).sort());
    const chosenRows = resultRows.result.rows.filter(row => row.results[runId].feedback_row_id === rowId);
    expect(chosenRows).toHaveLength(1);
    const exampleId = chosenRows[0].example_id;
    expect(exampleId).toBeTruthy();
    const feedback = page.getByTestId('row-panel');
    const openFeedback = async () => {
      const outputCell = page.locator(`[data-results-grid-root] .ag-row[row-id="${exampleId}"] .ag-cell[col-id="output|${runId}"]`);
      await expect(outputCell).toHaveCount(1, { timeout: UI_READY });
      await expect(outputCell).toContainText(`echo: ${JSON.stringify([{ type: 'text', text: prefix }])}`);
      await outputCell.click();
      await expect(feedback).toBeVisible({ timeout: UI_READY });
      await feedback.getByTestId('row-tab-compare').click();
      await feedback.getByRole('button', { name: `${metric.name} ${originalRun.label}`, exact: true }).click();
      await expect(feedback.getByRole('button', { name: 'Close score card', exact: true })).toBeVisible();
    };
    await openFeedback();
    await feedback.getByRole('button', { name: 'Override', exact: true }).click();
    await feedback.getByRole('combobox', { name: 'Corrected label', exact: true }).click();
    await page.getByRole('option', { name: 'Pass', exact: true }).click();
    await feedback.getByRole('textbox', { name: 'Comment', exact: true }).fill(`${prefix}-feedback`);
    const feedbackPath = `${path}feedback/`;
    const feedbackSaved = page.waitForResponse(r => new URL(r.url()).pathname === feedbackPath && r.request().method() === 'POST', { timeout: UI_READY });
    await feedback.getByRole('button', { name: 'Save correction', exact: true }).click();
    const feedbackResponse = await feedbackSaved;
    expect(feedbackResponse.status()).toBe(200);
    const feedbackId = (await feedbackResponse.json()).result.id as string;
    const body = feedbackResponse.request().postDataJSON();
    expect(feedbackId).toBeTruthy();
    expect(body).toEqual({ source: 'experiment', source_id: feedbackColumnId, user_eval_metric: metricId, row_id: rowId, value: 'Pass', explanation: `${prefix}-feedback` });
    const persisted = await scopeProbe.pg<{ id: string; source_id: string; row_id: string; user_eval_metric_id: string; value: string; explanation: string }>(
      'SELECT id,source_id,row_id,user_eval_metric_id,value,explanation FROM model_hub_feedback WHERE id=$1 AND organization_id=$2 AND NOT deleted', [feedbackId, owner.organizationId]);
    expect(persisted).toEqual([{ id: feedbackId, source_id: body.source_id, row_id: rowId, user_eval_metric_id: body.user_eval_metric, value: 'Pass', explanation: `${prefix}-feedback` }]);
    await expect(feedback.getByRole('paragraph').filter({ hasText: new RegExp(`^${prefix}-feedback$`) })).toHaveText(`${prefix}-feedback`, { timeout: UI_READY });
    await expect(feedback.getByTestId('score-card-value')).toHaveText('Pass');
    await feedback.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(feedback).toBeHidden();
    await page.reload({ waitUntil: 'domcontentloaded' });
    await openFeedback();
    await expect(feedback.getByRole('paragraph').filter({ hasText: new RegExp(`^${prefix}-feedback$`) })).toHaveText(`${prefix}-feedback`, { timeout: UI_READY });
    await expect(feedback.getByTestId('score-card-value')).toHaveText('Pass');
    const readback = await owner.api.get<{ result: { rows: ResultRow[] } }>(resultsPath, { runs: runId, example_id: exampleId, full: true });
    expect(readback.result.rows).toHaveLength(1);
    expect(readback.result.rows[0].example_id).toBe(exampleId);
    expect(readback.result.rows[0].results[runId].feedback_row_id).toBe(rowId);
    expect(readback.result.rows[0].results[runId].scores[metric.key].override).toMatchObject({ value: 'Pass', explanation: `${prefix}-feedback` });
    const feedbackDetails = `${feedbackPath}get-feedback-details/`;
    const params = { user_eval_metric_id: body.user_eval_metric, row_id: rowId };
    expect((await scopeActors.send(foreign, 'GET', path)).status).toBe(404);
    expect((await scopeActors.send(foreign, 'GET', `${path}rows/${rowId}/`)).status).toBe(404);
    expect((await scopeActors.send(foreign, 'POST', feedbackPath, body)).status).toBe(400);
    expect((await scopeActors.send(foreign, 'GET', feedbackDetails, undefined, params)).status).toBe(400);
    expect((await scopeActors.send(foreign, 'POST', `${path}stop/`, {})).status).toBe(404);
    // Preserve the old route's isolation assertion as well as testing the
    // current UI route. At this base experiments.py:7009-7015 returns
    // bad_request for an invisible rerun-cells experiment; the explicit
    // regression in test_native_prompt_create_bridge.py:1168-1176 asserts 400.
    expect((await scopeActors.send(foreign, 'POST', RERUN, { experiment_ids: [experimentId] })).status).toBe(404);
    expect((await scopeActors.send(foreign, 'POST', scopedRerunPath, { source_ids: [runId] })).status).toBe(400);
    expect((await scopeActors.send(foreign, 'DELETE', DELETE, { experiment_ids: [experimentId] })).status).toBe(404);
    expect(await detail()).toEqual(original);
    expect(await outputCells()).toEqual(outputs);
    const retained = await owner.api.get<{ result: { feedback: { id: string; value: string; comment: string }[] } }>(feedbackDetails, params);
    expect(retained.result.feedback.map(item => ({ id: item.id, value: item.value, comment: item.comment }))).toEqual([{ id: feedbackId, value: 'Pass', comment: `${prefix}-feedback` }]);
    const listUrl = `/dashboard/develop/${datasetId}?tab=experiments`;
    const rerun = async (label: RerunLabel) => {
      observe(() => {
        capture.invocations[label].reached = true;
        record({ kind: 'rerun-enter', invocation: label });
      });
      const before = await detail();
      // attemptsModel.js: every=all keeps the ORIGINAL run reachable after
      // a rerun creates a new attempt. Both writes must name that same run.
      await page.goto(`${listUrl}&every=all`, { waitUntil: 'domcontentloaded' });
      await expect(experimentRow).toHaveCount(1, { timeout: UI_READY });
      await experimentRow.hover();
      await experimentRow.getByRole('button', { name: `${originalRun.label} actions`, exact: true }).click();
      await page.getByRole('menuitem', { name: 'Rerun', exact: true }).click();
      await page.getByRole('menuitem', { name: 'Rerun all rows', exact: true }).click();
      if (label === 'initial-rerun') {
        // ExperimentRerunCellsSerializer.max_concurrent_rows (experiments.py:560-564)
        // accepts 1. Stop drains in-flight rows, so keep two of three undispatched.
        expect(pendingRerun).toBeUndefined();
        pendingRerun = { path: scopedRerunPath, runId };
      }
      const response = page.waitForResponse(r => new URL(r.url()).pathname === scopedRerunPath && r.request().method() === 'POST', { timeout: UI_READY });
      observe(() => {
        capture.invocations[label].preRerun = true;
        record({ kind: 'pre-rerun', invocation: label });
      });
      await page.getByRole('dialog').getByRole('button', { name: 'Re-run', exact: true }).click();
      const result = await response;
      observe(() => {
        const state = capture.invocations[label];
        state.responseObserved = true;
        state.httpStatus = result.status();
        record({ kind: 'rerun-response', invocation: label, httpStatus: state.httpStatus });
        // Do not await an optional body before the original assertion or cleanup.
        // Pending/failed reads stay explicit and never serialize a message or body.
        state.codeReadState = 'pending';
        void result.json().then((payload: unknown) => {
          observe(() => {
            const code = payload !== null && typeof payload === 'object' && !Array.isArray(payload)
              ? (payload as { code?: unknown }).code : undefined;
            // Exact top-level literals from experiments.py's 409 branches only.
            const safeCode: Code = code === 'execution_key_conflict' ? ['execution', 'key', 'conflict']
              : code === 'experiment_admission_cancelled' ? ['experiment', 'admission', 'cancelled'] : 'unavailable';
            state.codeReadState = 'observed';
            state.serverCode = safeCode;
            record({ kind: 'rerun-code', invocation: label, serverCode: safeCode });
          });
        }).catch(() => observe(() => {
          state.codeReadState = 'failed';
          capture.captureFailed = true;
        }));
      });
      expect(result.status()).toBe(200);
      expect(pendingRerun).toBeUndefined();
      expect(result.request().postDataJSON()).toEqual(label === 'initial-rerun'
        ? { source_ids: [runId], max_concurrent_rows: 1 }
        : { source_ids: [runId] });
      const admitted = await detail();
      expect(['Queued', 'Running']).toContain(admitted.status);
      const newRuns = admitted.runs.filter(run => !before.runs.some(previous => previous.run_id === run.run_id));
      expect(newRuns).toHaveLength(1);
      expect(newRuns[0].experiment_id).toBe(experimentId);
      return newRuns[0];
    };
    // The scoped log records arrivals before the delay; exclude evaluator traffic
    // and baseline the completed original run before admitting the stop target.
    const generationCount = async () => (await upstream.requests(prefix))
      .filter(request => request.method === 'POST' && request.path.endsWith('/chat/completions')).length;
    const generationsBeforeStop = await generationCount();
    const stoppedRun = await rerun('initial-rerun');
    selectedRunId = stoppedRun.run_id; // Control identity must not depend on diagnostics.
    observe(() => {
      capture.boundaries.selectedRunKnown = true;
    });
    // The header scopes Stop to every active run of this experiment, even
    // when its Results link is focused on the original completed run.
    await expect(experimentRow).toHaveCount(1, { timeout: UI_READY });
    await experimentRow.getByRole('link').and(page.locator(nameLinkSelector)).click();
    // With no generated rows yet, the active header says Waiting, not Running.
    // Scope to the experiment header, not a run chip in the Results grid.
    const activeHeader = page.getByRole('button', { name: 'Switch experiment', exact: true })
      .locator('xpath=ancestor::div[.//*[@data-testid="exp-stop"]][1]');
    await expect(activeHeader.getByText('Waiting', { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByTestId('exp-stop')).toBeEnabled({ timeout: UI_READY });
    await expect.poll(async () => (await generationCount()) - generationsBeforeStop, {
      timeout: UI_READY, intervals: [100, 250, 500],
    }).toBe(1);
    await page.getByTestId('exp-stop').click();
    // Selected-run control is on by default. Stop accepts the active run ids
    // asynchronously; persist_run_state later projects drained STOPPED to Cancelled.
    const stopped = page.waitForResponse(r => new URL(r.url()).pathname === `${path}stop/` && r.request().method() === 'POST', { timeout: UI_READY });
    observe(() => {
      capture.boundaries.stopSubmit = true;
      record({ kind: 'stop-submit-boundary' });
    });
    await page.getByRole('dialog').getByRole('button', { name: 'Stop run', exact: true }).click();
    const stopResponse = await stopped;
    observe(() => {
      capture.boundaries.stopResponse = true;
      const httpStatus = stopResponse.status();
      capture.boundaries.stop202 = httpStatus === 202;
      record({ kind: 'stop-response', httpStatus });
    });
    observe(() => {
      // This observes the existing request at response receipt, not its send time.
      const ids = stopResponse.request().postDataJSON()?.run_ids;
      const identityMatch = Array.isArray(ids) && ids.length === 1 && ids[0] === selectedRunId;
      capture.boundaries.stopRequestIdentityMatch = identityMatch;
      record({ kind: 'stop-request-seen', identityMatch });
    });
    expect(stopResponse.status()).toBe(202);
    expect(stopResponse.request().postDataJSON()).toEqual({ run_ids: [stoppedRun.run_id] });
    const stopResult = (await stopResponse.json()).result;
    observe(() => {
      capture.boundaries.stopReceipt = true;
      const identityMatch = stopResult?.experiment_id === experimentId && Array.isArray(stopResult?.runs) &&
        stopResult.runs.length === 1 && stopResult.runs[0]?.run_id === selectedRunId;
      capture.boundaries.stopReceiptIdentityMatch = identityMatch;
      record({ kind: 'stop-receipt', identityMatch });
    });
    expect(stopResult.experiment_id).toBe(experimentId);
    expect(stopResult.runs.map((run: { run_id: string }) => run.run_id)).toEqual([stoppedRun.run_id]);
    await expect.poll(async () => (await detail()).status, POLL.ASYNC_JOB).toBe('Cancelled');
    // runStatus.js maps backend Cancelled to Stopped. Assert the run chip
    // (RunChip labels a stopped run "Stopped at <done> of <total> rows"); the
    // run actions chip also says Stopped, so an unscoped text match is
    // ambiguous. The list's Status column is hidden by default and its AG Grid
    // center row is not the pinned name-link row.
    observe(() => { capture.boundaries.visibleStoppedReached = true; });
    try {
      await expect(page.getByLabel(/^Stopped at \d+ of \d+ rows$/)).toBeVisible({ timeout: UI_READY });
      observe(() => {
        capture.boundaries.visibleStoppedOutcome = 'passed';
        record({ kind: 'visible-stopped', outcome: 'passed' });
      });
    } catch (error) {
      observe(() => {
        capture.boundaries.visibleStoppedOutcome = 'failed';
        record({ kind: 'visible-stopped', outcome: 'failed' });
      });
      throw error;
    }
    await page.goto(listUrl, { waitUntil: 'domcontentloaded' });
    const stoppedRow = rowForRun(stoppedRun.run_id);
    await expect(stoppedRow).toHaveCount(1, { timeout: UI_READY });
    const beforeResume = await detail();
    expect(beforeResume.status).toBe('Cancelled');
    const selectedStopped = beforeResume.runs.filter(run => run.run_id === selectedRunId);
    expect(selectedStopped).toHaveLength(1);
    expect(selectedStopped[0].experiment_id).toBe(experimentId);
    expect(selectedStopped[0].status).toBe('cancelled');
    expect(selectedStopped[0].progress?.status).toBe('Cancelled');
    // results_grid.py:316-377 counts examples from the pinned plan. Missing
    // counts are unavailable, never zero; runResume.js uses total - done.
    const { total, done } = selectedStopped[0].progress ?? {};
    if (typeof total !== 'number' || !Number.isSafeInteger(total) || total < 0 ||
        typeof done !== 'number' || !Number.isSafeInteger(done) || done < 0 || done > total) {
      observe(() => {
        capture.resume.countsValid = false;
        record({ kind: 'remaining-work', outcome: 'failed' });
      });
      throw new Error('Stopped run requires available nonnegative integer progress counts with done <= total');
    }
    const rowsLeft = total - done;
    observe(() => {
      capture.resume.countsValid = true;
      capture.resume.rowsLeft = rowsLeft;
      record({ kind: 'remaining-work', outcome: rowsLeft > 0 ? 'passed' : 'failed' });
    });
    expect(rowsLeft, 'Stop must leave work for the same run to Resume').toBeGreaterThan(0);
    const runIdsBeforeResume = beforeResume.runs.map(run => run.run_id).sort();
    await stoppedRow.locator(`a[data-row-link][href*="&run=${selectedRunId}"]`).click();
    await expect(page).toHaveURL(new RegExp(`/experiment/${experimentId}/data`), { timeout: UI_READY });
    const resumeButton = page.getByTestId('exp-resume');
    await expect(resumeButton).toBeEnabled({ timeout: UI_READY });
    await expect(resumeButton).toHaveText(/^Resume [1-9]\d* left$/, { timeout: UI_READY });
    const resumeResponse = page.waitForResponse(r => new URL(r.url()).pathname === `${path}resume/` && r.request().method() === 'POST', { timeout: UI_READY });
    observe(() => {
      capture.resume.submit = true;
      record({ kind: 'resume-submit' });
    });
    await resumeButton.click();
    const resumed = await resumeResponse;
    observe(() => {
      capture.resume.response = true;
      capture.resume.httpStatus = resumed.status();
      record({ kind: 'resume-response', httpStatus: resumed.status() });
    });
    expect(resumed.status()).toBe(202);
    expect(resumed.request().postDataJSON()).toEqual({ run_ids: [selectedRunId] });
    const resumedBody = await resumed.json() as { result: {
      experiment_id: string; runs: { run_id: string; error?: unknown }[];
    } };
    observe(() => {
      const result = resumedBody?.result;
      capture.resume.identityMatch = result?.experiment_id === experimentId && Array.isArray(result?.runs) &&
        result.runs.length === 1 && result.runs[0]?.run_id === selectedRunId;
    });
    expect(resumedBody.result.experiment_id).toBe(experimentId);
    expect(resumedBody.result.runs.map(run => run.run_id)).toEqual([selectedRunId]);
    expect(resumedBody.result.runs.every(run => !run.error)).toBe(true);
    await expect.poll(async () => {
      const resumedDetail = await detail();
      const runIds = resumedDetail.runs.map(run => run.run_id).sort();
      observe(() => {
        capture.resume.sameRunSet = runIds.length === runIdsBeforeResume.length &&
          runIds.every((id, index) => id === runIdsBeforeResume[index]);
      });
      expect(runIds).toEqual(runIdsBeforeResume);
      const selected = resumedDetail.runs.filter(run => run.run_id === selectedRunId);
      expect(selected).toHaveLength(1);
      expect(selected[0].experiment_id).toBe(experimentId);
      return { experimentStatus: resumedDetail.status, selectedRunStatus: selected[0].status,
        progressStatus: selected[0].progress?.status };
    }, POLL.ASYNC_JOB).toEqual({ experimentStatus: 'Completed', selectedRunStatus: 'completed', progressStatus: 'Completed' });
    observe(() => {
      capture.resume.completed = true;
      record({ kind: 'resumed-completed', outcome: 'passed' });
    });
    const completedRun = await rerun('after-stop-rerun');
    await expect.poll(async () => (await detail()).status, POLL.ASYNC_JOB).toBe('Completed');
    const completed = await detail();
    expect(completed.prompt_configs.map(config => config.id)).toEqual(original.prompt_configs.map(config => config.id));
    expect((await outputCells()).map(cell => cell.value)).toEqual(outputs.map(cell => cell.value));
    expect(await scopeProbe.pg<{ id: string }>('SELECT id FROM model_hub_row WHERE dataset_id=$1 AND NOT deleted ORDER BY id', [original.snapshot_dataset_id])).toEqual(rows);
    await page.goto(listUrl, { waitUntil: 'domcontentloaded' });
    const completedRow = rowForRun(completedRun.run_id);
    await expect(completedRow).toHaveCount(1, { timeout: UI_READY });
    expect(completed.runs.find(run => run.run_id === completedRun.run_id)?.status.toLowerCase()).toBe('completed');
    await completedRow.getByRole('checkbox').check();
    // The bulk button now requires every kept attempt selected. The current
    // row context menu explicitly deletes the whole identified experiment,
    // including its kept attempts (LeaderboardGrid.jsx:547-559).
    await completedRow.locator(`a[data-row-link][href*="&run=${completedRun.run_id}"]`).click({ button: 'right' });
    await page.getByRole('menuitem', { name: `Delete experiment (${completed.runs.length} runs)`, exact: true }).click();
    const deleted = page.waitForResponse(r => new URL(r.url()).pathname === DELETE && r.request().method() === 'DELETE', { timeout: UI_READY });
    await page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click();
    const deleteResponse = await deleted;
    expect(deleteResponse.status()).toBe(200);
    expect(deleteResponse.request().postDataJSON()).toEqual({ experiment_ids: [experimentId] });
    expect(await scopeProbe.pg('SELECT id,deleted FROM model_hub_experimentstable WHERE id=$1', [experimentId])).toEqual([{ id: experimentId, deleted: true }]);
    expect((await scopeActors.send(owner, 'GET', path)).status).toBe(404);
    const remaining = await owner.api.get<{ results: { id: string }[] }>(`${EXPERIMENTS}list/`, { dataset_id: datasetId });
    expect(remaining.results).toEqual([]);
    await expect(list.locator(`a[data-row-link][href^="/dashboard/develop/experiment/${experimentId}/data?"]`)).toHaveCount(0, { timeout: UI_READY });
    // LeaderboardStates.jsx:140-144 is the current empty-list copy.
    await expect(page.getByTestId('experiments-empty-line')).toHaveText('No experiments yet. Pick one way to run; add another later to see which wins.', { timeout: UI_READY });
    await testInfo.attach('lifecycle-evidence', { body: JSON.stringify({ experimentId, runId, exampleId, rowId, metricId, feedbackColumnId, feedbackId, persisted, stoppedRunId: stoppedRun.run_id, completedRunId: completedRun.run_id, requests: scopeActors.evidence() }), contentType: 'application/json' });
  } finally {
    await attachCapture();
    try {
      if (!page.isClosed())
        await testInfo.attach('last-page-text', { body: await page.locator('body').innerText(), contentType: 'text/plain' });
    } catch (error) {
      // A routing failure can close the page while its optional text is read.
      if (!page.isClosed()) throw error;
    } finally {
      await context.close();
    }
  }
});
