import { test, expect } from '../../lib/scope-actors';
import { inspectManagedMock, registerMockModel } from '../../lib/managed-mock';
import { flowAnnotation } from '../../lib/flow-meta';

// model_hub/serializers/contracts.py: CompositeEvalCreateRequestSerializer.
const CREATE = '/model-hub/eval-templates/create-composite/';
// model_hub/urls.py: composite-jury-config and composite-jury-report.
const composite = (id: string) => `/model-hub/eval-templates/${id}/composite/`;
const UI_READY = 60_000;

test.beforeAll(() => { inspectManagedMock(); });

test('EVAL-E2E-068: save and run a scoped composite jury with step judges and a stored check', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-068', area: 'evals',
    userGoal: 'Configure several judges and a stored check, then inspect their saved verdicts without rescoring',
    steps: ['create a child evaluator and composite in an isolated organization',
      'add two judges, make the second judge evaluate each LLM step, and add a stored check',
      'save the jury and reload its settings', 'run mapped input through the loopback LLM STANDIN',
      'read the saved jury verdicts', 'refuse access from another organization and workspace'],
    backendChecks: ['the UI save persists exactly three seats and the step reducer',
      'real evaluator calls produce persisted Pass, Pass, Fail verdicts and their report',
      'foreign organization and workspace reads, writes and executions are refused'],
  }),
}, async ({ browser, scopeActors, scopeProbe }, testInfo) => {
  // Four independently bounded UI phases (4 * 60 s), plus actor/model seeding
  // and synchronous judge execution (90 s), with 90 s teardown headroom.
  test.setTimeout(420_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const a = scopeActors.ownerA;
  const model = await registerMockModel(scopeActors.owners[0], scopeProbe);
  const verdict = `jury-verdict-${suffix}`;
  // EvalTemplateCreateV2RequestSerializer; echo prompt pattern from eval-task.spec.ts.
  const child = await a.api.post<{ result: { id: string } }>('/model-hub/eval-templates/create-v2/', {
    name: `e2e-jury-child-${suffix}`, eval_type: 'llm', model: model.model,
    instructions: `Reply with exactly this JSON: {"result":"Pass","explanation":"${verdict} saw {{output}}"}`,
    output_type: 'pass_fail', pass_threshold: 0.5,
  });
  const parent = await a.api.post<{ result: { id: string } }>(CREATE, {
    name: `e2e-jury-${suffix}`, child_template_ids: [child.result.id],
    aggregation_enabled: true, aggregation_function: 'avg',
  });
  const id = parent.result.id;
  await testInfo.attach('jury-seeded-ids', { contentType: 'application/json', body: JSON.stringify({
    templateId: id, childId: child.result.id, organizationId: a.organizationId,
    workspaceId: a.workspaceId, externalSystem: 'loopback LLM STANDIN', verdict,
  }) });
  const context = await scopeActors.openContext(browser, a);
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (['localhost', '127.0.0.1', '::1'].includes(url.hostname)) await route.continue();
    else await route.abort('blockedbyclient');
  });
  const page = await context.newPage();
  const panel = page.getByRole('region', { name: 'Composite jury' });
  // frontend/src/routes/sections/dashboard.jsx evaluations/:evalId.
  await page.goto(`/dashboard/evaluations/${id}`);
  await expect(panel).toBeVisible({ timeout: UI_READY });
  await test.step('save seats from the browser', async () => {
    await panel.getByRole('button', { name: 'Add judge', exact: true }).click();
    await panel.getByRole('button', { name: 'Add judge', exact: true }).click();
    await panel.getByLabel('Level', { exact: true }).nth(1).click();
    await page.getByRole('option', { name: 'Each step', exact: true }).click();
    await panel.getByRole('button', { name: 'Add check', exact: true }).click();
    await panel.getByLabel('Check key', { exact: true }).fill('safe');
    const saved = page.waitForResponse(response => response.url().endsWith(`${composite(id)}jury/config/`) && response.request().method() === 'PUT');
    await panel.getByRole('button', { name: 'Save jury', exact: true }).click();
    expect((await saved).status()).toBe(200);
  }, { timeout: UI_READY });
  const read = await a.api.get<{ result: { seats: Array<Record<string, unknown>> } }>(`${composite(id)}jury/config/`);
  expect(read.result.seats.map(seat => seat.kind)).toEqual(['eval', 'eval', 'check']);
  expect(read.result.seats[1]).toMatchObject({ level: 'step', step_kind: 'llm', reduce: 'all_pass' });
  const stored = await scopeProbe.pg<{ config: { jury: unknown } }>(
    'SELECT config FROM model_hub_evaltemplate WHERE id=$1 AND organization_id=$2 AND workspace_id=$3',
    [id, a.organizationId, a.workspaceId]);
  expect(stored[0].config.jury).toEqual(read.result);
  await page.reload();
  await expect(panel.getByRole('group')).toHaveCount(3, { timeout: UI_READY });
  await model.assertReady();
  const mapping = { output: `row-${suffix}`, steps: [
    { kind: 'llm', mapping: { output: `step-${suffix}` } },
    { kind: 'tool', mapping: { output: 'not-judged' } },
  ], checks: [{ key: 'safe', passed: false }] };
  await panel.getByLabel('Mapped inputs (JSON)', { exact: true }).fill(JSON.stringify(mapping));
  const executed = page.waitForResponse(response => response.url().endsWith(`${composite(id)}execute/`) && response.request().method() === 'POST', { timeout: 90_000 });
  await panel.getByRole('button', { name: 'Run saved jury', exact: true }).click();
  const response = await executed;
  expect(response.status()).toBe(200);
  const run = await response.json();
  expect(run.result.aggregate_score).toBeCloseTo(2 / 3);
  const runId = run.result.evaluation_id;
  expect(runId).toBeTruthy();
  const report = await a.api.get<{ result: { seats: Array<{ passed: boolean; reason: string }>; disagreement: boolean } }>(
    `${composite(id)}jury/`, { run_id: runId });
  expect(report.result.seats.map(seat => seat.passed)).toEqual([true, true, false]);
  expect(report.result.seats.slice(0, 2).every(seat => seat.reason.includes(verdict))).toBe(true);
  expect(report.result.disagreement).toBe(true);
  const verdictTable = panel.getByRole('table', { name: 'Jury verdicts' });
  await verdictTable.scrollIntoViewIfNeeded();
  await expect(verdictTable).toBeVisible({ timeout: UI_READY });
  await expect(verdictTable.locator('tbody tr td:last-child')).toHaveText(['Pass', 'Pass', 'Fail'], { timeout: UI_READY });
  const results = await scopeProbe.pg<{ data: { jury: unknown } }>(
    'SELECT data FROM model_hub_evaluation WHERE id=$1 AND organization_id=$2 AND workspace_id=$3',
    [runId, a.organizationId, a.workspaceId]);
  expect(results).toHaveLength(1);
  expect(results[0].data.jury).toMatchObject({ disagreement: true, complete: true });
  for (const outsider of [scopeActors.ownerB, scopeActors.withWorkspace(a, scopeActors.emptyWorkspace.id)]) {
    for (const [method, path, data] of [
      ['GET', `${composite(id)}jury/?run_id=${runId}`, undefined],
      ['GET', `${composite(id)}jury/config/`, undefined],
      ['PUT', `${composite(id)}jury/config/`, read.result],
      ['POST', `${composite(id)}execute/`, { mapping }],
    ] as const) {
      const denied = await scopeActors.send(outsider, method, path, data);
      expect(denied.status).toBe(404);
    }
  }
  await testInfo.attach('jury-report', { contentType: 'application/json', body: JSON.stringify({ runId, report: report.result }) });
  await page.screenshot({ path: testInfo.outputPath('jury-report.png'), fullPage: true });
  await testInfo.attach('jury-report.png', { path: testInfo.outputPath('jury-report.png'), contentType: 'image/png' });
});
