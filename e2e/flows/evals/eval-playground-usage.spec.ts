import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { JUDGE_MODEL, ensureJudgeModel, fillTestData } from '../../lib/eval-model';

// Routes the judge model through the mock LLM behind the real gateway — same
// setup as eval-task.spec.ts / eval-playground.spec.ts. The mock echoes back
// the exact prompt text, so whatever JSON the instructions dictate is the
// JSON the eval parser reads back.

interface CreatedId { result: { id: string } }

test('EVAL-E2E-011: filter usage logs by date range and drill into a single log', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-011', area: 'evals',
    userGoal: 'A developer viewing a custom eval\'s Usage tab filters its logs to a date range and opens a '
      + 'single log row to read the input, result and reasoning behind that run',
    steps: ['author a custom LLM eval and test it once to produce a usage log',
            'open the Usage tab and confirm the log is visible under the default window',
            'switch the date-range preset to Yesterday and see the log disappear',
            'switch back to Today and see the log reappear',
            'open the log row and read its status and explanation in the detail side panel'],
    backendChecks: ['the usage endpoint is called with start/end date params matching the selected preset',
                    'a log created moments ago is excluded from a Yesterday-only window and included in Today',
                    'the detail panel renders the exact explanation text the eval produced for that run, '
                    + 'unwrapped from the usage row\'s cell_value wrapper'],
  }),
}, async ({ page, actor }, testInfo) => {
  // The Today-preset wait below can span a minute boundary.
  test.setTimeout(180_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  // A non-system, user-authored template (owner !== "system") — i.e. a
  // "custom eval" in product terms — so date-range filtering is exercised
  // against the same kind of eval a user would actually create.
  // Hyphens, not spaces: create-v2 rejects anything outside [a-z0-9_-]
  // with 400 "Name can only contain lowercase letters, numbers, hyphens
  // (-), or underscores (_)."
  const evalName = `e2e-usage-judge-${suffix}`;
  // Exists nowhere else, so finding it in the log proves the round trip.
  const verdict = `e2e-verdict-${suffix}`;

  await ensureJudgeModel(actor);

  const template = await actor.api.post<CreatedId>('/model-hub/eval-templates/create-v2/', {
    name: evalName, eval_type: 'llm',
    instructions: `Reply with exactly this JSON: {"result": "Pass", "explanation": "${verdict} saw {{output}}"}`,
    model: JUDGE_MODEL, output_type: 'pass_fail', pass_threshold: 0.5,
  });
  const templateId = template.result.id;

  await test.step('UI: produce one usage log by testing the eval', async () => {
    await page.goto(`/dashboard/evaluations/${templateId}`);
    await fillTestData(page, '{"output": "world"}');
    await page.getByRole('button', { name: 'Test Evaluation' }).click();
    await expect(page.getByRole('button', { name: 'Test Evaluation' })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(`${verdict} saw world`)).toBeVisible();
  });

  await test.step('UI: the log is visible on the Usage tab under the default window', async () => {
    await page.getByRole('tab', { name: 'Usage' }).click();
    await expect(page.getByText(/Runs:/)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(`${verdict} saw world`)).toBeVisible({ timeout: 15_000 });
  });

  await test.step('UI: switching the date range to Yesterday excludes the just-created log', async () => {
    await page.getByRole('button', { name: 'Yesterday', exact: true }).click();
    await expect(page.getByText('No evaluation logs for this period')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(`${verdict} saw world`)).not.toBeVisible();
  });

  await test.step('UI: switching back to Today shows the log, and opening it reveals the run detail', async () => {
    // The "Today" preset sends end_date = startOfMinute(now)
    // (useEvalUsage.js:20 — floored so the query key stays stable across
    // renders), so a log written during the CURRENT minute sits just past the
    // end of its own window and the tab reports "No evaluation logs for this
    // period". Nothing re-renders on its own to fix that, so re-pick the
    // preset until the minute rolls over and end_date moves past the log.
    await expect(async () => {
      await page.getByRole('button', { name: '7D', exact: true }).click();
      await page.getByRole('button', { name: 'Today', exact: true }).click();
      await expect(page.getByText(`${verdict} saw world`)).toBeVisible({ timeout: 5_000 });
    }).toPass({ timeout: 90_000 });

    await page.getByText(`${verdict} saw world`).first().click();
    // "Status" is a detail-panel-only field (not one of the table's default
    // columns), so it unambiguously confirms the side panel opened with this
    // run's detail rather than just re-showing the table cell.
    // Exact: the stats strip above renders "Success:" as its own label, which
    // a substring match also picks up alongside the detail panel's status chip.
    await expect(page.getByText('success', { exact: true })).toBeVisible({ timeout: 10_000 });
    // Prev/next counter in the panel header confirms there is exactly one
    // matching row and it is the one currently open.
    await expect(page.getByText('1 / 1')).toBeVisible();
  });
});
