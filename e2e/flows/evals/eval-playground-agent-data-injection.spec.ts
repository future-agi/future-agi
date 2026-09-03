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
// Data injection is a pure UI/config concern — `contextOptions` never leaves
// the browser as anything but the `data_injection` field on the eval's own
// config (buildDataInjection in frontend/src/sections/common/EvalPicker/
// evalPickerConfigUtils.js); there's no capability gate on it at all.
//
// Run mode stays "Quick" for the same reason as the other three agent
// flows, with one extra wrinkle worth calling out: `evaluator.py` silently
// upgrades quick -> auto (re-enabling tools/iterations) IF the *playground
// run itself* is supplied span/trace/session/call context (i.e. driven from
// the Tracing/Simulation source tabs). This flow tests from the default
// "Custom" source tab, which only ever sends a `mapping` of template
// variables — never span/trace/session/call context — so that upgrade path
// never triggers here regardless of what `data_injection` is configured to.
// What's under test is that the *eval's stored config* reflects the chosen
// context-injection option; not that a real span actually gets injected
// during this particular run.

interface EvalDetailResponse {
  result: {
    id: string; eval_type: string;
    config: { data_injection?: Record<string, boolean>; agent_mode?: string };
  };
}

test('EVAL-E2E-026: author a new Agent eval, turn on data injection, test it and publish', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-026', area: 'evals',
    userGoal: 'A developer creates an Agent-type eval and configures full span context to be '
      + 'injected when it runs, tests it and publishes',
    steps: ['open the create-eval page', 'confirm Agent is the default, unlocked eval type',
            'name the eval', 'switch run mode to Quick', 'pick a mock-routed model',
            'write instructions with a template variable',
            'turn on "Full span context" from the model bar\'s Data Injection picker',
            'test the draft against custom input and read the Pass verdict', 'save/publish the eval'],
    backendChecks: ['the published eval is stored with eval_type "agent"',
                    'the published eval\'s config.data_injection.span_context is true',
                    'the published eval\'s config.agent_mode is "quick"'],
  }),
}, async ({ page, actor, capabilities }, testInfo) => {
  // Every bounded wait in this spec, chained: past the config's 120s
  // default, so a slow run ends on the assertion that ran out rather
  // than a bare test timeout.
  test.setTimeout(300_000);
  // agentic_eval is oss_locked, so this flow needs a license naming it.
  // Unentitled, assert the tab is locked rather than skipping.
  if (!allowed(capabilities, 'agentic_eval')) {
    await assertAgentTabLocked(page);
    return;
  }


  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const evalName = `e2e-agent-injection-${suffix}`;
  const verdict = `e2e-verdict-${suffix}`;

  await ensureJudgeModel(actor);

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

  await test.step('UI: turn on "Full span context" from the model bar\'s "+" menu', async () => {
    await page.locator(`button:right-of(:text("${JUDGE_MODEL}"))`).first().click();
    await page.getByRole('menuitem').filter({ hasText: 'Data Injection' }).click();
    await page.getByRole('menuitem').filter({ hasText: 'Full span context' }).click();
    await page.keyboard.press('Escape');
    // Toggling any non-default context option collapses to a "+N context"
    // chip (ModelSelector.jsx) rather than the option's own label.
    await expect(page.getByText('+1 context', { exact: true })).toBeVisible();
  });

  await test.step('UI: test against custom input — Pass', async () => {
    await fillTestData(page, '{"output": "world"}');
    await page.getByRole('button', { name: 'Test Evaluation' }).click();
    await expect(page.getByRole('button', { name: 'Test Evaluation' })).toBeVisible({ timeout: EVAL_RUN });
    await expect(page.getByText('Pass', { exact: true })).toBeVisible();
    await expect(page.getByText(`${verdict} saw world`)).toBeVisible();
  });

  await test.step('UI: publish, and API lane confirms data injection + mode persisted', async () => {
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
    expect(detail.result.config.data_injection?.span_context).toBe(true);
    // The wire field is `mode` (EvalCreatePage's update payload), but
    // separate_evals.py persists it under a different key:
    // `template.config["agent_mode"] = req.mode` (:2711, and :2193 on
    // first create). Assert the stored key, which is also the one both
    // EvalCreatePage (:305) and EvalDetailPage (:380) read back.
    expect(detail.result.config.agent_mode).toBe('quick');
  });
});
