import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { JUDGE_MODEL, ensureJudgeModel, selectJudgeModel } from '../../lib/eval-model';

// Browser-side waits. The stack slows several-fold when specs run in parallel
// (CI runs two workers), so these are sized off that rather than the 10s
// expect default.
const UI_READY = 60_000;
// One synchronous playground run: prompt -> gateway -> mock LLM -> parse ->
// render, sized for the slowest case (a composite fanning out to children).
const EVAL_RUN = 90_000;

// Why `code`-type, not `agent`-type, for this flow:
//
// frontend/src/sections/evals/components/EvalCreatePage.jsx gates the
// "Agents" eval-type tab behind `agentEvalLocked` (from
// `useFeatureLocked(CAPABILITY.AGENTIC_EVAL)`, lines ~154-160). When locked,
// the tab renders disabled with a lock icon and a tooltip (lines 892-899,
// 927-984), the page's own default-type effect falls back to "llm" instead
// of "agent" (lines 264-269), and `handleSaveSingle` outright refuses to
// save an agent-type eval with a snackbar error (lines 454-460). In practice
// this gate never actually fires off-cloud: `agentic_eval` is left
// `oss_locked=False` in `futureagi/tfc/capabilities/registry.py`, and
// `tfc/capabilities/service.py` `check()` grants any `requires_license=True`
// feature for free on any self-hosted deployment (OSS or EE) unless it's
// `oss_locked` — see eval-playground-agent-connectors.spec.ts, which relies
// on exactly this to always show the Agent tab. So `agent`-type would be a
// deterministic choice here too (this file predates that finding, hence the
// Code pick below being framed as dodging non-determinism that doesn't
// actually exist) — but Code stays the better fit regardless, since it's
// also strictly simpler to run than `llm`-type: a backend trace (see PR
// description / investigation) confirms `EvalPlayGroundAPIView` dispatches
// code evals to `CustomCodeEval`, whose `_model` property is hardcoded to
// `None` — no LLM call, no gateway, no mock-model plumbing needed at all.
// `model` is inert at RUN time for code evals (`EvalCreatePage.jsx`'s own
// `handleTestEvaluation`/`handleSaveSingle` skip every fagi/model-lock check
// when `evalType === "code"`) — but NOT optional at SAVE time: the shared
// template serializer still rejects a blank `model` with 400 "model: This
// field may not be blank.", and the Code tab renders no model picker of its
// own. See the model pick in "name it, pick a model on the Agent tab" below.
// The function
// runs in a sandboxed subprocess (RestrictedPython) and its return value is
// deterministic given deterministic input — no mock server, no retries, no
// flakiness from a model in the loop.

interface EvalListResponse { result: { items: { id: string; name: string }[]; total: number } }
interface EvalDetailResponse { result: { id: string; eval_type: string; code: string | null } }

test('EVAL-E2E-005: author a new Code eval from scratch, test it and publish', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-005', area: 'evals',
    userGoal: 'A developer creates a new Code-type eval, writes a Python evaluate() function, '
      + 'tests it against inputs that should Pass and Fail, and publishes it',
    steps: ['open the create-eval page', 'name the eval',
            'pick a model (the shared draft serializer rejects a blank one, even for code)',
            'switch to the Code eval type',
            'write a Python evaluate() function that checks for a unique marker in the output',
            'test the draft against input containing the marker and read the Pass verdict',
            'edit the test input to omit the marker and read the Fail verdict',
            'save/publish the eval'],
    backendChecks: ['a template row is auto-created on page load and is fetchable at eval-templates/<id>/detail/',
                    'the published template is stored as eval_type "code" carrying the evaluate() source '
                      + 'whose marker came back in both verdicts',
                    'the same source returns Pass and Fail on two different inputs, so the sandbox ran it '
                      + 'rather than replaying a cached result',
                    'publishing sets it visible and searchable in the main eval list'],
  }),
}, async ({ page, actor }, testInfo) => {
  // Every bounded wait in this spec, chained: past the config's 120s
  // default, so a slow run ends on the assertion that ran out rather
  // than a bare test timeout.
  test.setTimeout(300_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  // Sanitized to [a-z0-9_-] on input by the Eval Name field itself — no spaces/case.
  const evalName = `e2e-create-code-${suffix}`;
  // Exists nowhere else, so finding it in the returned `reason` proves the
  // sandboxed function actually ran against the JSON we typed, rather than
  // some cached/default result.
  const marker = `e2e-marker-${suffix}`;

  // Single-line function body (no `\n` inside the signature or body) is
  // deliberate: Monaco's auto-indent-on-Enter (after a `:`-terminated line)
  // would otherwise stack on top of whatever leading whitespace we type
  // ourselves, corrupting indentation in ways this test can't detect before
  // the sandboxed run rejects it with a SyntaxError. Python allows a simple
  // `return` statement as the body on the same line as `def ...():`, so this
  // sidesteps the issue entirely rather than trying to get multi-line typing
  // exactly right blind.
  const evaluateCode = `def evaluate(input, output, expected, context, **kwargs): `
    + `return {"score": 1.0, "reason": "${marker} found in output"} if "${marker}" in str(output) `
    + `else {"score": 0.0, "reason": "${marker} missing from output"}`;

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

  await test.step('UI: name it, pick a model on the Agent tab, then switch to Code', async () => {
    await page.getByPlaceholder('Eg: Hallucination detector').fill(evalName);
    // The model pick has to happen HERE, before the Code tab is selected.
    //
    // A code eval never calls a model — but every draft save still goes
    // through the same template serializer, which rejects a blank `model`
    // with 400 "model: This field may not be blank." EvalCreatePage seeds
    // `model` with "turing_large" (:175) and then clears it to "" (:274)
    // once `turing_models` comes back denied, which it always does on this
    // OSS stack. So the draft starts un-saveable.
    //
    // The Code tab renders only CodeEvalEditor (:1050) — no ModelSelector at
    // all — so there is no way to fix that from inside the tab. `model` is
    // one useState shared across all three tabs and `setEvalType` (:899)
    // does not reset it, so selecting on the default Agent tab first is what
    // makes the code draft saveable. That is also the only path a real OSS
    // user has, which is worth flagging as a product bug in its own right.
    await selectJudgeModel(page, JUDGE_MODEL);
    await page.getByRole('tab', { name: 'Code', exact: true }).click();
  });

  await test.step('UI: replace the default template with our evaluate() function', async () => {
    // Two Monaco instances are on the page at once once the Code tab is
    // active: CodeEvalEditor (this eval's `code`, in the left "Eval details"
    // panel) and CustomJsonInput's test-data editor (in the right
    // TestPlayground panel, always mounted since "Custom" is the default
    // source tab). ResizablePanels.jsx renders `leftPanel` into a
    // "first-panel" div and `rightPanel` into a "second-panel" div, both
    // direct siblings in one flex row — so document order is guaranteed and
    // `.first()`/`.last()` reliably picks the code editor vs. the JSON editor.
    // `.view-lines`, not the `textarea.inputarea` these specs used to click:
    // Monaco's input textarea is a 1px element parked under the caret, so the
    // rendered `.view-line` sits on top of it and intercepts the click. That
    // made every click here retry until the test timed out. `.view-lines` is
    // the visible text surface, clicking it focuses the same textarea, and it
    // has one node per editor in the same document order — so `.first()` /
    // `.last()` keep picking the code vs. the JSON editor exactly as before.
    const codeEditor = page.locator('.monaco-editor .view-lines').first();
    await codeEditor.click();
    // Unlike the LLM/JSON editors in eval-create.spec.ts (which start empty
    // or hold a one-line scaffold), CodeEvalEditor mounts pre-filled with
    // PYTHON_CODE_TEMPLATE — a multi-line default. Select-all + delete
    // before typing so our function replaces it instead of interleaving
    // with it.
    await page.keyboard.press('ControlOrMeta+A');
    await page.keyboard.press('Backspace');
    await page.keyboard.type(evaluateCode, { delay: 5 });
  });

  await test.step('UI: test against input containing the marker — Pass', async () => {
    const jsonEditor = page.locator('.monaco-editor .view-lines').last();
    await jsonEditor.click();
    await page.keyboard.press('ControlOrMeta+A');
    await page.keyboard.press('Backspace');
    // Variables are live-parsed from the evaluate() signature above
    // (extractCodeEvaluateParams / TestPlayground.jsx), giving input/output/
    // expected — only `output` is read by our function, the others are
    // along for the ride.
    await page.keyboard.type(
      `{"input": "n/a", "output": "response text containing ${marker} inline", "expected": ""}`,
      { delay: 10 },
    );
    await page.getByRole('button', { name: 'Test Evaluation' }).click();
    await expect(page.getByRole('button', { name: 'Test Evaluation' })).toBeVisible({ timeout: EVAL_RUN });
    await expect(page.getByText('Pass', { exact: true })).toBeVisible();
    // `exact` is load-bearing: the reason string is also a literal inside the
    // evaluate() source sitting in the left Monaco editor, so a substring
    // match resolves to both that editor's `.view-lines` and the result
    // panel's <pre> and trips strict mode. Only the <pre> equals it exactly.
    await expect(page.getByText(`${marker} found in output`, { exact: true })).toBeVisible();
  });

  await test.step('UI: edit the test input to omit the marker — Fail', async () => {
    const jsonEditor = page.locator('.monaco-editor .view-lines').last();
    await jsonEditor.click();
    await page.keyboard.press('ControlOrMeta+A');
    await page.keyboard.press('Backspace');
    await page.keyboard.type(
      '{"input": "n/a", "output": "response text with no matching token", "expected": ""}',
      { delay: 10 },
    );
    await page.getByRole('button', { name: 'Test Evaluation' }).click();
    await expect(page.getByRole('button', { name: 'Test Evaluation' })).toBeVisible({ timeout: EVAL_RUN });
    await expect(page.getByText('Fail', { exact: true })).toBeVisible();
    await expect(page.getByText(`${marker} missing from output`, { exact: true })).toBeVisible();
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

    // The stored source is the same function whose marker came back in both
    // verdicts above, which is what ties the rendered result to the code the
    // sandbox actually ran.
    const detail = await actor.api.get<EvalDetailResponse>(`/model-hub/eval-templates/${draftId}/detail/`);
    expect(detail.result.eval_type).toBe('code');
    expect(detail.result.code).toContain(marker);
  });
});
