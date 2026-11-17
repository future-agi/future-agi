import { test, expect } from '../../lib/scope-actors';
import type { Locator, Page } from '@playwright/test';
import type { ScopeActor } from '../../lib/scope-actors';
import type { StateProbe } from '../../lib/state-probe';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';
import { API, MODEL, ROWS, STANDIN, outputCells, runCells, seedRunControl } from './run-control-seed';
import type { Control } from './run-control-seed';

// Before either test, start the barrier stand-in using run-control.README.md.
// bin/e2e test does not start it; managed mock-llm cannot replace its barriers.
// BASE frontend/src/pages/dashboard/Develop/ExperimentWrapper.jsx:34-35 selects DSX
// unless opens_in_full_results is false; Manage runs belongs to the classic page.
// Locator citations below are relative to frontend/src/sections/ at this BASE.
const UI_READY = 60_000;
// ExperimentRunControl model: immutable execution binding plus persisted phase.
type Binding = { id: number; experiment_dataset_id: string; workflow_id: string;
  workflow_run_id: string; config_id: string; phase: string };

type CatalogRun = { run_id: string; label: string };

async function catalogRun(actor: ScopeActor, probe: StateProbe, endpoint: string, variantId: string) {
  // futureagi/model_hub/services/experiment_run_control.py:174-198: an unselected
  // control read returns variant IDs; DSX sends canonical ExperimentRun IDs.
  // models/native_experiment_runs.py:62-71 persists the exact mapping.
  const links = await probe.pg<{ run_id: string }>(
    'SELECT run_id FROM model_hub_nativerunbinding WHERE variant_id=$1', [variantId]);
  expect(links).toHaveLength(1);
  const detail = await actor.api.get<{ result: { runs: CatalogRun[] } }>(endpoint);
  const matched = detail.result.runs.filter(run => run.run_id === links[0].run_id);
  expect(matched).toHaveLength(1);
  return { run_id: matched[0].run_id, label: matched[0].label };
}

// experiment-detail/results/RunsChip.jsx:37,82: expand the multi-run bar, also after reload.
async function openRunBar(page: Page) {
  await page.getByTestId('run-line').click();
  await expect(page.getByTestId('run-bar-popover')).toBeVisible({ timeout: UI_READY });
}

function runChip(page: Page, label: string) {
  // experiment-detail/results/RunsChip.jsx:82 scopes the bar;
  // common/Runs/RunChip.jsx:307,422 gives the chip role and named chevron.
  return page.getByTestId('run-bar-popover').getByRole('button')
    .filter({ has: page.getByLabel(`${label} menu`, { exact: true }) });
}

async function openRunMenu(chip: Locator) {
  // common/Runs/RunChip.jsx:422 names the chevron "<run label> menu".
  const menu = chip.getByLabel(/ menu$/);
  const label = (await menu.getAttribute('aria-label'))!.slice(0, -' menu'.length);
  await menu.click();
  return label;
}

async function stopRun(page: Page, chip: Locator) {
  const label = await openRunMenu(chip);
  // experiment-detail/results/RunBar.jsx:161: only this run's Stop, not exp-stop
  // (experiment-detail/ExperimentRow.jsx:767-769,1162 stops all active runs).
  await page.getByRole('menuitem', { name: `Stop ${label}`, exact: true }).click();
  // common/Runs/useRunActions.js:182-185 supplies the title and Stop run label;
  // common/ux/ConfirmDialog.jsx:90,150-157 supplies the named dialog and button.
  await page.getByRole('dialog', { name: `Stop ${label}?`, exact: true })
    .getByRole('button', { name: 'Stop run', exact: true }).click();
}

async function expectRunStatus(page: Page, chip: Locator, runId: string, status: 'Running' | 'Completed') {
  // common/Runs/RunChip.jsx:316 binds the visible chip to the selected UUID.
  await expect(chip).toHaveAttribute('data-run-id', runId, { timeout: UI_READY });
  if (status === 'Running') {
    // common/Runs/RunChip.jsx:141-150: active runs have a named progressbar.
    await expect(chip.getByRole('progressbar')).toBeVisible({ timeout: UI_READY });
  }
  await openRunMenu(chip);
  // experiment-detail/results/RunBar.jsx:98: wait until the menu is mounted.
  const details = page.getByRole('menuitem', { name: 'Show run details', exact: true });
  await expect(details).toBeVisible({ timeout: UI_READY });
  if (status === 'Completed') {
    // RunBar.jsx:158-190 omits Stop and Resume for completed runs. Other menu
    // actions remain, unlike the classic dialog's empty completed button cell.
    await expect(page.getByRole('menuitem', { name: /^(Stop|Resume) / })).toHaveCount(0, { timeout: UI_READY });
  }
  // RunBar.jsx:98: details exposes the status; completed chips have no status badge.
  await details.click();
  // experiment-detail/results/RunDetailsPopover.jsx:122,358: exact run UUID and
  // status token, allowing only the adjacent author/relative-time suffix to vary.
  await expect(page.getByText(runId, { exact: true })).toBeVisible({ timeout: UI_READY });
  await expect(page.getByText(new RegExp(`^e2e-control-[a-z0-9-]+ · ${status}(?: · |$)`)))
    .toBeVisible({ timeout: UI_READY });
  await page.keyboard.press('Escape');
  // RunDetailsPopover.jsx:362: wait for the details popover to close, not the run bar.
  await expect(page.getByRole('button', { name: 'Copy run ID', exact: true })).toHaveCount(0, { timeout: UI_READY });
}

test('DATA-E2E-021: stop one experiment run and resume without repeating completed work', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-021', area: 'datasets',
    userGoal: 'Stop one run while its sibling finishes, then resume the stopped run without repeating completed results.',
    steps: ['create two synthetic organizations and an experiment backed by a labelled local LLM STANDIN',
      'open the new experiment page, expand Runs and confirm Stop on only the selected run menu',
      'wait for current work to drain and the sibling to finish, then reload the stopped state',
      'refuse foreign organization and sibling workspace reads and writes',
      'resume the selected run through its run menu and read the completed result'],
    backendChecks: ['actual UI stop and resume requests select exactly one run and agree with persisted states',
      'completed output and evaluation cells and the sibling remain unchanged, and execution identities are stable through resume',
      'both runs complete every row with exactly one STANDIN call per output and no repeated completed work',
      'cross-organization and cross-workspace control requests are refused without changing the owned executions'],
  }),
}, async ({ browser, scopeActors, scopeProbe: probe, request }, testInfo) => {
  // Retain the overall budget for ASYNC_JOB polls, UI readiness and public seeding.
  test.setTimeout(960_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const actor = scopeActors.ownerA;
  const health = await request.get(`${STANDIN}/health`);
  expect(health.status()).toBe(200);
  expect(await health.json()).toEqual({ kind: 'LLM STANDIN', upstreams: [] });
  const seeded = await seedRunControl(actor, probe, suffix);
  await testInfo.attach('seeded-experiment', { contentType: 'application/json',
    body: JSON.stringify({ ...seeded, organizationId: actor.organizationId, workspaceId: actor.workspaceId }) });
  // Dataset identity comes from ManualDatasetCreateResponseSerializer, scope from Dataset.
  expect(await probe.pg(`SELECT organization_id,workspace_id FROM model_hub_dataset WHERE id=$1`, [seeded.datasetId]))
    .toEqual([{ organization_id: actor.organizationId, workspace_id: actor.workspaceId }]);
  const endpoint = `${API}${seeded.experimentId}/`;
  const read = () => actor.api.get<Control>(`${endpoint}run-control/`);
  const bindings = () => probe.pg<Binding>(`SELECT id, experiment_dataset_id, workflow_id,
    workflow_run_id, config_id, phase FROM model_hub_experimentruncontrol
    WHERE experiment_dataset_id IN (SELECT id FROM model_hub_experimentdatasettable WHERE experiment_id=$1)
    ORDER BY experiment_dataset_id, id`, [seeded.experimentId]);
  const count = async (role: string) => {
    const response = await request.get(`${STANDIN}/calls`, { params: { group: `CONTROL:${suffix}:${role}` } });
    expect(response.status()).toBe(200);
    return (await response.json()).count as number;
  };
  const release = async (phase: string) => {
    const response = await request.post(`${STANDIN}/release`, {
      params: { group: `CONTROL:${suffix}:selected:${phase}` },
    });
    expect(response.status()).toBe(200);
  };
  const context = await scopeActors.openContext(browser, actor);
  // Runtime traffic is local-only. In particular, analytics and license URLs cannot escape.
  await context.route('**/*', route => {
    const host = new URL(route.request().url()).hostname;
    return host === 'localhost' ? route.continue() : route.abort('blockedbyclient');
  });
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  let selectedId = '';
  let siblingId = '';
  try {
    // A drain call proves the second batch began; not all ten calls need
    // a worker slot at once. Assert exactly twenty after draining it below.
    await expect.poll(async () => (await count('selected')) > 10, POLL.ASYNC_JOB).toBe(true);
    const runs = (await read()).result.runs;
    expect(runs.map(run => run.name).sort()).toEqual([`selected-${MODEL}`, `sibling-${MODEL}`]);
    selectedId = runs.find(run => run.name === `selected-${MODEL}`)!.run_id;
    siblingId = runs.find(run => run.name === `sibling-${MODEL}`)!.run_id;
    const selectedRun = await catalogRun(actor, probe, endpoint, selectedId);
    const siblingRun = await catalogRun(actor, probe, endpoint, siblingId);
    await expect.poll(async () => (await bindings()).length, POLL.ASYNC_JOB).toBe(2);
    const beforeBindings = await bindings();
    const identities = (rows: Binding[]) => rows.map(({ phase: _phase, ...identity }) => identity);
    expect(new Set(beforeBindings.map(row => row.workflow_run_id)).size).toBe(2);
    const finishedBeforeStop = (await outputCells(probe, selectedId)).filter(cell => cell.status === 'pass');
    expect(finishedBeforeStop).toHaveLength(10);
    expect(finishedBeforeStop.map(cell => cell.value)).toEqual(Array(10).fill(`CONTROL:${suffix}:selected`));
    await testInfo.attach('execution-identities', { contentType: 'application/json',
      body: JSON.stringify({ beforeBindings, finishedBeforeStop, selectedId, siblingId, selectedRun, siblingRun }) });
    await page.goto(`/dashboard/develop/experiment/${seeded.experimentId}/data`, { waitUntil: 'domcontentloaded' });
    await openRunBar(page);
    const selectedChip = runChip(page, selectedRun.label);
    const siblingChip = runChip(page, siblingRun.label);
    await expectRunStatus(page, selectedChip, selectedRun.run_id, 'Running');
    await test.step('backend check 1: native selected stop persists after draining', async () => {
      const stopped = page.waitForResponse(response => new URL(response.url()).pathname === `${endpoint}stop/`
        && response.request().method() === 'POST', { timeout: UI_READY });
      await stopRun(page, selectedChip);
      const response = await stopped;
      expect(response.status()).toBe(202);
      expect(response.request().postDataJSON()).toEqual({ run_ids: [selectedRun.run_id] });
      expect((await response.json()).result.runs.map((run: { run_id: string }) => run.run_id)).toEqual([selectedRun.run_id]);
      await release('drain');
      await expect.poll(async () => (await read()).result.runs.map(run => [run.name, run.status]).sort(), POLL.ASYNC_JOB)
        .toEqual([[`selected-${MODEL}`, 'STOPPED'], [`sibling-${MODEL}`, 'COMPLETED']]);
      await expect.poll(async () => (await bindings()).map(row => row.phase).sort(), POLL.ASYNC_JOB)
        .toEqual(['COMPLETED', 'STOPPED']);
      await expect.poll(() => count('selected'), POLL.ASYNC_JOB).toBe(20);
      expect(await probe.pg(`SELECT name,status FROM model_hub_experimentdatasettable
        WHERE experiment_id=$1 ORDER BY name`, [seeded.experimentId]))
        .toEqual([{ name: `selected-${MODEL}`, status: 'Cancelled' }, { name: `sibling-${MODEL}`, status: 'Completed' }]);
      expect(await probe.pg(`SELECT status FROM model_hub_experimentstable WHERE id=$1`, [seeded.experimentId]))
        .toEqual([{ status: 'Cancelled' }]);
      await page.reload({ waitUntil: 'domcontentloaded' });
      await openRunBar(page);
      await expect(selectedChip).toHaveAttribute('data-run-id', selectedRun.run_id, { timeout: UI_READY });
      // common/Runs/RunChip.jsx:220-230: the stopped chip keeps an exact Stopped badge.
      await expect(selectedChip.getByText('Stopped', { exact: true })).toBeVisible({ timeout: UI_READY });
      await expectRunStatus(page, siblingChip, siblingRun.run_id, 'Completed');
    });
    // EvaluationRunner._get_column_config uses experiment_evaluation; CellStatus.PASS is "pass".
    const pausedRunCells = await runCells(probe, selectedId);
    const siblingRunCells = await runCells(probe, siblingId);
    const completedRunCells = pausedRunCells.filter(cell => cell.status === 'pass');
    expect(completedRunCells.filter(cell => cell.source === 'experiment_evaluation')).toHaveLength(20);
    expect(siblingRunCells.filter(cell => cell.source === 'experiment_evaluation' && cell.status === 'pass')).toHaveLength(ROWS);
    expect(await count('selected:judge')).toBe(20);
    expect(await count('sibling:judge')).toBe(ROWS);
    const pausedCells = await outputCells(probe, selectedId);
    const completedPaused = pausedCells.filter(cell => cell.status === 'pass');
    expect(completedPaused).toHaveLength(20);
    const siblingCells = await outputCells(probe, siblingId);
    expect(siblingCells).toHaveLength(ROWS);
    expect(siblingCells.every(cell => cell.status === 'pass')).toBe(true);
    expect(siblingCells.map(cell => cell.value)).toEqual(Array(ROWS).fill(`CONTROL:${suffix}:sibling`));
    expect(await count('selected')).toBe(20);
    expect(await count('sibling')).toBe(ROWS);
    await test.step('backend check 4: foreign scopes cannot read, stop or resume the owned experiment', async () => {
      for (const other of [scopeActors.ownerB, scopeActors.withWorkspace(actor, scopeActors.emptyWorkspace.id)]) {
        for (const [method, action] of [['GET', 'run-control'], ['POST', 'stop'], ['POST', 'resume']]) {
          const response = await scopeActors.send(other, method, `${endpoint}${action}/`,
            method === 'POST' ? { run_ids: [selectedId] } : undefined);
          expect(response.status, `${method} ${action} from ${other.workspaceId}`).toBe(404);
        }
      }
      expect(await outputCells(probe, selectedId)).toEqual(pausedCells);
      expect(await outputCells(probe, siblingId)).toEqual(siblingCells);
      expect((await read()).result.runs.find(run => run.run_id === selectedId)!.status).toBe('STOPPED');
      expect(identities(await bindings())).toEqual(identities(beforeBindings));
    });
    await test.step('backend check 1: native resume retains its selection and persists Running', async () => {
      const label = await openRunMenu(selectedChip);
      const resumed = page.waitForResponse(response => new URL(response.url()).pathname === `${endpoint}resume/`
        && response.request().method() === 'POST', { timeout: UI_READY });
      // RunBar.jsx:179-184 and common/Runs/useRunActions.js:486: this run, four rows left.
      await page.getByRole('menuitem', { name: `Resume ${label} · ${ROWS - 20} left`, exact: true }).click();
      const response = await resumed;
      expect(response.status()).toBe(202);
      expect(response.request().postDataJSON()).toEqual({ run_ids: [selectedRun.run_id] });
      await expect.poll(async () => (await bindings()).find(row => row.experiment_dataset_id === selectedId)!.phase,
        POLL.ASYNC_JOB).toBe('RUNNING');
      expect(await probe.pg(`SELECT status FROM model_hub_experimentdatasettable WHERE id=$1`, [selectedId]))
        .toEqual([{ status: 'Running' }]);
      expect(await probe.pg(`SELECT status FROM model_hub_experimentstable WHERE id=$1`, [seeded.experimentId]))
        .toEqual([{ status: 'Running' }]);
      await expectRunStatus(page, selectedChip, selectedRun.run_id, 'Running');
      await release('finish');
    });
    await test.step('backend checks 2 and 3: no repeated work or replacement execution', async () => {
      await expect.poll(async () => (await read()).result.runs.map(run => run.status), POLL.ASYNC_JOB)
        .toEqual(['COMPLETED', 'COMPLETED']);
      const afterCells = await outputCells(probe, selectedId);
      expect(afterCells).toHaveLength(ROWS);
      expect(afterCells.every(cell => cell.status === 'pass')).toBe(true);
      expect(afterCells.map(cell => cell.value)).toEqual(Array(ROWS).fill(`CONTROL:${suffix}:selected`));
      expect(afterCells.filter(cell => completedPaused.some(old => old.id === cell.id))).toEqual(completedPaused);
      expect(afterCells.filter(cell => finishedBeforeStop.some(old => old.id === cell.id))).toEqual(finishedBeforeStop);
      expect(await outputCells(probe, siblingId)).toEqual(siblingCells);
      expect(identities(await bindings())).toEqual(identities(beforeBindings));
      const completed = await runCells(probe, selectedId);
      expect(completed.filter(cell => completedRunCells.some(old => old.id === cell.id))).toEqual(completedRunCells);
      expect(completed.filter(cell => cell.source === 'experiment_evaluation' && cell.status === 'pass')).toHaveLength(ROWS);
      expect(await runCells(probe, siblingId)).toEqual(siblingRunCells);
      expect(await count('selected')).toBe(ROWS);
      expect(await count('sibling')).toBe(ROWS);
      expect(await count('selected:judge')).toBe(ROWS);
      expect(await count('sibling:judge')).toBe(ROWS);
      expect(await catalogRun(actor, probe, endpoint, selectedId)).toEqual(selectedRun);
      expect(await catalogRun(actor, probe, endpoint, siblingId)).toEqual(siblingRun);
      await expectRunStatus(page, selectedChip, selectedRun.run_id, 'Completed');
      await testInfo.attach('completed-state', { contentType: 'application/json', body: JSON.stringify({
        runs: (await read()).result.runs, bindings: await bindings(), afterCells, siblingCells,
        completedRunCells: completed, siblingRunCells,
        calls: { selected: await count('selected'), sibling: await count('sibling'),
          selectedJudge: await count('selected:judge'), siblingJudge: await count('sibling:judge') },
      }) });
    });
  } finally {
    await release('drain');
    await release('finish');
    await context.close();
  }
});

test('legacy whole-stop cancels a cooperatively paused execution instead of returning a no-op',
  async ({ browser, scopeActors, scopeProbe: probe, request }, testInfo) => {
    test.setTimeout(480_000);
    const suffix = `cancel-${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const actor = scopeActors.ownerA;
    const seeded = await seedRunControl(actor, probe, suffix);
    await testInfo.attach('seeded-cancellation', { contentType: 'application/json', body: JSON.stringify(seeded) });
    const endpoint = `${API}${seeded.experimentId}/`;
    const read = () => actor.api.get<Control>(`${endpoint}run-control/`);
    const context = await scopeActors.openContext(browser, actor);
    await context.route('**/*', route => new URL(route.request().url()).hostname === 'localhost'
      ? route.continue() : route.abort('blockedbyclient'));
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    const release = (phase: string) => request.post(`${STANDIN}/release`, {
      params: { group: `CONTROL:${suffix}:selected:${phase}` },
    });
    const count = async () => {
      const response = await request.get(`${STANDIN}/calls`, {
        params: { group: `CONTROL:${suffix}:selected` },
      });
      expect(response.status()).toBe(200);
      return (await response.json()).count as number;
    };
    try {
      // workflows.py:638-663 schedules ten rows, but shared worker slots need
      // not dispatch all ten together. One drain call proves batch two began.
      // Keep the exact twenty-call assertion after releasing that batch below.
      await expect.poll(async () => (await count()) > 10, POLL.ASYNC_JOB).toBe(true);
      const runs = (await read()).result.runs;
      const selectedId = runs.find(run => run.name === `selected-${MODEL}`)!.run_id;
      const siblingId = runs.find(run => run.name === `sibling-${MODEL}`)!.run_id;
      const selectedRun = await catalogRun(actor, probe, endpoint, selectedId);
      await page.goto(`/dashboard/develop/experiment/${seeded.experimentId}/data`, { waitUntil: 'domcontentloaded' });
      await openRunBar(page);
      const selectedChip = runChip(page, selectedRun.label);
      await expectRunStatus(page, selectedChip, selectedRun.run_id, 'Running');
      const stopped = page.waitForResponse(response => new URL(response.url()).pathname === `${endpoint}stop/`
        && response.request().method() === 'POST', { timeout: UI_READY });
      await stopRun(page, selectedChip);
      const response = await stopped;
      expect(response.status()).toBe(202);
      expect(response.request().postDataJSON()).toEqual({ run_ids: [selectedRun.run_id] });
      expect((await response.json()).result.runs.map((run: { run_id: string }) => run.run_id)).toEqual([selectedRun.run_id]);
      await release('drain');
      await expect.poll(async () => (await read()).result.runs.find(run => run.run_id === selectedId)!.status,
        POLL.ASYNC_JOB).toBe('STOPPED');
      await expect.poll(count, POLL.ASYNC_JOB).toBe(20);
      expect((await outputCells(probe, selectedId)).filter(cell => cell.status === 'pass')).toHaveLength(20);
      await expect.poll(() => probe.pg('SELECT status FROM model_hub_experimentstable WHERE id=$1', [seeded.experimentId]),
        POLL.ASYNC_JOB).toEqual([{ status: 'Cancelled' }]);
      const sibling = await runCells(probe, siblingId);
      // ExperimentRow.jsx:425-427,1133 offers whole Stop only while Running/Queued
      // with no active run (or a run-control-off legacy retry). Run control is on
      // here (common/ExperimentRunControl/rerunPauseGuard.js:17-25), so this has
      // no such UI action, so retain the legacy API path, not the header's run-id Stop.
      // ExperimentStopV2View's public empty-body contract cancels the parent, unlike selected Stop.
      const whole = await scopeActors.send<{ result: { workflows_cancelled: { main: boolean } } }>(
        actor, 'POST', `${endpoint}stop/`, {});
      expect(whole.status).toBe(200);
      expect(whole.body.result.workflows_cancelled.main).toBe(true);
      await expect.poll(async () => ['CANCELED', 'TERMINATED'].includes(
        (await read()).result.runs.find(run => run.run_id === selectedId)!.status), POLL.ASYNC_JOB).toBe(true);
      expect((await scopeActors.send(actor, 'POST', `${endpoint}resume/`, { run_ids: [selectedId] })).status).toBe(409);
      expect(await runCells(probe, siblingId)).toEqual(sibling);
      await testInfo.attach('legacy-cancelled-state', { contentType: 'application/json',
        body: JSON.stringify({ whole, control: await read() }) });
    } finally {
      await release('drain');
      await release('finish');
      await context.close();
    }
  });
