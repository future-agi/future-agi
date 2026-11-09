import { randomUUID } from 'node:crypto';
import { test, expect } from '../../lib/scope-actors';
import { inspectManagedMock, registerMockModel } from '../../lib/managed-mock';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// model_hub/urls.py; UserEvalMutation/UpdateRequestSerializer and JuryReportQuerySerializer.
const datasetPath = (id: string, action: string) => `/model-hub/develops/${id}/${action}/`;
const composite = (id: string) => `/model-hub/eval-templates/${id}/composite/`;
const UI_READY = 60_000;
const EXPECTED_AGREEMENT = 0.5; // JURY_NEG_AGREEMENT
const EXPECTED_REFUSAL = 404; // JURY_NEG_SCOPE
const EXPECTED_SEATS = 2; // JURY_NEG_VERSION
const EXPECTED_STEPS_COLUMN = 'evidence_steps'; // JURY_NEG_BINDING
const SUMMARY_COLUMN_TYPE = 'text'; // JURY_NEG_SWITCH

test('EVAL-E2E-074: bind a saved jury to dataset rows and inspect disagreement', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-074', area: 'evals',
    userGoal: 'Run a saved jury on dataset steps and checks, then inspect agreement and navigate the rows where judges disagree',
    steps: ['seed two synthetic rows and a composite with a step judge, stored check and API-pinned jury version',
      'open the dataset evaluation drawer and map steps and checks to real columns',
      'verify the stored version label with no selector, save, reopen after live changes, and run through the native dataset action',
      'inspect pairwise agreement and open the disagreement row in the saved report',
      'reject foreign organization and workspace reads, writes and version references',
      'seed a summary pin and then the original pin through the scoped API, preserve each through the locked picker, and rerun through the native dataset action'],
    backendChecks: ['the browser mapping save and reopen preserve the API-seeded pin and selected evidence columns after live jury changes',
      'the stored historical jury runs after live aggregation changes and persists both seats with a numeric result column',
      'the run report aggregates two stored rows with exact pairwise agreement and disagreement navigation',
      'foreign organization and workspace requests cannot read or change the jury binding or report',
      'each API-seeded pin survives a no-op browser reopen and reruns into the same single result column with the stored version type and values'],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Seed120 + drawer3*UI60 + reopen2*UI60 + runEVAL90 + report2*UI60 + scope90
  // + switch2*(drawer2*UI60 + runEVAL90) = 1140 seconds.
  test.setTimeout(1_140_000);
  const ui = expect.configure({ timeout: UI_READY });
  const a = scopes.ownerA;
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const EXPECTED_SOURCE_ROW = 1; // JURY_NEG_ROW
  const name = `e2e-jury-rows-${suffix}`;
  const routing = inspectManagedMock({ evalBackground: true });
  const model = await registerMockModel(scopes.owners[0], probe, () => expect(inspectManagedMock({ evalBackground: true })).toEqual(routing));
  const datasetId = (await a.api.post<{ result: { dataset_id: string } }>('/model-hub/develops/create-empty-dataset/', { new_dataset_name: name })).result.dataset_id;
  for (const column of ['output', 'steps', 'checks', 'evidence_steps', 'evidence_checks']) {
    await a.api.post(datasetPath(datasetId, 'add_static_column'), { new_column_name: column, column_type: 'text' });
  }
  const rowIds = [randomUUID(), randomUUID()];
  await a.api.post(datasetPath(datasetId, 'add_rows'), { rows: rowIds.map((id, index) => ({ id, cells: [
    { column_name: 'output', value: `row-${index}-${suffix}` },
    { column_name: 'steps', value: JSON.stringify([{ kind: 'llm', mapping: { output: 'old-input' } }]) },
    { column_name: 'checks', value: JSON.stringify([{ key: 'safe', passed: true }]) },
    { column_name: 'evidence_steps', value: JSON.stringify([{ kind: 'llm', mapping: { output: `step-${index}-${suffix}` } }]) },
    { column_name: 'evidence_checks', value: JSON.stringify([{ key: 'safe', passed: index === 0 }]) },
  ] })) });
  const columns = await probe.pg<{ id: string; name: string }>('SELECT id,name FROM model_hub_column WHERE dataset_id=$1 AND NOT deleted', [datasetId]);
  const col = (name: string) => columns.find(column => column.name === name)!.id;
  const child = (await a.api.post<{ result: { id: string } }>('/model-hub/eval-templates/create-v2/', {
    name: `e2e-jury-child-${suffix}`, eval_type: 'llm', model: model.model, output_type: 'pass_fail',
    instructions: 'Reply with exactly this JSON: {"result":"Pass","explanation":"jury saw {{output}}"}',
  })).result.id;
  const parent = (await a.api.post<{ result: { id: string } }>('/model-hub/eval-templates/create-composite/', {
    name, child_template_ids: [child], aggregation_enabled: true, aggregation_function: 'avg',
  })).result.id;
  const jury = { seats: [
    { id: 'judge', kind: 'eval', child_id: child, level: 'step', step_kind: 'llm', reduce: 'all_pass', weight: 1 },
    { id: 'safe', kind: 'check', check_key: 'safe', weight: 1 },
  ] };
  expect((await scopes.send(a, 'PUT', `${composite(parent)}jury/config/`, jury)).status).toBe(200);
  const [savedVersion] = await probe.pg<{ id: string; version_number: number }>('SELECT id,version_number FROM model_hub_eval_template_version WHERE eval_template_id=$1 AND is_default AND NOT deleted', [parent]);
  await a.api.post(datasetPath(datasetId, 'add_user_eval'), { name, template_id: parent, run: false,
    pinned_version_id: savedVersion.id,
    config: { mapping: { output: col('output'), steps: col('steps'), checks: col('checks') } } });
  const [binding] = await probe.pg<{ id: string }>('SELECT id FROM model_hub_userevalmetric WHERE dataset_id=$1 AND NOT deleted', [datasetId]);
  await testInfo.attach('seeded-jury-identities', { contentType: 'application/json', body: JSON.stringify({ datasetId, parent, child, rowIds, binding, savedVersion, organizationId: a.organizationId, workspaceId: a.workspaceId, externalSystem: 'loopback LLM STANDIN' }) });
  const context = await scopes.openContext(browser, a);
  await context.route(url => /^https?:$/.test(url.protocol) && !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname), route => route.abort());
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  const openList = async () => {
    await page.goto(`/dashboard/develop/${datasetId}`, { waitUntil: 'domcontentloaded' });
    const more = page.locator('[data-action-bar="grid"]').getByRole('button', { name: 'More actions', exact: true });
    const evaluate = page.getByRole('menuitem', { name: /^Evaluate/ });
    await expect(async () => {
      if (await evaluate.isVisible()) await page.keyboard.press('Escape');
      await more.click();
      await expect(evaluate).toBeEnabled({ timeout: 2_000 });
    }).toPass({ timeout: UI_READY });
    await evaluate.click();
    await ui(page.getByRole('dialog', { name: 'Evaluations', exact: true }).getByText(name, { exact: true }).filter({ visible: true })).toBeVisible();
  };
  await openList();
  // SavedEvalsList: name block -> clickable row -> card (including inline run confirmation).
  const savedRow = () => page.getByRole('dialog', { name: 'Evaluations', exact: true }).getByText(name, { exact: true }).filter({ visible: true }).locator('..').locator('..').locator('..');
  await savedRow().getByRole('button', { name: 'Edit mapping & config', exact: true }).click();
  // DatasetTestMode.jsx renders one Select column textbox in each variable row.
  for (const [key, target] of [['steps', 'evidence_steps'], ['checks', 'evidence_checks']]) {
    const variableRow = page.getByText(key, { exact: true }).filter({ visible: true }).locator('..').locator('..').filter({ has: page.getByPlaceholder('Select column') });
    await ui(variableRow).toHaveCount(1);
    await variableRow.getByPlaceholder('Select column').click();
    await page.getByPlaceholder('Search columns…').fill(target);
    await page.getByText(target, { exact: true }).filter({ visible: true }).last().click();
  }
  const expectLockedPin = async (version: { version_number: number }) => {
    await ui(page.getByText(`Version label: V${version.version_number}`, { exact: true })).toBeVisible();
    await ui(page.getByRole('combobox', { name: 'Saved eval version' })).toHaveCount(0);
  };
  await expectLockedPin(savedVersion);
  const editPath = datasetPath(datasetId, `edit_and_run_user_eval/${binding.id}`);
  const edited = page.waitForResponse(response => new URL(response.url()).pathname === editPath && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Update Evaluation', exact: true }).click();
  const editResponse = await edited;
  expect(editResponse.status()).toBe(200);
  expect(editResponse.request().postDataJSON().pinned_version_id).toBe(savedVersion.id);
  const [storedBinding] = await probe.pg<{ config: { mapping: Record<string, string> }; pinned_version_id: string }>('SELECT config,pinned_version_id FROM model_hub_userevalmetric WHERE id=$1 AND organization_id=$2 AND workspace_id=$3', [binding.id, a.organizationId, a.workspaceId]);
  expect(storedBinding.config.mapping).toEqual({ output: col('output'), steps: col(EXPECTED_STEPS_COLUMN), checks: col('evidence_checks') });
  expect(storedBinding.pinned_version_id).toBe(savedVersion.id);
  // Change only the current definition. The selected version must keep its two seats.
  expect((await scopes.send(a, 'PUT', `${composite(parent)}jury/config/`, { seats: [{ id: 'new-check', kind: 'check', check_key: 'safe', weight: 1 }] })).status).toBe(200);
  // CompositeEvalDetailView.patch accepts partial aggregation settings.
  expect((await scopes.send(a, 'PATCH', composite(parent), { aggregation_enabled: false })).status).toBe(200);
  await openList();
  await savedRow().getByRole('button', { name: 'Edit mapping & config', exact: true }).click();
  for (const [key, target] of [['steps', 'evidence_steps'], ['checks', 'evidence_checks']]) {
    const variableRow = page.getByText(key, { exact: true }).filter({ visible: true }).locator('..').locator('..').filter({ has: page.getByPlaceholder('Select column') });
    await ui(variableRow.getByPlaceholder('Select column')).toHaveValue(target);
  }
  await expectLockedPin(savedVersion);
  await ui(page.getByRole('button', { name: 'Update Evaluation', exact: true })).toBeDisabled();
  const [reopenedBinding] = await probe.pg<{ config: { mapping: Record<string, string> }; pinned_version_id: string }>('SELECT config,pinned_version_id FROM model_hub_userevalmetric WHERE id=$1 AND organization_id=$2 AND workspace_id=$3', [binding.id, a.organizationId, a.workspaceId]);
  expect(reopenedBinding.config).toEqual(storedBinding.config);
  expect(reopenedBinding.pinned_version_id).toBe(savedVersion.id);
  await model.assertReady();
  await openList();
  const started = page.waitForResponse(response => new URL(response.url()).pathname === datasetPath(datasetId, 'start_evals_process'));
  await savedRow().getByRole('button', { name: 'Run', exact: true }).click();
  await ui(savedRow().getByText('Run this evaluation on all rows?', { exact: true })).toBeVisible();
  await savedRow().getByRole('button', { name: 'Run', exact: true }).filter({ hasText: 'Run' }).click();
  expect((await started).status()).toBe(200);
  await expect.poll(async () => (await probe.pg<{ status: string }>('SELECT status FROM model_hub_userevalmetric WHERE id=$1', [binding.id]))[0].status, POLL.EVAL_RESULT).toBe('Completed');
  const resultColumns = await probe.pg<{ data_type: string }>('SELECT data_type FROM model_hub_column WHERE dataset_id=$1 AND source_id=$2 AND NOT deleted', [datasetId, binding.id]);
  expect(resultColumns.map(column => column.data_type)).toEqual(['float']); // JURY_NEG_COLUMN
  const report = (await a.api.get<{ result: any }>(`${composite(parent)}jury/`, { dataset_id: datasetId })).result;
  expect(report.version_id).toBe(savedVersion.id);
  expect(report.children).toHaveLength(EXPECTED_SEATS);
  expect(report.n).toBe(2);
  expect(report.pairs[0].agreement).toBe(EXPECTED_AGREEMENT);
  expect(report.split_rows).toEqual([rowIds[1]]);
  expect(report.rows.map((row: any) => row.aggregate_score)).toEqual([1, 0.5]);
  expect(report.rows.map((row: any) => row.seats[0].reason)).toEqual([`jury saw step-0-${suffix}`, `jury saw step-1-${suffix}`]);
  const storedRows = await probe.pg<{ data: any }>('SELECT data FROM model_hub_evaluation WHERE eval_config->>\'jury_run_id\'=$1 AND organization_id=$2 AND workspace_id=$3 ORDER BY created_at', [report.run_id, a.organizationId, a.workspaceId]);
  expect(storedRows.map(row => row.data.jury.seats.map((seat: any) => seat.passed))).toEqual([[true, true], [true, false]]);
  await page.goto(`/dashboard/evaluations/${parent}`);
  const panel = page.getByRole('region', { name: 'Dataset jury report' });
  await ui(panel).toBeVisible({ timeout: UI_READY });
  await ui(panel.getByRole('status')).toHaveText('Judges disagree on 1 of 2 rows');
  await ui(panel.getByRole('table', { name: 'Pairwise jury agreement' }).locator('tbody tr td')).toHaveText(['judge / safe', '50%', '2']);
  await panel.getByRole('button', { name: 'Open disagreement rows', exact: true }).click();
  await ui(panel.getByRole('combobox', { name: 'Jury row' })).toHaveText('Row 2 — judges disagree');
  await ui(panel.getByRole('table', { name: 'Dataset row jury verdicts' }).locator('tbody tr td:nth-child(2)')).toHaveText(['Pass', 'Fail']);
  for (const outsider of [scopes.ownerB, scopes.withWorkspace(a, scopes.emptyWorkspace.id)]) {
    for (const [method, path, body] of [
      ['GET', `${composite(parent)}jury/?run_id=${report.run_id}`, undefined],
      ['GET', `${composite(parent)}jury/config/?version_id=${savedVersion.id}`, undefined],
      ['POST', editPath, { config: { mapping: storedBinding.config.mapping }, run: false }],
    ] as const) {
      expect((await scopes.send(outsider, method, path, body)).status).toBe(EXPECTED_REFUSAL);
    }
  }
  await testInfo.attach('saved-dataset-jury-report', { contentType: 'application/json', body: JSON.stringify({ report, storedBinding, reopenedBinding, resultColumns, storedRows }) });
  await page.screenshot({ path: testInfo.outputPath('dataset-jury.png'), fullPage: true });
  await testInfo.attach('dataset-jury.png', { path: testInfo.outputPath('dataset-jury.png'), contentType: 'image/png' });
  // row-view.js fetchRowInView posts this existing source-row read.
  const sourceRead = page.waitForResponse(response => new URL(response.url()).pathname === datasetPath(datasetId, 'get-row-data') && response.request().postDataJSON()?.row_id === rowIds[1]);
  await panel.getByRole('link', { name: 'Open dataset row', exact: true }).click();
  const sourceResponse = await sourceRead;
  expect(sourceResponse.status()).toBe(200);
  expect((await sourceResponse.json()).result.current.row_id).toBe(rowIds[EXPECTED_SOURCE_ROW]);
  await expect(page.getByTestId('row-panel')).toBeVisible({ timeout: UI_READY });
  expect(new URL(page.url()).searchParams.get('row')).toBe(rowIds[EXPECTED_SOURCE_ROW]);
  // Save the same seats while live aggregation is off: a summary version of the jury.
  const [resultColumn] = await probe.pg<{ id: string }>('SELECT id FROM model_hub_column WHERE dataset_id=$1 AND source_id=$2 AND NOT deleted', [datasetId, binding.id]);
  expect((await scopes.send(a, 'PUT', `${composite(parent)}jury/config/`, jury)).status).toBe(200);
  const [summaryVersion] = await probe.pg<{ id: string; version_number: number; config_snapshot: any }>('SELECT id,version_number,config_snapshot FROM model_hub_eval_template_version WHERE eval_template_id=$1 AND NOT deleted ORDER BY version_number DESC LIMIT 1', [parent]);
  expect(summaryVersion.config_snapshot.aggregation_enabled).toBe(false);
  expect(summaryVersion.config_snapshot.jury.seats).toHaveLength(EXPECTED_SEATS);
  const seedPinAndRun = async (version: { id: string; version_number: number }, dataType: string) => {
    // The lock removes UI pin switching; scoped API setup establishes the
    // stored version that the real browser displays with no-op Update disabled.
    expect((await scopes.send(a, 'POST', editPath, {
      template_id: parent, pinned_version_id: version.id, run: false,
      config: { mapping: storedBinding.config.mapping },
    })).status).toBe(200);
    await openList();
    await savedRow().getByRole('button', { name: 'Edit mapping & config', exact: true }).click();
    await expectLockedPin(version);
    await ui(page.getByRole('button', { name: 'Update Evaluation', exact: true })).toBeDisabled();
    const [pinned] = await probe.pg<{ config: { mapping: Record<string, string> }; pinned_version_id: string }>('SELECT config,pinned_version_id FROM model_hub_userevalmetric WHERE id=$1 AND organization_id=$2 AND workspace_id=$3', [binding.id, a.organizationId, a.workspaceId]);
    expect(pinned.pinned_version_id).toBe(version.id);
    expect(pinned.config.mapping).toEqual(storedBinding.config.mapping);
    await openList();
    // SavedEvalsList: a completed binding offers Re-run with an overwrite confirmation.
    const rerun = page.waitForResponse(response => new URL(response.url()).pathname === datasetPath(datasetId, 'start_evals_process'));
    await savedRow().getByRole('button', { name: 'Re-run', exact: true }).click();
    await ui(savedRow().getByText('Re-run? This will overwrite previous results.', { exact: true })).toBeVisible();
    await savedRow().getByRole('button', { name: 'Re-run', exact: true }).filter({ hasText: 'Re-run' }).click();
    expect((await rerun).status()).toBe(200);
    await expect.poll(async () => (await probe.pg<{ status: string }>('SELECT status FROM model_hub_userevalmetric WHERE id=$1', [binding.id]))[0].status, POLL.EVAL_RESULT).toBe('Completed');
    // Exactly one result column: the original one, retyped for the selected version.
    const switchedColumns = await probe.pg<{ id: string; data_type: string }>('SELECT id,data_type FROM model_hub_column WHERE dataset_id=$1 AND source_id=$2 AND NOT deleted', [datasetId, binding.id]);
    expect(switchedColumns).toEqual([{ id: resultColumn.id, data_type: dataType }]);
    const cells = (await probe.pg<{ value: string; status: string; value_infos: unknown }>('SELECT c.value,c.status,c.value_infos FROM model_hub_cell c JOIN model_hub_row r ON r.id=c.row_id WHERE c.column_id=$1 AND NOT c.deleted ORDER BY r."order"', [resultColumn.id]))
      .map(cell => ({ ...cell, value_infos: (typeof cell.value_infos === 'string' ? JSON.parse(cell.value_infos) : cell.value_infos) as any }));
    expect(cells.map(cell => cell.status)).toEqual(['pass', 'pass']);
    expect(cells.map(cell => cell.value_infos.aggregation_enabled)).toEqual([dataType === 'float', dataType === 'float']);
    const switchedReport = (await a.api.get<{ result: any }>(`${composite(parent)}jury/`, { dataset_id: datasetId })).result;
    expect(switchedReport.version_id).toBe(version.id);
    return { switchedColumns, cells, switchedReport };
  };
  const toSummary = await seedPinAndRun(summaryVersion, SUMMARY_COLUMN_TYPE);
  expect(toSummary.cells.map(cell => cell.value)).toEqual(toSummary.cells.map(cell => cell.value_infos.summary ?? ''));
  const toScore = await seedPinAndRun(savedVersion, 'float');
  expect(toScore.cells.map(cell => cell.value)).toEqual(['1.0000', '0.5000']);
  expect(toScore.switchedReport.rows.map((row: any) => row.aggregate_score)).toEqual([1, 0.5]);
  await testInfo.attach('pin-switch-result-column', { contentType: 'application/json', body: JSON.stringify({ resultColumn, summaryVersion: { id: summaryVersion.id, version_number: summaryVersion.version_number }, toSummary, toScore }) });
  await context.close();
});
