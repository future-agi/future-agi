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
// deterministically unlocked in this repo's e2e stack (AGENTIC_EVAL is
// `oss_locked=False` in tfc/capabilities/registry.py, so it's granted free
// on any non-cloud self-hosted deployment — and this e2e stack never sets
// CLOUD_DEPLOYMENT, confirmed via `docker compose ... config`).
//
// Knowledge bases need no such extra capability at all: FEATURE_KNOWLEDGE_
// BASE is `oss_baseline=True` in the registry, so `tfc/capabilities/
// service.py`'s `check()` grants it unconditionally at step 2, before any
// deployment-flavor branching — unlike the Connectors scenario, this one
// isn't even contingent on the local-dev EE_LICENSE_KEY in root `.env`.
//
// Run mode is "Quick" for the same determinism reason as the connectors
// flow: `ee/evals/llm/agent_evaluator/evaluator.py` only consults `tools`/
// `knowledge_bases` when `tools_allowed` is true, which quick mode forces
// off (`MAX_ITERATIONS = 1`, no tool branch at all). So attaching a
// knowledge base here proves the *configuration* persists correctly on the
// eval, without depending on a real embedding/retrieval round trip during
// the playground test run.

interface CreatedKB { result: { kb_id: string; kb_name: string } }
interface EvalDetailResponse {
  result: { id: string; eval_type: string; config: { knowledge_bases?: string[]; agent_mode?: string } };
}

test('EVAL-E2E-025: author a new Agent eval, attach a knowledge base, test it and publish', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-025', area: 'evals',
    userGoal: 'A developer creates an Agent-type eval, attaches a knowledge base for the evaluator '
      + 'to draw context from, tests it and publishes',
    steps: ['create an empty knowledge base via the API', 'open the create-eval page',
            'confirm Agent is the default, unlocked eval type', 'name the eval', 'switch run mode to Quick',
            'pick a mock-routed model', 'write instructions with a template variable',
            'attach the knowledge base from the model bar\'s Knowledge Base picker',
            'test the draft against custom input and read the Pass verdict', 'save/publish the eval'],
    backendChecks: ['the published eval is stored with eval_type "agent"',
                    'the published eval\'s config.knowledge_bases carries the created KB id',
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
  const evalName = `e2e-agent-kb-${suffix}`;
  const kbName = `e2e-kb-${suffix}`;
  const verdict = `e2e-verdict-${suffix}`;

  await ensureJudgeModel(actor);

  // CreateKnowledgeBaseView (model_hub/views/develop_dataset.py) reads
  // uploaded files from multipart `request.FILES`, entirely separately from
  // its (JSON-compatible) `name` field — posting no files at all still
  // creates a valid, empty, zero-size KB row, which is all this flow needs
  // to exist and be selectable in the picker.
  const kb = await actor.api.post<CreatedKB>('/model-hub/knowledge-base/', { name: kbName });
  const kbId = kb.result.kb_id;

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

  await test.step('UI: attach the knowledge base from the model bar\'s "+" menu', async () => {
    await page.locator(`button:right-of(:text("${JUDGE_MODEL}"))`).first().click();
    await page.getByRole('menuitem').filter({ hasText: 'Knowledge Base' }).click();
    // Search narrows the list in case other KBs exist in this fresh
    // worker-scoped org/workspace — belt and suspenders, not strictly
    // needed since provisioning gives each worker its own org.
    await page.getByPlaceholder('Search knowledge bases...').fill(kbName);
    await page.getByText(kbName, { exact: true }).click();
    await page.keyboard.press('Escape');
    // The "+" menu collapses the selection into a "N KB" chip rather than
    // the KB's own name.
    await expect(page.getByText('1 KB', { exact: true })).toBeVisible();
  });

  await test.step('UI: test against custom input — Pass', async () => {
    await fillTestData(page, '{"output": "world"}');
    await page.getByRole('button', { name: 'Test Evaluation' }).click();
    await expect(page.getByRole('button', { name: 'Test Evaluation' })).toBeVisible({ timeout: EVAL_RUN });
    await expect(page.getByText('Pass', { exact: true })).toBeVisible();
    await expect(page.getByText(`${verdict} saw world`)).toBeVisible();
  });

  await test.step('UI: publish, and API lane confirms the knowledge base + mode persisted', async () => {
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
    expect(detail.result.config.knowledge_bases).toContain(kbId);
    // The wire field is `mode` (EvalCreatePage's update payload), but
    // separate_evals.py persists it under a different key:
    // `template.config["agent_mode"] = req.mode` (:2711, and :2193 on
    // first create). Assert the stored key, which is also the one both
    // EvalCreatePage (:305) and EvalDetailPage (:380) read back.
    expect(detail.result.config.agent_mode).toBe('quick');
  });
});
