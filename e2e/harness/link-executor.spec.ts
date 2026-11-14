import { request } from '@playwright/test';
import { test, expect } from '../lib/fixtures';
import { sendTrace } from '../lib/otlp';
import { POLL } from '../lib/state-probe';
import { E2E } from '../lib/env';

// The code-evaluation path is asynchronous here, unlike the browser playground.
// Wire bodies follow flows/evals/eval-task.spec.ts and CustomEvalConfigSerializer.
test('Temporal code evaluations reach the isolated executor with fallback disabled', async ({ actor, probe }, testInfo) => {
  test.setTimeout(240_000); // SPAN_VISIBLE + EVAL_RESULT + ASYNC_JOB + API setup.
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const path = `/ok/e2e-worker-link-${suffix}`;
  try {
    const templates = await probe.pg<{ id: string; name: string }>(
      "SELECT id, name FROM model_hub_evaltemplate WHERE name = ANY($1) AND owner = 'system' AND deleted = false ORDER BY name",
      [['contains_valid_link', 'no_invalid_links']]);
    expect(templates.map(row => row.name)).toEqual(['contains_valid_link', 'no_invalid_links']);
    const seeded = await sendTrace(req, { collectorUrl: E2E.collectorUrl,
      apiKey: actor.apiKey, secretKey: actor.secretKey, projectName: `e2e-worker-links-${suffix}`,
      childAttributes: { 'e2e.link.text': `http://worker-allowed.example${path}` } });
    await expect.poll(async () => Number((await probe.ch<{ n: string }>(
      'SELECT count() AS n FROM spans FINAL WHERE trace_id = {t:String}', { t: seeded.traceId }))[0].n),
    POLL.SPAN_VISIBLE).toBe(2);
    const [{ project_id: projectId }] = await probe.ch<{ project_id: string }>(
      'SELECT DISTINCT project_id FROM spans FINAL WHERE trace_id = {t:String}', { t: seeded.traceId });
    const configs: string[] = [];
    for (const template of templates) {
      const created = await actor.api.post<{ result: { id: string } }>('/tracer/custom-eval-config/', {
        project: projectId, eval_template: template.id, name: `${template.name}-${suffix}`,
        mapping: { text: 'e2e.link.text' }, config: { mapping: { text: 'e2e.link.text' } }, error_localizer: false,
      });
      configs.push(created.result.id);
    }
    const now = Date.now();
    const created = await actor.api.post<{ result: { id: string } }>('/tracer/eval-task/', {
      name: `e2e-worker-link-task-${suffix}`, project: projectId, evals: configs,
      filters: { project_id: projectId, date_range: [new Date(now - 3_600_000).toISOString(), new Date(now + 3_600_000).toISOString()] },
      run_type: 'historical', row_type: 'spans', spans_limit: 100000, sampling_rate: 100,
    });
    const taskId = created.result.id;
    await testInfo.attach('worker-eval-seed', { contentType: 'application/json',
      body: JSON.stringify({ ...seeded, templates, configs, taskId, organizationId: actor.organizationId, path }) });
    await expect.poll(async () => (await actor.api.get<{ status: string }>(`/tracer/eval-task/${taskId}/`)).status,
      POLL.EVAL_RESULT).toBe('completed');
    await expect.poll(async () => await probe.pg<{ status: string; output_bool: boolean; eval_explanation: string }>(
      `SELECT status, output_bool, eval_explanation FROM tracer_eval_logger
       WHERE eval_task_id = $1 AND observation_span_id = $2 ORDER BY eval_explanation`, [taskId, seeded.spanIds[1]]),
    POLL.ASYNC_JOB).toEqual([
      { status: 'completed', output_bool: true, eval_explanation: 'All 1 link(s) are valid' },
      { status: 'completed', output_bool: true, eval_explanation: 'Link found and valid (status 200)' },
    ]);
    const journal = await req.get(`${E2E.linkWorkerCanaryUrl}/requests?prefix=${encodeURIComponent(path)}`);
    expect(journal.status()).toBe(200);
    expect(await journal.json()).toEqual([
      { method: 'HEAD', host: 'worker-allowed.example', path },
      { method: 'HEAD', host: 'worker-allowed.example', path },
    ]);
  } finally {
    await req.dispose();
  }
});
