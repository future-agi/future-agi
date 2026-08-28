import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { JUDGE_MODEL, ensureJudgeModel, fillTestData, selectJudgeModel } from '../../lib/eval-model';

// Routes both child judges through the mock LLM behind the real gateway —
// same setup as eval-create.spec.ts / eval-playground.spec.ts / eval-task.spec.ts.
// The mock echoes back the exact (already-templated) prompt text, so whatever
// JSON each child's instructions dictate is the JSON each child's own parser
// reads back — deterministic per child, and therefore deterministic once
// aggregated.

interface CreatedId { result: { id: string } }
interface CompositeChildResult {
  child_id: string;
  child_name: string;
  score: number | null;
  reason: string | null;
}
interface CompositeExecuteResult {
  result: {
    aggregate_score: number | null;
    aggregate_pass: boolean | null;
    children: CompositeChildResult[];
  };
}
interface CompositeDetail {
  result: {
    id: string;
    template_type: string;
    aggregation_enabled: boolean;
    aggregation_function: string;
    children: { child_id: string }[];
  };
}

test('EVAL-E2E-004: combine two evals into a composite, test the aggregate and publish', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-004', area: 'evals',
    userGoal: 'A developer combines two existing evals into a composite under an aggregation '
      + 'method, tests the aggregate against custom input, reads the per-child and aggregate '
      + 'results, and publishes it',
    steps: ['author two deterministic pass/fail child evals via the API',
            'open the create-eval page and switch to Composite mode',
            'name the composite', 'pick both children through the eval picker drawer',
            'switch the aggregation function to Average',
            'test the composite against custom input',
            'read the per-child scores, the aggregate score and the PASS verdict',
            'publish the composite',
            'confirm the saved composite is independently executable via its own endpoint'],
    backendChecks: ['the composite is created via POST /model-hub/eval-templates/create-composite/ '
                      + 'with both child template ids and aggregation_function="avg"',
                    'adhoc execution (execute-adhoc) runs both children synchronously in the same '
                      + 'request/response cycle and returns aggregate_score = avg(1.0, 0.0) = 0.5',
                    'aggregate_pass is true because 0.5 meets the default 0.5 pass_threshold '
                      + '(determine_pass_fail uses >=)',
                    'the saved composite re-runs the identical aggregation via its own '
                      + '/composite/execute/ endpoint, independent of the create-page UI'],
  }),
}, async ({ page, actor }, testInfo) => {
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  // Hyphens, not spaces: create-v2 rejects anything outside [a-z0-9_-] with
  // "Name can only contain lowercase letters, numbers, hyphens (-), or
  // underscores (_)." (the single-eval Name field sanitizes typed input to
  // the same charset, so the API is the only place a space can slip in).
  const passChildName = `e2e-composite-pass-${suffix}`;
  const failChildName = `e2e-composite-fail-${suffix}`;
  const compositeName = `e2e-composite-${suffix}`;
  // Each exists nowhere else, so finding it in a child's `reason` proves that
  // child actually ran (rather than the composite silently skipping it).
  const passVerdict = `e2e-pass-verdict-${suffix}`;
  const failVerdict = `e2e-fail-verdict-${suffix}`;

  await ensureJudgeModel(actor);

  // Two children, same shape as eval-create.spec.ts's single eval, except one
  // is pinned to always report "Pass" and the other always "Fail". Per
  // model_hub/utils/scoring.py's normalize_score for output_type="pass_fail",
  // a "Pass" verdict scores 1.0 and any other string (including "Fail")
  // scores 0.0 — so composite aggregation over these two children is fully
  // predictable by hand: avg(1.0, 0.0) = 0.5, and 0.5 >= the default 0.5
  // pass_threshold, so aggregate_pass is true.
  const passChild = await actor.api.post<CreatedId>('/model-hub/eval-templates/create-v2/', {
    name: passChildName, eval_type: 'llm',
    instructions: `Reply with exactly this JSON: {"result": "Pass", "explanation": "${passVerdict} saw {{output}}"}`,
    model: JUDGE_MODEL, output_type: 'pass_fail', pass_threshold: 0.5,
  });
  const failChild = await actor.api.post<CreatedId>('/model-hub/eval-templates/create-v2/', {
    name: failChildName, eval_type: 'llm',
    instructions: `Reply with exactly this JSON: {"result": "Fail", "explanation": "${failVerdict} saw {{output}}"}`,
    model: JUDGE_MODEL, output_type: 'pass_fail', pass_threshold: 0.5,
  });

  await page.goto('/dashboard/evaluations/create');

  await test.step('UI: switch to Composite mode and name it', async () => {
    await page.getByRole('tab', { name: 'Composite' }).click();
    await expect(page.getByText('Composite Configuration')).toBeVisible();

    // RISK: unlike the single-eval Name field (which has a placeholder —
    // see eval-create.spec.ts), CompositeDetailPanel's Name TextField
    // (frontend/src/sections/evals/components/CompositeDetailPanel.jsx)
    // has no placeholder, label or aria-label — only a helperText below it.
    // It is the only plain <input type="text"> under the "Composite
    // Configuration" panel until children are added (Description is a
    // <textarea>; weight/param fields that appear later are type="number"),
    // so it's targeted structurally, scoped to the panel to avoid any
    // unrelated text input elsewhere on the page.
    const compositePanel = page.getByText('Composite Configuration').locator('xpath=../..');
    await compositePanel.locator('input[type="text"]').first().fill(compositeName);
  });

  const addChild = async (childName: string) => {
    await page.getByRole('button', { name: 'Add evaluation' }).click();
    const searchBox = page.getByPlaceholder('Search evaluations...');
    await searchBox.fill(childName);
    const row = page.locator('tr').filter({ hasText: childName });
    await expect(row).toBeVisible({ timeout: 15_000 });
    await row.getByRole('button', { name: 'Add', exact: true }).click();

    // Two possible outcomes, decided by EvalPickerDrawer.handleSelectEval
    // (frontend/src/sections/common/EvalPicker/EvalPickerDrawer.jsx:83-101):
    // it adds the child inline and closes the drawer only when
    // needsModelSelection() is false (:54-77 — false when the template
    // already resolves a judge `model`). These children are created via the
    // API with an explicit `model`, so inline is the expected path.
    // A child authored WITHOUT one — the norm on an OSS deployment, where
    // the seeded Turing default is stripped (see lib/eval-model.ts) — makes
    // `!detail?.model` true and the drawer advances to a "Configure
    // Evaluation" step that must pick a model and confirm with "Add to
    // Composite" (EvalPickerConfigFull.jsx:2276-2280) instead. Waiting on
    // the close first costs nothing on the inline path and keeps the flow
    // from hanging on a step it never expected.
    const addedInline = await searchBox
      .waitFor({ state: 'hidden', timeout: 10_000 })
      .then(() => true)
      .catch(() => false);
    if (!addedInline) {
      await selectJudgeModel(page, JUDGE_MODEL);
      await page.getByRole('button', { name: 'Add to Composite' }).click();
      await expect(searchBox).toBeHidden();
    }
  };

  await test.step('UI: pick both children through the eval picker drawer', async () => {
    // EvalPickerDrawer opens with skipConfig=true from CompositeDetailPanel
    // (frontend/src/sections/evals/components/CompositeDetailPanel.jsx),
    // and both children already carry a model — so EvalPickerDrawer's
    // needsModelSelection resolves false and each "Add" click adds the
    // child directly and closes the drawer, with no intermediate mapping
    // step (frontend/src/sections/common/EvalPicker/EvalPickerDrawer.jsx).
    await addChild(passChildName);
    await addChild(failChildName);
    await expect(page.getByText('Children (2)')).toBeVisible();
    await expect(page.getByText(passChildName)).toBeVisible();
    await expect(page.getByText(failChildName)).toBeVisible();
  });

  await test.step('UI: switch aggregation to Average', async () => {
    // MUI Select: click the trigger showing the current value's label, then
    // pick the option by its visible label (AGGREGATION_OPTIONS in
    // CompositeDetailPanel.jsx: weighted_avg="Weighted Average",
    // avg="Average", min="Minimum (safety gate)", max="Maximum",
    // pass_rate="Pass Rate"). "weighted_avg" is the default, so this is a
    // real mode switch, not a no-op — though with the default per-child
    // weight of 1.0 on both children, weighted_avg and avg compute the same
    // number here; avg is picked because its math needs no weight inputs.
    await page.getByText('Weighted Average', { exact: true }).click();
    await page.getByRole('option', { name: 'Average', exact: true }).click();
  });

  await test.step('UI: test the composite against custom input', async () => {
    // Composite mode's Custom tab (TestPlayground.jsx) scaffolds one JSON
    // field per union of the children's required_keys — both children
    // declare {{output}}, so the union is exactly ["output"].
    await fillTestData(page, '{"output": "world"}');
    await page.getByRole('button', { name: 'Test Evaluation' }).click();
    await expect(page.getByRole('button', { name: 'Test Evaluation' })).toBeVisible({ timeout: 45_000 });

    // Aggregate: CompositeResultView.jsx renders
    // "Aggregate Score (Average)" + aggregate_score.toFixed(3), a PASS/FAIL
    // chip from aggregate_pass, and a "<completed>/<total> completed" chip.
    await expect(page.getByText('Aggregate Score (Average)')).toBeVisible();
    await expect(page.getByText('0.500', { exact: true })).toBeVisible();
    await expect(page.getByText('PASS', { exact: true })).toBeVisible();
    await expect(page.getByText('2/2 completed')).toBeVisible();

    // Per child: score.toFixed(3) chip plus the judge's own explanation
    // text, rendered verbatim as markdown (child.reason) — proves each
    // child actually ran against the mock rather than one being skipped.
    await expect(page.getByText('1.000', { exact: true })).toBeVisible();
    await expect(page.getByText('0.000', { exact: true })).toBeVisible();
    await expect(page.getByText(`${passVerdict} saw world`)).toBeVisible();
    await expect(page.getByText(`${failVerdict} saw world`)).toBeVisible();
  });

  let compositeId = '';
  await test.step('UI: publish the composite', async () => {
    await page.getByRole('button', { name: 'Save Evaluation' }).click();
    await expect(page.getByText('Composite evaluation created successfully')).toBeVisible({ timeout: 15_000 });
    await page.waitForURL(/\/dashboard\/evaluations\/[^/]+$/, { timeout: 15_000 });
    // Read the id off the PATHNAME, not the raw URL. EvalDetailPage appends
    // `?v=<version_number>` once it loads the saved version (:417), and `?`
    // is not `/`, so the `[^/]+$` pattern above happily matches
    // "<id>?v=1" — which then went into the API path and 404'd.
    compositeId = new URL(page.url()).pathname.split('/dashboard/evaluations/')[1];
    expect(compositeId).toMatch(/.+/);
  });

  await test.step('API lane: the saved composite carries both children and re-runs the same aggregation', async () => {
    const detail = await actor.api.get<CompositeDetail>(`/model-hub/eval-templates/${compositeId}/composite/`);
    expect(detail.result.template_type).toBe('composite');
    expect(detail.result.aggregation_enabled).toBe(true);
    expect(detail.result.aggregation_function).toBe('avg');
    expect(detail.result.children.map((c) => c.child_id).sort()).toEqual(
      [passChild.result.id, failChild.result.id].sort(),
    );

    // Independent of the create-page UI: execute the now-saved composite
    // directly through /composite/execute/ with a fresh mapping value, and
    // confirm the same avg(1.0, 0.0) = 0.5 / pass math holds.
    const executed = await actor.api.post<CompositeExecuteResult>(
      `/model-hub/eval-templates/${compositeId}/composite/execute/`,
      { mapping: { output: 'confirm' } },
    );
    expect(executed.result.aggregate_score).toBe(0.5);
    expect(executed.result.aggregate_pass).toBe(true);
    const byName = Object.fromEntries(executed.result.children.map((c) => [c.child_name, c]));
    expect(byName[passChildName].score).toBe(1);
    expect(byName[failChildName].score).toBe(0);
    expect(byName[passChildName].reason).toBe(`${passVerdict} saw confirm`);
    expect(byName[failChildName].reason).toBe(`${failVerdict} saw confirm`);
  });
});
