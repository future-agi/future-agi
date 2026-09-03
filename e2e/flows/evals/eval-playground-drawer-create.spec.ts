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

// Group A of today's scenario list ("alternate eval-creation entry points":
// datasets / Observe / Simulation, x agent/llm/code) collapses to ONE flow.
// Grounded by reading the actual mechanism rather than assuming the 3x3 grid
// needs 9 flows:
//
// 1. EvaluationDrawer.jsx's "config" visibleSection is intercepted (a
//    useEffect that fires the instant visibleSection becomes "config") to
//    open the shared `EvalPickerDrawer` and immediately snap visibleSection
//    back to "list" — EvaluationsSelectionGrid/EvaluationCardsGrid (the
//    inline eval-browsing UI investigated today) never gets a rendered frame
//    in this drawer for dataset/task/experiment modules. The real "Add
//    Evaluation" UI is `src/sections/common/EvalPicker/EvalPickerDrawer.jsx`,
//    and creating a brand-new eval from inside it is
//    `EvalPickerCreateNew.jsx`, reached via its list step's "Create New Eval"
//    button.
// 2. EvalPickerCreateNew.jsx's eval-type tabs (Agents/LLM-As-A-Judge/Code)
//    only swap the middle editor (InstructionEditor vs
//    ModelSelector+LLMPromptEditor vs CodeEvalEditor) — draft creation,
//    auto-save, test-and-save wiring, and the endpoint the saved eval is
//    POSTed to are all identical regardless of which tab is active. Agent
//    type is additionally gated behind `useFeatureLocked(CAPABILITY.
//    AGENTIC_EVAL)` (same gate eval-playground-code.spec.ts documents for
//    the standalone create page) — but that gate is unconditionally open
//    off-cloud (`agentic_eval` isn't `oss_locked`; see
//    eval-playground-agent-connectors.spec.ts, which relies on this to
//    always show the Agent tab), so it's not actually a determinism concern
//    here either. This flow still uses Code, for the same reasoning
//    eval-playground-code.spec.ts gives independent of the gate: no LLM/
//    gateway plumbing, deterministic sandboxed execution.
// 3. The per-entry-point routing this drawer performs (dataset -> add-eval
//    save-only, task -> createEvalTaskConfig run:true, experiment ->
//    experiment.addEval) is exhaustively proven at the unit level by
//    EvaluationDrawer.test.jsx (read today) asserting on the exact outgoing
//    URL/payload per module. Re-driving that same `handleRun` switch end-to-
//    end for every module would re-assert a different endpoint string
//    through the identical code path — not exercise new UI surface.
// 4. The right-hand test/mapping panel EvalPickerCreateNew renders per
//    `source` (DatasetTestMode / TracingTestMode / SimulationTestMode) is
//    the exact same component TestPlayground.jsx uses for an *existing*
//    eval — which is Group B's subject. Proving the dataset-mapping and
//    tracing-mapping mechanisms once each there covers this drawer's right
//    panel too; there is no additional behavior in EvalPickerCreateNew's use
//    of those components beyond what TestPlayground already exercises.
// Simulation as an entry point is skipped here for the same reason it's
// skipped in Group B: no cheap, real way to seed a simulation run was found
// (grepped e2e/lib and e2e/flows — no existing helper, unlike sendTrace for
// Observe or create-dataset-manually for datasets), and guessing at a UI
// flow to create one live risks asserting on unverified selectors.

interface CreateDatasetResult { result: { dataset_id: string } }
interface DatasetTableColumn { id: string; name: string }
interface DatasetTableRow { row_id: string }
interface DatasetTableResult { result: { column_config: DatasetTableColumn[]; table: DatasetTableRow[] } }
interface EvalsListResult { result: { evals: { id: string; name: string }[] } }

test('EVAL-E2E-006: author a brand-new Code eval from the shared Add-Evaluation drawer on a dataset', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-006', area: 'evals',
    userGoal: 'A developer opens the shared eval-picker drawer from a dataset\'s "Evaluate" '
      + 'action, creates a brand-new Code eval inline (rather than the standalone create page), '
      + 'tests it against a real row of that same dataset, and saves it as a dataset binding',
    steps: ['open a dataset that already has one row', 'click "Evaluate" to open the shared eval drawer',
            'click "Add Evaluations" to open the eval picker', 'click "Create New Eval"',
            'switch to the Code eval type and name it', 'write a Python evaluate() function',
            'map the function\'s "output" parameter to the dataset\'s real column',
            'test it against the real row and read the Pass verdict',
            'save it, and confirm the dataset now carries this eval as a saved (not run) binding'],
    backendChecks: ['the saved template is returned by the main eval list, which excludes drafts '
                      + '(the create path stores visible_ui = not is_draft)',
                    'the dataset picked up by the drawer is the exact dataset the drawer was opened from '
                      + '(sourceId threads through EvaluationDrawer -> EvalPickerDrawer -> EvalPickerCreateNew '
                      + 'as DatasetTestMode\'s initialDatasetId, so no dataset picker is even shown)',
                    'the code runs in the sandboxed Python executor against the dataset\'s actual cell value',
                    'saving POSTs template_id to develops/<datasetId>/add_user_eval/ with run:false — '
                      + 'a save-only dataset binding, not an immediate run',
                    'the dataset\'s own eval list (get_evals_list) now includes this eval by name'],
  }),
}, async ({ page, actor }, testInfo) => {
  // Every bounded wait in this spec, chained: past the config's 120s
  // default, so a slow run ends on the assertion that ran out rather
  // than a bare test timeout.
  test.setTimeout(300_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const datasetName = `e2e-drawer-dataset-${suffix}`;
  const evalName = `e2e-drawer-code-${suffix}`;
  // Exists nowhere else, so finding it in the returned reason proves the
  // sandboxed function ran against this exact cell's real value.
  const marker = `e2e-marker-${suffix}`;

  await ensureJudgeModel(actor);

  const { dataset_id: datasetId } = (await actor.api.post<CreateDatasetResult>(
    '/model-hub/develops/create-dataset-manually/',
    { dataset_name: datasetName, model_type: 'GenerativeLLM', number_of_rows: 1, number_of_columns: 1 },
  )).result;
  await testInfo.attach('dataset-id', { body: datasetId, contentType: 'text/plain' });

  // The view creates exactly one column named "Column {i+1}" per
  // ManuallyCreateDatasetView (model_hub/views/develop_dataset.py) — with
  // number_of_columns=1 that's deterministically "Column 1".
  const table = await actor.api.get<DatasetTableResult>(
    `/model-hub/develops/${datasetId}/get-dataset-table/`, { current_page_index: 0, page_size: 10 },
  );
  const column = table.result.column_config[0];
  const row = table.result.table[0];
  expect(column.name).toBe('Column 1');

  await actor.api.post(`/model-hub/develops/${datasetId}/update_cell_value/`, {
    column_id: column.id, row_id: row.row_id, new_value: `contains ${marker} right here`,
  });

  // Single-line body (no embedded newlines) — same reasoning as
  // eval-code.spec.ts: Monaco's auto-indent-on-Enter would otherwise corrupt
  // hand-typed indentation on a multi-line function body.
  const evaluateCode = `def evaluate(output, **kwargs): `
    + `return {"score": 1.0, "reason": "${marker} found in output"} if "${marker}" in str(output) `
    + `else {"score": 0.0, "reason": "${marker} missing from output"}`;

  await page.goto(`/dashboard/develop/${datasetId}?tab=data`);

  await test.step('UI: open the shared eval drawer and the eval picker', async () => {
    await page.getByRole('button', { name: 'Evaluate' }).click();
    // The dataset page mounts several EvaluationDrawers (DevelopDetailView,
    // RunOptimization, DatasetOptimizationDrawer) and a closed one keeps its
    // whole subtree in the DOM, so every string it renders exists 2+ times
    // page-wide. Scoping by `.MuiDrawer-root` makes it worse — those roots
    // nest. Visibility is what distinguishes the drawer just opened.
    await expect(
      page.getByText('All Evaluations', { exact: true }).filter({ visible: true }),
    ).toBeVisible();
    await expect(
      page.getByText('Select and configure the evals to run in your dataset', { exact: true })
        .filter({ visible: true }),
    ).toBeVisible();
    await page.getByRole('button', { name: 'Add Evaluations' }).click();
    await expect(page.getByText('Select Evaluation')).toBeVisible();
  });

  await test.step('UI: create a new Code eval', async () => {
    await page.getByRole('button', { name: 'Create New Eval' }).click();
    await expect(page.getByText('Create New Evaluation')).toBeVisible();
    // Pick the model BEFORE switching to Code. A Code eval never calls one,
    // but every save goes through the shared template serializer, which
    // rejects a blank `model` with 400 "model: This field may not be blank."
    // The Code tab renders no ModelSelector, and `model` is one useState
    // shared across tabs — so the default Agents tab is the only place to set
    // it. EVAL-E2E-005 hits the same constraint.
    await selectJudgeModel(page, JUDGE_MODEL);
    await page.getByRole('tab', { name: 'Code', exact: true }).click();
    await page.getByPlaceholder('e.g. hallucination_detector').fill(evalName);

    // The drawer's panel holds exactly one Monaco, but the dataset page
    // underneath keeps its own column-formula Monaco mounted — and that one
    // is FIRST in DOM order. It is hidden while the drawer is open, so filter
    // on visibility rather than guessing an index.
    const codeEditor = page.locator('.monaco-editor .view-lines').filter({ visible: true });
    await codeEditor.click();
    await page.keyboard.press('ControlOrMeta+A');
    await page.keyboard.press('Backspace');
    await page.keyboard.type(evaluateCode, { delay: 5 });
  });

  await test.step('UI: the dataset is already pre-selected — map the variable to the real column', async () => {
    // initialDatasetId hides DatasetTestMode's own dataset Autocomplete
    // entirely, so the row table for our dataset renders immediately.
    await expect(page.getByText(`"contains ${marker} right here"`)).toBeVisible({ timeout: UI_READY });

    // RISK: ColumnTreeSelect (frontend/src/sections/evals/components/
    // DatasetTestMode.jsx) is a custom dropdown, not a native <select> or MUI
    // Autocomplete with an ARIA "option" role — clicking its trigger opens a
    // Popper portal containing a plain clickable Typography per column. The
    // dataset's own row-detail table also renders the literal text
    // "Column 1" as a column-name label, so this text is ambiguous on the
    // page as a whole. Filtering the dropdown's own search box first, then
    // taking .last() (the Popper is portal-mounted after the row table in
    // DOM order), is how eval-create.spec.ts disambiguates an analogous
    // duplicate-name situation for the model picker.
    await page.getByPlaceholder('Select column').click();
    await page.getByPlaceholder('Search columns…').fill('Column 1');
    await page.getByText('Column 1', { exact: true }).last().click();
  });

  await test.step('UI: test against the real row — Pass', async () => {
    await page.getByRole('button', { name: 'Test Evaluation' }).click();
    await expect(page.getByRole('button', { name: 'Test Evaluation' })).toBeVisible({ timeout: EVAL_RUN });
    await expect(page.getByText('Pass', { exact: true })).toBeVisible();
    // Exact: the reason string is also a literal in the code editor's Monaco
    // buffer, so a substring match resolves to both it and the result <pre>.
    await expect(page.getByText(`${marker} found in output`, { exact: true })).toBeVisible();
  });

  await test.step('UI: save as a dataset binding — the picker closes back to the drawer\'s saved-evals list', async () => {
    // The binding request itself, not just its rendered aftermath: "save-only"
    // is a property of the payload (run:false), and the drawer looks identical
    // either way.
    const addEvalPost = page.waitForRequest((r) =>
      r.url().includes(`/model-hub/develops/${datasetId}/add_user_eval/`) && r.method() === 'POST',
      { timeout: UI_READY });
    await page.getByRole('button', { name: 'Save & Add Evaluation' }).click();
    const bound = (await addEvalPost).postDataJSON();
    expect(bound.run).toBe(false);
    expect(bound.template_id).toBeTruthy();
    // keepOpenAfterSave={module === "dataset"} only stops EvalPickerContent.
    // handleSaveEval from calling onClose itself; the host closes the picker
    // regardless. EvaluationDrawer's onEvalAdded awaits
    // `handleRun(payload, () => { setEvalPickerOpen(false);
    // setVisibleSection("list"); })`, and handleRun's onSuccessFn invokes that
    // success callback on every successful add — so a saved eval always lands
    // back on the parent drawer's saved-evals list, not the picker's list step.
    await expect(page.getByText('Select Evaluation')).toBeHidden({ timeout: UI_READY });
    await expect(
      page.getByText(evalName, { exact: true }).filter({ visible: true }),
    ).toBeVisible({ timeout: UI_READY });
    // The mapping made above is what the binding carries — the saved-evals row
    // renders it as "<variable>\u2192<column>".
    await expect(
      page.getByText(/output\s*\u2192\s*Column 1/).filter({ visible: true }).first(),
    ).toBeVisible();
  });

  await test.step('API lane: the eval is published and bound to this dataset as a save-only entry', async () => {
    const list = await actor.api.post<{ result: { items: { id: string; name: string; is_draft?: boolean }[] } }>(
      '/model-hub/eval-templates/list/', { search: evalName },
    );
    expect(list.result.items.map((i) => i.name)).toContain(evalName);

    const evals = await actor.api.get<EvalsListResult>(`/model-hub/develops/${datasetId}/get_evals_list/`);
    expect(evals.result.evals.map((e) => e.name)).toContain(evalName);
  });
});
