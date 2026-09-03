import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { JUDGE_MODEL, ensureJudgeModel, fillTestData } from '../../lib/eval-model';

// Browser-side waits. The stack slows several-fold when specs run in parallel
// (CI runs two workers), so these are sized off that rather than the 10s
// expect default.
const UI_READY = 60_000;
// One synchronous playground run: prompt -> gateway -> mock LLM -> parse ->
// render, sized for the slowest case (a composite fanning out to children).
const EVAL_RUN = 90_000;

// Routes the judge model through the mock LLM behind the real gateway — same
// setup as eval-task.spec.ts / eval-playground.spec.ts.

interface CreatedId { result: { id: string } }
interface FeedbackListResponse { result: { items: { explanation: string }[]; total: number } }

// Scoping note (see PR/report): the product scenario list asks for feedback
// "via a dataset context and via an Observe context". Reading
// AddEvalsFeedbackDrawer.jsx shows the *first-time* Add Feedback submission
// (source = existingFeedback?.source || "eval_playground") always posts to
// the same `develop.eval.addEvalsFeedback` endpoint with the same drawer/
// form, regardless of whether the log being annotated came from the
// playground, a dataset evaluation or an Observe-driven eval run — there is
// exactly one add-feedback mechanism reachable from this page. The
// dataset/observe branches inside that same file only activate when
// *editing* a pre-existing feedback record that already carries that source
// tag (posting instead to `develop.eval.updateFeedback` or
// `project.submitObservationSpanFeedbackActionType`) — and creating such a
// pre-existing record requires a different surface entirely (the trace
// detail drawer's own add-feedback-form.jsx, which posts to
// `project.submitFeedback`, itself a different page from the eval detail
// page this task is scoped to). So this single flow — adding feedback to a
// usage log from the eval detail page — is the whole of the addressable
// scenario; the edit-time branching for pre-existing dataset/observe
// feedback is called out as an explicit gap in the report rather than
// guessed at here.
test('EVAL-E2E-012: add feedback to a logged eval result from the Usage tab', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-012', area: 'evals',
    userGoal: 'A developer reviews a usage log for a custom eval and submits human feedback '
      + '("Auto Learning") on the result',
    steps: ['author a custom LLM eval and test it once to produce a usage log',
            'open the Usage tab and open the log row',
            'click Add Feedback, choose the correct value, an explanation and a retune action',
            'submit the feedback and see the panel switch to Edit Feedback',
            'open the Feedback tab and confirm the submitted feedback is listed there'],
    backendChecks: ['a first-time Add Feedback submission on an eval-playground log posts log_id + value '
                    + '+ explanation + action_type to the eval-playground feedback endpoint',
                    'the new feedback is retrievable from the feedback-list endpoint keyed by the eval template'],
  }),
}, async ({ page, actor }, testInfo) => {
  // Every bounded wait in this spec, chained: past the config's 120s
  // default, so a slow run ends on the assertion that ran out rather
  // than a bare test timeout.
  test.setTimeout(420_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  // Hyphens, not spaces: create-v2 rejects anything outside [a-z0-9_-]
  // with 400 "Name can only contain lowercase letters, numbers, hyphens
  // (-), or underscores (_)."
  const evalName = `e2e-feedback-judge-${suffix}`;
  const verdict = `e2e-verdict-${suffix}`;
  // Exists nowhere else, so finding it back in the Feedback tab / API list
  // proves the submission round-tripped rather than showing stale UI state.
  const improvementNote = `e2e-feedback-note-${suffix}`;

  await ensureJudgeModel(actor);

  const template = await actor.api.post<CreatedId>('/model-hub/eval-templates/create-v2/', {
    name: evalName, eval_type: 'llm',
    instructions: `Reply with exactly this JSON: {"result": "Pass", "explanation": "${verdict} saw {{output}}"}`,
    model: JUDGE_MODEL, output_type: 'pass_fail', pass_threshold: 0.5,
  });
  const templateId = template.result.id;
  await testInfo.attach('template-id', { body: templateId, contentType: 'text/plain' });

  await test.step('UI: produce one usage log by testing the eval', async () => {
    await page.goto(`/dashboard/evaluations/${templateId}`);
    await fillTestData(page, '{"output": "world"}');
    await page.getByRole('button', { name: 'Test Evaluation' }).click();
    await expect(page.getByRole('button', { name: 'Test Evaluation' })).toBeVisible({ timeout: EVAL_RUN });
    await expect(page.getByText(`${verdict} saw world`)).toBeVisible();
  });

  await test.step('UI: open the log on the Usage tab', async () => {
    await page.getByRole('tab', { name: 'Usage' }).click();
    await page.getByText(`${verdict} saw world`).first().click();
    // By ROLE, not text: EvalUsageTab renders one button whose label flips
    // between "Add Feedback" and "Edit Feedback", and the drawer title uses
    // the same string — so bare text matches a <button> and a <p>. The button
    // is what this flow means in both places.
    await expect(page.getByRole('button', { name: 'Add Feedback' })).toBeVisible({ timeout: UI_READY });
  });

  await test.step('UI: submit feedback for this result', async () => {
    await page.getByRole('button', { name: 'Add Feedback' }).click();
    // Confirms the drawer opened in "add" (not "edit") mode.
    await expect(page.getByText('Feedbacks for Auto Learning')).toBeVisible({ timeout: UI_READY });
    await page.getByRole('radio', { name: 'Passed' }).check();
    await page.getByPlaceholder(
      "Write the explanation the eval should have given for this result, and why it's correct",
    ).fill(improvementNote);
    // FormControlLabel wraps the whole option (radio + title + description)
    // in a real <label>, so clicking the title text toggles the radio.
    await page.getByText('Re-tune', { exact: true }).click();
    // The wire body, not just the outcome: AddEvalsFeedbackDrawer.jsx:223-233
    // spreads the form fields and adds action_type + log_id for an
    // eval-playground log, and this is the only lane that can see it.
    const feedbackPost = page.waitForRequest((r) =>
      r.url().includes('/model-hub/eval-playground/feedback/') && r.method() === 'POST',
      { timeout: UI_READY });
    await page.getByRole('button', { name: 'Submit feedback' }).click();
    const posted = (await feedbackPost).postDataJSON();
    expect(Object.keys(posted)).toEqual(
      expect.arrayContaining(['log_id', 'value', 'explanation', 'action_type']));
    expect(posted.explanation).toBe(improvementNote);
    expect(posted.action_type).toBe('retune');

    // The drawer closes and the same panel now offers to edit what was just
    // submitted — the clearest signal the feedback round-tripped through a
    // refetch of this same log.
    await expect(page.getByRole('button', { name: 'Edit Feedback' })).toBeVisible({ timeout: UI_READY });
    // Scoped to <p>: getByText matches form controls by their VALUE too, and
    // the note is still in the drawer's textarea. The panel copy is what
    // proves the feedback round-tripped.
    await expect(page.locator('p').filter({ hasText: improvementNote })).toBeVisible();
  });

  await test.step('UI: the feedback is listed on the Feedback tab', async () => {
    await page.getByRole('tab', { name: 'Feedback' }).click();
    await expect(page.getByText(improvementNote)).toBeVisible({ timeout: UI_READY });
    await page.getByText(improvementNote).click();
    // "Log ID" is a detail-panel-only field on this tab, confirming the
    // panel opened with this specific feedback row.
    await expect(page.getByText('Log ID')).toBeVisible({ timeout: UI_READY });
  });

  await test.step('API lane: the feedback is retrievable from the feedback-list endpoint', async () => {
    const list = await actor.api.get<FeedbackListResponse>(
      `/model-hub/eval-templates/${templateId}/feedback-list/`, { page: 0, page_size: 25 },
    );
    expect(list.result.items.map((i) => i.explanation)).toContain(improvementNote);
  });
});
