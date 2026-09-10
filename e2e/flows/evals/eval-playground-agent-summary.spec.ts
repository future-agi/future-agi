import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { allowed } from '../../lib/capabilities';
import { assertAgentTabLocked } from '../../lib/agent-eval';
import { JUDGE_MODEL, ensureJudgeModel, fillTestData, selectJudgeModel } from '../../lib/eval-model';

// Browser-side waits. The stack slows several-fold when specs run in parallel
// (CI runs two workers), so these are sized off that rather than the 10s
// expect default.
const UI_READY = 60_000;
// One synchronous playground run: prompt -> gateway -> mock LLM -> parse ->
// render, sized for the slowest case (a composite fanning out to children).
const EVAL_RUN = 90_000;

// See eval-agent-connectors.spec.ts for the full trace of why Agent-type is
// deterministically unlocked in this repo's e2e stack.
//
// Summary is a pure prompt-shaping config, folded into the same single
// system-prompt build as the rest of the agent instructions
// (`_build_eval_system_prompt` in ee/evals/llm/agent_evaluator/
// evaluator.py) — there's no separate LLM call for summary generation, in
// the playground or elsewhere, so it doesn't threaten the one-mock-call
// determinism the "Quick" mode gives the other three agent flows.
//
// This flow exercises two distinct summary configurations against the
// same eval: first a built-in non-default preset ("Long"), then a
// genuinely custom template (name + free-text criteria) created via the
// same summary-templates CRUD the "+" menu's "Create custom template" form
// itself calls (model_hub/views/eval_summary_templates.py). Selecting a
// saved template sets the eval's `summary` to `{type: "custom:<template
// id>"}` — SummarySubmenu.jsx never round-trips the literal string
// "custom" back out to EvalCreatePage; a real custom summary is always a
// reference to a saved template by id.

interface CreatedTemplate { result: { id: string; name: string } }
interface EvalDetailResponse {
  result: { id: string; eval_type: string; config: { summary?: { type?: string }; agent_mode?: string } };
}

test('EVAL-E2E-027: author a new Agent eval, configure a long then a custom summary, test it and publish', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-027', area: 'evals',
    userGoal: 'A developer creates an Agent-type eval, tries the built-in "Long" summary preset, '
      + 'then switches to a saved custom summary template, testing after each change, and publishes',
    steps: ['create a custom summary template via the API', 'open the create-eval page',
            'confirm Agent is the default, unlocked eval type', 'name the eval', 'switch run mode to Quick',
            'pick a mock-routed model', 'write instructions with a template variable',
            'select the "Long" summary preset and test — read the Pass verdict',
            'switch to the saved custom summary template and test again — read the Pass verdict',
            'save/publish the eval with the custom summary active'],
    backendChecks: ['the published eval is stored with eval_type "agent"',
                    'the published eval\'s config.summary.type is "custom:<template id>"',
                    'the published eval\'s config.agent_mode is "quick"'],
  }),
}, async ({ page, actor, capabilities }, testInfo) => {
  // Every bounded wait in this spec, chained: past the config's 120s
  // default, so a slow run ends on the assertion that ran out rather
  // than a bare test timeout.
  test.setTimeout(360_000);
  // agentic_eval is oss_locked, so this flow needs a license naming it.
  // Unentitled, assert the tab is locked rather than skipping.
  if (!allowed(capabilities, 'agentic_eval')) {
    await assertAgentTabLocked(page);
    return;
  }


  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const evalName = `e2e-agent-summary-${suffix}`;
  const templateName = `e2e-summary-tmpl-${suffix}`;
  const verdict = `e2e-verdict-${suffix}`;

  await ensureJudgeModel(actor);

  const template = await actor.api.post<CreatedTemplate>('/model-hub/eval-summary-templates/', {
    name: templateName,
    criteria: 'Provide a one-sentence summary focused on whether the marker text was found.',
  });
  const templateId = template.result.id;
  await testInfo.attach('template-id', { body: templateId, contentType: 'text/plain' });

  let draftId = '';

  await test.step('UI: a draft is auto-created, defaulting to Agent type (unlocked)', async () => {
    await page.goto('/dashboard/evaluations/create');
    await page.waitForURL(/\/dashboard\/evaluations\/create\/.+/, { timeout: UI_READY });
    draftId = page.url().split('/dashboard/evaluations/create/')[1];
    expect(draftId).toMatch(/.+/);
    await testInfo.attach('draft-id', { body: draftId, contentType: 'text/plain' });
    await expect(page.locator('.ql-editor')).toHaveAttribute(
      'data-placeholder', 'You are a helpful assistant', { timeout: UI_READY },
    );
  });

  await test.step('UI: name it', async () => {
    await page.getByPlaceholder('Eg: Hallucination detector').fill(evalName);
  });

  await test.step('UI: switch run mode to Quick', async () => {
    await page.getByText('Agent', { exact: true }).click();
    await page.getByRole('menuitem').filter({ hasText: 'Quick' }).click();
  });

  await test.step('UI: pick the mock-routed model', async () => {
    await selectJudgeModel(page, JUDGE_MODEL);
  });

  await test.step('UI: write instructions with a template variable', async () => {
    await page.locator('.ql-editor').click();
    await page.keyboard.type(
      `Reply with exactly this JSON: {"result": "Pass", "explanation": "${verdict} saw {{output}}"}`,
      { delay: 10 },
    );
    await page.keyboard.press('Escape');
  });

  await test.step('UI: select the "Long" summary preset and test — Pass', async () => {
    await page.locator(`button:right-of(:text("${JUDGE_MODEL}"))`).first().click();
    await page.getByRole('menuitem').filter({ hasText: 'Summary' }).click();
    await page.getByRole('menuitem').filter({ hasText: 'Long' }).click();
    // Selecting a preset closes the popover on its own (SummarySubmenu's
    // onSelect calls setPlusSubmenu(null)/setPlusAnchor(null)).
    await expect(page.getByText('Long', { exact: true })).toBeVisible();

    await fillTestData(page, '{"output": "world"}');
    await page.getByRole('button', { name: 'Test Evaluation' }).click();
    await expect(page.getByRole('button', { name: 'Test Evaluation' })).toBeVisible({ timeout: EVAL_RUN });
    await expect(page.getByText('Pass', { exact: true })).toBeVisible();
    await expect(page.getByText(`${verdict} saw world`)).toBeVisible();
  });

  await test.step('UI: switch to the saved custom summary template and test again — Pass', async () => {
    await page.locator(`button:right-of(:text("${JUDGE_MODEL}"))`).first().click();
    await page.getByRole('menuitem').filter({ hasText: 'Summary' }).click();
    // Click the template's own name text (not the whole menuitem row) —
    // the row also carries small edit/delete IconButtons at its right edge
    // that a row-center click could land on instead.
    await page.getByText(templateName, { exact: true }).click();
    // Only match left once the popover closes and the chip is all that's
    // left carrying this exact text.
    await expect(page.getByText(templateName, { exact: true })).toHaveCount(1);

    await fillTestData(page, '{"output": "world"}');
    await page.getByRole('button', { name: 'Test Evaluation' }).click();
    await expect(page.getByRole('button', { name: 'Test Evaluation' })).toBeVisible({ timeout: EVAL_RUN });
    await expect(page.getByText('Pass', { exact: true })).toBeVisible();
    await expect(page.getByText(`${verdict} saw world`)).toBeVisible();
  });

  await test.step('UI: publish, and API lane confirms the custom summary + mode persisted', async () => {
    await page.getByRole('button', { name: 'Save Evaluation' }).click();
    await expect(page.getByText('Evaluation saved successfully')).toBeVisible({ timeout: UI_READY });
    // EvalDetailPage appends `?v=<version_number>` once it has loaded the
    // saved version (:417), so anchor on the id followed by end-of-string
    // OR the query string rather than end-of-string alone.
    await expect(page).toHaveURL(new RegExp(`/dashboard/evaluations/${draftId}(\\?|$)`));

    const detail = await actor.api.get<EvalDetailResponse>(
      `/model-hub/eval-templates/${draftId}/detail/`,
    );
    expect(detail.result.eval_type).toBe('agent');
    expect(detail.result.config.summary?.type).toBe(`custom:${templateId}`);
    // The wire field is `mode` (EvalCreatePage's update payload), but
    // separate_evals.py persists it under a different key:
    // `template.config["agent_mode"] = req.mode` (:2711, and :2193 on
    // first create). Assert the stored key, which is also the one both
    // EvalCreatePage (:305) and EvalDetailPage (:380) read back.
    expect(detail.result.config.agent_mode).toBe('quick');
  });
});
