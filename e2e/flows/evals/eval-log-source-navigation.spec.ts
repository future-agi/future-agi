import { request, type Response } from '@playwright/test';
import { randomBytes } from 'node:crypto';
import { test, expect } from '../../lib/mock-model-fixtures';
import { E2E } from '../../lib/env';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { inspectManagedMock } from '../../lib/managed-mock';
import { flowAnnotation } from '../../lib/flow-meta';

const TASK_PATH = '/tracer/eval-task/';
// The Usage tab's list request: frontend/src/sections/evals/hooks/useEvalUsage.js
// (useEvalUsageLogs → endpoints.develop.eval.getEvalUsage(templateId)).
const usagePath = (templateId: string) => `/model-hub/eval-templates/${templateId}/usage/`;
// The logs request sends EvalUsageTab's default page size; the chart request on the same
// path sends page_size=1 (useEvalUsageChart), so its one-row table is not the list.
const LOGS_PAGE_SIZE = '25';
// The opened log's source request: frontend/src/sections/evals/EvalDetails/EvalsLog/
// useEvalLogSourceNavigation.js (endpoints.develop.eval.getEvalLogs + include_source_navigation).
const LOG_PATH = '/model-hub/get-eval-logs';
// lib/otlp.ts puts fi.span.kind=llm on the child; the grandchild gets its own value below.
// The root carries no such attribute, so the judge's one input is missing there.
const MAPPED_ATTRIBUTE = 'fi.span.kind';
const UI_READY = 60_000;
// frontend/src/sections/evals/EvalDetails/EvalsLog/SourceNavigationRow.jsx (COPY.unavailable).
const UNAVAILABLE = 'The source is unavailable or you do not have access.';

// futureagi/model_hub/serializers/contracts.py: EvalLogSourceNavigationSerializer.
interface SourceNavigation {
  status: string; kind: string | null; project_id: string | null;
  trace_id: string | null; span_id: string | null; retryable: boolean;
}
interface LogBody { result: Record<string, unknown> & { source_navigation?: SourceNavigation } }
// futureagi/model_hub/views/separate_evals.py (EvalUsageStatsView table rows).
interface UsageRow { row_id: string; source: { cell_value: string }; reason: { cell_value: string } }
interface UsageBody { result: { table: UsageRow[] } }
// usage_apicalllog: futureagi/ee/usage/models/usage.py (APICallLog, app label "usage").
interface LedgerRow { log_id: string; status: string; config: string }

// The ledger config is JSON, sometimes stored as a JSON string (the views json.loads it too).
const parseConfig = (raw: string): Record<string, unknown> => {
  let value: unknown = raw;
  for (let i = 0; i < 3 && typeof value === 'string'; i++) value = JSON.parse(value);
  return value as Record<string, unknown>;
};

test.beforeAll(() => { inspectManagedMock({ evalBackground: true }); });
test.use({ evalBackground: true, viewport: { width: 1600, height: 1100 } });

test('EVAL-E2E-014: an eval log opens the exact trace span it evaluated from the eval Usage tab', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-014', area: 'evals',
    userGoal: 'Open an evaluation, open one of its Observe evaluation logs, and view exactly the trace span that log evaluated',
    steps: ['seed a root → child → grandchild trace and run one eval task over it via the managed mock judge',
      'open the eval, its Usage tab, and the grandchild span\'s evaluation log',
      'View trace opens that trace on the grandchild span; Back to evaluation returns to the same log',
      'open the child span\'s log; View trace opens the child span; Esc closes only the trace, then the log',
      'reopen the grandchild log, delete its project, then View trace: unavailable, with no trace and no link',
      'read the log through the API without the opt-in'],
    backendChecks: ['the task completes with the child and grandchild evaluations completed and the unmapped root errored',
      'the usage ledger holds one successful Observe log per evaluated span, stamped with its exact project, trace and span, and both reach the ClickHouse mirror the Usage tab reads',
      'the Usage tab lists exactly those two logs, as source tracer',
      'opening a log asks for its source with the opt-in and gets ready/trace with the exact project, trace and span',
      'the trace opens on the evaluated span (grandchild, then child), not the root or the sibling',
      'when the project is deleted after the log opened, View trace re-checks and gets unavailable with every id null; no trace or link opens',
      'without the opt-in the log response has no source_navigation, and =false is identical'],
  }),
}, async ({ page, actor, probe, mockModel }, testInfo) => {
  // Sequential named budgets: SPAN_VISIBLE (15 s) + EVAL_RESULT (90 s) + ASYNC_JOB (60 s,
  // PG ledger) + CDC_VISIBLE (180 s, ledger mirror) + 14 * UI_READY (840 s): Usage rows,
  // Usage response, two source responses, View trace, drawer, span, Back, child row, child
  // source, child span, two Esc, unavailable state. Add 30 s for seeding and navigation; a
  // per-flow ceiling, not a sum of every default-budget operation.
  test.setTimeout(1_215_000);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `e2e-log-source-${suffix}`;
  const evalName = `e2e-log-source-judge-${suffix}`;
  // Exists nowhere else: finding it on a result proves this run's task produced it.
  const verdict = `e2e-log-source-verdict-${suffix}`;
  // Collector span ids are 16 hex; minted here so the grandchild can name its parent.
  const [rootSpanId, childSpanId, grandchildSpanId] = [0, 1, 2].map(() => randomBytes(8).toString('hex'));
  try {
    const seeded = await sendTrace(req, {
      collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey, projectName,
      rootSpanId, childSpanId,
      additionalSpans: [{ spanId: grandchildSpanId, parentSpanId: childSpanId, name: 'e2e.tool-call',
        attributes: { [MAPPED_ATTRIBUTE]: 'tool' } }],
    });
    expect(seeded.spanIds).toEqual([rootSpanId, childSpanId, grandchildSpanId]);
    await expect.poll(async () => (await probe.ch<{ id: string; parent_span_id: string | null }>(
      'SELECT id, parent_span_id FROM spans FINAL WHERE trace_id={t:String} ORDER BY id', { t: seeded.traceId }))
      .map(({ id, parent_span_id: parent }) => [id, parent || null]),
    POLL.SPAN_VISIBLE).toEqual([[rootSpanId, null], [childSpanId, rootSpanId], [grandchildSpanId, childSpanId]]
      .sort(([a], [b]) => (a! < b! ? -1 : 1)));
    const [{ project_id: projectId }] = await probe.ch<{ project_id: string }>(
      'SELECT DISTINCT project_id FROM spans FINAL WHERE trace_id={t:String}', { t: seeded.traceId });
    await testInfo.attach('fixture.json', { contentType: 'application/json', body: JSON.stringify({
      projectId, projectName, organizationId: actor.organizationId, workspaceId: actor.workspaceId,
      traceId: seeded.traceId, rootSpanId, childSpanId, grandchildSpanId, evalName }) });

    const { templateId, taskId } = await test.step('run an eval task over the seeded spans', async () => {
      await mockModel.assertReady();
      // Wire bodies as in flows/evals/task-usage-trace-drawer.spec.ts (pinned from the serializers).
      const template = await actor.api.post<{ result: { id: string } }>('/model-hub/eval-templates/create-v2/', {
        name: `e2e-log-source-template-${suffix}`, eval_type: 'llm', model: mockModel.model,
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
        name: `e2e-log-source-task-${suffix}`, project: projectId, evals: [config.result.id],
        filters: { project_id: projectId,
          date_range: [new Date(now - 3_600_000).toISOString(), new Date(now + 3_600_000).toISOString()] },
        run_type: 'historical', row_type: 'spans', spans_limit: 100000, sampling_rate: 100,
      });
      return { templateId: template.result.id, taskId: created.result.id };
    });
    await testInfo.attach('ids.json', { contentType: 'application/json', body: JSON.stringify({ templateId, taskId }) });

    await test.step('storage lane: child and grandchild completed, the root errored', async () => {
      await expect.poll(async () =>
        (await actor.api.get<{ status: string }>(`${TASK_PATH}${taskId}/`)).status, POLL.EVAL_RESULT).toBe('completed');
      const rows = await probe.pg<{ observation_span_id: string; status: string; eval_explanation: string | null }>(
        `SELECT observation_span_id, status, eval_explanation FROM tracer_eval_logger
         WHERE eval_task_id=$1 AND deleted=false`, [taskId]);
      await testInfo.attach('pg-results.json', { contentType: 'application/json', body: JSON.stringify(rows) });
      const bySpan = new Map(rows.map((row) => [row.observation_span_id, row]));
      expect(rows.map((row) => [row.observation_span_id, row.status]).sort()).toEqual(
        [[rootSpanId, 'errored'], [childSpanId, 'completed'], [grandchildSpanId, 'completed']].sort());
      expect(bySpan.get(childSpanId)!.eval_explanation).toBe(`${verdict} saw llm`);
      expect(bySpan.get(grandchildSpanId)!.eval_explanation).toBe(`${verdict} saw tool`);
    });

    const logs = await test.step('storage lane: one stamped ledger log per evaluated span', async () => {
      let ledger: { log_id: string; status: string; source: unknown; project_id: unknown;
        trace_id: unknown; span_id: unknown }[] = [];
      try {
        await expect.poll(async () => {
          ledger = (await probe.pg<LedgerRow>(
            `SELECT log_id::text AS log_id, status, config::text AS config FROM usage_apicalllog
             WHERE source_id=$1 AND deleted=false AND status='success'`, [templateId]))
            .map(({ log_id: logId, status, config }) => {
              const parsed = parseConfig(config);
              return { log_id: logId, status, source: parsed.source, project_id: parsed.project_id,
                trace_id: parsed.trace_id, span_id: parsed.span_id };
            });
          return ledger.map(({ span_id: spanId }) => spanId).sort();
        }, POLL.ASYNC_JOB).toEqual([childSpanId, grandchildSpanId].sort());
      } finally {
        await testInfo.attach('ledger.json', { contentType: 'application/json', body: JSON.stringify(ledger) });
      }
      for (const row of ledger) {
        expect(row).toMatchObject({ status: 'success', source: 'tracer', project_id: projectId,
          trace_id: seeded.traceId });
      }
      const logFor = (spanId: string) => ledger.find(({ span_id: s }) => s === spanId)!.log_id;
      return { child: logFor(childSpanId), grandchild: logFor(grandchildSpanId) };
    });

    await test.step('storage lane: both logs reach the ClickHouse mirror the Usage tab reads', async () => {
      // EvalUsageStatsView reads usage_apicalllog from ClickHouse (model_hub/selectors/eval_usage.py),
      // fed from Postgres by the PeerDB CDC mirror; FINAL folds the processing → success versions.
      await expect.poll(async () => (await probe.ch<{ log_id: string }>(
        `SELECT toString(log_id) AS log_id FROM usage_apicalllog FINAL
         WHERE source_id={t:String} AND status='success' AND deleted=0 AND _peerdb_is_deleted=0`,
        { t: templateId })).map(({ log_id: logId }) => logId).sort(), POLL.CDC_VISIBLE)
        .toEqual([logs.child, logs.grandchild].sort());
    });

    const usageBodies: UsageBody[] = [];
    page.on('response', async (response: Response) => {
      const url = new URL(response.url());
      if (url.origin !== E2E.apiUrl || url.pathname !== usagePath(templateId) || response.status() !== 200
        || url.searchParams.get('page_size') !== LOGS_PAGE_SIZE) return;
      try { usageBodies.push(await response.json() as UsageBody); } catch { /* body gone after navigation */ }
    });
    const sourceResponse = (logId: string) => page.waitForResponse((response: Response) => {
      const url = new URL(response.url());
      return url.origin === E2E.apiUrl && url.pathname === LOG_PATH && url.searchParams.get('log_id') === logId
        && url.searchParams.get('include_source_navigation') === 'true';
    }, { timeout: UI_READY });
    const readySource = (spanId: string): SourceNavigation => ({ status: 'ready', kind: 'trace',
      project_id: projectId, trace_id: seeded.traceId, span_id: spanId, retryable: false });
    // MUI DataGrid keys each row by getRowId (the log id) as data-id.
    const row = (logId: string) => page.locator(`.MuiDataGrid-row[data-id="${logId}"]`);
    // The Usage panel's header prints the open log's id, first 12 characters.
    const openLog = (logId: string) => page.getByText(logId.slice(0, 12), { exact: true });
    const viewTrace = page.getByRole('button', { name: 'View trace', exact: true });
    // TraceDetailDrawerV2 marks its body with data-drawer-content.
    const drawer = page.locator('.MuiDrawer-paper').filter({ has: page.locator('[data-drawer-content]') });

    await test.step('Usage lists the two Observe logs', async () => {
      await page.goto(`/dashboard/evaluations/${templateId}`);
      await page.getByRole('tab', { name: 'Usage' }).click({ timeout: UI_READY });
      await expect(row(logs.grandchild)).toBeVisible({ timeout: UI_READY });
      await expect(row(logs.child)).toBeVisible();
      await expect.poll(() => usageBodies.at(-1)?.result.table.map((r) => [r.row_id, r.source.cell_value,
        r.reason.cell_value]).sort(), { timeout: UI_READY }).toEqual([
        [logs.child, 'tracer', `${verdict} saw llm`], [logs.grandchild, 'tracer', `${verdict} saw tool`]].sort());
      await testInfo.attach('usage.json', { contentType: 'application/json', body: JSON.stringify(usageBodies.at(-1)) });
    });

    await test.step('View trace opens the grandchild span; Back returns to its log', async () => {
      const shown = sourceResponse(logs.grandchild);
      await row(logs.grandchild).click();
      const body = await (await shown).json() as LogBody;
      expect(body.result.source_navigation).toEqual(readySource(grandchildSpanId));
      await expect(openLog(logs.grandchild)).toBeVisible({ timeout: UI_READY });
      await expect(viewTrace).toBeVisible({ timeout: UI_READY });
      // Activation re-checks the source before anything opens.
      const recheck = sourceResponse(logs.grandchild);
      await viewTrace.click();
      expect((await (await recheck).json() as LogBody).result.source_navigation)
        .toEqual(readySource(grandchildSpanId));
      await expect(drawer).toHaveCount(1, { timeout: UI_READY });
      await expect(drawer.getByText('Trace ID :', { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(drawer.getByText(seeded.traceId, { exact: true })).toBeVisible();
      // The span pane prints the selected span's id: the grandchild, not the root the
      // drawer would select by default, nor its parent.
      await expect(drawer.getByText(grandchildSpanId, { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(drawer.getByText(rootSpanId, { exact: true })).toHaveCount(0);
      await expect(drawer.getByText(childSpanId, { exact: true })).toHaveCount(0);
      await page.getByRole('button', { name: 'Back to evaluation', exact: true }).click();
      await expect(drawer).toHaveCount(0, { timeout: UI_READY });
      await expect(openLog(logs.grandchild)).toBeVisible();
      await expect(viewTrace).toBeVisible();
    });

    await test.step('the child log opens the child span; Esc closes the trace, then the log', async () => {
      const shown = sourceResponse(logs.child);
      await row(logs.child).click();
      expect((await (await shown).json() as LogBody).result.source_navigation).toEqual(readySource(childSpanId));
      await expect(openLog(logs.child)).toBeVisible({ timeout: UI_READY });
      await viewTrace.click({ timeout: UI_READY });
      await expect(drawer).toHaveCount(1, { timeout: UI_READY });
      await expect(drawer.getByText(childSpanId, { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(drawer.getByText(rootSpanId, { exact: true })).toHaveCount(0);
      await expect(drawer.getByText(grandchildSpanId, { exact: true })).toHaveCount(0);
      await page.keyboard.press('Escape');
      await expect(drawer).toHaveCount(0, { timeout: UI_READY });
      await expect(openLog(logs.child)).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(openLog(logs.child)).toHaveCount(0, { timeout: UI_READY });
    });

    await test.step('a project deleted after the log opened: View trace re-checks and shows unavailable', async () => {
      const shown = sourceResponse(logs.grandchild);
      await row(logs.grandchild).click();
      expect((await (await shown).json() as LogBody).result.source_navigation).toEqual(readySource(grandchildSpanId));
      await expect(viewTrace).toBeVisible({ timeout: UI_READY });
      await actor.api.delete(`/tracer/project/${projectId}/`);
      const recheck = sourceResponse(logs.grandchild);
      await viewTrace.click();
      expect((await (await recheck).json() as LogBody).result.source_navigation).toEqual({ status: 'unavailable',
        kind: null, project_id: null, trace_id: null, span_id: null, retryable: false });
      await expect(page.getByText(UNAVAILABLE, { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(viewTrace).toHaveCount(0);
      await expect(drawer).toHaveCount(0);
      await expect(page.getByRole('dialog')).toHaveCount(0);
      await expect(page.locator(`a[href*="${projectId}"]`)).toHaveCount(0);
      // The evaluation itself stays on screen.
      await expect(openLog(logs.grandchild)).toBeVisible();
      await expect(page.getByText(`${verdict} saw tool`, { exact: true }).last()).toBeVisible();
      await page.getByRole('button', { name: 'Close log', exact: true }).click();
      await expect(openLog(logs.grandchild)).toHaveCount(0, { timeout: UI_READY });
    });

    await test.step('API lane: without the opt-in the log is unchanged', async () => {
      const plain = await actor.api.get<LogBody>(LOG_PATH, { log_id: logs.grandchild });
      const off = await actor.api.get<LogBody>(LOG_PATH, { log_id: logs.grandchild,
        include_source_navigation: 'false' });
      const on = await actor.api.get<LogBody>(LOG_PATH, { log_id: logs.grandchild,
        include_source_navigation: 'true' });
      await testInfo.attach('log-api.json', { contentType: 'application/json', body: JSON.stringify({ plain, off, on }) });
      expect(plain.result.log_id).toBe(logs.grandchild);
      expect(Object.keys(plain.result)).not.toContain('source_navigation');
      expect(off).toEqual(plain);
      const { source_navigation: nav, ...rest } = on.result;
      expect(rest).toEqual(plain.result);
      expect(nav?.status).toBe('unavailable');
    });
  } finally {
    await req.dispose();
  }
});
