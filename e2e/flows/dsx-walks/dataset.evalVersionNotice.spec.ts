import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// SavedEvalsList.jsx: saved-eval-definition / saved-eval-newer-version.
// API bodies follow ManuallyCreateDataset, EvalCreatePage and EvaluationDrawer;
// useEvalVersions sends {} to versions/create and versions/:id/set-default.
const dp = (id: string, action: string) => `/model-hub/develops/${id}/${action}/`;
const tp = (id: string, action: string) => `/model-hub/eval-templates/${id}/${action}/`;
type Envelope<T> = { result: T };
type Version = { id: string; version_number: number; is_default: boolean; pass_threshold: number };
type Binding = { id: string; name: string; template_id: string; pinned_version_id: string | null;
  version: string; latest_version: string; pass_threshold: number; higher_is_better: boolean };

test('@DSX-WALK-EVAL-02: Distinguish a pinned evaluation’s running version from the newest saved version and its pass rule.', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DSX-WALK-EVAL-02', area: 'dsx-walks',
    userGoal: 'Read the actual version and pass rule of each saved dataset evaluation, including a newer-version notice, without leaking another tenant’s templates.',
    steps: [
      'seed a dataset and score template in each organization; save a pinned and a default-following binding in organization A',
      'save a stricter second template version through the served API',
      'open Evaluate and read exact running-version, pass-rule and newer-version text for both bindings',
      'make V1 default, reload, and verify that only the unpinned evaluation follows the change',
      'request the dataset evaluations and template detail and versions as organization B',
    ],
    backendChecks: [
      'get_evals_list reports V1/0.5 for the pin and V2/0.8 for the default-following binding',
      'after changing the default, both report V1/0.5 while latest_version remains v2',
      'foreign dataset list, template detail, version list and set-default requests return exactly 404',
      'rejected requests leave the authorized binding list and template versions unchanged',
    ],
  }),
}, async ({ browser, scopeActors: scopes }, testInfo) => {
  test.setTimeout(600_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const call = async <T,>(actor: ScopeActor, method: string, path: string, body?: unknown,
    params?: Record<string, string | number>): Promise<T> => {
    const res = await scopes.send<T>(actor, method, path, body, params);
    expect(res.status, `${method} ${path}`).toBe(200);
    return res.body;
  };
  const seed = async (actor: ScopeActor, label: string) => {
    const name = `walk_notice_${label}_${suffix}`;
    const created = await call<Envelope<{ dataset_id: string }>>(actor, 'POST',
      '/model-hub/develops/create-dataset-manually/', {
        dataset_name: name, model_type: 'GenerativeLLM', number_of_rows: 1, number_of_columns: 1,
        columns: [{ name: 'output', type: 'text', role: 'input' }],
      });
    const datasetId = created.result.dataset_id;
    const table = await call<Envelope<{ column_config: { id: string; name: string }[] }>>(actor, 'GET', dp(datasetId, 'get-dataset-table'));
    expect(table.result.column_config.map(c => c.name)).toEqual(['output']);
    const template = await call<Envelope<{ id: string }>>(actor, 'POST', '/model-hub/eval-templates/create-v2/', {
      name, eval_type: 'llm', model: 'gpt-4o-mini', instructions: 'Score this synthetic reply: {{output}}',
      output_type: 'percentage', pass_threshold: 0.5, check_internet: false, error_localizer_enabled: false,
    });
    const templateId = template.result.id;
    const versions = await call<Envelope<{ versions: Version[] }>>(actor, 'GET', tp(templateId, 'versions'));
    expect(versions.result.versions).toHaveLength(1);
    const v1 = versions.result.versions[0].id;
    for (const role of ['pinned', 'follows'] as const) {
      await call(actor, 'POST', dp(datasetId, 'add_user_eval'), {
        name: `${name}_${role}`, template_id: templateId, model: 'gpt-4o-mini', run: false,
        pinned_version_id: role === 'pinned' ? v1 : null,
        config: { mapping: { output: table.result.column_config[0].id }, config: {}, reason_column: false },
      });
    }
    return { actor, name, datasetId, templateId, v1 };
  };
  const a = await seed(scopes.ownerA, 'a');
  const b = await seed(scopes.ownerB, 'b');
  const list = async (t: typeof a) => (await call<Envelope<{ evals: Binding[] }>>(t.actor, 'GET',
    dp(t.datasetId, 'get_evals_list'), undefined, { eval_type: 'user' })).result.evals;
  const beforeB = await list(b);
  await call(a.actor, 'PUT', tp(a.templateId, 'update'), { pass_threshold: 0.8 });
  const version = await call<Envelope<Version>>(a.actor, 'POST', tp(a.templateId, 'versions/create'), {});
  expect(version.result).toMatchObject({ version_number: 2, is_default: true });
  const v2 = version.result.id;
  const before = await list(a);
  expect(before).toHaveLength(2);
  const held = before.find(e => e.name === `${a.name}_pinned`)!;
  const follows = before.find(e => e.name === `${a.name}_follows`)!;
  expect(held).toMatchObject({ template_id: a.templateId, pinned_version_id: a.v1, version: 'v1',
    latest_version: 'v2', pass_threshold: 0.5, higher_is_better: true });
  expect(follows).toMatchObject({ template_id: a.templateId, pinned_version_id: null, version: 'v2',
    latest_version: 'v2', pass_threshold: 0.8, higher_is_better: true });
  await testInfo.attach('seeded-ids', { contentType: 'application/json', body: JSON.stringify({
    a: { organizationId: a.actor.organizationId, datasetId: a.datasetId, templateId: a.templateId,
      v1: a.v1, v2, bindingIds: before.map(e => e.id) },
    b: { organizationId: b.actor.organizationId, datasetId: b.datasetId, templateId: b.templateId,
      v1: b.v1, bindingIds: beforeB.map(e => e.id) },
  }) });
  const context = await scopes.openContext(browser, a.actor);
  await context.route('**/*', route => new URL(route.request().url()).hostname === 'localhost'
    ? route.continue() : route.abort('blockedbyclient'));
  const page = await context.newPage();
  page.setDefaultTimeout(60_000);
  try {
    const open = async () => {
      await page.goto(`/dashboard/develop/${a.datasetId}?tab=data`, { waitUntil: 'domcontentloaded' });
      const item = page.getByRole('menuitem', { name: /^Evaluate(?! guide)/ });
      await expect(async () => {
        if (await item.isVisible()) await page.keyboard.press('Escape');
        await page.locator('[data-action-bar="grid"]').getByRole('button', { name: 'More actions', exact: true }).click();
        await expect(item).toBeEnabled({ timeout: 2_000 });
      }).toPass({ timeout: 60_000 });
      await item.click();
      await expect(page.getByTestId('saved-eval-row').filter({ visible: true })).toHaveCount(2);
    };
    const row = (name: string) => page.getByTestId('saved-eval-row').filter({ visible: true })
      .filter({ has: page.getByText(name, { exact: true }) });
    await open();
    await expect(row(held.name).getByTestId('saved-eval-definition')).toHaveText('↑ Higher is better · Pass ≥ 0.50 · v1 · pinned');
    await expect(row(held.name).getByTestId('saved-eval-newer-version')).toHaveText('v1 → v2 available');
    await expect(row(follows.name).getByTestId('saved-eval-definition')).toHaveText('↑ Higher is better · Pass ≥ 0.80 · v2');
    await expect(row(follows.name).getByTestId('saved-eval-newer-version')).toHaveCount(0);
    await call(a.actor, 'PUT', tp(a.templateId, `versions/${a.v1}/set-default`), {});
    await open();
    await expect(row(held.name).getByTestId('saved-eval-definition')).toHaveText('↑ Higher is better · Pass ≥ 0.50 · v1 · pinned');
    await expect(row(follows.name).getByTestId('saved-eval-definition')).toHaveText('↑ Higher is better · Pass ≥ 0.50 · v1');
    for (const name of [held.name, follows.name]) {
      await expect(row(name).getByTestId('saved-eval-newer-version')).toHaveText('v1 → v2 available');
    }
    const after = await list(a);
    for (const item of after) expect(item).toMatchObject({ version: 'v1', latest_version: 'v2', pass_threshold: 0.5 });
    expect(after.find(e => e.id === held.id)?.pinned_version_id).toBe(a.v1);
    expect(after.find(e => e.id === follows.id)?.pinned_version_id).toBeNull();
    const versions = await call<Envelope<{ versions: Version[] }>>(a.actor, 'GET', tp(a.templateId, 'versions'));
    expect(versions.result.versions.map(v => ({ id: v.id, is_default: v.is_default, pass_threshold: v.pass_threshold })))
      .toEqual([{ id: v2, is_default: false, pass_threshold: 0.8 }, { id: a.v1, is_default: true, pass_threshold: 0.5 }]);
    expect((await scopes.send(b.actor, 'GET', dp(a.datasetId, 'get_evals_list'), undefined, { eval_type: 'user' })).status).toBe(404);
    for (const action of ['detail', 'versions']) {
      expect((await scopes.send(b.actor, 'GET', tp(a.templateId, action))).status).toBe(404);
    }
    expect((await scopes.send(b.actor, 'PUT', tp(a.templateId, `versions/${v2}/set-default`), {})).status).toBe(404);
    expect(await list(a)).toEqual(after);
    expect(await list(b)).toEqual(beforeB);
    expect(await call(a.actor, 'GET', tp(a.templateId, 'versions'))).toEqual(versions);
  } finally {
    await context.close();
  }
});
