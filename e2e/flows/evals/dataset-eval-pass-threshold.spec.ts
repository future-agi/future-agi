import { randomUUID } from 'node:crypto';
import type { BrowserContext, Page } from '@playwright/test';
import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// model_hub/urls.py and contracts.py: dataset, binding and template wires.
const CREATE_DATASET = '/model-hub/develops/create-empty-dataset/';
const CREATE_TEMPLATE = '/model-hub/eval-templates/create-v2/'; // EvalTemplateCreateV2RequestSerializer
const datasetPath = (id: string, action: string) => `/model-hub/develops/${id}/${action}/`;
const templatePath = (id: string, action: string) => `/model-hub/eval-templates/${id}/${action}/`;
const LIST_QUERY = { eval_type: 'user' };
// Never executed by this flow (every binding is saved with run: false); a
// non-Turing name passes the OSS model gate on add and edit.
const MODEL = 'gpt-4o-mini';
const UI_READY = 60_000;
// Expectation-only fallibility anchors, never used to build a request or a seeded value.
const EXPECTED_BAR = 0.7; // BAR_NEG_STORAGE: the bar the binding stores
const EXPECTED_VERSIONS = 2; // BAR_NEG_VERSION: saved versions after a bar-only update
const EXPECTED_FOREIGN = [403, 404]; // BAR_NEG_TENANCY: another organization's reads and writes

type Envelope<T> = { result: T };
type Version = { id: string; version_number: number; is_default: boolean };
type ListItem = { id: string; name: string; pinned_version_id: string | null; version: string | null;
  pass_threshold: number | null; run_config?: { pass_threshold?: number } };
type Stored = { id: string; pinned_version_id: string | null; bar: string | null;
  organization_id: string; workspace_id: string };
type Tenant = { actor: ScopeActor; name: string; datasetId: string; templateId: string; columnId: string;
  bindingId: string; versions: Version[] };

test('EVAL-E2E-017: the pass threshold set in a dataset evaluation editor is saved on that evaluation and keeps its version', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-017', area: 'evals',
    userGoal: 'Set the pass threshold of an evaluation saved on my dataset, see it as the pass rule the dataset applies and keep its recorded version, for my organization only',
    steps: [
      'through the public API, give each of two organizations a dataset, a Scoring evaluation with two saved versions and that evaluation saved on the dataset pinned to its first version',
      'open the dataset Evaluate drawer, read the pass rule, open the evaluation editor and move the pass threshold from 50% to 70%',
      'choose Update Evaluation and read the saved evaluation through the public API and PostgreSQL',
      'read the pass rule in the drawer, reopen the editor and verify Update Evaluation is disabled without changes and sends no update request',
      'as the other organization, try to read the first organization\'s dataset evaluations and versions and to change its pass threshold',
    ],
    backendChecks: [
      'the update request carries the selected version and the 0.7 threshold under the actor organization and workspace',
      'PostgreSQL stores 0.7 in the saved evaluation\'s run settings, the pin stays on the first version, no version is added and the default version and evaluation threshold are unchanged',
      'the dataset eval list reports a 0.70 pass rule on the pinned version, and the reopened editor shows 70% with Update disabled, no POST and the complete stored binding, threshold and pin unchanged',
      'the other organization is refused and nothing of the first organization changes',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Provision/setup 120 + two tenants * (8 UI60 + API reads) + headroom.
  test.setTimeout(1_200_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const ui = expect.configure({ timeout: UI_READY });
  const attach = (name: string, value: unknown) => testInfo.attach(name, {
    contentType: 'application/json', body: JSON.stringify(value),
  });
  const call = async <T,>(actor: ScopeActor, method: string, path: string, body?: unknown) => {
    const response = await scopes.send<T>(actor, method, path, body);
    expect(response.status, `${method} ${path}: ${JSON.stringify(response.body).slice(0, 400)}`).toBe(200);
    return response.body;
  };
  const listed = async (t: Tenant) => {
    const response = await scopes.send<Envelope<{ evals: ListItem[] }>>(t.actor, 'GET',
      datasetPath(t.datasetId, 'get_evals_list'), undefined, LIST_QUERY);
    expect(response.status).toBe(200);
    expect(response.body.result.evals.map(item => item.id)).toEqual([t.bindingId]);
    return response.body.result.evals[0];
  };
  const stored = (t: Tenant) => probe.pg<Stored>(
    `SELECT id, pinned_version_id, config->'run_config'->>'pass_threshold' AS bar, organization_id, workspace_id
     FROM model_hub_userevalmetric WHERE dataset_id=$1 AND NOT deleted`, [t.datasetId]);
  const versionsOf = (t: Tenant) => probe.pg<{ version_number: number; is_default: boolean }>(
    `SELECT version_number, is_default FROM model_hub_eval_template_version
     WHERE eval_template_id=$1 AND NOT deleted ORDER BY version_number`, [t.templateId]);
  const templateBar = async (t: Tenant) => Number((await probe.pg<{ pass_threshold: number | null }>(
    'SELECT pass_threshold FROM model_hub_evaltemplate WHERE id=$1', [t.templateId]))[0].pass_threshold);

  async function seed(actor: ScopeActor, label: string): Promise<Tenant> {
    const name = `e2e-bar-${label}-${suffix}`;
    const d = await call<Envelope<{ dataset_id: string }>>(actor, 'POST', CREATE_DATASET, { new_dataset_name: name });
    const datasetId = d.result.dataset_id;
    await call(actor, 'POST', datasetPath(datasetId, 'add_static_column'), { new_column_name: 'output', column_type: 'text' });
    await call(actor, 'POST', datasetPath(datasetId, 'add_rows'), {
      rows: [{ id: randomUUID(), cells: [{ column_name: 'output', value: `reply-${label}` }] }],
    });
    const [column] = await probe.pg<{ id: string }>(
      "SELECT id FROM model_hub_column WHERE dataset_id=$1 AND name='output' AND NOT deleted", [datasetId]);
    const template = await call<Envelope<{ id: string }>>(actor, 'POST', CREATE_TEMPLATE, {
      name, eval_type: 'llm', model: MODEL, output_type: 'percentage', pass_threshold: 0.5,
      instructions: 'Score from 0 to 1 how polite this reply is: {{output}}',
      check_internet: false, error_localizer_enabled: false,
    });
    const templateId = template.result.id;
    await call(actor, 'PUT', templatePath(templateId, 'update'), {
      instructions: 'Score from 0 to 1 how polite and complete this reply is: {{output}}',
    });
    await call(actor, 'POST', templatePath(templateId, 'versions/create'), {});
    const v = await call<Envelope<{ versions: Version[] }>>(actor, 'GET', templatePath(templateId, 'versions'));
    const versions = v.result.versions.sort((x, y) => x.version_number - y.version_number);
    expect(versions.map(x => [x.version_number, x.is_default])).toEqual([[1, false], [2, true]]);
    await call(actor, 'POST', datasetPath(datasetId, 'add_user_eval'), {
      name, template_id: templateId, model: MODEL, run: false,
      pinned_version_id: versions[0].id, config: { mapping: { output: column.id }, config: {} },
    });
    const [binding] = await probe.pg<{ id: string }>(
      'SELECT id FROM model_hub_userevalmetric WHERE dataset_id=$1 AND NOT deleted', [datasetId]);
    return { actor, name, datasetId, templateId, columnId: column.id, bindingId: binding.id, versions };
  }
  const a = await seed(scopes.ownerA, 'a');
  const b = await seed(scopes.ownerB, 'b');
  await attach('seeded-identities', [a, b].map(({ actor, ...rest }) => ({ ...rest,
    organizationId: actor.organizationId, workspaceId: actor.workspaceId })));

  async function lockToLoopback(context: BrowserContext) {
    const guard = await import('./editor-proof/browser-guard');
    await guard.lockToLoopback(context, testInfo.outputPath('browser-network'));
  }
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
  const versionSelect = (page: Page) => page.getByRole('combobox', { name: /^(Saved eval version|Recorded version)$/ });
  const bar = (page: Page) => page.getByRole('slider');
  async function openEditor(page: Page, t: Tenant) {
    await evalRow(page, t).getByRole('button', { name: 'Edit mapping & config', exact: true }).click();
    await ui(versionSelect(page)).toHaveCount(0);
    const header = page.getByRole('heading', { name: t.name, exact: true })
      .filter({ visible: true }).locator('xpath=..');
    await ui(header.getByText('Version label: V1', { exact: true })).toBeVisible();
    await ui(bar(page)).toHaveCount(1);
  }
  async function update(page: Page, t: Tenant) {
    const path = datasetPath(t.datasetId, `edit_and_run_user_eval/${t.bindingId}`);
    const responsePromise = page.waitForResponse(r => new URL(r.url()).pathname === path && r.request().method() === 'POST');
    await page.getByRole('button', { name: 'Update Evaluation', exact: true }).click();
    const response = await responsePromise;
    expect(response.status()).toBe(200);
    expect(response.request().headers()['x-organization-id']).toBe(t.actor.organizationId);
    expect(response.request().headers()['x-workspace-id']).toBe(t.actor.workspaceId);
    return response.request().postDataJSON();
  }

  for (const t of [a, b]) {
    const context = await scopes.openContext(browser, t.actor);
    await lockToLoopback(context);
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    await openList(page, t);
    await ui(evalRow(page, t).getByTestId('saved-eval-definition'))
      .toHaveText('↑ Higher is better · Pass ≥ 0.50 · v1 · pinned');
    expect(Number((await listed(t)).pass_threshold)).toBeCloseTo(0.5, 5);

    await openEditor(page, t);
    await ui(bar(page)).toHaveAttribute('aria-valuenow', '50');
    // OutputTypeConfig.jsx Pass Threshold slider (MUI, keyboard step 1).
    await bar(page).focus();
    for (let i = 0; i < 20; i += 1) await page.keyboard.press('ArrowRight');
    await ui(bar(page)).toHaveAttribute('aria-valuenow', '70');
    const sent = await update(page, t);
    expect(sent.pinned_version_id).toBe(t.versions[0].id);
    expect(sent.config?.run_config?.pass_threshold).toBeCloseTo(0.7, 5);

    const item = await listed(t);
    expect(item.pinned_version_id).toBe(t.versions[0].id);
    expect(item.version).toBe('v1');
    expect(Number(item.pass_threshold)).toBeCloseTo(0.7, 5);
    const rows = await stored(t);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ id: t.bindingId, pinned_version_id: t.versions[0].id,
      organization_id: t.actor.organizationId, workspace_id: t.actor.workspaceId });
    expect(Number(rows[0].bar), 'BAR_NEG_STORAGE').toBeCloseTo(EXPECTED_BAR, 5);
    const versions = await versionsOf(t);
    expect(versions.length, 'BAR_NEG_VERSION').toBe(EXPECTED_VERSIONS);
    expect(versions).toEqual([{ version_number: 1, is_default: false }, { version_number: 2, is_default: true }]);
    expect(await templateBar(t)).toBeCloseTo(0.5, 5);
    await attach(`updated-${t.name}`, { sent, item, rows, versions });

    await openList(page, t);
    await ui(evalRow(page, t).getByTestId('saved-eval-definition'))
      .toHaveText('↑ Higher is better · Pass ≥ 0.70 · v1 · pinned');
    const bindingState = () => probe.pg<{ config: unknown; model: string; error_localizer: boolean; pinned_version_id: string | null }>(
      'SELECT config,model,error_localizer,pinned_version_id FROM model_hub_userevalmetric WHERE id=$1', [t.bindingId]);
    const beforeNoop = await bindingState();
    const posts: string[] = [];
    const updatePath = datasetPath(t.datasetId, `edit_and_run_user_eval/${t.bindingId}`);
    const recordUpdate = (request: import('@playwright/test').Request) => {
      if (new URL(request.url()).pathname === updatePath && request.method() === 'POST') posts.push(request.url());
    };
    page.on('request', recordUpdate);
    await openEditor(page, t);
    await ui(bar(page)).toHaveAttribute('aria-valuenow', '70');
    await ui(page.getByRole('button', { name: 'Update Evaluation', exact: true })).toBeDisabled();
    const after = await stored(t);
    expect(after).toEqual(rows);
    expect(await bindingState()).toEqual(beforeNoop);
    expect(beforeNoop[0].pinned_version_id).toBe(t.versions[0].id);
    const unchanged = await listed(t);
    expect(unchanged.pinned_version_id).toBe(t.versions[0].id);
    expect(Number(unchanged.pass_threshold)).toBeCloseTo(EXPECTED_BAR, 5);
    expect(await versionsOf(t)).toEqual(versions);
    expect(await templateBar(t)).toBeCloseTo(0.5, 5);
    expect(posts).toEqual([]);
    page.off('request', recordUpdate);
    await context.close();
  }

  const beforeA = await stored(a); const beforeB = await stored(b);
  for (const [own, foreign] of [[a, b], [b, a]] as const) {
    const refusals = {
      list: await scopes.send(own.actor, 'GET', datasetPath(foreign.datasetId, 'get_evals_list'), undefined, LIST_QUERY),
      versions: await scopes.send(own.actor, 'GET', templatePath(foreign.templateId, 'versions')),
      bar: await scopes.send(own.actor, 'POST', datasetPath(foreign.datasetId, `edit_and_run_user_eval/${foreign.bindingId}`), {
        template_id: foreign.templateId, model: MODEL, run: false, pinned_version_id: foreign.versions[0].id,
        config: { mapping: { output: foreign.columnId }, config: {}, run_config: { pass_threshold: 0.1 } },
      }),
    };
    for (const [key, res] of Object.entries(refusals)) {
      expect(EXPECTED_FOREIGN, `BAR_NEG_TENANCY ${key} answered ${res.status}`).toContain(res.status);
      const text = JSON.stringify(res.body);
      for (const value of [foreign.name, foreign.bindingId, ...foreign.versions.map(v => v.id)])
        expect(text).not.toContain(value);
    }
    await attach(`refusals-${own.name}`, Object.fromEntries(Object.entries(refusals).map(([k, r]) => [k, r.status])));
  }
  expect(await stored(a)).toEqual(beforeA); expect(await stored(b)).toEqual(beforeB);
  expect((await versionsOf(a)).length).toBe(2); expect((await versionsOf(b)).length).toBe(2);
  await attach('tenant-receipts', scopes.receipts);
});
