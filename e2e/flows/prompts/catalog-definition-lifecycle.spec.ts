import { test, expect } from '../../lib/mock-model-fixtures';
import { MOCK_MODEL as MODEL, MOCK_BASE as GATEWAY } from '../../lib/managed-mock';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// WorkbenchProvider.jsx getSaveTemplatePayload/getRunTemplatePayload;
// EvaluationDrawer.jsx:657 preserves is_run:true for both Add and Update.
const PROMPTS = '/model-hub/prompt-templates/';
const METRICS = '/tracer/dashboard/metrics/';
const VALUES = '/tracer/dashboard/filter_values/';
const UI_READY = 60_000;
// WorkbenchProvider: 10s socket fallback, then native 120s status poll.
const PROMPT_RUN = 130_000;

interface EvalResult { status: string; output: unknown; value: unknown;
  meta: { reason: string; failure: boolean; token_count: number } }
interface VersionRow { id: string; original_template_id: string; template_version: string; updated_at: Date;
  is_draft: boolean; output: string[] | null; metadata: unknown[];
  prompt_config_snapshot: { messages: { role: string; content: { type: string; text: string }[] }[];
    configuration: { model: string; tools: unknown[] } };
  evaluation_results: Record<string, { name: string; results: EvalResult[] }> }
interface ConfigRow { id: string; name: string; prompt_template_id: string; eval_template_id: string;
  mapping: { output: string }; config: { run_config: { model: string } }; deleted: boolean;
  organization_id: string; workspace_id: string; email: string }
interface ConfigPage { result: { template_id: string; evaluation_configs: {
  id: string; name: string; mapping: { output: string }; run_config: { check_internet: boolean } }[] } }
interface UpdateBody { id: string; name: string; user_eval_id?: string;
  mapping: { output: string }; model: string; is_run: boolean; version_to_run: string[];
  error_localizer: boolean; config: { params: object; run_config: {
    model: string; check_internet: boolean; tools: Record<string, unknown>; knowledge_bases: unknown[];
    error_localizer_enabled: boolean; choice_scores: Record<string, number> } } }

test('PROMPT-E2E-001: a mock-run prompt exposes its current evaluation configuration lifecycle', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'PROMPT-E2E-001', area: 'prompts',
    userGoal: 'Run a synthetic prompt through the local mock, then discover, edit and remove its current evaluation configuration through supported UI',
    steps: ['create and name a prompt from scratch and select the local mock model',
      'Run Prompt and wait for real stored output before opening the Evaluation tab',
      'Add Evaluation mapped to model_output and inspect its current catalog choices',
      'edit mapping to model_input with the same binding ID and name and wait for completion',
      'delete the binding and reload to verify current discovery excludes it'],
    backendChecks: [
      'real mock output and the prompt evaluation binding retain exact parent, actor scope and model with terminal stored results',
      'current prompt catalog identifies the exact binding and its two configured choices',
      'mapping edit preserves binding ID/name, completes, and deletion removes it from UI and current discovery',
    ],
  }),
}, async ({ page, actor, probe, mockModel }, testInfo) => {
  test.setTimeout(720_000); // 130 Run Prompt + 2x60 native jobs + 7x60 UI + 50 headroom.
  page.setDefaultTimeout(UI_READY);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const promptName = `e2e-prompt1-${suffix}`;
  const bindingName = `${promptName}-binding`;
  const evaluatorName = `${promptName}-judge`;
  const input = `${promptName}-input`;
  // Captured UI autosave: Quill preserves its terminating paragraph newline.
  const serializedInput = `${input}\n`;
  const alpha = `Alpha-${suffix}`;
  const beta = `Beta-${suffix}`;
  const verdict = `${promptName}-verdict`;
  const choices = { [alpha]: 1, [beta]: 0 };
  const ids: Record<string, string> = { organizationId: actor.organizationId,
    workspaceId: actor.workspaceId, modelId: mockModel.id, promptName, bindingName, input, alpha, beta, verdict };
  const requests: { path: string; body: unknown }[] = [];
  const socketFrames: unknown[] = [];
  const assertMockOnly = mockModel.assertReady;
  const readVersion = async () => {
    const versions = await probe.pg<VersionRow>(
      'SELECT * FROM model_hub_promptversion WHERE original_template_id=$1 AND template_version=$2 AND deleted=false',
      [ids.promptId, 'v1']);
    expect(versions).toHaveLength(1);
    return versions[0];
  };
  const readConfig = async () => probe.pg<ConfigRow>(
    `SELECT c.id,c.name,c.prompt_template_id,c.eval_template_id,c.mapping,c.config,c.deleted,
       p.organization_id,p.workspace_id,u.email FROM model_hub_promptevalconfig c
       JOIN model_hub_prompttemplate p ON p.id=c.prompt_template_id
       JOIN accounts_user u ON u.id=c.user_id WHERE c.id=$1`, [ids.bindingId]);
  const terminalEval = async (phase: string, previousUpdatedAt: Date) => {
    try {
      await expect.poll(async () => {
        const version = await readVersion();
        const results = version.evaluation_results[ids.bindingId]?.results;
        if (results?.some(r => r.status === 'Error')) throw new Error(JSON.stringify(results));
        // _update_eval_result_atomically updates updated_at only on a result
        // write. Initial Running setup does not; reject a previous Completed.
        return version.updated_at > previousUpdatedAt ? results?.map(r => r.status) : ['Previous result'];
      }, POLL.ASYNC_JOB).toEqual(['Completed']); // native thread pool; not a Temporal claim.
      const stored = (await readVersion()).evaluation_results[ids.bindingId].results;
      expect(stored[0].meta.failure).toBe(false);
      expect(stored[0].meta.reason).toBe(verdict);
      // Captured native categorical result; CustomPromptEvaluator -> format_output.
      expect(stored[0].value).toEqual({ score: 1, choice: alpha });
      expect(stored[0].output).toBe('choices');
      expect(stored[0].meta.token_count).toBe(14); // non-streaming judge, not Run Prompt.
      const response = await actor.api.get<{ result: { v1: { eval_output: Record<string, EvalResult[]> } } }>(
        `${PROMPTS}${ids.promptId}/evaluations/`, { versions: JSON.stringify(['v1']) });
      expect(response.result.v1.eval_output[ids.bindingId]).toEqual(stored);
      await testInfo.attach(`${phase}-terminal-results`, { contentType: 'application/json',
        body: JSON.stringify({ bindingId: ids.bindingId, stored, api: response.result.v1 }) });
    } catch (error) {
      await testInfo.attach(`${phase}-EXECUTION-FAILURE`, { contentType: 'application/json',
        body: JSON.stringify({ ids, error: String(error), version: await readVersion() }) });
      throw error; // Never greenwash Running/Error as successful prompt execution.
    }
  };

  page.on('request', r => {
    const path = new URL(r.url()).pathname;
    if (path.startsWith(PROMPTS) && ['POST', 'DELETE'].includes(r.method())) {
      requests.push({ path, body: r.method() === 'POST' ? r.postDataJSON() : new URL(r.url()).search });
    }
  });
  page.on('websocket', ws => {
    if (new URL(ws.url()).pathname !== '/ws/prompt-stream/') return;
    // Do not attach the socket URL: its query includes the auth token.
    ws.on('framesent', frame => socketFrames.push(JSON.parse(String(frame.payload))));
  });
  // WorkbenchProvider:967 uses the actual autosave payload for HTTP fallback.
  // Do not force that transport. WS references the stored version checked before Run.
  await page.route('**/run_template/', async route => {
    const body = route.request().postDataJSON();
    if (body.is_run) {
      const safe = new URL(route.request().url()).origin === new URL(E2E.apiUrl).origin &&
        new URL(route.request().url()).pathname === `${PROMPTS}${ids.promptId}/run_template/` &&
        body.is_run === 'prompt' && body.version === 'v1' && body.prompt_config?.length === 1 &&
        body.prompt_config[0].configuration?.model === MODEL &&
        (body.prompt_config[0].configuration.tools?.length ?? 0) === 0 &&
        body.evaluation_configs?.length === 0;
      if (!safe) { await route.abort('blockedbyclient'); throw new Error('STOP: unsafe Run Prompt wire'); }
      await assertMockOnly();
    }
    await route.continue();
  });
  // Safety veto only: never rewrite a request or manufacture an API response.
  // Checking the model in a response would be too late to prevent paid dispatch.
  await page.route('**/update-evaluation-configs/', async route => {
    const body = route.request().postDataJSON() as UpdateBody;
    const runtime = body.config?.run_config;
    const safe = new URL(route.request().url()).origin === new URL(E2E.apiUrl).origin &&
      new URL(route.request().url()).pathname === `${PROMPTS}${ids.promptId}/update-evaluation-configs/` &&
      body.id === ids.evaluatorId && body.model === MODEL && runtime?.model === MODEL &&
      body.error_localizer === false && body.is_run === true &&
      runtime.check_internet === false && runtime.error_localizer_enabled === false &&
      Object.keys(runtime.tools ?? {}).length === 0 && (runtime.knowledge_bases?.length ?? 0) === 0;
    if (!safe) {
      await testInfo.attach('BLOCKED-unsafe-evaluation-dispatch', { contentType: 'application/json', body: JSON.stringify(body) });
      await route.abort('blockedbyclient');
      return;
    }
    await assertMockOnly(); // Recheck at the dispatch boundary, not only before the click.
    await route.continue();
  });
  try {
    await test.step('preflight and public prerequisites: only the local deterministic mock', async () => {
      await assertMockOnly();
      // EvalTemplateCreateV2RequestSerializer; mock echoes literal verdict JSON.
      // Keep the mapped data outside the JSON string, including JSON-shaped output.
      const template = await actor.api.post<{ result: { id: string } }>('/model-hub/eval-templates/create-v2/', {
        name: evaluatorName, eval_type: 'llm', model: MODEL, output_type: 'deterministic',
        instructions: `Reply with exactly this JSON: {"result":"${alpha}","explanation":"${verdict}"}\nMapped input: {{output}}`,
        pass_threshold: 0.5, choice_scores: choices, check_internet: false,
        error_localizer_enabled: false, template_format: 'mustache',
      });
      ids.evaluatorId = template.result.id;
      await testInfo.attach('prerequisites', { contentType: 'application/json', body: JSON.stringify({ ids,
        model: MODEL, route: GATEWAY }) });
    });

    await test.step('UI: create, name and configure the prompt from scratch', async () => {
      await page.goto('/dashboard/workbench/all', { waitUntil: 'domcontentloaded' });
      await page.getByRole('button', { name: 'Create prompt', exact: true }).first().click();
      const draft = page.waitForResponse(r => new URL(r.url()).pathname === `${PROMPTS}create-draft/` &&
        r.request().method() === 'POST', { timeout: UI_READY });
      await page.getByText('Start from scratch', { exact: true }).click();
      const response = await draft;
      expect(response.status()).toBe(200);
      ids.promptId = (await response.json()).result.root_template;
      await expect(page).toHaveURL(new RegExp(`/dashboard/workbench/create/${ids.promptId}`), { timeout: UI_READY });
      // PromptActions.jsx:518: unnamed edit IconButton is the first button in
      // the All Prompts breadcrumb container for a newly created/unlabelled prompt.
      await page.getByRole('link', { name: 'All Prompts', exact: true }).locator('..').getByRole('button').first().click();
      await page.locator('input:focus').fill(promptName);
      const named = page.waitForResponse(r => new URL(r.url()).pathname === `${PROMPTS}${ids.promptId}/save-name/`,
        { timeout: UI_READY });
      await page.locator('input:focus').press('Enter');
      expect((await named).status()).toBe(200);
      // PromptCard/PromptEditor uses Quill. The default cards are system, user.
      await expect(page.locator('.ql-editor[contenteditable="true"]')).toHaveCount(2, { timeout: UI_READY });
      await page.locator('.ql-editor[contenteditable="true"]').nth(1).fill(input);
      await page.getByText('Select Model', { exact: true }).click();
      await page.getByPlaceholder('Select model', { exact: true }).fill(MODEL);
      await page.getByText(MODEL, { exact: true }).click();
      await expect.poll(async () => {
        const v = await readVersion();
        return { model: v.prompt_config_snapshot.configuration.model,
          user: v.prompt_config_snapshot.messages.filter(m => m.role === 'user') };
      }, POLL.ASYNC_JOB).toEqual({ model: MODEL,
        user: [{ role: 'user', content: [{ type: 'text', text: serializedInput }] }] });
    });

    await test.step('UI: Run Prompt produces real stored output and enables normal Evaluation tab', async () => {
      await assertMockOnly();
      expect((await readVersion()).prompt_config_snapshot.configuration.model).toBe(MODEL);
      await page.getByRole('button', { name: 'Run Prompt', exact: true }).click();
      // replace_variables preserves content blocks; mock server JSON-stringifies them.
      const output = `echo: ${JSON.stringify([{ type: 'text', text: serializedInput }])}`;
      await expect.poll(async () => {
        const v = await readVersion();
        return { output: v.output, is_draft: v.is_draft };
      }, { timeout: PROMPT_RUN, intervals: [1000, 2500] }).toEqual({ output: [output], is_draft: false });
      const version = await readVersion();
      ids.versionId = version.id;
      expect(version.metadata).toHaveLength(1);
      expect(Object.keys(version.metadata[0] as object).length).toBeGreaterThan(0);
      const status = await actor.api.get<{ result: { status: string; executions_result: { output: string[] } } }>(
        `${PROMPTS}${ids.promptId}/get-run-status/`, { template_version: 'v1' });
      expect(status.result.status).toBe('completed');
      expect(status.result.executions_result.output).toEqual([output]);
      await expect(page.getByText(output, { exact: true }).first()).toBeVisible({ timeout: UI_READY });
      await testInfo.attach('real-prompt-output', { contentType: 'application/json', body: JSON.stringify({ ids, version, status }) });
      await page.getByRole('tab', { name: 'Evaluation', exact: true }).click();
      await page.getByRole('button', { name: 'Add Evaluations', exact: true }).click();
      // Wait for the actual drawer button: .last() can bind to the covered
      // page button while the saved-eval list is still loading.
      await page.getByText('All Evaluations', { exact: true }).locator('../..')
        .getByRole('button', { name: 'Add Evaluations', exact: true }).click();
      await page.getByPlaceholder('Search evaluations...').fill(evaluatorName);
      // EvalPickerList.jsx:800: row click expands details; its Add button
      // selects the evaluator and opens Configure Evaluation.
      await page.getByRole('row').filter({ hasText: evaluatorName }).getByRole('button', { name: 'Add', exact: true }).click();
      await page.getByPlaceholder('e.g. toxicity-check, my-custom-eval').fill(bindingName);
      await page.getByPlaceholder('Select column', { exact: true }).click();
      await page.getByText('model_output', { exact: true }).click();
    });

    await test.step('backend check 1: real output, scoped binding and completed Add', async () => {
      await assertMockOnly();
      // EvalPickerConfigFull displays the actual model; its saved config is checked below.
      await expect(page.getByText(MODEL, { exact: true }).first()).toBeVisible({ timeout: UI_READY });
      const previousUpdatedAt = (await readVersion()).updated_at;
      const saved = page.waitForResponse(r => new URL(r.url()).pathname === `${PROMPTS}${ids.promptId}/update-evaluation-configs/` &&
        r.request().method() === 'POST', { timeout: UI_READY });
      await page.getByRole('button', { name: 'Add Evaluation', exact: true }).click();
      const response = await saved;
      expect(response.status()).toBe(200);
      const body = response.request().postDataJSON() as UpdateBody;
      expect(body).toMatchObject({ id: ids.evaluatorId, name: bindingName, model: MODEL,
        mapping: { output: 'output_prompt' }, is_run: true, version_to_run: ['v1'], error_localizer: false,
        config: { params: {}, run_config: { model: MODEL, check_internet: false,
          error_localizer_enabled: false, choice_scores: choices } } });
      expect(body.user_eval_id).toBeUndefined();
      ids.bindingId = (await response.json()).result.prompt_eval_config_id;
      const rows = await readConfig();
      expect(rows).toHaveLength(1);
      expect(rows[0]).toMatchObject({ id: ids.bindingId, name: bindingName, prompt_template_id: ids.promptId,
        eval_template_id: ids.evaluatorId, organization_id: actor.organizationId, workspace_id: actor.workspaceId,
        email: actor.email, deleted: false, mapping: { output: 'output_prompt' } });
      expect(rows[0].config.run_config.model).toBe(MODEL);
      await terminalEval('add', previousUpdatedAt);
    });

    await test.step('backend check 2: exact current prompt binding and configured choices', async () => {
      const configs = await actor.api.get<ConfigPage>(`${PROMPTS}${ids.promptId}/evaluation-configs/`);
      expect(configs.result.template_id).toBe(ids.promptId);
      expect(configs.result.evaluation_configs.map(c => c.id)).toEqual([ids.bindingId]);
      ids.propertyId = `eval_config:${ids.bindingId}`;
      const catalog = await actor.api.post<{ result: { metrics: { property_id: string; name: string; display_name: string }[] } }>(METRICS,
        { source: 'prompts', per_eval_config: true, cursor_mode: true, category: 'eval_metric', search: bindingName, page_size: 25 });
      // source_adapters._eval_definition: name is UUID, display_name is binding name.
      expect(catalog.result.metrics.map(m => ({ property_id: m.property_id, name: m.name, display_name: m.display_name })))
        .toEqual([{ property_id: ids.propertyId, name: ids.bindingId, display_name: bindingName }]);
      const values = await actor.api.post<{ result: { values: { value: string; label: string }[] } }>(VALUES,
        { property_id: ids.propertyId, source: 'prompts', page_size: 25 });
      expect(values.result.values).toEqual([alpha, beta].map(value => ({ value, label: value })));
      await expect(page.getByText(bindingName, { exact: true }).first()).toBeVisible({ timeout: UI_READY });
      await testInfo.attach('current-catalog', { contentType: 'application/json', body: JSON.stringify({ ids, configs, catalog, values }) });
    });

    await test.step('backend check 3: same binding mapping edit, terminal Update, deletion/current discovery', async () => {
      await page.getByLabel('Edit mapping & config', { exact: true }).click();
      await expect(page.getByPlaceholder('e.g. toxicity-check, my-custom-eval')).toBeDisabled({ timeout: UI_READY });
      await expect(page.getByPlaceholder('e.g. toxicity-check, my-custom-eval')).toHaveValue(bindingName, { timeout: UI_READY });
      await page.getByPlaceholder('Select column', { exact: true }).click();
      await page.getByText('model_input', { exact: true }).click();
      await assertMockOnly();
      await expect(page.getByText(MODEL, { exact: true }).first()).toBeVisible({ timeout: UI_READY });
      const previousUpdatedAt = (await readVersion()).updated_at;
      const saved = page.waitForResponse(r => new URL(r.url()).pathname === `${PROMPTS}${ids.promptId}/update-evaluation-configs/` &&
        r.request().method() === 'POST', { timeout: UI_READY });
      await page.getByRole('button', { name: 'Update Evaluation', exact: true }).click();
      const response = await saved;
      expect(response.status()).toBe(200);
      expect(response.request().postDataJSON()).toMatchObject({ user_eval_id: ids.bindingId,
        id: ids.evaluatorId, name: bindingName, mapping: { output: 'input_prompt' }, model: MODEL,
        is_run: true, version_to_run: ['v1'], config: { run_config: { model: MODEL, check_internet: false } } });
      expect((await response.json()).result.prompt_eval_config_id).toBe(ids.bindingId);
      expect((await readConfig())[0]).toMatchObject({ id: ids.bindingId, name: bindingName, mapping: { output: 'input_prompt' } });
      expect((await readConfig())[0].config.run_config.model).toBe(MODEL);
      await terminalEval('update', previousUpdatedAt);
      const config = await actor.api.get<ConfigPage>(`${PROMPTS}${ids.promptId}/evaluation-configs/`);
      expect(config.result.evaluation_configs).toHaveLength(1);
      expect(config.result.evaluation_configs[0]).toMatchObject({ id: ids.bindingId, name: bindingName,
        // eval_list.build_run_config_view projects its allowlist, excluding model.
        // The model is asserted in the actual wire and stored binding above.
        mapping: { output: 'input_prompt' }, run_config: { check_internet: false } });
      const values = await actor.api.post<{ result: { values: { value: string; label: string }[] } }>(VALUES,
        { property_id: ids.propertyId, source: 'prompts', page_size: 25 });
      expect(values.result.values).toEqual([alpha, beta].map(value => ({ value, label: value })));
      await page.getByLabel('Edit mapping & config', { exact: true }).click();
      await expect(page.getByPlaceholder('Select column', { exact: true })).toHaveValue('model_input', { timeout: UI_READY });
      // EvalPickerConfigFull.jsx:1231: first header button is Back; in edit
      // mode EvalPickerDrawer.handleBackToList closes to SavedEvalsList.
      await page.locator('.MuiDrawer-paper:visible')
        .filter({ has: page.getByPlaceholder('e.g. toxicity-check, my-custom-eval') })
        .getByText(evaluatorName, { exact: true }).locator('..').getByRole('button').first().click();
      await page.getByLabel('Delete', { exact: true }).click();
      await expect(page.getByText('Delete this evaluation and its results?', { exact: true })).toBeVisible({ timeout: UI_READY });
      const removed = page.waitForResponse(r => new URL(r.url()).pathname === `${PROMPTS}${ids.promptId}/delete-evaluation-config/` &&
        r.request().method() === 'DELETE', { timeout: UI_READY });
      await page.getByRole('button', { name: 'Delete', exact: true }).click();
      const responseDelete = await removed;
      expect(responseDelete.status()).toBe(200);
      expect(new URL(responseDelete.url()).searchParams.get('id')).toBe(ids.bindingId);
      expect((await readConfig())[0].deleted).toBe(true);
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.getByRole('button', { name: 'Add Evaluations', exact: true }).click();
      await expect(page.getByText(bindingName, { exact: true })).toHaveCount(0, { timeout: UI_READY });
      const remaining = await actor.api.get<ConfigPage>(`${PROMPTS}${ids.promptId}/evaluation-configs/`);
      expect(remaining.result.evaluation_configs).toEqual([]);
      const catalog = await actor.api.post<{ result: { metrics: unknown[] } }>(METRICS,
        { source: 'prompts', per_eval_config: true, cursor_mode: true, category: 'eval_metric', search: bindingName, page_size: 25 });
      expect(catalog.result.metrics).toEqual([]);
      const removedValues = await actor.api.post<{ result: { values: unknown[] } }>(VALUES,
        { property_id: ids.propertyId, source: 'prompts', page_size: 25 });
      expect(removedValues.result.values).toEqual([]);
      await testInfo.attach('deleted-current-discovery', { contentType: 'application/json',
        body: JSON.stringify({ ids, configBeforeDeletion: config, remaining, catalog, removedValues }) });
    });
  } finally {
    await testInfo.attach('prompt-lifecycle-ids-and-wire', { contentType: 'application/json',
      body: JSON.stringify({ ids, requests, socketFrames }) });
    if (ids.promptId) {
      await testInfo.attach('final-persisted-prompt-version', { contentType: 'application/json',
        body: JSON.stringify(await readVersion()) });
    }
  }
});
