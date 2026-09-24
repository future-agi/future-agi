import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// docker-compose.yml + stack/gateway.e2e.yaml: backend-side validation of this
// model uses the isolated gateway and mock, never a host URL or paid provider.
const GATEWAY_INTERNAL_URL = 'http://agentcc-gateway:8080/v1';
const GATEWAY_INTERNAL_KEY = 'local-dev-only-shared-secret-replace-me';
const JUDGE_MODEL = 'gpt-4o'; // Distinct from EVAL-E2E-001's gpt-4o-mini.
// EvalCreatePage: draft POST followed by publish PUT; EvalDetailPage: update
// PUT then versions/create POST. Neither step executes an evaluation.
const TEMPLATES = '/model-hub/eval-templates/';
const METRICS = '/tracer/dashboard/metrics/';
const VALUES = '/tracer/dashboard/filter_values/';
// README's browser first-paint ceiling, not the default 10-second expect budget.
const UI_READY = 60_000;

interface Created { result: { id: string } }
interface Metric { property_id: string; name: string; display_name: string }
interface MetricsPage { result: { metrics: Metric[] } }
interface ValuePage {
  result: { values: { value: string; label: string }[]; next_cursor: string | null; has_more: boolean };
}
interface TemplateRow {
  id: string; name: string; organization_id: string; workspace_id: string;
  config: { choice_scores: Record<string, number> }; choices: string[];
}

test('EVAL-E2E-002: configured evaluator choices stay current in the bound project', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-002', area: 'evals',
    userGoal: 'Create and edit a choice evaluator through the UI and discover only its current choices in its bound Observe project',
    steps: ['configure a mock-backed model and ingest two projects',
      'create and publish a choice evaluator through the UI',
      'attach it to only one project through the public API',
      'inspect its Observe filter choices', 'rename a choice with Save Version',
      'refresh the picker and reject a cursor from the old vocabulary'],
    backendChecks: [
      'current choices and the project binding persist under the actor organization/workspace',
      'the catalog exposes the config in its bound project and excludes it from the sibling project',
      'values API and Observe picker reflect exact current never-used choices before and after UI editing',
      'a valid old-vocabulary cursor is rejected after Save Version changes the definition',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(480_000); // ASYNC_JOB 60 + SPAN_VISIBLE 15 + six UI_READY 360 + 45 headroom.
  page.setDefaultTimeout(UI_READY);
  const req = await request.newContext();
  try {
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const name = `e2e-eval2-${suffix}`;
    const alpha = `Alpha-${suffix}`;
    const beta = `Beta-${suffix}`;
    const gamma = `Gamma-${suffix}`;
    const traces: Awaited<ReturnType<typeof sendTrace>>[] = [];
    for (const part of ['bound', 'sibling']) {
      traces.push(await sendTrace(req, {
        collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
        projectName: `${name}-${part}`, rootName: `${name}-${part}-root`,
      }));
    }
    await testInfo.attach('trace-ids', { body: JSON.stringify(traces), contentType: 'application/json' });
    await expect.poll(async () => (await probe.ch<{ id: string }>(
      'SELECT id FROM spans FINAL WHERE trace_id IN ({a:String}, {b:String})',
      { a: traces[0].traceId, b: traces[1].traceId })).map(r => r.id).sort(), POLL.SPAN_VISIBLE)
      .toEqual(traces.flatMap(t => t.spanIds).sort());
    const projects = await probe.pg<{ id: string; name: string }>(
      'SELECT id, name FROM tracer_project WHERE name = ANY($1) AND organization_id = $2 AND workspace_id = $3',
      [traces.map(t => t.projectName), actor.organizationId, actor.workspaceId]);
    expect(projects.map(p => p.name).sort()).toEqual(traces.map(t => t.projectName).sort());
    const projectId = projects.find(p => p.name === traces[0].projectName)!.id;
    const siblingId = projects.find(p => p.name === traces[1].projectName)!.id;
    await actor.api.post('/model-hub/custom_models/create/', {
      model_provider: 'openai', model_name: JUDGE_MODEL, input_token_cost: 0, output_token_cost: 0,
      config_json: { key: GATEWAY_INTERNAL_KEY, api_base: GATEWAY_INTERNAL_URL },
    });
    let templateId = '';
    await test.step('UI: author and publish the choice evaluator', async () => {
      const draft = page.waitForResponse(r => new URL(r.url()).pathname === `${TEMPLATES}create-v2/` &&
        r.request().method() === 'POST', { timeout: UI_READY });
      await page.goto('/dashboard/evaluations/create', { waitUntil: 'domcontentloaded' });
      const draftResponse = await draft;
      expect(draftResponse.status()).toBe(200);
      templateId = ((await draftResponse.json()) as Created).result.id;
      await testInfo.attach('eval-template-id', { contentType: 'application/json',
        body: JSON.stringify({ templateId, projectId, siblingId, name, alpha, beta, gamma,
          organizationId: actor.organizationId, workspaceId: actor.workspaceId }) });
      await page.getByPlaceholder('Eg: Hallucination detector').fill(name);
      await page.getByRole('tab', { name: 'LLM-As-A-Judge', exact: true }).click();
      // MessageEditorBlock's PromptEditor uses a Quill contenteditable, not textarea.
      await page.locator('.ql-editor[contenteditable="true"]').first()
        .fill(`Classify {{output}} as ${alpha} or ${beta}.`);
      await page.getByText('Select model', { exact: true }).first().click();
      await page.getByPlaceholder('Search models...').fill(JUDGE_MODEL);
      await page.getByRole('menuitem').filter({ hasText: JUDGE_MODEL }).first().click();
      await page.getByRole('radio', { name: 'Choices', exact: true }).check();
      const choice = page.getByPlaceholder('Choice name', { exact: true });
      await choice.nth(0).fill(alpha);
      await choice.nth(0).press('Tab'); // OutputTypeConfig commits rename on blur.
      await choice.nth(0).locator('xpath=..').locator('xpath=..').locator('xpath=..')
        .getByRole('combobox').click();
      await page.getByRole('option', { name: 'Pass', exact: true }).click();
      await page.getByRole('button', { name: 'Add Choice', exact: true }).click();
      await choice.nth(1).fill(beta);
      await choice.nth(1).press('Tab');
      await choice.nth(1).locator('xpath=..').locator('xpath=..').locator('xpath=..')
        .getByRole('combobox').click();
      await page.getByRole('option', { name: 'Fail', exact: true }).click();
      const published = page.waitForResponse(r => new URL(r.url()).pathname === `${TEMPLATES}${templateId}/update/` &&
        r.request().method() === 'PUT' && r.request().postDataJSON().publish === true, { timeout: UI_READY });
      await page.getByRole('button', { name: 'Save Evaluation', exact: true }).click();
      expect((await published).status()).toBe(200);
      await expect(page).toHaveURL(new RegExp(`/dashboard/evaluations/${templateId}$`), { timeout: UI_READY });
    });
    const binding = await actor.api.post<Created>('/tracer/custom-eval-config/', {
      project: projectId, eval_template: templateId, name, model: JUDGE_MODEL,
      mapping: { output: 'fi.span.kind' }, config: { mapping: { output: 'fi.span.kind' } }, error_localizer: false,
    });
    const configId = binding.result.id;
    await testInfo.attach('eval-binding-id', { body: JSON.stringify({ templateId, configId, projectId, siblingId }),
      contentType: 'application/json' });

    await test.step('backend check 1: scoped current definition and binding', async () => {
      const templates = await probe.pg<TemplateRow>(
        'SELECT id, name, organization_id, workspace_id, config, choices FROM model_hub_evaltemplate WHERE id = $1', [templateId]);
      expect(templates).toHaveLength(1);
      expect(templates[0]).toMatchObject({ id: templateId, name, organization_id: actor.organizationId,
        workspace_id: actor.workspaceId, choices: [alpha, beta] });
      expect(templates[0].config.choice_scores).toEqual({ [alpha]: 1, [beta]: 0 });
      const configs = await probe.pg<{ id: string; project_id: string; eval_template_id: string }>(
        'SELECT id, project_id, eval_template_id FROM tracer_custom_eval_config WHERE id = $1', [configId]);
      expect(configs).toEqual([{ id: configId, project_id: projectId, eval_template_id: templateId }]);
      const executions = await probe.pg<{ id: string }>(
        'SELECT id FROM tracer_eval_logger WHERE custom_eval_config_id = $1', [configId]);
      expect(executions).toEqual([]);
    });
    await test.step('backend check 2: project catalog isolation', async () => {
      for (const id of [projectId, siblingId]) {
        const catalog = await actor.api.post<MetricsPage>(METRICS, {
          source: 'traces', project_ids: id, cursor_mode: true, per_eval_config: true,
          category: 'eval_metric', search: name, page_size: 25,
        });
        expect(catalog.result.metrics.map(m => m.property_id))
          .toEqual(id === projectId ? [`eval_config:${configId}`] : []);
      }
    });

    const valuesQuery = { property_id: `eval_config:${configId}`, source: 'traces', project_ids: projectId };
    const firstPage = await actor.api.post<ValuePage>(VALUES, { ...valuesQuery, page_size: 1 });
    expect(firstPage.result.values.map(v => v.value)).toEqual([alpha]);
    expect(firstPage.result.has_more).toBe(true);
    const oldCursor = firstPage.result.next_cursor;
    expect(oldCursor).toEqual(expect.any(String));
    const oldSecond = await actor.api.post<ValuePage>(VALUES, { ...valuesQuery, page_size: 1, cursor: oldCursor });
    expect(oldSecond.result.values.map(v => v.value)).toEqual([beta]);

    for (const phase of ['initial', 'edited'] as const) {
      if (phase === 'edited') {
        await test.step('UI: save the replacement choice as a new version', async () => {
          await page.goto(`/dashboard/evaluations/${templateId}`, { waitUntil: 'domcontentloaded' });
          // OutputTypeConfig uses defaultValue={label}; JSON object key order
          // may change on reload, so anchor to the minted label, not a row index.
          const choice = page.locator(`input[placeholder="Choice name"][value="${beta}"]`);
          await expect(choice).toHaveValue(beta, { timeout: UI_READY });
          await choice.fill(gamma);
          await choice.press('Tab');
          const version = page.waitForResponse(r => new URL(r.url()).pathname === `${TEMPLATES}${templateId}/versions/create/` &&
            r.request().method() === 'POST', { timeout: UI_READY });
          await page.getByRole('button', { name: 'Save Version', exact: true }).click();
          const response = await version;
          expect(response.status()).toBe(200);
          const saved = await response.json() as { result: { id: string; version_number: number } };
          await testInfo.attach('saved-version', { body: JSON.stringify(saved), contentType: 'application/json' });
          const versions = await probe.pg<{ id: string; eval_template_id: string;
            config_snapshot: { choice_scores: Record<string, number> } }>(
            'SELECT id, eval_template_id, config_snapshot FROM model_hub_eval_template_version WHERE id = $1',
            [saved.result.id]);
          expect(versions).toHaveLength(1);
          expect(versions[0].eval_template_id).toBe(templateId);
          expect(versions[0].config_snapshot.choice_scores).toEqual({ [alpha]: 1, [gamma]: 0 });
        });
      }
      const expected = [alpha, phase === 'initial' ? beta : gamma];
      await test.step(`backend check 3: ${phase} never-used choices in API and Observe`, async () => {
        const templates = await probe.pg<TemplateRow>(
          'SELECT id, config, choices FROM model_hub_evaltemplate WHERE id = $1', [templateId]);
        expect([...templates[0].choices].sort()).toEqual([...expected].sort());
        expect(templates[0].config.choice_scores).toEqual({ [alpha]: 1, [expected[1]]: 0 });
        const values = await actor.api.post<ValuePage>(VALUES, { ...valuesQuery, page_size: 25 });
        // Save Version preserves the edited definition's order, which can differ
        // after JSONB reload. Qualify the exact vocabulary, not a display order.
        expect(values.result.values.map(v => v.value).sort()).toEqual([...expected].sort());
        // LLMTracingView handleGroupByChange; TraceFilterPanel existing hooks.
        await page.goto(`/dashboard/observe/${projectId}/llm-tracing?tab=traces&selectedTab=spans`,
          { waitUntil: 'domcontentloaded' });
        await page.getByRole('button', { name: 'Filter', exact: true }).click({ timeout: UI_READY });
        await page.getByRole('button', { name: 'Property', exact: true }).first().click();
        await page.getByPlaceholder('Search properties...').fill(name);
        await page.locator(`[data-filter-property-option="${configId}"]`).click({ timeout: UI_READY });
        await page.locator(`[data-filter-value-trigger="${configId}"]`).click();
        await expect.poll(async () => (await page.locator('[data-filter-value-option]').allTextContents())
          .map(text => text.trim()).sort(), { timeout: UI_READY }).toEqual([...expected].sort());
        await testInfo.attach(`${phase}-picker`, { body: await page.screenshot(), contentType: 'image/png' });
      });
    }
    await test.step('backend check 4: reject the previously valid old vocabulary cursor', async () => {
      await expect(actor.api.post(VALUES, { ...valuesQuery, page_size: 1, cursor: oldCursor }))
        .rejects.toMatchObject({ status: 400, body: { code: 'cursor_mismatch' } });
    });
  } finally {
    await req.dispose();
  }
});
