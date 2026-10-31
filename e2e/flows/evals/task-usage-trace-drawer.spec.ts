import { request, type Response } from '@playwright/test';
import { test, expect } from '../../lib/mock-model-fixtures';
import { E2E } from '../../lib/env';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { inspectManagedMock } from '../../lib/managed-mock';
import { flowAnnotation } from '../../lib/flow-meta';

const TASK_PATH = '/tracer/eval-task/';
// The Usage tab's runs request: frontend/src/sections/tasks/hooks/useTaskUsage.js
// (useTaskUsageLogs → endpoints.project.getEvalTaskUsage with include_summary=false).
const USAGE_PATH = '/tracer/eval-task/get_usage/';
// The only attribute lib/otlp.ts puts on the llm child by default; the judge's
// single input maps to it, so the child is the one span the task evaluates.
const MAPPED_ATTRIBUTE = 'fi.span.kind';
const UI_READY = 60_000;

// Fields of one run, from futureagi/tracer/views/eval_task.py (get_usage log_items).
interface UsageRun { id: string; eval_name: string | null; trace_id: string | null; span_id: string | null }
interface UsageBody { result: { logs: { results: UsageRun[]; count: number } } }

test.beforeAll(() => { inspectManagedMock({ evalBackground: true }); });
test.use({ evalBackground: true, viewport: { width: 1600, height: 1100 } });

test('EVAL-E2E-006: a task run opens its trace in a drawer from the Usage tab', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-006', area: 'evals',
    userGoal: 'Open the trace behind an eval task result without leaving the task Usage tab',
    steps: ['seed a trace and run an eval task over it through the mock judge',
      'open the task Usage tab and click its run',
      'read the run panel Trace ID and Span ID',
      'click Open trace and read the drawer',
      'press Esc to close the drawer, then Esc again to close the run panel'],
    backendChecks: ['the task completes with exactly two PG results: the evaluated child completed, the unmapped root errored',
      'the Usage runs response carries only the completed run, with the seeded trace and span IDs',
      'the run panel shows the seeded trace and span IDs',
      'the drawer opens on the seeded trace with the evaluated span selected and its Evals tab open',
      'the first Esc closes only the drawer and the second closes the run panel'],
  }),
}, async ({ page, actor, probe, mockModel }, testInfo) => {
  // EVAL_RESULT (90 s) for the Temporal task, then navigation and four UI_READY
  // waits; the runs list reads Postgres, so no CDC budget is spent.
  test.setTimeout(300_000);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `e2e-usage-drawer-${suffix}`;
  const evalName = `e2e-usage-drawer-judge-${suffix}`;
  // Exists nowhere else: finding it on the result proves the task really ran.
  const verdict = `e2e-usage-drawer-verdict-${suffix}`;
  try {
    const seeded = await sendTrace(req, {
      collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey, projectName,
    });
    await expect.poll(async () => Number((await probe.ch<{ n: string }>(
      'SELECT count() AS n FROM spans FINAL WHERE trace_id={t:String}', { t: seeded.traceId }))[0].n),
    POLL.SPAN_VISIBLE).toBe(seeded.spanIds.length);
    const [{ project_id: projectId }] = await probe.ch<{ project_id: string }>(
      'SELECT DISTINCT project_id FROM spans FINAL WHERE trace_id={t:String}', { t: seeded.traceId });
    const evaluatedSpanId = seeded.spanIds[1];
    await testInfo.attach('fixture.json', { contentType: 'application/json', body: JSON.stringify({
      projectId, projectName, organizationId: actor.organizationId, workspaceId: actor.workspaceId,
      traceId: seeded.traceId, spanIds: seeded.spanIds, evaluatedSpanId, evalName }) });

    const taskId = await test.step('run an eval task over the seeded spans', async () => {
      await mockModel.assertReady();
      // Wire bodies as in flows/evals/eval-task.spec.ts (pinned from the serializers).
      const template = await actor.api.post<{ result: { id: string } }>('/model-hub/eval-templates/create-v2/', {
        name: `e2e-usage-drawer-template-${suffix}`, eval_type: 'llm', model: mockModel.model,
        instructions: `Reply with exactly this JSON: {"result": "Pass", "explanation": "${verdict} saw {{output}}"}`,
        output_type: 'pass_fail', pass_threshold: 0.5,
      });
      const config = await actor.api.post<{ result: { id: string } }>('/tracer/custom-eval-config/', {
        project: projectId, eval_template: template.result.id, name: evalName, model: mockModel.model,
        mapping: { output: MAPPED_ATTRIBUTE }, config: { mapping: { output: MAPPED_ATTRIBUTE } },
        error_localizer: false,
      });
      const now = Date.now();
      const created = await actor.api.post<{ result: { id: string } }>(TASK_PATH, {
        name: `e2e-usage-drawer-task-${suffix}`, project: projectId, evals: [config.result.id],
        filters: { project_id: projectId,
          date_range: [new Date(now - 3_600_000).toISOString(), new Date(now + 3_600_000).toISOString()] },
        run_type: 'historical', row_type: 'spans', spans_limit: 100000, sampling_rate: 100,
      });
      return created.result.id;
    });

    const runs = await test.step('storage lane: the evaluated span completed, the root errored', async () => {
      await expect.poll(async () =>
        (await actor.api.get<{ status: string }>(`${TASK_PATH}${taskId}/`)).status, POLL.EVAL_RESULT).toBe('completed');
      const rows = await probe.pg<{ id: string; observation_span_id: string; status: string; eval_explanation: string }>(
        `SELECT id::text AS id, observation_span_id, status, eval_explanation FROM tracer_eval_logger
         WHERE eval_task_id=$1 AND deleted=false`, [taskId]);
      await testInfo.attach('pg-results.json', { contentType: 'application/json', body: JSON.stringify(rows) });
      // The task scores both seeded spans; only the llm child carries the mapped
      // attribute, so the root errors (as in flows/evals/eval-task.spec.ts).
      const bySpan = new Map(rows.map(row => [row.observation_span_id, row]));
      expect(rows.map(row => [row.observation_span_id, row.status]).sort())
        .toEqual([[seeded.spanIds[0], 'errored'], [evaluatedSpanId, 'completed']].sort());
      expect(bySpan.get(evaluatedSpanId)!.eval_explanation).toBe(`${verdict} saw llm`);
      return { evaluated: bySpan.get(evaluatedSpanId)!.id, root: bySpan.get(seeded.spanIds[0])!.id };
    });
    const runId = runs.evaluated;
    const position = '1 / 1';

    await test.step('Usage: the run panel shows the seeded trace and span', async () => {
      await page.goto(`/dashboard/tasks/${taskId}`);
      const runsResponse = page.waitForResponse((response: Response) => {
        const url = new URL(response.url());
        return url.origin === E2E.apiUrl && url.pathname === USAGE_PATH
          && url.searchParams.get('eval_task_id') === taskId && url.searchParams.get('include_summary') === 'false';
      }, { timeout: UI_READY });
      await page.getByRole('tab', { name: 'Usage' }).click();
      const response = await runsResponse;
      expect(response.status()).toBe(200);
      const body = await response.json() as UsageBody;
      await testInfo.attach('usage-runs.json', { contentType: 'application/json', body: JSON.stringify(body) });
      // Usage lists successful runs only; the errored root stays in the task
      // logs (futureagi/tracer/views/eval_task.py:_successful_usage_queryset).
      expect(body.result.logs.results.map(run => ({ id: run.id, eval_name: run.eval_name,
        trace_id: run.trace_id, span_id: run.span_id })))
        .toEqual([{ id: runId, eval_name: evalName, trace_id: seeded.traceId, span_id: evaluatedSpanId }]);
      expect(body.result.logs.results.map(run => run.id)).not.toContain(runs.root);

      // MUI DataGrid keys each row by getRowId (the run id) as data-id.
      await page.locator(`.MuiDataGrid-row[data-id="${runId}"]`).click({ timeout: UI_READY });
      await expect(page.getByText(position, { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(page.getByText(seeded.traceId, { exact: true })).toBeVisible();
      await expect(page.getByText(evaluatedSpanId, { exact: true })).toBeVisible();
    });

    // TraceDetailDrawerV2 marks its body with data-drawer-content; the Usage
    // tab mounts the drawer only while it is open.
    const drawer = page.locator('.MuiDrawer-paper').filter({ has: page.locator('[data-drawer-content]') });
    await test.step('Open trace: the drawer lands on the evaluated span', async () => {
      await page.getByRole('button', { name: 'Open trace', exact: true }).click();
      await expect(drawer).toHaveCount(1, { timeout: UI_READY });
      await expect(drawer.getByText('Trace ID :', { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(drawer.getByText(seeded.traceId, { exact: true })).toBeVisible();
      // The span pane prints the selected span's id: the child, not the root
      // the drawer would select by default.
      await expect(drawer.getByText(evaluatedSpanId, { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(drawer.getByText(seeded.spanIds[0], { exact: true })).toHaveCount(0);
      await expect(drawer.getByRole('tab', { name: 'Evals' })).toHaveAttribute('aria-selected', 'true');
      await expect(drawer.getByText(evalName)).toBeVisible({ timeout: UI_READY });
    });

    await test.step('Esc closes the drawer, then the run panel', async () => {
      await page.keyboard.press('Escape');
      await expect(drawer).toHaveCount(0, { timeout: UI_READY });
      await expect(page.getByText(position, { exact: true })).toBeVisible();
      await expect(page.getByText(seeded.traceId, { exact: true })).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(page.getByText(position, { exact: true })).toHaveCount(0, { timeout: UI_READY });
      await expect(page.getByText(seeded.traceId, { exact: true })).toHaveCount(0);
    });
  } finally {
    await req.dispose();
  }
});
