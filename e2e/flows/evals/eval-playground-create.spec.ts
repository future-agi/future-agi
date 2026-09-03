import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { JUDGE_MODEL, ensureJudgeModel, fillTestData, selectJudgeModel } from '../../lib/eval-model';

// Browser-side waits. The stack slows several-fold when specs run in parallel
// (CI runs two workers), so these are sized off that rather than the 10s
// expect default.
const UI_READY = 60_000;
// One synchronous playground run: prompt -> gateway -> mock LLM -> parse ->
// render, sized for the slowest case (a composite fanning out to children).
const EVAL_RUN = 90_000;

// Routes the judge model through the mock LLM behind the real gateway — same
// setup as eval-task.spec.ts / eval-playground.spec.ts. The mock echoes back
// the exact prompt text, so whatever JSON the instructions dictate is the
// JSON the eval parser reads back.

interface EvalListResponse { result: { items: { id: string; name: string }[]; total: number } }
interface EvalDetailResponse { result: { id: string; instructions: string } }

test('EVAL-E2E-003: author a new LLM eval from scratch, test it and publish', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-003', area: 'evals',
    userGoal: 'A developer creates a new LLM-as-a-judge eval, picks a model, writes instructions, tests it and publishes',
    steps: ['open the create-eval page', 'name the eval', 'switch to the LLM-as-a-Judge type',
            'pick a model', 'write judge instructions with a template variable',
            'test the draft against custom input', 'read the Pass verdict',
            'save/publish the eval'],
    backendChecks: ['a template row is auto-created on page load and is fetchable at eval-templates/<id>/detail/',
                    'the draft carries the typed instructions by the time the playground test has run',
                    'publishing sets it visible and searchable in the main eval list'],
  }),
}, async ({ page, actor }, testInfo) => {
  // Every bounded wait in this spec, chained: past the config's 120s
  // default, so a slow run ends on the assertion that ran out rather
  // than a bare test timeout.
  test.setTimeout(240_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  // Sanitized to [a-z0-9_-] on input by the Eval Name field itself — no spaces/case.
  const evalName = `e2e-create-judge-${suffix}`;
  // Exists nowhere else, so finding it in the result proves the round trip.
  const verdict = `e2e-verdict-${suffix}`;

  await ensureJudgeModel(actor);

  let draftId = '';

  await test.step('UI: a draft is auto-created on page load', async () => {
    await page.goto('/dashboard/evaluations/create');
    await page.waitForURL(/\/dashboard\/evaluations\/create\/.+/, { timeout: UI_READY });
    draftId = page.url().split('/dashboard/evaluations/create/')[1];
    expect(draftId).toMatch(/.+/);
    await testInfo.attach('draft-id', { body: draftId, contentType: 'text/plain' });
    // The URL alone only proves the SPA routed somewhere. Fetching the id
    // back is what proves a row was actually created behind it.
    const draft = await actor.api.get<EvalDetailResponse>(`/model-hub/eval-templates/${draftId}/detail/`);
    expect(draft.result.id).toBe(draftId);
  });

  await test.step('UI: name it and switch to LLM-as-a-Judge', async () => {
    await page.getByPlaceholder('Eg: Hallucination detector').fill(evalName);
    await page.getByRole('tab', { name: 'LLM-As-A-Judge' }).click();
  });

  await test.step('UI: pick the mock-routed model', async () => {
    await selectJudgeModel(page, JUDGE_MODEL);
  });

  await test.step('UI: write judge instructions with a template variable', async () => {
    await page.locator('.ql-editor').click();
    await page.keyboard.type(
      `Reply with exactly this JSON: {"result": "Pass", "explanation": "${verdict} saw {{output}}"}`,
      { delay: 10 },
    );
    // Dismiss any mention-autocomplete popup the "{{" denotation may have opened.
    await page.keyboard.press('Escape');
  });

  await test.step('UI: test the draft against custom input', async () => {
    await fillTestData(page, '{"output": "world"}');
    await page.getByRole('button', { name: 'Test Evaluation' }).click();
    await expect(page.getByRole('button', { name: 'Test Evaluation' })).toBeVisible({ timeout: EVAL_RUN });
    await expect(page.getByText('Pass', { exact: true })).toBeVisible();
    await expect(page.getByText(`${verdict} saw world`)).toBeVisible();

    // EvalCreatePage autosaves the draft before running it
    // (EvalCreatePage.jsx:572), so the instructions the run used are already
    // persisted — read them back rather than trusting the rendered result.
    const saved = await actor.api.get<EvalDetailResponse>(`/model-hub/eval-templates/${draftId}/detail/`);
    expect(saved.result.instructions).toContain(verdict);
  });

  await test.step('UI: publish, and API lane confirms it is now visible and searchable', async () => {
    await page.getByRole('button', { name: 'Save Evaluation' }).click();
    await expect(page.getByText('Evaluation saved successfully')).toBeVisible({ timeout: UI_READY });
    // EvalDetailPage appends `?v=<version_number>` once it has loaded the
    // saved version (:417), so anchor on the id followed by end-of-string
    // OR the query string rather than end-of-string alone.
    await expect(page).toHaveURL(new RegExp(`/dashboard/evaluations/${draftId}(\\?|$)`));

    const list = await actor.api.post<EvalListResponse>('/model-hub/eval-templates/list/', { search: evalName });
    expect(list.result.items.map((i) => i.id)).toEqual([draftId]);
  });
});
