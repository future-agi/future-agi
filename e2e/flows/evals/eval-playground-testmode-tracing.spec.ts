import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { JUDGE_MODEL, ensureJudgeModel } from '../../lib/eval-model';

// Second flow of Group B — see eval-testmode-dataset.spec.ts for the full
// scoping rationale (TestPlayground.test.jsx's delegation contract makes
// eval type orthogonal to which source tab is active, so one eval type per
// tab is enough). This flow exercises TracingTestMode, a fundamentally
// different data source (ingested spans, not dataset rows) with its own
// project/row-type picker and field-path resolver — a genuinely distinct
// mechanism from DatasetTestMode worth its own flow.
//
// Simulation's SimulationTestMode is the third SOURCE_TABS member and is
// skipped: unlike a dataset (create-dataset-manually) or a trace
// (sendTrace), this suite has no cheap, already-grounded way to seed a real
// simulation run — grepping e2e/lib and e2e/flows turns up no helper, and
// simulation runs are created through a multi-step scenario-generation flow
// (see project_tasks_xl_oom_scenario_gen in project memory) that isn't
// something to reverse-engineer into a UI script without risking an
// unverified interaction.

interface CreatedId { result: { id: string } }

test('EVAL-E2E-008: test an existing eval against a real span via the Tracing source tab', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-008', area: 'evals',
    userGoal: 'A developer opens a saved eval, switches the playground to the Tracing source tab, '
      + 'picks the project their trace landed in, maps the eval\'s variable to a real span field, '
      + 'and runs the test',
    steps: ['seed a trace with a root span and an llm-call child span', 'author a deterministic Code eval',
            'open the eval\'s detail page', 'switch the playground to the Tracing source tab',
            'search for and select the seeded project', 'map the code\'s "output" parameter to the span\'s name',
            'click Test Evaluation and read the Pass verdict and reason'],
    backendChecks: ['TracingTestMode fetches the real span via getSpansForObserveProject + getTrace, not a mock',
                    'the Test Evaluation button stays disabled until the mapped variable + a loaded row make '
                      + 'the tab ready (TracingTestMode\'s onReadyChange -> EvalDetailPage\'s isPlaygroundReady)',
                    'the eval executes in the sandboxed Python executor against the real span\'s name field'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(180_000);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `e2e-testmode-tracing-${suffix}`;
  const evalName = `e2e-testmode-tracing-code-${suffix}`;

  const seeded = await sendTrace(req, {
    collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey, projectName,
  });
  await expect.poll(async () => {
    const rows = await probe.ch<{ n: string }>(
      'SELECT count() AS n FROM spans FINAL WHERE trace_id = {t:String}', { t: seeded.traceId });
    return Number(rows[0].n);
  }, POLL.SPAN_VISIBLE).toBe(seeded.spanIds.length);

  // Both seeded spans (otlp.ts: "e2e.root" and "e2e.llm-call") share the
  // "e2e." prefix, so this check is deterministic regardless of which of the
  // two rows TracingTestMode's row navigator lands on by default (both spans
  // carry the same start timestamp in otlp.ts, so which sorts first isn't
  // something this flow controls or needs to).
  const evaluateCode = `def evaluate(output, **kwargs): `
    + `return {"score": 1.0, "reason": "observed name=" + str(output)} if str(output).startswith("e2e.") `
    + `else {"score": 0.0, "reason": "unexpected name " + str(output)}`;

  // `model` is mandatory even for a Code eval, which never calls one: the
  // playground re-saves the draft through the shared template serializer
  // before every run, and that serializer rejects a blank model with 400
  // "model: This field may not be blank." The run then ends as "Test
  // completed" with no verdict and only a snackbar to say why — which is
  // exactly how this flow failed. EVAL-E2E-007 sets it for the same reason;
  // EVAL-E2E-005 documents the UI-side version of the same constraint.
  await ensureJudgeModel(actor);
  const template = await actor.api.post<CreatedId>('/model-hub/eval-templates/create-v2/', {
    name: evalName, eval_type: 'code', code: evaluateCode, code_language: 'python',
    model: JUDGE_MODEL, output_type: 'pass_fail', pass_threshold: 0.5,
  });

  await page.goto(`/dashboard/evaluations/${template.result.id}`);

  await test.step('UI: switch to the Tracing source tab and pick the seeded project', async () => {
    await page.getByRole('tab', { name: 'Tracing' }).click();
    await page.getByPlaceholder('Search projects...').click();
    await page.getByRole('option', { name: projectName }).click();
  });

  await test.step('UI: map the variable to the span\'s name field', async () => {
    // TracingTestMode's mapping control is a real MUI Autocomplete (unlike
    // DatasetTestMode's custom ColumnTreeSelect), so a native option role
    // is available and unambiguous.
    await page.getByPlaceholder('Search column...').click();
    await page.getByPlaceholder('Search column...').fill('name');
    await page.getByRole('option', { name: 'name', exact: true }).click();
  });

  await test.step('UI: test — Pass, with the real span name proven in the reason', async () => {
    await page.getByRole('button', { name: 'Test Evaluation' }).click();
    await expect(page.getByRole('button', { name: 'Test Evaluation' })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText('Pass', { exact: true })).toBeVisible();
    await expect(page.getByText(/observed name=e2e\./)).toBeVisible();
  });

  await req.dispose();
});
