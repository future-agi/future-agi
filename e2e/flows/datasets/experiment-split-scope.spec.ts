import { test, expect } from '../../lib/scope-actors';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';
import { CREATE, ROWS, STANDIN, resultCells, seedScope } from './experiment-scope-seed';

// RunExperiment and DataStep native controls; existing split-name test id.
const UI_READY = 60_000;
// DatasetSplitWriteSerializer and DatasetSplitMembershipSerializer, model_hub/urls.py.
const splitsPath = (id: string) => `/model-hub/datasets/${id}/splits/`;
type Split = { result: { id: string; name: string; row_count: number } };
type Detail = { result: { run_scope: { split_ids: string[]; selected_row_ids: string[];
  selected_count: number; eligible_count: number; source_count: number } } };

test('EXP-E2E-006: run only the live members of a dataset split', {
  tag: ['@flow'],
  annotation: flowAnnotation({ id: 'EXP-E2E-006', area: 'datasets',
    userGoal: 'Choose a dataset split in New experiment and run exactly its saved member rows.',
    steps: [
      'seed a six-row dataset, prompt and evaluation with a labelled loopback LLM STANDIN',
      'create and assign a three-row split through the real split APIs',
      'open New experiment, select that split, and submit the wizard',
      'read the server scope receipt and verify the exact persisted snapshot and completed output rows',
      'refuse foreign, wrong-workspace and deleted split identifiers before any experiment or snapshot writes',
    ],
    backendChecks: [
      'the live split API and wizard count report exactly the three selected source rows',
      'the wizard POST and persisted scope retain the split id and exactly its member ids',
      'the snapshot copies exactly the member source values and completed output and score rows have that cardinality',
      'foreign, wrong-workspace and deleted split requests return 400 without changing experiment, dataset, row or cell counts',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe, request }, testInfo) => {
  // 120s seeding + 8 UI_READY actions + ASYNC_JOB completion + 60s denials.
  test.setTimeout(720_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const a = scopes.ownerA;
  const seed = await seedScope(a, probe, suffix, scopes);
  const selected = [seed.rowIds[0], seed.rowIds[2], seed.rowIds[4]];
  const splitName = `smoke-${suffix}`;
  const split = (await a.api.post<Split>(splitsPath(seed.datasetId), { name: splitName })).result;
  await a.api.post(`${splitsPath(seed.datasetId)}membership/`, { row_ids: selected, add: [split.id] });
  const unavailable: string[] = [];
  for (const actor of [scopes.ownerB, scopes.withWorkspace(a, scopes.emptyWorkspace.id)]) {
    const other = await actor.api.post<{ result: { dataset_id: string } }>(
      '/model-hub/develops/create-dataset-manually/', {
        dataset_name: `e2e-split-foreign-${suffix}`, number_of_rows: 1, number_of_columns: 1,
      });
    unavailable.push((await actor.api.post<Split>(splitsPath(other.result.dataset_id), { name: 'foreign' })).result.id);
  }
  const removed = (await a.api.post<Split>(splitsPath(seed.datasetId), { name: `removed-${suffix}` })).result;
  expect((await scopes.send(a, 'DELETE', `${splitsPath(seed.datasetId)}${removed.id}/`)).status).toBe(200);
  unavailable.push(removed.id);
  await testInfo.attach('seeded-identities', { contentType: 'application/json', body: JSON.stringify({
    ...seed, split, selected, unavailable, organizationId: a.organizationId, workspaceId: a.workspaceId,
  }) });
  expect(await (await request.get(`${STANDIN}/health`)).json()).toEqual({ kind: 'LLM STANDIN', upstreams: [] });
  const current = await a.api.get<{ result: { splits: Array<{ id: string; row_count: number }> } }>(splitsPath(seed.datasetId));
  expect(current.result.splits).toEqual([expect.objectContaining({ id: split.id, row_count: selected.length })]);
  const context = await scopes.openContext(browser, a);
  await context.route('**/*', route => new URL(route.request().url()).hostname === 'localhost'
    ? route.continue() : route.abort('blockedbyclient'));
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  let submitted: Record<string, unknown> = {};
  let experimentId = '';
  try {
    const query = new URLSearchParams({ tab: 'experiments', new_experiment: '1', source: 'prompt',
      prompt_id: seed.promptId, prompt_version: seed.promptVersion, split: splitName });
    await test.step('configure and choose live split', async () => {
      await page.goto(`/dashboard/develop/${seed.datasetId}?${query}`, { waitUntil: 'domcontentloaded' });
      await page.getByRole('textbox', { name: /^Experiment name/ }).fill(`e2e-split-scope-${suffix}`);
      await page.getByRole('button', { name: /^Continue/ }).click();
      const chip = page.getByTestId(`split-${splitName}`);
      await expect(chip).toHaveAttribute('aria-pressed', 'true', { timeout: UI_READY });
      await chip.click();
      await expect(chip).toHaveAttribute('aria-pressed', 'false', { timeout: UI_READY });
      await chip.click();
      await expect(page.getByText(`${selected.length} examples (${splitName})`, { exact: true })).toBeVisible({ timeout: UI_READY });
      await page.getByRole('button', { name: /^Continue/ }).click();
      await expect(page.getByText(seed.evalName, { exact: true }).first()).toBeVisible({ timeout: UI_READY });
      await page.getByRole('button', { name: /^Continue/ }).click();
    }, { timeout: UI_READY * 4 });
    await test.step('submit and read persisted scope', async () => {
      const posted = page.waitForResponse(response => new URL(response.url()).pathname === CREATE
        && response.request().method() === 'POST', { timeout: UI_READY });
      await page.getByRole('button', { name: /^Run experiment/ }).click();
      const response = await posted;
      expect(response.status(), await response.text()).toBe(200);
      submitted = response.request().postDataJSON();
      expect(submitted.split_ids).toEqual([split.id]);
      experimentId = /experiment_id=([0-9a-f-]{36})/.exec((await response.json()).result)?.[1] ?? '';
      expect(experimentId).not.toBe('');
      await expect(page.getByText(`Server selected ${selected.length} of ${selected.length} eligible rows (${ROWS} source rows)`, { exact: true })).toBeVisible({ timeout: UI_READY });
      const scope = (await a.api.get<Detail>(`${CREATE}${experimentId}/`)).result.run_scope;
      expect(scope.split_ids).toEqual([split.id]);
      expect([...scope.selected_row_ids].sort()).toEqual([...selected].sort());
      expect(scope.selected_count).toBe(selected.length);
      expect(scope.eligible_count).toBe(selected.length);
      const copied = await probe.pg<{ value: string }>(`SELECT c.value FROM model_hub_cell c
        JOIN model_hub_column col ON col.id=c.column_id
        JOIN model_hub_experimentstable e ON e.snapshot_dataset_id=c.dataset_id
        WHERE e.id=$1 AND col.source_id=$2 AND NOT c.deleted ORDER BY c.value`, [experimentId, seed.columnId]);
      expect(copied.map(cell => cell.value)).toEqual(selected.map(id => `source-${id}`).sort());
      await testInfo.attach('wire-and-snapshot', { contentType: 'application/json', body: JSON.stringify({ submitted, experimentId, scope, copied }) });
    }, { timeout: UI_READY * 2 });
    await expect.poll(async () => {
      const cells = await resultCells(probe, experimentId);
      return cells.length === selected.length * 2 && cells.every(cell => cell.status === 'pass');
    }, POLL.ASYNC_JOB).toBe(true);
    const cells = await resultCells(probe, experimentId);
    expect(cells.filter(cell => cell.source === 'experiment').map(cell => cell.value)).toEqual(selected.map(() => `SCOPE:${suffix}`));
    expect(cells.filter(cell => cell.source === 'experiment_evaluation').map(cell => cell.value)).toEqual(selected.map(() => 'Passed'));
    await expect(page.locator('.ag-cell').filter({ hasText: `SCOPE:${suffix}` }).first()).toBeVisible({ timeout: UI_READY });
    const counts = () => probe.pg(`SELECT
      (SELECT count(*)::int FROM model_hub_experimentstable WHERE dataset_id=$2) AS experiments,
      (SELECT count(*)::int FROM model_hub_dataset WHERE organization_id=$1) AS datasets,
      (SELECT count(*)::int FROM model_hub_row r JOIN model_hub_dataset d ON d.id=r.dataset_id WHERE d.organization_id=$1) AS rows,
      (SELECT count(*)::int FROM model_hub_cell c JOIN model_hub_dataset d ON d.id=c.dataset_id WHERE d.organization_id=$1) AS cells`, [a.organizationId, seed.datasetId]);
    const before = await counts();
    for (const splitId of unavailable) {
      const denied = await scopes.send(a, 'POST', CREATE, {
        ...submitted, name: `e2e-denied-${splitId}`, split_ids: [splitId],
      });
      expect(denied.status).toBe(400);
      expect(await counts()).toEqual(before);
    }
    expect((await scopes.send(scopes.ownerB, 'GET', `${CREATE}${experimentId}/`)).status).toBe(404);
    await testInfo.attach('completed-and-refused', { contentType: 'application/json', body: JSON.stringify({ cells, before, after: await counts(), unavailable }) });
  } catch (error) {
    await testInfo.attach('split-scope-page', { contentType: 'image/png', body: await page.screenshot({ fullPage: true }) });
    await testInfo.attach('split-scope-page-text', { contentType: 'text/plain', body: await page.locator('body').innerText() });
    throw error;
  } finally {
    await context.close();
  }
});
