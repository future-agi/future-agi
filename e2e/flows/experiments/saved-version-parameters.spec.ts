import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { promisify } from 'node:util';
import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// Depends on gw/E2E-MOCKLLM-RECEIPT-1 e502c9b693. The managed proof mounts
// that exact e2e tree at /receipt/e2e in mock-llm; receipt reads execute INSIDE
// its network namespace, never through a published Docker port. No lib/stack
// implementation is duplicated here. Each invocation needs a fresh run scope in
// the gateway's provider headers and E2E_PARAMLOSS_RECEIPT_RUN, with workers=1.
// OpenAI-route coverage is deliberately absent until 617fc455fb is integrated.
// Keep VITE_DSX_EXPERIMENTS_BASIC=true on both product revisions for parity
// with the pre-flip build. The current launchSwitch.js defaults ON when unset;
// explicit false disables NEW wizard enhancements, not classic prompt creation.
const UI_READY = 60_000;
// ManualDatasetCreateRequestSerializer; PromptTemplateViewSet.create_draft;
// ExperimentCreateV2Serializer; frontend RunExperiment/serverBody.js.
const DATASETS = '/model-hub/develops/create-dataset-manually/';
const PROMPTS = '/model-hub/prompt-templates/create-draft/';
const EXPERIMENTS = '/model-hub/experiments/v2/';
// Synthetic model names configured only in the proof stack's real gateway.
// Both use OpenAI-compatible CUSTOM MODEL ingress; their actual upstream legs
// are Anthropic Messages and Gemini generateContent, respectively.
const LEGS = [
  { model: 'claude-paramloss-fixture', route: '/v1/messages', gemini: false },
  { model: 'gemini-2.5-paramloss-fixture', route: '/v1beta/models/gemini-2.5-paramloss-fixture:generateContent', gemini: true },
];
const exec = promisify(execFile);
interface Receipt {
  id: string; at: string; digest: string;
  request: { route: string; model: string; params: Record<string, unknown>;
    generation_config: Record<string, unknown>; stream: boolean };
}
interface Version { id: string; prompt_config_snapshot: { configuration: Record<string, unknown> } }
interface Run { id: string; prompt_version_id: string; model_params: Record<string, unknown> }
interface CreateBody {
  name: string; dataset_id: string;
  prompt_config: { prompt_id: string; prompt_version: string; model: string;
    model_params: Record<string, unknown> }[];
  [key: string]: unknown;
}
// The receipt digest contract is canonical sorted-key JSON, not the HTTP body.
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') {
    const item = value as Record<string, unknown>;
    return `{${Object.keys(item).sort().map(key => `${JSON.stringify(key)}:${canonical(item[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

// Documented mock-receipts.mjs Compose client invocation, not a new mock/probe.
async function receipts(run: string): Promise<Receipt[]> {
  const container = process.env.E2E_PARAMLOSS_MOCK_CONTAINER;
  if (!container || !/^[a-zA-Z0-9_.-]+$/.test(container)) throw new Error('receipt mock container required');
  const script = `import { MockReceipts } from '/receipt/e2e/lib/mock-receipts.mjs';
    console.log(JSON.stringify(await new MockReceipts(process.argv[1]).list()));`;
  try {
    const { stdout } = await exec('docker', ['exec', container, 'node', '--input-type=module', '-e', script, run],
      { timeout: 10_000, maxBuffer: 1_048_576 });
    return JSON.parse(stdout);
  } catch {
    // Docker diagnostics may contain environment values; never attach them.
    throw new Error('loopback mock receipt read failed');
  }
}

test('EXP-E2E-001: saved prompt settings reach both provider legs unchanged', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EXP-E2E-001', area: 'experiments',
    userGoal: 'Create experiments from saved prompt versions and run exactly their saved settings without exposing another organization\'s version',
    steps: [
      'seed two organizations, one-row data, a deterministic evaluation and two saved prompt versions',
      'open each saved prompt in the real experiment wizard and inspect its parameters',
      'select the evaluation and Run experiment without editing those parameters',
      'read each run-scoped Anthropic Messages or Gemini request receipt',
      'try to create an experiment using the other organization\'s saved version',
    ],
    backendChecks: [
      'the UI request and persisted run reference the selected scoped version with temperature=0 and max_tokens=64',
      'each actual provider receipt has a valid canonical digest and exactly temperature=0 and token limit=64, with no substituted defaults',
      'a foreign prompt version is refused and creates no experiment row or provider request',
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe }, testInfo) => {
  // Two legs x (6 UI_READY + ASYNC_JOB), seeding/refusal + 120s headroom.
  test.setTimeout(960_000);
  const runScope = process.env.E2E_PARAMLOSS_RECEIPT_RUN;
  if (!runScope || !/^[A-Za-z0-9_.-]{1,80}$/.test(runScope)) throw new Error('isolated receipt run scope required');
  for (const url of [E2E.apiUrl, E2E.appUrl]) expect(new URL(url).hostname).toBe('localhost');
  const { ownerA, ownerB } = scopeActors;
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const prefix = `e2e-paramloss-${suffix}`;
  const context = await scopeActors.openContext(browser, ownerA);
  // Safety veto only; neither the product body nor its responses are rewritten.
  await context.route('**/*', route => new URL(route.request().url()).hostname === 'localhost'
    ? route.continue() : route.abort('blockedbyclient'));
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  const dataset = await ownerA.api.post<{ result: { dataset_id: string } }>(DATASETS,
    { dataset_name: prefix, number_of_rows: 1, number_of_columns: 1 });
  const datasetId = dataset.result.dataset_id;
  // test_code_eval.py pins this public code-evaluator shape. No LLM judge calls.
  const evaluatorName = `${prefix}-nonempty`;
  const evaluator = await ownerA.api.post<{ result: { id: string } }>('/model-hub/eval-templates/create-v2/', {
    name: evaluatorName, eval_type: 'code', code_language: 'python', output_type: 'pass_fail',
    code: 'def evaluate(output):\n    return bool(output)',
  });
  await testInfo.attach('seeded-experiment-inputs', { contentType: 'application/json', body: JSON.stringify({
    datasetId, evaluatorId: evaluator.result.id, organizationId: ownerA.organizationId,
    workspaceId: ownerA.workspaceId, foreignOrganizationId: ownerB.organizationId, runScope,
  }) });
  let submitted: CreateBody | undefined;
  const evidence: unknown[] = [];
  try {
    for (const leg of LEGS) {
      await test.step(`seed saved ${leg.model} version`, async () => {
        // custom_models/create validates by making a real completion; it uses the
        // loopback-only gateway and sentinel from docker-compose.distributed.yml.
        await ownerA.api.post('/model-hub/custom_models/create/', {
          model_provider: 'openai', model_name: leg.model, input_token_cost: 0, output_token_cost: 0,
          config_json: { key: 'local-dev-only-shared-secret-replace-me', api_base: 'http://agentcc-gateway:8080/v1' },
        });
      });
      const promptName = `${prefix}-${leg.model}`;
      const created = await ownerA.api.post<{ result: { id: string } }>(PROMPTS, {
        name: promptName, is_draft: false, variable_names: {}, prompt_config: [{
          messages: [{ role: 'user', content: [{ type: 'text', text: `${prefix}-input` }] }],
          // Deliberately omit providers: this is the shipped metadata-loss case.
          configuration: { model: leg.model, model_detail: { value: leg.model }, temperature: 0, max_tokens: 64 },
        }],
      });
      const promptId = created.result.id;
      const versions = await scopeProbe.pg<Version>(
        `SELECT v.id,v.prompt_config_snapshot FROM model_hub_promptversion v
         JOIN model_hub_prompttemplate p ON p.id=v.original_template_id
         WHERE p.id=$1 AND p.organization_id=$2 AND p.workspace_id=$3 AND v.template_version='v1' AND NOT v.deleted`,
        [promptId, ownerA.organizationId, ownerA.workspaceId]);
      expect(versions).toHaveLength(1);
      const versionId = versions[0].id;
      expect(versions[0].prompt_config_snapshot.configuration).toMatchObject({ temperature: 0, max_tokens: 64 });
      await testInfo.attach(`seeded-${leg.model}`, { contentType: 'application/json',
        body: JSON.stringify({ promptId, versionId, promptName, configuration: versions[0].prompt_config_snapshot.configuration }) });
      const before = new Set((await receipts(runScope)).map(r => r.id));
      const name = `${promptName}-experiment`;
      await test.step('UI: select the saved version and inspect parameters', async () => {
        // RunSources/runOnRows.js pins tab=experiments: the wizard is mounted
        // only on that tab. ConfigureStepLLM reads the prompt/version identity.
        // api/develop/experiment-create.js useExperimentNameSuggestion.
        const suggestion = page.waitForResponse(r => new URL(r.url()).pathname ===
          `/model-hub/experiments/v2/suggest-name/${datasetId}/`, { timeout: UI_READY });
        await page.goto(`/dashboard/develop/${datasetId}?tab=experiments&new_experiment=1&source=prompt&prompt_id=${promptId}&prompt_version=${versionId}`);
        expect((await suggestion).status()).toBe(200);
        // Wait for version hydration and the initial auto-name before typing;
        // RunExperiment.jsx otherwise can populate the field during fill().
        await expect.soft(page.getByTestId('model-params-summary')).toHaveText('temp 0 · max 64 tokens', { timeout: UI_READY });
        const nameField = page.getByRole('textbox', { name: /^Experiment name/ });
        await expect(nameField).not.toHaveValue('', { timeout: UI_READY });
        await nameField.fill(name);
        await expect(nameField).toHaveValue(name, { timeout: UI_READY });
        await page.getByTestId('wizard-continue').click();
      }, { timeout: UI_READY * 3 });
      await test.step('UI: select data and deterministic evaluation', async () => {
        await page.getByTestId('wizard-continue').click();
        await page.getByRole('button', { name: 'Add evaluation', exact: true }).click();
        await page.getByPlaceholder('Search evaluations...').fill(evaluatorName);
        await page.getByRole('row').filter({ hasText: evaluatorName }).getByRole('button', { name: 'Add', exact: true }).click();
        await page.getByTestId('wizard-continue').click();
      }, { timeout: UI_READY * 2 });
      const response = await test.step('UI: Run experiment', async () => {
        const pending = page.waitForResponse(r => new URL(r.url()).pathname === EXPERIMENTS && r.request().method() === 'POST', { timeout: UI_READY });
        // RunExperiment.jsx includes the platform shortcut in the accessible name.
        await page.getByRole('button', { name: /^Run experiment\b/ }).click();
        const result = await pending;
        expect(result.status()).toBe(200);
        submitted = result.request().postDataJSON() as CreateBody;
        expect(submitted.name).toBe(name);
        expect(submitted.dataset_id).toBe(datasetId);
        expect(submitted.prompt_config).toHaveLength(1);
        expect(submitted.prompt_config[0]).toMatchObject({ prompt_id: promptId, prompt_version: versionId, model: leg.model });
        // Soft so the pre-fix fallibility run still reaches the actual HTTP receipt.
        expect.soft(submitted.prompt_config[0].model_params).toMatchObject({ temperature: 0, max_tokens: 64 });
        return result.json();
      }, { timeout: UI_READY });
      const experiments = await scopeProbe.pg<{ id: string }>(
        `SELECT e.id FROM model_hub_experimentstable e JOIN model_hub_dataset d ON d.id=e.dataset_id
         WHERE e.name=$1 AND e.dataset_id=$2 AND d.organization_id=$3 AND d.workspace_id=$4 AND NOT e.deleted`,
        [name, datasetId, ownerA.organizationId, ownerA.workspaceId]);
      expect(experiments).toHaveLength(1);
      const experimentId = experiments[0].id;
      let actual: Receipt[] = [];
      await expect.poll(async () => {
        actual = (await receipts(runScope)).filter(r => !before.has(r.id));
        return actual.map(r => r.request.route);
      }, POLL.ASYNC_JOB).toEqual([leg.route]);
      // Attach BEFORE asserting values, so a red run carries the wrong receipt.
      evidence.push({ experimentId, versionId, submitted, response, receipts: actual });
      await testInfo.attach(`provider-receipt-${leg.model}`, { contentType: 'application/json', body: JSON.stringify(evidence.at(-1)) });
      const receipt = actual[0];
      expect(receipt.digest).toBe(`sha256:${createHash('sha256').update(canonical(receipt.request)).digest('hex')}`);
      expect(receipt.request.stream).toBe(false);
      expect.soft(leg.gemini ? receipt.request.generation_config : receipt.request.params,
        `actual ${leg.model} provider parameters`).toEqual(leg.gemini
          ? { temperature: 0, maxOutputTokens: 64 } : { temperature: 0, max_tokens: 64 });
      const configs = await scopeProbe.pg<Run>(
        `SELECT p.id,p.prompt_version_id,p.model_params FROM model_hub_experimentpromptconfig p
         JOIN model_hub_experimentdatasettable d ON d.id=p.experiment_dataset_id WHERE d.experiment_id=$1`, [experimentId]);
      await testInfo.attach(`persisted-${leg.model}`, { contentType: 'application/json', body: JSON.stringify(configs) });
      expect(configs).toHaveLength(1);
      expect(configs[0].prompt_version_id).toBe(versionId);
      expect.soft(configs[0].model_params).toMatchObject({ temperature: 0, max_tokens: 64 });
    }
    await test.step('API: foreign version is refused without a run', async () => {
      const foreign = await ownerB.api.post<{ result: { id: string } }>(PROMPTS, {
        name: `${prefix}-foreign`, is_draft: false, variable_names: {}, prompt_config: [{
          messages: [{ role: 'user', content: [{ type: 'text', text: 'foreign synthetic prompt' }] }],
          configuration: { model: LEGS[0].model, temperature: 0, max_tokens: 64 },
        }],
      });
      const [version] = await scopeProbe.pg<{ id: string }>(
        'SELECT id FROM model_hub_promptversion WHERE original_template_id=$1 AND template_version=$2', [foreign.result.id, 'v1']);
      expect(version).toBeDefined();
      const before = await receipts(runScope);
      const refusedName = `${prefix}-refused`;
      const refusal = await scopeActors.send(ownerA, 'POST', EXPERIMENTS, {
        ...submitted, name: refusedName,
        prompt_config: [{ ...submitted!.prompt_config[0], prompt_id: foreign.result.id, prompt_version: version.id }],
      });
      expect([400, 403, 404]).toContain(refusal.status);
      expect(await scopeProbe.pg('SELECT id FROM model_hub_experimentstable WHERE name=$1 AND dataset_id=$2',
        [refusedName, datasetId])).toEqual([]);
      expect(await receipts(runScope)).toEqual(before);
      await testInfo.attach('foreign-version-refusal', { contentType: 'application/json', body: JSON.stringify({
        promptId: foreign.result.id, versionId: version.id, status: refusal.status, body: refusal.body,
      }) });
    });
  } catch (error) {
    // Capture before closing our explicit context; automatic failure artifacts
    // otherwise see a closed page rather than the wizard that failed.
    await testInfo.attach('wizard-at-failure', { contentType: 'text/plain',
      body: `${page.url()}\n${await page.locator('body').innerText()}` });
    await testInfo.attach('wizard-at-failure-image', { contentType: 'image/png', body: await page.screenshot() });
    throw error;
  } finally {
    await testInfo.attach('all-provider-receipts', { contentType: 'application/json', body: JSON.stringify(await receipts(runScope)) });
    await context.close();
  }
});
