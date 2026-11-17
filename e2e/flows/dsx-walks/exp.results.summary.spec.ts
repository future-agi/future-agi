import { createHash, randomUUID } from 'node:crypto';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned ExperimentAnalysisView.jsx is the active Summary, not SummaryToolbar.
// It has no chart/export: this walk qualifies the real analysis table only.
// External-run bodies follow frontend/src/api/develop/external-run-handoff.js;
// version/metric/result setup follows DATA-E2E-179's public API fixture. No model executes.
const CREATE = '/model-hub/develops/create-dataset-manually/';
const RUNS = '/model-hub/experiment-runs/';
const analysisPath = (id: string) => `/model-hub/datasets/${id}/results/analysis/`;
const UI_READY = 60_000;
type Created = { result: { dataset_id: string } };
type Metric = { result: { metric_id: string; evaluator_version_id: string; name: string } };
type Run = { result: { id: string; label: string; evaluator_bindings: { metric_id: string; evaluator_version_id: string }[] } };
type Analysis = { status: boolean; result: {
  dataset_id: string; running: boolean; examples_in_view: number; examples_total: number;
  baseline_run_id: string | null; candidate_run_id: string | null;
  scope: { complete: boolean; population: string };
  runs: { run_id: string; name: string; label: string; status: string; dataset_version_id: string;
    planned_trials: number; recorded_trials: number; missing_trials: number }[];
  metrics: { key: string; name: string; aggregation: string; denominator_unit: string;
    per_run: { run_id: string; value: number; n: number; expected_n: number; missing_n: number; evaluator_version_id: string }[];
    vs_baseline: { run_id: string; baseline_run_id: string; delta: number; n: number; availability: { state: string; reason: string | null } }[] }[];
} };

test('@DSX-WALK-RES-51: Compare exact recorded scores in Summary and refuse another tenant’s analysis references.', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DSX-WALK-RES-51', area: 'dsx-walks',
    userGoal: 'Read scored-trial means and paired differences for two recorded runs without counting missing evidence as zero',
    steps: ['seed an immutable two-example version, a pass/fail metric and two external runs with synthetic recorded scores',
      'open the compare Data page and select Summary', 'verify means and denominators, change to no baseline and reload',
      'attempt foreign analysis and foreign run-reference reads'],
    backendChecks: ['the two runs have two recorded trials each on the same version and evaluator',
      'means are 0.5 and 1; the candidate-minus-baseline delta is 0.5 over two paired trials',
      'no-baseline read has no paired comparisons; foreign dataset and foreign run references return 404'],
  }),
}, async ({ browser, scopeActors: scopes }, testInfo) => {
  test.setTimeout(600_000);
  const a = scopes.ownerA, b = scopes.ownerB;
  const stem = `e2e-dsx-res51-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const make = async (actor: typeof a, name: string) => {
    const created = await scopes.send<Created>(actor, 'POST', CREATE, {
      dataset_name: name, model_type: 'GenerativeLLM', number_of_rows: 2, number_of_columns: 1,
      columns: [{ name: 'input', type: 'text', role: 'input' }],
    });
    expect(created.status).toBe(200);
    return created.body.result.dataset_id;
  };
  const datasetId = await make(a, stem), foreignDatasetId = await make(b, `${stem}-foreign`);
  const metricName = `${stem}-correct`;
  const metric = await scopes.send<Metric>(a, 'POST', `/model-hub/datasets/${datasetId}/external-metrics/`, {
    name: metricName, output_type: 'pass_fail', higher_is_better: true,
    source_hash: createHash('sha256').update(`${stem}-scorer`).digest('hex'),
  });
  expect(metric.status).toBe(200);
  const { metric_id: metricId, evaluator_version_id: evaluatorId } = metric.body.result;
  const examples = [randomUUID(), randomUUID()];
  const version = await scopes.send<{ result: { id: string } }>(a, 'POST', `/model-hub/datasets/${datasetId}/versions/`, {
    description: stem,
    items: examples.map((example_id, i) => ({ example_id, input: { question: `synthetic-${i}` }, expected: { answer: `answer-${i}` } })),
  });
  expect(version.status).toBe(201);
  const versionId = version.body.result.id;
  const runs: { id: string; label: string; name: string }[] = [];
  for (const [variant, scores] of [['baseline', [0, 1]], ['candidate', [1, 1]]] as const) {
    const id = randomUUID(), name = `${stem}-${variant}`;
    const created = await scopes.send<Run>(a, 'POST', RUNS, {
      run_id: id, dataset_version_id: versionId, experiment_id: null,
      expected_items: examples.map(example_id => ({ example_id, trial_number: 1 })),
      evaluator_metric_ids: [metricId], model_config: {}, metadata: { name },
    });
    expect(created.status).toBe(200);
    expect(created.body.result.id).toBe(id);
    expect(created.body.result.evaluator_bindings).toEqual([expect.objectContaining({ metric_id: metricId, evaluator_version_id: evaluatorId })]);
    const written = await scopes.send(a, 'POST', `${RUNS}${id}/results/`, {
      items: examples.map((example_id, i) => ({ example_id, trial_number: 1,
        receipt_key: `${stem}-${variant}-${i}`, status: 'completed', output: { text: `${variant}-${i}` },
        latency_ms: 12, cost: 0,
        score_refs: [{ metric_id: metricId, evaluator_version_id: evaluatorId, status: 'assessed', value: scores[i] }],
      })),
    });
    expect(written.status).toBe(200);
    runs.push({ id, name, label: created.body.result.label });
  }
  const [base, candidate] = runs;
  await testInfo.attach('seeded-ids', { contentType: 'application/json', body: JSON.stringify({
    datasetId, foreignDatasetId, versionId, examples, metricId, evaluatorId, runs,
    organizationA: a.organizationId, organizationB: b.organizationId,
  }) });
  const params = { runs: runs.map(r => r.id).join(','), baseline: base.id, cand: candidate.id, include: 'root_causes' };
  const assertAnalysis = (body: Analysis, baseline: boolean) => {
    expect(body.status).toBe(true);
    const result = body.result;
    expect(result).toMatchObject({ dataset_id: datasetId, running: false, examples_in_view: 2, examples_total: 2,
      baseline_run_id: baseline ? base.id : null, candidate_run_id: baseline ? candidate.id : null,
      scope: { complete: true, population: 'full_filtered_plan' } });
    expect(result.runs).toHaveLength(2);
    runs.forEach(run => expect(result.runs.find(r => r.run_id === run.id)).toMatchObject({
      name: run.name, label: run.label, status: 'completed', dataset_version_id: versionId,
      planned_trials: 2, recorded_trials: 2, missing_trials: 0,
    }));
    expect(result.metrics).toHaveLength(1);
    const metric = result.metrics[0];
    expect(metric).toMatchObject({ key: metricId, name: metricName, aggregation: 'assessed_trial_mean', denominator_unit: 'example_trial' });
    expect(metric.per_run).toHaveLength(2);
    for (const [run, value] of [[base, 0.5], [candidate, 1]] as const)
      expect(metric.per_run.find(r => r.run_id === run.id)).toMatchObject({ value, n: 2, expected_n: 2, missing_n: 0, evaluator_version_id: evaluatorId });
    expect(metric.vs_baseline).toEqual(baseline ? [expect.objectContaining({ run_id: candidate.id,
      baseline_run_id: base.id, delta: 0.5, n: 2, availability: { state: 'available', reason: null } })] : []);
  };
  assertAnalysis(await a.api.get<Analysis>(analysisPath(datasetId), params), true);
  const context = await scopes.openContext(browser, a);
  await context.route('**/*', r => ['localhost', '127.0.0.1'].includes(new URL(r.request().url()).hostname)
    ? r.continue() : r.abort('blockedbyclient'));
  try {
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    await page.setViewportSize({ width: 1600, height: 1000 });
    await page.goto(`/dashboard/develop/${datasetId}/compare/data?${new URLSearchParams(params)}`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('tab', { name: 'Summary', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Summary', exact: true, level: 1 })).toBeVisible();
    await expect(page.getByText('Showing 2 of 2 examples. Averages use scored trials only. Missing, errored and unscored trials are left out, not counted as zero.', { exact: true })).toBeVisible();
    const values = page.getByRole('table', { name: `Values for ${metricName}`, exact: true });
    for (const [run, mean, difference] of [[base, '0.5', 'Baseline'], [candidate, '1', '0.5 · 2 trials compared']] as const) {
      const row = values.getByRole('row').filter({ hasText: `${run.name} (${run.label})` });
      await expect(row.getByRole('cell')).toHaveText([
        `${run.name} (${run.label})completed`, mean, '2 / 2; 0 not recorded', difference,
      ]);
    }
    const withoutBaseline = page.waitForResponse(r => {
      const url = new URL(r.url());
      return url.pathname === analysisPath(datasetId) && url.searchParams.get('baseline') === 'none';
    });
    await page.getByRole('combobox', { name: 'Analysis baseline', exact: true }).selectOption('none');
    const read = await withoutBaseline;
    expect(read.status()).toBe(200);
    assertAnalysis(await read.json() as Analysis, false);
    await expect(values.getByRole('cell', { name: 'No baseline', exact: true })).toHaveCount(2);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('combobox', { name: 'Analysis baseline', exact: true })).toHaveValue('none');
    await expect(values.getByRole('cell', { name: 'No baseline', exact: true })).toHaveCount(2);
  } finally { await context.close(); }
  expect((await scopes.send(b, 'GET', analysisPath(datasetId), undefined, params)).status).toBe(404);
  expect((await scopes.send(b, 'GET', analysisPath(foreignDatasetId), undefined, params)).status).toBe(404);
  expect((await scopes.send(b, 'GET', `${RUNS}${base.id}/`)).status).toBe(404);
  assertAnalysis(await a.api.get<Analysis>(analysisPath(datasetId), params), true);
});
