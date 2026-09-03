import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { JUDGE_MODEL, ensureJudgeModel } from '../../lib/eval-model';

// Browser-side waits. The stack slows several-fold when specs run in parallel
// (CI runs two workers), so these are sized off that rather than the 10s
// expect default.
const UI_READY = 60_000;
// One synchronous playground run: prompt -> gateway -> mock LLM -> parse ->
// render, sized for the slowest case (a composite fanning out to children).
const EVAL_RUN = 90_000;

// Group B of today's scenario list ("testing an eval after filtering by a
// non-Custom source tab": Dataset / Tracing / Simulation x agent/llm/code)
// collapses to one flow per source tab, not one per eval type. Grounded by
// TestPlayground.test.jsx (extended today): the "source tabs: Dataset /
// Tracing / Simulation" describe block proves TestPlayground's own contract
// with each mode component is pure delegation — same prop set forwarded,
// same ref.runTest() delegation — regardless of `evalType` (evalType only
// feeds TestPlayground's own variable-extraction memo, which is orthogonal
// to which source tab is active). So proving the Dataset-tab mechanism once,
// with whichever eval type is cheapest to run, covers all three eval types
// for this tab. Code is used here for the same reason eval-code.spec.ts
// gives: no capability gate, no LLM/gateway plumbing, deterministic
// sandboxed execution — keeping this flow's focus on the source-tab
// mechanism rather than re-proving judge-model plumbing already covered by
// eval-create.spec.ts / eval-playground.spec.ts.
//
// This flow is deliberately distinct from EVAL-E2E-006 (which also drives
// DatasetTestMode, from the eval-creation drawer): here the eval already
// exists and is opened from its own detail page, where DatasetTestMode is
// NOT given an `initialDatasetId` — so, unlike EVAL-E2E-006, the dataset
// Autocomplete picker itself is exercised (browse/search/select a dataset
// from scratch), a genuinely different code path within the same component.

interface CreateDatasetResult { result: { dataset_id: string } }
interface DatasetTableColumn { id: string; name: string }
interface DatasetTableRow { row_id: string }
interface DatasetTableResult { result: { column_config: DatasetTableColumn[]; table: DatasetTableRow[] } }
interface CreatedId { result: { id: string } }

test('EVAL-E2E-007: test an existing eval against a real dataset row via the Dataset source tab', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-007', area: 'evals',
    userGoal: 'A developer opens a saved eval, switches the playground to the Dataset source tab, '
      + 'picks a real dataset from scratch, maps the eval\'s variable to a real column, and runs the test',
    steps: ['author a deterministic Code eval via the API', 'seed a one-row dataset via the API',
            'open the eval\'s detail page', 'switch the playground to the Dataset source tab',
            'search for and select the seeded dataset', 'map the code\'s "output" parameter to the real column',
            'click Test Evaluation and read the Pass verdict and reason'],
    backendChecks: ['DatasetTestMode fetches the dataset\'s real row via get-dataset-table, not a mock',
                    'the Test Evaluation button stays disabled until onReadyChange reports the dataset '
                      + 'selected and every variable mapped (TestPlayground -> EvalDetailPage\'s '
                      + 'isPlaygroundReady gate)',
                    'the eval executes in the sandboxed Python executor against the real cell value, '
                      + 'with no model/gateway involved'],
  }),
}, async ({ page, actor }, testInfo) => {
  // Every bounded wait in this spec, chained: past the config's 120s
  // default, so a slow run ends on the assertion that ran out rather
  // than a bare test timeout.
  test.setTimeout(180_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const datasetName = `e2e-testmode-dataset-${suffix}`;
  const evalName = `e2e-testmode-code-${suffix}`;
  const marker = `e2e-marker-${suffix}`;

  const evaluateCode = `def evaluate(output, **kwargs): `
    + `return {"score": 1.0, "reason": "${marker} found in output"} if "${marker}" in str(output) `
    + `else {"score": 0.0, "reason": "${marker} missing from output"}`;

  await ensureJudgeModel(actor);

  // `model` is never used by a code eval at run time (CustomCodeEval hardcodes
  // `_model = None`), but it is still required by the template serializer.
  // Seeding it here matters because of what the DETAIL page does on load:
  // EvalCreatePage/EvalDetailPage resolve `config.model || d.model ||
  // "turing_large"`, and the `turing_models` denial then blanks that fallback
  // back to "" — so a model-less code eval makes the page autosave a blank
  // `model` and every later draft PUT 400s with "model: This field may not be
  // blank." before the playground run is ever reached.
  const template = await actor.api.post<CreatedId>('/model-hub/eval-templates/create-v2/', {
    name: evalName, eval_type: 'code', code: evaluateCode, code_language: 'python',
    model: JUDGE_MODEL, output_type: 'pass_fail', pass_threshold: 0.5,
  });
  const templateId = template.result.id;
  await testInfo.attach('template-id', { body: templateId, contentType: 'text/plain' });

  const { dataset_id: datasetId } = (await actor.api.post<CreateDatasetResult>(
    '/model-hub/develops/create-dataset-manually/',
    { dataset_name: datasetName, model_type: 'GenerativeLLM', number_of_rows: 1, number_of_columns: 1 },
  )).result;
  await testInfo.attach('dataset-id', { body: datasetId, contentType: 'text/plain' });
  const table = await actor.api.get<DatasetTableResult>(
    `/model-hub/develops/${datasetId}/get-dataset-table/`, { current_page_index: 0, page_size: 10 },
  );
  const column = table.result.column_config[0];
  const row = table.result.table[0];
  await actor.api.post(`/model-hub/develops/${datasetId}/update_cell_value/`, {
    column_id: column.id, row_id: row.row_id, new_value: `contains ${marker} right here`,
  });

  await page.goto(`/dashboard/evaluations/${templateId}`);

  await test.step('UI: switch to the Dataset source tab and pick the seeded dataset', async () => {
    await page.getByRole('tab', { name: 'Dataset' }).click();
    await page.getByPlaceholder('Choose from dataset list').click();
    await page.getByRole('option', { name: datasetName }).click();
    await expect(page.getByText(`"contains ${marker} right here"`)).toBeVisible({ timeout: UI_READY });
  });

  await test.step('UI: map the variable to the real column', async () => {
    // The readiness gate this flow claims: a dataset alone is not enough,
    // the variable has to be mapped before TestPlayground reports ready
    // (onReadyChange -> EvalDetailPage's isPlaygroundReady).
    await expect(page.getByRole('button', { name: 'Test Evaluation' })).toBeDisabled();
    // Same ColumnTreeSelect disambiguation as EVAL-E2E-006 — the row-detail
    // table also shows the literal text "Column 1", so filter the dropdown's
    // own search box first and take the portal-mounted match.
    await page.getByPlaceholder('Select column').click();
    await page.getByPlaceholder('Search columns…').fill('Column 1');
    await page.getByText('Column 1', { exact: true }).last().click();
    await expect(page.getByRole('button', { name: 'Test Evaluation' })).toBeEnabled({ timeout: UI_READY });
  });

  await test.step('UI: test — Pass, with the real cell value proven in the reason', async () => {
    await page.getByRole('button', { name: 'Test Evaluation' }).click();
    await expect(page.getByRole('button', { name: 'Test Evaluation' })).toBeVisible({ timeout: EVAL_RUN });
    await expect(page.getByText('Pass', { exact: true })).toBeVisible();
    // Exact: the reason string is also inside the code editor's Monaco buffer
    // (a `<span class="mtk20">` token), so a substring match resolves to both
    // it and the result <pre>. Only the <pre> equals it exactly.
    await expect(page.getByText(`${marker} found in output`, { exact: true })).toBeVisible();
  });
});
