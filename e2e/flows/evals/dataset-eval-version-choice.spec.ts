import { randomUUID } from 'node:crypto';
import type { Page, Response } from '@playwright/test';
import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { inspectManagedMock, registerMockModel, MOCK_MODEL } from '../../lib/managed-mock';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// model_hub/urls.py and contracts.py: dataset, binding, template, prompt and experiment wires.
const CREATE_DATASET = '/model-hub/develops/create-empty-dataset/';
const CREATE_TEMPLATE = '/model-hub/eval-templates/create-v2/';
const PROMPTS = '/model-hub/prompt-templates/create-draft/'; // PromptTemplateViewSet.create_draft
const PROMPT_VERSIONS = '/model-hub/prompt-history-executions/'; // PromptHistoryExecutionViewSet.list
const EXPERIMENTS = '/model-hub/experiments/v2/'; // ExperimentCreateV2Serializer
const datasetPath = (id: string, action: string) => `/model-hub/develops/${id}/${action}/`;
const templatePath = (id: string, action: string) => `/model-hub/eval-templates/${id}/${action}/`;
const UI_READY = 60_000;
const RUNS = { ...POLL.EVAL_RESULT, timeout: POLL.EVAL_RESULT.timeout * 2 };
// Expectation-only fallibility anchors, never used to build a request or a seeded value.
const EXPECTED_MENU = ['Default version', 'V2 (default)', 'V1']; // CHOICE_NEG_MENU
const EXPECTED_CARD_PIN = 'v1'; // CHOICE_NEG_CARD
const EXPECTED_PIN_REFUSAL = 400; // CHOICE_NEG_TENANCY: a foreign version on an own evaluation
const EXPECTED_FOREIGN_TARGET = [403, 404]; // another organization's versions, dataset and evaluations

type Envelope<T> = { result: T };
type Version = { id: string; version_number: number; is_default: boolean };
type Role = 'pinned' | 'unpinned' | 'v1' | 'none';
type Tenant = { actor: ScopeActor; name: string; tag: string; datasetId: string; templateId: string;
  rowId: string; columnId: string; versions: Version[]; bindings: Partial<Record<Role, string>> };
type Cell = { source: string; source_id: string; value: string; status: string };

// The loopback judge STANDIN answers with the JSON after "Reply with exactly this JSON:"; the
// mapped input stays outside that JSON so an experiment's run output cannot break it.
const judged = (explanation: string) =>
  `Reply with exactly this JSON: {"result":"saved","explanation":"${explanation}"}\nMapped input: {{output}}`;

test("EVAL-E2E-082: an evaluation's version choice on a dataset and on the new experiment's eval card, across two organizations", {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-082', area: 'evals',
    userGoal: 'Choose which saved version of an evaluation runs on my dataset and on a new experiment, see that choice where I make it and get that version\'s results, for my organization only',
    steps: [
      'through the public API, give each of two organizations a dataset and an evaluation with two saved versions, V2 the default, saved on the dataset twice: pinned to V1 and with no pin',
      'open each saved evaluation\'s editor from the dataset Evaluate drawer, read its version choice and open its menu',
      'pick V2 on the pinned evaluation and choose Update Evaluation',
      'add the evaluation again from the drawer with V1 picked, then once more with no pick',
      'run each saved evaluation from the dataset\'s Run action through the loopback judge STANDIN',
      'choose Save version beside the choice and open its menu again',
      'in the New experiment wizard, add the evaluation, pick V1 in its card\'s editor, read the card and run the experiment',
      'as the other organization, try to read and save the first organization\'s versions, pin a foreign version and list or change its saved evaluations',
    ],
    backendChecks: [
      'the update request carries V2\'s id as pinned_version_id under the actor organization and workspace; PostgreSQL stores the pin on V2 and no version is added',
      'the add with V1 picked is stored pinned to V1 and the add with no pick is stored with no pin',
      'the dataset run stores each evaluation\'s own version result: V1\'s score and reason for the V1 pin, V2\'s for the V2 pin and for both unpinned evaluations',
      'Save version stores a third, default version under the actor organization and workspace and changes no saved evaluation\'s pin',
      'the experiment create request carries version_pin v1 on the card\'s evaluation; PostgreSQL stores that evaluation pinned to V1, the experiment detail reports V1, its run cells hold V1\'s score and reason, and an unknown version label is refused with 400',
      'the other organization gets 403 or 404 reading or saving versions and listing or changing saved evaluations, 400 pinning a foreign version, and nothing of either organization changes',
    ],
    browserChecks: [
      'the version choice reads V1 on the pinned evaluation and Default version on the other; its menu offers Default version, V2 (default) and V1 and never Latest; Save version and Update Evaluation sit beside it',
      'after Save version the choice reads V3 (default) and its menu lists V3 (default), V2 and V1',
      'the experiment card\'s editor offers the same choice, and after V1 is picked the card\'s chips show V1 · pinned',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Provision/setup 120 + two tenants * (30 UI60 + 2 RUNS180 + API reads) + headroom.
  test.setTimeout(4_500_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const ui = expect.configure({ timeout: UI_READY });
  const attach = (name: string, value: unknown) => testInfo.attach(name, {
    contentType: 'application/json', body: JSON.stringify(value),
  });
  const routing = inspectManagedMock({ evalBackground: true });
  await attach('external-judge-STANDIN', { label: 'Loopback echo judge STANDIN, not live-model quality proof', routing });
  const assertStack = () => expect(inspectManagedMock({ evalBackground: true })).toEqual(routing);
  for (const owner of scopes.owners) await registerMockModel(owner, probe, assertStack);
  const call = async <T,>(actor: ScopeActor, method: string, path: string, body?: unknown) => {
    const response = await scopes.send<T>(actor, method, path, body);
    expect(response.status, `${method} ${path}: ${JSON.stringify(response.body).slice(0, 400)}`).toBe(200);
    return response.body;
  };
  const stored = (t: Tenant) => probe.pg<{ id: string; name: string; pinned_version_id: string | null;
    organization_id: string; workspace_id: string }>(
    `SELECT id,name,pinned_version_id,organization_id,workspace_id FROM model_hub_userevalmetric
     WHERE dataset_id=$1 AND NOT deleted ORDER BY name`, [t.datasetId]);
  const pinOf = async (t: Tenant, role: Role) =>
    (await stored(t)).filter(b => b.name === `${t.name}-${role}`).map(b => b.pinned_version_id);
  const versionRows = (t: Tenant) => probe.pg<{ id: string; version_number: number; is_default: boolean;
    organization_id: string | null }>(
    `SELECT id,version_number,is_default,organization_id FROM model_hub_eval_template_version
     WHERE eval_template_id=$1 AND NOT deleted ORDER BY version_number`, [t.templateId]);
  const expectScoped = (response: Response, t: Tenant) => {
    expect(response.request().headers()['x-organization-id']).toBe(t.actor.organizationId);
    expect(response.request().headers()['x-workspace-id']).toBe(t.actor.workspaceId);
  };

  async function seed(actor: ScopeActor, label: string): Promise<Tenant> {
    const name = `e2e-evver-${label}-${suffix}`;
    const tag = `${label}-${suffix}`;
    const d = await call<Envelope<{ dataset_id: string }>>(actor, 'POST', CREATE_DATASET, { new_dataset_name: name });
    const datasetId = d.result.dataset_id;
    await call(actor, 'POST', datasetPath(datasetId, 'add_static_column'), { new_column_name: 'output', column_type: 'text' });
    const rowId = randomUUID();
    await call(actor, 'POST', datasetPath(datasetId, 'add_rows'), {
      rows: [{ id: rowId, cells: [{ column_name: 'output', value: `reply-${tag}` }] }],
    });
    const [column] = await probe.pg<{ id: string }>(
      "SELECT id FROM model_hub_column WHERE dataset_id=$1 AND name='output' AND NOT deleted", [datasetId]);
    const template = await call<Envelope<{ id: string }>>(actor, 'POST', CREATE_TEMPLATE, {
      name, eval_type: 'llm', model: MOCK_MODEL, output_type: 'deterministic',
      instructions: judged(`historic-${tag}`), choice_scores: { saved: 0.2 },
      check_internet: false, error_localizer_enabled: false,
    });
    const templateId = template.result.id;
    await call(actor, 'PUT', templatePath(templateId, 'update'), {
      instructions: judged(`newest-${tag}`), choice_scores: { saved: 0.9 },
    });
    await call(actor, 'POST', templatePath(templateId, 'versions/create'), {});
    const v = await call<Envelope<{ versions: Version[] }>>(actor, 'GET', templatePath(templateId, 'versions'));
    const versions = v.result.versions.sort((x, y) => x.version_number - y.version_number);
    expect(versions.map(x => [x.version_number, x.is_default])).toEqual([[1, false], [2, true]]);
    for (const role of ['pinned', 'unpinned'] as const) {
      await call(actor, 'POST', datasetPath(datasetId, 'add_user_eval'), {
        name: `${name}-${role}`, template_id: templateId, model: MOCK_MODEL, run: false,
        ...(role === 'pinned' ? { pinned_version_id: versions[0].id } : {}),
        config: { mapping: { output: column.id }, config: {} },
      });
    }
    const t: Tenant = { actor, name, tag, datasetId, templateId, rowId, columnId: column.id, versions, bindings: {} };
    const rows = await stored(t);
    expect(rows.map(r => [r.name, r.pinned_version_id])).toEqual([
      [`${name}-pinned`, versions[0].id], [`${name}-unpinned`, null]]);
    for (const r of rows) t.bindings[r.name.slice(name.length + 1) as Role] = r.id;
    return t;
  }
  const a = await seed(scopes.ownerA, 'a');
  const b = await seed(scopes.ownerB, 'b');
  await attach('seeded-identities', [a, b].map(({ actor, ...rest }) => ({ ...rest,
    organizationId: actor.organizationId, workspaceId: actor.workspaceId })));

  async function openList(page: Page, t: Tenant, count: number) {
    await page.goto(`/dashboard/develop/${t.datasetId}`, { waitUntil: 'domcontentloaded' });
    const more = page.locator('[data-action-bar="grid"]').getByRole('button', { name: 'More actions', exact: true });
    // More also holds "Evaluate guide" (DevelopDataRightSection.jsx:456); a disabled row's name adds its reason.
    const evaluate = page.getByRole('menuitem', { name: /^Evaluate(?! guide)/ });
    await expect(async () => {
      if (await evaluate.isVisible()) await page.keyboard.press('Escape');
      await more.click();
      await expect(evaluate).toBeEnabled({ timeout: 2_000 });
    }).toPass({ timeout: UI_READY });
    await evaluate.click();
    await ui(page.getByTestId('saved-eval-row').filter({ visible: true })).toHaveCount(count);
  }
  const evalRow = (page: Page, t: Tenant, role: Role) => page.getByTestId('saved-eval-row')
    .filter({ visible: true }).filter({ has: page.getByText(`${t.name}-${role}`, { exact: true }) });
  async function openEditor(page: Page, t: Tenant, role: Role, count: number) {
    await openList(page, t, count);
    await evalRow(page, t, role).getByRole('button', { name: 'Edit mapping & config', exact: true }).click();
  }
  // EvalPickerConfigFull.jsx header: Save version and, beside it, the version choice (an MUI
  // Select without an accessible name; versionPickHosts.js gives the dataset and experiment
  // hosts the "pick" choice).
  const saveVersion = (page: Page) => page.getByRole('button', { name: 'Save version', exact: true }).filter({ visible: true });
  const versionChoice = (page: Page) => saveVersion(page).locator('xpath=..').getByRole('combobox');
  const updateButton = (page: Page) => page.getByRole('button', { name: 'Update Evaluation', exact: true });
  const options = (page: Page) => page.getByRole('listbox').getByRole('option');
  async function expectMenu(page: Page, expected: string[]) {
    await versionChoice(page).click();
    await ui(options(page).first()).toBeVisible();
    await expect.poll(async () => (await options(page).allTextContents()).map(text => text.trim()).sort(),
      { timeout: UI_READY }).toEqual([...expected].sort());
    await ui(page.getByRole('option', { name: /latest/i })).toHaveCount(0);
    await page.keyboard.press('Escape');
    await ui(options(page)).toHaveCount(0);
  }
  async function pick(page: Page, option: string) {
    await versionChoice(page).click();
    await page.getByRole('listbox').getByRole('option', { name: option, exact: true }).click();
    await ui(options(page)).toHaveCount(0);
    await ui(versionChoice(page)).toHaveText(option);
  }
  async function addFromDrawer(page: Page, t: Tenant, role: 'v1' | 'none', count: number) {
    await openList(page, t, count);
    // SavedEvalsList's header reads "Evals (n)" or "n of m selected"; its Add opens the picker.
    await page.getByText(/^(?:Evals \(\d+\)|\d+ of \d+ selected)$/).filter({ visible: true }).locator('..')
      .getByRole('button', { name: 'Add', exact: true }).click();
    await page.getByPlaceholder('Search evaluations...', { exact: true }).fill(t.name);
    const templateRow = page.getByRole('row').filter({ has: page.getByText(t.name, { exact: true }) });
    await ui(templateRow).toHaveCount(1);
    await templateRow.getByRole('button', { name: 'Add', exact: true }).click();
    await page.getByPlaceholder('e.g. toxicity-check, my-custom-eval', { exact: true }).fill(`${t.name}-${role}`);
    // DatasetTestMode: select the real column for the evaluation's one variable.
    const mappingRow = page.getByText('output', { exact: true }).locator('xpath=../..')
      .filter({ has: page.getByPlaceholder('Select column', { exact: true }) });
    await mappingRow.getByPlaceholder('Select column', { exact: true }).click();
    await page.getByPlaceholder('Search columns…', { exact: true }).fill('output');
    await page.getByRole('tooltip').filter({ has: page.getByPlaceholder('Search columns…', { exact: true }) })
      .getByText('output', { exact: true }).click();
    await ui(mappingRow.getByPlaceholder('Select column', { exact: true })).toHaveValue('output');
    await ui(versionChoice(page)).toHaveText('Default version');
    await expectMenu(page, EXPECTED_MENU);
    if (role === 'v1') await pick(page, 'V1');
    const addPath = datasetPath(t.datasetId, 'add_user_eval');
    const added = page.waitForResponse(r => new URL(r.url()).pathname === addPath && r.request().method() === 'POST');
    const add = page.getByRole('button', { name: 'Add Evaluation', exact: true });
    await ui(add).toBeEnabled();
    await add.click();
    const response = await added;
    expect(response.status()).toBe(200);
    expectScoped(response, t);
    const wire = response.request().postDataJSON();
    expect(wire).toMatchObject({ name: `${t.name}-${role}`, template_id: t.templateId,
      config: { mapping: { output: t.columnId } } });
    expect(wire.pinned_version_id ?? null).toBe(role === 'v1' ? t.versions[0].id : null);
    const [binding] = (await stored(t)).filter(r => r.name === `${t.name}-${role}`);
    expect(binding).toEqual({ id: expect.any(String), name: `${t.name}-${role}`,
      pinned_version_id: role === 'v1' ? t.versions[0].id : null,
      organization_id: t.actor.organizationId, workspace_id: t.actor.workspaceId });
    t.bindings[role] = binding.id;
    return wire;
  }

  for (const t of [a, b]) {
    const context = await scopes.openContext(browser, t.actor);
    await context.route(url => /^https?:$/.test(url.protocol) &&
      !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname), route => route.abort());
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);

    await test.step('each saved evaluation\'s editor shows its version choice', async () => {
      for (const [role, shown] of [['pinned', 'V1'], ['unpinned', 'Default version']] as const) {
        await openEditor(page, t, role, 2);
        await ui(versionChoice(page)).toHaveText(shown);
        if (role === 'pinned') await ui(page.getByText('Version label: V1', { exact: true })).toBeVisible();
        await ui(saveVersion(page)).toBeEnabled();
        await ui(updateButton(page)).toBeVisible();
        await expectMenu(page, EXPECTED_MENU);
        await ui(versionChoice(page)).toHaveText(shown);
      }
      expect(await pinOf(t, 'pinned')).toEqual([t.versions[0].id]);
      expect(await pinOf(t, 'unpinned')).toEqual([null]);
    });

    await test.step('picking V2 on the pinned evaluation updates its pin and adds no version', async () => {
      await openEditor(page, t, 'pinned', 2);
      await ui(versionChoice(page)).toHaveText('V1');
      await pick(page, 'V2 (default)');
      const editPath = datasetPath(t.datasetId, `edit_and_run_user_eval/${t.bindings.pinned}`);
      const updated = page.waitForResponse(r => new URL(r.url()).pathname === editPath && r.request().method() === 'POST');
      await ui(updateButton(page)).toBeEnabled();
      await updateButton(page).click();
      const response = await updated;
      expect(response.status()).toBe(200);
      expectScoped(response, t);
      expect(response.request().postDataJSON().pinned_version_id).toBe(t.versions[1].id);
      expect(await pinOf(t, 'pinned')).toEqual([t.versions[1].id]);
      expect(await pinOf(t, 'unpinned')).toEqual([null]);
      expect((await versionRows(t)).map(v => v.id)).toEqual(t.versions.map(v => v.id));
    });

    await test.step('adding the evaluation again stores the version picked, or no pin', async () => {
      const v1 = await addFromDrawer(page, t, 'v1', 2);
      const none = await addFromDrawer(page, t, 'none', 3);
      await attach(`added-${t.name}`, { v1, none, stored: await stored(t) });
      expect((await stored(t)).map(r => [r.name, r.pinned_version_id])).toEqual([
        [`${t.name}-none`, null], [`${t.name}-pinned`, t.versions[1].id],
        [`${t.name}-unpinned`, null], [`${t.name}-v1`, t.versions[0].id]]);
      expect((await versionRows(t)).map(v => v.id)).toEqual(t.versions.map(v => v.id));
    });

    await test.step('the dataset run stores each evaluation\'s own version result', async () => {
      await assertStack();
      const roles: Role[] = ['pinned', 'unpinned', 'v1', 'none'];
      await openList(page, t, 4);
      const runPath = datasetPath(t.datasetId, 'start_evals_process');
      for (const role of roles) {
        const row = evalRow(page, t, role);
        const started = page.waitForResponse(r => new URL(r.url()).pathname === runPath && r.request().method() === 'POST');
        await row.getByRole('button', { name: 'Run', exact: true }).click();
        await ui(row.getByText('Run this evaluation on all rows?', { exact: true })).toBeVisible();
        await row.getByRole('button', { name: 'Run', exact: true }).filter({ hasText: 'Run' }).click();
        const response = await started;
        expect(response.status()).toBe(200);
        expectScoped(response, t);
        expect(response.request().postDataJSON()).toEqual({ user_eval_ids: [t.bindings[role]] });
      }
      await expect.poll(async () => {
        const listed = await scopes.send<Envelope<{ evals: Array<{ id: string; status: string }> }>>(t.actor, 'GET',
          datasetPath(t.datasetId, 'get_evals_list'), undefined, { eval_type: 'user' });
        expect(listed.status).toBe(200);
        return listed.body.result.evals.map(e => e.status);
      }, RUNS).toEqual(['Completed', 'Completed', 'Completed', 'Completed']);
      const cells = await probe.pg<Cell>(
        `SELECT col.source,col.source_id,c.value,c.status FROM model_hub_cell c JOIN model_hub_column col ON col.id=c.column_id
         WHERE c.dataset_id=$1 AND c.row_id=$2 AND NOT c.deleted AND NOT col.deleted
           AND col.source IN ('evaluation','evaluation_reason') ORDER BY col.source,col.source_id`, [t.datasetId, t.rowId]);
      await attach(`dataset-run-${t.name}`, { bindings: t.bindings, cells });
      const v1 = { value: "{'score': 0.2, 'choice': 'saved'}", reason: `historic-${t.tag}` };
      const v2 = { value: "{'score': 0.9, 'choice': 'saved'}", reason: `newest-${t.tag}` };
      for (const [role, expected] of [['pinned', v2], ['unpinned', v2], ['v1', v1], ['none', v2]] as const) {
        const id = t.bindings[role]!;
        const result = cells.filter(c => c.source === 'evaluation' && c.source_id === id);
        const reason = cells.filter(c => c.source === 'evaluation_reason' && c.source_id.endsWith(`-sourceid-${id}`));
        expect(result.map(c => [c.value, c.status]), `result of ${role}`).toEqual([[expected.value, 'pass']]);
        expect(reason.map(c => c.value), `reason of ${role}`).toEqual([expected.reason]);
      }
    });

    await test.step('Save version stores a new default that the choice lists', async () => {
      const pinsBefore = await stored(t);
      await openEditor(page, t, 'pinned', 4);
      await ui(versionChoice(page)).toHaveText('V2 (default)');
      const createPath = templatePath(t.templateId, 'versions/create');
      const saved = page.waitForResponse(r => new URL(r.url()).pathname === createPath && r.request().method() === 'POST');
      await saveVersion(page).click();
      const response = await saved;
      expect(response.status()).toBe(200);
      expectScoped(response, t);
      const created: Version = (await response.json()).result;
      expect(created.version_number).toBe(3);
      await ui(versionChoice(page)).toHaveText('V3 (default)');
      await expectMenu(page, ['Default version', 'V3 (default)', 'V2', 'V1']);
      const versions = await versionRows(t);
      expect(versions.map(v => [v.version_number, v.is_default, v.organization_id])).toEqual([
        [1, false, t.actor.organizationId], [2, false, t.actor.organizationId], [3, true, t.actor.organizationId]]);
      expect(versions[2].id).toBe(created.id);
      // Saving a version is not an Update of any saved evaluation.
      expect(await stored(t)).toEqual(pinsBefore);
      t.versions = versions.map(v => ({ id: v.id, version_number: v.version_number, is_default: v.is_default }));
    });

    await test.step('the New experiment card runs the version picked on it', async () => {
      // A separate one-row dataset without an "output" column, so the card maps output to the run's output.
      const expData = await call<Envelope<{ dataset_id: string }>>(t.actor, 'POST', CREATE_DATASET,
        { new_dataset_name: `${t.name}-expdata` });
      const expDatasetId = expData.result.dataset_id;
      await call(t.actor, 'POST', datasetPath(expDatasetId, 'add_static_column'), { new_column_name: 'question', column_type: 'text' });
      await call(t.actor, 'POST', datasetPath(expDatasetId, 'add_rows'), {
        rows: [{ id: randomUUID(), cells: [{ column_name: 'question', value: `question-${t.tag}` }] }],
      });
      const promptName = `${t.name}-prompt`;
      const prompt = await call<Envelope<{ id: string }>>(t.actor, 'POST', PROMPTS, {
        name: promptName, is_draft: false,
        prompt_config: [{ messages: [{ role: 'user', content: `${promptName}-input` }],
          configuration: { model: MOCK_MODEL, temperature: 0, max_tokens: 128, tools: [] } }],
      });
      const promptVersions = await scopes.send<{ results: Array<{ id: string; is_draft: boolean }> }>(t.actor, 'GET',
        PROMPT_VERSIONS, undefined, { template_id: prompt.result.id });
      expect(promptVersions.status).toBe(200);
      expect(promptVersions.body.results.map(v => v.is_draft)).toEqual([false]);
      const promptVersion = promptVersions.body.results[0].id;

      // RunExperiment.jsx parseRunOnRows: the public prompt-prefill link opens the wizard.
      await page.goto(`/dashboard/develop/${expDatasetId}?tab=experiments&new_experiment=1&source=prompt&prompt_id=${prompt.result.id}&prompt_version=${promptVersion}`,
        { waitUntil: 'domcontentloaded' });
      await ui(page.getByText(promptName, { exact: true }).first()).toBeVisible();
      const experimentName = `${t.name}-experiment`;
      const nameField = page.getByRole('textbox', { name: /^Experiment name/ });
      await ui(nameField).not.toHaveValue('');
      await nameField.fill(experimentName);
      await ui(nameField).toHaveValue(experimentName);
      await page.getByTestId('wizard-continue').click();
      await page.getByTestId('wizard-continue').click();
      await page.getByRole('button', { name: 'Add evaluation', exact: true }).click();
      await page.getByPlaceholder('Search evaluations...').fill(t.name);
      await ui(page.getByRole('progressbar', { name: 'Searching evaluations' })).toHaveCount(0);
      const templateRow = page.getByRole('row').filter({ has: page.getByText(t.name, { exact: true }) });
      await ui(templateRow).toHaveCount(1);
      await templateRow.getByRole('button', { name: 'Add', exact: true }).click();
      // EvaluationStepExperimentCreation.jsx: Add attaches the card; its pencil opens the editor.
      const card = page.getByTestId(`eval-card-${t.name}`);
      await ui(card).toBeVisible();
      await card.getByRole('button', { name: 'Edit evaluation', exact: true }).click();
      await ui(versionChoice(page)).toHaveText('Default version');
      await expectMenu(page, ['Default version', 'V3 (default)', 'V2', 'V1']);
      await pick(page, 'V1');
      await ui(updateButton(page)).toBeEnabled();
      await updateButton(page).click();
      // The card's chips (exp.new.evalCardChips) name the pin: allowNaModel.js versionPinOf.
      const chips = page.getByTestId('eval-card-chips').filter({ visible: true });
      await ui(chips).toHaveCount(1);
      await ui(chips.getByText('V1 · pinned', { exact: true })).toBeVisible();
      await page.getByTestId('wizard-continue').click();
      await assertStack();
      const run = page.getByRole('button', { name: /^Run experiment/ });
      await ui(run).toBeEnabled();
      const [created] = await Promise.all([
        page.waitForResponse(r => new URL(r.url()).pathname === EXPERIMENTS && r.request().method() === 'POST', { timeout: UI_READY }),
        run.click(),
      ]);
      expect(created.status()).toBe(200);
      expectScoped(created, t);
      const wire = created.request().postDataJSON();
      expect(wire.dataset_id).toBe(expDatasetId);
      expect(wire.user_eval_metrics).toHaveLength(1);
      expect(wire.user_eval_metrics[0]).toMatchObject({ template_id: t.templateId });
      expect(wire.user_eval_metrics[0].config.version_pin, 'CHOICE_NEG_CARD').toBe(EXPECTED_CARD_PIN);
      expect(wire.user_eval_metrics[0]).not.toHaveProperty('pinned_version_id');
      const experimentId = /experiment_id=([0-9a-f-]{36})/.exec((await created.json()).result)?.[1];
      expect(experimentId).toBeTruthy();
      // experiments.py _create_eval_metrics_inline: resolve_eval_pin -> UserEvalMetric.pinned_version.
      const metrics = await probe.pg<{ template_id: string; pinned_version_id: string | null; version_pin: string | null;
        organization_id: string; workspace_id: string }>(
        `SELECT m.template_id,m.pinned_version_id,m.config->>'version_pin' AS version_pin,m.organization_id,m.workspace_id
         FROM model_hub_userevalmetric m
         JOIN model_hub_experimentstable_user_eval_template_ids link ON link.userevalmetric_id=m.id
         WHERE link.experimentstable_id=$1 AND NOT m.deleted`, [experimentId]);
      expect(metrics).toEqual([{ template_id: t.templateId, pinned_version_id: t.versions[0].id, version_pin: 'v1',
        organization_id: t.actor.organizationId, workspace_id: t.actor.workspaceId }]);
      const detail = await call<Envelope<{ snapshot_dataset_id: string;
        user_eval_metrics: Array<{ pinned_version_id: string | null; pinned_version_number: number | null }> }>>(
        t.actor, 'GET', `${EXPERIMENTS}${experimentId}/`);
      expect(detail.result.user_eval_metrics.map(m => [m.pinned_version_id, m.pinned_version_number]))
        .toEqual([[t.versions[0].id, 1]]);
      const scores = () => probe.pg<{ value: string; status: string }>(
        `SELECT c.value,c.status FROM model_hub_cell c JOIN model_hub_column col ON col.id=c.column_id
         JOIN model_hub_experimentstable e ON e.snapshot_dataset_id=c.dataset_id
         WHERE e.id=$1 AND NOT c.deleted AND NOT col.deleted AND col.source='experiment_evaluation'`, [experimentId]);
      await expect.poll(async () => (await scopes.send<Envelope<{ status: string }>>(t.actor, 'GET',
        `${EXPERIMENTS}${experimentId}/`)).body.result.status, RUNS).toBe('Completed');
      await expect.poll(async () => (await scores()).map(c => c.status), RUNS).toEqual(['pass']);
      const judgedCells = await scores();
      const reasons = await probe.pg<{ value: string }>(
        `SELECT c.value FROM model_hub_cell c JOIN model_hub_experimentstable e ON e.snapshot_dataset_id=c.dataset_id
         WHERE e.id=$1 AND NOT c.deleted AND c.value IN ($2,$3) ORDER BY c.value`,
        [experimentId, `historic-${t.tag}`, `newest-${t.tag}`]);
      await attach(`experiment-${t.name}`, { experimentId, wire, metrics, detail: detail.result, judgedCells, reasons });
      expect(judgedCells.map(c => c.value)).toEqual([expect.stringContaining('0.2')]);
      expect(judgedCells.map(c => c.value)).not.toEqual([expect.stringContaining('0.9')]);
      expect(reasons.map(c => c.value)).toEqual([`historic-${t.tag}`]);
      // resolve_eval_pin: a label that names no saved version is refused before anything is written.
      const refusedName = `${experimentName}-refused`;
      const refused = await scopes.send(t.actor, 'POST', EXPERIMENTS, { ...wire, name: refusedName,
        user_eval_metrics: [{ ...wire.user_eval_metrics[0], config: { ...wire.user_eval_metrics[0].config, version_pin: 'v9' } }] });
      expect(refused.status).toBe(400);
      expect(await probe.pg('SELECT id FROM model_hub_experimentstable WHERE name=$1 AND dataset_id=$2',
        [refusedName, expDatasetId])).toEqual([]);
    });
    await context.close();
  }

  const before = { a: await stored(a), b: await stored(b), va: await versionRows(a), vb: await versionRows(b) };
  const answered: Record<string, Record<string, number>> = {};
  for (const [own, foreign] of [[a, b], [b, a]] as const) {
    const attempts = {
      readVersions: await scopes.send(own.actor, 'GET', templatePath(foreign.templateId, 'versions')),
      saveVersion: await scopes.send(own.actor, 'POST', templatePath(foreign.templateId, 'versions/create'), {}),
      listEvaluations: await scopes.send(own.actor, 'GET', datasetPath(foreign.datasetId, 'get_evals_list'),
        undefined, { eval_type: 'user' }),
      changeEvaluation: await scopes.send(own.actor, 'POST',
        datasetPath(foreign.datasetId, `edit_and_run_user_eval/${foreign.bindings.pinned}`), {
          template_id: foreign.templateId, pinned_version_id: foreign.versions[0].id, run: false,
          config: { mapping: { output: foreign.columnId }, config: {} },
        }),
    };
    for (const [key, res] of Object.entries(attempts)) {
      expect(EXPECTED_FOREIGN_TARGET, `${key} answered ${res.status}`).toContain(res.status);
      const text = JSON.stringify(res.body);
      for (const value of [foreign.name, ...foreign.versions.map(v => v.id)]) expect(text).not.toContain(value);
    }
    const pinForeign = await scopes.send(own.actor, 'POST',
      datasetPath(own.datasetId, `edit_and_run_user_eval/${own.bindings.unpinned}`), {
        template_id: own.templateId, pinned_version_id: foreign.versions[0].id, run: false,
        config: { mapping: { output: own.columnId }, config: {} },
      });
    expect(pinForeign.status, 'CHOICE_NEG_TENANCY').toBe(EXPECTED_PIN_REFUSAL);
    answered[own.name] = { ...Object.fromEntries(Object.entries(attempts).map(([k, r]) => [k, r.status])),
      pinForeignVersion: pinForeign.status };
  }
  await attach('tenant-refusals', answered);
  expect({ a: await stored(a), b: await stored(b), va: await versionRows(a), vb: await versionRows(b) }).toEqual(before);
  await attach('tenant-receipts', scopes.receipts);
});
