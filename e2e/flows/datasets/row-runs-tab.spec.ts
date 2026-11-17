import { createHash, randomUUID } from 'node:crypto';
import type { Browser, BrowserContext, Page, Response as PwResponse } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import type { ScopeActor, ScopeActors } from '../../lib/scope-actors';
import type { StateProbe } from '../../lib/state-probe';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// The Row view's Runs tab (DatapointDrawerV2/RowRunsTab.jsx, RowRunsTable.jsx,
// RowFieldsTab.jsx) over DatasetRowRunsView (model_hub/views/row_runs.py,
// services/row_runs.py). V2 create starts workflows; the e2e stack supplies the mock upstream.
// Its inline prompts have no canonical run binding; the public external-run API
// supplies the recorded runs after all three experiments reach a terminal state.
const UI_READY = 60_000;
// Long enough for a page that will not show a row to settle before absence checks.
const SETTLE_MS = 15_000;
// ManualDatasetSchemaRequestSerializer: Column 1..n and n empty rows.
const DATASETS = '/model-hub/develops/create-dataset-manually/';
// futureagi/model_hub/serializers/contracts.py:2548-2593 defines a saved code evaluation.
const TEMPLATES = '/model-hub/eval-templates/create-v2/';
// DatasetUpdateCellValueRequestSerializer.
const cellPath = (dataset: string) => `/model-hub/develops/${dataset}/update_cell_value/`;
// ExtractJsonColumnRequestSerializer: an extracted_json column, a generated column of the row.
const extractPath = (dataset: string) => `/model-hub/develops/${dataset}/extract-json-column/`;
// ExternalMetricCreateRequestSerializer: a pass/fail metric the caller scores.
const metricsPath = (dataset: string) => `/model-hub/datasets/${dataset}/external-metrics/`;
// DatasetVersionCreateSerializer: each item names its row (legacy_row_id).
const versionsPath = (dataset: string) => `/model-hub/datasets/${dataset}/versions/`;
// futureagi/model_hub/services/experiment_service.py:16-21 names the v2 create route.
const EXPERIMENTS = '/model-hub/experiments/v2/';
// ExternalRunCreateRequestSerializer and ExternalRunResultsRequestSerializer.
const RUNS = '/model-hub/experiment-runs/';
const resultsPath = (run: string) => `/model-hub/experiment-runs/${run}/results/`;
// DatasetRowRunsView: GET only, scope and sort query.
const rowRunsPath = (dataset: string, row: string) => `/model-hub/datasets/${dataset}/rows/${row}/runs/`;
const catalogPath = (dataset: string) => `/model-hub/datasets/${dataset}/runs/`;
const CHECKING = 'Checking whether these runs can be compared…';
const CHECK_FAILED = "Couldn't check whether these runs can be compared.";
const UP_TO_FOUR = 'Side by side shows up to 4 runs. Untick one to pick another.';
const UNAVAILABLE = "Side-by-side compare needs a server read this release doesn't have yet.";
const UNRECORDED_ONE = "1 experiment on this dataset has no recorded runs, so its results aren't listed here.";
const NEVER_RUN = "This row hasn't been run in any experiment yet";

type RunEval = { value: number | null; n: number; status: string };
type RowRun = {
  run_id: string; example_id: string; label: string; experiment_id: string; experiment_name: string;
  status: string; output: unknown; evals: Record<string, RunEval>; dataset_version: { id: string };
};
type RowRuns = { status: boolean; result: {
  summary: { runs: number; experiments: number; primary_metric_name: string | null; primary_scored: number;
    primary_pass: number; classic_experiments: number };
  metrics: { key: string; name: string; output_type: string | null }[];
  runs: RowRun[];
} };
type Catalog = { status: boolean; result: { count: number; results: {
  run_id: string; experiment_id: string; dataset_version_id: string; comparison_group: string | null;
}[] } };
type Metric = { result: { metric_id: string; evaluator_version_id: string; name: string } };
type RunHeader = { result: { id: string; label: string; experiment_id: string; status: string;
  evaluator_bindings: { metric_id: string; evaluator_version_id: string }[] } };
type Seeded = {
  dataset: string; rows: string[]; examples: string[]; column: string; version: string;
  metric: { id: string; version: string; name: string };
  experiments: { alpha: string; beta: string; classic: string };
  names: { alpha: string; beta: string; classic: string };
  runs: { alpha: { id: string; label: string; output: string }; beta: { id: string; label: string; output: string } };
};

const sha256 = (value: string) => createHash('sha256').update(value).digest('hex');

async function seedTenantA(scopes: ScopeActors, probe: StateProbe, suffix: string): Promise<Seeded> {
  const a = scopes.ownerA;
  const created = await scopes.send<{ result: { dataset_id: string } }>(a, 'POST', DATASETS,
    { dataset_name: `e2e-data179-${suffix}`, number_of_rows: 2, number_of_columns: 1 });
  expect(created.status).toBe(200);
  const dataset = created.body.result.dataset_id;
  expect(await probe.pg<{ organization_id: string; workspace_id: string }>(
    'SELECT organization_id::text, workspace_id::text FROM model_hub_dataset WHERE id=$1::uuid AND NOT deleted',
    [dataset])).toEqual([{ organization_id: a.organizationId, workspace_id: a.workspaceId }]);
  const rows = (await probe.pg<{ id: string }>(
    'SELECT id::text AS id FROM model_hub_row WHERE dataset_id=$1::uuid AND NOT deleted ORDER BY "order", id',
    [dataset])).map(r => r.id);
  expect(rows).toHaveLength(2);
  const columns = (await probe.pg<{ id: string }>(
    'SELECT id::text AS id FROM model_hub_column WHERE dataset_id=$1::uuid AND NOT deleted', [dataset])).map(c => c.id);
  expect(columns).toHaveLength(1);
  const column = columns[0];
  for (const [i, row] of rows.entries()) {
    const written = await scopes.send(a, 'POST', cellPath(dataset), {
      row_id: row, column_id: column, new_value: JSON.stringify({ answer: `e2e-179-answer-${i}-${suffix}` }) });
    expect(written.status).toBe(200);
  }
  // A generated column, so the Row tab has its "Generated columns on this dataset" section.
  const extracted = await scopes.send(a, 'POST', extractPath(dataset),
    { column_id: column, json_key: 'answer', new_column_name: `Answer ${suffix}` });
  expect(extracted.status).toBe(200);

  // V2 requires a nonempty source snapshot, prompt config and evaluation list.
  const template = await scopes.send<{ result: { id: string } }>(a, 'POST', TEMPLATES, {
    name: `e2e-data179-seed-${suffix}`, eval_type: 'code', code_language: 'python', output_type: 'pass_fail',
    code: 'def evaluate(output, **kwargs):\n    return bool(output)\n', pass_threshold: 0.5,
    check_internet: false, error_localizer_enabled: false,
  });
  expect(template.status).toBe(200);
  const names = { alpha: `e2e-data179-alpha-${suffix}`, beta: `e2e-data179-beta-${suffix}`,
    classic: `e2e-data179-classic-${suffix}` };
  const experiments = { alpha: '', beta: '', classic: '' };
  for (const key of ['alpha', 'beta', 'classic'] as const) {
    // Inline TTS entries execute, but have no prompt version for the canonical
    // run binding in services/experiment_submit.py. This retains the experiment
    // with no recorded runs without pretending the v2 create is save-only.
    const made = await scopes.send(a, 'POST', EXPERIMENTS, {
      name: names[key], dataset_id: dataset, column_id: column, experiment_type: 'tts',
      prompt_config: [{ name: names[key], model: 'tts-1',
        messages: [{ role: 'user', content: [{ type: 'text', text: 'Read the input.' }] }] }],
      user_eval_metrics: [{ template_id: template.body.result.id, name: `seed-${key}-${suffix}`,
        config: { mapping: { output: column } } }],
    });
    expect(made.status, `experiment ${key}`).toBe(200);
    const found = await probe.pg<{ id: string }>(
      'SELECT id::text AS id FROM model_hub_experimentstable WHERE dataset_id=$1::uuid AND name=$2 AND NOT deleted',
      [dataset, names[key]]);
    expect(found).toHaveLength(1);
    experiments[key] = found[0].id;
    const snapshots = await probe.pg<{ id: string }>(
      'SELECT snapshot_dataset_id::text AS id FROM model_hub_experimentstable WHERE id=$1::uuid', [found[0].id]);
    expect(snapshots).toHaveLength(1);
    expect(snapshots[0].id).toBeTruthy();
    expect(await probe.pg('SELECT id FROM model_hub_row WHERE dataset_id=$1::uuid AND NOT deleted', [snapshots[0].id]))
      .toHaveLength(2);
    expect(await probe.pg<{ prompt_version_id: string | null }>(
      `SELECT p.prompt_version_id::text FROM model_hub_experimentpromptconfig p
       JOIN model_hub_experimentdatasettable t ON t.id=p.experiment_dataset_id
       WHERE t.experiment_id=$1::uuid AND NOT t.deleted AND NOT p.deleted`, [found[0].id]))
      .toEqual([{ prompt_version_id: null }]);
    // Terminal execution, not model success, is the prerequisite for this read-only walk.
    await expect.poll(async () => {
      const [state] = await probe.pg<{ status: string }>(
        'SELECT status FROM model_hub_experimentstable WHERE id=$1::uuid AND NOT deleted', [found[0].id]);
      return state?.status;
    }, POLL.ASYNC_JOB).toMatch(/^(Completed|Failed|Error|Cancelled)$/);
  }
  expect(await probe.pg('SELECT id FROM model_hub_experimentrun WHERE experiment_id=ANY($1::uuid[])',
    [Object.values(experiments)])).toEqual([]);

  const metricName = `e2e-179-correct-${suffix}`;
  const metric = await scopes.send<Metric>(a, 'POST', metricsPath(dataset), {
    name: metricName, output_type: 'pass_fail', higher_is_better: true,
    source_hash: sha256(`e2e-179-scorer-${suffix}`) });
  expect(metric.status).toBe(200);
  expect(metric.body.result.name).toBe(metricName);

  // One example per row, linked to its row: the row's runs are found through it.
  const examples = rows.map(() => randomUUID());
  const version = await scopes.send<{ result: { id: string } }>(a, 'POST', versionsPath(dataset), {
    description: `e2e-179-${suffix}`,
    items: rows.map((row, i) => ({ input: { question: `e2e-179-question-${i}-${suffix}` },
      expected: { answer: `e2e-179-answer-${i}-${suffix}` }, legacy_row_id: row, example_id: examples[i] })),
  });
  expect(version.status).toBe(201);

  // Two recorded runs that try the row, in two experiments: the older fails, the newer passes.
  const runs = { alpha: { id: '', label: '', output: '' }, beta: { id: '', label: '', output: '' } };
  for (const [key, value] of [['alpha', 0], ['beta', 1]] as const) {
    const runId = randomUUID();
    const header = await scopes.send<RunHeader>(a, 'POST', RUNS, {
      run_id: runId, dataset_version_id: version.body.result.id, experiment_id: experiments[key],
      evaluator_metric_ids: [metric.body.result.metric_id],
      expected_items: examples.map(example_id => ({ example_id, trial_number: 1 })),
    });
    expect(header.status, `run ${key}`).toBe(200);
    expect(header.body.result.id).toBe(runId);
    expect(header.body.result.experiment_id).toBe(experiments[key]);
    const binding = header.body.result.evaluator_bindings.find(b => b.metric_id === metric.body.result.metric_id);
    expect(binding?.evaluator_version_id).toBe(metric.body.result.evaluator_version_id);
    const output = `e2e-179-${key}-output-${suffix}`;
    const ingested = await scopes.send(a, 'POST', resultsPath(runId), {
      items: examples.map((example_id, i) => ({
        example_id, trial_number: 1, receipt_key: `e2e-179-${key}-${i}-${suffix}`, status: 'completed',
        output: { text: i === 0 ? output : `${output}-other` }, latency_ms: 120, cost: 0.01,
        score_refs: [{ metric_id: metric.body.result.metric_id, evaluator_version_id: metric.body.result.evaluator_version_id,
          status: 'assessed', value: i === 0 ? value : 1 }],
      })),
    });
    expect(ingested.status, `results ${key}`).toBe(200);
    runs[key] = { id: runId, label: header.body.result.label, output };
  }
  return { dataset, rows, examples, column, version: version.body.result.id,
    metric: { id: metric.body.result.metric_id, version: metric.body.result.evaluator_version_id, name: metricName },
    experiments, names, runs };
}

// What the seed wrote, read back from Postgres; compared again after the walk (no writes).
async function stored(probe: StateProbe, seed: Seeded) {
  const experiments = await probe.pg<{ id: string; name: string; recorded: boolean }>(
    `SELECT e.id::text AS id, e.name,
       EXISTS (SELECT 1 FROM model_hub_experimentrun r WHERE r.experiment_id = e.id) AS recorded
     FROM model_hub_experimentstable e WHERE e.dataset_id=$1::uuid AND NOT e.deleted ORDER BY e.name`, [seed.dataset]);
  const runs = await probe.pg<{ id: string; experiment_id: string; task_kind: string; status: string }>(
    `SELECT r.id::text AS id, r.experiment_id::text AS experiment_id, r.task_kind, r.status
     FROM model_hub_experimentrun r JOIN model_hub_dataset_version v ON v.id = r.dataset_version_id
     WHERE v.dataset_id=$1::uuid AND NOT r.deleted ORDER BY r.ordinal`, [seed.dataset]);
  const items = await probe.pg<{ run_id: string; example_id: string; status: string; output: unknown; score_refs: unknown }>(
    `SELECT i.run_id::text AS run_id, i.example_id::text AS example_id, i.status, i.output, i.score_refs
     FROM model_hub_experimentrunitem i WHERE i.run_id = ANY($1::uuid[]) AND NOT i.deleted
     ORDER BY i.run_id, i.example_id`, [[seed.runs.alpha.id, seed.runs.beta.id]]);
  const links = await probe.pg<{ legacy_row_id: string; example_id: string }>(
    `SELECT legacy_row_id::text AS legacy_row_id, example_id::text AS example_id
     FROM model_hub_dataset_version_item WHERE dataset_version_id=$1::uuid ORDER BY legacy_row_id`, [seed.version]);
  return { experiments, runs, items, links };
}

type Opened = { context: BrowserContext; page: Page; reads: URL[]; answers: PwResponse[] };

async function openRowView(scopes: ScopeActors, browser: Browser, actor: ScopeActor, dataset: string, row: string,
  query = '', waitForRow = true, beforeNavigate?: (page: Page) => Promise<void>): Promise<Opened> {
  const context = await scopes.openContext(browser, actor);
  // Runtime traffic stays on fixture loopback. External requests are blocked, never mocked.
  await context.route('**/*', route => ['localhost', '127.0.0.1'].includes(new URL(route.request().url()).hostname)
    ? route.continue() : route.abort('blockedbyclient'));
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  // Every read of this row's runs the page sends, and every answer it gets.
  const reads: URL[] = [];
  const answers: PwResponse[] = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (request.method() === 'GET' && url.pathname === rowRunsPath(dataset, row)) reads.push(url);
  });
  page.on('response', response => {
    if (new URL(response.url()).pathname === rowRunsPath(dataset, row)) answers.push(response);
  });
  await beforeNavigate?.(page);
  const loaded = waitForRow ? page.waitForResponse(r => new URL(r.url()).pathname ===
    `/model-hub/develops/${dataset}/get-row-data/` && r.request().method() === 'POST', { timeout: UI_READY }) : null;
  await page.goto(`/dashboard/develop/${dataset}?row=${row}${query}`, { waitUntil: 'domcontentloaded' });
  if (loaded) expect((await loaded).status()).toBe(200);
  return { context, page, reads, answers };
}

const runsTab = (page: Page) => page.getByTestId('row-tab-runs');
const runOrder = (page: Page) =>
  page.locator('[data-run-row]').evaluateAll(rows => rows.map(r => r.getAttribute('data-run-row')));

async function expectRunsTabOpen(page: Page) {
  await expect(runsTab(page)).toHaveAttribute('aria-selected', 'true');
  await expect.poll(() => new URL(page.url()).searchParams.get('rowTab')).toBe('runs');
  await expect(page.getByTestId('row-runs-summary')).toBeVisible();
}

// The link a run opens: the run's experiment Data page, at the row's example, in the run's history group.
async function expectRunPage(page: Page, open: () => Promise<void>, run: RowRun, trajectory: boolean) {
  const target = `/dashboard/develop/experiment/${run.experiment_id}/data`;
  // The URL as the link opened it, before anything on that page can change it.
  const seen: { url?: URL } = {};
  const opened = page.waitForURL(url => {
    if (url.pathname !== target) return false;
    seen.url = new URL(url.href);
    return true;
  }, { timeout: UI_READY });
  await open();
  await opened;
  const params = seen.url?.searchParams;
  expect(params?.get('row')).toBe(run.example_id);
  expect(params?.get('run')).toBe(run.run_id);
  expect(params?.get('history')).toBe(run.run_id);
  expect(params?.get('dtab') ?? null).toBe(trajectory ? 'trajectory' : null);
  return seen.url?.href ?? '';
}

// Cross-experiment comparisons use the dataset catalog, not experiment identity.
// Capture the entry URL before Compare Data adds its default candidate to it.
async function expectComparePage(page: Page, open: () => Promise<void>, seed: Seeded, ordered: RowRun[]) {
  const target = `/dashboard/develop/${seed.dataset}/compare/data`;
  const query = new URLSearchParams({ runs: ordered.map(r => r.run_id).join(','),
    baseline: ordered[0].run_id, row: seed.examples[0] });
  const seen: { url?: URL } = {};
  const opened = page.waitForURL(url => {
    if (url.pathname !== target) return false;
    seen.url = new URL(url.href);
    return true;
  }, { timeout: UI_READY });
  await open();
  await opened;
  expect(`${seen.url?.pathname}${seen.url?.search}`).toBe(`${target}?${query}`);
  await expect(page.getByText('Compare runs', { exact: true })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Data', exact: true })).toHaveAttribute('aria-selected', 'true');
  const drawer = page.getByTestId('result-drawer-overlay');
  await expect(drawer).toBeVisible();
  await expect(drawer.getByTestId('row-tab-compare')).toHaveAttribute('aria-selected', 'true');
  const scores = drawer.getByTestId('drawer-run-score');
  await expect(scores).toHaveCount(2, { timeout: UI_READY });
  await expect.poll(() => scores.locator('[data-run-id]').evaluateAll(chips =>
    chips.map(chip => chip.getAttribute('data-run-id')))).toEqual(ordered.map(r => r.run_id));
  for (const run of ordered) {
    const score = scores.filter({ has: page.locator(`[data-run-id="${run.run_id}"]`) });
    await expect(score.getByText(run.label, { exact: true })).toBeVisible();
    await expect(score.getByText(run.run_id === seed.runs.alpha.id ? 'Fail' : 'Pass', { exact: true })).toBeVisible();
    await expect(score.getByTestId('run-role-badge')).toHaveCount(run === ordered[0] ? 1 : 0);
    if (run === ordered[0]) await expect(score.getByTestId('run-role-badge')).toHaveText('Baseline');
    await expect(drawer.getByText((run.output as { text: string }).text, { exact: true })).toBeVisible();
  }
  // The route may add cand, but it must retain the selected population and example.
  const params = new URL(page.url()).searchParams;
  expect([params.get('runs'), params.get('baseline'), params.get('row'), params.get('history')])
    .toEqual([query.get('runs'), ordered[0].run_id, seed.examples[0], null]);
}

async function expectComparisonBlocked(page: Page, picks: RowRun[], notice: string) {
  const atRow = page.url();
  for (const run of picks)
    await page.getByRole('checkbox', { name: `Select ${run.label} for side by side`, exact: true }).check();
  const compare = page.getByTestId('row-runs-side-by-side');
  await expect(compare).toHaveText('Compare side by side (2)');
  await expect(compare).toBeDisabled();
  await expect(compare).toHaveAccessibleDescription(notice);
  await expect(page.getByText(notice, { exact: true })).toBeVisible();
  await expect(page.getByText(UNAVAILABLE, { exact: true })).toHaveCount(0);
  await expect(page.getByText(UP_TO_FOUR, { exact: true })).toHaveCount(0);
  await page.getByTestId(`row-run-menu-${picks[0].label}`).click();
  const sideBySide = page.getByRole('menuitem', { name: 'Side by side with the newest run', exact: true });
  await expect(sideBySide).toHaveAttribute('aria-disabled', 'true');
  await expect(sideBySide).toHaveAccessibleDescription(notice);
  await expect(sideBySide.getByText(notice, { exact: true })).toBeVisible();
  await expect(page.getByRole('menuitem', { name: 'Open this result in the experiment', exact: true })).toBeVisible();
  await expect(page.getByRole('menuitem', { name: 'Copy run id', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  expect(page.url()).toBe(atRow);
}

test('DATA-E2E-179: the row panel\'s Runs tab across two organizations', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-179', area: 'datasets',
    userGoal: 'See every recorded run that tried a dataset row, compare compatible runs across experiments, open a run where it ran, and never see another organization\'s runs',
    steps: [
      'in the first organization, seed a two-row dataset with a generated column, a pass/fail external metric, a version that links each example to its row, three experiments and two recorded runs in two of them that try the row',
      'open the row in Row view and open its Runs tab by clicking it, by its number key, by a rowTab=runs link and from the Row tab\'s Also ran in 2 experiment runs link',
      'read each run\'s experiment, output and verdict and the summary, then change Show and Sort',
      'open a run\'s result from its menu and its trajectory from its trace chip',
      'compare compatible cross-experiment runs from the selected-runs button and a run menu in dataset Compare Data',
      'hold and abort the dataset catalog read separately; both comparison doors stay disabled with the exact reason',
      'in the second organization, read the first organization\'s row runs, open its row, then open a row of its own',
      'after the read-only walk, record a subset-plan run and verify that a pair in different comparison groups stays disabled',
    ],
    backendChecks: [
      'the dataset has three experiments; the two with recorded runs each hold one completed external run whose item for the row stores its output and pass/fail score, and the third has none',
      'GET rows/{row_id}/runs/ lists exactly the two runs, newest first, with experiment name, output and verdict; its summary counts 2 runs, 2 experiments, 1 of 2 passing and 1 experiment with no recorded runs',
      'the page reads it once when the row opens with the Runs tab; Sort Worst first reads it with sort=worst and lists the failing run first',
      'the dataset catalog puts the original two runs in one comparison group; a later subset-plan run has a different group despite the same dataset version and evaluator',
      'the second organization gets 404 for that read with no run id, example id, experiment name or output of the first, and no request writes a run, an item or an experiment',
    ],
    browserChecks: [
      'the Runs tab shows its count of 2, opens by click, key, link and the Row tab\'s link, and Show and Sort set rshow and rsort',
      'result and trajectory links open the run\'s experiment Data page with row set to the row\'s example and run and history set to that run',
      'compatible cross-experiment runs open dataset Compare Data with exactly runs, baseline and row; its Data tab shows both run verdicts, the baseline badge and the row outputs',
      'differing comparison groups, a pending catalog read and a failed catalog read each disable the selected-runs button and the run menu item with their own exact reason; no up-to-four line shows for two picks',
      'the second organization\'s pages show no run of the first; its own row says it has not been run in any experiment',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Provisioning + seeding + four page loads x UI_READY + two settles + headroom.
  test.setTimeout(900_000);
  const a = scopes.ownerA;
  const b = scopes.ownerB;
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const seed = await seedTenantA(scopes, probe, suffix);
  const [row] = seed.rows;
  const path = rowRunsPath(seed.dataset, row);
  await testInfo.attach('row-runs-identities', { contentType: 'application/json', body: JSON.stringify({
    ...seed, organizationId: a.organizationId, workspaceId: a.workspaceId, foreignOrganizationId: b.organizationId }) });

  // 1. Backend state: three experiments, two recorded runs, one classic.
  const before = await stored(probe, seed);
  expect(before.experiments).toEqual([
    { id: seed.experiments.alpha, name: seed.names.alpha, recorded: true },
    { id: seed.experiments.beta, name: seed.names.beta, recorded: true },
    { id: seed.experiments.classic, name: seed.names.classic, recorded: false },
  ]);
  expect(before.runs).toEqual([
    { id: seed.runs.alpha.id, experiment_id: seed.experiments.alpha, task_kind: 'external', status: 'completed' },
    { id: seed.runs.beta.id, experiment_id: seed.experiments.beta, task_kind: 'external', status: 'completed' },
  ]);
  // Compared in one code-point order, whatever the database collation.
  const byRow = (x: { legacy_row_id: string }, y: { legacy_row_id: string }) =>
    x.legacy_row_id < y.legacy_row_id ? -1 : x.legacy_row_id > y.legacy_row_id ? 1 : 0;
  expect([...before.links].sort(byRow)).toEqual(
    seed.rows.map((r, i) => ({ legacy_row_id: r, example_id: seed.examples[i] })).sort(byRow));
  for (const [key, value] of [['alpha', 0], ['beta', 1]] as const) {
    const item = before.items.find(i => i.run_id === seed.runs[key].id && i.example_id === seed.examples[0]);
    expect(item?.status).toBe('completed');
    expect(item?.output).toEqual({ text: seed.runs[key].output });
    expect(item?.score_refs).toEqual([expect.objectContaining({ metric_id: seed.metric.id,
      evaluator_version_id: seed.metric.version, status: 'assessed', value })]);
  }

  // 3. The owner's read: each run with its experiment, output and verdict; the classic experiment counted.
  const read = await scopes.send<RowRuns>(a, 'GET', path);
  expect(read.status).toBe(200);
  expect(read.body.status).toBe(true);
  const { summary, metrics, runs } = read.body.result;
  expect(summary).toEqual(expect.objectContaining({ runs: 2, experiments: 2, primary_metric_name: seed.metric.name,
    primary_scored: 2, primary_pass: 1, classic_experiments: 1 }));
  expect(metrics.map(m => [m.key, m.name, m.output_type])).toEqual([[seed.metric.id, seed.metric.name, 'pass_fail']]);
  expect(runs.map(r => r.run_id)).toEqual([seed.runs.beta.id, seed.runs.alpha.id]);
  const byId = Object.fromEntries(runs.map(r => [r.run_id, r]));
  const alpha = byId[seed.runs.alpha.id];
  const beta = byId[seed.runs.beta.id];
  for (const [run, key, value] of [[alpha, 'alpha', 0], [beta, 'beta', 1]] as const) {
    expect(run.label).toBe(seed.runs[key].label);
    expect(run.example_id).toBe(seed.examples[0]);
    expect(run.experiment_id).toBe(seed.experiments[key]);
    expect(run.experiment_name).toBe(seed.names[key]);
    expect(run.status).toBe('completed');
    expect(run.output).toEqual({ text: seed.runs[key].output });
    expect(run.evals[seed.metric.id]).toEqual(expect.objectContaining({ value, n: 1, status: 'assessed' }));
    expect(run.dataset_version.id).toBe(seed.version);
  }
  expect(JSON.stringify(read.body)).not.toContain(seed.experiments.classic);
  expect(JSON.stringify(read.body)).not.toContain(seed.names.classic);
  const worst = await scopes.send<RowRuns>(a, 'GET', path, undefined, { sort: 'worst' });
  expect(worst.status).toBe(200);
  expect(worst.body.result.runs.map(r => r.run_id)).toEqual([alpha.run_id, beta.run_id]);
  // Results compares one immutable version, plan and evaluator binding, even across experiments.
  const catalog = await scopes.send<Catalog>(a, 'GET', catalogPath(seed.dataset));
  expect(catalog.status).toBe(200);
  expect(catalog.body.status).toBe(true);
  expect(catalog.body.result.count).toBe(2);
  const catalogRuns = catalog.body.result.results;
  expect(catalogRuns.map(r => [r.run_id, r.experiment_id, r.dataset_version_id])).toEqual([
    [beta.run_id, beta.experiment_id, seed.version], [alpha.run_id, alpha.experiment_id, seed.version],
  ]);
  const group = catalogRuns[0].comparison_group;
  expect(group).toMatch(/^[a-f0-9]{64}$/);
  expect(catalogRuns.map(r => r.comparison_group)).toEqual([group, group]);
  const privateValues = [alpha.run_id, beta.run_id, ...seed.examples, ...Object.values(seed.names),
    seed.runs.alpha.output, seed.runs.beta.output, ...Object.values(seed.experiments)];

  await test.step('2-3, 6. the Runs tab opens by click with its count and one read, and from the Row tab', async () => {
    const { context, page, reads } = await openRowView(scopes, browser, a, seed.dataset, row);
    await expect(runsTab(page)).toBeVisible();
    await expect(runsTab(page)).toBeEnabled();
    await runsTab(page).click();
    await expectRunsTabOpen(page);
    await expect(page.getByTestId('row-runs-summary')).toHaveText(
      `Ran in 2 runs · 2 experiments · Passes ${seed.metric.name} in 1/2 · 1 failing`);
    await expect(page.getByTestId('row-runs-unrecorded')).toHaveText(UNRECORDED_ONE);
    await expect.poll(() => runOrder(page)).toEqual([beta.run_id, alpha.run_id]);
    for (const [run, verdict] of [[alpha, 'Fail'], [beta, 'Pass']] as const) {
      const listed = page.locator(`[data-run-row="${run.run_id}"]`);
      await expect(listed).toContainText(run.experiment_name);
      await expect(listed).toContainText(String((run.output as { text: string }).text));
      await expect(listed).toContainText(verdict);
    }
    await expect(page.locator(`text=${seed.names.classic}`)).toHaveCount(0);
    // One read of the row's runs, with the tab's default Show and Sort.
    expect(reads.map(u => [u.searchParams.get('scope'), u.searchParams.get('sort')])).toEqual([['all', 'newest']]);
    // The tab's count is the number of recorded runs that tried the row, from the row's runs
    // summary. Checked after the one-read check so the count cannot delay the click past the
    // 5 s cache window.
    await expect(runsTab(page)).toHaveText(/^Runs\s*2$/);

    // 6. The Row tab's link, counted from the same runs summary, opens the Runs tab.
    await page.getByTestId('row-tab-row').click();
    await expect(page.getByTestId('row-tab-row')).toHaveAttribute('aria-selected', 'true');
    await expect.soft(page.getByTestId('row-section-outputs')).toBeVisible();
    const alsoRan = page.getByRole('button', { name: /^Also ran in 2 experiment runs → Runs$/ });
    await expect(alsoRan).toBeVisible();
    await alsoRan.click();
    await expectRunsTabOpen(page);
    await context.close();
  });

  await test.step('2. the Runs tab opens by its number key and by a rowTab=runs link', async () => {
    const { context, page } = await openRowView(scopes, browser, a, seed.dataset, row);
    await expect(page.getByTestId('row-tab-row')).toHaveAttribute('aria-selected', 'true');
    // `1`-`4` open the first four tabs that open, in screen order.
    const open = await page.locator('[data-testid^="row-tab-"]:not([disabled])')
      .evaluateAll(tabs => tabs.map(t => t.getAttribute('data-testid')));
    const index = open.indexOf('row-tab-runs');
    expect(index).toBeGreaterThanOrEqual(0);
    expect(index).toBeLessThan(4);
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
    await page.keyboard.press(String(index + 1));
    await expectRunsTabOpen(page);
    await context.close();

    const linked = await openRowView(scopes, browser, a, seed.dataset, row, '&rowTab=runs');
    await expectRunsTabOpen(linked.page);
    await expect.poll(() => runOrder(linked.page)).toEqual([beta.run_id, alpha.run_id]);
    expect(linked.reads.map(u => [u.searchParams.get('scope'), u.searchParams.get('sort')]))
      .toEqual([['all', 'newest']]);
    await linked.context.close();
  });

  await test.step('4, 7. Show and Sort change the list; compatible runs open dataset Compare Data', async () => {
    const { context, page } = await openRowView(scopes, browser, a, seed.dataset, row, '&rowTab=runs');
    await expectRunsTabOpen(page);
    await expect.poll(() => runOrder(page)).toEqual([beta.run_id, alpha.run_id]);

    // 7. Different experiments are allowed: the real dataset catalog puts these runs in one group.
    const compare = page.getByTestId('row-runs-side-by-side');
    await expect(compare).toBeVisible();
    await expect(compare).toBeEnabled();
    await expect(page.getByText(UNAVAILABLE, { exact: true })).toHaveCount(0);
    await expect(page.getByText(CHECKING, { exact: true })).toHaveCount(0);
    for (const run of [alpha, beta])
      await expect(page.getByRole('checkbox', { name: `Select ${run.label} for side by side` })).toBeVisible();
    await expect(page.getByText(UP_TO_FOUR)).toHaveCount(0);

    // 4. Sort: Worst first reads with sort=worst; the failing run comes first.
    const sorted = page.waitForRequest(r => {
      const url = new URL(r.url());
      return url.pathname === path && url.searchParams.get('sort') === 'worst';
    });
    await page.getByRole('button', { name: /^Sort:\s*Newest$/ }).click();
    await page.getByRole('menuitem', { name: 'Worst first', exact: true }).click();
    await sorted;
    await expect.poll(() => new URL(page.url()).searchParams.get('rsort')).toBe('worst');
    await expect.poll(() => runOrder(page)).toEqual([alpha.run_id, beta.run_id]);

    // Show: the failing, then the passing run only.
    await page.getByRole('button', { name: /^Show:\s*All runs$/ }).click();
    await page.getByRole('menuitem', { name: `Failing ${seed.metric.name}`, exact: true }).click();
    await expect.poll(() => new URL(page.url()).searchParams.get('rshow')).toBe('fail');
    await expect.poll(() => runOrder(page)).toEqual([alpha.run_id]);
    await page.getByRole('button', { name: /^Show:/ }).click();
    await page.getByRole('menuitem', { name: `Passing ${seed.metric.name}`, exact: true }).click();
    await expect.poll(() => new URL(page.url()).searchParams.get('rshow')).toBe('pass');
    await expect.poll(() => runOrder(page)).toEqual([beta.run_id]);
    await page.getByRole('button', { name: /^Show:/ }).click();
    await page.getByRole('menuitem', { name: 'All runs', exact: true }).click();
    await expect.poll(() => new URL(page.url()).searchParams.get('rshow') ?? 'all').toBe('all');
    await expect.poll(() => runOrder(page)).toEqual([alpha.run_id, beta.run_id]);

    // With no pinned baseline, the first pick is the baseline, even after sorting alpha first.
    for (const run of [beta, alpha])
      await page.getByRole('checkbox', { name: `Select ${run.label} for side by side`, exact: true }).check();
    await expect(compare).toHaveText('Compare side by side (2)');
    await expect(compare).toBeEnabled();
    await expect(page.getByText(UP_TO_FOUR)).toHaveCount(0);
    await expectComparePage(page, () => compare.click(), seed, [beta, alpha]);

    await page.goto(`/dashboard/develop/${seed.dataset}?row=${row}&rowTab=runs`, { waitUntil: 'domcontentloaded' });
    await expectRunsTabOpen(page);
    await expect(page.getByTestId('row-runs-side-by-side')).toBeEnabled();
    // The menu anchors on alpha instead, preserving its other actions.
    await page.getByTestId(`row-run-menu-${alpha.label}`).click();
    await expect(page.getByRole('menuitem', { name: 'Open this result in the experiment', exact: true })).toBeVisible();
    await expect(page.getByRole('menuitem', { name: 'Copy run id', exact: true })).toBeVisible();
    const sideBySide = page.getByRole('menuitem', { name: 'Side by side with the newest run', exact: true });
    await expect(sideBySide).toBeVisible();
    await expect(sideBySide).toBeEnabled();
    await expectComparePage(page, () => sideBySide.click(), seed, [alpha, beta]);
    await context.close();
  });

  await test.step('7. a pending dataset catalog read keeps both comparison doors disabled', async () => {
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    let catalogRequested = false;
    const { context, page } = await openRowView(scopes, browser, a, seed.dataset, row, '&rowTab=runs', true,
      async page => {
        // Hold only this transport; when released it reaches the real API unchanged.
        await page.route(url => url.pathname === catalogPath(seed.dataset), async route => {
          catalogRequested = true;
          await pending;
          await route.fallback();
        });
      });
    try {
      await expectRunsTabOpen(page);
      await expect.poll(() => catalogRequested).toBe(true);
      await expectComparisonBlocked(page, [alpha, beta], CHECKING);
      const answered = page.waitForResponse(r => new URL(r.url()).pathname === catalogPath(seed.dataset));
      release();
      expect((await answered).status()).toBe(200);
      await expect(page.getByTestId('row-runs-side-by-side')).toBeEnabled();
      await expect(page.getByText(CHECKING, { exact: true })).toHaveCount(0);
    } finally {
      release();
      await context.close();
    }
  });

  await test.step('7. a failed dataset catalog read keeps both comparison doors disabled', async () => {
    const { context, page } = await openRowView(scopes, browser, a, seed.dataset, row, '&rowTab=runs', true,
      async page => {
        // Inject a transport failure, not a fabricated catalog or results response.
        await page.route(url => url.pathname === catalogPath(seed.dataset), route => route.abort('failed'));
      });
    try {
      await expectRunsTabOpen(page);
      await expect(page.getByTestId('row-runs-side-by-side')).toHaveAccessibleDescription(CHECK_FAILED, { timeout: UI_READY });
      await expectComparisonBlocked(page, [alpha, beta], CHECK_FAILED);
      await expect(page.getByRole('button', { name: 'Try again', exact: true })).toBeVisible();
      // The server still has the compatible group; only this browser's catalog read failed.
      const healthy = await scopes.send<Catalog>(a, 'GET', catalogPath(seed.dataset));
      expect(healthy.status).toBe(200);
      expect(healthy.body.result.results.map(r => r.comparison_group)).toEqual([group, group]);
    } finally { await context.close(); }
  });

  await test.step('5. a run\'s result and trajectory links open its Data page at the row, in its history group', async () => {
    const { context, page } = await openRowView(scopes, browser, a, seed.dataset, row, '&rowTab=runs');
    await expectRunsTabOpen(page);
    const result = await expectRunPage(page, async () => {
      await page.getByTestId(`row-run-menu-${alpha.label}`).click();
      await page.getByRole('menuitem', { name: 'Open this result in the experiment', exact: true }).click();
    }, alpha, false);
    await page.goto(`/dashboard/develop/${seed.dataset}?row=${row}&rowTab=runs`, { waitUntil: 'domcontentloaded' });
    await expectRunsTabOpen(page);
    // An external run has no trace: its chip still opens the run's trajectory at the row.
    const trajectory = await expectRunPage(page, () => page.locator(`[data-run-row="${beta.run_id}"]`)
      .getByRole('button', { name: 'No trace', exact: true }).click(), beta, true);
    await testInfo.attach('row-runs-links', { contentType: 'application/json', body: JSON.stringify({ result, trajectory }) });
    await context.close();
  });

  await test.step('8. the second organization reads no run of the first', async () => {
    const foreign = await scopes.send<unknown>(b, 'GET', path);
    expect(foreign.status).toBe(404);
    for (const value of privateValues) expect(JSON.stringify(foreign.body)).not.toContain(value);
    // The owner still reads it: the refusal is scoping, not a missing row.
    expect((await scopes.send<RowRuns>(a, 'GET', path)).status).toBe(200);

    // B's browser on A's row: no run of A shows, and no runs read answers with them.
    const opened = await openRowView(scopes, browser, b, seed.dataset, row, '&rowTab=runs', false);
    await opened.page.waitForTimeout(SETTLE_MS);
    await expect(opened.page.locator('[data-run-row]')).toHaveCount(0);
    const shown = await opened.page.locator('body').innerText();
    for (const value of privateValues) expect(shown).not.toContain(value);
    for (const answer of opened.answers) {
      expect(answer.status()).not.toBe(200);
      const body = await answer.text().catch(() => '');
      for (const value of privateValues) expect(body).not.toContain(value);
    }
    await testInfo.attach('foreign-row-runs-answers', { contentType: 'application/json',
      body: JSON.stringify(opened.answers.map(r => r.status())) });
    await opened.context.close();

    // B's own row: its Runs tab lists nothing and says the row has not run.
    const own = await scopes.send<{ result: { dataset_id: string } }>(b, 'POST', DATASETS,
      { dataset_name: `e2e-data179-b-${suffix}`, number_of_rows: 1, number_of_columns: 1 });
    expect(own.status).toBe(200);
    const [ownRow] = await probe.pg<{ id: string }>(
      'SELECT id::text AS id FROM model_hub_row WHERE dataset_id=$1::uuid AND NOT deleted', [own.body.result.dataset_id]);
    const ownRead = await scopes.send<RowRuns>(b, 'GET', rowRunsPath(own.body.result.dataset_id, ownRow.id));
    expect(ownRead.status).toBe(200);
    expect(ownRead.body.result.runs).toEqual([]);
    expect(ownRead.body.result.summary).toEqual(expect.objectContaining({ runs: 0, experiments: 0, classic_experiments: 0 }));
    const mine = await openRowView(scopes, browser, b, own.body.result.dataset_id, ownRow.id, '&rowTab=runs');
    await expect(runsTab(mine.page)).toHaveAttribute('aria-selected', 'true');
    await expect(mine.page.getByText(NEVER_RUN, { exact: true })).toBeVisible();
    await expect(mine.page.locator('[data-run-row]')).toHaveCount(0);
    const mineShown = await mine.page.locator('body').innerText();
    for (const value of privateValues) expect(mineShown).not.toContain(value);
    await mine.context.close();
  });

  // No request of the walk wrote a run, an item or an experiment.
  expect(await stored(probe, seed)).toEqual(before);

  await test.step('7. different comparison groups keep both comparison doors disabled', async () => {
    // Same version and evaluator, but only one planned example: this is a real incompatible group.
    // Seed it after the original read-only checks so their two-run invariants remain exact.
    const subsetId = randomUUID();
    const made = await scopes.send<RunHeader>(a, 'POST', RUNS, {
      run_id: subsetId, dataset_version_id: seed.version, experiment_id: seed.experiments.beta,
      evaluator_metric_ids: [seed.metric.id],
      expected_items: [{ example_id: seed.examples[0], trial_number: 1 }],
    });
    expect(made.status).toBe(200);
    expect(made.body.result.id).toBe(subsetId);
    expect(made.body.result.evaluator_bindings).toEqual([
      expect.objectContaining({ metric_id: seed.metric.id, evaluator_version_id: seed.metric.version }),
    ]);
    const ingested = await scopes.send(a, 'POST', resultsPath(subsetId), { items: [{
      example_id: seed.examples[0], trial_number: 1, receipt_key: `e2e-179-subset-${suffix}`,
      status: 'completed', output: { text: `e2e-179-subset-output-${suffix}` },
      score_refs: [{ metric_id: seed.metric.id, evaluator_version_id: seed.metric.version, status: 'assessed', value: 1 }],
    }] });
    expect(ingested.status).toBe(200);
    const changed = await scopes.send<Catalog>(a, 'GET', catalogPath(seed.dataset));
    expect(changed.status).toBe(200);
    expect(changed.body.result.count).toBe(3);
    const catalogRuns = changed.body.result.results;
    expect(catalogRuns.map(r => [r.run_id, r.experiment_id, r.dataset_version_id])).toEqual([
      [subsetId, beta.experiment_id, seed.version], [beta.run_id, beta.experiment_id, seed.version],
      [alpha.run_id, alpha.experiment_id, seed.version],
    ]);
    expect(catalogRuns[0].comparison_group).toMatch(/^[a-f0-9]{64}$/);
    expect(catalogRuns[0].comparison_group).not.toBe(group);
    expect(catalogRuns.slice(1).map(r => r.comparison_group)).toEqual([group, group]);
    const listed = await scopes.send<RowRuns>(a, 'GET', path);
    expect(listed.status).toBe(200);
    expect(listed.body.result.runs.map(r => r.run_id)).toEqual([subsetId, beta.run_id, alpha.run_id]);
    const subset = listed.body.result.runs[0];
    const withSubset = await stored(probe, seed);
    const { context, page } = await openRowView(scopes, browser, a, seed.dataset, row, '&rowTab=runs');
    try {
      await expectRunsTabOpen(page);
      await expect.poll(() => runOrder(page)).toEqual([subsetId, beta.run_id, alpha.run_id]);
      const notice = `Runs ${alpha.label} and ${subset.label} can't be compared side by side because their set-ups differ.`;
      await expectComparisonBlocked(page, [alpha, subset], notice);
    } finally { await context.close(); }
    expect(await stored(probe, seed)).toEqual(withSubset);
  });
});
