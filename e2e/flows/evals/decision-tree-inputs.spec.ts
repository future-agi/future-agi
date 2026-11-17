import type { Locator, Page } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { inspectManagedMock, registerMockModel } from '../../lib/managed-mock';
import { flowAnnotation } from '../../lib/flow-meta';
import { E2E } from '../../lib/env';

// model_hub/urls.py T-EVALB routes and DagInputsRequestSerializer.
const CREATE = '/model-hub/eval-templates/create-dag/';
const INPUTS = '/model-hub/eval-templates/dag/inputs/';
const ADHOC = '/model-hub/eval-templates/dag/execute-adhoc/';
const tree = (id: string, tail = '') => `/model-hub/eval-templates/${id}/dag/${tail}`;
const UI_READY = 60_000;
type Dag = { root: string; output_type: string; nodes: Array<Record<string, any>> };
type Result = { score: number; value: number; passed: boolean; path_key: string; path: Array<{ reason: string }> };

async function pick(page: Page, scope: Locator, name: string | RegExp, option: string) {
  await scope.getByRole('combobox', { name }).click();
  await page.getByRole('option', { name: option, exact: true }).click();
}

test.beforeAll(() => { inspectManagedMock(); });

test('EVAL-E2E-073: child eval and tool inputs reach decision-tree tests and precise stored scores', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-073', area: 'evals',
    userGoal: 'Author a tree using an existing eval and a tool-call check, then test and run it with every input and an exact score',
    steps: ['select an existing eval in the editor and connect a tool-call check and scores',
      'save and reload the definition with its required inputs',
      'test typed expected and tool-call inputs through the loopback LLM STANDIN',
      'map dataset columns, run, and inspect precise stored paths',
      'refuse missing inputs and cross-tenant child resolution and execution'],
    backendChecks: ['saved definitions resolve the child eval required keys in the current workspace',
      'typed expected and tool-call values reach the evaluator and choose the tool-called path',
      'dataset runs store 0.499 unchanged with a failing verdict in both score and pass/fail modes',
      'missing inputs and foreign organization or workspace access are refused without extra runs'],
  }),
}, async ({ browser, request, scopeActors, scopeProbe }, testInfo) => {
  test.setTimeout(600_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const a = scopeActors.ownerA;
  const model = await registerMockModel(scopeActors.owners[0], scopeProbe);
  const name = `e2e-tree-inputs-${suffix}`;
  const childName = `${name}-child`;
  const datasetName = `${name}-rows`;
  const expected = `reference-${suffix}`;
  // EvalTemplateCreateV2RequestSerializer; loopback echo judge format.
  const child = (await a.api.post<{ result: { id: string } }>('/model-hub/eval-templates/create-v2/', {
    name: childName, eval_type: 'llm', model: model.model, output_type: 'pass_fail', pass_threshold: 0.5,
    instructions: 'Reply with exactly this JSON: {"result":"Pass","explanation":"saw {{output}} against {{expected}}"}',
  })).result.id;
  // create-dataset-from-local-file multipart; JSON is a quoted CSV cell.
  const csv = `reply,expected,calls\nanswer,${expected},"[{""function"":{""name"":""lookup""}}]"\nanswer,${expected},[]\n`;
  const upload = await request.post(`${E2E.apiUrl}/model-hub/develops/create-dataset-from-local-file/`, {
    headers: { Authorization: `Bearer ${a.tokens.access}`, 'X-Organization-Id': a.organizationId, 'X-Workspace-Id': a.workspaceId },
    multipart: { new_dataset_name: datasetName, model_type: 'GenerativeLLM',
      file: { name: `${datasetName}.csv`, mimeType: 'text/csv', buffer: Buffer.from(csv) } },
  });
  expect(upload.status()).toBe(200);
  const datasetId = (await upload.json()).result.dataset_id as string;
  await testInfo.attach('seeded-input-ids', { contentType: 'application/json', body: JSON.stringify({ child, datasetId, expected,
    organizationId: a.organizationId, workspaceId: a.workspaceId, externalSystem: 'loopback LLM STANDIN' }) });
  await expect.poll(async () => (await scopeProbe.pg<{ n: string }>(
    'SELECT count(*) AS n FROM model_hub_cell WHERE dataset_id=$1 AND NOT deleted', [datasetId]))[0].n,
  { timeout: 120_000 }).toBe('6');
  await model.assertReady();
  const context = await scopeActors.openContext(browser, a);
  await context.route('**/*', async route => {
    if (['localhost', '127.0.0.1', '::1'].includes(new URL(route.request().url()).hostname)) await route.continue();
    else await route.abort('blockedbyclient');
  });
  const page = await context.newPage();
  await page.goto('/dashboard/evaluations/create');
  await page.getByPlaceholder('e.g. hallucination_detector').fill(name);
  await page.getByRole('tab', { name: 'Decision tree', exact: true }).click();
  const first = page.getByTestId('dag-node').first();
  await pick(page, first, 'Node kind', 'Eval');
  await pick(page, first, /^Eval/, childName);
  await pick(page, first, '1 Pass goes to', 'New check');
  await pick(page, first, '1 Fail goes to', 'New score');
  const check = page.getByTestId('dag-node').nth(1);
  await pick(page, check, /^Check/, 'Tool called');
  await expect(check.getByLabel('On', { exact: true })).toHaveValue('tool_calls');
  await check.getByLabel('Tool name').fill('lookup');
  await pick(page, check, '2 Pass goes to', 'New score');
  const nodes = page.getByTestId('dag-node');
  await nodes.nth(2).getByLabel('Label', { exact: true }).fill('Not called');
  await nodes.nth(2).getByRole('spinbutton', { name: 'Score' }).fill('0');
  await nodes.nth(3).getByLabel('Label', { exact: true }).fill('Near threshold');
  await nodes.nth(3).getByRole('spinbutton', { name: 'Score' }).fill('0.499');
  await pick(page, check, '2 Fail goes to', 'Not called (0.00)');
  await page.getByRole('spinbutton', { name: 'Pass at ≥' }).fill('0.5');
  await expect(page.getByTestId('dag-test').getByRole('combobox', { name: /^\{\{expected\}\}/ })).toBeVisible({ timeout: UI_READY });
  const saved = page.waitForResponse(r => new URL(r.url()).pathname === CREATE && r.request().method() === 'POST');
  await page.getByTestId('dag-save').click();
  const saveResponse = await saved;
  expect(saveResponse.status()).toBe(200);
  const id = (await saveResponse.json()).result.id as string;
  await page.waitForURL(`**/dashboard/evaluations/${id}`);
  await expect(page.getByTestId('dag-detail').getByTestId('dag-node')).toHaveCount(4, { timeout: UI_READY });
  await page.reload({ waitUntil: 'domcontentloaded' });
  const dag = (await a.api.get<{ result: { dag: Dag } }>(tree(id))).result.dag;
  expect(dag.nodes.find(n => n.kind === 'eval')?.template_id).toBe(child);
  expect(dag.nodes.find(n => n.kind === 'check')?.check).toEqual({ type: 'tool_called', on: 'tool_calls', value: 'lookup' });
  const inputs = await a.api.post<{ result: { required_keys: string[] } }>(INPUTS, { dag });
  expect(inputs.result.required_keys).toEqual(['output', 'expected', 'tool_calls']);
  const panel = page.getByTestId('dag-test');
  await panel.getByRole('button', { name: 'Type values', exact: true }).click();
  await panel.getByLabel('Sample output', { exact: true }).fill('answer');
  await panel.getByLabel('{{expected}}', { exact: true }).fill(expected);
  await panel.getByLabel('{{tool_calls}}', { exact: true }).fill('[{"function":{"name":"lookup"}}]');
  const tested = page.waitForResponse(r => new URL(r.url()).pathname === ADHOC, { timeout: 90_000 });
  await panel.getByRole('button', { name: 'Test', exact: true }).click();
  const testResponse = await tested;
  expect(testResponse.status()).toBe(200);
  const result = ((await testResponse.json()) as { result: Result }).result;
  expect(result.path[0].reason).toContain(expected);
  expect(result.path[1].reason).toBe('lookup was called');
  expect(result.score).toBe(0.499);
  expect(result.value).toBe(0.499);
  expect(result.passed).toBe(false);
  await expect(panel.getByTestId('dag-test-total')).toContainText('Fail');
  const runPanel = page.getByTestId('dag-run');
  await pick(page, runPanel, /^Dataset/, datasetName);
  for (const [key, column] of [['output', 'reply'], ['expected', 'expected'], ['tool_calls', 'calls']]) {
    await pick(page, runPanel, new RegExp(`^\\{\\{${key}\\}\\}`), column);
  }
  const ran = page.waitForResponse(r => new URL(r.url()).pathname === tree(id, 'runs/'), { timeout: 120_000 });
  await runPanel.getByRole('button', { name: 'Run on dataset', exact: true }).click();
  const runResponse = await ran;
  expect(runResponse.status()).toBe(200);
  const run = (await runResponse.json()).result;
  expect(run.rows.map((r: Result) => [r.value, r.passed])).toEqual([[0.499, false], [0, false]]);
  await expect(page.getByTestId('dag-path-row')).toHaveCount(2, { timeout: UI_READY });
  const report = (await a.api.get<{ result: { paths: Array<{ mean: number }> } }>(tree(id, 'paths/'), { run_id: run.run_id })).result;
  expect(report.paths.map(p => p.mean).sort()).toEqual([0, 0.499]);
  const stored = await scopeProbe.pg<{ output_float: number; output_bool: boolean }>(
    'SELECT output_float, output_bool FROM model_hub_evaluation WHERE parent_evaluation_id=$1 AND organization_id=$2 AND workspace_id=$3 ORDER BY output_float',
    [run.run_id, a.organizationId, a.workspaceId]);
  expect(stored).toEqual([{ output_float: 0, output_bool: false }, { output_float: 0.499, output_bool: false }]);
  // The same near-threshold score must remain failing in binary output mode.
  const binary = await a.api.post<{ result: Result }>(ADHOC, { dag: { ...dag, output_type: 'pass_fail' }, output: 'answer',
    values: { expected, tool_calls: '[{"name":"lookup"}]' } });
  expect([binary.result.score, binary.result.value, binary.result.passed]).toEqual([0.499, 0, false]);
  await pick(page, page.getByTestId('dag-editor'), /^Output/, 'Pass / Fail');
  const patched = page.waitForResponse(r => new URL(r.url()).pathname === tree(id) && r.request().method() === 'PATCH');
  await page.getByTestId('dag-save-version').click();
  expect((await patched).status()).toBe(200);
  const ranBinary = page.waitForResponse(r => new URL(r.url()).pathname === tree(id, 'runs/'), { timeout: 120_000 });
  await runPanel.getByRole('button', { name: 'Run on dataset', exact: true }).click();
  const binaryRunResponse = await ranBinary;
  expect(binaryRunResponse.status()).toBe(200);
  const binaryRun = (await binaryRunResponse.json()).result;
  expect(binaryRun.rows.map((r: Result) => [r.value, r.passed])).toEqual([[0, false], [0, false]]);
  const binaryStored = await scopeProbe.pg<{ output_float: number; output_bool: boolean; value: string }>(
    'SELECT output_float, output_bool, value FROM model_hub_evaluation WHERE parent_evaluation_id=$1 AND organization_id=$2 AND workspace_id=$3 ORDER BY output_float',
    [binaryRun.run_id, a.organizationId, a.workspaceId]);
  expect(binaryStored).toEqual([{ output_float: 0, output_bool: false, value: '0' }, { output_float: 0.499, output_bool: false, value: '0' }]);
  const missing = await scopeActors.send(a, 'POST', ADHOC, { dag, output: 'answer', values: { tool_calls: '[]' } });
  expect(missing.status).toBe(400);
  for (const outsider of [scopeActors.ownerB, scopeActors.withWorkspace(a, scopeActors.emptyWorkspace.id)]) {
    expect((await scopeActors.send(outsider, 'POST', INPUTS, { dag })).status).toBe(400);
    expect((await scopeActors.send(outsider, 'POST', ADHOC, { dag, output: 'answer', template_id: id })).status).toBe(404);
    expect((await scopeActors.send(outsider, 'GET', tree(id, 'paths/'))).status).toBe(404);
  }
  const count = await scopeProbe.pg<{ n: string }>('SELECT count(*) AS n FROM model_hub_evaluation WHERE eval_template_id=$1 AND parent_evaluation_id IS NULL', [id]);
  expect(count[0].n).toBe('2');
  await testInfo.attach('tree-input-results', { contentType: 'application/json', body: JSON.stringify({ id, runId: run.run_id, result, stored }) });
});
