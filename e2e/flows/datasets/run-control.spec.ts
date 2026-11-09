import { test, expect } from '../../lib/scope-actors';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';
import { API, MODEL, ROWS, STANDIN, outputCells, runCells, seedRunControl } from './run-control-seed';
import type { Control } from './run-control-seed';

// ExperimentRunControl.jsx supplies the dialog, buttons and run-control-{UUID} hooks.
// routes/sections/dashboard.jsx mounts the classic wrapper at experiment/:experimentId/data.
const UI_READY = 60_000;
// ExperimentRunControl model: immutable execution binding plus persisted phase.
type Binding = { id: number; experiment_dataset_id: string; workflow_id: string;
  workflow_run_id: string; config_id: string; phase: string };

test('DATA-E2E-021: stop one experiment run and resume without repeating completed work', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-021', area: 'datasets',
    userGoal: 'Stop one run while its sibling finishes, then resume the stopped run without repeating completed results.',
    steps: ['create two synthetic organizations and an experiment backed by a labelled local LLM STANDIN',
      'open the classic experiment page and stop only the selected run in Manage runs',
      'wait for current work to drain and the sibling to finish, then reload the stopped state',
      'refuse foreign organization and sibling workspace reads and writes',
      'resume the selected run through the same dialog and read the completed result'],
    backendChecks: ['actual UI stop and resume requests select exactly one run and agree with persisted states',
      'completed output and evaluation cells and the sibling remain unchanged, and execution identities are stable through resume',
      'both runs complete every row with exactly one STANDIN call per output and no repeated completed work',
      'cross-organization and cross-workspace control requests are refused without changing the owned executions'],
  }),
}, async ({ browser, scopeActors, scopeProbe: probe, request }, testInfo) => {
  // 6 ASYNC_JOB polls (360s) + 8 UI_READY actions (480s) + 120s public seeding.
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
    await expect.poll(() => count('selected'), POLL.ASYNC_JOB).toBe(20);
    const runs = (await read()).result.runs;
    expect(runs.map(run => run.name).sort()).toEqual([`selected-${MODEL}`, `sibling-${MODEL}`]);
    selectedId = runs.find(run => run.name === `selected-${MODEL}`)!.run_id;
    siblingId = runs.find(run => run.name === `sibling-${MODEL}`)!.run_id;
    await expect.poll(async () => (await bindings()).length, POLL.ASYNC_JOB).toBe(2);
    const beforeBindings = await bindings();
    const identities = (rows: Binding[]) => rows.map(({ phase: _phase, ...identity }) => identity);
    expect(new Set(beforeBindings.map(row => row.workflow_run_id)).size).toBe(2);
    const finishedBeforeStop = (await outputCells(probe, selectedId)).filter(cell => cell.status === 'pass');
    expect(finishedBeforeStop).toHaveLength(10);
    expect(finishedBeforeStop.map(cell => cell.value)).toEqual(Array(10).fill(`CONTROL:${suffix}:selected`));
    await testInfo.attach('execution-identities', { contentType: 'application/json',
      body: JSON.stringify({ beforeBindings, finishedBeforeStop, selectedId, siblingId }) });
    await page.goto(`/dashboard/develop/experiment/${seeded.experimentId}/data`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: 'Manage runs', exact: true }).click();
    const selectedRow = page.getByTestId(`run-control-${selectedId}`);
    const siblingRow = page.getByTestId(`run-control-${siblingId}`);
    await expect(selectedRow.getByText('Running', { exact: true })).toBeVisible({ timeout: UI_READY });
    await test.step('backend check 1: native selected stop persists after draining', async () => {
      const stopped = page.waitForResponse(response => new URL(response.url()).pathname === `${endpoint}stop/`
        && response.request().method() === 'POST', { timeout: UI_READY });
      await selectedRow.getByRole('button', { name: 'Stop', exact: true }).click();
      const response = await stopped;
      expect(response.status()).toBe(202);
      expect(response.request().postDataJSON()).toEqual({ run_ids: [selectedId] });
      expect((await response.json()).result.runs.map((run: { run_id: string }) => run.run_id)).toEqual([selectedId]);
      await release('drain');
      await expect.poll(async () => (await read()).result.runs.map(run => [run.name, run.status]).sort(), POLL.ASYNC_JOB)
        .toEqual([[`selected-${MODEL}`, 'STOPPED'], [`sibling-${MODEL}`, 'COMPLETED']]);
      await expect.poll(async () => (await bindings()).map(row => row.phase).sort(), POLL.ASYNC_JOB)
        .toEqual(['COMPLETED', 'STOPPED']);
      expect(await probe.pg(`SELECT name,status FROM model_hub_experimentdatasettable
        WHERE experiment_id=$1 ORDER BY name`, [seeded.experimentId]))
        .toEqual([{ name: `selected-${MODEL}`, status: 'Cancelled' }, { name: `sibling-${MODEL}`, status: 'Completed' }]);
      expect(await probe.pg(`SELECT status FROM model_hub_experimentstable WHERE id=$1`, [seeded.experimentId]))
        .toEqual([{ status: 'Cancelled' }]);
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.getByRole('button', { name: 'Manage runs', exact: true }).click();
      await expect(selectedRow.getByText('Stopped', { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(siblingRow.getByText('Completed', { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(siblingRow.getByRole('button')).toHaveCount(0, { timeout: UI_READY });
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
      const resumed = page.waitForResponse(response => new URL(response.url()).pathname === `${endpoint}resume/`
        && response.request().method() === 'POST', { timeout: UI_READY });
      await selectedRow.getByRole('button', { name: 'Resume', exact: true }).click();
      const response = await resumed;
      expect(response.status()).toBe(202);
      expect(response.request().postDataJSON()).toEqual({ run_ids: [selectedId] });
      await expect.poll(async () => (await bindings()).find(row => row.experiment_dataset_id === selectedId)!.phase,
        POLL.ASYNC_JOB).toBe('RUNNING');
      expect(await probe.pg(`SELECT status FROM model_hub_experimentdatasettable WHERE id=$1`, [selectedId]))
        .toEqual([{ status: 'Running' }]);
      expect(await probe.pg(`SELECT status FROM model_hub_experimentstable WHERE id=$1`, [seeded.experimentId]))
        .toEqual([{ status: 'Running' }]);
      await expect(selectedRow.getByText('Running', { exact: true })).toBeVisible({ timeout: UI_READY });
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
      await expect(selectedRow.getByText('Completed', { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(selectedRow.getByRole('button')).toHaveCount(0, { timeout: UI_READY });
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
    try {
      await expect.poll(async () => (await (await request.get(`${STANDIN}/calls`, {
        params: { group: `CONTROL:${suffix}:selected` },
      })).json()).count, POLL.ASYNC_JOB).toBe(20);
      const runs = (await read()).result.runs;
      const selectedId = runs.find(run => run.name === `selected-${MODEL}`)!.run_id;
      const siblingId = runs.find(run => run.name === `sibling-${MODEL}`)!.run_id;
      await page.goto(`/dashboard/develop/experiment/${seeded.experimentId}/data`, { waitUntil: 'domcontentloaded' });
      await page.getByRole('button', { name: 'Manage runs', exact: true }).click();
      const stopped = page.waitForResponse(response => new URL(response.url()).pathname === `${endpoint}stop/`
        && response.request().method() === 'POST', { timeout: UI_READY });
      await page.getByTestId(`run-control-${selectedId}`).getByRole('button', { name: 'Stop', exact: true }).click();
      expect((await stopped).status()).toBe(202);
      await release('drain');
      await expect.poll(async () => (await read()).result.runs.find(run => run.run_id === selectedId)!.status,
        POLL.ASYNC_JOB).toBe('STOPPED');
      await expect.poll(() => probe.pg('SELECT status FROM model_hub_experimentstable WHERE id=$1', [seeded.experimentId]),
        POLL.ASYNC_JOB).toEqual([{ status: 'Cancelled' }]);
      const sibling = await runCells(probe, siblingId);
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
