import { test, expect } from '../../lib/scope-actors';
import { inspectManagedMock, registerMockModel, MOCK_MODEL } from '../../lib/managed-mock';
import { uploadFixture } from '../../lib/upload';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';
import { pauseAdmissionService } from './experiment-admission-outage';

// model_hub/urls.py and ExperimentCreateV2Serializer.
const EXPERIMENTS = '/model-hub/experiments/v2/';
// PromptTemplateViewSet.create_draft and PromptHistoryExecutionViewSet.list.
const PROMPTS = '/model-hub/prompt-templates/';
const VERSIONS = '/model-hub/prompt-history-executions/';
// UploadFileModal.jsx and CreateDatasetFromLocalFileRequestSerializer.
const UPLOAD = '/model-hub/develops/create-dataset-from-local-file/';
const UI_READY = 60_000;
interface Config { id: string; prompt_id: string; prompt_version: string; model: string }
interface Detail { id: string; status: string; snapshot_dataset_id: string; prompt_configs: Config[] }

test('PROMPT-E2E-004: create a prompt experiment and retry without duplicating execution', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'PROMPT-E2E-004', area: 'prompts',
    userGoal: 'Create a prompt experiment from its public wizard and safely retry the identical submission',
    steps: [
      'upload a synthetic dataset and seed a published prompt and evaluator through public APIs',
      'open the prompt-prefilled experiment wizard, select an evaluation group and click Run experiment',
      'in the isolated admission-outage run, discard and reopen the saved draft, restore Temporal and retry unchanged',
      'read the classic experiment page and exact persisted prompt output',
      'retry the captured creation request and verify the original experiment and output are retained',
      'try the owner dataset, experiment and prompt from another organization',
    ],
    backendChecks: [
      'the browser POST persists the exact selected prompt version, model and group evaluator and three expected output cells',
      'the isolated outage produces a real 503 with committed pending intent; reopening restores the draft and an identical browser retry admits that same experiment',
      'replaying the identical POST returns the same experiment, snapshot, configuration and output identities without another execution',
      'the other organization cannot create against the owner dataset, read the experiment or inject its prompt into the owner experiment',
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe }, testInfo) => {
  // 2 ASYNC_JOB + 17 UI_READY + 120s setup/fault-control headroom; no CDC reads.
  test.setTimeout(1_260_000);
  const receipt = inspectManagedMock();
  await testInfo.attach('loopback-LLM-STANDIN', { body: JSON.stringify(receipt), contentType: 'application/json' });
  const owner = scopeActors.ownerA;
  const foreign = scopeActors.ownerB;
  const model = await registerMockModel(scopeActors.owners[0], scopeProbe, async () => expect(inspectManagedMock()).toEqual(receipt));
  const prefix = `e2e-create-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const createPrompt = async (actor: typeof owner, name: string) => {
    const result = await actor.api.post<{ result: { id: string } }>(`${PROMPTS}create-draft/`, {
      name, is_draft: false,
      prompt_config: [{ messages: [{ role: 'user', content: `${name}-input` }], configuration: { model: MOCK_MODEL, temperature: 0, max_tokens: 128, tools: [] } }],
    });
    const versions = await actor.api.get<{ results: { id: string; is_draft: boolean }[] }>(VERSIONS, { template_id: result.result.id });
    expect(versions.results).toHaveLength(1);
    expect(versions.results[0].is_draft).toBe(false);
    return { id: result.result.id, version: versions.results[0].id, name };
  };
  const prompt = await createPrompt(owner, `${prefix}-prompt`);
  const foreignPrompt = await createPrompt(foreign, `${prefix}-foreign`);
  // EvalTemplateCreateV2: same loopback-backed contract as experiment-rerun.spec.ts.
  const evaluator = await owner.api.post<{ result: { id: string } }>('/model-hub/eval-templates/create-v2/', {
    name: `${prefix}-judge`, eval_type: 'llm', model: MOCK_MODEL, output_type: 'deterministic',
    instructions: `Reply with exactly this JSON: {"result":"Pass","explanation":"${prefix}-verdict"}\nMapped input: {{output}}`,
    pass_threshold: 0.5, choice_scores: { Pass: 1, Fail: 0 }, check_internet: false,
    error_localizer_enabled: false, template_format: 'mustache',
  });
  // EvalGroupView.create -> create_eval_group returns the serialized group directly.
  const group = await owner.api.post<{ result: { id: string } }>('/model-hub/eval-groups/', {
    name: `${prefix}-group`, description: 'Synthetic prompt experiment group',
    eval_template_ids: [evaluator.result.id],
  });
  await testInfo.attach('seeded-identities', { body: JSON.stringify({ ownerOrg: owner.organizationId, foreignOrg: foreign.organizationId, prompt, foreignPrompt, evaluatorId: evaluator.result.id, groupId: group.result.id }), contentType: 'application/json' });
  const context = await scopeActors.openContext(browser, owner);
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  const browserErrors: string[] = [];
  page.on('pageerror', error => browserErrors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') browserErrors.push(message.text()); });
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (['localhost', '127.0.0.1'].includes(url.hostname) || ['data:', 'blob:'].includes(url.protocol)) await route.continue();
    else await route.abort('blockedbyclient');
  });
  try {
    await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
    await page.getByText('New dataset', { exact: true }).click();
    await page.getByText('Upload a file', { exact: true }).click();
    const upload = page.getByRole('dialog').filter({ has: page.getByText('Upload a file', { exact: true }) });
    await uploadFixture(upload.locator('input[type="file"]'), 'catalog-regions.csv');
    await upload.getByPlaceholder('Dataset name').fill(`${prefix}-data`);
    const uploaded = page.waitForResponse(r => new URL(r.url()).pathname === UPLOAD && r.request().method() === 'POST', { timeout: UI_READY });
    await upload.getByRole('button', { name: 'Import 3 rows', exact: true }).click();
    const uploadedResponse = await uploaded;
    expect(uploadedResponse.status()).toBe(200);
    const datasetId = (await uploadedResponse.json()).result.dataset_id as string;
    await expect.poll(async () => (await scopeProbe.pg<{ n: string }>('SELECT count(*) AS n FROM model_hub_row WHERE dataset_id=$1 AND NOT deleted', [datasetId]))[0].n, POLL.ASYNC_JOB).toBe('3');
    // UploadFileModal navigates only after all imported values have been saved.
    await expect(page).toHaveURL(new RegExp(`/develop/${datasetId}([?]|$)`), { timeout: UI_READY });
    // RunExperiment.jsx parseRunOnRows: public prompt-prefill deep link, not a component mount.
    await page.goto(`/dashboard/develop/${datasetId}?tab=experiments&new_experiment=1&source=prompt&prompt_id=${prompt.id}&prompt_version=${prompt.version}`, { waitUntil: 'domcontentloaded' });
    await expect(page.getByText(prompt.name, { exact: true }).first()).toBeVisible({ timeout: UI_READY });
    await expect(page.getByText('v1', { exact: true }).first()).toBeVisible({ timeout: UI_READY });
    const name = page.getByRole('textbox', { name: /^Experiment name/ });
    await name.fill(`${prefix}-experiment`);
    await expect(name).toHaveValue(`${prefix}-experiment`);
    await page.getByTestId('wizard-continue').click();
    await page.getByTestId('wizard-continue').click();
    await page.getByRole('button', { name: 'Add a group', exact: true }).click();
    await page.getByPlaceholder('Search groups', { exact: true }).fill(`${prefix}-group`);
    const groupRows = page.getByTestId('add-eval-group-row');
    await expect(groupRows).toHaveCount(1, { timeout: UI_READY });
    await expect(groupRows.getByText(`${prefix}-group · 1 evaluation`, { exact: true })).toBeVisible({ timeout: UI_READY });
    await groupRows.click();
    await expect(page.getByRole('dialog', { name: 'New experiment', exact: true }).getByText(`${prefix}-judge`, { exact: true }).filter({ visible: true })).toHaveCount(1, { timeout: UI_READY });
    await page.getByTestId('wizard-continue').click();
    await model.assertReady();
    // RunExperiment.jsx includes the visible keyboard shortcut in the button name.
    const run = page.getByRole('button', { name: /^Run experiment/ });
    await expect(run).toBeEnabled({ timeout: UI_READY });
    const browserCreates: unknown[] = [];
    page.on('request', request => {
      if (new URL(request.url()).pathname === EXPERIMENTS && request.method() === 'POST') browserCreates.push(request.postDataJSON());
    });
    let pendingId: string | undefined;
    const outage = process.env.E2E_EXPERIMENT_ADMISSION_OUTAGE === '1';
    const restoreAdmission = pauseAdmissionService(receipt, testInfo.config.workers);
    let saved: import('@playwright/test').Response;
    try {
      [saved] = await Promise.all([
        page.waitForResponse(r => new URL(r.url()).pathname === EXPERIMENTS && r.request().method() === 'POST', { timeout: UI_READY }),
        run.click(),
      ]);
      if (outage) {
        expect(saved.status()).toBe(503);
        const failed = await saved.json();
        expect(failed.status).toBe(false);
        expect(failed.code).toBe('experiment_admission_unconfirmed');
        pendingId = /experiment_id=([0-9a-f-]{36})/.exec(failed.detail)?.[1];
        expect(pendingId).toBeTruthy();
        const pending = await scopeProbe.pg<{ id: string; state: string }>(
          `SELECT e.id,d.dataset_config->'_experiment_v2_submission'->>'state' AS state
           FROM model_hub_experimentstable e JOIN model_hub_dataset d ON d.id=e.snapshot_dataset_id
           WHERE e.id=$1 AND e.dataset_id=$2`, [pendingId, datasetId]);
        expect(pending).toEqual([{ id: pendingId, state: 'pending' }]);
        const originalWire = saved.request().postDataJSON();
        await testInfo.attach('real-admission-error', { body: JSON.stringify({ failed, pending, originalWire }), contentType: 'application/json' });
        await expect(page.getByRole('dialog', { name: 'New experiment', exact: true }).getByRole('alert')).toHaveText(failed.detail, { timeout: UI_READY });
        await page.getByRole('button', { name: 'Close', exact: true }).click();
        await page.getByRole('button', { name: 'Discard', exact: true }).click();
        await expect(run).toHaveCount(0, { timeout: UI_READY });
        // Stay in the same document/query-cache session. No payload, values,
        // prompt-prefill URL or API replay is supplied to this reopen action.
        await page.getByRole('button', { name: 'New experiment', exact: true }).first().click();
        await expect(run).toBeEnabled({ timeout: UI_READY });
        await expect(page.getByText(`${prefix}-experiment`, { exact: true }).first()).toBeVisible({ timeout: UI_READY });
        restoreAdmission();
        [saved] = await Promise.all([
          page.waitForResponse(r => new URL(r.url()).pathname === EXPERIMENTS && r.request().method() === 'POST', { timeout: UI_READY }),
          run.click(),
        ]);
        expect(saved.request().postDataJSON()).toEqual(originalWire);
      }
    } finally {
      restoreAdmission();
    }
    expect(saved.status()).toBe(200);
    const wire = saved.request().postDataJSON();
    expect(wire.user_eval_metrics.map((metric: { template_id: string }) => metric.template_id)).toEqual([evaluator.result.id]);
    expect(wire.name).toBe(`${prefix}-experiment`);
    const created = await saved.json();
    const experimentId = /experiment_id=([0-9a-f-]{36})/.exec(created.result)?.[1];
    expect(experimentId).toBeTruthy();
    if (outage) expect(experimentId).toBe(pendingId);
    const admitted = await scopeProbe.pg<{ state: string }>(
      `SELECT d.dataset_config->'_experiment_v2_submission'->>'state' AS state
       FROM model_hub_experimentstable e JOIN model_hub_dataset d ON d.id=e.snapshot_dataset_id WHERE e.id=$1`, [experimentId]);
    expect(admitted).toEqual([{ state: 'accepted' }]);
    const attachedEvaluators = await scopeProbe.pg<{ template_id: string }>(
      `SELECT m.template_id FROM model_hub_userevalmetric m
       JOIN model_hub_experimentstable_user_eval_template_ids link ON link.userevalmetric_id=m.id
       WHERE link.experimentstable_id=$1 AND NOT m.deleted ORDER BY m.template_id`, [experimentId]);
    expect(attachedEvaluators).toEqual([{ template_id: evaluator.result.id }]);
    const path = `${EXPERIMENTS}${experimentId}/`;
    await testInfo.attach('submitted-experiment', { body: JSON.stringify({ datasetId, experimentId, wire }), contentType: 'application/json' });
    const detail = async () => (await owner.api.get<{ result: Detail }>(path)).result;
    await expect(page).toHaveURL(new RegExp(`/experiment/${experimentId}/data`), { timeout: UI_READY });
    await expect.poll(async () => (await detail()).status, POLL.ASYNC_JOB).toBe('Completed');
    const original = await detail();
    expect(original.prompt_configs).toHaveLength(1);
    const config = original.prompt_configs[0];
    expect(config.prompt_id).toBe(prompt.id);
    expect(config.prompt_version).toBe(prompt.version);
    expect(config.model).toBe(MOCK_MODEL);
    // SourceChoices.EXPERIMENT_EVALUATION and CellStatus.PASS: completed judge work, not only membership.
    const scored = await scopeProbe.pg<{ status: string }>(
      `SELECT c.status FROM model_hub_cell c JOIN model_hub_column col ON col.id=c.column_id
       WHERE c.dataset_id=$1 AND col.source='experiment_evaluation' AND NOT col.deleted AND NOT c.deleted ORDER BY c.id`, [original.snapshot_dataset_id]);
    expect(scored).toEqual(Array(3).fill({ status: 'pass' }));
    await expect(page.getByText('Pass', { exact: true })).toHaveCount(3, { timeout: UI_READY });
    const cells = () => scopeProbe.pg<{ id: string; value: string; status: string }>(
      `SELECT c.id,c.value,c.status FROM model_hub_cell c JOIN model_hub_column col ON col.id=c.column_id
       JOIN model_hub_experimentpromptconfig epc ON col.source_id=epc.experiment_dataset_id::text
       WHERE epc.id=$1 AND col.source='experiment' AND NOT col.deleted AND NOT c.deleted ORDER BY c.id`, [config.id]);
    const outputs = await cells();
    expect(outputs.map(cell => cell.value)).toEqual(Array(3).fill(`echo: ${JSON.stringify([{ type: 'text', text: `${prompt.name}-input` }])}`));
    expect(outputs.filter(cell => cell.status === 'error')).toEqual([]);
    await expect(page.getByText(`echo: ${JSON.stringify([{ type: 'text', text: `${prompt.name}-input` }])}`, { exact: true })).toHaveCount(3, { timeout: UI_READY });
    const retried = await scopeActors.send(owner, 'POST', EXPERIMENTS, wire);
    expect(retried.status).toBe(200);
    expect(retried.body).toEqual(created);
    expect(await detail()).toEqual(original);
    expect(await cells()).toEqual(outputs);
    const matches = await scopeProbe.pg<{ id: string }>('SELECT id FROM model_hub_experimentstable WHERE dataset_id=$1 AND name=$2 AND NOT deleted', [datasetId, wire.name]);
    expect(matches.map(row => row.id)).toEqual([experimentId]);
    expect(browserCreates).toEqual(outage ? [wire, wire] : [wire]);
    await testInfo.attach('browser-submissions', { body: JSON.stringify({ outage, browserCreates }), contentType: 'application/json' });
    expect((await scopeActors.send(foreign, 'POST', EXPERIMENTS, wire)).status).toBe(404);
    expect((await scopeActors.send(foreign, 'GET', path)).status).toBe(404);
    expect((await scopeActors.send(foreign, 'GET', `/model-hub/eval-groups/${group.result.id}/`)).status).toBe(400);
    const retainedGroup = await owner.api.get<{ result: { members: { eval_template_id: string }[] } }>(`/model-hub/eval-groups/${group.result.id}/`);
    expect(retainedGroup.result.members.map(member => member.eval_template_id)).toEqual([evaluator.result.id]);
    const invalid = { ...wire, name: `${prefix}-refused`, prompt_config: [{ ...wire.prompt_config[0], prompt_id: foreignPrompt.id, prompt_version: foreignPrompt.version }] };
    expect((await scopeActors.send(owner, 'POST', EXPERIMENTS, invalid)).status).toBe(400);
    expect(await detail()).toEqual(original);
    expect(await cells()).toEqual(outputs);
    await testInfo.attach('scope-evidence', { body: JSON.stringify(scopeActors.evidence()), contentType: 'application/json' });
  } finally {
    await testInfo.attach('browser-errors', { body: JSON.stringify(browserErrors), contentType: 'application/json' });
    await testInfo.attach('last-page-text', { body: await page.locator('body').innerText(), contentType: 'text/plain' });
    await context.close();
  }
});
