import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { JUDGE_MODEL, ensureJudgeModel, fillTestData } from '../../lib/eval-model';

// Routes the judge model through the mock LLM behind the real gateway — same
// setup as eval-task.spec.ts. The mock echoes back the exact prompt text, so
// whatever JSON the instructions dictate is the JSON the eval parser reads.

interface CreatedId { result: { id: string } }
interface EvalListResponse { result: { items: { id: string; name: string }[]; total: number } }

test('EVAL-E2E-002: search, test and edit an eval in the playground', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-002', area: 'evals',
    userGoal: 'A developer finds an eval in the list, tests it against sample input, edits its config and re-tests',
    steps: ['open the evaluations list', 'search for the eval by name',
            'open the matching eval', 'run a test against custom input',
            'read the Pass verdict and explanation', 'edit the eval instructions',
            're-run the test', 'read the updated Fail verdict'],
    backendChecks: ['the search endpoint returns exactly the eval created for this run',
                    'the playground test runs synchronously against the mock LLM through the real gateway',
                    'editing instructions in the UI changes the next test result without saving first'],
  }),
}, async ({ page, actor }, testInfo) => {
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  // Hyphens, not spaces: create-v2 rejects anything outside [a-z0-9_-]
  // with 400 "Name can only contain lowercase letters, numbers, hyphens
  // (-), or underscores (_)."
  const evalName = `e2e-playground-judge-${suffix}`;
  // Exists nowhere else, so finding it in the result proves the round trip.
  const verdict = `e2e-verdict-${suffix}`;

  await ensureJudgeModel(actor);

  const template = await actor.api.post<CreatedId>('/model-hub/eval-templates/create-v2/', {
    name: evalName, eval_type: 'llm',
    instructions: `Reply with exactly this JSON: {"result": "Pass", "explanation": "${verdict} saw {{output}}"}`,
    model: JUDGE_MODEL, output_type: 'pass_fail', pass_threshold: 0.5,
  });
  const templateId = template.result.id;

  await test.step('API lane: search returns exactly this eval', async () => {
    const list = await actor.api.post<EvalListResponse>('/model-hub/eval-templates/list/', { search: evalName });
    expect(list.result.items.map((i) => i.id)).toEqual([templateId]);
  });

  await test.step('UI: filter the list down to this eval and open it', async () => {
    await page.goto('/dashboard/evaluations');
    await page.getByPlaceholder('Search').fill(evalName);
    await expect(page.getByText(evalName)).toBeVisible({ timeout: 15_000 });
    await page.getByText(evalName).click();
    await expect(page).toHaveURL(new RegExp(`/dashboard/evaluations/${templateId}`));
  });

  await test.step('UI: test the eval against custom input', async () => {
    // Custom is the playground's default source tab — no tab click needed.
    await fillTestData(page, '{"output": "world"}');
    await page.getByRole('button', { name: 'Test Evaluation' }).click();
    await expect(page.getByRole('button', { name: 'Test Evaluation' })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText('Pass', { exact: true })).toBeVisible();
    await expect(page.getByText(`${verdict} saw world`)).toBeVisible();
  });

  await test.step('UI: edit the instructions and re-test — the verdict flips', async () => {
    // Replace the whole instruction rather than double-clicking the word
    // "Pass". Quill renders the block as one text run plus a <strong> mention
    // node for {{output}}, so no element's text is exactly "Pass" and
    // `.ql-editor` scoped getByText('Pass', { exact: true }) matches nothing
    // at all — the dblclick just waited out the whole test timeout. Retyping
    // is also how every authoring flow in this suite enters instructions
    // containing {{output}}, so the mention round-trips the same way there.
    await page.locator('.ql-editor').click();
    await page.keyboard.press('ControlOrMeta+A');
    await page.keyboard.press('Backspace');
    await page.keyboard.type(
      `Reply with exactly this JSON: {"result": "Fail", "explanation": "${verdict} saw {{output}}"}`,
      { delay: 10 },
    );
    await page.keyboard.press('Escape');

    await page.getByRole('button', { name: 'Test Evaluation' }).click();
    await expect(page.getByRole('button', { name: 'Test Evaluation' })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText('Fail', { exact: true })).toBeVisible();
  });
});
