import { randomUUID } from 'node:crypto';
import type { Page } from '@playwright/test';
import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { inspectManagedMock, registerMockModel } from '../../lib/managed-mock';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// model_hub/urls.py and contracts.py: dataset and template mutation wires.
const CREATE_DATASET = '/model-hub/develops/create-empty-dataset/';
const CREATE_TEMPLATE = '/model-hub/eval-templates/create-v2/';
const datasetPath = (id: string, action: string) => `/model-hub/develops/${id}/${action}/`;
const templatePath = (id: string, action: string) => `/model-hub/eval-templates/${id}/${action}/`;
const UI_READY = 60_000;
// Expectation-only perturbations, never used to construct seeded inputs or requests.
const EXPECTED_PIN_NUMBER = 1; // PIN_NEG_STORAGE
const EXPECTED_SCORE = 0.2; // PIN_NEG_EXECUTION
const EXPECTED_FOREIGN = 400; // PIN_NEG_TENANCY

type Envelope<T> = { result: T };
type Version = { id: string; version_number: number; is_default: boolean };
type Binding = { id: string; name: string; pinned_version_id: string | null; status: string; column_id: string | null };
type Tenant = { actor: ScopeActor; name: string; datasetId: string; templateId: string;
  rowId: string; columnId: string; bindingId: string; versions: Version[]; value: string };

test('EVAL-E2E-009: stored dataset pins survive the locked picker and execute the historical choice score', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-009', area: 'evals',
    userGoal: 'Keep my dataset evaluation on its stored version when saving settings in the locked picker and run its historical definition without changing another organization',
    steps: [
      'create synthetic inputs and two saved choice-evaluation versions in each of two organizations',
      'seed V1, latest and default pins through the scoped API, then display each with no-op Update disabled and no version selector',
      'reopen each stored pin without creating versions, then explicitly save one new version through Save version',
      'restore V1 through the scoped API, verify the locked picker preserves it, and use the native Run action',
      'read the historical score from the public dataset table and PostgreSQL, then refuse foreign versions and datasets',
    ],
    backendChecks: [
      'real browser mutations and scoped API/PostgreSQL readback retain API-seeded pins without extra versions; only explicit Save version creates one',
      'native execution with a labelled loopback judge STANDIN persists the historical choice score and original input for each tenant',
      'foreign version and dataset requests are refused without changing either tenant binding',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Provision/setup 120 + two tenants * (6 UI60 + EVAL90 + CDC180) + headroom.
  test.setTimeout(1_440_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const ui = expect.configure({ timeout: UI_READY });
  const attach = (name: string, value: unknown) => testInfo.attach(name, {
    contentType: 'application/json', body: JSON.stringify(value, (_key, item) =>
      typeof item === 'string' && /^[a-f0-9]{64}$/.test(item) ? `sha256:${item}` : item),
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
  const list = async (t: Tenant) => {
    const response = await scopes.send<Envelope<{ evals: Binding[] }>>(t.actor, 'GET',
      datasetPath(t.datasetId, 'get_evals_list'), undefined, { eval_type: 'user' });
    expect(response.status).toBe(200);
    expect(response.body.result.evals.map(b => b.id)).toEqual([t.bindingId]);
    return response.body.result.evals[0];
  };
  const storage = (t: Tenant) => probe.pg<{ id: string; pinned_version_id: string | null;
    organization_id: string; workspace_id: string }>(
    'SELECT id,pinned_version_id,organization_id,workspace_id FROM model_hub_userevalmetric WHERE dataset_id=$1 AND NOT deleted',
    [t.datasetId]);
  async function seed(actor: ScopeActor, label: string): Promise<Tenant> {
    const name = `e2e-pin-${label}-${suffix}`;
    const d = await call<Envelope<{ dataset_id: string }>>(actor, 'POST', CREATE_DATASET, { new_dataset_name: name });
    const datasetId = d.result.dataset_id;
    await call(actor, 'POST', datasetPath(datasetId, 'add_static_column'), { new_column_name: 'output', column_type: 'text' });
    const rowId = randomUUID();
    const value = `input-${label}-${suffix}`;
    await call(actor, 'POST', datasetPath(datasetId, 'add_rows'), {
      rows: [{ id: rowId, cells: [{ column_name: 'output', value }] }],
    });
    const [column] = await probe.pg<{ id: string }>(
      "SELECT id FROM model_hub_column WHERE dataset_id=$1 AND name='output' AND NOT deleted", [datasetId]);
    const template = await call<Envelope<{ id: string }>>(actor, 'POST', CREATE_TEMPLATE, {
      name, eval_type: 'llm', model: 'gpt-4o', output_type: 'deterministic',
      instructions: 'Reply with exactly this JSON: {"result":"saved","explanation":"historic {{output}}"}',
      choice_scores: { saved: 0.2 },
      check_internet: false, error_localizer_enabled: false,
    });
    const templateId = template.result.id;
    await call(actor, 'PUT', templatePath(templateId, 'update'), {
      choice_scores: { saved: 0.9 },
      instructions: 'Reply with exactly this JSON: {"result":"saved","explanation":"newest {{output}}"}',
    });
    await call(actor, 'POST', templatePath(templateId, 'versions/create'), {});
    const v = await call<Envelope<{ versions: Version[] }>>(actor, 'GET', templatePath(templateId, 'versions'));
    const versions = v.result.versions.sort((a, b) => a.version_number - b.version_number);
    expect(versions.map(v => v.version_number)).toEqual([1, 2]);
    await call(actor, 'POST', datasetPath(datasetId, 'add_user_eval'), {
      name, template_id: templateId, model: 'gpt-4o', run: false,
      pinned_version_id: versions[0].id, config: { mapping: { output: column.id }, config: {} },
    });
    const [binding] = await probe.pg<{ id: string }>(
      'SELECT id FROM model_hub_userevalmetric WHERE dataset_id=$1 AND NOT deleted', [datasetId]);
    return { actor, name, datasetId, templateId, rowId, columnId: column.id, bindingId: binding.id, versions, value };
  }
  const a = await seed(scopes.ownerA, 'a');
  const b = await seed(scopes.ownerB, 'b');
  await attach('seeded-identities', [a, b].map(({ actor, ...rest }) => ({ ...rest,
    organizationId: actor.organizationId, workspaceId: actor.workspaceId })));

  async function openList(page: Page, t: Tenant) {
    await page.goto(`/dashboard/develop/${t.datasetId}`, { waitUntil: 'domcontentloaded' });
    const more = page.locator('[data-action-bar="grid"]').getByRole('button', { name: 'More actions', exact: true });
    const evaluate = page.getByRole('menuitem', { name: /^Evaluate/ });
    await expect(async () => {
      if (await evaluate.isVisible()) await page.keyboard.press('Escape');
      await more.click();
      await expect(evaluate).toBeEnabled({ timeout: 2_000 });
    }).toPass({ timeout: UI_READY });
    await evaluate.click();
    await ui(page.getByTestId('saved-eval-row').filter({ visible: true })).toHaveCount(1);
  }
  const evalRow = (page: Page, t: Tenant) => page.getByTestId('saved-eval-row')
    .filter({ visible: true }).filter({ has: page.getByText(t.name, { exact: true }) });
  // Pins are setup state, not a user-selectable control under the picker lock.
  const seedPin = (t: Tenant, pin: string | null) => call(t.actor, 'POST',
    datasetPath(t.datasetId, `edit_and_run_user_eval/${t.bindingId}`), {
      template_id: t.templateId, pinned_version_id: pin, run: false,
      config: { mapping: { output: t.columnId }, config: {} },
    });
  const expectLockedPin = async (page: Page, number: number) => {
    await ui(page.getByText(new RegExp(`^Version label: V?${number}$`))).toBeVisible();
    await ui(page.getByRole('combobox', { name: 'Saved eval version' })).toHaveCount(0);
    await ui(page.getByRole('button', { name: 'Save version', exact: true })).toBeEnabled();
  };
  const expectNoopPin = async (page: Page, t: Tenant, pin: string | null) => {
    const bindingState = () => probe.pg<{ config: unknown; model: string; error_localizer: boolean; pinned_version_id: string | null }>(
      'SELECT config,model,error_localizer,pinned_version_id FROM model_hub_userevalmetric WHERE id=$1', [t.bindingId]);
    const before = await bindingState();
    await ui(page.getByRole('button', { name: 'Update Evaluation', exact: true })).toBeDisabled();
    expect(await bindingState()).toEqual(before);
    expect((await list(t)).pinned_version_id).toBe(pin);
    expect(before[0].pinned_version_id).toBe(pin);
  };
  const updatePin = async (page: Page, t: Tenant, pin: string | null) => {
    const path = datasetPath(t.datasetId, `edit_and_run_user_eval/${t.bindingId}`);
    const responsePromise = page.waitForResponse(r => new URL(r.url()).pathname === path && r.request().method() === 'POST');
    await page.getByRole('button', { name: 'Update Evaluation', exact: true }).click();
    const response = await responsePromise;
    expect(response.status()).toBe(200);
    expect(response.request().headers()['x-organization-id']).toBe(t.actor.organizationId);
    expect(response.request().headers()['x-workspace-id']).toBe(t.actor.workspaceId);
    expect(response.request().postDataJSON().pinned_version_id).toBe(pin);
    expect((await list(t)).pinned_version_id).toBe(pin);
    expect(await storage(t)).toEqual([{ id: t.bindingId, pinned_version_id: pin,
      organization_id: t.actor.organizationId, workspace_id: t.actor.workspaceId }]);
  };

  for (const t of [a, b]) {
    const context = await scopes.openContext(browser, t.actor);
    await context.route(url => /^https?:$/.test(url.protocol) &&
      !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname), route => route.abort());
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    // The API resolves latest to a concrete ID. Reopening without an edit
    // preserves that ID (or explicit null) without sending an Update request.
    for (const stored of [
      { pin: t.versions[0].id, shown: 1 },
      { pin: 'latest', shown: 2 },
      { pin: null, shown: 2 },
      { pin: t.versions[0].id, shown: 1 },
    ]) {
      await seedPin(t, stored.pin);
      const expectedPin = stored.pin === 'latest' ? t.versions[1].id : stored.pin;
      expect((await list(t)).pinned_version_id).toBe(expectedPin);
      await openList(page, t);
      await evalRow(page, t).getByRole('button', { name: 'Edit mapping & config', exact: true }).click();
      await expectLockedPin(page, stored.shown);
      await expectNoopPin(page, t, expectedPin);
      await openList(page, t);
      await evalRow(page, t).getByRole('button', { name: 'Edit mapping & config', exact: true }).click();
      await expectLockedPin(page, stored.shown);
      await openList(page, t);
    }
    // Creating a saved version is a separate, explicit action; API setup and
    // no-op browser reopens above must not create any. Exercise the real Save version.
    const countBeforeSave = await probe.pg<{ n: number }>(
      'SELECT count(*)::int AS n FROM model_hub_eval_template_version WHERE eval_template_id=$1 AND NOT deleted', [t.templateId]);
    expect(countBeforeSave).toEqual([{ n: 2 }]);
    await seedPin(t, 'latest');
    await openList(page, t);
    await evalRow(page, t).getByRole('button', { name: 'Edit mapping & config', exact: true }).click();
    await expectLockedPin(page, 2);
    const savedResponse = page.waitForResponse(r => new URL(r.url()).pathname === templatePath(t.templateId, 'versions/create') && r.request().method() === 'POST');
    await page.getByRole('button', { name: 'Save version', exact: true }).click();
    const saved = await savedResponse;
    expect(saved.status()).toBe(200);
    const createdVersion: Version = (await saved.json()).result;
    expect(createdVersion.version_number).toBe(3);
    await expectLockedPin(page, 3);
    await updatePin(page, t, createdVersion.id);
    await seedPin(t, t.versions[0].id);
    await openList(page, t);
    await evalRow(page, t).getByRole('button', { name: 'Edit mapping & config', exact: true }).click();
    await expectLockedPin(page, 1);
    await expectNoopPin(page, t, t.versions[0].id);
    await openList(page, t);
    const versions = await probe.pg<Version>(
      'SELECT id,version_number FROM model_hub_eval_template_version WHERE eval_template_id=$1 AND NOT deleted ORDER BY version_number',
      [t.templateId]);
    expect(versions.map(v => v.version_number)).toEqual([1, 2, 3]);
    expect(versions.slice(0, 2).map(v => v.id)).toEqual(t.versions.map(v => v.id));
    expect((await storage(t))[0].pinned_version_id, 'PIN_NEG_STORAGE').toBe(t.versions[EXPECTED_PIN_NUMBER - 1].id);
    await attach(`saved-${t.name}`, { versions, binding: await storage(t), api: await list(t) });

    await assertStack();
    const runPath = datasetPath(t.datasetId, 'start_evals_process');
    const started = page.waitForResponse(r => new URL(r.url()).pathname === runPath && r.request().method() === 'POST');
    await evalRow(page, t).getByRole('button', { name: 'Run', exact: true }).click();
    await ui(evalRow(page, t).getByText('Run this evaluation on all rows?', { exact: true })).toBeVisible();
    await evalRow(page, t).getByRole('button', { name: 'Run', exact: true }).filter({ hasText: 'Run' }).click();
    const response = await started;
    expect(response.status()).toBe(200);
    expect(response.request().postDataJSON()).toEqual({ user_eval_ids: [t.bindingId] });
    await expect.poll(async () => (await list(t)).status, POLL.EVAL_RESULT).toBe('Completed');
    const cells = await probe.pg<{ value: string; status: string; source: string }>(
      `SELECT c.value,c.status,col.source FROM model_hub_cell c JOIN model_hub_column col ON col.id=c.column_id
       WHERE c.dataset_id=$1 AND c.row_id=$2 AND NOT c.deleted AND NOT col.deleted ORDER BY col.source`, [t.datasetId, t.rowId]);
    const result = cells.find(c => c.source === 'evaluation');
    expect(result?.status).toBe('pass');
    expect(result?.value, 'PIN_NEG_EXECUTION').toBe(`{'score': ${EXPECTED_SCORE}, 'choice': 'saved'}`);
    expect(cells.find(c => c.source === 'evaluation_reason')?.value).toBe(`historic ${t.value}`);
    const inputs = await probe.pg<{ value: string }>(
      'SELECT value FROM model_hub_cell WHERE column_id=$1 AND row_id=$2 AND NOT deleted', [t.columnId, t.rowId]);
    expect(inputs).toEqual([{ value: t.value }]);
    const binding = await list(t);
    const table = await call<Envelope<{ table: Array<Record<string, unknown>> }>>(t.actor, 'GET', datasetPath(t.datasetId, 'get-dataset-table'));
    expect(table.result.table).toHaveLength(1);
    expect(table.result.table[0][binding.column_id!]).toMatchObject({ cell_value: result!.value, status: 'pass' });
    await attach(`executed-${t.name}`, { cells, binding, table });
    await context.close();
  }
  const beforeA = await storage(a); const beforeB = await storage(b);
  for (const [own, foreign] of [[a, b], [b, a]]) {
    const denied = await scopes.send(own.actor, 'POST', datasetPath(own.datasetId, `edit_and_run_user_eval/${own.bindingId}`), {
      template_id: own.templateId, pinned_version_id: foreign.versions[0].id, run: false,
      config: { mapping: { output: own.columnId }, config: {} },
    });
    expect(denied.status, 'PIN_NEG_TENANCY').toBe(EXPECTED_FOREIGN);
    const foreignDataset = await scopes.send(own.actor, 'GET', datasetPath(foreign.datasetId, 'get_evals_list'), undefined, { eval_type: 'user' });
    expect(foreignDataset.status).toBe(404);
  }
  expect(await storage(a)).toEqual(beforeA); expect(await storage(b)).toEqual(beforeB);
  await attach('tenant-refusals', scopes.receipts);
});
