import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { JUDGE_MODEL, ensureJudgeModel } from '../../lib/eval-model';

// Same mock-LLM-behind-the-real-gateway setup as eval-task.spec.ts /
// eval-composite.spec.ts — see those files for the full rationale.
const MAPPED_ATTRIBUTE = 'fi.span.kind';

interface CreatedId { result: { id: string } }

interface EvalResultRow {
  status: string;
  output_float: number | null;
  eval_explanation: string | null;
  output_metadata: string;
}

test('EVAL-E2E-017: a composite eval runs over ingested spans via a full async eval task', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-017', area: 'evals',
    userGoal: 'A developer attaches a composite eval (two LLM-as-judge children under an '
      + 'aggregation function) to a project and runs it as a real async eval task over ingested '
      + 'spans — not the adhoc/playground composite test, and not a single eval',
    steps: ['seed a trace', 'point an OpenAI-compatible judge model at the gateway',
            'author two deterministic pass/fail child evals and combine them into a composite',
            'attach the composite to the project as a custom eval config',
            'create an eval task on the project referencing that config',
            'wait for completion', 'read the aggregated result on the span in Observe'],
    backendChecks: ['EvalTask.evals is a CustomEvalConfig id, so the composite is attached the same '
                      + 'way a single eval is — tracer/serializers/eval_task.py has no template_type check',
                    'the Temporal-driven run_entry pipeline (tracer/services/eval_tasks/run_entry.py) '
                      + 'dispatches to tracer/utils/eval.py::_execute_evaluation, which explicitly '
                      + 'branches on eval_template.template_type == "composite" and delegates to '
                      + '_execute_composite_on_span — the same aggregation-aware executor the sync '
                      + '/composite/execute/ endpoint uses',
                    'the aggregate score lands in output_float (not output_bool — composite writes '
                      + 'through the "score" config-output branch since a composite parent\'s '
                      + 'config is always {}), and eval_explanation is the deterministic '
                      + '"[child] (score: X.XX, weight: W) / reason" summary built by '
                      + 'aggregate_summaries(), not an LLM-synthesized one',
                    'output_metadata carries aggregate_pass, aggregation_function and the full '
                      + 'per-child breakdown (child_name/score/reason) alongside the aggregate'],
  }),
}, async ({ page, actor, probe }, testInfo) => {

  // Same 270s Temporal+CDC floor as eval-task.spec.ts, plus headroom for two
  // child LLM calls per span instead of one.
  test.setTimeout(360_000);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `e2e-eval-composite-task-${suffix}`;
  const passChildName = `e2e-task-composite-pass-${suffix}`;
  const failChildName = `e2e-task-composite-fail-${suffix}`;
  const compositeName = `e2e-task-composite-${suffix}`;
  const configName = `e2e task composite config ${suffix}`;
  // Each exists nowhere else, so finding it in a child's reason proves that
  // child actually ran against the mock (mirrors eval-composite.spec.ts).
  const passVerdict = `e2e-verdict-pass-${suffix}`;
  const failVerdict = `e2e-verdict-fail-${suffix}`;

  const seeded = await sendTrace(req, {
    collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
    secretKey: actor.secretKey, projectName,
  });
  await expect.poll(async () => {
    const rows = await probe.ch<{ n: string }>(
      'SELECT count() AS n FROM spans FINAL WHERE trace_id = {t:String}', { t: seeded.traceId });
    return Number(rows[0].n);
  }, POLL.SPAN_VISIBLE).toBe(seeded.spanIds.length);

  const [{ project_id: projectId }] = await probe.ch<{ project_id: string }>(
    'SELECT DISTINCT project_id FROM spans FINAL WHERE trace_id = {t:String}', { t: seeded.traceId });

  await ensureJudgeModel(actor);

  const configId = await test.step('author two children, combine into a composite, attach to the project', async () => {
    // Deterministic pass/pass-fail children — identical shape to
    // eval-composite.spec.ts, but here consumed by the async pipeline
    // instead of an adhoc /execute/ call.
    const passChild = await actor.api.post<CreatedId>('/model-hub/eval-templates/create-v2/', {
      name: passChildName, eval_type: 'llm',
      instructions: `Reply with exactly this JSON: {"result": "Pass", "explanation": "${passVerdict} saw {{output}}"}`,
      model: JUDGE_MODEL, output_type: 'pass_fail', pass_threshold: 0.5,
    });
    const failChild = await actor.api.post<CreatedId>('/model-hub/eval-templates/create-v2/', {
      name: failChildName, eval_type: 'llm',
      instructions: `Reply with exactly this JSON: {"result": "Fail", "explanation": "${failVerdict} saw {{output}}"}`,
      model: JUDGE_MODEL, output_type: 'pass_fail', pass_threshold: 0.5,
    });

    const composite = await actor.api.post<CreatedId>('/model-hub/eval-templates/create-composite/', {
      name: compositeName,
      child_template_ids: [passChild.result.id, failChild.result.id],
      // avg(1.0, 0.0) = 0.5 — same predictable math as eval-composite.spec.ts.
      aggregation_function: 'avg',
    });

    // Attaching a composite template to a CustomEvalConfig is the same call
    // as attaching a single template — tracer/serializers/custom_eval_config.py
    // puts no template_type restriction on eval_template.
    const config = await actor.api.post<CreatedId>('/tracer/custom-eval-config/', {
      project: projectId, eval_template: composite.result.id, name: configName,
      model: JUDGE_MODEL,
      mapping: { output: MAPPED_ATTRIBUTE },
      config: { mapping: { output: MAPPED_ATTRIBUTE } },
      error_localizer: false,
    });
    return config.result.id;
  });

  const taskId = await test.step('create the eval task referencing the composite config', async () => {
    const now = Date.now();
    const created = await actor.api.post<CreatedId>('/tracer/eval-task/', {
      name: `e2e composite eval task ${suffix}`,
      project: projectId,
      evals: [configId],
      filters: {
        project_id: projectId,
        date_range: [new Date(now - 3_600_000).toISOString(), new Date(now + 3_600_000).toISOString()],
      },
      run_type: 'historical',
      row_type: 'spans',
      spans_limit: 100000,
      sampling_rate: 100,
    });
    return created.result.id;
  });

  await test.step('API lane: the task reaches completed', async () => {
    await expect.poll(async () => {
      const task = await actor.api.get<{ status: string }>(`/tracer/eval-task/${taskId}/`);
      return task.status;
    }, POLL.EVAL_RESULT).toBe('completed');
  });

  // Built by hand to match aggregate_summaries() in
  // futureagi/model_hub/utils/composite_aggregation.py exactly: one
  // "[name] (score: X.XX, weight: W)" line + reason per child, joined by a
  // blank line, with no trailing newline (.strip()).
  const expectedSummary = `[${passChildName}] (score: 1.00, weight: 1.0)\n${passVerdict} saw llm\n\n`
    + `[${failChildName}] (score: 0.00, weight: 1.0)\n${failVerdict} saw llm`;

  await test.step('storage lane: the aggregate lands in output_float, not output_bool', async () => {
    let row: EvalResultRow | undefined;
    await expect.poll(async () => {
      const rows = await probe.ch<EvalResultRow>(
        `SELECT status, output_float, eval_explanation, output_metadata FROM tracer_eval_logger FINAL
         WHERE eval_task_id = {t:String} AND observation_span_id = {s:String}`,
        { t: taskId, s: seeded.spanIds[1] });
      row = rows[0];
      return row?.status;
    }, POLL.CDC_VISIBLE).toBe('completed');

    expect(row?.output_float).toBe(0.5);
    expect(row?.eval_explanation).toBe(expectedSummary);
    const metadata = JSON.parse(row?.output_metadata ?? '{}') as {
      aggregate_pass?: boolean;
      aggregation_function?: string;
      children?: { child_name: string; score: number; reason: string }[];
    };
    expect(metadata.aggregate_pass).toBe(true);
    expect(metadata.aggregation_function).toBe('avg');
    const byName = Object.fromEntries((metadata.children ?? []).map((c) => [c.child_name, c]));
    expect(byName[passChildName]?.score).toBe(1);
    expect(byName[failChildName]?.score).toBe(0);
    expect(byName[passChildName]?.reason).toBe(`${passVerdict} saw llm`);
    expect(byName[failChildName]?.reason).toBe(`${failVerdict} saw llm`);
  });

  await test.step('UI: the aggregated result shows on the span in Observe', async () => {
    // query_service.py's per-span eval pivot scales output_float by 100 for
    // display (0.5 -> score 50), and EvalsTabView counts score >= 50 as
    // passed — the same ">=" boundary determine_pass_fail used server-side.
    await page.goto(`/dashboard/observe/${projectId}/llm-tracing?selectedTab=spans`);
    await page.getByText('e2e.llm-call').first().click({ timeout: 30_000 });
    await page.getByRole('tab', { name: 'Evals' }).click();
    await expect(page.getByText(configName)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText('1/1 passed')).toBeVisible();
    await page.getByText(configName).click();
    await expect(page.getByText(passVerdict, { exact: false })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(failVerdict, { exact: false })).toBeVisible();
  });

  await req.dispose();
});
