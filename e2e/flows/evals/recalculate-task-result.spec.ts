import { request } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// The eval runs inside the worker container, so its judge model must reach the
// gateway over the compose network. Same address, key and model as
// flows/evals/eval-task.spec.ts (docker-compose.distributed.yml
// AGENTCC_INTERNAL_URL / AGENTCC_INTERNAL_API_KEY; stack/gateway.e2e.yaml).
const GATEWAY_INTERNAL_URL = 'http://agentcc-gateway:8080/v1';
const GATEWAY_INTERNAL_KEY = 'local-dev-only-shared-secret-replace-me';
const JUDGE_MODEL = 'gpt-4o-mini';
// A string attribute this flow puts on the llm child span; the eval's single
// input variable maps to it. Its value changes between the task's run and the
// Recalculate, so the replacement's verdict shows the judge ran again.
const MAPPED_ATTRIBUTE = 'e2e.answer';
// One browser action's first-paint budget, as in the observe flows.
const UI_READY = 60_000;

interface CreatedId { result: { id: string } }
// POST /tracer/observation-span/submit_feedback/ (tracer/views/observation_span.py:submit_feedback).
interface FeedbackCreated { result: { feedback_id: string } }

interface EvalRow {
  id: string;
  status: string;
  deleted: boolean;
  eval_explanation: string | null;
}

test('EVAL-E2E-015: Recalculate on an eval task\'s result replaces it in place', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-015', area: 'evals',
    userGoal: 'A user who disagrees with an eval task\'s verdict on a span leaves feedback and recalculates that result, and the span keeps exactly one result, computed again on the span\'s current input',
    steps: ['seed a trace through the collector, and register its trace and LLM span through the public trace and span API',
            'author an LLM-as-a-judge eval (mock LLM through the gateway) and run an eval task over the project',
            'leave feedback on the task\'s result for the LLM span (the Observe feedback form\'s request)',
            're-send the span with a new value for the eval\'s mapped attribute',
            'open the eval, its Feedback tab, the feedback, Edit Feedback',
            'pick Re-calculate and re-tune and submit the feedback',
            'read the span\'s eval result in Observe'],
    backendChecks: ['the eval task completes with one live completed entry for the span and eval in PG tracer_eval_logger, its verdict naming the span\'s first input',
                    'Recalculate answers 200 and the feedback row records action recalculate with the user\'s note',
                    'afterwards the span and eval have exactly one live entry: completed, of the same eval task, a new row whose verdict names the span\'s new input (the judge ran again)',
                    'the previous entry is retired (deleted = true), not left live beside the new one',
                    'the CDC mirror carries both to CH tracer_eval_logger: the new entry completed, the previous one deleted'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Budgets in sequence: the task's Temporal drain (EVAL_RESULT, 90 s), the
  // first result through the CDC mirror (CDC_VISIBLE, 180 s), the synchronous
  // Recalculate (one UI_READY), the replacement through the mirror again
  // (CDC_VISIBLE, 180 s), plus seeding and the UI steps.
  test.setTimeout(720_000);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `e2e-recalc-${suffix}`;
  const evalName = `e2e recalc judge ${suffix}`;
  // Travels judge prompt -> gateway -> mock -> parsed verdict -> PG -> CDC -> CH;
  // exists nowhere else.
  const verdict = `e2e-recalc-verdict-${suffix}`;
  // The user's improvement note on the feedback.
  const note = `e2e-recalc-note-${suffix}`;
  // The span's mapped input as the task saw it, and as Recalculate sees it.
  const firstAnswer = `first-${suffix}`;
  const secondAnswer = `second-${suffix}`;
  // Replaying the same ids and timestamps replaces the span's ClickHouse row
  // (lib/otlp.ts:SendTraceConfig, harness/otlp-shapes.spec.ts).
  const startTimeUnixNano = BigInt(Date.now()) * 1_000_000n;
  const traceConfig = (answer: string) => ({
    collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
    secretKey: actor.secretKey, projectName,
    traceId: randomUUID().replaceAll('-', ''),
    rootSpanId: randomUUID().replaceAll('-', '').slice(0, 16),
    childSpanId: randomUUID().replaceAll('-', '').slice(16),
    startTimeUnixNano, endTimeUnixNano: startTimeUnixNano + 50_000_000n,
    childAttributes: { 'fi.span.kind': 'llm', [MAPPED_ATTRIBUTE]: answer },
  });
  const ids = traceConfig(firstAnswer);
  const answerInCh = async (spanId: string) => {
    const rows = await probe.ch<{ answer: string }>(
      `SELECT attrs_string[{k:String}] AS answer FROM spans FINAL WHERE id = {s:String}`,
      { k: MAPPED_ATTRIBUTE, s: spanId });
    return rows[0]?.answer;
  };

  try {
    const seeded = await sendTrace(req, ids);
    const spanId = seeded.spanIds[1];
    await expect.poll(async () => {
      const rows = await probe.ch<{ n: string }>(
        'SELECT count() AS n FROM spans FINAL WHERE trace_id = {t:String}', { t: seeded.traceId });
      return Number(rows[0].n);
    }, POLL.SPAN_VISIBLE).toBe(seeded.spanIds.length);
    const [{ project_id: projectId }] = await probe.ch<{ project_id: string }>(
      'SELECT DISTINCT project_id FROM spans FINAL WHERE trace_id = {t:String}', { t: seeded.traceId });

    await test.step('register the trace and the LLM span in Postgres', async () => {
      // Feedback and Recalculate look the span up in Postgres
      // (tracer/views/observation_span.py:submit_feedback and
      // submit_feedback_action_type), and fi-collector writes ClickHouse only.
      // The public trace and span API registers the same ids, as an SDK on the
      // Django ingestion path would. Bodies per tracer/views/trace.py:bulk_create
      // and tracer/serializers/observation_span.py:ObservationSpanSerializer.
      await actor.api.post('/tracer/trace/bulk_create/', {
        traces: [{ id: seeded.traceId, project: projectId, name: 'e2e.root' }],
      });
      await actor.api.post('/tracer/observation-span/', {
        id: spanId, project: projectId, trace: seeded.traceId, name: 'e2e.llm-call',
        observation_type: 'llm',
        start_time: new Date(Date.now() - 60_000).toISOString(),
        end_time: new Date().toISOString(),
      });
    });

    const { templateId, configId } = await test.step('configure an LLM judge that resolves to the mock', async () => {
      // Wire bodies as in flows/evals/eval-task.spec.ts (pinned there from the
      // serializers and a captured Create Task request).
      // The actor is worker-scoped and the model name is unique per org: a flow
      // that ran earlier in this worker (EVAL-E2E-001, EVAL-E2E-002) may have
      // registered the same judge, against the same gateway, already.
      const registered = await probe.pg(
        `SELECT id FROM model_hub_customaimodel
          WHERE organization_id = $1 AND user_model_id = $2 AND deleted = false`,
        [actor.organizationId, JUDGE_MODEL]);
      if (registered.length === 0) {
        await actor.api.post('/model-hub/custom_models/create/', {
          model_provider: 'openai', model_name: JUDGE_MODEL,
          input_token_cost: 0, output_token_cost: 0,
          config_json: { key: GATEWAY_INTERNAL_KEY, api_base: GATEWAY_INTERNAL_URL },
        });
      }
      const template = await actor.api.post<CreatedId>('/model-hub/eval-templates/create-v2/', {
        name: `e2e-recalc-judge-${suffix}`,
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
      return { templateId: template.result.id, configId: config.result.id };
    });

    const taskId = await test.step('run an eval task over the project', async () => {
      const now = Date.now();
      const created = await actor.api.post<CreatedId>('/tracer/eval-task/', {
        name: `e2e recalc task ${suffix}`,
        project: projectId,
        evals: [configId],
        filters: {
          project_id: projectId,
          date_range: [new Date(now - 3_600_000).toISOString(), new Date(now + 3_600_000).toISOString()],
        },
        run_type: 'historical', row_type: 'spans', spans_limit: 100000, sampling_rate: 100,
      });
      const id = created.result.id;
      await expect.poll(async () => {
        const task = await actor.api.get<{ status: string }>(`/tracer/eval-task/${id}/`);
        return task.status;
      }, POLL.EVAL_RESULT).toBe('completed');
      return id;
    });
    await testInfo.attach('seeded ids', {
      contentType: 'application/json',
      body: JSON.stringify({ projectId, traceId: seeded.traceId, spanId, templateId, configId, taskId }, null, 2),
    });

    // Every result for the span and eval, live or retired, on the primary.
    const resultsInPg = () => probe.pg<EvalRow>(
      `SELECT id::text AS id, status, deleted, eval_explanation FROM tracer_eval_logger
        WHERE observation_span_id = $1 AND custom_eval_config_id = $2 ORDER BY created_at`,
      [spanId, configId]);

    const previous = await test.step('storage lane: the task left one live completed result', async () => {
      const rows = await resultsInPg();
      expect(rows.map((r) => [r.status, r.deleted])).toEqual([['completed', false]]);
      expect(rows[0].eval_explanation).toBe(`${verdict} saw ${firstAnswer}`);
      return rows[0];
    });

    const feedbackId = await test.step('leave feedback on the result', async () => {
      // The Observe feedback form's request
      // (frontend/src/components/traceDetailDrawer/dialogue-boxes/add-feedback-form.jsx:handleFormSubmit).
      const fb = await actor.api.post<FeedbackCreated>('/tracer/observation-span/submit_feedback/', {
        observation_span_id: spanId,
        custom_eval_config_id: configId,
        feedback_value: 'Failed',
        feedback_explanation: `${verdict} saw ${firstAnswer}`,
        feedback_improvement: note,
      });
      return fb.result.feedback_id;
    });
    await testInfo.attach('feedback id', { body: feedbackId });

    await test.step('the span\'s input changes before the Recalculate', async () => {
      // The same span, re-sent with a new value for the mapped attribute. Only
      // a judge that runs again can produce a verdict that names it.
      await sendTrace(req, { ...ids, childAttributes: { 'fi.span.kind': 'llm', [MAPPED_ATTRIBUTE]: secondAnswer } });
      await expect.poll(() => answerInCh(spanId), POLL.SPAN_VISIBLE).toBe(secondAnswer);
    });

    await test.step('UI: Edit Feedback, pick Re-calculate and submit', async () => {
      await page.goto(`/dashboard/evaluations/${templateId}`);
      await page.getByRole('tab', { name: /^Feedback/ }).click({ timeout: UI_READY });
      // The Feedback History table shows the feedback's explanation as its
      // "Improvement Note" (components/EvalFeedbackTab.jsx); the form sends the
      // result's explanation there, which carries this run's minted verdict.
      await page.getByText(`${verdict} saw ${firstAnswer}`).click({ timeout: UI_READY });
      await page.getByRole('button', { name: 'Edit Feedback' }).click({ timeout: UI_READY });
      await page.getByText('Re-calculate and re-tune').click({ timeout: UI_READY });
      const recalculated = page.waitForResponse(
        (r) => r.url().includes('/tracer/observation-span/submit_feedback_action_type/')
          && r.request().method() === 'POST',
        { timeout: UI_READY });
      await page.getByRole('button', { name: 'Submit feedback' }).click();
      const response = await recalculated;
      expect(response.status()).toBe(200);
      const body = await response.request().postDataJSON() as { action_type: string; feedback_id: string };
      expect([body.action_type, body.feedback_id]).toEqual(['recalculate', feedbackId]);
      // The submit landed on this feedback, which keeps the user's note.
      const [stored] = await probe.pg<{ action_type: string; feedback_improvement: string }>(
        'SELECT action_type, feedback_improvement FROM model_hub_feedback WHERE id = $1', [feedbackId]);
      expect([stored.action_type, stored.feedback_improvement]).toEqual(['recalculate', note]);
    });

    const replacement = await test.step('storage lane: one live result, a new one, the old retired', async () => {
      const rows = await resultsInPg();
      const live = rows.filter((r) => !r.deleted);
      expect(live.map((r) => r.status)).toEqual(['completed']);
      expect(live[0].id).not.toBe(previous.id);
      // Computed again, on the span's current input: not the previous verdict.
      expect(live[0].eval_explanation).toBe(`${verdict} saw ${secondAnswer}`);
      expect(rows.find((r) => r.id === previous.id)?.deleted).toBe(true);
      const [entry] = await probe.pg<{ eval_task_id: string }>(
        'SELECT eval_task_id FROM tracer_eval_logger WHERE id = $1', [live[0].id]);
      expect(entry.eval_task_id).toBe(taskId);
      return live[0];
    });

    await test.step('storage lane: the mirror carries the replacement and the retirement to CH', async () => {
      // FINAL: the entries' PG rows are updated through their lifecycle, and an
      // unmerged read of this ReplacingMergeTree returns a stale version.
      await expect.poll(async () => {
        const rows = await probe.ch<{ id: string; status: string; deleted: number | boolean }>(
          `SELECT toString(id) AS id, status, deleted FROM tracer_eval_logger FINAL
            WHERE observation_span_id = {s:String} AND toString(custom_eval_config_id) = {c:String}
            ORDER BY id`,
          { s: spanId, c: configId });
        return rows
          .map((r) => `${r.id === previous.id ? 'previous' : r.id === replacement.id ? 'replacement' : r.id}:${r.status}:${Number(r.deleted)}`)
          .sort();
      }, POLL.CDC_VISIBLE).toEqual(['previous:completed:1', 'replacement:completed:0']);
    });

    await test.step('UI: the span shows the recalculated result in Observe', async () => {
      await page.goto(`/dashboard/observe/${projectId}/llm-tracing?selectedTab=spans`);
      await page.getByText('e2e.llm-call').first().click({ timeout: UI_READY });
      await page.getByRole('tab', { name: 'Evals' }).click({ timeout: UI_READY });
      await expect(page.getByText('1/1 passed')).toBeVisible({ timeout: UI_READY });
      // One row for the eval: the replacement, not the retired result beside it.
      await expect(page.getByText(evalName, { exact: true })).toHaveCount(1, { timeout: UI_READY });
      // The row keeps the judge's explanation collapsed (as in EVAL-E2E-001).
      await page.getByText(evalName, { exact: true }).click();
      await expect(page.getByText(`${verdict} saw ${secondAnswer}`)).toBeVisible({ timeout: UI_READY });
      await expect(page.getByText(`${verdict} saw ${firstAnswer}`)).toHaveCount(0);
    });
  } finally {
    await req.dispose();
  }
});
