import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// The eval runs inside the worker container, so its judge model must reach the
// gateway over the compose network — E2E.gatewayUrl is a host port the worker
// cannot resolve. Address + shared key are the root compose defaults
// (docker-compose.yml AGENTCC_INTERNAL_URL / AGENTCC_INTERNAL_API_KEY); the key
// is the same one harness/mock-llm.spec.ts authenticates with.
const GATEWAY_INTERNAL_URL = 'http://agentcc-gateway:8080/v1';
const GATEWAY_INTERNAL_KEY = 'local-dev-only-shared-secret-replace-me';
// One of the models the gateway maps to the mock provider (stack/gateway.e2e.yaml).
const JUDGE_MODEL = 'gpt-4o-mini';
// The only attribute lib/otlp.ts puts on a span, and only on the llm child —
// the eval's single input variable maps to it.
const MAPPED_ATTRIBUTE = 'fi.span.kind';

interface CreatedId { result: { id: string } }

interface EvalResultRow {
  status: string;
  output_bool: number | null;
  eval_explanation: string | null;
  output_metadata: string;
}

// The mock server answers every completion with this fixed usage block
// (stack/mock-llm/server.mjs), so it is a fingerprint no real provider produces.
const MOCK_USAGE = { prompt_tokens: 7, completion_tokens: 7, total_tokens: 14 };

test('EVAL-E2E-001: eval task runs over ingested spans via the mock LLM', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-001', area: 'evals',
    userGoal: 'A developer runs an LLM eval over ingested spans and reads the results',
    steps: ['seed a trace', 'point an OpenAI-compatible judge model at the gateway',
            'author an LLM-as-a-judge eval and attach it to the project',
            'create an eval task on the project', 'wait for completion',
            'read the eval result on the span in Observe'],
    backendChecks: ['EvalTask status reaches completed (Temporal workflow ran)',
                    'result rows land in CH tracer_eval_logger via the PeerDB CDC mirror (PG EvalLogger → CH)',
                    'the stored explanation is exactly the verdict the judge prompt dictated, with the span\'s mapped attribute substituted in, and the result carries the mock LLM\'s token usage',
                    'the Pass verdict is parsed out of the model response into output_bool = true'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // The Temporal drain (EVAL_RESULT, 90s) then the CDC mirror (CDC_VISIBLE,
  // 180s) are a 270s floor on their own; the remaining 90s covers seeding, the
  // creates and the UI waits. That headroom is the point: without it the outer
  // timeout fires first and hides the CH poll's own "Expected: completed".
  test.setTimeout(360_000);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `e2e-eval1-${suffix}`;
  const evalName = `e2e echo judge ${suffix}`;
  // Travels judge prompt → gateway → mock → parsed verdict → PG → CDC → CH, and
  // exists nowhere else, so finding it on the result proves the whole hop.
  const verdict = `e2e-verdict-${suffix}`;

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

  const evalConfigId = await test.step('configure an LLM judge that resolves to the mock', async () => {
    // Settings → Models: an OpenAI-compatible endpoint of the user's own. The
    // backend runs a live completion before saving, so a broken
    // worker → gateway → mock hop fails here rather than inside the workflow.
    await actor.api.post('/model-hub/custom_models/create/', {
      model_provider: 'openai', model_name: JUDGE_MODEL,
      input_token_cost: 0, output_token_cost: 0,
      config_json: { key: GATEWAY_INTERNAL_KEY, api_base: GATEWAY_INTERNAL_URL },
    });

    // The mock answers `echo: <last user message>`, so the verdict this prompt
    // dictates is the verdict that comes back — and `{{output}}` is substituted
    // with the span's attribute on the way out, so the reply proves the request
    // carried real span data. No shipped system template can complete against
    // the mock: they all rely on the judge composing its own JSON.
    const template = await actor.api.post<CreatedId>('/model-hub/eval-templates/create-v2/', {
      name: `e2e-echo-judge-${suffix}`,
      eval_type: 'llm',
      instructions: `Reply with exactly this JSON: {"result": "Pass", "explanation": "${verdict} saw {{output}}"}`,
      model: JUDGE_MODEL, output_type: 'pass_fail', pass_threshold: 0.5,
    });

    const config = await actor.api.post<CreatedId>('/tracer/custom-eval-config/', {
      project: projectId, eval_template: template.result.id, name: evalName,
      model: JUDGE_MODEL,
      mapping: { output: MAPPED_ATTRIBUTE },
      config: { mapping: { output: MAPPED_ATTRIBUTE } },
      error_localizer: false,
    });
    return config.result.id;
  });

  const taskId = await test.step('create the eval task', async () => {
    const now = Date.now();
    // Pinned from the app's own request: captured with page.on('request') while
    // clicking "Create Task" on /dashboard/tasks/create. The form's zod resolver
    // is what turns `evalsDetails` into `evals` and the two date pickers into
    // `filters.date_range`, so the wire body is not the form's shape.
    const created = await actor.api.post<CreatedId>('/tracer/eval-task/', {
      name: `e2e eval task ${suffix}`,
      project: projectId,
      evals: [evalConfigId],
      filters: {
        project_id: projectId,
        date_range: [new Date(now - 3_600_000).toISOString(), new Date(now + 3_600_000).toISOString()],
      },
      run_type: 'historical',
      row_type: 'spans',
      spans_limit: 100000,
      // The create form defaults this slider to 50; 100 so the seeded spans are
      // evaluated instead of sampled away.
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

  await test.step('storage lane: the verdict reaches CH through the CDC mirror', async () => {
    // Django writes EvalLogger to PG only; tracer_eval_logger is populated
    // solely by the PeerDB fact mirror, so poll past its sync interval. FINAL
    // because the entry's PG row is UPDATEd through pending → running →
    // terminal: a sync boundary inside that lifecycle lands several versions of
    // it in this ReplacingMergeTree, and an unmerged read returns the stale one.
    // sendTrace seeds [root, llm-call]; only the llm child carries the mapped
    // attribute, so it is the one span the eval can run on.
    let row: EvalResultRow | undefined;
    // Polling the status rather than the row count keeps an eval that ran and
    // failed distinguishable from one the mirror hasn't delivered yet, and
    // keeping the polled row means the assertions below read that same version.
    await expect.poll(async () => {
      const rows = await probe.ch<EvalResultRow>(
        `SELECT status, output_bool, eval_explanation, output_metadata FROM tracer_eval_logger FINAL
         WHERE eval_task_id = {t:String} AND observation_span_id = {s:String}`,
        { t: taskId, s: seeded.spanIds[1] });
      row = rows[0];
      return row?.status;
    }, POLL.CDC_VISIBLE).toBe('completed');

    expect(row?.output_bool).toBe(1);
    expect(row?.eval_explanation).toBe(`${verdict} saw llm`);
    const metadata = JSON.parse(row?.output_metadata ?? '{}') as { usage?: Record<string, number> };
    expect(metadata.usage).toEqual(MOCK_USAGE);
  });

  await test.step('UI: the eval result shows on the span in Observe', async () => {
    await page.goto(`/dashboard/observe/${projectId}/llm-tracing?selectedTab=spans`);
    await page.getByText('e2e.llm-call').first().click({ timeout: 30_000 });
    await page.getByRole('tab', { name: 'Evals' }).click();
    await expect(page.getByText(evalName)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText('1/1 passed')).toBeVisible();
    // The row keeps the judge's explanation collapsed; opening it is the only
    // place the product renders the verdict text itself.
    await page.getByText(evalName).click();
    await expect(page.getByText(`${verdict} saw llm`)).toBeVisible({ timeout: 15_000 });
  });

  await req.dispose();
});
