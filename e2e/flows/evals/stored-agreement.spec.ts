import { randomUUID } from 'node:crypto';
import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// model_hub/urls.py and serializers/scores.py; labels use annotations-labels CRUD.
const CREATE_DATASET = '/model-hub/develops/create-empty-dataset/';
const CREATE_TEMPLATE = '/model-hub/eval-templates/create-v2/';
const LABELS = '/model-hub/annotations-labels/';
const SCORES = '/model-hub/scores/';
// AnnotationQueueViewSet and QueueItemViewSet.add_items; queue notes use Score PATCH.
const QUEUES = '/model-hub/annotation-queues/';
const EXPECT_VALUE_TIME_UNCHANGED = true;
const datasetPath = (id: string, action: string) => `/model-hub/develops/${id}/${action}/`;
const agreementPath = (id: string) => `/model-hub/eval-templates/${id}/agreement/`;
const UI_READY = 60_000;
// Assertion anchors only. Seeds below do not read these constants.
const EXPECTED_ACCURACY = 2 / 3;
const EXPECTED_FOREIGN = 404;
const EXPECTED_STORED_CHOICE = 'Pass';
type Envelope<T> = { result: T };
type Report = { key: string; n: number; accuracy: number; kappa: number | null; excluded: number; disagreements: { row_id: string; human: string; judge: string }[] };
type Answer = { sources: { key: string; label_name: string }[]; report: Report };
type Tenant = { actor: ScopeActor; name: string; datasetId: string; templateId: string; bindingId: string; rowIds: string[]; labelIds: string[]; labelNames: string[]; olderScoreId: string };
type ScoreClock = { id: string; value_updated_at: string; updated_at: string; notes: string | null; value: { selected: string[] } };

test('EVAL-E2E-012: inspect stored judge agreement with human labels without rescoring', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-012', area: 'evals',
    userGoal: 'Compare my saved dataset evaluation results with human labels and inspect disagreements without running the judge again',
    steps: [
      'save synthetic evaluation results and two human label sources through public APIs in each organization',
      'open Agreement and select each human label source',
      'read exact agreement and disagreement rows, edit notes on an older vote in another queue, then refresh',
      'verify saved values in PostgreSQL and deny foreign template, dataset, label and source requests',
    ],
    backendChecks: [
      'the browser agreement response and scoped API readback compute the known statistics from the saved label source',
      'PostgreSQL retains the exact submitted judge choices and human labels without a judge execution request',
      'notes-only edits persist without moving the value timestamp or changing the latest reviewer vote and displayed report',
      'foreign template, dataset, label and source identifiers return 404 without foreign names or stored changes',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // 120s provisioning + two tenants * (3 UI_READY + 30s API/storage) + 60s headroom.
  test.setTimeout(600_000);
  const ui = expect.configure({ timeout: UI_READY });
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const attach = (name: string, value: unknown) => testInfo.attach(name, { contentType: 'application/json', body: JSON.stringify(value) });
  async function call<T>(actor: ScopeActor, method: string, path: string, body?: unknown, params?: Record<string, string>) {
    const response = await scopes.send<T>(actor, method, path, body, params);
    expect(response.status, `${method} ${path}: ${JSON.stringify(response.body).slice(0, 300)}`).toBe(200);
    return response.body;
  }
  async function seed(actor: ScopeActor, tag: string): Promise<Tenant> {
    const name = `e2e-agreement-${tag}-${suffix}`;
    const ds = await call<Envelope<{ dataset_id: string }>>(actor, 'POST', CREATE_DATASET, { new_dataset_name: name });
    const datasetId = ds.result.dataset_id;
    await call(actor, 'POST', datasetPath(datasetId, 'add_static_column'), { new_column_name: 'output', column_type: 'text' });
    const [column] = await probe.pg<{ id: string }>("SELECT id FROM model_hub_column WHERE dataset_id=$1 AND name='output' AND NOT deleted", [datasetId]);
    const template = await call<Envelope<{ id: string }>>(actor, 'POST', CREATE_TEMPLATE, {
      name, eval_type: 'llm', model: 'gpt-4o-mini', output_type: 'pass_fail',
      instructions: 'Assess {{output}}', check_internet: false, error_localizer_enabled: false,
    });
    await call(actor, 'POST', datasetPath(datasetId, 'add_user_eval'), {
      name: `${name}-judge`, template_id: template.result.id, model: 'gpt-4o-mini', run: true,
      config: { mapping: { output: column.id }, config: {} },
    });
    const [binding] = await probe.pg<{ id: string }>('SELECT id FROM model_hub_userevalmetric WHERE dataset_id=$1 AND NOT deleted', [datasetId]);
    // Initialize a result column on the empty dataset, then stop before adding
    // any inputs. No rows are available for model execution during this setup.
    await call(actor, 'POST', datasetPath(datasetId, `stop_user_eval/${binding.id}`), {});
    const [resultColumn] = await probe.pg<{ name: string }>("SELECT name FROM model_hub_column WHERE dataset_id=$1 AND source='evaluation' AND source_id=$2 AND NOT deleted", [datasetId, binding.id]);
    expect(resultColumn).toBeTruthy();
    const rowIds = [randomUUID(), randomUUID(), randomUUID()];
    // The public add-rows writer accepts saved result values. This proves reads of
    // persisted results, not generation or historical execution of those results.
    await call(actor, 'POST', datasetPath(datasetId, 'add_rows'), {
      rows: rowIds.map((id, index) => ({ id, cells: [
        { column_name: 'output', value: `${name}-${index}` },
        { column_name: resultColumn.name, value: ['Pass', "{'score': 1, 'choice': 'Pass'}", 'Fail'][index] },
      ] })),
    });
    const labelIds: string[] = [], labelNames: string[] = [];
    for (const label of ['alpha', 'beta']) {
      const labelName = `${name}-${label}`;
      const created = await call<Envelope<{ id: string }>>(actor, 'POST', LABELS, {
        name: labelName, type: 'categorical', settings: {
          options: [{ label: 'Pass' }, { label: 'Fail' }], multi_choice: false,
          rule_prompt: '', auto_annotate: false, strategy: null,
        },
      });
      labelIds.push(created.result.id); labelNames.push(labelName);
      for (const [index, id] of rowIds.entries()) await call(actor, 'POST', SCORES, {
        source_type: 'dataset_row', source_id: id, label_id: created.result.id,
        value: { selected: [(label === 'alpha' ? index === 0 : index < 2) ? 'Pass' : 'Fail'] },
      });
    }
    const [older] = await probe.pg<{ id: string }>('SELECT id FROM model_hub_score WHERE label_id=$1 AND dataset_row_id=$2 AND NOT deleted', [labelIds[0], rowIds[0]]);
    await call(actor, 'PATCH', `${SCORES}${older.id}/`, { value: { selected: ['Fail'] } });
    const queue = await scopes.send<{ id: string }>(actor, 'POST', QUEUES, { name: `${name}-second-review`, label_ids: [labelIds[0]] });
    expect(queue.status).toBe(201);
    await call(actor, 'POST', `${QUEUES}${queue.body.id}/update-status/`, { status: 'active' });
    await call(actor, 'POST', `${QUEUES}${queue.body.id}/items/add-items/`, { items: [{ source_type: 'dataset_row', source_id: rowIds[0] }] });
    const [item] = await probe.pg<{ id: string }>('SELECT id FROM model_hub_queueitem WHERE queue_id=$1 AND NOT deleted', [queue.body.id]);
    await call(actor, 'POST', SCORES, { source_type: 'dataset_row', source_id: rowIds[0], label_id: labelIds[0], queue_item_id: item.id, value: { selected: ['Pass'] } });
    return { actor, name, datasetId, templateId: template.result.id, bindingId: binding.id, rowIds, labelIds, labelNames, olderScoreId: older.id };
  }
  const a = await seed(scopes.ownerA, 'a'), b = await seed(scopes.ownerB, 'b');
  await attach('seeded-identities', [a, b].map(({ actor, ...t }) => ({ ...t, organizationId: actor.organizationId, workspaceId: actor.workspaceId })));
  const stored = (t: Tenant) => probe.pg<{ row_id: string; value: string }>(
    "SELECT c.row_id,c.value FROM model_hub_cell c JOIN model_hub_column col ON col.id=c.column_id JOIN model_hub_row r ON r.id=c.row_id WHERE c.dataset_id=$1 AND col.source='evaluation' AND col.source_id=$2 AND NOT c.deleted ORDER BY r.\"order\"", [t.datasetId, t.bindingId]);
  const baseline = [await stored(a), await stored(b)];
  for (const t of [a, b]) {
    const context = await scopes.openContext(browser, t.actor);
    await context.route(url => /^https?:$/.test(url.protocol) && !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname), route => route.abort());
    const page = await context.newPage(); page.setDefaultTimeout(UI_READY);
    const execution: string[] = [];
    page.on('request', request => {
      const path = new URL(request.url()).pathname;
      if (request.method() === 'POST' && (/eval-playground|execute|start_evals_process|\/improve\//.test(path))) execution.push(path);
    });
    const pending = page.waitForResponse(response => new URL(response.url()).pathname === agreementPath(t.templateId));
    await page.goto(`/dashboard/evaluations/${t.templateId}?tab=agreement`, { waitUntil: 'domcontentloaded' });
    const response = await pending;
    // EvalAgreementView publishes only GET in the generated API contract.
    expect(response.request().method(), 'AGREEMENT_NEG_METHOD').toBe('GET');
    expect(response.status()).toBe(200);
    expect(response.request().headers()['x-organization-id']).toBe(t.actor.organizationId);
    expect(response.request().headers()['x-workspace-id']).toBe(t.actor.workspaceId);
    const answer = (await response.json() as Envelope<Answer>).result;
    expect(answer.sources.map(s => s.label_name)).toEqual(t.labelNames);
    expect(answer.report.accuracy, 'AGREEMENT_NEG_API').toBeCloseTo(EXPECTED_ACCURACY);
    expect(answer.report.n).toBe(3);
    await ui(page.getByTestId('w7-headline')).toHaveText('Agreement 67% · κ 0.40 · n 3');
    await ui(page.getByRole('table', { name: 'Stored disagreements' }).locator('tbody tr')).toHaveText([`${t.rowIds[1]}FailPass`]);
    await page.getByRole('combobox', { name: 'Human label source' }).click();
    await page.getByRole('option', { name: `${t.name} · ${t.name}-judge · ${t.labelNames[1]}`, exact: true }).click();
    await ui(page.getByTestId('w7-headline')).toHaveText('Agreement 100% · κ 1.00 · n 3');
    await ui(page.getByRole('table', { name: 'Stored disagreements' }).locator('tbody tr')).toHaveCount(0);
    await page.getByRole('combobox', { name: 'Human label source' }).click();
    await page.getByRole('option', { name: `${t.name} · ${t.name}-judge · ${t.labelNames[0]}`, exact: true }).click();
    await ui(page.getByTestId('w7-headline')).toHaveText('Agreement 67% · κ 0.40 · n 3');
    const clock = async () => (await probe.pg<ScoreClock>('SELECT id,value_updated_at::text,updated_at::text,notes,value FROM model_hub_score WHERE id=$1', [t.olderScoreId]))[0];
    const beforeNote = await clock();
    await call(t.actor, 'PATCH', `${SCORES}${t.olderScoreId}/`, { notes: `${t.name}-clarification` });
    const afterNote = await clock();
    expect(afterNote.notes).toBe(`${t.name}-clarification`);
    expect(afterNote.updated_at).not.toBe(beforeNote.updated_at);
    expect(afterNote.value).toEqual({ selected: ['Fail'] });
    expect(afterNote.value_updated_at === beforeNote.value_updated_at, 'AGREEMENT_NEG_VALUE_TIME').toBe(EXPECT_VALUE_TIME_UNCHANGED);
    const refresh = page.waitForResponse(r => new URL(r.url()).pathname === agreementPath(t.templateId));
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    const refreshed = await refresh;
    expect(refreshed.status()).toBe(200);
    expect((await refreshed.json() as Envelope<Answer>).result.report).toEqual(answer.report);
    await ui(page.getByTestId('w7-headline')).toHaveText('Agreement 67% · κ 0.40 · n 3');
    await attach(`notes-${t.name}`, { beforeNote, afterNote });
    await ui(page.getByRole('button', { name: /Improve judge|Train judge|Compare with another/ })).toHaveCount(0);
    expect(execution).toEqual([]);
    const readback = await call<Envelope<Answer>>(t.actor, 'GET', agreementPath(t.templateId), undefined, { source: `${t.bindingId}:${t.labelIds[0]}` });
    expect(readback.result.report).toEqual(answer.report);
    const cells = await stored(t);
    expect(cells.map(c => c.row_id)).toEqual(t.rowIds);
    expect(cells.map(c => c.value), 'AGREEMENT_NEG_STORAGE').toEqual([EXPECTED_STORED_CHOICE, "{'score': 1, 'choice': 'Pass'}", 'Fail']);
    const scores = await probe.pg<{ row_id: string; value: { selected: string[] }; organization_id: string; workspace_id: string }>(
      'SELECT DISTINCT ON (r."order") s.dataset_row_id AS row_id,s.value,s.organization_id,s.workspace_id FROM model_hub_score s JOIN model_hub_row r ON r.id=s.dataset_row_id WHERE s.label_id=$1 AND NOT s.deleted ORDER BY r."order",s.value_updated_at DESC,s.created_at DESC,s.id DESC', [t.labelIds[0]]);
    expect(scores).toEqual(t.rowIds.map((id, index) => ({ row_id: id, value: { selected: [index === 0 ? 'Pass' : 'Fail'] }, organization_id: t.actor.organizationId, workspace_id: t.actor.workspaceId })));
    await attach(`saved-${t.name}`, { answer, readback, cells, scores });
    await context.close();
  }
  for (const [own, foreign] of [[a, b], [b, a]]) {
    for (const [templateId, params] of [
      [foreign.templateId, {}], [own.templateId, { dataset_id: foreign.datasetId }],
      [own.templateId, { label_id: foreign.labelIds[0] }],
      [own.templateId, { source: `${foreign.bindingId}:${foreign.labelIds[0]}` }],
    ] as [string, Record<string, string>][]) {
      const response = await scopes.send(own.actor, 'GET', agreementPath(templateId), undefined, params);
      expect(response.status, 'AGREEMENT_NEG_TENANCY').toBe(EXPECTED_FOREIGN);
      expect(JSON.stringify(response.body)).not.toContain(foreign.name);
    }
  }
  expect([await stored(a), await stored(b)]).toEqual(baseline);
  await attach('scope-receipts', scopes.receipts);
});
