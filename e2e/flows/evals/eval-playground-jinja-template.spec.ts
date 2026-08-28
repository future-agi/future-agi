import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { JUDGE_MODEL, ensureJudgeModel, fillTestData, selectJudgeModel } from '../../lib/eval-model';

// Routes the judge model through the mock LLM behind the real gateway — same
// setup as eval-create.spec.ts / eval-task.spec.ts.

interface EvalDetailResponse { result: { template_format: string } }
interface EvalListResponse { result: { items: { id: string; name: string }[]; total: number } }

// Why this doesn't try to prove Jinja-only syntax (e.g. {% if %}) actually
// renders differently from Mustache:
//
// futureagi/ee/evals/llm/custom_prompt_evaluator/evaluator.py builds ONE
// jinja2.Environment (variable_start_string="{{", variable_end_string="}}")
// and renders every LLM eval's rule_prompt through it regardless of
// `self.template_format` — that field is only consulted afterwards, to
// decide whether to json.loads() string context values so `{% for %}` loops
// see native lists/dicts (lines ~230-242). So a plain `{{output}}` variable
// is rendered byte-for-byte identically under "mustache" and "jinja" — the
// mock-echo-verdict mechanism eval-create.spec.ts relies on is a drop-in
// swap, not something that needs re-proving here.
//
// What genuinely differs by format, and what this flow exercises instead:
// the frontend's own variable-detection/gating switches from a `{{...}}`
// regex to a real nunjucks AST parse (extractJinjaVariables /
// frontend/src/utils/jinjaVariables.js), and `template_format` is persisted
// on EvalTemplate.config and round-trips back out of GET .../detail/
// (model_hub/views/separate_evals.py EvalTemplateDetailView, line ~2432).
// That UI toggle, its persistence, and the eval still completing end-to-end
// in that mode are exactly what's asserted below.
test('EVAL-E2E-018: author a new LLM eval in Jinja template format, test it and publish', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-018', area: 'evals',
    userGoal: 'A developer creates an LLM-as-a-judge eval and switches its template '
      + 'format from the default Mustache to Jinja before writing instructions',
    steps: ['open the create-eval page', 'name the eval', 'switch to the LLM-As-A-Judge type',
            'pick a model', 'switch the template format from Mustache to Jinja',
            'write judge instructions with a {{variable}} in Jinja mode',
            'test the draft against custom input and read the Pass verdict',
            'save/publish the eval'],
    backendChecks: ['the saved template persists template_format: "jinja" in EvalTemplate.config, '
                      + 'returned as-is by GET /model-hub/eval-templates/<id>/detail/',
                    'the playground test still completes via the mock LLM through the real gateway — '
                      + 'the backend renders both template formats through the same jinja2 Environment',
                    'publishing sets it visible and searchable in the main eval list'],
  }),
}, async ({ page, actor }, testInfo) => {
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  // Sanitized to [a-z0-9_-] on input by the Eval Name field itself — no spaces/case.
  const evalName = `e2e-create-jinja-${suffix}`;
  // Exists nowhere else, so finding it in the result proves the round trip.
  const verdict = `e2e-verdict-${suffix}`;

  await ensureJudgeModel(actor);

  let draftId = '';

  await test.step('UI: a draft is auto-created on page load', async () => {
    await page.goto('/dashboard/evaluations/create');
    await page.waitForURL(/\/dashboard\/evaluations\/create\/.+/, { timeout: 15_000 });
    draftId = page.url().split('/dashboard/evaluations/create/')[1];
    expect(draftId).toMatch(/.+/);
  });

  await test.step('UI: name it and switch to LLM-as-a-Judge', async () => {
    await page.getByPlaceholder('Eg: Hallucination detector').fill(evalName);
    await page.getByRole('tab', { name: 'LLM-As-A-Judge' }).click();
  });

  await test.step('UI: pick the mock-routed model', async () => {
    await selectJudgeModel(page, JUDGE_MODEL);
  });

  await test.step('UI: switch the template format from Mustache to Jinja', async () => {
    // LLMPromptEditor.jsx renders the current format ("Mustache" by default)
    // as a clickable pill that opens a Popover of the two format choices.
    await expect(page.getByText('Mustache', { exact: true })).toBeVisible();
    await page.getByText('Mustache', { exact: true }).click();
    const jinjaItem = page.getByRole('menuitem', { name: 'Jinja' });
    await jinjaItem.click();
    // The menu item's label (LLMPromptEditor.jsx:398, a body2 Typography) and
    // the pill's label (:353) are both the exact text "Jinja", so asserting
    // on the pill while the Popover is still mounted trips strict mode.
    // `toBeHidden` is not enough — it passes as soon as the paper starts its
    // exit transition, while the node is still attached and still matched by
    // getByText. Wait for the Popover to actually unmount.
    // Wait on the POPOVER ELEMENT, not the menu item's role. MUI stamps
    // `aria-hidden` on a closing Modal before it unmounts, so `getByRole`
    // reports count 0 while the node is still attached and still matched by
    // getByText — leaving two "Jinja" nodes (the pill and the menu item).
    await expect(page.locator('.MuiPopover-root')).toHaveCount(0);
    await expect(page.getByText('Jinja', { exact: true })).toBeVisible();
  });

  await test.step('UI: write judge instructions with a Jinja variable', async () => {
    // {{output}} is valid under both formats (nunjucks parses a bare
    // Symbol reference the same way the old {{...}} regex would match it) —
    // deliberately not more exotic Jinja syntax, see the top-of-file note on
    // why that isn't needed to prove this path works.
    await page.locator('.ql-editor').click();
    await page.keyboard.type(
      `Reply with exactly this JSON: {"result": "Pass", "explanation": "${verdict} saw {{output}}"}`,
      { delay: 10 },
    );
    // Dismiss any mention-autocomplete popup the "{{" denotation may have
    // opened — MessageEditor.jsx also arms "{%" as a denotation char in
    // Jinja mode, but this instructions text never types that sequence.
    await page.keyboard.press('Escape');
  });

  await test.step('UI: test the draft against custom input', async () => {
    await fillTestData(page, '{"output": "world"}');
    await page.getByRole('button', { name: 'Test Evaluation' }).click();
    await expect(page.getByRole('button', { name: 'Test Evaluation' })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText('Pass', { exact: true })).toBeVisible();
    await expect(page.getByText(`${verdict} saw world`)).toBeVisible();
  });

  await test.step('UI: publish, and API lane confirms format + visibility', async () => {
    await page.getByRole('button', { name: 'Save Evaluation' }).click();
    await expect(page.getByText('Evaluation saved successfully')).toBeVisible({ timeout: 15_000 });
    // EvalDetailPage appends `?v=<version_number>` once it has loaded the
    // saved version (:417), so anchor on the id followed by end-of-string
    // OR the query string rather than end-of-string alone.
    await expect(page).toHaveURL(new RegExp(`/dashboard/evaluations/${draftId}(\\?|$)`));

    const detail = await actor.api.get<EvalDetailResponse>(`/model-hub/eval-templates/${draftId}/detail/`);
    expect(detail.result.template_format).toBe('jinja');

    const list = await actor.api.post<EvalListResponse>('/model-hub/eval-templates/list/', { search: evalName });
    expect(list.result.items.map((i) => i.id)).toEqual([draftId]);
  });
});
