import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';
import { MockUpstream } from '../../lib/upstream';
import {
  EXPERIMENTS, attach, seedPairwise, nativeState, judgeState, verdictProjection,
  type Example, type CatalogRun, type Detail,
} from './pairwise-judge-fixture';

// README Writing a flow: each browser phase has its own first-paint allowance.
const UI_READY = 60_000;
// Native experiment generation + row evaluation; not a CDC wait.
const NATIVE_RUNS_READY = { timeout: POLL.EVAL_RESULT.timeout + POLL.ASYNC_JOB.timeout,
  intervals: POLL.EVAL_RESULT.intervals };
// pairwise_judges.py dispatches asynchronous judge work on tasks_s.
const PAIRWISE_COMPLETED = POLL.ASYNC_JOB;

// Draft: walk-lead must qualify the local custom-model route, visible surface,
// managed run and one-sided fallibility proofs. No launch-state overrides.
test('EXP-E2E-013: compare two experiment runs with a pairwise judge', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EXP-E2E-013', area: 'experiments',
    userGoal: 'Compare two experiment runs outputs with an automated pairwise judge and see its exact verdicts',
    steps: [
      'seed a two-row dataset, deterministic local model, two saved prompt sources and a pinned pairwise judge version through public APIs',
      'wait for two comparable native runs on the same immutable examples and trials',
      'select both runs in the dataset row Runs tab and open Compare side by side',
      'make the preferred run the baseline and select one example row',
      'start a judge job from the selection bar using the saved judge version UUID',
      'inspect exact completion counts, both example verdicts and the judge summary',
    ],
    backendChecks: [
      'Storage and API, synchronous seed phase (120s): saved judge UUID pins the exact single A/B/tie evaluator configuration and local model',
      'Postgres, NATIVE_RUNS_READY (EVAL_RESULT + ASYNC_JOB, 150s): exactly two completed runs share the two immutable examples, trial 1 and inputs, with exact preferred and other outputs',
      'Postgres, PAIRWISE_COMPLETED (ASYNC_JOB, 60s): the browser-created job and both items complete with exact scope, version pins, snapshots, canonical winners and reasons',
      'API, immediate reads after completion (30s phase): raw detail and oriented summary equal the expected two verdicts and scoped mock receipts identify local judge calls',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(920_000);
  // 60s provisioning + 120s seed + 150s native completion + 8*60s UI
  // + 60s judge completion + 30s API/receipts + 20s cleanup = 920s.
  for (const url of [E2E.appUrl, E2E.apiUrl, E2E.mockLlmUrl]) {
    expect(new URL(url).hostname, 'this draft requires the local walk stack').toBe('localhost');
  }
  page.setDefaultTimeout(UI_READY);
  // Safety veto only: no rewritten responses, launch flags or real providers.
  await page.context().route('**/*', route => new URL(route.request().url()).hostname === 'localhost'
    ? route.continue() : route.abort('blockedbyclient'));
  const prefix = `e2e-pairwise-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const mock = new MockUpstream();
  try {
    const seeded = await test.step('API: seed saved sources and judge',
      () => seedPairwise(actor, probe, mock, prefix, testInfo), { timeout: 120_000 });
    let native: Awaited<ReturnType<typeof nativeState>> = { runs: [], items: [] };
    await expect.poll(async () => {
      native = await nativeState(probe, actor, seeded.experimentId);
      return { runs: native.runs.map(r => ({ version: r.prompt_version_id, status: r.status })),
        items: native.items.map(i => i.status) };
    }, NATIVE_RUNS_READY).toEqual({
      runs: seeded.prompts.map(p => ({ version: p.versionId, status: 'completed' }))
        .sort((a, b) => a.version.localeCompare(b.version)),
      items: Array(4).fill('completed'),
    });
    const preferred = native.runs.find(r => r.prompt_version_id === seeded.prompts[0].versionId)!;
    const other = native.runs.find(r => r.prompt_version_id === seeded.prompts[1].versionId)!;
    expect(preferred.dataset_version_id).toBe(other.dataset_version_id);
    // native_run_binding.py captures {values: {originalColumnId: cellValue}};
    // dataset_versions.py preserves the original legacy row identity.
    const examples = await probe.pg<Example>(`SELECT i.example_id,i.input,i.legacy_row_id
      FROM model_hub_dataset_version_item i JOIN model_hub_dataset_version v ON v.id=i.dataset_version_id
      JOIN model_hub_dataset d ON d.id=v.dataset_id
      WHERE v.id=$1 AND d.id=$2 AND d.organization_id=$3 AND d.workspace_id=$4
        AND NOT i.deleted AND NOT v.deleted ORDER BY i.example_id`,
    [preferred.dataset_version_id, seeded.datasetId, actor.organizationId, actor.workspaceId]);
    expect(examples.map(e => e.legacy_row_id).sort()).toEqual([...seeded.rowIds].sort());
    for (const example of examples) {
      expect(example.input).toEqual({ values: {
        [seeded.columnId]: seeded.questions[seeded.rowIds.indexOf(example.legacy_row_id)],
      } });
    }
    for (const [run, output] of [[preferred, seeded.preferredOutput], [other, seeded.otherOutput]] as const) {
      expect(native.items.filter(i => i.run_id === run.id)).toEqual(examples.map(e => ({
        run_id: run.id, example_id: e.example_id, trial_number: 1, status: 'completed', input: e.input, output,
      })));
    }
    // ResultsRunsView returns GeneralMethods.result, not probe.apiList's table.
    const catalog = await actor.api.get<{ result: { results: CatalogRun[] } }>(
      `/model-hub/datasets/${seeded.datasetId}/runs/`, { experiment_id: seeded.experimentId, limit: 500 });
    expect(catalog.result.results.map(r => r.run_id).sort()).toEqual([preferred.id, other.id].sort());
    const preferredLabel = catalog.result.results.find(r => r.run_id === preferred.id)!.label;
    const otherLabel = catalog.result.results.find(r => r.run_id === other.id)!.label;
    const groups = catalog.result.results.map(r => r.comparison_group);
    expect(groups[0]).toBeTruthy();
    expect(groups[1]).toBe(groups[0]);
    await attach(testInfo, 'completed-native-runs', { ...native, examples, catalog: catalog.result });

    // UI phase 1: RowView and RowRunsTab expose the existing Runs tab/checkboxes.
    await test.step('UI: open the seeded row Runs tab', async () => {
      await page.goto(`/dashboard/develop/${seeded.datasetId}?row=${seeded.rowIds[0]}&rowTab=runs`,
        { waitUntil: 'domcontentloaded', timeout: UI_READY });
      await expect(page.getByTestId('row-tab-runs')).toBeVisible({ timeout: UI_READY });
      await page.getByTestId('row-tab-runs').click();
      await expect(page.getByRole('checkbox', { name: `Select ${preferredLabel} for side by side`, exact: true }))
        .toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });
    await test.step('UI: select the two completed runs', async () => {
      for (const label of [preferredLabel, otherLabel]) {
        const checkbox = page.getByRole('checkbox', { name: `Select ${label} for side by side`, exact: true });
        await checkbox.check();
        await expect(checkbox).toBeChecked({ timeout: UI_READY });
      }
      await expect(page.getByTestId('row-runs-side-by-side')).toHaveText('Compare side by side (2)', { timeout: UI_READY });
    }, { timeout: UI_READY });
    await test.step('UI: open the experiment comparison', async () => {
      await page.getByTestId('row-runs-side-by-side').click();
      await expect(page).toHaveURL(url => url.pathname === `/dashboard/develop/experiment/${seeded.experimentId}/data`
        && JSON.stringify((url.searchParams.get('runs') ?? '').split(',').sort()) === JSON.stringify([preferred.id, other.id].sort()),
      { timeout: UI_READY });
      await expect(page.getByTestId('run-line')).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });
    // RunsChip -> RunBar -> common/Runs/RunChip: existing menu, not a URL override.
    await test.step('UI: use the preferred run as baseline', async () => {
      await page.getByTestId('run-line').click();
      // RunChip's menu trigger is a labelled span (common/Runs/RunChip.jsx aria-label `${label} menu`), not a button.
      await page.getByTestId('run-bar-popover').getByLabel(`${preferredLabel} menu`, { exact: true }).click();
      const choose = page.getByRole('menuitem', { name: 'Use as baseline run for this view', exact: true });
      const already = page.getByRole('menuitem', { name: 'Baseline run for this view', exact: true });
      await expect(choose.or(already)).toBeVisible({ timeout: UI_READY });
      if (await choose.isVisible()) await choose.click();
      else await page.keyboard.press('Escape');
      // Close the run-bar popover without changing the selected runs.
      await page.keyboard.press('Escape');
      await expect(page.getByTestId('run-bar-popover')).not.toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });
    await test.step('UI: select one example', async () => {
      // ResultsGrid's AG Grid row-id is the immutable example UUID. The pinned
      // selection column owns the checkbox (no dependency on row display order).
      const row = page.locator(`[data-results-grid-root] .ag-row[row-id="${examples[0].example_id}"]`);
      await row.getByRole('checkbox').check();
      await expect(page.getByTestId('results-selection-bar').getByText('1 selected', { exact: true }))
        .toHaveText('1 selected', { timeout: UI_READY });
    }, { timeout: UI_READY });
    const dialog = page.getByRole('dialog', { name: 'Pairwise judge', exact: true });
    await test.step('UI: open the pairwise judge dialog', async () => {
      // SelectionBar may overflow its action into More actions at narrow widths.
      const bar = page.getByTestId('results-selection-bar');
      const start = bar.getByRole('button', { name: 'Start judge job…', exact: true });
      if (await start.isVisible()) await start.click();
      else {
        await bar.getByRole('button', { name: 'More actions', exact: true }).click();
        await page.getByRole('menuitem', { name: 'Start judge job…', exact: true }).click();
      }
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await expect(dialog.getByText(`A (baseline): ${preferred.id}`, { exact: true }))
        .toHaveText(`A (baseline): ${preferred.id}`, { timeout: UI_READY });
      await expect(dialog.getByText(`B (candidate): ${other.id}`, { exact: true }))
        .toHaveText(`B (candidate): ${other.id}`, { timeout: UI_READY });
    }, { timeout: UI_READY });
    const root = `${EXPERIMENTS}${seeded.experimentId}/pairwise/`;
    const jobId = await test.step('UI: submit the explicitly saved judge version', async () => {
      await dialog.getByLabel('Judge version ID', { exact: true }).fill(seeded.judgeVersionId);
      const pending = page.waitForResponse(r => new URL(r.url()).pathname === `${root}judges/`
        && r.request().method() === 'POST', { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Start judge job', exact: true }).click();
      const response = await pending;
      expect(response.request().postDataJSON()).toEqual({
        run_a: preferred.id, run_b: other.id, judge_version: seeded.judgeVersionId,
      });
      expect(response.status()).toBe(202);
      const job = await response.json() as Detail;
      await attach(testInfo, 'browser-created-judge', { experimentId: seeded.experimentId, job });
      expect(job.id).toMatch(/^[0-9a-f-]{36}$/);
      return job.id;
    }, { timeout: UI_READY });

    const [runA, runB] = [preferred.id, other.id].sort();
    const expectedWinner = runA === preferred.id ? 'a' : 'b';
    const expectedVerdicts = examples.map(e => ({ example_id: e.example_id, trial_number: 1,
      status: 'completed', winner: expectedWinner, reason: seeded.reason, error: '' }));
    let storage: Awaited<ReturnType<typeof judgeState>> = { jobs: [], items: [] };
    await expect.poll(async () => {
      storage = await judgeState(probe, actor, seeded.experimentId, jobId);
      return { jobs: storage.jobs.map(j => j.status), items: storage.items.map(i => i.status) };
    }, PAIRWISE_COMPLETED).toEqual({ jobs: ['completed'], items: ['completed', 'completed'] });
    await attach(testInfo, 'completed-pairwise-storage', storage);
    const { finished_at: finished, ...jobFields } = storage.jobs[0];
    expect(finished).not.toBeNull();
    expect(jobFields).toEqual({ id: jobId, experiment_id: seeded.experimentId,
      organization_id: actor.organizationId, workspace_id: actor.workspaceId,
      run_a_id: runA, run_b_id: runB, judge_version_id: seeded.judgeVersionId,
      dataset_version_id: preferred.dataset_version_id, status: 'completed' });
    expect(storage.items.map(verdictProjection)).toEqual(expectedVerdicts);
    for (const [i, item] of storage.items.entries()) {
      expect(item.finished_at).not.toBeNull();
      expect(['ab', 'ba']).toContain(item.presentation_order);
      expect(item.snapshot).toEqual({ input: examples[i].input,
        output_a: runA === preferred.id ? seeded.preferredOutput : seeded.otherOutput,
        output_b: runB === preferred.id ? seeded.preferredOutput : seeded.otherOutput });
    }

    await test.step('API: exact verdicts, summary and local judge receipts', async () => {
      // PairwiseJudgeDetailView returns raw JSON, without a result/table envelope.
      const detail = await actor.api.get<Detail>(`${root}judges/${jobId}/`, { offset: 0, limit: 50 });
      const { id, source, run_a, run_b, judge_version, dataset_version, status, counts, total, next_offset } = detail;
      expect({ id, source, run_a, run_b, judge_version, dataset_version, status, counts, total, next_offset })
        .toEqual({ id: jobId, source: 'judge', run_a: runA, run_b: runB, judge_version: seeded.judgeVersionId,
          dataset_version: preferred.dataset_version_id, status: 'completed', counts: { completed: 2 }, total: 2, next_offset: null });
      expect(detail.items.map(verdictProjection)).toEqual(expectedVerdicts);
      const summary = await actor.api.get(`${root}summary/`, { run_a: preferred.id, run_b: other.id, judge_job: jobId });
      const human = { a_wins: 0, b_wins: 0, ties: 0, rows_reviewed: 0 };
      expect(summary).toEqual({ ...human, human,
        judge: { a_wins: 2, b_wins: 0, ties: 0, rows_reviewed: 2 },
        agreement: { matches: 0, compared: 0, rate: null }, rows_total: 2 });
      // Never attach request headers (they can contain the synthetic provider key).
      const receipts = (await mock.requests(prefix)).filter(r => {
        const text = JSON.stringify(r.body);
        return text.includes(`<output_a>${seeded.preferredOutput}</output_a>`)
          || text.includes(`<output_b>${seeded.preferredOutput}</output_b>`);
      }).map(r => ({ method: r.method, path: r.path, body: r.body }));
      expect(receipts).toHaveLength(2);
      expect(receipts.map(r => ({ method: r.method, path: r.path }))).toEqual(Array(2).fill({
        method: 'POST', path: `/s/${prefix}/-/v1/chat/completions`,
      }));
      await attach(testInfo, 'pairwise-api-oracles', { detail, summary, receipts });
    }, { timeout: 30_000 });

    // Phase 8, PairwiseJudgeDialog: both saved examples, despite selecting ONE.
    await test.step('UI: read both exact verdicts and judge counts', async () => {
      for (const text of ['Status: completed', '2/2 completed · 0 failed · 0 cancelled',
        'Human: A 0 · B 0 · Tie 0', 'Judge: A 2 · B 0 · Tie 0',
        'Agreement: no comparable human votes yet']) {
        await expect(dialog.getByText(text, { exact: true })).toHaveText(text, { timeout: UI_READY });
      }
      await expect(dialog.getByText(/ · Trial \d+ · /)).toHaveText(
        examples.map(e => `${e.example_id} · Trial 1 · A`), { timeout: UI_READY });
      await expect(dialog.getByText(seeded.reason, { exact: true }))
        .toHaveText([seeded.reason, seeded.reason], { timeout: UI_READY });
    }, { timeout: UI_READY });
  } finally {
    // Retain product records for diagnosis; clear this test's rules only.
    await test.step('dispose scoped mock replies', () => mock.clearReplies(prefix), { timeout: 20_000 });
  }
});
