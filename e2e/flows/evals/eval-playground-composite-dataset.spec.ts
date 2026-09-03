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

// Routes both child judges through the mock LLM behind the real gateway —
// same setup as eval-composite.spec.ts. This flow is eval-composite.spec.ts's
// twin with one deliberate swap: the Custom JSON tab is replaced by the
// Dataset source tab, proving DatasetTestMode's own isComposite branch
// (confirmed by reading TestPlayground.jsx/DatasetTestMode.jsx: isComposite
// is threaded through and switches the run call to
// useExecuteCompositeEvalAdhoc instead of the single-eval path) rather than
// re-proving the aggregation math eval-composite.spec.ts already covers.

interface CreatedId { result: { id: string } }
interface CreateDatasetResult { result: { dataset_id: string } }
interface DatasetTableColumn { id: string; name: string }
interface DatasetTableRow { row_id: string }
interface DatasetTableResult { result: { column_config: DatasetTableColumn[]; table: DatasetTableRow[] } }

test('EVAL-E2E-030: test a composite eval against a real dataset row via the Dataset source tab', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-030', area: 'evals',
    userGoal: 'A developer combines two evals into a composite, switches the playground to the '
      + 'Dataset source tab, maps the union of child variables to a real dataset column, and reads '
      + 'the aggregate result from a real row instead of typed JSON',
    steps: ['author two deterministic pass/fail child evals via the API', 'seed a one-row dataset via the API',
            'open the create-eval page and switch to Composite mode', 'name it and add both children',
            'switch the playground to the Dataset source tab', 'pick the seeded dataset from scratch',
            'map the union variable to the real column', 'test and read the aggregate PASS + per-child scores'],
    backendChecks: ['the Dataset tab dispatches the run to composite/execute-adhoc, not the single-eval '
                      + 'path, when the eval under test is composite',
                    'the union of both children\'s required_keys collapses to one mapped column, '
                      + 'since both children declare the same {{output}} variable',
                    'the aggregate score is avg(1.0, 0.0) = 0.5, identical math to the Custom-tab '
                      + 'composite flow, proving the source tab only changes how input is supplied'],
  }),
}, async ({ page, actor }, testInfo) => {
  // Every bounded wait in this spec, chained: past the config's 120s
  // default, so a slow run ends on the assertion that ran out rather
  // than a bare test timeout.
  test.setTimeout(240_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  // Hyphens, not spaces: create-v2 rejects anything outside [a-z0-9_-] with
  // "Name can only contain lowercase letters, numbers, hyphens (-), or
  // underscores (_)."
  const passChildName = `e2e-composite-ds-pass-${suffix}`;
  const failChildName = `e2e-composite-ds-fail-${suffix}`;
  const compositeName = `e2e-composite-ds-${suffix}`;
  const datasetName = `e2e-composite-ds-${suffix}`;
  // Each exists nowhere else, so finding it in a child's `reason` proves that
  // child actually ran against the real cell value.
  const passVerdict = `e2e-pass-verdict-${suffix}`;
  const failVerdict = `e2e-fail-verdict-${suffix}`;
  const cellMarker = `e2e-cell-${suffix}`;

  await ensureJudgeModel(actor);

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

  const { dataset_id: datasetId } = (await actor.api.post<CreateDatasetResult>(
    '/model-hub/develops/create-dataset-manually/',
    { dataset_name: datasetName, model_type: 'GenerativeLLM', number_of_rows: 1, number_of_columns: 1 },
  )).result;
  const table = await actor.api.get<DatasetTableResult>(
    `/model-hub/develops/${datasetId}/get-dataset-table/`, { current_page_index: 0, page_size: 10 },
  );
  const column = table.result.column_config[0];
  const row = table.result.table[0];
  await actor.api.post(`/model-hub/develops/${datasetId}/update_cell_value/`, {
    column_id: column.id, row_id: row.row_id, new_value: `contains ${cellMarker} right here`,
  });
  await testInfo.attach('seeded-ids', {
    body: JSON.stringify({
      passChildId: passChild.result.id, failChildId: failChild.result.id, datasetId,
    }),
    contentType: 'application/json',
  });

  await page.goto('/dashboard/evaluations/create');

  await test.step('UI: switch to Composite mode, name it and add both children', async () => {
    await page.getByRole('tab', { name: 'Composite' }).click();
    await expect(page.getByText('Composite Configuration')).toBeVisible();
    const compositePanel = page.getByText('Composite Configuration').locator('xpath=../..');
    await compositePanel.locator('input[type="text"]').first().fill(compositeName);

    const addChild = async (childName: string) => {
      await page.getByRole('button', { name: 'Add evaluation' }).click();
      const searchBox = page.getByPlaceholder('Search evaluations...');
      await searchBox.fill(childName);
      const row2 = page.locator('tr').filter({ hasText: childName });
      await expect(row2).toBeVisible({ timeout: UI_READY });
      await row2.getByRole('button', { name: 'Add', exact: true }).click();

      // Inline add vs the "Configure Evaluation" step — same branch
      // eval-playground-composite.spec.ts documents in full
      // (EvalPickerDrawer.jsx:83-101 / :54-77). These children carry an
      // explicit `model`, so inline is expected; a model-less child (the
      // OSS norm) takes the config step instead.
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
    await addChild(passChildName);
    await addChild(failChildName);
    await expect(page.getByText('Children (2)')).toBeVisible();
  });

  await test.step('UI: switch to Dataset, pick the seeded dataset and map the column', async () => {
    await page.getByRole('tab', { name: 'Dataset' }).click();
    await page.getByPlaceholder('Choose from dataset list').click();
    await page.getByRole('option', { name: datasetName }).click();
    await expect(page.getByText(`"contains ${cellMarker} right here"`)).toBeVisible({ timeout: UI_READY });

    // Same ColumnTreeSelect disambiguation as eval-testmode-dataset.spec.ts:
    // the row-detail table also renders the literal text "Column 1", so
    // filter the dropdown's own search box first and take the portal-mounted
    // match.
    await page.getByPlaceholder('Select column').click();
    await page.getByPlaceholder('Search columns…').fill('Column 1');
    await page.getByText('Column 1', { exact: true }).last().click();
  });

  await test.step('UI: test — aggregate PASS with both children\'s real scores', async () => {
    // Which endpoint the tab picks is the claim; the rendered aggregate looks
    // the same either way, so capture the request itself.
    const compositeRun = page.waitForRequest((r) =>
      r.url().includes('/model-hub/eval-templates/composite/execute-adhoc/') && r.method() === 'POST',
      { timeout: EVAL_RUN });
    await page.getByRole('button', { name: 'Test Evaluation' }).click();
    await compositeRun;
    await expect(page.getByRole('button', { name: 'Test Evaluation' })).toBeVisible({ timeout: EVAL_RUN });

    await expect(page.getByText('Aggregate Score (Weighted Average)')).toBeVisible();
    await expect(page.getByText('0.500', { exact: true })).toBeVisible();
    await expect(page.getByText('PASS', { exact: true })).toBeVisible();
    await expect(page.getByText('2/2 completed')).toBeVisible();
    await expect(page.getByText('1.000', { exact: true })).toBeVisible();
    await expect(page.getByText('0.000', { exact: true })).toBeVisible();
    await expect(page.getByText(`${passVerdict} saw contains ${cellMarker} right here`)).toBeVisible();
    await expect(page.getByText(`${failVerdict} saw contains ${cellMarker} right here`)).toBeVisible();
  });
});
