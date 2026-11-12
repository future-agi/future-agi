import { request, type Page } from '@playwright/test';
import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { sendTrace } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL, type StateProbe } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// tracer/urls.py DatasetView actions; ProductionDataSheet.jsx labels.
const PREVIEW = '/tracer/dataset/preview_sources/';
const CREATE = '/tracer/dataset/add_to_new_dataset/';
const ADD = '/tracer/dataset/add_to_existing_dataset/';
const UI_READY = 60_000;
type Created = { result: { id: string } };
type Preview = { result: { items: { span_id: string; name: string }[] } };

async function mapName(page: Page) {
  await page.getByRole('button', { name: 'Remove column 2', exact: true }).click();
  await page.getByLabel('Column 1', { exact: true }).fill('operation');
  await page.getByRole('combobox', { name: /^Source field 1\b/ }).click();
  await page.getByRole('option', { name: 'name', exact: true }).click();
  await page.getByRole('combobox', { name: /^Type 1\b/ }).click();
  await page.getByRole('option', { name: 'Text', exact: true }).click();
}
async function stored(probe: StateProbe, id: string) {
  return probe.pg<{ row_id: string; value: string; status: string }>(
    `SELECT c.row_id, c.value, c.status FROM model_hub_cell c JOIN model_hub_row r ON r.id=c.row_id
     WHERE c.dataset_id=$1 AND NOT c.deleted AND NOT r.deleted ORDER BY r."order", r.id`, [id]);
}
async function persisted(page: Page, probe: StateProbe, actor: ScopeActor, id: string, values: string[], perturb?: string) {
  await expect.poll(async () => (await stored(probe, id)).map(row => ({ value: row.value, status: row.status })), POLL.ASYNC_JOB)
    .toEqual((perturb === 'values' ? [...values, 'absent'] : values).map(value => ({ value, status: 'pass' })));
  const [{ id: column }] = await probe.pg<{ id: string }>('SELECT id FROM model_hub_column WHERE dataset_id=$1 AND NOT deleted', [id]);
  const table = await actor.api.get<{ result: { table: { row_id: string; [key: string]: unknown }[] } }>(`/model-hub/develops/${id}/get-dataset-table/`, { current_page_index: 0, page_size: 25 });
  expect(table.result.table.map(row => row.row_id)).toEqual((await stored(probe, id)).map(row => row.row_id));
  expect(table.result.table.map(row => (row[column] as { cell_value: string }).cell_value)).toEqual(values);
  expect(await probe.pg('SELECT organization_id,workspace_id FROM model_hub_dataset WHERE id=$1', [id]))
    .toEqual([{ organization_id: perturb === 'ownership' ? '00000000-0000-0000-0000-000000000000' : actor.organizationId, workspace_id: actor.workspaceId }]);
  await expect(page.locator(`.ag-row [col-id="${column}"]`)).toHaveText(values, { timeout: UI_READY });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.locator(`.ag-row [col-id="${column}"]`)).toHaveText(values, { timeout: UI_READY });
}

test('DATA-E2E-033: Observe selections become persisted dataset rows within their workspace', {
  tag: ['@flow'], annotation: flowAnnotation({
    id: 'DATA-E2E-033', area: 'datasets',
    userGoal: 'Create a dataset and add mapped Observe traces, spans and session traces without importing another workspace’s data',
    steps: ['ingest recent and old traces in two organizations and run an evaluation with a labelled loopback judge STANDIN',
      'open From production through New dataset and the palette, verify last-week failures, select a session and map its traces into a new dataset',
      'add a selected child span through Add rows and a second trace through the production deep link',
      'reload the rows and refuse foreign sources and destinations without partial changes'],
    backendChecks: ['the source preview returns exactly the recent traces of the failed session and excludes unevaluated sessions and old traces',
      'the asynchronous worker persists exact mapped cells; the table API and reloaded browser agree with stored row identities',
      'datasets retain the submitting organization and workspace',
      'foreign source, foreign destination and sibling-workspace requests fail without creating datasets, columns or cells'],
  }),
}, async ({ browser, scopeActors, scopeProbe }, testInfo) => {
  test.setTimeout(1_500_000); // Two eval budgets + six worker polls + UI chains in both tenants.
  const perturb = process.env.E2E_PRODUCTION_PERTURB;
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const req = await request.newContext();
  const seeded = [];
  try {
    for (const [index, actor] of [scopeActors.ownerA, scopeActors.ownerB].entries()) {
      const owner = scopeActors.owners.find(item => item.organizationId === actor.organizationId)!;
      const label = `e2e-production-${index}-${suffix}`;
      const base = { collectorUrl: E2E.collectorUrl, apiKey: owner.apiKey, secretKey: owner.secretKey, projectName: label,
        resourceAttributes: { project_type: 'observe' }, rootAttributes: { 'session.id': label }, childAttributes: { 'session.id': label } };
      const first = await sendTrace(req, { ...base, rootName: `${label}-first`, childName: `${label}-child` });
      await expect.poll(async () => Number((await scopeProbe.ch<{ n: string }>('SELECT count() n FROM spans FINAL WHERE trace_id={t:String}', { t: first.traceId }))[0].n), POLL.SPAN_VISIBLE).toBe(2);
      const [{ id: project }] = await scopeProbe.pg<{ id: string }>('SELECT id FROM tracer_project WHERE name=$1 AND organization_id=$2', [label, actor.organizationId]);
      // Same public model/template/config/task contracts as evals/eval-task.spec.ts.
      // The external judge is a labelled HTTP STANDIN on the isolated fixture network.
      await actor.api.post('/model-hub/custom_models/create/', { model_provider: 'openai', model_name: 'gpt-4o-mini', input_token_cost: 0, output_token_cost: 0,
        config_json: { key: 'local-dev-only-shared-secret-replace-me', api_base: 'http://agentcc-gateway:8080/v1' } });
      const template = await actor.api.post<Created>('/model-hub/eval-templates/create-v2/', { name: `${label}-judge`, eval_type: 'llm',
        instructions: `Reply with exactly this JSON: {"result": "Fail", "explanation": "${label} saw {{output}}"}`,
        model: 'gpt-4o-mini', output_type: 'pass_fail', pass_threshold: 0.5 });
      const config = await actor.api.post<Created>('/tracer/custom-eval-config/', { project, eval_template: template.result.id, name: `${label}-eval`, model: 'gpt-4o-mini',
        mapping: { output: 'fi.span.kind' }, config: { mapping: { output: 'fi.span.kind' } }, error_localizer: false });
      const now = Date.now();
      const task = await actor.api.post<Created>('/tracer/eval-task/', { name: `${label}-task`, project, evals: [config.result.id],
        filters: { project_id: project, date_range: [new Date(now - 3600000).toISOString(), new Date(now + 3600000).toISOString()] }, run_type: 'historical', row_type: 'spans', spans_limit: 100000, sampling_rate: 100 });
      await expect.poll(async () => (await actor.api.get<{ status: string }>(`/tracer/eval-task/${task.result.id}/`)).status, POLL.EVAL_RESULT).toBe('completed');
      await expect.poll(async () => (await scopeProbe.pg<{ output_bool: boolean }>('SELECT output_bool FROM tracer_eval_logger WHERE eval_task_id=$1 AND observation_span_id=$2 AND status=$3', [task.result.id, first.spanIds[1], 'completed']))[0]?.output_bool, POLL.EVAL_RESULT).toBe(false);
      const peer = await sendTrace(req, { ...base, rootName: `${label}-session-peer`, childName: `${label}-peer-child` });
      const second = await sendTrace(req, { ...base, rootName: `${label}-second`, childName: `${label}-other-child`, rootAttributes: { 'session.id': `${label}-other` }, childAttributes: { 'session.id': `${label}-other` } });
      const old = await sendTrace(req, { ...base, rootName: `${label}-old`, startTimeUnixNano: BigInt(Date.now() - 8 * 86400000) * 1000000n });
      await expect.poll(async () => Number((await scopeProbe.ch<{ n: string }>('SELECT count() n FROM spans FINAL WHERE project_id={p:UUID}', { p: project }))[0].n), POLL.SPAN_VISIBLE).toBe(8);
      seeded.push({ actor, project, first, peer, second, old, label, dataset: '' });
      await testInfo.attach(`sources-${index}`, { contentType: 'application/json', body: JSON.stringify({ project, first, peer, second, old, organizationId: actor.organizationId, judge: 'loopback HTTP STANDIN' }) });
    }
    for (const item of seeded) {
      const { actor, project, first, peer, second, label } = item;
      const context = await scopeActors.openContext(browser, actor);
      await context.route('**/*', route => { const url = new URL(route.request().url()); return url.hostname === 'localhost' || ['data:', 'blob:'].includes(url.protocol) ? route.continue() : route.abort('blockedbyclient'); });
      const page = await context.newPage(); page.setDefaultTimeout(UI_READY);
      try {
        await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
        await expect(page.getByRole('button', { name: 'New dataset', exact: true })).toBeVisible({ timeout: UI_READY });
        const reading = page.waitForResponse(r => new URL(r.url()).pathname === PREVIEW);
        if (actor.organizationId === scopeActors.ownerA.organizationId) {
          await page.getByRole('button', { name: 'New dataset', exact: true }).click();
          await page.getByText('From production traces', { exact: true }).click();
        } else {
          await page.keyboard.press('Control+k');
          await page.getByRole('combobox', { name: 'Command', exact: true }).fill('New dataset from production traces');
          await page.locator('[data-command="datasets.fromTraces"]').click();
        }
        const preview = await reading; expect(preview.status(), await preview.text()).toBe(200);
        expect((await preview.json() as Preview).result.items.map(row => row.span_id)).toEqual(perturb === 'selection' ? [second.spanIds[0]] : [peer.spanIds[0], first.spanIds[0]]);
        await page.getByRole('checkbox', { name: /^Session .*\(2 traces\)$/ }).check();
        await page.getByLabel('Dataset name', { exact: true }).fill(label);
        await mapName(page);
        for (const unsupported of ['Save version', 'Keep as source', 'Split', 'Task suite']) await expect(page.getByText(unsupported, { exact: true })).toHaveCount(0);
        const creating = page.waitForResponse(r => new URL(r.url()).pathname === CREATE);
        await page.getByRole('button', { name: 'Create dataset with 2 rows', exact: true }).click();
        const created = await creating; expect(created.status(), await created.text()).toBe(200);
        expect(created.request().postDataJSON()).toMatchObject({ project, span_ids: [peer.spanIds[0], first.spanIds[0]], mapping_config: [{ col_name: 'operation', span_field: 'name', data_type: 'text' }] });
        item.dataset = (await created.json()).result.dataset_id;
        await testInfo.attach(`destination-${label}`, { contentType: 'application/json', body: JSON.stringify({ dataset: item.dataset, organizationId: actor.organizationId, workspaceId: actor.workspaceId }) });
        const values = [`${label}-session-peer`, `${label}-first`];
        await persisted(page, scopeProbe, actor, item.dataset, values, perturb);
        for (const [kind, value] of [['Spans', `${label}-child`], ['Traces', `${label}-second`]]) {
          if (kind === 'Spans') {
            await page.getByRole('button', { name: 'Add rows', exact: true }).click();
            await page.getByText('From production traces', { exact: true }).click();
          } else await page.goto(`/dashboard/develop/${item.dataset}?tab=data&add=production`, { waitUntil: 'domcontentloaded' });
          await page.getByRole('combobox', { name: /^Source (?:Sessions|Spans|Traces)$/ }).click();
          await page.getByRole('option', { name: kind, exact: true }).click();
          if (kind === 'Traces') await page.getByRole('checkbox', { name: 'Failing evaluations only', exact: true }).uncheck();
          await page.getByRole('checkbox', { name: new RegExp(`${value} ·`) }).check();
          await mapName(page);
          const adding = page.waitForResponse(r => new URL(r.url()).pathname === ADD);
          await page.getByRole('button', { name: 'Add 1 row', exact: true }).click();
          const added = await adding; expect(added.status(), await added.text()).toBe(200);
          values.push(value);
          await persisted(page, scopeProbe, actor, item.dataset, values);
        }
        await testInfo.attach(`rows-${label}`, { contentType: 'image/png', body: await page.screenshot() });
      } finally { await context.close(); }
    }
    for (const item of seeded) {
      const other = seeded.find(value => value !== item)!;
      const mapping = [{ col_name: 'operation', span_field: 'name', data_type: 'text' }];
      const baseline = await stored(scopeProbe, item.dataset);
      const state = () => scopeProbe.pg('SELECT (SELECT count(*) FROM model_hub_dataset WHERE organization_id=$1) AS datasets, (SELECT count(*) FROM model_hub_column WHERE dataset_id=$2) AS columns', [item.actor.organizationId, item.dataset]);
      const before = await state();
      for (const selected of [[other.first.spanIds[0]], [item.first.spanIds[0], other.first.spanIds[0]]]) {
        const refused = await scopeActors.send(item.actor, 'POST', CREATE, { project: item.project, span_ids: selected, mapping_config: mapping, new_dataset_name: `refused-${Date.now()}` });
        expect(refused.status).toBe(perturb === 'isolation' ? 200 : 400);
        expect((await scopeActors.send(item.actor, 'POST', ADD, { project: item.project, span_ids: selected, dataset_id: item.dataset, new_mapping_config: [{ ...mapping[0], col_name: 'must-not-exist' }] })).status).toBe(400);
      }
      const denied = [other.actor];
      if (item.actor.organizationId === scopeActors.ownerA.organizationId) denied.push(scopeActors.withWorkspace(item.actor, scopeActors.emptyWorkspace.id));
      for (const foreign of denied) {
        expect((await scopeActors.send(foreign, 'POST', ADD, { project: item.project, span_ids: [item.first.spanIds[0]], dataset_id: item.dataset, mapping_config: mapping })).status).toBe(400);
        expect((await scopeActors.send(foreign, 'POST', PREVIEW, { project: item.project, source_kind: 'traces' })).status).toBe(400);
      }
      expect(await state()).toEqual(before);
      expect(await stored(scopeProbe, item.dataset)).toEqual(baseline);
    }
  } finally { await req.dispose(); }
});
