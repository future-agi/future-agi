import type { Page } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { inspectManagedMock } from '../../lib/managed-mock';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// evaluations/decision_models.py: model identity differs from the selected connection.
const GROUP = 'openai/gpt-6-luna-decisions';
const DIRECT = 'decision/openai/gpt-6-luna';
const ROUTER = 'decision/openrouter/openai/gpt-6-luna-decisions';
const LABEL = 'GPT-6 Luna Decisions';
// useDecisionModels, KeyCard and EvalDetailPage's existing endpoints.
const CATALOG = '/model-hub/api/models_list/';
const KEYS = '/model-hub/api-keys/';
const TEMPLATES = '/model-hub/eval-templates/';
const PLAYGROUND = '/model-hub/eval-playground/';
// README: browser first-paint budget, not Playwright's default five seconds.
const UI_READY = 60_000;
const ui = expect.configure({ timeout: UI_READY });

type Mapping = { question_type: string; pass: { criteria_true: string; criteria_false: string } };
type Catalog = { results: { model_name: string; connections: { model_name: string; provider: string; is_available: boolean }[] }[] };
type Created = { result: { id: string } };
type Detail = { result: { model: string; config: { model: string; jev_mapping: Mapping } } };
type Stored = { model: string; organization_id: string; workspace_id: string; config: { model: string; jev_mapping: Mapping } };
type Version = { id: string; model: string; version_number: number; is_default: boolean; config_snapshot: { model: string; jev_mapping: Mapping } };
type Result = { result: { metadata: string | { decision: Decision }; output: string } };
type Decision = { requested_model: string; actual_model: string; connection: string; probability: number; verdict: string;
  usage: { input_tokens: number; output_tokens: number } };

async function openPicker(page: Page) {
  // ModelSelector's accessible trigger, shared by creation and saved evaluators.
  await page.getByRole('button', { name: /^Evaluation model:/ }).click();
  const search = page.getByPlaceholder('Search models...');
  await ui(search).toBeVisible();
  await search.fill(LABEL);
  const picker = page.locator('.MuiPopover-paper').filter({ has: search });
  await ui(picker.getByRole('menuitem').filter({ hasText: LABEL })).toHaveCount(1);
  return picker;
}

async function saveSyntheticKey(page: Page, provider: 'OpenAI' | 'OpenRouter') {
  const drawer = page.locator('.MuiDrawer-paper').filter({ has: page.getByPlaceholder('Search API provider') });
  await ui(drawer).toBeVisible();
  // Provider display-name casing is legacy Openai for native, OpenRouter is pinned.
  const card = drawer.locator('form').filter({ has: page.getByText(provider === 'OpenAI' ? /^Openai$/ : /^OpenRouter$/) });
  await ui(card).toHaveCount(1);
  await card.getByLabel('API Key', { exact: true }).fill(`e2e-decision-${provider.toLowerCase()}-key`);
  const write = page.waitForResponse(r => new URL(r.url()).pathname === KEYS && r.request().method() === 'POST', { timeout: UI_READY });
  await card.getByRole('button', { name: 'save', exact: true }).click();
  const response = await write;
  expect(response.status()).toBe(200);
  // Never attach the key request/response. Only the nonsecret record ID is useful.
  return ((await response.json()) as { result: { id: string } }).result.id;
}

async function runFromUI(page: Page, input: string, model: string, connection: string, actualModel: string) {
  // CodeEditor.jsx mounts Monaco. Use native paste: insertText is interpreted
  // as typing and can auto-close braces in a multiline JSON document.
  const editor = page.locator('.monaco-editor textarea.inputarea').first();
  await ui(editor).toBeVisible();
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.evaluate(text => navigator.clipboard.writeText(text), JSON.stringify({ input }, null, 2));
  await editor.focus();
  await editor.press('ControlOrMeta+a');
  await editor.press('ControlOrMeta+v');
  await ui(page.locator('.monaco-editor .view-lines').first()).toContainText(input);
  await ui(page.getByText('Invalid JSON', { exact: true })).toBeHidden();
  // Use normal keyboard activation: a late nonmodal success toast can cover
  // the footer and pause on hover. Do not force-click through that overlay.
  const runButton = page.getByRole('button', { name: 'Test Evaluation', exact: true })
    .and(page.locator('button.MuiButton-root'));
  await ui(runButton).toBeVisible();
  await ui(runButton).toBeEnabled();
  await runButton.focus();
  await ui(runButton).toBeFocused();
  const [response] = await Promise.all([
    page.waitForResponse(r => new URL(r.url()).pathname === PLAYGROUND && r.request().method() === 'POST', { timeout: UI_READY }),
    runButton.press('Enter'),
  ]);
  expect(response.request().postDataJSON().config.mapping).toEqual({ input });
  const body = await response.json() as Result;
  expect(response.status(), JSON.stringify(body)).toBe(200);
  const metadata = typeof body.result.metadata === 'string' ? JSON.parse(body.result.metadata) : body.result.metadata;
  expect(metadata.decision).toMatchObject({ requested_model: model, actual_model: actualModel,
    connection, probability: 0.9, verdict: 'pass', usage: { input_tokens: 37, output_tokens: 0 } });
  expect(body.result.output).toBe('Passed');
  await ui(page.getByText(`Requested: ${model}`, { exact: true })).toBeVisible();
  await ui(page.getByText(`Connection: ${connection === 'openai' ? 'OpenAI' : 'OpenRouter'}`, { exact: true })).toBeVisible();
  return metadata.decision as Decision;
}

test('EVAL-E2E-012: a decision evaluator runs through the chosen direct or OpenRouter connection and retains it after reload', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-012', area: 'evals',
    userGoal: 'Choose a decision model’s direct or OpenRouter connection, run a Pass/Fail evaluation through it, and retain the explicit choice after reload',
    steps: ['attest the isolated managed mock routing',
      'search one decision model with native OpenAI and OpenRouter connections',
      'add the native synthetic key through the product drawer and publish a Pass/Fail evaluator',
      'run the evaluator through native OpenAI against deterministic synthetic input',
      'choose OpenRouter, add its synthetic key through the drawer and run again',
      'save the new version as default and reload the persisted connection'],
    backendChecks: [
      'the model-list API exposes one group with the exact native OpenAI and OpenRouter route IDs shown by the UI',
      'UI-created provider keys belong only to the isolated actor scope and update that connection’s catalog readiness',
      'the template and its version store the explicit route, Pass/Fail criteria and exact organization/workspace scope',
      'both UI playground calls run through the real backend/gateway and return exact synthetic probability, verdict, usage and connection metadata',
      'the reloaded editor, detail API and default version retain the saved OpenRouter route and authored criteria',
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe }, testInfo) => {
  test.setTimeout(660_000); // 8×UI_READY (480s) + fixture/API120s + storage/headroom60s.
  const receipt = inspectManagedMock();
  await testInfo.attach('verified-managed-mock', { contentType: 'application/json', body: JSON.stringify(receipt) });
  const actor = scopeActors.ownerA;
  const context = await scopeActors.openContext(browser, actor);
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const name = `e2e-decision12-${suffix}`;
  const criteria = { criteria_true: `Explicit confirmation ${suffix}`, criteria_false: `No confirmation ${suffix}` };
  const input = `e2e-decision:pass ${suffix}`;
  let id = '';
  const keyIds: string[] = [];
  try {
    await test.step('backend check1: one model group and two explicit connections', async () => {
      const catalog = await actor.api.get<Catalog>(CATALOG, { model_type: 'decision', limit: 100 });
      const groups = catalog.results.filter(row => row.model_name === GROUP);
      expect(groups).toHaveLength(1);
      expect(groups[0].connections.map(c => c.model_name).sort()).toEqual([DIRECT, ROUTER].sort());
      expect(groups[0].connections.map(c => c.is_available)).toEqual([false, false]);
    });
    await test.step('UI: author a new judge and choose native OpenAI', async () => {
      const draft = page.waitForResponse(r => new URL(r.url()).pathname === `${TEMPLATES}create-v2/` && r.request().method() === 'POST', { timeout: UI_READY });
      await page.goto(`${E2E.appUrl}/dashboard/evaluations/create`, { waitUntil: 'domcontentloaded' });
      const response = await draft;
      expect(response.status()).toBe(200);
      id = ((await response.json()) as Created).result.id;
      await testInfo.attach('created-template', { contentType: 'application/json', body: JSON.stringify({ id, name, organizationId: actor.organizationId, workspaceId: actor.workspaceId }) });
      await page.getByRole('tab', { name: 'LLM-As-A-Judge', exact: true }).click();
      await page.getByPlaceholder('Eg: Hallucination detector').fill(name);
      await page.locator('.ql-editor[contenteditable="true"]').first().fill('Does {{input}} explicitly confirm the requested action?');
      const picker = await openPicker(page);
      const selector = picker.getByRole('combobox', { name: `Connection for ${LABEL}`, exact: true });
      await ui(selector).toHaveValue(DIRECT);
      await ui(selector.locator('option')).toHaveText(['OpenAI · Add key', 'OpenRouter · Add key']);
      await picker.getByRole('menuitem').filter({ hasText: LABEL }).click();
    }, { timeout: UI_READY });
    await test.step('UI: add the synthetic native key and return to the evaluator', async () => {
      keyIds.push(await saveSyntheticKey(page, 'OpenAI'));
      await ui(page.getByText(`${LABEL} · OpenAI`, { exact: true })).toBeVisible();
    }, { timeout: UI_READY });
    await test.step('UI: publish the Pass/Fail criteria', async () => {
      await page.getByLabel('Describe a pass (true)').fill(criteria.criteria_true);
      await page.getByLabel('Describe a fail (false)').fill(criteria.criteria_false);
      const publish = page.waitForResponse(r => new URL(r.url()).pathname === `${TEMPLATES}${id}/update/` && r.request().method() === 'PUT' && r.request().postDataJSON()?.publish === true, { timeout: UI_READY });
      await page.getByRole('button', { name: 'Save Evaluation', exact: true }).click();
      expect((await publish).status()).toBe(200);
      await ui(page).toHaveURL(new RegExp(`/dashboard/evaluations/${id}$`));
      await ui(page.getByText(`${LABEL} · OpenAI`, { exact: true })).toBeVisible();
    }, { timeout: UI_READY });
    await test.step('backend check3: native template and version store the exact criteria and scope', async () => {
      const rows = await scopeProbe.pg<Stored>('SELECT model,config,organization_id,workspace_id FROM model_hub_evaltemplate WHERE id=$1', [id]);
      expect(rows).toHaveLength(1);
      expect(rows[0]).toMatchObject({ model: DIRECT, organization_id: actor.organizationId, workspace_id: actor.workspaceId,
        config: { model: DIRECT, jev_mapping: { question_type: 'noul', pass: criteria } } });
      const versions = await scopeProbe.pg<Version>('SELECT id,model,version_number,is_default,config_snapshot FROM model_hub_eval_template_version WHERE eval_template_id=$1 ORDER BY version_number', [id]);
      expect(versions).toHaveLength(1);
      expect(versions[0].model).toBe(DIRECT);
      expect(versions[0].config_snapshot.jev_mapping.pass).toEqual(criteria);
    });
    await test.step('backend check4a: run native OpenAI from the UI', async () => {
      const result = await runFromUI(page, input, DIRECT, 'openai', 'gpt-6-luna');
      await testInfo.attach('native-decision-result', { contentType: 'application/json', body: JSON.stringify(result) });
    }, { timeout: UI_READY });
    await test.step('UI: change only the connection and add the OpenRouter key', async () => {
      const picker = await openPicker(page);
      await picker.getByRole('combobox', { name: `Connection for ${LABEL}`, exact: true }).selectOption(ROUTER);
      keyIds.push(await saveSyntheticKey(page, 'OpenRouter'));
      await ui(page.getByText(`${LABEL} · OpenRouter`, { exact: true })).toBeVisible();
    }, { timeout: UI_READY });
    await test.step('backend check2: both UI-created keys are scoped and readiness agrees', async () => {
      const rows = await scopeProbe.pg<{ id: string; provider: string; organization_id: string; workspace_id: string }>(
        'SELECT id,provider,organization_id,workspace_id FROM model_hub_apikey WHERE id=ANY($1::uuid[]) ORDER BY provider', [keyIds]);
      expect(rows).toEqual([
        { id: keyIds[0], provider: 'openai', organization_id: actor.organizationId, workspace_id: actor.workspaceId },
        { id: keyIds[1], provider: 'openrouter', organization_id: actor.organizationId, workspace_id: actor.workspaceId },
      ]);
      const catalog = await actor.api.get<Catalog>(CATALOG, { model_type: 'decision', limit: 100 });
      expect(catalog.results.find(row => row.model_name === GROUP)!.connections.map(c => c.is_available)).toEqual([true, true]);
      await testInfo.attach('provider-key-record-ids', { contentType: 'application/json', body: JSON.stringify({ keyIds }) });
    });
    await test.step('backend check4b: run OpenRouter from the UI without changing the question', async () => {
      const result = await runFromUI(page, input, ROUTER, 'openrouter', GROUP);
      await testInfo.attach('router-decision-result', { contentType: 'application/json', body: JSON.stringify(result) });
    }, { timeout: UI_READY });
    await test.step('UI: save the OpenRouter version and make it default', async () => {
      const saved = page.waitForResponse(r => new URL(r.url()).pathname === `${TEMPLATES}${id}/versions/create/` && r.request().method() === 'POST', { timeout: UI_READY });
      await page.getByRole('button', { name: 'Save Version', exact: true }).click();
      expect((await saved).status()).toBe(200);
      // EvalTemplateVersionManager.create_version makes the new version default
      // atomically; there is no second Set as Default action for an already-default row.
      await ui(page.getByText('Default', { exact: true })).toBeVisible();
    }, { timeout: UI_READY });
    await test.step('backend check5: reload, API and default version retain the selected connection', async () => {
      await page.goto(`${E2E.appUrl}/dashboard/evaluations/${id}`, { waitUntil: 'domcontentloaded' });
      await ui(page.getByText(`${LABEL} · OpenRouter`, { exact: true })).toBeVisible();
      await ui(page.getByLabel('Describe a pass (true)')).toHaveValue(criteria.criteria_true);
      await ui(page.getByLabel('Describe a fail (false)')).toHaveValue(criteria.criteria_false);
      const detail = await actor.api.get<Detail>(`${TEMPLATES}${id}/detail/`);
      expect(detail.result.model).toBe(ROUTER);
      expect(detail.result.config.jev_mapping.pass).toEqual(criteria);
      const versions = await scopeProbe.pg<Version>('SELECT id,model,version_number,is_default,config_snapshot FROM model_hub_eval_template_version WHERE eval_template_id=$1 AND is_default=true', [id]);
      expect(versions).toHaveLength(1);
      expect(versions[0]).toMatchObject({ model: ROUTER, version_number: 2, config_snapshot: { model: ROUTER, jev_mapping: { pass: criteria } } });
      await testInfo.attach('reloaded-decision', { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
    }, { timeout: UI_READY });
  } finally {
    await context.close();
  }
});
