import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// EvalPickerConfigFull.jsx: Save version, adjacent Select, Vn options,
// Update Evaluation. This covers stored identity/pin semantics, not execution.
const dp = (id: string, action: string) => `/model-hub/develops/${id}/${action}/`;
const tp = (id: string, action: string) => `/model-hub/eval-templates/${id}/${action}/`;
type Envelope<T> = { result: T };
type Version = { id: string; version_number: number; is_default: boolean; criteria: string; model: string };
type Binding = { id: string; name: string; template_id: string; pinned_version_id: string | null;
  version: string; status: string; config: Record<string, unknown> };

test('@DSX-WALK-EVAL-03: Save and select evaluation versions while preserving distinct template and dataset-binding identities.', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DSX-WALK-EVAL-03', area: 'dsx-walks',
    userGoal: 'Choose a saved version for a dataset evaluation and save a new template version without silently updating the existing binding.',
    steps: [
      'seed two independent organizations with a dataset, template, V1/V2 and a binding pinned to V1',
      'open the dataset evaluation editor, select V2 and update the existing binding',
      'reopen, save V3, and verify that saving a template version does not repin the binding',
      'reopen once more and verify the binding still selects V2',
      'refuse organization B’s reads, version creation and binding update against organization A',
    ],
    backendChecks: [
      'the UI sends V2’s UUID as pinned_version_id with run:false; get_evals_list retains the same binding id and template id',
      'Save version creates exactly V3 as the default; the binding stays pinned to V2 with unchanged status and config',
      'all foreign template and dataset operations return exactly 404 with no state changes',
    ],
  }),
}, async ({ browser, scopeActors: scopes }, testInfo) => {
  test.setTimeout(720_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const call = async <T,>(actor: ScopeActor, method: string, path: string, body?: unknown,
    params?: Record<string, string | number>): Promise<T> => {
    const res = await scopes.send<T>(actor, method, path, body, params);
    expect(res.status, `${method} ${path}`).toBe(200);
    return res.body;
  };
  const seed = async (actor: ScopeActor, label: string) => {
    const name = `walk_versions_${label}_${suffix}`;
    const created = await call<Envelope<{ dataset_id: string }>>(actor, 'POST', '/model-hub/develops/create-dataset-manually/', {
      dataset_name: name, model_type: 'GenerativeLLM', number_of_rows: 1, number_of_columns: 1,
      columns: [{ name: 'output', type: 'text', role: 'input' }],
    });
    const datasetId = created.result.dataset_id;
    const table = await call<Envelope<{ column_config: { id: string; name: string }[] }>>(actor, 'GET', dp(datasetId, 'get-dataset-table'));
    expect(table.result.column_config.map(c => c.name)).toEqual(['output']);
    const createdTemplate = await call<Envelope<{ id: string }>>(actor, 'POST', '/model-hub/eval-templates/create-v2/', {
      name, eval_type: 'llm', model: 'gpt-4o-mini', instructions: 'Score the synthetic reply: {{output}}',
      output_type: 'percentage', pass_threshold: 0.5, check_internet: false, error_localizer_enabled: false,
    });
    const templateId = createdTemplate.result.id;
    await call(actor, 'PUT', tp(templateId, 'update'), { instructions: 'Score the complete synthetic reply: {{output}}' });
    await call(actor, 'POST', tp(templateId, 'versions/create'), {});
    const versions = (await call<Envelope<{ versions: Version[] }>>(actor, 'GET', tp(templateId, 'versions'))).result.versions;
    expect(versions.map(v => [v.version_number, v.is_default])).toEqual([[2, true], [1, false]]);
    const v1 = versions[1].id;
    const v2 = versions[0].id;
    await call(actor, 'POST', dp(datasetId, 'add_user_eval'), {
      name, template_id: templateId, model: 'gpt-4o-mini', run: false, pinned_version_id: v1,
      config: { mapping: { output: table.result.column_config[0].id }, config: {}, reason_column: false },
    });
    const listed = await call<Envelope<{ evals: Binding[] }>>(actor, 'GET', dp(datasetId, 'get_evals_list'), undefined, { eval_type: 'user' });
    expect(listed.result.evals).toHaveLength(1);
    const binding = listed.result.evals[0];
    expect(binding).toMatchObject({ name, template_id: templateId, pinned_version_id: v1, version: 'v1' });
    expect(binding.id).not.toBe(templateId);
    return { actor, name, datasetId, templateId, v1, v2, binding };
  };
  const a = await seed(scopes.ownerA, 'a');
  const b = await seed(scopes.ownerB, 'b');
  const list = async (t: typeof a) => (await call<Envelope<{ evals: Binding[] }>>(t.actor, 'GET',
    dp(t.datasetId, 'get_evals_list'), undefined, { eval_type: 'user' })).result.evals;
  await testInfo.attach('seeded-ids', { contentType: 'application/json', body: JSON.stringify([a, b].map(t => ({
    organizationId: t.actor.organizationId, datasetId: t.datasetId, templateId: t.templateId,
    v1: t.v1, v2: t.v2, bindingId: t.binding.id,
  }))) });
  const context = await scopes.openContext(browser, a.actor);
  await context.route('**/*', route => new URL(route.request().url()).hostname === 'localhost'
    ? route.continue() : route.abort('blockedbyclient'));
  const page = await context.newPage();
  page.setDefaultTimeout(60_000);
  try {
    const openEditor = async () => {
      await page.goto(`/dashboard/develop/${a.datasetId}?tab=data`, { waitUntil: 'domcontentloaded' });
      const evaluate = page.getByRole('menuitem', { name: /^Evaluate(?! guide)/ });
      await expect(async () => {
        if (await evaluate.isVisible()) await page.keyboard.press('Escape');
        await page.locator('[data-action-bar="grid"]').getByRole('button', { name: 'More actions', exact: true }).click();
        await expect(evaluate).toBeEnabled({ timeout: 2_000 });
      }).toPass({ timeout: 60_000 });
      await evaluate.click();
      const row = page.getByTestId('saved-eval-row').filter({ visible: true }).filter({ has: page.getByText(a.name, { exact: true }) });
      await row.getByRole('button', { name: 'Edit mapping & config', exact: true }).click();
    };
    const save = page.getByRole('button', { name: 'Save version', exact: true }).filter({ visible: true });
    // The actual Select is unnamed; its immediate parent is the same Box as Save version.
    const choice = save.locator('..').getByRole('combobox');
    await openEditor();
    await expect(choice).toHaveText('V1');
    await choice.click();
    await expect(page.getByRole('listbox').getByRole('option')).toHaveText(['Default version', 'V2 (default)', 'V1']);
    await page.getByRole('option', { name: 'V2 (default)', exact: true }).click();
    const editPath = dp(a.datasetId, `edit_and_run_user_eval/${a.binding.id}`);
    const updating = page.waitForResponse(r => new URL(r.url()).pathname === editPath && r.request().method() === 'POST');
    await page.getByRole('button', { name: 'Update Evaluation', exact: true }).click();
    const updated = await updating;
    expect(updated.status()).toBe(200);
    expect(updated.request().postDataJSON()).toMatchObject({ run: false, pinned_version_id: a.v2 });
    const afterPin = await list(a);
    expect(afterPin).toHaveLength(1);
    expect(afterPin[0]).toMatchObject({ id: a.binding.id, template_id: a.templateId,
      pinned_version_id: a.v2, version: 'v2', status: a.binding.status });
    await openEditor();
    await expect(choice).toHaveText('V2 (default)');
    const creating = page.waitForResponse(r => new URL(r.url()).pathname === tp(a.templateId, 'versions/create')
      && r.request().method() === 'POST');
    await save.click();
    const created = await creating;
    expect(created.status()).toBe(200);
    const v3: Version = (await created.json()).result;
    expect(v3).toMatchObject({ version_number: 3, is_default: true });
    await expect(page.getByText('Version V3 saved', { exact: true })).toBeVisible();
    await expect(choice).toHaveText('V3 (default)');
    const versions = await call<Envelope<{ versions: Version[] }>>(a.actor, 'GET', tp(a.templateId, 'versions'));
    expect(versions.result.versions.map(v => [v.id, v.version_number, v.is_default])).toEqual([
      [v3.id, 3, true], [a.v2, 2, false], [a.v1, 1, false],
    ]);
    const afterSave = await list(a);
    expect(afterSave).toHaveLength(1);
    expect(afterSave[0]).toMatchObject({ id: a.binding.id, template_id: a.templateId,
      pinned_version_id: a.v2, version: 'v2', status: afterPin[0].status, config: afterPin[0].config });
    await openEditor();
    await expect(choice).toHaveText('V2');
    await choice.click();
    await expect(page.getByRole('listbox').getByRole('option')).toHaveText(['Default version', 'V3 (default)', 'V2', 'V1']);
    await page.keyboard.press('Escape');
    for (const action of ['detail', 'versions']) {
      expect((await scopes.send(b.actor, 'GET', tp(a.templateId, action))).status).toBe(404);
    }
    expect((await scopes.send(b.actor, 'POST', tp(a.templateId, 'versions/create'), {})).status).toBe(404);
    expect((await scopes.send(b.actor, 'POST', editPath, { model: 'gpt-4o-mini', run: false,
      pinned_version_id: v3.id, config: afterSave[0].config })).status).toBe(404);
    expect(await list(a)).toEqual(afterSave);
    expect(await list(b)).toEqual([b.binding]);
    expect(await call(a.actor, 'GET', tp(a.templateId, 'versions'))).toEqual(versions);
    await testInfo.attach('saved-version-id', { contentType: 'application/json', body: JSON.stringify({
      templateId: a.templateId, createdVersionId: v3.id, retainedBindingPin: a.v2,
    }) });
  } finally {
    await context.close();
  }
});
