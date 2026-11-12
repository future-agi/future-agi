import { test, expect } from '../../lib/scope-actors';
import { inspectManagedMock, registerMockModel, MOCK_MODEL, MOCK_BASE, MOCK_KEY } from '../../lib/managed-mock';
import { uploadFixture } from '../../lib/upload';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// model_hub/urls.py and ExperimentUpdateV2Serializer; ClassicRerunAction.jsx.
const EXPERIMENTS = '/model-hub/experiments/v2/';
// PromptTemplateViewSet.create_draft and PromptHistoryExecutionViewSet.list.
const PROMPTS = '/model-hub/prompt-templates/';
const VERSIONS = '/model-hub/prompt-history-executions/';
// UploadFileModal.jsx and CreateDatasetFromLocalFileRequestSerializer.
const UPLOAD = '/model-hub/develops/create-dataset-from-local-file/';
const UI_READY = 60_000;
interface Config { id: string; name: string; model: string; prompt_id: string; prompt_version: string }
interface Detail { id: string; snapshot_dataset_id: string; prompt_configs: Config[]; status: string }
interface Cell { id: string; column_id: string; row_id: string; value: string; status: string }

// The provider is a labelled STANDIN. All API, auth, storage and Temporal paths are real.
test('PROMPT-E2E-002: re-run changes create new variants and retain earlier results', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'PROMPT-E2E-002', area: 'prompts',
    userGoal: 'Change a prompt or model on the classic experiment page and keep the earlier results as separate variants',
    steps: [
      'seed published prompts and an evaluated experiment on an uploaded synthetic dataset',
      'open the classic experiment page while the original run may still be executing',
      'open Re-run with changes, select a different published prompt and run a new variant',
      'without reloading, wait for that run to finish and run another variant with a different loopback-backed model',
      'try the same experiment and a foreign prompt from another organization',
    ],
    backendChecks: [
      'the original and both appended configs retain their exact prompt versions and models and produce independently expected output cells',
      'each UI PUT creates exactly one new config and leaves all earlier config and output cell identities and values unchanged',
      'another organization cannot read or append to the experiment and a foreign prompt cannot be appended by the owner',
    ],
    browserChecks: [
      'Re-run with changes is disabled while each appended variant is queued or running and becomes available again when it completes, with no page reload',
      'experiment status reads stay bounded while executions are pending',
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe }, testInfo) => {
  // 3 × ASYNC_JOB + 2 × CDC_VISIBLE + 11 × UI_READY + 60 seconds setup headroom.
  test.setTimeout(1_260_000);
  const receipt = inspectManagedMock();
  const assertStack = async () => expect(inspectManagedMock()).toEqual(receipt);
  await testInfo.attach('loopback-LLM-STANDIN', { contentType: 'application/json', body: JSON.stringify(receipt) });
  const owner = scopeActors.ownerA;
  const foreign = scopeActors.ownerB;
  const model = await registerMockModel(scopeActors.owners[0], scopeProbe, assertStack);
  // Same public registration contract as registerMockModel; second name is also
  // pinned in gateway.e2e.yaml and mock-llm/server.mjs, never a live provider.
  await model.assertReady();
  await owner.api.post('/model-hub/custom_models/create/', {
    model_provider: 'openai', model_name: 'gpt-4o-mini', input_token_cost: 0, output_token_cost: 0,
    config_json: { key: MOCK_KEY, api_base: MOCK_BASE },
  });
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const prefix = `e2e-rerun-${suffix}`;
  const context = await scopeActors.openContext(browser, owner);
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (['localhost', '127.0.0.1'].includes(url.hostname) || ['data:', 'blob:'].includes(url.protocol)) {
      await route.continue();
    } else await route.abort('blockedbyclient');
  });
  const ids: Record<string, unknown> = { organizationId: owner.organizationId, workspaceId: owner.workspaceId };
  const createPrompt = async (actor: typeof owner, name: string, input: string) => {
    const created = await actor.api.post<{ result: { id: string } }>(`${PROMPTS}create-draft/`, {
      name, is_draft: false,
      prompt_config: [{ messages: [{ role: 'user', content: input }], configuration: { model: MOCK_MODEL, temperature: 0, max_tokens: 128, tools: [] } }],
    });
    const listed = await actor.api.get<{ results: { id: string; is_draft: boolean; template_version: string }[] }>(VERSIONS, { template_id: created.result.id });
    expect(listed.results).toHaveLength(1);
    expect(listed.results[0].is_draft).toBe(false);
    return { id: created.result.id, version: listed.results[0].id, name, input };
  };
  const original = await createPrompt(owner, `${prefix}-original`, `${prefix}-first`);
  const changed = await createPrompt(owner, `${prefix}-changed`, `${prefix}-second`);
  const foreignPrompt = await createPrompt(foreign, `${prefix}-foreign`, `${prefix}-foreign-input`);
  const evaluator = await owner.api.post<{ result: { id: string } }>('/model-hub/eval-templates/create-v2/', {
    name: `${prefix}-judge`, eval_type: 'llm', model: MOCK_MODEL, output_type: 'deterministic',
    instructions: `Reply with exactly this JSON: {"result":"Pass","explanation":"${prefix}-verdict"}\nMapped input: {{output}}`,
    pass_threshold: 0.5, choice_scores: { Pass: 1, Fail: 0 }, check_internet: false,
    error_localizer_enabled: false, template_format: 'mustache',
  });
  ids.prompts = { original, changed, foreignPrompt };
  ids.evaluatorId = evaluator.result.id;
  await testInfo.attach('seeded-prompts', { body: JSON.stringify(ids), contentType: 'application/json' });
  await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
  // DevelopView.newButton and UploadFileModal use the current launch copy.
  await page.getByText('New dataset', { exact: true }).click();
  await page.getByText('Upload a file', { exact: true }).click();
  const upload = page.getByRole('dialog').filter({ has: page.getByText('Upload a file', { exact: true }) });
  await uploadFixture(upload.locator('input[type="file"]'), 'catalog-regions.csv');
  await upload.getByPlaceholder('Dataset name').fill(`${prefix}-data`);
  const uploaded = page.waitForResponse(r => new URL(r.url()).pathname === UPLOAD && r.request().method() === 'POST', { timeout: UI_READY });
  await upload.getByRole('button', { name: 'Import 3 rows', exact: true }).click();
  const response = await uploaded;
  expect(response.status()).toBe(200);
  const datasetId = (await response.json()).result.dataset_id as string;
  ids.datasetId = datasetId;
  await expect.poll(async () => (await scopeProbe.pg<{ n: string }>('SELECT count(*) AS n FROM model_hub_row WHERE dataset_id=$1 AND NOT deleted', [datasetId]))[0].n, POLL.ASYNC_JOB).toBe('3');
  await model.assertReady();
  const created = await owner.api.post<{ result: string }>(EXPERIMENTS, {
    name: `${prefix}-experiment`, dataset_id: datasetId, experiment_type: 'llm',
    prompt_config: [{ name: 'Original variant', prompt_id: original.id, prompt_version: original.version, model: MOCK_MODEL, model_params: { temperature: 0, max_tokens: 128 } }],
    user_eval_metrics: [{ name: 'output-check', template_id: evaluator.result.id, model: MOCK_MODEL, config: { mapping: { output: 'output' } } }],
  });
  const experimentId = /experiment_id=([0-9a-f-]{36})/.exec(created.result)?.[1];
  expect(experimentId).toBeTruthy();
  const path = `${EXPERIMENTS}${experimentId}/`;
  ids.experimentId = experimentId;
  ids.prompts = { original, changed, foreignPrompt };
  await testInfo.attach('seeded-identities', { body: JSON.stringify(ids), contentType: 'application/json' });
  const detail = async () => (await owner.api.get<{ result: Detail }>(path)).result;
  const outputCells = async (configId: string) => scopeProbe.pg<Cell>(
    `SELECT c.id,c.column_id,c.row_id,c.value,c.status FROM model_hub_cell c
     JOIN model_hub_column col ON col.id=c.column_id
     JOIN model_hub_experimentpromptconfig epc ON col.source_id=epc.experiment_dataset_id::text
     WHERE epc.id=$1 AND col.source='experiment' AND NOT col.deleted AND NOT c.deleted ORDER BY c.id`, [configId]);
  const retainedCells = async (configId: string) => scopeProbe.pg<Cell>(
    `SELECT c.id,c.column_id,c.row_id,c.value,c.status FROM model_hub_cell c
     JOIN model_hub_column col ON col.id=c.column_id
     JOIN model_hub_experimentpromptconfig epc ON col.source_id=epc.experiment_dataset_id::text
       OR (col.source IN ('experiment_evaluation','experiment_evaluation_tags') AND col.source_id LIKE epc.experiment_dataset_id::text || '-%')
     WHERE epc.id=$1 AND NOT col.deleted AND NOT c.deleted ORDER BY c.id`, [configId]);
  const awaitOutputs = async (config: Config, input: string) => {
    // run_prompt.py:625 normalizes text into typed content; the loopback
    // provider in mock-llm/server.mjs echoes that exact JSON representation.
    await expect.poll(async () => (await outputCells(config.id)).map(c => c.value), POLL.ASYNC_JOB)
      .toEqual(Array(3).fill(`echo: ${JSON.stringify([{ type: 'text', text: input }])}`));
    await expect.poll(async () => (await detail()).status, POLL.ASYNC_JOB).toBe('Completed');
    const cells = await retainedCells(config.id);
    expect(cells.filter(cell => cell.status === 'error')).toEqual([]);
    return cells;
  };
  const first = (await detail()).prompt_configs;
  expect(first).toHaveLength(1);
  // The page opens before the original run is known to be complete. Every
  // later state change must reach it through its own status reads.
  const statusReads: number[] = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (request.method() === 'GET' && url.pathname === path && !url.search) statusReads.push(Date.now());
  });
  const opened = Date.now();
  await page.goto(`/dashboard/develop/experiment/${experimentId}/data?datasetId=${datasetId}`, { waitUntil: 'domcontentloaded' });
  const rerun = page.getByRole('button', { name: 'Re-run with changes', exact: true });
  await expect(rerun).toBeVisible({ timeout: UI_READY });
  // A reload or document replacement discards this marker.
  await page.evaluate(() => { (window as unknown as { __rerunDocument?: boolean }).__rerunDocument = true; });
  const originals = new Map([[first[0].id, await awaitOutputs(first[0], original.input)]]);
  // setup_prompt_v2 persists effective version defaults during execution;
  // preserve the completed run, not its pre-execution configuration.
  let previousConfigs = (await detail()).prompt_configs;
  for (const change of ['prompt', 'model']) {
    await test.step(`UI and persisted state: append ${change} variant without a reload`, async () => {
      await expect(rerun).toBeEnabled({ timeout: UI_READY });
      await rerun.click();
      const dialog = page.getByRole('dialog', { name: 'Re-run with changes', exact: true });
      await dialog.getByLabel('Variant name', { exact: true }).fill(`${prefix}-${change}-variant`);
      if (change === 'prompt') {
        await dialog.getByRole('combobox', { name: 'Prompt', exact: true }).fill(changed.name);
        await page.getByRole('option', { name: changed.name, exact: true }).click();
        await dialog.getByRole('combobox', { name: 'Published version', exact: true }).click();
        await page.getByRole('option', { name: 'v1', exact: true }).click();
      } else {
        await dialog.getByText(MOCK_MODEL, { exact: true }).first().click();
        // ShowModelDropdown loads catalog pages; search instead of assuming
        // this model is included in the initial page.
        await page.getByPlaceholder('Select model', { exact: true }).fill('gpt-4o-mini');
        await page.getByText('gpt-4o-mini', { exact: true }).last().click();
      }
      await model.assertReady();
      const submitted = page.waitForResponse(r => new URL(r.url()).pathname === path && r.request().method() === 'PUT', { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Run new variant', exact: true }).click();
      const saved = await submitted;
      expect(saved.status()).toBe(200);
      const body = saved.request().postDataJSON();
      expect(body.append_only).toBe(true);
      expect(body.prompt_config).toHaveLength(1);
      expect(body.prompt_config[0]).not.toHaveProperty('id');
      // The append response reports the admitted execution; the open page
      // follows it from pending to completion.
      expect(['Queued', 'Running']).toContain((await saved.json()).result.status);
      await expect(rerun).toBeDisabled();
      const next = (await detail()).prompt_configs;
      expect(next).toHaveLength(previousConfigs.length + 1);
      for (const prior of previousConfigs) expect(next.find(c => c.id === prior.id)).toEqual(prior);
      const child = next.find(c => !previousConfigs.some(p => p.id === c.id))!;
      expect(child.prompt_id).toBe(change === 'prompt' ? changed.id : original.id);
      expect(child.prompt_version).toBe(change === 'prompt' ? changed.version : original.version);
      expect(child.model).toBe(change === 'prompt' ? MOCK_MODEL : 'gpt-4o-mini');
      const cells = await awaitOutputs(child, change === 'prompt' ? changed.input : original.input);
      for (const [id, kept] of originals) expect(await retainedCells(id)).toEqual(kept);
      originals.set(child.id, cells);
      const completed = (await detail()).prompt_configs;
      for (const prior of previousConfigs) expect(completed.find(c => c.id === prior.id)).toEqual(prior);
      previousConfigs = completed;
      await expect(rerun).toBeEnabled({ timeout: UI_READY });
      await testInfo.attach(`${change}-variant`, { body: JSON.stringify({ child, cells, submitted: body }), contentType: 'application/json' });
    });
  }
  await test.step('UI: one document and bounded status reads', async () => {
    expect(await page.evaluate(() => (window as unknown as { __rerunDocument?: boolean }).__rerunDocument)).toBe(true);
    const elapsed = Date.now() - opened;
    await testInfo.attach('status-reads', { body: JSON.stringify({ count: statusReads.length, elapsedMs: elapsed, offsetsMs: statusReads.map(at => at - opened) }), contentType: 'application/json' });
    // The status read backs off from 2 seconds; a refetch loop would make hundreds.
    expect(statusReads.length).toBeLessThanOrEqual(Math.ceil(elapsed / 2_000) + 10);
  });
  await test.step('API: tenant refusal paired with owner readback', async () => {
    const foreignRead = await scopeActors.send(foreign, 'GET', path);
    expect(foreignRead.status).toBe(404);
    const body = { append_only: true, prompt_config: [{ name: 'Refused variant', prompt_id: original.id, prompt_version: original.version, model: MOCK_MODEL }] };
    expect((await scopeActors.send(foreign, 'PUT', path, body)).status).toBe(404);
    expect((await scopeActors.send(owner, 'PUT', path, { append_only: true, prompt_config: [{ ...body.prompt_config[0], prompt_id: foreignPrompt.id, prompt_version: foreignPrompt.version }] })).status).toBe(400);
    expect((await detail()).prompt_configs).toEqual(previousConfigs);
    for (const [id, kept] of originals) expect(await retainedCells(id)).toEqual(kept);
  });
  await testInfo.attach('scope-evidence', { body: JSON.stringify(scopeActors.evidence()), contentType: 'application/json' });
  await context.close();
});
