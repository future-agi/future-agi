import { randomUUID } from 'node:crypto';
import type { BrowserContext, Page } from '@playwright/test';
import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// model_hub/urls.py and contracts.py: dataset, binding and template wires.
const CREATE_DATASET = '/model-hub/develops/create-empty-dataset/';
const CREATE_TEMPLATE = '/model-hub/eval-templates/create-v2/'; // EvalTemplateCreateV2RequestSerializer
const datasetPath = (id: string, action: string) => `/model-hub/develops/${id}/${action}/`;
const templatePath = (id: string, action: string) => `/model-hub/eval-templates/${id}/${action}/`;
// Never executed by this flow (the binding is saved with run: false); a
// non-Turing name passes the OSS model gate on add.
const MODEL = 'gpt-4o-mini';
const UI_READY = 60_000;
// Expectation-only fallibility anchors, never used to build a request or a seeded value.
const EXPECTED_LATEST_BADGE = 'Version label: V1'; // HEADER_NEG_VERSION: legacy anchor name; lock retains the stored V1 badge
const EXPECTED_SAVED_VERSIONS = 3; // HEADER_NEG_STORAGE: saved versions after one Save version
const EXPECTED_FOREIGN = [403, 404]; // HEADER_NEG_TENANCY: another organization's reads and writes

type Envelope<T> = { result: T };
type Version = { id: string; version_number: number; is_default: boolean };
type Tenant = { actor: ScopeActor; name: string; datasetId: string; templateId: string; columnId: string;
  bindingId: string; versions: Version[] };

test('EVAL-E2E-018: the evaluation editor header names the saved version being configured', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-018', area: 'evals',
    userGoal: 'See which saved version of an evaluation I am configuring on my dataset, and whether I have unsaved changes, for my organization only',
    steps: [
      'through the public API, give each of two organizations a dataset and an evaluation with two saved versions, saved on the dataset pinned to the first',
      'open the evaluation editor from the dataset Evaluate drawer and read its header',
      'verify the version selector is absent and the stored version label remains, then move the pass threshold without saving',
      'reopen the editor, verify the version selector is absent and save a new version, then reopen it again',
      'as the other organization, try to read the first organization\'s evaluation, its versions and to save a version of it',
    ],
    backendChecks: [
      'the header names the evaluation and its type and shows the stored saved version, else the evaluation\'s current version from the detail read',
      'unsaved changes show as Edited and change nothing; the binding stays pinned to the first version',
      'Save version stores a third, default version under the actor organization and workspace; the header shows it and the detail read reports it as current',
      'the other organization is refused and nothing of the first organization changes',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Provision/setup 120 + two tenants * (10 UI60 + API reads) + headroom.
  test.setTimeout(1_500_000);
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
  const versionsOf = (t: Tenant) => probe.pg<{ version_number: number; is_default: boolean;
    organization_id: string | null }>(
    `SELECT version_number, is_default, organization_id FROM model_hub_eval_template_version
     WHERE eval_template_id=$1 AND NOT deleted ORDER BY version_number`, [t.templateId]);
  const pinOf = async (t: Tenant) => (await probe.pg<{ pinned_version_id: string | null }>(
    'SELECT pinned_version_id FROM model_hub_userevalmetric WHERE id=$1 AND NOT deleted', [t.bindingId]))
    .map(r => r.pinned_version_id);

  async function seed(actor: ScopeActor, label: string): Promise<Tenant> {
    const name = `e2e-header-${label}-${suffix}`;
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
  async function openEditor(page: Page, t: Tenant) {
    await page.goto(`/dashboard/develop/${t.datasetId}`, { waitUntil: 'domcontentloaded' });
    const more = page.locator('[data-action-bar="grid"]').getByRole('button', { name: 'More actions', exact: true });
    const evaluate = page.getByRole('menuitem', { name: /^Evaluate/ });
    await expect(async () => {
      if (await evaluate.isVisible()) await page.keyboard.press('Escape');
      await more.click();
      await expect(evaluate).toBeEnabled({ timeout: 2_000 });
    }).toPass({ timeout: UI_READY });
    await evaluate.click();
    const row = page.getByTestId('saved-eval-row').filter({ visible: true })
      .filter({ has: page.getByText(t.name, { exact: true }) });
    await ui(row).toHaveCount(1);
    await row.getByRole('button', { name: 'Edit mapping & config', exact: true }).click();
  }
  // EvalPickerConfigFull.jsx header: the evaluation name (subtitle1 heading),
  // VersionBadge, EvalTypeBadge and the Edited chip.
  const header = (page: Page, t: Tenant) =>
    page.getByRole('heading', { name: t.name, exact: true }).filter({ visible: true }).locator('xpath=..');
  const badge = (page: Page, t: Tenant) =>
    header(page, t).locator('.MuiChip-label').filter({ hasText: /^Version label: V\d+$/ });
  const versionSelect = (page: Page) => page.getByRole('combobox', { name: /^(Saved eval version|Recorded version)$/ });

  for (const t of [a, b]) {
    const context = await scopes.openContext(browser, t.actor);
    await lockToLoopback(context);
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    const detailPath = templatePath(t.templateId, 'detail');
    const detailRead = page.waitForResponse(r => new URL(r.url()).pathname === detailPath && r.ok());
    await openEditor(page, t);
    const detail = await (await detailRead).json() as Envelope<{ current_version: string }>;
    expect(detail.result.current_version).toBe('V2');
    await ui(header(page, t)).toBeVisible();
    await ui(badge(page, t)).toHaveText('Version label: V1');
    await ui(header(page, t).getByText('LLM', { exact: true })).toBeVisible();
    await ui(header(page, t).getByText('Edited', { exact: true })).toHaveCount(0);

    await ui(versionSelect(page)).toHaveCount(0);
    await ui(badge(page, t), 'HEADER_NEG_VERSION').toHaveText(EXPECTED_LATEST_BADGE);
    await ui(page.getByRole('option', { name: /^(Latest|Saved latest|V1|V2)/ })).toHaveCount(0);
    await ui(header(page, t).getByText('Edited', { exact: true })).toHaveCount(0);
    const slider = page.getByRole('slider');
    await ui(slider).toHaveCount(1);
    await slider.focus();
    await page.keyboard.press('ArrowRight');
    await ui(header(page, t).getByText('Edited', { exact: true })).toBeVisible();
    await ui(badge(page, t)).toHaveText('Version label: V1');
    // Nothing above saved anything.
    expect(await pinOf(t)).toEqual([t.versions[0].id]);
    expect((await versionsOf(t)).map(v => v.version_number)).toEqual([1, 2]);

    await openEditor(page, t);
    await ui(badge(page, t)).toHaveText('Version label: V1');
    await ui(header(page, t).getByText('Edited', { exact: true })).toHaveCount(0);
    await ui(versionSelect(page)).toHaveCount(0);
    await ui(badge(page, t)).toHaveText('Version label: V1');
    const saved = page.waitForResponse(r => new URL(r.url()).pathname === templatePath(t.templateId, 'versions/create')
      && r.request().method() === 'POST');
    await page.getByRole('button', { name: 'Save version', exact: true }).click();
    const response = await saved;
    expect(response.status()).toBe(200);
    expect(response.request().headers()['x-organization-id']).toBe(t.actor.organizationId);
    expect(response.request().headers()['x-workspace-id']).toBe(t.actor.workspaceId);
    expect((await response.json()).result.version_number).toBe(3);
    await ui(badge(page, t)).toHaveText('Version label: V3');
    await ui(versionSelect(page)).toHaveCount(0);
    const versions = await versionsOf(t);
    expect(versions.length, 'HEADER_NEG_STORAGE').toBe(EXPECTED_SAVED_VERSIONS);
    expect(versions).toEqual([1, 2, 3].map(n => ({ version_number: n, is_default: n === 3,
      organization_id: t.actor.organizationId })));
    const current = await call<Envelope<{ current_version: string }>>(t.actor, 'GET', detailPath);
    expect(current.result.current_version).toBe('V3');
    // Saving a version is not an Update of the dataset's evaluation.
    expect(await pinOf(t)).toEqual([t.versions[0].id]);

    await openEditor(page, t);
    await ui(badge(page, t)).toHaveText('Version label: V1');
    await ui(versionSelect(page)).toHaveCount(0);
    await ui(badge(page, t)).toHaveText('Version label: V1');
    await attach(`header-${t.name}`, { detail: detail.result, current: current.result, versions });
    await context.close();
  }

  const before = { a: await versionsOf(a), b: await versionsOf(b), pinA: await pinOf(a), pinB: await pinOf(b) };
  for (const [own, foreign] of [[a, b], [b, a]] as const) {
    const refusals = {
      detail: await scopes.send(own.actor, 'GET', templatePath(foreign.templateId, 'detail')),
      versions: await scopes.send(own.actor, 'GET', templatePath(foreign.templateId, 'versions')),
      save: await scopes.send(own.actor, 'POST', templatePath(foreign.templateId, 'versions/create'), {}),
    };
    for (const [key, res] of Object.entries(refusals)) {
      expect(EXPECTED_FOREIGN, `HEADER_NEG_TENANCY ${key} answered ${res.status}`).toContain(res.status);
      const text = JSON.stringify(res.body);
      for (const value of [foreign.name, ...foreign.versions.map(v => v.id)]) expect(text).not.toContain(value);
    }
    await attach(`refusals-${own.name}`, Object.fromEntries(Object.entries(refusals).map(([k, r]) => [k, r.status])));
  }
  expect({ a: await versionsOf(a), b: await versionsOf(b), pinA: await pinOf(a), pinB: await pinOf(b) })
    .toEqual(before);
  await attach('tenant-receipts', scopes.receipts);
});
