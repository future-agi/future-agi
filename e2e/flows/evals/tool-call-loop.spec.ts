import { request } from '@playwright/test';
import { randomBytes } from 'node:crypto';
import { writeFile } from 'node:fs/promises';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// model_hub/system_evals/function/tool_call_loop_repetition.yaml
const EVAL_NAME = 'tool_call_loop_repetition';
// model_hub/urls.py and tracer/urls.py; TaskLivePreview uses eval-playground.
const PREVIEW_PATH = '/model-hub/eval-playground/';
const TASK_PATH = '/tracer/eval-task/';
const CONFIG_PATH = '/tracer/custom-eval-config/';
// README Writing a flow: first-paint budget, separate from store propagation.
const UI_READY = 60_000;

test.use({ viewport: { width: 1440, height: 900 } });

test('EVAL-E2E-021: pick the tool loop evaluator and compare repeated calls with progress', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-021', area: 'evals',
    userGoal: 'Run the built-in tool loop evaluator on stored Observe traces and retain the result',
    steps: ['seed repeated and progressing completed tool spans', 'choose Traces and the built-in evaluator',
      'configure repeats and window without input mapping', 'preview both traces and inspect scores and reasons',
      'submit a historical task and inspect persisted results'],
    backendChecks: ['ClickHouse contains exactly the seeded tool span identities',
      'browser preview reads each trace and returns its expected score and window reason',
      'Postgres and ClickHouse retain exactly the two trace-target results'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(600_000);
  page.setDefaultTimeout(UI_READY);
  const req = await request.newContext();
  const suffix = `${Date.now().toString(36)}-${testInfo.workerIndex}`;
  const projectName = `e2e-tool-loops-${suffix}`;
  const taskName = `e2e-tool-loop-task-${suffix}`;
  const jobId = `job-${suffix}`;
  const fixtures: { traceId: string; toolIds: string[]; score: number; progress: boolean }[] = [];
  const attach = async (name: string, value: unknown) => {
    const path = testInfo.outputPath(name);
    await writeFile(path, JSON.stringify(value, null, 2));
    await testInfo.attach(name, { path, contentType: 'application/json' });
  };
  try {
    for (const progress of [false, true]) {
      const rootId = randomBytes(8).toString('hex');
      const toolIds = Array.from({ length: 3 }, () => randomBytes(8).toString('hex'));
      const start = BigInt(Date.now() - 10_000) * 1_000_000n;
      // fi-collector/exporter/clickhouse25exporter/converter.go: completed tool
      // kind, input.value/output.value, and tool.name are actual stored telemetry.
      const attrs = (index: number) => ({ 'fi.span.kind': 'tool', 'tool.name': 'poll',
        'input.value': JSON.stringify({ id: jobId }),
        'output.value': JSON.stringify({ status: progress ? index : 'waiting' }) });
      const seeded = await sendTrace(req, {
        collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
        projectName, resourceAttributes: { project_type: 'observe' },
        rootName: `${progress ? 'progress' : 'repeat'}-${suffix}`, rootSpanId: rootId,
        childName: 'poll', childSpanId: toolIds[0], childAttributes: attrs(0),
        startTimeUnixNano: start, endTimeUnixNano: start + 50_000_000n,
        additionalSpans: toolIds.slice(1).map((spanId, i) => ({ spanId, parentSpanId: rootId,
          name: 'poll', attributes: attrs(i + 1),
          startTimeUnixNano: start + BigInt(i + 1) * 100_000_000n,
          endTimeUnixNano: start + BigInt(i + 1) * 100_000_000n + 50_000_000n })),
      });
      fixtures.push({ traceId: seeded.traceId, toolIds, score: progress ? 1 : 0.2333, progress });
    }
    await attach('seeded-identities.json', { projectName, taskName, jobId, fixtures });
    for (const fixture of fixtures) {
      await expect.poll(async () => (await probe.ch<{ id: string }>(
        "SELECT id FROM spans FINAL WHERE trace_id={trace:String} AND lower(observation_type)='tool' ORDER BY id",
        { trace: fixture.traceId })).map(row => row.id), POLL.SPAN_VISIBLE).toEqual([...fixture.toolIds].sort());
    }
    const [{ project_id: projectId }] = await probe.ch<{ project_id: string }>(
      'SELECT DISTINCT project_id FROM spans FINAL WHERE trace_id={trace:String}', { trace: fixtures[0].traceId });
    await page.goto('/dashboard/evaluations');
    await page.getByPlaceholder('Search', { exact: true }).fill(EVAL_NAME);
    await expect(page.getByRole('row').filter({ hasText: EVAL_NAME })).toHaveCount(1, { timeout: UI_READY });
    await page.screenshot({ path: testInfo.outputPath('catalog-after.png') });
    // TaskCreatePage supports these date deep links; avoid unrelated historical rows.
    const window = new URLSearchParams({ startDate: new Date(Date.now() - 600_000).toISOString(),
      endDate: new Date(Date.now() + 600_000).toISOString() });
    await page.goto(`/dashboard/tasks/create?${window}`);
    await page.getByPlaceholder('Enter task name').fill(taskName);
    await page.getByRole('combobox', { name: /^Project/ }).click();
    await page.getByRole('option', { name: projectName, exact: true }).click();
    await page.getByRole('tab', { name: 'Traces', exact: true }).click();
    await page.getByRole('button', { name: 'All', exact: true }).click();
    await page.getByRole('button', { name: 'Add Evaluation', exact: true }).click();
    await page.getByPlaceholder('Search evaluations...').fill(EVAL_NAME);
    await page.getByRole('row').filter({ hasText: EVAL_NAME }).getByRole('button', { name: 'Add', exact: true }).click();
    // EvalPickerConfigFull renders function parameter keys as TextField labels.
    await page.getByLabel('min_repeats', { exact: true }).fill('3');
    await page.getByLabel('window_size', { exact: true }).fill('8');
    const savedConfig = page.waitForResponse(r => r.url().endsWith(CONFIG_PATH)
      && r.request().method() === 'POST', { timeout: UI_READY });
    await page.getByRole('button', { name: 'Add Evaluation', exact: true }).click();
    const configResponse = await savedConfig;
    expect(configResponse.ok()).toBe(true);
    const configBody = await configResponse.json();
    await attach('configured-evaluator.json', { request: configResponse.request().postDataJSON(), response: configBody });
    const seen: string[] = [];
    const previewReasons = new Map<string, string>();
    for (let i = 0; i < 2; i++) {
      const preview = page.waitForResponse(r => r.url().endsWith(PREVIEW_PATH), { timeout: UI_READY });
      await page.getByRole('button', { name: 'Test on this row', exact: true }).click();
      const response = await preview;
      const payload = response.request().postDataJSON();
      const body = await response.json();
      await attach(`preview-${i}.json`, { payload, body });
      expect(response.ok()).toBe(true);
      const fixture = fixtures.find(f => f.traceId === payload.trace_id);
      expect(fixture).toBeTruthy();
      seen.push(payload.trace_id);
      previewReasons.set(payload.trace_id, body.result.reason);
      expect(payload.config.params).toMatchObject({ min_repeats: 3, window_size: 8 });
      expect(body.result.output).toBe(fixture!.score);
      expect(body.result.reason).toContain('window_size=8');
      if (!fixture!.progress) {
        expect(body.result.reason).toContain('exact_repeat');
        expect(body.result.reason).toContain(`poll: {"id": "${jobId}"}`);
      } else {
        expect(body.result.reason).toContain('No repetitive');
      }
      await expect(page.getByText(body.result.reason, { exact: true })).toBeVisible({ timeout: UI_READY });
      await page.getByText(body.result.reason, { exact: true }).scrollIntoViewIfNeeded();
      await page.screenshot({ path: testInfo.outputPath(fixture!.progress ? 'progress-result.png' : 'loop-result.png') });
      if (i === 0) {
        await page.getByRole('button', { name: 'Next row', exact: true }).click();
        const nextTrace = fixtures.find(f => f.traceId !== fixture!.traceId)!;
        await expect(page.getByText(`"${nextTrace.traceId}"`, { exact: true })).toBeVisible({ timeout: UI_READY });
      }
    }
    expect(seen.sort()).toEqual(fixtures.map(f => f.traceId).sort());
    const submitted = page.waitForResponse(r => r.url().endsWith(TASK_PATH)
      && r.request().method() === 'POST', { timeout: UI_READY });
    await page.getByRole('button', { name: 'Create task', exact: true }).click();
    const taskResponse = await submitted;
    expect(taskResponse.ok()).toBe(true);
    expect(taskResponse.request().postDataJSON()).toMatchObject({ project: projectId,
      row_type: 'traces', run_type: 'historical', sampling_rate: 100 });
    const taskId = (await taskResponse.json()).result.id;
    await attach('task-identity.json', { taskId, projectId, configId: configBody.result.id });
    await expect(page).toHaveURL(new RegExp(`/dashboard/tasks/${taskId}`), { timeout: UI_READY });
    await expect.poll(async () => (await actor.api.get<{ status: string }>(`${TASK_PATH}${taskId}/`)).status,
      POLL.EVAL_RESULT).toBe('completed');
    const expected = fixtures.map(f => ({ trace_id: f.traceId, output_float: f.score,
      eval_explanation: previewReasons.get(f.traceId), target_type: 'trace', status: 'completed' }))
      .sort((a, b) => a.trace_id.localeCompare(b.trace_id));
    const pgRows = await probe.pg<{ trace_id: string; output_float: number; target_type: string; status: string }>(
      'SELECT trace_id,output_float,eval_explanation,target_type,status FROM tracer_eval_logger WHERE eval_task_id=$1 AND deleted=false ORDER BY trace_id', [taskId]);
    expect(pgRows).toEqual(expected);
    await expect.poll(async () => probe.ch(
      'SELECT trace_id,output_float,eval_explanation,target_type,status FROM tracer_eval_logger FINAL WHERE eval_task_id={task:String} AND deleted=false ORDER BY toString(trace_id)',
      { task: taskId }), POLL.CDC_VISIBLE).toEqual(expected);
    await attach('persisted-results.json', pgRows);
  } finally {
    await req.dispose();
  }
});
