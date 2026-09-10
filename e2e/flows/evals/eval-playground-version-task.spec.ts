import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { JUDGE_MODEL, ensureJudgeModel } from '../../lib/eval-model';

// Browser-side waits. The stack slows several-fold when specs run in parallel
// (CI runs two workers), so these are sized off that rather than the 10s
// expect default.
const UI_READY = 60_000;

// Same gateway/mock setup as eval-task.spec.ts — see that file's header
// comment for why these exact values (internal compose hostname + shared
// key) are used instead of E2E.gatewayUrl.
const MAPPED_ATTRIBUTE = 'fi.span.kind';
const MOCK_USAGE = { prompt_tokens: 7, completion_tokens: 7, total_tokens: 14 };

interface CreatedId { result: { id: string } }
interface EvalVersionsResponse { result: { versions: { id: string; version_number: number; is_default: boolean }[] } }
interface EvalResultRow {
  status: string;
  output_bool: boolean | number | null;
  eval_explanation: string | null;
  output_metadata: string;
}

// Why there's no "pick a version for this eval task" step anywhere below:
//
// An eval task's config (tracer/serializers/custom_eval_config.py,
// eval_task.py) points `CustomEvalConfig.eval_template` at the EvalTemplate
// row itself — there is no version FK, and no `version` field on either
// serializer. At run time CustomPromptEvaluator is built from
// `EvalTemplate.criteria` / `.config` / `.model` directly (confirmed via
// EvalTemplateDetailView, model_hub/views/separate_evals.py ~2362-2377:
// "Detail should reflect current template state... Version snapshots are
// immutable and available in /versions.") — i.e. these are the *live*
// columns, not a snapshot pointer. EvalTemplateVersion rows exist purely as
// point-in-time snapshots for history/restore (SetDefaultVersionView,
// RestoreVersionView); saving a new version updates the live template AND
// writes the snapshot in the same request
// (EvalDetailPage.jsx's handleSaveVersion calls updateEval then
// createVersion). So "use version N in an eval task" is simply: whatever
// version is currently saved on the template is what any eval task created
// afterwards runs — proven below by creating V2 with an inverted verdict
// and a distinguishing marker, then showing the eval task result reflects
// V2, not the V1 it was originally created with.
test('EVAL-E2E-020: save a new eval version from the detail page and confirm an eval task runs it', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-020', area: 'evals',
    userGoal: 'A developer edits an existing eval\'s instructions on its detail page, saves a new '
      + 'version, then creates an eval task that runs against the project\'s ingested spans',
    steps: ['seed a trace', 'point an OpenAI-compatible judge model at the gateway',
            'create an LLM-as-a-judge eval whose V1 instructions verdict Fail',
            'open the eval\'s detail page', 'rewrite the instructions to verdict Pass instead',
            'click Save Version and read the "Version V2 saved" confirmation',
            'create an eval task against the project', 'wait for completion',
            'read the eval result and confirm it reflects V2, not V1'],
    backendChecks: ['GET .../versions/ shows exactly one version (V1, is_default) right after creation',
                    'clicking Save Version calls PUT .../update/ (updates the live EvalTemplate columns) '
                      + 'then POST .../versions/create/, and GET .../versions/ now shows two versions',
                    'the eval task (Temporal workflow) resolves to completed, and its CH result carries '
                      + 'V2\'s verdict/marker/output_bool — proving the run read the live template state, '
                      + 'not the V1 config the CustomEvalConfig was originally pointed at',
                    'the result still carries the mock LLM\'s fixed token usage, confirming it went '
                      + 'through the same worker -> gateway -> mock hop as eval-task.spec.ts'],
  }),
}, async ({ page, actor, probe }, testInfo) => {

  // Same 270s Temporal+CDC floor as eval-task.spec.ts, plus headroom for the
  // extra UI edit-and-save step before the task is even created.
  test.setTimeout(420_000);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `e2e-eval-ver-${suffix}`;
  const evalName = `e2e-version-judge-${suffix}`;
  // Exists nowhere else, so finding it (with the "v2" tag) on the result
  // proves the judge prompt live at task-run time was V2's, not V1's.
  const verdict = `e2e-verdict-${suffix}`;

  const seeded = await sendTrace(req, {
    collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
    secretKey: actor.secretKey, projectName,
  });
  await testInfo.attach('seeded-trace', { body: JSON.stringify(seeded), contentType: 'application/json' });
  await expect.poll(async () => {
    const rows = await probe.ch<{ n: string }>(
      'SELECT count() AS n FROM spans FINAL WHERE trace_id = {t:String}', { t: seeded.traceId });
    return Number(rows[0].n);
  }, POLL.SPAN_VISIBLE).toBe(seeded.spanIds.length);

  const [{ project_id: projectId }] = await probe.ch<{ project_id: string }>(
    'SELECT DISTINCT project_id FROM spans FINAL WHERE trace_id = {t:String}', { t: seeded.traceId });

  await ensureJudgeModel(actor);

  const templateId = await test.step('API: create V1 — a Fail verdict', async () => {
    const template = await actor.api.post<CreatedId>('/model-hub/eval-templates/create-v2/', {
      name: evalName, eval_type: 'llm',
      instructions: `Reply with exactly this JSON: {"result": "Fail", "explanation": "${verdict} v1 saw {{output}}"}`,
      model: JUDGE_MODEL, output_type: 'pass_fail', pass_threshold: 0.5,
    });
    const versions = await actor.api.get<EvalVersionsResponse>(
      `/model-hub/eval-templates/${template.result.id}/versions/`);
    expect(versions.result.versions).toHaveLength(1);
    expect(versions.result.versions[0]).toMatchObject({ version_number: 1, is_default: true });
    return template.result.id;
  });

  await test.step('UI: edit the instructions on the detail page to a Pass verdict and save V2', async () => {
    await page.goto(`/dashboard/evaluations/${templateId}`);
    await expect(page.getByText(evalName)).toBeVisible({ timeout: UI_READY });
    // Save Version starts disabled — nothing is dirty yet.
    await expect(page.getByRole('button', { name: 'Save Version' })).toBeDisabled();

    // Select-all + retype rather than double-click-replacing individual
    // words (eval-code.spec.ts's proven pattern for a non-empty editor,
    // applied here to Quill's contenteditable instead of Monaco) — avoids
    // depending on exactly how Quill's DOM splits this sentence into nodes.
    await page.locator('.ql-editor').click();
    await page.keyboard.press('ControlOrMeta+A');
    await page.keyboard.press('Backspace');
    await page.keyboard.type(
      `Reply with exactly this JSON: {"result": "Pass", "explanation": "${verdict} v2 saw {{output}}"}`,
      { delay: 10 },
    );
    // Dismiss any mention-autocomplete popup the "{{" denotation may have opened.
    await page.keyboard.press('Escape');

    await expect(page.getByRole('button', { name: 'Save Version' })).toBeEnabled();
    await page.getByRole('button', { name: 'Save Version' }).click();
    await expect(page.getByText('Version V2 saved')).toBeVisible({ timeout: UI_READY });
  });

  await test.step('API lane: a second version now exists', async () => {
    const versions = await actor.api.get<EvalVersionsResponse>(`/model-hub/eval-templates/${templateId}/versions/`);
    expect(versions.result.versions.map((v) => v.version_number).sort()).toEqual([1, 2]);
  });

  const evalConfigId = await test.step('configure the eval on the project', async () => {
    const config = await actor.api.post<CreatedId>('/tracer/custom-eval-config/', {
      project: projectId, eval_template: templateId, name: evalName,
      model: JUDGE_MODEL,
      mapping: { output: MAPPED_ATTRIBUTE },
      config: { mapping: { output: MAPPED_ATTRIBUTE } },
      error_localizer: false,
    });
    return config.result.id;
  });

  const taskId = await test.step('create the eval task', async () => {
    const now = Date.now();
    const created = await actor.api.post<CreatedId>('/tracer/eval-task/', {
      name: `e2e eval version task ${suffix}`,
      project: projectId,
      evals: [evalConfigId],
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

  await test.step('storage lane: the result reflects V2, not the V1 the config was created against', async () => {
    let row: EvalResultRow | undefined;
    await expect.poll(async () => {
      const rows = await probe.ch<EvalResultRow>(
        `SELECT status, output_bool, eval_explanation, output_metadata FROM tracer_eval_logger FINAL
         WHERE eval_task_id = {t:String} AND observation_span_id = {s:String}`,
        { t: taskId, s: seeded.spanIds[1] });
      row = rows[0];
      return row?.status;
    }, POLL.CDC_VISIBLE).toBe('completed');

    // V1 said Fail; only V2 says Pass — this is the whole point of the flow.
    expect([true, 1]).toContain(row?.output_bool);
    expect(row?.eval_explanation).toBe(`${verdict} v2 saw llm`);
    const metadata = JSON.parse(row?.output_metadata ?? '{}') as { usage?: Record<string, number> };
    expect(metadata.usage).toEqual(MOCK_USAGE);
  });

  await req.dispose();
});
