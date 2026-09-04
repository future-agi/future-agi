import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { JUDGE_MODEL, ensureJudgeModel, fillTestData, selectJudgeModel } from '../../lib/eval-model';

// Browser-side waits. The stack slows several-fold when specs run in parallel
// (CI runs two workers), so these are sized off that rather than the 10s
// expect default.
const UI_READY = 60_000;
// One synchronous playground run: prompt -> gateway -> mock LLM -> parse ->
// render, sized for the slowest case (a composite fanning out to children).
const EVAL_RUN = 90_000;

// Routes the judge model through the mock LLM behind the real gateway — same
// setup as eval-create.spec.ts (EVAL-E2E-003) / eval-task.spec.ts.

// Why this flow can't literally prove the few-shot content reached the
// model (the way EVAL-E2E-003/005/007 prove their inputs round-tripped via
// the mock's echo):
//
// e2e/stack/mock-llm/server.mjs's `reply()` returns
// `echo: <content of the LAST role:"user" message>`. Reading
// agentic_eval/core_evals/fi_evals/llm/custom_prompt_evaluator/evaluator.py
// (lines ~378-423, identical in structure to the ee/ copy) shows the
// `messages` array is built as: [system] + [one user/assistant pair per
// few-shot example] + [any extra editor turns] + [the real eval user
// message, ALWAYS appended last]. Since the mock's `reply()` walks the
// array backwards and stops at the first `role: "user"` it finds, it always
// resolves to that final real eval message — never to a few-shot example's
// user turn, no matter how few-shot content is crafted. There is no
// assertion this suite can make on the Test Evaluation output that would
// distinguish "few-shot examples were sent to the model" from "they
// weren't" — the mock is structurally blind to every message but the last.
//
// So this flow instead verifies the two things that ARE real and
// observable end-to-end:
//   1. The FewShotExamples picker (a dataset multi-select, not a search-as-
//      you-type box — its `useQuery` in FewShotExamples.jsx calls
//      `axios.get(getDatasets())` with no params at all, so it always reads
//      backend page 0 / page_size 10 with no name filter) actually drives
//      `fewShotExamples` state, which EvalCreatePage.jsx's
//      `handleTestEvaluation` persists to the draft (`few_shot_examples:
//      fewShotExamples.map(ds => ({id, name}))`, line ~404-407) BEFORE the
//      playground test runs — same "draft saved before test" contract
//      eval-create.spec.ts / eval-code.spec.ts already document.
//   2. That saved `few_shot_examples: [{id, name}]` round-trips intact
//      through `GET /model-hub/eval-templates/<id>/detail/` after publish.
// This also exercises the real backend resolution path for real: reading
// futureagi/evaluations/engine/instance.py (~356-369) confirms that at
// eval-run time (including the Test Evaluation click, since it runs through
// the same `EvaluationRunner._create_eval_instance` ->
// `evaluations.engine.instance.create_eval_instance`), a dataset-ID few-shot
// entry is expanded via `expand_static_few_shot_examples` /
// `_examples_from_datasets` (model_hub/utils/few_shot_examples.py), which
// requires the dataset to have columns literally named "input" and "output"
// (case-insensitive) — hence this flow builds its dataset with exactly
// those two column names via the API, and a passing Test Evaluation proves
// that resolution path ran without error against a real dataset row.
//
// Dataset discoverability without server-side search: BaseModel.Meta
// (tfc/utils/base_model.py) sets `ordering = ("-created_at",)`, and
// GetDatasetsView (model_hub/views/develop_dataset.py) never calls its own
// `.order_by()` unless the caller sends `sort` — which FewShotExamples.jsx
// never does. So Django applies the model's default ordering, and our
// freshly-created dataset (newest in the org) is guaranteed to land on page
// 0 regardless of how many older datasets other specs left behind in this
// shared, worker-scoped `actor` org.

interface CreateEmptyDatasetResult { result: { dataset_id: string; dataset_name: string } }
interface AddedColumn { id: string; name: string }
interface AddColumnsResult { result: { data: AddedColumn[] } }
interface DatasetTableRow { row_id: string }
interface DatasetTableResult { result: { table: DatasetTableRow[] } }
interface EvalDetailResult { result: { config: { few_shot_examples?: { id: string; name: string }[] } } }
interface EvalListResponse { result: { items: { id: string; name: string }[]; total: number } }

test('EVAL-E2E-021: attach few-shot examples to an LLM-as-a-judge eval and publish it', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-021', area: 'evals',
    userGoal: 'A developer creates an LLM-as-a-Judge eval, attaches a dataset of few-shot examples via the '
      + 'Few-shot Examples picker, tests the eval, and publishes it with the dataset selection persisted',
    steps: ['seed a two-column (input/output) few-shot dataset via the API', 'open the create-eval page',
            'name the eval and switch to the LLM-As-A-Judge type', 'pick a model',
            'write judge instructions with a template variable',
            'open the Few-shot Examples picker and select the seeded dataset',
            'see the dataset rendered as a chip', 'test the draft and read the Pass verdict',
            'save/publish the eval'],
    backendChecks: ['the Few-shot Examples Autocomplete reads real datasets from get-datasets, not a mock',
                    'the draft is saved with `few_shot_examples: [{id, name}]` before the playground test runs',
                    'the saved config round-trips through the eval-templates detail endpoint after publish',
                    'the LLM-as-a-judge run resolves the dataset-backed few-shot entry via '
                      + 'expand_static_few_shot_examples without error'],
  }),
}, async ({ page, actor }, testInfo) => {
  // Every bounded wait in this spec, chained: past the config's 120s
  // default, so a slow run ends on the assertion that ran out rather
  // than a bare test timeout.
  test.setTimeout(240_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const datasetName = `e2e-fewshot-ds-${suffix}`;
  // Sanitized to [a-z0-9_-] on input by the Eval Name field itself — no spaces/case.
  const evalName = `e2e-fewshot-eval-${suffix}`;
  // Exists nowhere else, so finding it in the result proves the round trip
  // through the real (non-few-shot) rendered instructions text.
  const verdict = `e2e-verdict-${suffix}`;

  await ensureJudgeModel(actor);

  let datasetId = '';
  await test.step('API: seed a two-column (input/output) few-shot dataset with one row', async () => {
    const created = await actor.api.post<CreateEmptyDatasetResult>(
      '/model-hub/develops/create-empty-dataset/',
      { new_dataset_name: datasetName, model_type: 'GenerativeLLM' },
    );
    datasetId = created.result.dataset_id;

    const columns = await actor.api.post<AddColumnsResult>(
      `/model-hub/develops/${datasetId}/add_columns/`,
      {
        new_columns_data: [
          { name: 'input', data_type: 'text' },
          { name: 'output', data_type: 'text' },
        ],
      },
    );
    const inputColumn = columns.result.data.find((c) => c.name === 'input')!;
    const outputColumn = columns.result.data.find((c) => c.name === 'output')!;

    await actor.api.post(`/model-hub/develops/${datasetId}/add_empty_rows/`, { num_rows: 1 });
    const table = await actor.api.get<DatasetTableResult>(
      `/model-hub/develops/${datasetId}/get-dataset-table/`, { current_page_index: 0, page_size: 10 },
    );
    const rowId = table.result.table[0].row_id;

    await actor.api.post(`/model-hub/develops/${datasetId}/update_cell_value/`, {
      column_id: inputColumn.id, row_id: rowId, new_value: `few-shot input ${suffix}`,
    });
    await actor.api.post(`/model-hub/develops/${datasetId}/update_cell_value/`, {
      column_id: outputColumn.id, row_id: rowId, new_value: `few-shot output ${suffix}`,
    });
  });

  let draftId = '';

  await test.step('UI: a draft is auto-created on page load', async () => {
    await page.goto('/dashboard/evaluations/create');
    await page.waitForURL(/\/dashboard\/evaluations\/create\/.+/, { timeout: UI_READY });
    draftId = page.url().split('/dashboard/evaluations/create/')[1];
    expect(draftId).toMatch(/.+/);
    await testInfo.attach('draft-id', { body: draftId, contentType: 'text/plain' });
  });

  await test.step('UI: name it and switch to LLM-as-a-Judge', async () => {
    await page.getByPlaceholder('Eg: Hallucination detector').fill(evalName);
    await page.getByRole('tab', { name: 'LLM-As-A-Judge' }).click();
  });

  await test.step('UI: pick the mock-routed model', async () => {
    await selectJudgeModel(page, JUDGE_MODEL);
  });

  await test.step('UI: write judge instructions with a template variable', async () => {
    await page.locator('.ql-editor').click();
    await page.keyboard.type(
      `Reply with exactly this JSON: {"result": "Pass", "explanation": "${verdict} saw {{output}}"}`,
      { delay: 10 },
    );
    // Dismiss any mention-autocomplete popup the "{{" denotation may have opened.
    await page.keyboard.press('Escape');
  });

  await test.step('UI: attach the seeded dataset as a few-shot example', async () => {
    await page.getByPlaceholder('Search datasets...').click();
    await page.getByPlaceholder('Search datasets...').fill(datasetName);
    await page.getByRole('option', { name: datasetName }).click();
    // Close the still-open listbox so the option's own text node doesn't
    // shadow the chip's identical text when we assert on it next.
    await page.keyboard.press('Escape');
    await expect(page.getByText(datasetName)).toBeVisible();
  });

  await test.step('UI: test the draft against custom input', async () => {
    await fillTestData(page, '{"output": "world"}');
    await page.getByRole('button', { name: 'Test Evaluation' }).click();
    await expect(page.getByRole('button', { name: 'Test Evaluation' })).toBeVisible({ timeout: EVAL_RUN });
    await expect(page.getByText('Pass', { exact: true })).toBeVisible();
    await expect(page.getByText(`${verdict} saw world`)).toBeVisible();
  });

  await test.step('UI: publish, and API lane confirms the few-shot selection persisted', async () => {
    await page.getByRole('button', { name: 'Save Evaluation' }).click();
    await expect(page.getByText('Evaluation saved successfully')).toBeVisible({ timeout: UI_READY });
    // EvalDetailPage appends `?v=<version_number>` once it has loaded the
    // saved version (:417), so anchor on the id followed by end-of-string
    // OR the query string rather than end-of-string alone.
    await expect(page).toHaveURL(new RegExp(`/dashboard/evaluations/${draftId}(\\?|$)`));

    const list = await actor.api.post<EvalListResponse>('/model-hub/eval-templates/list/', { search: evalName });
    expect(list.result.items.map((i) => i.id)).toEqual([draftId]);

    const detail = await actor.api.get<EvalDetailResult>(`/model-hub/eval-templates/${draftId}/detail/`);
    expect(detail.result.config.few_shot_examples).toEqual([{ id: datasetId, name: datasetName }]);
  });
});
