import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// SavedEvalsList.jsx aria-label, analysis.js whole-set PUT, RunExperiment.jsx
// default seeding, EvaluationStepExperimentCreation.jsx eval-default-chip.
const dp = (id: string, action: string) => `/model-hub/develops/${id}/${action}/`;
type Envelope<T> = { result: T };
type Binding = { id: string; name: string; template_id: string; is_experiment_default: boolean;
  pinned_version_id: string | null; config: Record<string, unknown>; status: string };

test('@DSX-WALK-EVAL-04: Persist an experiment-default evaluation and preselect it only in its own dataset’s new wizard.', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DSX-WALK-EVAL-04', area: 'dsx-walks',
    userGoal: 'Choose which saved evaluation is preselected in new experiments, reopen that choice and keep it isolated to my dataset and organization.',
    steps: [
      'seed two dataset evaluations in organization A and one in organization B through served APIs',
      'turn on Use in new experiments for exactly one evaluation and reload the drawer',
      'open a prompt-prefilled new experiment and verify exactly that evaluation has a Default badge',
      'turn the choice off, reopen the wizard and verify neither evaluation is selected',
      'refuse organization B’s read and default-set write against organization A’s dataset',
    ],
    backendChecks: [
      'the UI PUT body is exactly experiment_default_eval_ids containing the selected binding id, then an empty array',
      'get_evals_list reports the exact per-binding default flags without changing config, pins or statuses',
      'foreign read and PUT return exactly 404; both organizations’ bindings remain unchanged',
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
  const seed = async (actor: ScopeActor, label: string, count: number) => {
    const name = `walk_defaults_${label}_${suffix}`;
    const created = await call<Envelope<{ dataset_id: string }>>(actor, 'POST', '/model-hub/develops/create-dataset-manually/', {
      dataset_name: name, model_type: 'GenerativeLLM', number_of_rows: 1, number_of_columns: 1,
      columns: [{ name: 'output', type: 'text', role: 'input' }],
    });
    const datasetId = created.result.dataset_id;
    const table = await call<Envelope<{ column_config: { id: string; name: string }[] }>>(actor, 'GET', dp(datasetId, 'get-dataset-table'));
    expect(table.result.column_config.map(c => c.name)).toEqual(['output']);
    const template = await call<Envelope<{ id: string }>>(actor, 'POST', '/model-hub/eval-templates/create-v2/', {
      name, eval_type: 'llm', model: 'gpt-4o-mini', instructions: 'Check the synthetic reply: {{output}}',
      output_type: 'pass_fail', pass_threshold: 0.5, check_internet: false, error_localizer_enabled: false,
    });
    for (let i = 0; i < count; i += 1) {
      await call(actor, 'POST', dp(datasetId, 'add_user_eval'), {
        name: `${name}_${i}`, template_id: template.result.id, model: 'gpt-4o-mini', run: false,
        config: { mapping: { output: table.result.column_config[0].id }, config: {}, reason_column: false },
      });
    }
    const list = (await call<Envelope<{ evals: Binding[] }>>(actor, 'GET', dp(datasetId, 'get_evals_list'),
      undefined, { eval_type: 'user' })).result.evals;
    expect(list).toHaveLength(count);
    expect(list.map(e => e.is_experiment_default)).toEqual(Array(count).fill(false));
    return { actor, name, datasetId, templateId: template.result.id, list };
  };
  const a = await seed(scopes.ownerA, 'a', 2);
  const b = await seed(scopes.ownerB, 'b', 1);
  const selected = a.list.find(e => e.name === `${a.name}_0`)!;
  const unselected = a.list.find(e => e.name === `${a.name}_1`)!;
  // Saved prompt only; no Run/Test action is invoked. Same create-draft/prompt_config
  // wire as frontend/src/api/develop/prompt.js and the existing version-choice flow.
  const promptName = `walk_defaults_prompt_${suffix}`;
  const prompt = await call<Envelope<{ id: string }>>(a.actor, 'POST', '/model-hub/prompt-templates/create-draft/', {
    name: promptName, is_draft: false,
    prompt_config: [{ messages: [{ role: 'user', content: 'Synthetic fixture reply' }],
      configuration: { model: 'gpt-4o-mini', temperature: 0, max_tokens: 16, tools: [] } }],
  });
  const promptVersions = await call<{ results: { id: string; is_draft: boolean }[] }>(a.actor, 'GET',
    '/model-hub/prompt-history-executions/', undefined, { template_id: prompt.result.id });
  expect(promptVersions.results).toHaveLength(1);
  expect(promptVersions.results[0].is_draft).toBe(false);
  const promptVersionId = promptVersions.results[0].id;
  const list = async (t: typeof a) => (await call<Envelope<{ evals: Binding[] }>>(t.actor, 'GET',
    dp(t.datasetId, 'get_evals_list'), undefined, { eval_type: 'user' })).result.evals;
  await testInfo.attach('seeded-ids', { contentType: 'application/json', body: JSON.stringify({
    datasets: [a, b].map(t => ({ organizationId: t.actor.organizationId, datasetId: t.datasetId,
      templateId: t.templateId, bindingIds: t.list.map(e => e.id) })), promptId: prompt.result.id, promptVersionId,
  }) });
  const context = await scopes.openContext(browser, a.actor);
  await context.route('**/*', route => new URL(route.request().url()).hostname === 'localhost'
    ? route.continue() : route.abort('blockedbyclient'));
  const page = await context.newPage();
  page.setDefaultTimeout(60_000);
  try {
    const openList = async () => {
      await page.goto(`/dashboard/develop/${a.datasetId}?tab=data`, { waitUntil: 'domcontentloaded' });
      const evaluate = page.getByRole('menuitem', { name: /^Evaluate(?! guide)/ });
      await expect(async () => {
        if (await evaluate.isVisible()) await page.keyboard.press('Escape');
        await page.locator('[data-action-bar="grid"]').getByRole('button', { name: 'More actions', exact: true }).click();
        await expect(evaluate).toBeEnabled({ timeout: 2_000 });
      }).toPass({ timeout: 60_000 });
      await evaluate.click();
    };
    const toggle = (name: string) => page.getByLabel(`Use ${name} in new experiments`, { exact: true });
    const mutation = dp(a.datasetId, 'edit_dataset_behavior');
    const setDefault = async (on: boolean) => {
      const pending = page.waitForResponse(r => new URL(r.url()).pathname === mutation && r.request().method() === 'PUT');
      await toggle(selected.name).setChecked(on);
      const response = await pending;
      expect(response.status()).toBe(200);
      expect(response.request().postDataJSON()).toEqual({ experiment_default_eval_ids: on ? [selected.id] : [] });
      await expect(page.getByText(on ? `${selected.name} will be preselected in new experiments`
        : `${selected.name} won't be preselected in new experiments`, { exact: true })).toBeVisible();
    };
    const openWizard = async () => {
      await page.goto(`/dashboard/develop/${a.datasetId}?tab=experiments&new_experiment=1&source=prompt&prompt_id=${prompt.result.id}&prompt_version=${promptVersionId}`,
        { waitUntil: 'domcontentloaded' });
      await expect(page.getByText(promptName, { exact: true }).first()).toBeVisible();
      const name = page.getByRole('textbox', { name: /^Experiment name/ });
      await expect(name).not.toHaveValue('');
      await name.fill(`walk_defaults_experiment_${suffix}`);
      await page.getByTestId('wizard-continue').click();
      await page.getByTestId('wizard-continue').click();
      await expect(page.getByRole('button', { name: 'Add evaluation', exact: true })).toBeVisible();
    };
    await openList();
    await expect(toggle(selected.name)).not.toBeChecked();
    await setDefault(true);
    const enabled = await list(a);
    for (const item of enabled) {
      const original = a.list.find(e => e.id === item.id)!;
      expect(item).toMatchObject({ id: original.id, config: original.config, status: original.status,
        pinned_version_id: original.pinned_version_id, is_experiment_default: item.id === selected.id });
    }
    await openList();
    await expect(toggle(selected.name)).toBeChecked();
    await expect(toggle(unselected.name)).not.toBeChecked();
    await openWizard();
    const card = page.getByTestId(`eval-card-${selected.name}`);
    await expect(card).toBeVisible();
    await expect(card.getByTestId('eval-default-chip')).toHaveText('Default');
    await expect(page.getByTestId(`eval-card-${unselected.name}`)).toHaveCount(0);
    await expect(page.getByTestId('eval-default-chip')).toHaveCount(1);
    await openList();
    await setDefault(false);
    const cleared = await list(a);
    expect(cleared.map(e => e.is_experiment_default)).toEqual([false, false]);
    await openWizard();
    await expect(page.getByTestId(`eval-card-${selected.name}`)).toHaveCount(0);
    await expect(page.getByTestId(`eval-card-${unselected.name}`)).toHaveCount(0);
    await expect(page.getByTestId('eval-default-chip')).toHaveCount(0);
    expect((await scopes.send(b.actor, 'GET', dp(a.datasetId, 'get_evals_list'), undefined, { eval_type: 'user' })).status).toBe(404);
    expect((await scopes.send(b.actor, 'PUT', mutation, { experiment_default_eval_ids: [selected.id] })).status).toBe(404);
    expect(await list(a)).toEqual(cleared);
    expect(await list(b)).toEqual(b.list);
  } finally {
    await context.close();
  }
});
