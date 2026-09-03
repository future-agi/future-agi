import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { JUDGE_MODEL, ensureJudgeModel, fillTestData, selectJudgeModel } from '../../lib/eval-model';
import { ApiError } from '../../lib/api-client';
import { allowed } from '../../lib/capabilities';
import type { TestActor } from '../../lib/provisioning';

// Browser-side waits. The stack slows several-fold when specs run in parallel
// (CI runs two workers), so these are sized off that rather than the 10s
// expect default.
const UI_READY = 60_000;
// One synchronous playground run: prompt -> gateway -> mock LLM -> parse ->
// render, sized for the slowest case (a composite fanning out to children).
const EVAL_RUN = 90_000;

// The unentitled half of EVAL-E2E-024. Kept beside the flow rather than in
// lib/ so both halves of the same product boundary read together.
//
// Gated on the `falcon_ai` capability, NOT on deployment mode: mode reports
// only that EE_LICENSE_KEY is set. This repo's dev key grants falcon_ai and
// CI's does not, so a mode-gated version passed locally and failed CI on a 402
// that was correct behaviour.
async function assertConnectorsDenied(actor: TestActor): Promise<void> {
  const denied = await actor.api
    .post('/falcon-ai/mcp-connectors/', {
      name: 'e2e-oss-denied', server_url: 'https://example-mcp.e2e.invalid/sse',
      transport: 'sse', auth_type: 'none',
    })
    .then(() => null)
    .catch((err: unknown) => err);

  expect(denied, 'creating an MCP connector must be refused without falcon_ai').toBeInstanceOf(ApiError);
  const err = denied as ApiError;
  // 402 (not 403): the product treats this as "upgrade required", and the
  // frontend keys its upsell off that status.
  expect(err.status).toBe(402);
  const body = err.body as { code?: string; details?: { feature?: string[] }; upgrade_required?: boolean };
  expect(body.code).toBe('ENTITLEMENT_DENIED');
  // Names the feature, so a future gate that denies for an unrelated reason
  // cannot satisfy this assertion by accident.
  expect(body.details?.feature).toContain('falcon_ai');
  expect(body.upgrade_required).toBe(true);
}

// Why `agent`-type is safe to drive here, unlike eval-code.spec.ts /
// eval-drawer-create.spec.ts (which both deliberately picked Code instead):
//
// Those specs' own comments call agent-type "non-deterministic ... whether a
// freshly-provisioned e2e org has AGENTIC_EVAL entitled is not something
// this suite controls" (frontend/src/sections/evals/components/
// EvalCreatePage.jsx gates the "Agents" tab behind
// `useFeatureLocked(CAPABILITY.AGENTIC_EVAL)`). That's true in general, but
// `futureagi/tfc/capabilities/registry.py` FEATURE_AGENTIC_EVAL leaves
// `oss_locked` at its default `False` — step "3.5" of
// `tfc/capabilities/service.py` `check()` grants any `requires_license=True`
// feature for free on *any* self-hosted deployment (OSS or EE, licensed or
// not) unless it's `oss_locked`. So AGENTIC_EVAL is always unlocked off
// cloud, and this spec can rely on the Agent tab being enabled unconditionally.
//
// What this scenario adds on top of AGENTIC_EVAL — selecting an *external
// connector* — needs the `falcon_ai` capability, which IS `oss_locked=True`:
// it requires a real EE license even off-cloud, because `ee.falcon_ai` is
// only registered as a Django app when `ee_feature_enabled("ee.falcon_ai")`
// (tfc/settings/settings.py:155), which needs `EE_LICENSE_KEY` set. Unlike
// AGENTIC_EVAL this is genuinely environment-dependent, so the test probes it
// at runtime via the public `GET /api/deployment-info/` (the `deploymentMode`
// fixture, `tfc/views/deployment.py`).
//
// It does NOT skip on `"oss"`. Both outcomes are product behaviour worth
// asserting, so the mode selects WHICH half runs: off-cloud the flow asserts
// that `POST /falcon-ai/mcp-connectors/` is refused 402 ENTITLEMENT_DENIED
// naming `falcon_ai`; entitled, it drives the full attach-and-publish flow
// below. Both lanes are green, neither is a permanent skip, and the
// entitlement boundary is covered from both sides.
//
// Test-run determinism: the eval's "mode" (Auto/Agent/Quick — a runtime
// dial for AgentEvaluator, unrelated to the eval *type*) is set to "Quick"
// below. Traced through `ee/evals/llm/agent_evaluator/evaluator.py`, quick
// mode sets `AgentLoop.MAX_ITERATIONS = 1` and `tools_allowed = False`,
// so the configured connector never actually gets invoked during a test
// run — the same literal-JSON-in-the-prompt trick eval-create.spec.ts (now
// eval-playground-create.spec.ts) uses for LLM-as-judge evals still yields
// exactly one deterministic mock-LLM call here. What this flow proves is
// the *configuration* surface (attaching/persisting a connector on an
// agent eval), not a live MCP tool invocation — there is no local MCP
// server in the e2e stack to invoke anyway.

interface CreatedConnector { result: { id: string; name: string } }
interface EvalDetailResponse {
  result: { id: string; eval_type: string; config: { tools?: Record<string, boolean>; agent_mode?: string } };
}

test('EVAL-E2E-024: author a new Agent eval, attach an external connector, test it and publish', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-024', area: 'evals',
    userGoal: 'A developer creates an Agent-type eval, attaches an external MCP connector as a '
      + 'tool the evaluator can use, tests it and publishes',
    steps: ['create an MCP connector via the Falcon AI connectors API', 'open the create-eval page',
            'confirm Agent is the default, unlocked eval type', 'name the eval', 'switch run mode to Quick',
            'pick a mock-routed model', 'write instructions with a template variable',
            'attach the connector from the model bar\'s Connectors picker',
            'test the draft against custom input and read the Pass verdict', 'save/publish the eval'],
    backendChecks: ['the published eval is stored with eval_type "agent"',
                    'the published eval\'s config.tools carries the connector id as a truthy key',
                    'the published eval\'s config.agent_mode is "quick"'],
  }),
}, async ({ page, actor, capabilities }, testInfo) => {
  // Every bounded wait in this spec, chained: past the config's 120s
  // default, so a slow run ends on the assertion that ran out rather
  // than a bare test timeout.
  test.setTimeout(300_000);
  // OSS does not skip this flow — it asserts the other half of it. MCP
  // connectors sit behind `falcon_ai`, one of the four `oss_locked` features
  // (futureagi/tfc/capabilities/registry.py:81-83), so refusing to create one
  // off-cloud IS the product behaviour, not an absence of it. Asserting the
  // refusal here keeps the entitlement boundary itself covered — a regression
  // that silently handed OSS a working connector would otherwise pass
  // unnoticed — and keeps both lanes green without a permanent skip.
  if (!allowed(capabilities, 'falcon_ai')) {
    await assertConnectorsDenied(actor);
    return;
  }

  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const evalName = `e2e-agent-connector-${suffix}`;
  const connectorName = `e2e-connector-${suffix}`;
  const verdict = `e2e-verdict-${suffix}`;

  await ensureJudgeModel(actor);

  // Pure DB-backed create — no live MCP server needed, no synchronous
  // discovery/verification call on creation (views_connectors.py `post`).
  const connector = await actor.api.post<CreatedConnector>('/falcon-ai/mcp-connectors/', {
    name: connectorName,
    server_url: 'https://example-mcp.e2e.invalid/sse',
    transport: 'sse',
    auth_type: 'none',
  });
  const connectorId = connector.result.id;

  let draftId = '';

  await test.step('UI: a draft is auto-created, defaulting to Agent type (unlocked)', async () => {
    await page.goto('/dashboard/evaluations/create');
    await page.waitForURL(/\/dashboard\/evaluations\/create\/.+/, { timeout: UI_READY });
    draftId = page.url().split('/dashboard/evaluations/create/')[1];
    expect(draftId).toMatch(/.+/);
    await testInfo.attach('draft-id', { body: draftId, contentType: 'text/plain' });
    // Agent-type renders InstructionEditor (Quill) with this exact
    // placeholder — a completely different component tree from the
    // LLM-as-judge default, so this is direct proof AGENTIC_EVAL resolved
    // unlocked and "agent" (not "llm") is the live default eval type.
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

  await test.step('UI: attach the connector from the model bar\'s "+" menu', async () => {
    // The "+" button (Connectors / Knowledge Base / Data Injection / Summary)
    // is an icon-only IconButton with no accessible name — it sits
    // immediately to the right of the model-name pill in document order, so
    // Playwright's `:right-of()` layout selector finds it reliably without
    // depending on Iconify's internal DOM output.
    await page.locator(`button:right-of(:text("${JUDGE_MODEL}"))`).first().click();
    await page.getByRole('menuitem').filter({ hasText: 'Connectors' }).click();
    await page.getByRole('menuitem', { name: connectorName }).click();
    await page.keyboard.press('Escape');
    // The chip rendered for an active connector shows its name.
    await expect(page.getByText(connectorName, { exact: true })).toBeVisible();
  });

  await test.step('UI: test against custom input — Pass', async () => {
    await fillTestData(page, '{"output": "world"}');
    await page.getByRole('button', { name: 'Test Evaluation' }).click();
    await expect(page.getByRole('button', { name: 'Test Evaluation' })).toBeVisible({ timeout: EVAL_RUN });
    await expect(page.getByText('Pass', { exact: true })).toBeVisible();
    await expect(page.getByText(`${verdict} saw world`)).toBeVisible();
  });

  await test.step('UI: publish, and API lane confirms the connector + mode persisted', async () => {
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
    expect(detail.result.config.tools?.[connectorId]).toBe(true);
    // The wire field is `mode` (EvalCreatePage's update payload), but
    // separate_evals.py persists it under a different key:
    // `template.config["agent_mode"] = req.mode` (:2711, and :2193 on
    // first create). Assert the stored key, which is also the one both
    // EvalCreatePage (:305) and EvalDetailPage (:380) read back.
    expect(detail.result.config.agent_mode).toBe('quick');
  });
});
