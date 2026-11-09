import type { APIRequestContext, Locator, Page } from '@playwright/test';
import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { inspectManagedMock, registerMockModel } from '../../lib/managed-mock';
import { flowAnnotation } from '../../lib/flow-meta';
import { E2E } from '../../lib/env';

// model_hub/urls.py T-EVALB block: dag-create, dag-detail, dag-execute-adhoc, dag-runs, dag-paths.
const CREATE = '/model-hub/eval-templates/create-dag/';
const ADHOC = '/model-hub/eval-templates/dag/execute-adhoc/';
const tree = (id: string, tail = '') => `/model-hub/eval-templates/${id}/dag/${tail}`;
// model_hub/urls.py create-dataset-from-local-file (multipart, processed in the background).
const UPLOAD = '/model-hub/develops/create-dataset-from-local-file/';
const UI_READY = 60_000;

type Step = { node_id: string; answer: string; reason: string };
type Dag = { root: string; output_type: string; nodes: Array<Record<string, unknown>> };
type Paths = { run_id: string; n: number; paths: Array<{ label: string; n: number; rows: string[] }> };

const leaves = [
  { id: 'leaf1', kind: 'leaf', label: 'Resolved', score: 1 },
  { id: 'leaf2', kind: 'leaf', label: 'Unresolved', score: 0 },
];

/** The browser-authored check tree with a judged first question in front of it. */
function judgedTree(saved: Dag, model: string, marker: string): Dag {
  const check = saved.nodes.find(node => node.kind === 'check')!;
  return {
    ...saved,
    root: 'q1',
    nodes: [
      { id: 'q1', kind: 'judge', judge_model: model,
        question: `Reply with exactly this JSON: {"result":"Pass","explanation":"${marker} saw {{output}}"}`,
        answers: [{ label: 'Yes', next: check.id }, { label: 'No', next: 'off' }] },
      ...saved.nodes,
      { id: 'off', kind: 'leaf', label: 'Off topic', score: 0.2 },
    ],
  };
}

async function uploadReplies(request: APIRequestContext, actor: ScopeActor, name: string) {
  const csv = 'reply\nWe sent your refund today\nPlease hold while we check\nYour refund is approved\n';
  const response = await request.post(`${E2E.apiUrl}${UPLOAD}`, {
    headers: { Authorization: `Bearer ${actor.tokens.access}`,
      'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId },
    multipart: { new_dataset_name: name, model_type: 'GenerativeLLM',
      file: { name: `${name}.csv`, mimeType: 'text/csv', buffer: Buffer.from(csv) } },
  });
  expect(response.status()).toBe(200);
  return ((await response.json()) as { result: { dataset_id: string } }).result.dataset_id;
}

async function pick(page: Page, scope: Locator, name: string | RegExp, option: string) {
  await scope.getByRole('combobox', { name }).click();
  await page.getByRole('option', { name: option, exact: true }).click();
}

test.beforeAll(() => { inspectManagedMock(); });

test('EVAL-E2E-072: author, validate, test and run a scoped decision tree and read the paths it took', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-072', area: 'evals',
    userGoal: 'Build a decision tree of checks, see which path an output takes, and compare the paths across a dataset',
    steps: ['author a check with two scores in the decision tree editor and save it',
      'add a judged first question through the API and refuse cyclic or too-deep trees',
      'test one output in the browser through the loopback LLM STANDIN and read the path',
      'run the saved tree on an uploaded dataset from the browser',
      'read the stored paths in the browser and through the API',
      'refuse the tree, its runs and its tests to another organization and workspace'],
    backendChecks: ['the browser save persists exactly the authored check tree in the creating workspace',
      'cyclic and too-deep definitions are refused with nothing written',
      'the judged question reaches the loopback model and its verdict decides the path',
      'a run stores one result per dataset row and the paths report counts them',
      'foreign organization and workspace reads, writes, runs and tests are refused'],
  }),
}, async ({ browser, request, scopeActors, scopeProbe }, testInfo) => {
  // Five bounded UI phases (5 * 60 s), dataset processing (120 s) and seeding,
  // with 90 s teardown headroom.
  test.setTimeout(540_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const a = scopeActors.ownerA;
  const model = await registerMockModel(scopeActors.owners[0], scopeProbe);
  const marker = `tree-judge-${suffix}`;
  const name = `e2e-tree-${suffix}`;
  const datasetName = `e2e-tree-replies-${suffix}`;
  const datasetId = await uploadReplies(request, a, datasetName);
  await expect.poll(async () => (await scopeProbe.pg<{ n: string }>(
    'SELECT count(*) AS n FROM model_hub_cell WHERE dataset_id=$1 AND NOT deleted', [datasetId]))[0].n,
  { timeout: 120_000 }).toBe('3');

  const context = await scopeActors.openContext(browser, a);
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (['localhost', '127.0.0.1', '::1'].includes(url.hostname)) await route.continue();
    else await route.abort('blockedbyclient');
  });
  const page = await context.newPage();
  let id = '';
  await test.step('author a check tree in the editor and save it', async () => {
    // frontend/src/routes/sections/dashboard.jsx evaluations/create.
    await page.goto('/dashboard/evaluations/create');
    await page.getByPlaceholder('Eg: Hallucination detector').fill(name);
    await page.getByRole('tab', { name: 'Decision tree', exact: true }).click();
    const first = page.getByTestId('dag-node').first();
    await pick(page, first, 'Node kind', 'Check');
    await first.getByLabel('Text', { exact: true }).fill('refund');
    await pick(page, first, '1 Pass goes to', 'New score');
    await pick(page, first, '1 Fail goes to', 'New score');
    const scores = page.getByTestId('dag-node');
    await expect(scores).toHaveCount(3);
    for (const [index, leaf] of leaves.entries()) {
      await scores.nth(index + 1).getByLabel('Label', { exact: true }).fill(leaf.label);
      await scores.nth(index + 1).getByRole('spinbutton', { name: 'Score' }).fill(String(leaf.score));
    }
    const saved = page.waitForResponse(r => new URL(r.url()).pathname === CREATE && r.request().method() === 'POST');
    await page.getByTestId('dag-save').click();
    const response = await saved;
    expect(response.status()).toBe(200);
    id = ((await response.json()) as { result: { id: string } }).result.id;
    await page.waitForURL(`**/dashboard/evaluations/${id}`);
  }, { timeout: UI_READY });

  const authored = (await a.api.get<{ result: { dag: Dag } }>(tree(id))).result.dag;
  expect(authored).toEqual({ schema: 'fi-dag-v1', root: 'n1', output_type: 'score', nodes: [
    { id: 'n1', kind: 'check', check: { type: 'contains', on: 'output', value: 'refund' },
      answers: [{ label: 'Pass', next: 'leaf1' }, { label: 'Fail', next: 'leaf2' }] },
    ...leaves,
  ] });
  const stored = await scopeProbe.pg<{ eval_type: string; config: { dag: Dag } }>(
    'SELECT eval_type, config FROM model_hub_evaltemplate WHERE id=$1 AND organization_id=$2 AND workspace_id=$3',
    [id, a.organizationId, a.workspaceId]);
  expect(stored).toHaveLength(1);
  expect(stored[0].eval_type).toBe('dag');
  expect(stored[0].config.dag).toEqual(authored);

  const judged = judgedTree(authored, model.model, marker);
  for (const [dag, message] of [
    [{ ...judged, nodes: judged.nodes.map(n => n.id === 'n1'
      ? { ...n, answers: [{ label: 'Pass', next: 'q1' }, { label: 'Fail', next: 'leaf2' }] } : n) }, '2 loops back to 1'],
    [{ root: 'c0', nodes: [...Array.from({ length: 13 }, (_, i) => ({ id: `c${i}`, kind: 'check',
      check: { type: 'length', on: 'output', op: 'gte', n: 0 },
      answers: [{ label: 'Pass', next: i < 12 ? `c${i + 1}` : 'leaf1' }, { label: 'Fail', next: 'leaf1' }] })), leaves[0]] },
    'A path is at most 12 questions deep'],
  ] as const) {
    const refused = await scopeActors.send<{ errors: string[] }>(a, 'PATCH', tree(id), { dag });
    expect(refused.status).toBe(400);
    expect(refused.body.errors).toContain(message);
  }
  expect((await a.api.get<{ result: { dag: Dag } }>(tree(id))).result.dag).toEqual(authored);
  await a.api.patch(tree(id), { dag: judged });

  await model.assertReady();
  await page.reload();
  const detail = page.getByTestId('dag-detail');
  await expect(detail.getByTestId('dag-node')).toHaveCount(5, { timeout: UI_READY });
  await test.step('test one output and read the path it took', async () => {
    const panel = page.getByTestId('dag-test');
    await panel.getByRole('button', { name: 'Type values', exact: true }).click();
    await panel.getByLabel('Sample output', { exact: true }).fill('We sent your refund today');
    const tested = page.waitForResponse(r => new URL(r.url()).pathname === ADHOC, { timeout: 90_000 });
    await panel.getByRole('button', { name: 'Test', exact: true }).click();
    const response = await tested;
    expect(response.status()).toBe(200);
    const result = (await response.json()) as { result: { path_key: string; path: Step[] } };
    expect(result.result.path_key).toBe('q1:Yes>n1:Pass>leaf1');
    expect(result.result.path[0].reason).toContain(marker);
    await expect(panel.getByTestId('dag-test-total')).toContainText('Path: 1 Yes → 2 Pass → Resolved (1.00)');
  }, { timeout: 120_000 });

  let runId = '';
  await test.step('run the saved tree on the uploaded dataset', async () => {
    const panel = page.getByTestId('dag-run');
    await pick(page, panel, /^Dataset/, datasetName);
    await pick(page, panel, /^\{\{output\}\}/, 'reply');
    const ran = page.waitForResponse(r => new URL(r.url()).pathname === tree(id, 'runs/'), { timeout: 120_000 });
    await panel.getByRole('button', { name: 'Run on dataset', exact: true }).click();
    const response = await ran;
    expect(response.status()).toBe(200);
    runId = ((await response.json()) as { result: { run_id: string } }).result.run_id;
    const rows = page.getByTestId('dag-path-row');
    await expect(rows).toHaveCount(2, { timeout: UI_READY });
    await expect(rows.first()).toContainText('1 Yes → 2 Pass → Resolved');
    await expect(rows.first().getByTestId('dag-path-n')).toHaveText('2');
    await expect(rows.nth(1)).toContainText('1 Yes → 2 Fail → Unresolved');
  }, { timeout: 180_000 });

  const report = (await a.api.get<{ result: Paths }>(tree(id, 'paths/'), { run_id: runId })).result;
  expect(report.run_id).toBe(runId);
  expect(report.n).toBe(3);
  expect(report.paths.map(p => [p.label, p.n])).toEqual([
    ['1 Yes → 2 Pass → Resolved', 2], ['1 Yes → 2 Fail → Unresolved', 1]]);
  const results = await scopeProbe.pg<{ key: string }>(
    `SELECT c.data->'dag'->>'path_key' AS key FROM model_hub_evaluation c
     JOIN model_hub_evaluation r ON r.id=c.parent_evaluation_id
     WHERE r.id=$1 AND r.eval_template_id=$2 AND r.organization_id=$3 AND r.workspace_id=$4
       AND c.organization_id=$3 AND c.workspace_id=$4 ORDER BY key`,
    [runId, id, a.organizationId, a.workspaceId]);
  expect(results.map(r => r.key)).toEqual(['q1:Yes>n1:Fail>leaf2', 'q1:Yes>n1:Pass>leaf1', 'q1:Yes>n1:Pass>leaf1']);

  for (const outsider of [scopeActors.ownerB, scopeActors.withWorkspace(a, scopeActors.emptyWorkspace.id)]) {
    for (const [method, path, data] of [
      ['GET', tree(id), undefined],
      ['PATCH', tree(id), { description: 'taken over' }],
      ['GET', `${tree(id, 'paths/')}?run_id=${runId}`, undefined],
      ['POST', tree(id, 'runs/'), { dataset_id: datasetId, mapping: { output: datasetId } }],
      ['POST', ADHOC, { dag: judged, output: 'x', template_id: id }],
    ] as const) {
      const denied = await scopeActors.send(outsider, method, path, data);
      expect(denied.status).toBe(404);
    }
  }
  const after = await scopeProbe.pg<{ n: string; description: string }>(
    `SELECT (SELECT count(*) FROM model_hub_evaluation WHERE eval_template_id=$1 AND parent_evaluation_id IS NULL)::text AS n,
            (SELECT description FROM model_hub_evaltemplate WHERE id=$1) AS description`, [id]);
  expect(after[0].n).toBe('1');
  expect(after[0].description).not.toBe('taken over');
  await testInfo.attach('decision-tree-ids', { contentType: 'application/json', body: JSON.stringify({
    templateId: id, datasetId, runId, organizationId: a.organizationId, workspaceId: a.workspaceId,
    externalSystem: 'loopback LLM STANDIN', marker, report }) });
  await page.screenshot({ path: testInfo.outputPath('decision-tree-paths.png'), fullPage: true });
  await testInfo.attach('decision-tree-paths.png', { path: testInfo.outputPath('decision-tree-paths.png'), contentType: 'image/png' });
});
