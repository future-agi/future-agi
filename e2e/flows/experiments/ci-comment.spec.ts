import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { EXPERIMENTS, RUNS, NATIVE_TERMINAL, expectedSnippet, seedCIComparison } from './ci-comment-fixture';

// README Writing a flow: first paint/navigation budget per UI phase.
const UI_READY = 60_000;
const SEED_API = 120_000;
// ciComment.js curl --max-time 60; same ceiling for the custom upload.
const CI_CALL = 60_000;
// Primary Postgres ingestion reads, not CDC; models/experiments.py.
const PRIMARY_VISIBLE = 60_000;
const HEADROOM = 30_000;

test('EXP-E2E-012: copy a CI comment step and report a custom uploaded run against its baseline', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EXP-E2E-012', area: 'experiments',
    userGoal: 'Use Run in CI with its documented custom upload path, read the pull-request comment body, and open the uploaded run in Results',
    steps: [
      'API-seed a uniquely named native prompt experiment, a four-example immutable version, two numeric metrics, a completed baseline and a fresh candidate header',
      'open Summary and Run in CI; select the baseline, allow one regression and include one ranked row; assert the complete HTTPS snippet and copied clipboard text',
      'perform the documented custom upload through the canonical results endpoint and fetch the comment using only the organization API-key pair on localhost',
      'assert the exact failing Markdown including all comparison counts, the ranked row and evaluator, and the Results link',
      'open the unmodified returned results_path and assert exactly the four candidate outputs in that run column',
    ],
    backendChecks: [
      'storage lane: native seed reaches terminal state within NATIVE_TERMINAL (150s); candidate header starts draft with no result items',
      'storage lane: PRIMARY_VISIBLE (60s) reads exactly one completed candidate for this experiment/version and exactly four completed items with their outputs and evaluator scores from Postgres',
      'API lane: two CI_CALL budgets (60s each) cover custom upload and comment GET; exact body has passed=false, two improved/regressed/unchanged/not-comparable comparisons and top_n=1',
      'UI lane: three UI_READY phases (60s each) cover Summary, copied snippet and returned Results path with the exact candidate output set',
    ],
  }),
}, async ({ actor, probe, page, context }, testInfo) => {
  test.setTimeout(SEED_API + NATIVE_TERMINAL + 3 * UI_READY + 2 * CI_CALL + PRIMARY_VISIBLE + HEADROOM);
  // No production requests, launch overrides, generated runner execution or GitHub posting.
  for (const url of [E2E.apiUrl, E2E.appUrl, E2E.mockLlmUrl, E2E.pgUrl]) {
    expect(new URL(url).hostname, 'this flow requires the localhost stack').toBe('localhost');
  }
  await context.route('**/*', route => new URL(route.request().url()).hostname === 'localhost'
    ? route.continue() : route.abort('blockedbyclient'));
  page.setDefaultTimeout(UI_READY);
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: E2E.appUrl });
  const seed = await seedCIComparison(actor, probe, testInfo);

  // models/experiments.py: scope reads to minted run ids, not worker-wide counts.
  const runState = () => probe.pg(
    `SELECT id, experiment_id, dataset_version_id, task_kind, status FROM model_hub_experimentrun
     WHERE id=$1 AND NOT deleted`, [seed.candidateId]);
  expect(await runState()).toEqual([{ id: seed.candidateId, experiment_id: seed.experimentId,
    dataset_version_id: seed.versionId, task_kind: 'external', status: 'draft' }]);
  expect(await probe.pg('SELECT id FROM model_hub_experimentrunitem WHERE run_id=$1 AND NOT deleted',
    [seed.candidateId])).toEqual([]);

  // ExperimentSummaryView + RunInCIPanel: no launch switch override.
  await page.goto(`/dashboard/develop/experiment/${seed.experimentId}/summary`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Run in CI', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Run in CI', exact: true });
  await expect(dialog).toBeVisible({ timeout: UI_READY });
  await dialog.getByLabel('Baseline run', { exact: true }).selectOption(seed.baselineId);
  await dialog.getByLabel('Allowed regressions', { exact: true }).fill('1');
  await dialog.getByLabel('Regressed rows to include', { exact: true }).fill('1');
  const snippet = expectedSnippet(seed.experimentId, seed.baselineId);
  const code = dialog.getByTestId('ci-comment-snippet').locator('pre code');
  // ScriptBlock drops only the final newline for display; the clipboard retains it.
  await expect(code).toHaveText(snippet.slice(0, -1), { timeout: UI_READY });
  await expect.poll(() => code.textContent(), { timeout: UI_READY }).toBe(snippet.slice(0, -1));
  await dialog.getByRole('button', { name: 'Copy GitHub Actions step', exact: true }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText()), { timeout: UI_READY }).toBe(snippet);
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();

  // RunInCIPanel explicitly supports custom uploads (lines 57–65). The generated
  // Your code runner instead names an unregistered nested /runs/{id}/results/
  // endpoint and {results, complete}; this flow does NOT claim it executes that runner.
  // Use the registered ExternalRunResultsRequestSerializer {items} contract, then
  // the same comment query/headers as the snippet, locally over HTTP if configured.
  // A standalone fetch has no browser cookies, bearer token or workspace headers.
  const headers = { 'X-Api-Key': actor.apiKey, 'X-Secret-Key': actor.secretKey };
  const upload = await fetch(`${E2E.apiUrl}${RUNS}${seed.candidateId}/results/`, {
    method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ items: seed.candidateItems }),
    credentials: 'omit', redirect: 'error', signal: AbortSignal.timeout(CI_CALL),
  });
  expect(upload.status).toBe(200);
  await upload.arrayBuffer();
  const query = new URLSearchParams({ run_id: seed.candidateId, baseline_run_id: seed.baselineId,
    max_regressed: '1', top_n: '1' });
  const comment = await fetch(`${E2E.apiUrl}${EXPERIMENTS}${seed.experimentId}/ci-summary/comment/?${query}`, {
    headers, credentials: 'omit', redirect: 'error', signal: AbortSignal.timeout(CI_CALL),
  });
  expect(comment.status).toBe(200);
  const result = await comment.json() as { body: string; passed: boolean; results_path: string };
  // experiment_ci_comment.py: tie-break equal deltas by example UUID, then metric UUID.
  const metricId = seed.metrics.map(m => m.metric_id).sort()[0];
  const resultsPath = `/dashboard/develop/experiment/${seed.experimentId}/data`;
  expect(result).toEqual({
    body: [
      '## Evaluation CI: FAIL',
      'Regressed comparisons: **2** (allowed: **1**).',
      `Run: \`${seed.candidateId}\``,
      `Baseline: \`${seed.baselineId}\``,
      '',
      'Improved: **2** | Regressed: **2** | Unchanged: **2** | Not comparable: **2**',
      'Counts are row/evaluator comparisons, not unique rows.',
      '',
      '### Regressed rows (showing 1 of 2)',
      '| Row ID | Evaluator ID | Baseline | Run |',
      '| --- | --- | ---: | ---: |',
      `| \`${seed.examples[1]}\` | \`${metricId}\` | 1 | 0 |`,
      '',
      'Sorted by absolute score change, then row ID and evaluator ID.',
      'Scores retain comparison precision; categorical scores are pass rates.',
      '',
      `[View experiment Results](${resultsPath})`,
      '',
    ].join('\n'),
    passed: false, results_path: resultsPath,
  });

  // Header and item sets are read together under one primary visibility budget.
  const expectedItems = seed.candidateItems.map(({ example_id, trial_number, status, output, score_refs }) =>
    ({ example_id, trial_number, status, output, score_refs })).sort((a, b) => a.example_id.localeCompare(b.example_id));
  await expect.poll(async () => ({ runs: await runState(), items: await probe.pg(
    `SELECT example_id, trial_number, status, output, score_refs FROM model_hub_experimentrunitem
     WHERE run_id=$1 AND NOT deleted ORDER BY example_id`, [seed.candidateId]) }),
  { timeout: PRIMARY_VISIBLE, intervals: [500, 1_000, 2_000] }).toEqual({
    runs: [{ id: seed.candidateId, experiment_id: seed.experimentId,
      dataset_version_id: seed.versionId, task_kind: 'external', status: 'completed' }], items: expectedItems,
  });

  // Follow exactly the server link, without appending a candidate selector that
  // would conceal a broken default history selection. Newest compatible group
  // contains this candidate and baseline (ExperimentRunHistory.jsx).
  await page.goto(result.results_path, { waitUntil: 'domcontentloaded' });
  await expect(page).toHaveURL(new URL(resultsPath, E2E.appUrl).href, { timeout: UI_READY });
  // resultsColumns.js pins output|<run UUID>; OutputCell's ClampedText owns each
  // exact output, alongside a separate trace chip. Four cells plus four unique
  // exact texts proves the set without depending on the default ranking order.
  const cells = page.locator(`[data-results-grid-root] .ag-row [col-id="output|${seed.candidateId}"]`);
  await expect(cells).toHaveCount(4, { timeout: UI_READY });
  for (const { output } of seed.candidateItems) {
    const text = cells.getByText(output, { exact: true });
    await expect(text).toHaveText(output, { timeout: UI_READY });
    await expect(text).toBeVisible({ timeout: UI_READY });
  }
});
