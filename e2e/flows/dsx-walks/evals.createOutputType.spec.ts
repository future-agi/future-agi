import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// EvalCreatePage.jsx + OutputTypeConfig.jsx: actual radio labels, Choice name,
// Add Choice, the Pass threshold slider and choice-row Select. No judge runs.
const tp = (id: string, action: string) => `/model-hub/eval-templates/${id}/${action}/`;
type Envelope<T> = { result: T };
type Detail = { id: string; name: string; output_type: string; pass_threshold: number;
  choice_scores: Record<string, number> | null; multi_choice: boolean };
type OutputKind = 'pass_fail' | 'percentage' | 'deterministic';
const labels: Record<OutputKind, string> = { pass_fail: 'Pass/fail', percentage: 'Scoring', deterministic: 'Choices' };

test('@DSX-WALK-EVAL-06: Publish and reopen Pass/fail, Scoring and Choices evaluations with their exact saved output rules.', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DSX-WALK-EVAL-06', area: 'dsx-walks',
    userGoal: 'Choose and persist each evaluation output shape and its pass rule without exposing the definition to another organization.',
    steps: [
      'seed unpublished LLM evaluation drafts through the served create API in two independent organizations',
      'publish Pass/fail, Scoring at 65%, and Choices mapping accepted to Pass and rejected to Fail in organization A',
      'reopen each published evaluation and read its selected output type and saved rule',
      'publish a separate Scoring evaluation in organization B',
      'refuse cross-organization detail reads and output-rule writes in both directions',
    ],
    backendChecks: [
      'publish requests carry the exact output_type, pass_threshold and choice_scores selected in the UI',
      'detail GET and V1 snapshot retain the exact output shape, threshold and choice mapping',
      'foreign reads and updates return exactly 404 and leave all authorized detail responses unchanged',
    ],
  }),
}, async ({ browser, scopeActors: scopes }, testInfo) => {
  test.setTimeout(900_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const call = async <T,>(actor: ScopeActor, method: string, path: string, body?: unknown): Promise<T> => {
    const response = await scopes.send<T>(actor, method, path, body);
    expect(response.status, `${method} ${path}`).toBe(200);
    return response.body;
  };
  const drafts: { actor: ScopeActor; id: string; name: string; kind: OutputKind }[] = [];
  for (const [actor, prefix, kinds] of [
    [scopes.ownerA, 'a', ['pass_fail', 'percentage', 'deterministic']],
    [scopes.ownerB, 'b', ['percentage']],
  ] as const) {
    for (const kind of kinds) {
      const created = await call<Envelope<{ id: string }>>(actor, 'POST', '/model-hub/eval-templates/create-v2/', {
        is_draft: true, eval_type: 'llm', model: 'gpt-4o-mini', output_type: 'pass_fail', pass_threshold: 0.5,
        instructions: 'Evaluate the synthetic reply: {{output}}',
        messages: [{ role: 'system', content: 'Evaluate the synthetic reply: {{output}}' }],
        check_internet: false, error_localizer_enabled: false,
      });
      drafts.push({ actor, id: created.result.id, name: `walk_output_${prefix}_${kind}_${suffix}`, kind });
    }
  }
  await testInfo.attach('seeded-ids', { contentType: 'application/json', body: JSON.stringify(drafts.map(d => ({
    organizationId: d.actor.organizationId, workspaceId: d.actor.workspaceId, templateId: d.id, outputType: d.kind,
  }))) });
  const saved: { draft: typeof drafts[number]; detail: Envelope<Detail> }[] = [];
  for (const draft of drafts) {
    const context = await scopes.openContext(browser, draft.actor);
    await context.route('**/*', route => new URL(route.request().url()).hostname === 'localhost'
      ? route.continue() : route.abort('blockedbyclient'));
    const page = await context.newPage();
    page.setDefaultTimeout(60_000);
    try {
      await page.goto(`/dashboard/evaluations/create/${draft.id}`, { waitUntil: 'domcontentloaded' });
      await page.locator('#eval-create-name').fill(draft.name);
      for (const label of Object.values(labels)) await expect(page.getByRole('radio', { name: label, exact: true })).toBeEnabled();
      await page.getByRole('radio', { name: labels[draft.kind], exact: true }).check();
      const choices = page.getByPlaceholder('Choice name', { exact: true });
      if (draft.kind === 'percentage') {
        // Scoring seeds a choice row. Remove it to persist a raw numeric score.
        await expect(choices).toHaveValue('Choice 1');
        await choices.locator('..').locator('..').locator('..').getByRole('button').click();
        await expect(choices).toHaveCount(0);
        const threshold = page.getByRole('slider', { name: 'Pass threshold', exact: true });
        await expect(threshold).toHaveAttribute('aria-valuenow', '50');
        await threshold.focus();
        for (let i = 0; i < 15; i += 1) await threshold.press('ArrowRight');
        await expect(threshold).toHaveAttribute('aria-valuenow', '65');
      } else if (draft.kind === 'deterministic') {
        await expect(choices).toHaveValue('Choice 1');
        await choices.nth(0).fill('accepted');
        await choices.nth(0).press('Tab');
        await choices.nth(0).locator('..').locator('..').locator('..').getByRole('combobox').click();
        await page.getByRole('option', { name: 'Pass', exact: true }).click();
        await page.getByRole('button', { name: 'Add Choice', exact: true }).click();
        await choices.nth(1).fill('rejected');
        await choices.nth(1).press('Tab');
        await choices.nth(1).locator('..').locator('..').locator('..').getByRole('combobox').click();
        await page.getByRole('option', { name: 'Fail', exact: true }).click();
        await expect(page.getByRole('checkbox', { name: 'Allow multiple choices (LLM can select more than one)', exact: true })).not.toBeChecked();
      }
      const threshold = draft.kind === 'percentage' ? 0.65 : 0.5;
      const choiceScores = draft.kind === 'deterministic' ? { accepted: 1, rejected: 0 } : null;
      const pending = page.waitForResponse(r => new URL(r.url()).pathname === tp(draft.id, 'update')
        && r.request().method() === 'PUT' && r.request().postDataJSON()?.publish === true);
      await page.getByRole('button', { name: 'Save Evaluation', exact: true }).click();
      const published = await pending;
      expect(published.status()).toBe(200);
      expect(published.request().postDataJSON()).toMatchObject({ name: draft.name, publish: true,
        output_type: draft.kind, pass_threshold: threshold, choice_scores: choiceScores, multi_choice: false });
      await expect(page).toHaveURL(new RegExp(`/dashboard/evaluations/${draft.id}$`));
      const detail = await call<Envelope<Detail>>(draft.actor, 'GET', tp(draft.id, 'detail'));
      expect(detail.result).toMatchObject({ id: draft.id, name: draft.name, output_type: draft.kind,
        pass_threshold: threshold, choice_scores: choiceScores, multi_choice: false });
      const versions = await call<Envelope<{ versions: { id: string; version_number: number;
        output_type_normalized: string; pass_threshold: number; choice_scores: Record<string, number> | null }[] }>>(
        draft.actor, 'GET', tp(draft.id, 'versions'));
      expect(versions.result.versions).toHaveLength(1);
      expect(versions.result.versions[0]).toMatchObject({ version_number: 1, output_type_normalized: draft.kind,
        pass_threshold: threshold, choice_scores: choiceScores });
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(page.getByRole('radio', { name: labels[draft.kind], exact: true })).toBeChecked();
      if (draft.kind === 'percentage') {
        await expect(page.getByRole('slider', { name: 'Pass threshold', exact: true })).toHaveAttribute('aria-valuenow', '65');
        await expect(choices).toHaveCount(0);
      } else if (draft.kind === 'deterministic') {
        await expect(choices).toHaveCount(2);
        // JSON object order is not an identity: anchor by the persisted label.
        for (const [label, category] of [['accepted', 'Pass'], ['rejected', 'Fail']] as const) {
          const field = page.locator(`input[placeholder="Choice name"][value="${label}"]`);
          await expect(field).toHaveValue(label);
          await expect(field.locator('..').locator('..').locator('..').getByRole('combobox')).toHaveText(category);
        }
      }
      saved.push({ draft, detail });
      await testInfo.attach(`published-${draft.kind}-${draft.actor === scopes.ownerA ? 'a' : 'b'}`, {
        contentType: 'application/json', body: JSON.stringify({ templateId: draft.id, versionId: versions.result.versions[0].id,
          outputType: draft.kind, passThreshold: threshold, choiceScores }),
      });
    } finally {
      await context.close();
    }
  }
  for (const { draft, detail } of saved) {
    const outsider = draft.actor === scopes.ownerA ? scopes.ownerB : scopes.ownerA;
    expect((await scopes.send(outsider, 'GET', tp(draft.id, 'detail'))).status).toBe(404);
    expect((await scopes.send(outsider, 'PUT', tp(draft.id, 'update'), { pass_threshold: 0.1 })).status).toBe(404);
    expect(await call(draft.actor, 'GET', tp(draft.id, 'detail'))).toEqual(detail);
  }
});
