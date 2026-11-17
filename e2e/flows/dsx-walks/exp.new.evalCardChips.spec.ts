import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// EvaluationStepExperimentCreation.jsx / evalCardModel-g7.js: Score 0–1,
// Pass/fail, Higher is better, eval-pass-at-chip, eval-judge-chip, v1.
// evalFromDatasetDefault supplies the saved binding's rule, not a generic Auto value.
const dp = (id: string, action: string) => `/model-hub/develops/${id}/${action}/`;
const tp = (id: string, action: string) => `/model-hub/eval-templates/${id}/${action}/`;
type Envelope<T> = { result: T };
type Binding = { id: string; name: string; template_id: string; model: string; output_type: string;
  pass_threshold: number; higher_is_better: boolean; version: string; is_experiment_default: boolean };
type Seed = { actor: ScopeActor; name: string; datasetId: string; promptId: string; promptVersionId: string;
  promptName: string; evals: Binding[]; score: number };

test('@DSX-WALK-EVAL-08: Show exact output, pass-rule, judge and version chips on a new experiment’s evaluation cards.', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DSX-WALK-EVAL-08', area: 'dsx-walks',
    userGoal: 'See truthful score and pass/fail evaluation metadata on a new experiment without running a judge or leaking another tenant’s definitions.',
    steps: [
      'seed two independent datasets, saved prompts and default score/pass-fail evaluations through served APIs',
      'open each prompt-prefilled new experiment and continue to its evaluations',
      'read exact output, direction, threshold, judge and version chips; pass/fail must not display a numeric pass bar',
      'reopen the wizard and verify those exact chips again',
      'refuse cross-organization evaluation-list, template-detail and template-update requests',
    ],
    backendChecks: [
      'get_evals_list and template detail report exact output_type, model, pass_threshold and version for each card',
      'opening and reopening the wizard leaves saved bindings and template details unchanged and sends no judge or experiment-create request',
      'foreign list/detail/update requests return exactly 404 and leave both tenants’ data unchanged',
    ],
  }),
}, async ({ browser, scopeActors: scopes }, testInfo) => {
  test.setTimeout(900_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const call = async <T,>(actor: ScopeActor, method: string, path: string, body?: unknown,
    params?: Record<string, string | number>): Promise<T> => {
    const response = await scopes.send<T>(actor, method, path, body, params);
    expect(response.status, `${method} ${path}`).toBe(200);
    return response.body;
  };
  const seeds: Seed[] = [];
  for (const [actor, label, score] of [[scopes.ownerA, 'a', 0.75], [scopes.ownerB, 'b', 0.25]] as const) {
    const name = `walk_chips_${label}_${suffix}`;
    const created = await call<Envelope<{ dataset_id: string }>>(actor, 'POST', '/model-hub/develops/create-dataset-manually/', {
      dataset_name: name, model_type: 'GenerativeLLM', number_of_rows: 1, number_of_columns: 1,
      columns: [{ name: 'output', type: 'text', role: 'input' }],
    });
    const datasetId = created.result.dataset_id;
    const table = await call<Envelope<{ column_config: { id: string; name: string }[] }>>(actor, 'GET', dp(datasetId, 'get-dataset-table'));
    expect(table.result.column_config.map(c => c.name)).toEqual(['output']);
    for (const kind of ['percentage', 'pass_fail']) {
      const template = await call<Envelope<{ id: string }>>(actor, 'POST', '/model-hub/eval-templates/create-v2/', {
        name: `${name}_${kind}`, eval_type: 'llm', model: 'gpt-4o-mini', output_type: kind,
        pass_threshold: kind === 'percentage' ? score : 0.5, instructions: 'Assess the synthetic reply: {{output}}',
        check_internet: false, error_localizer_enabled: false,
      });
      await call(actor, 'POST', dp(datasetId, 'add_user_eval'), {
        name: `${name}_${kind}`, template_id: template.result.id, model: 'gpt-4o-mini', run: false,
        config: { mapping: { output: table.result.column_config[0].id }, config: {}, reason_column: false },
      });
    }
    let evals = (await call<Envelope<{ evals: Binding[] }>>(actor, 'GET', dp(datasetId, 'get_evals_list'),
      undefined, { eval_type: 'user' })).result.evals;
    expect(evals).toHaveLength(2);
    await call(actor, 'PUT', dp(datasetId, 'edit_dataset_behavior'), { experiment_default_eval_ids: evals.map(e => e.id) });
    evals = (await call<Envelope<{ evals: Binding[] }>>(actor, 'GET', dp(datasetId, 'get_evals_list'),
      undefined, { eval_type: 'user' })).result.evals;
    const promptName = `${name}_prompt`;
    const prompt = await call<Envelope<{ id: string }>>(actor, 'POST', '/model-hub/prompt-templates/create-draft/', {
      name: promptName, is_draft: false,
      prompt_config: [{ messages: [{ role: 'user', content: 'Synthetic fixture reply' }],
        configuration: { model: 'gpt-4o-mini', temperature: 0, max_tokens: 16, tools: [] } }],
    });
    const versions = await call<{ results: { id: string; is_draft: boolean }[] }>(actor, 'GET',
      '/model-hub/prompt-history-executions/', undefined, { template_id: prompt.result.id });
    expect(versions.results).toHaveLength(1);
    expect(versions.results[0].is_draft).toBe(false);
    seeds.push({ actor, name, datasetId, promptName, promptId: prompt.result.id,
      promptVersionId: versions.results[0].id, evals, score });
  }
  await testInfo.attach('seeded-ids', { contentType: 'application/json', body: JSON.stringify(seeds.map(t => ({
    organizationId: t.actor.organizationId, workspaceId: t.actor.workspaceId, datasetId: t.datasetId,
    promptId: t.promptId, promptVersionId: t.promptVersionId,
    evaluations: t.evals.map(e => ({ bindingId: e.id, templateId: e.template_id })),
  }))) });
  const list = async (t: Seed) => (await call<Envelope<{ evals: Binding[] }>>(t.actor, 'GET',
    dp(t.datasetId, 'get_evals_list'), undefined, { eval_type: 'user' })).result.evals;
  for (const t of seeds) {
    const details = new Map<string, unknown>();
    for (const item of t.evals) {
      const kind = item.name.endsWith('_percentage') ? 'percentage' : 'pass_fail';
      expect(item).toMatchObject({ model: 'gpt-4o-mini', output_type: kind,
        pass_threshold: kind === 'percentage' ? t.score : 0.5, higher_is_better: true,
        version: 'v1', is_experiment_default: true });
      const detail = await call<Envelope<{ id: string; output_type: string; pass_threshold: number; model: string; current_version: string }>>(
        t.actor, 'GET', tp(item.template_id, 'detail'));
      expect(detail.result).toMatchObject({ id: item.template_id, output_type: kind, model: 'gpt-4o-mini',
        pass_threshold: kind === 'percentage' ? t.score : 0.5, current_version: 'V1' });
      details.set(item.template_id, detail);
    }
    const context = await scopes.openContext(browser, t.actor);
    await context.route('**/*', route => new URL(route.request().url()).hostname === 'localhost'
      ? route.continue() : route.abort('blockedbyclient'));
    const page = await context.newPage();
    page.setDefaultTimeout(60_000);
    const executions: string[] = [];
    page.on('request', request => {
      const path = new URL(request.url()).pathname;
      if (request.method() === 'POST' && ['/model-hub/eval-playground/', '/model-hub/experiments/v2/'].includes(path)) executions.push(path);
    });
    try {
      for (const visit of [1, 2]) {
        await page.goto(`/dashboard/develop/${t.datasetId}?tab=experiments&new_experiment=1&source=prompt&prompt_id=${t.promptId}&prompt_version=${t.promptVersionId}`,
          { waitUntil: 'domcontentloaded' });
        await expect(page.getByText(t.promptName, { exact: true }).first()).toBeVisible();
        const experimentName = page.getByRole('textbox', { name: /^Experiment name/ });
        await expect(experimentName).not.toHaveValue('');
        await experimentName.fill(`${t.name}_experiment_${visit}`);
        await page.getByTestId('wizard-continue').click();
        await page.getByTestId('wizard-continue').click();
        await expect(page.getByRole('button', { name: 'Add evaluation', exact: true })).toBeVisible();
        await expect(page.getByTestId('eval-card-chips').filter({ visible: true })).toHaveCount(2);
        for (const item of t.evals) {
          const card = page.getByTestId(`eval-card-${item.name}`);
          const chips = card.getByTestId('eval-card-chips');
          await expect(card).toBeVisible();
          await expect(chips.getByText('Higher is better', { exact: true })).toBeVisible();
          await expect(chips.getByText('v1', { exact: true })).toBeVisible();
          if (item.output_type === 'percentage') {
            await expect(chips.getByText('Score 0–1', { exact: true })).toBeVisible();
            await expect(chips.getByTestId('eval-pass-at-chip')).toHaveText(`Pass at ≥ ${t.score.toFixed(2)} · judge gpt-4o-mini`);
            await expect(chips.getByTestId('eval-judge-chip')).toHaveCount(0);
          } else {
            await expect(chips.getByText('Pass/fail', { exact: true })).toBeVisible();
            await expect(chips.getByTestId('eval-pass-at-chip')).toHaveCount(0);
            await expect(chips.getByTestId('eval-judge-chip')).toHaveText('judge gpt-4o-mini');
          }
          await expect(chips.getByTestId('eval-auto-chip')).toHaveCount(0);
        }
        for (const foreign of seeds.filter(other => other !== t).flatMap(other => other.evals)) {
          await expect(page.getByTestId(`eval-card-${foreign.name}`)).toHaveCount(0);
        }
        expect(await list(t)).toEqual(t.evals);
      }
      expect(executions).toEqual([]);
      const outsider = seeds.find(other => other !== t)!;
      expect((await scopes.send(outsider.actor, 'GET', dp(t.datasetId, 'get_evals_list'), undefined, { eval_type: 'user' })).status).toBe(404);
      for (const item of t.evals) {
        expect((await scopes.send(outsider.actor, 'GET', tp(item.template_id, 'detail'))).status).toBe(404);
        expect((await scopes.send(outsider.actor, 'PUT', tp(item.template_id, 'update'), { pass_threshold: 0.1 })).status).toBe(404);
        expect(await call(t.actor, 'GET', tp(item.template_id, 'detail'))).toEqual(details.get(item.template_id));
      }
      expect(await list(t)).toEqual(t.evals);
      expect(await list(outsider)).toEqual(outsider.evals);
    } finally {
      await context.close();
    }
  }
});
