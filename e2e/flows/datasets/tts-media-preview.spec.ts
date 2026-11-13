import { randomUUID } from 'node:crypto';
import { test, expect } from '../../lib/mock-model-fixtures';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// futureagi/model_hub/urls.py: create-empty-dataset, add_columns, add_rows,
// and update_column_type are all under develops (not the datasets API).
const DATASETS = '/model-hub/develops/';
// frontend/src/routes/paths.js and sections/develop-detail/DataTab/DevelopDataV2.jsx.
const DATASET_PAGE = '/dashboard/develop';
// frontend/src/utils/axios.js endpoints.develop.runPrompt.preview;
// RunPrompt.jsx posts onSubmitPreview's transformed config to this endpoint.
const PREVIEW_REQUEST = '**/model-hub/develops/preview_run_prompt_column/';
// futureagi/agentic_eval/core_evals/run_prompt/available_models.py: OpenAI mode=tts.
const MODEL = 'openai/tts-1';
// The same catalog entry's supported_voices; LiteLLMModelVoicesView returns
// catalog voices, and useVoiceOptions maps their name/id to label/value.
const VOICE = 'alloy';
// TH-9061 approved error-state seed. develop_dataset.py:_convert_cell_to_audio
// calls validate_file_url -> ssrf_guard.safe_fetch, which rejects loopback before
// opening a connection. perform_conversion uses allow_partial_failure=True:
// DatatypeConverter.convert preserves the original value and marks the cell error.
// Seed-only constant: changing it must NOT move the storage assertion's oracle.
const RECORDING_URL = 'https://127.0.0.1/th9061-recording.wav';
// futureagi/model_hub/views/run_prompt.py:check_literal_speech_prompt, single
// audio-column rejection; PreviewRunPromptColumnView returns it in result.responses.
const EXPECTED = 'Text-to-speech model "openai/tts-1" reads the prompt aloud as text, so it cannot use the audio column "Recording". Remove this variable from the prompt.';
// e2e/README.md Writing a flow / observe/trace-ingestion.spec.ts: per-action
// first-paint budget under parallel stack load, not the default 10s expect budget.
const UI_READY = 60_000;

test('DATA-E2E-003: a text-to-speech preview explains why it cannot use an audio column', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DATA-E2E-003', area: 'datasets',
    userGoal: 'Understand why a text-to-speech Run Prompt cannot read an audio column aloud, even when its recording URL is unavailable',
    steps: ['seed Text and Recording columns and convert the unavailable recording to audio',
      'open the dataset and name a Run Prompt with Text-to-Speech selected',
      'select openai/tts-1 and the alloy voice',
      'enter {{Recording}} and choose Test on the first row',
      'Run the preview and read the actionable rejection in Prompt Test Raw'],
    backendChecks: ['the actor-scoped dataset has a completed audio Recording column and its exact seeded row retains the URL with cell status error',
      'the browser preview POST targets that dataset and audio-column variable with TTS/alloy, returns HTTP 200 and exactly the rejection displayed in Raw'],
  }),
}, async ({ page, actor, probe, mockModel }, testInfo) => {
  test.setTimeout(360_000); // ASYNC_JOB 60 + 4 x UI_READY 240 + seed/API headroom 60.
  page.setDefaultTimeout(UI_READY);
  const name = `e2e-data3-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const promptName = `${name}-speech`;
  const text = `${name}-text`;
  const rowId = randomUUID();

  const { datasetId, textColumnId, recordingColumnId } = await test.step('seed: API-only error-state audio recording', async () => {
    // Same isolated, attested fixture as prompts/catalog-definition-lifecycle.spec.ts.
    // registerMockModel registers gpt-4o with provider=openai. LiteLLMModelListView
    // includes CustomAIModel providers in valid_providers, making openai/tts-1
    // selectable (ShowModelDropdown otherwise opens key configuration).
    // This does NOT configure a speech provider; no successful synthesis is claimed.
    await mockModel.assertReady();
    // contracts.py:CreateEmptyDatasetRequestSerializer; CreateEmptyDatasetView
    // returns result.dataset_id and creates no rows or columns.
    const created = await actor.api.post<{ result: { dataset_id: string } }>(
      `${DATASETS}create-empty-dataset/`, { new_dataset_name: name });
    const datasetId = created.result.dataset_id;
    // contracts.py:DatasetAddColumnsRequestSerializer + AddColumnsView consume
    // name/data_type; ColumnSerializer in result.data supplies immutable IDs.
    const added = await actor.api.post<{ result: { data: {
      id: string; name: string; data_type: string; dataset: string;
    }[] } }>(`${DATASETS}${datasetId}/add_columns/`, {
      new_columns_data: [{ name: 'Text', data_type: 'text' }, { name: 'Recording', data_type: 'text' }],
    });
    const columns = added.result.data;
    expect(columns.map(c => ({ name: c.name, data_type: c.data_type, dataset: c.dataset })))
      .toEqual([{ name: 'Text', data_type: 'text', dataset: datasetId },
        { name: 'Recording', data_type: 'text', dataset: datasetId }]);
    const textColumnId = columns.find(c => c.name === 'Text')!.id;
    const recordingColumnId = columns.find(c => c.name === 'Recording')!.id;
    await testInfo.attach('seeded-tts-dataset', { contentType: 'application/json',
      body: JSON.stringify({ datasetId, name, promptName, rowId, textColumnId, recordingColumnId,
        columns, text, recordingUrl: RECORDING_URL, mockModelId: mockModel.id,
        organizationId: actor.organizationId, workspaceId: actor.workspaceId }) });
    // contracts.py:DatasetAddRowsRequestSerializer / DatasetRowRequestSerializer /
    // DatasetRowCellRequestSerializer; AddDataRowsView honors the supplied row UUID.
    // Write while Recording is TEXT: writing directly to AUDIO would attempt an
    // upload and replace a failed media value with an empty string.
    await actor.api.post(`${DATASETS}${datasetId}/add_rows/`, {
      rows: [{ id: rowId, cells: [{ column_name: 'Text', value: text },
        { column_name: 'Recording', value: RECORDING_URL }] }],
    });
    // contracts.py:DatasetUpdateColumnTypeRequestSerializer + UpdateColumnTypeView.put.
    // preview:false dispatches perform_conversion (or its synchronous fallback).
    await actor.api.put(`${DATASETS}${datasetId}/update_column_type/${recordingColumnId}/`, {
      new_column_type: 'audio', preview: false, force_update: true,
    });
    return { datasetId, textColumnId, recordingColumnId };
  });

  await test.step('storage: conversion completes and preserves the scoped recording URL', async () => {
    // UpdateColumnTypeView sets data_type BEFORE conversion. Wait for status too;
    // choices.py:StatusType.COMPLETED is capitalized, CellStatus.ERROR is lowercase.
    // PG is authoritative here, so ASYNC_JOB covers conversion, not a CDC wait.
    await expect.poll(() => probe.pg<{ name: string; data_type: string; status: string }>(
      'SELECT name, data_type, status FROM model_hub_column WHERE dataset_id = $1 AND id = $2 AND NOT deleted',
      [datasetId, recordingColumnId]), POLL.ASYNC_JOB)
      .toEqual([{ name: 'Recording', data_type: 'audio', status: 'Completed' }]);
    expect(await probe.pg<{ name: string; organization_id: string; workspace_id: string }>(
      'SELECT name, organization_id, workspace_id FROM model_hub_dataset WHERE id = $1 AND NOT deleted', [datasetId]))
      .toEqual([{ name, organization_id: actor.organizationId, workspace_id: actor.workspaceId }]);
    expect(await probe.pg<{ id: string }>(
      'SELECT id FROM model_hub_row WHERE dataset_id = $1 AND NOT deleted', [datasetId]))
      .toEqual([{ id: rowId }]);
    // develop_dataset.py:Cell.value is TextField. Keep this literal independent
    // of RECORDING_URL so a one-sided seed perturbation demonstrably fails.
    const cells = await probe.pg<{ row_id: string; value: string; status: string }>(
      'SELECT row_id, value, status FROM model_hub_cell WHERE dataset_id = $1 AND column_id = $2 AND row_id = $3 AND NOT deleted',
      [datasetId, recordingColumnId, rowId]);
    expect(cells).toEqual([{ row_id: rowId, value: 'https://127.0.0.1/th9061-recording.wav', status: 'error' }]);
    await testInfo.attach('converted-recording-cell', { body: JSON.stringify(cells), contentType: 'application/json' });
  });

  await test.step('UI: open the seeded dataset and configure a speech prompt', async () => {
    await page.goto(`${DATASET_PAGE}/${datasetId}`, { waitUntil: 'domcontentloaded' });
    // DevelopDataV2.getRowId uses row_id; DataTab/common.js uses column UUIDs as fields.
    await expect(page.locator(`.ag-row[row-id="${rowId}"] [col-id="${textColumnId}"]`))
      .toHaveText([text], { timeout: UI_READY });
    // DevelopDataRightSection.jsx button; RunPrompt.jsx name; modelTypes.jsx radio.
    await page.getByRole('button', { name: 'Run Prompt', exact: true }).click();
    await page.getByPlaceholder('Prompt Name', { exact: true }).fill(promptName);
    await page.getByRole('radio', { name: /^Text-to-Speech/ }).check();
    await expect(page.getByRole('radio', { name: /^Text-to-Speech/ })).toBeChecked({ timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('UI: select the catalog TTS model and explicitly select alloy', async () => {
    // CustomModelDropdown.SearchField is named "Select Model" while closed
    // (the open menu drops the name, and hidden drawers reuse its "Select
    // Option" placeholder), so open it and pick from ShowModelDropdown's
    // MUI menuitems without typing a filter.
    const model = page.getByRole('textbox', { name: 'Select Model', exact: true });
    await model.click();
    await page.getByRole('menuitem', { name: MODEL, exact: true }).click();
    await expect(model).toHaveValue(MODEL, { timeout: UI_READY });
    // FormSearchSelectFieldState is a textbox, NOT a native select; like the
    // model field its label is not associated, so use its "Choose a voice"
    // placeholder. FormSelectMenus renders menuitems. Explicitly select even the
    // default: RunPrompt.jsx's onChange sets voice as well as voiceId.
    const voice = page.getByPlaceholder('Choose a voice', { exact: true });
    await voice.click();
    await page.getByRole('menuitem', { name: VOICE, exact: true }).click();
    await expect(voice).toHaveValue(VOICE, { timeout: UI_READY });
  }, { timeout: UI_READY });

  const previewDialog = page.getByRole('dialog').filter({
    has: page.getByText('Run preview on custom rows', { exact: true }),
  });
  await test.step('UI: enter the audio variable and test the first row', async () => {
    // PromptTTSInput excludes audio from suggestions, but RunPrompt validates
    // against ALL columns. PromptEditor mention.allowedChars excludes closing
    // braces: fill the complete token atomically, then blur, rather than selecting
    // an autocomplete item (or sending Escape to the enclosing unsaved drawer).
    const editor = page.locator('.ql-editor[contenteditable="true"]');
    await expect(editor).toHaveCount(1, { timeout: UI_READY });
    await editor.fill('{{Recording}}');
    await page.getByPlaceholder('Prompt Name', { exact: true }).click();
    await expect(editor).toHaveText('{{Recording}}', { timeout: UI_READY });
    await page.getByRole('button', { name: 'Test', exact: true }).click();
    await expect(previewDialog).toBeVisible({ timeout: UI_READY });
    // RunPreviewModal: First N rows is a radio with descriptive children;
    // numberOfRows is the dialog's sole MUI Select, not a number input. The
    // FormSelectField InputLabel has no labelId, so scope the role to this dialog.
    await previewDialog.getByRole('radio', { name: /^First N rows/ }).check();
    const numberOfRows = previewDialog.getByRole('combobox');
    await numberOfRows.click();
    await page.getByRole('option', { name: '1', exact: true }).click();
    await expect(numberOfRows).toHaveText('1', { timeout: UI_READY });
  }, { timeout: UI_READY });

  const { response, body } = await test.step('UI: run the preview and read Prompt Test Raw', async () => {
    await mockModel.assertReady();
    // Capture the native browser request, never replay or replace this preview.
    const preview = page.waitForResponse(PREVIEW_REQUEST, { timeout: UI_READY });
    await previewDialog.getByRole('button', { name: 'Run', exact: true }).click();
    const response = await preview;
    const body: { result: { responses: string[] } } = await response.json();
    await testInfo.attach('tts-preview-wire', { contentType: 'application/json',
      body: JSON.stringify({ status: response.status(), request: response.request().postDataJSON(), response: body }) });
    // TestRunPrompt.jsx renders Prompt Test and Raw/Markdown tabs in the same
    // parent Box. RawPreview prints result.responses, not the audio-player view.
    const panel = page.getByText('Prompt Test', { exact: true }).locator('..');
    await expect(page.getByText('Prompt Test', { exact: true })).toBeVisible({ timeout: UI_READY });
    await panel.getByRole('tab', { name: 'Raw', exact: true }).click();
    await expect(panel.getByRole('tab', { name: 'Raw', exact: true })).toHaveAttribute('aria-selected', 'true', { timeout: UI_READY });
    await expect(panel.getByText(EXPECTED, { exact: true })).toBeVisible({ timeout: UI_READY });
    return { response, body };
  }, { timeout: UI_READY });

  await test.step('API: the actual one-row TTS preview returns exactly the rejection', async () => {
    expect(response.request().method()).toBe('POST');
    expect(response.status()).toBe(200);
    const sent = response.request().postDataJSON();
    // PreviewRunPromptSerializer accepts dataset_id/name/config/first_n_rows.
    // RunPreviewModal parses its "1" to a number; RunPrompt.onSubmitPreview maps
    // outputFormat to output_format and buildRunPromptConfig sets model_type;
    // workbench/createPrompt/common.js normalizes voiceId to voice_id.
    expect(sent).toMatchObject({ dataset_id: datasetId, name: promptName, first_n_rows: 1,
      config: { model: MODEL, output_format: 'audio', tools: [],
        run_prompt_config: { model_type: 'tts', voice: VOICE, voice_id: VOICE } } });
    expect(sent.row_indices).toBeUndefined();
    // RunPrompt/common.js:replaceVariablesWithFields maps the display name to
    // DataTab/common.js's column UUID field. PromptCards/common.js:getBlocks
    // retains Quill's terminal newline; sanitizeContent preserves nonempty text.
    expect(sent.config.messages.filter((message: { role: string }) => message.role === 'user'))
      .toEqual([{ role: 'user', content: [{ type: 'text', text: `{{${recordingColumnId}}}\n` }] }]);
    expect(body.result.responses).toEqual([EXPECTED]);
  });

  // mock-model-fixtures owns/disposes the request context and PG probe in finally;
  // Playwright owns the browser context. Keep seeded rows for failure diagnosis.
});
