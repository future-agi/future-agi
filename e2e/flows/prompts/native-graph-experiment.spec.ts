import { randomUUID } from 'node:crypto';
import { test, expect } from '../../lib/scope-actors';
import type { ScopeActor, ScopeActors } from '../../lib/scope-actors';
import { inspectManagedMock, registerMockModel, MOCK_MODEL } from '../../lib/managed-mock';
import { uploadFixture } from '../../lib/upload';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// agent_playground/urls.py, GraphCreateSerializer and VersionCreateSerializer.
const GRAPHS = '/agent-playground/graphs/';
// UploadFileModal.jsx and CreateDatasetFromLocalFileRequestSerializer.
const UPLOAD = '/model-hub/develops/create-dataset-from-local-file/';
// model_hub/urls.py, ExperimentCreateV2Serializer and ExperimentDetailV2Serializer.
const EXPERIMENTS = '/model-hub/experiments/v2/';
const UI_READY = 60_000;
type Graph = { id: string; name: string };
type Version = { id: string; version_number: number };
type Detail = { id: string; name: string; status: string; dataset_id: string;
  snapshot_dataset_id: string; agent_configs: { agent_id: string; agent_version: string }[] };
type ResultCell = { cell_value: string; status: string };
type Rows = { table: ({ row_id: string } & Record<string, ResultCell>)[] };

async function graphVersion(scopes: ScopeActors, actor: ScopeActor,
  graphId: string, templateId: string, prefix: string): Promise<Version> {
  // NodeWriteSerializer and PortWriteSerializer: native input/output ports, no provider config.
  const response = await scopes.send<{ result: Version }>(actor, 'POST', `${GRAPHS}${graphId}/versions/`, {
    status: 'active', commit_message: prefix,
    nodes: [{ id: randomUUID(), type: 'atomic', name: 'Transform', node_template_id: templateId,
      config: { input_key: 'region', transform: 'upper', prefix },
      ports: [
        { id: randomUUID(), key: 'custom', display_name: 'region', direction: 'input', data_schema: { type: 'string' } },
        { id: randomUUID(), key: 'custom', display_name: 'response', direction: 'output', data_schema: { type: 'string' } },
      ] }], node_connections: [],
  });
  expect(response.status).toBe(201);
  return response.body.result;
}

test('PROMPT-E2E-007: run a saved graph version and isolate its experiment results', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'PROMPT-E2E-007', area: 'prompts',
    userGoal: 'Choose an older saved agent graph version, run it on dataset rows and inspect only authorized persisted results',
    steps: [
      'seed two tenant-owned graphs and two versions of the owned graph through public APIs',
      'upload synthetic CSV through the file dialog',
      'choose Agent graph, excluding Already running and the foreign graph',
      'select the older saved version, with global-variable mapping hidden',
      'select the loopback-STANDIN evaluation group and submit the experiment',
      'read exact completed native outputs in Postgres, the rows API and the classic grid',
      'reject foreign graph and version admission and foreign experiment reads without mutation',
    ],
    backendChecks: [
      'the browser-selected graph and older version persist on a snapshot in the caller organization and workspace',
      'the real native graph worker writes exactly the independently expected transformed input for every imported row',
      'the loopback-STANDIN evaluator completes on all three native outputs',
      'the rows API and classic grid expose the same exact completed outputs',
      'foreign graph and version admission and foreign experiment reads are refused without changing the owner experiment or creating another experiment',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // 12 UI_READY + 2 ASYNC_JOB + EVAL_RESULT + 150s setup/readback headroom.
  test.setTimeout(1_080_000);
  const receipt = inspectManagedMock();
  await testInfo.attach('loopback-LLM-STANDIN', { contentType: 'application/json', body: JSON.stringify(receipt) });
  const a = scopes.ownerA;
  const b = scopes.ownerB;
  const model = await registerMockModel(scopes.owners[0], probe, async () => expect(inspectManagedMock()).toEqual(receipt));
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const name = `e2e-native-${suffix}`;
  const graphName = `${name}-owned`;
  const foreignName = `${name}-foreign`;
  const prefix = `${name}:`;
  // Only the native node template is provisioned on the isolated stack; never results.
  const templates = await probe.pg<{ id: string }>(
    'SELECT id FROM agent_playground_node_template WHERE name=$1 AND NOT deleted', ['deterministic_text']);
  expect(templates, 'The isolated stack must provision the native node template').toHaveLength(1);
  const own = await scopes.send<{ result: Graph }>(a, 'POST', GRAPHS, { name: graphName });
  const foreign = await scopes.send<{ result: Graph }>(b, 'POST', GRAPHS, { name: foreignName });
  expect(own.status).toBe(201);
  expect(foreign.status).toBe(201);
  const graph = own.body.result;
  const foreignGraph = foreign.body.result;
  const chosen = await graphVersion(scopes, a, graph.id, templates[0].id, prefix);
  const newer = await graphVersion(scopes, a, graph.id, templates[0].id, `${name}-newer:`);
  const foreignVersion = await graphVersion(scopes, b, foreignGraph.id, templates[0].id, `${name}-foreign:`);
  // get_global_variable_names_for_versions exposes the native input, making the mapping negative non-vacuous.
  const listedVersions = await a.api.get<{ result: { versions: (Version & { global_variables: string[] })[] } }>(`${GRAPHS}${graph.id}/versions/`);
  expect(listedVersions.result.versions.find(version => version.id === chosen.id)?.global_variables).toEqual(['region']);
  // EvalTemplateCreateV2Serializer: echo-compatible evaluation through the managed STANDIN.
  const evaluator = await a.api.post<{ result: { id: string } }>('/model-hub/eval-templates/create-v2/', {
    name: `${name}-judge`, eval_type: 'llm', model: MOCK_MODEL, output_type: 'deterministic',
    instructions: `Reply with exactly this JSON: {"result":"Pass","explanation":"${name}-verdict"}\nMapped input: {{output}}`,
    pass_threshold: 0.5, choice_scores: { Pass: 1, Fail: 0 }, check_internet: false,
    error_localizer_enabled: false, template_format: 'mustache',
  });
  const group = await a.api.post<{ result: { id: string } }>('/model-hub/eval-groups/', {
    name: `${name}-group`, description: 'Synthetic native graph evaluation', eval_template_ids: [evaluator.result.id],
  });
  await testInfo.attach('native-graph-seed', { contentType: 'application/json', body: JSON.stringify({
    graph, chosen, newer, foreignGraph, foreignVersion, evaluatorId: evaluator.result.id,
    organizationId: a.organizationId, workspaceId: a.workspaceId, foreignOrganizationId: b.organizationId,
  }) });
  const context = await scopes.openContext(browser, a);
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    return ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname) || ['data:', 'blob:'].includes(url.protocol)
      ? route.continue() : route.abort('blockedbyclient');
  });
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  try {
    await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
    await page.getByText('New dataset', { exact: true }).click();
    await page.getByText('Upload a file', { exact: true }).click();
    const upload = page.getByRole('dialog').filter({ has: page.getByText('Upload a file', { exact: true }) });
    await uploadFixture(upload.locator('input[type="file"]'), 'catalog-regions.csv');
    await upload.getByPlaceholder('Dataset name').fill(`${name}-data`);
    const uploaded = page.waitForResponse(r => new URL(r.url()).pathname === UPLOAD && r.request().method() === 'POST', { timeout: UI_READY });
    await upload.getByRole('button', { name: 'Import 3 rows', exact: true }).click();
    const uploadResponse = await uploaded;
    expect(uploadResponse.status()).toBe(200);
    const datasetId = (await uploadResponse.json()).result.dataset_id as string;
    await expect.poll(async () => (await probe.pg<{ n: string }>(
      'SELECT count(*) AS n FROM model_hub_cell WHERE dataset_id=$1 AND NOT deleted', [datasetId]))[0].n,
    POLL.ASYNC_JOB).toBe('6');
    await expect(page).toHaveURL(new RegExp(`/develop/${datasetId}([?]|$)`), { timeout: UI_READY });
    await page.getByTestId('new-experiment-kinds').first().click();
    const kinds = page.getByRole('menu', { name: 'What to run', exact: true });
    await expect(kinds.getByRole('menuitem')).toHaveCount(2, { timeout: UI_READY });
    await expect(page.getByTestId('new-experiment-kind-running')).toHaveCount(0);
    await page.getByTestId('new-experiment-kind-agent').click();
    const picker = page.getByTestId('run-target-picker');
    await picker.getByPlaceholder('Search agents').fill(name);
    await expect(picker.getByText(graphName, { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(picker.getByText(foreignName, { exact: true })).toHaveCount(0);
    await picker.getByText(graphName, { exact: true }).click();
    const drawer = page.getByRole('dialog', { name: 'New experiment', exact: true });
    await drawer.getByRole('textbox', { name: /^Experiment name/ }).fill(`${name}-experiment`);
    await expect(drawer.getByRole('textbox', { name: /^Experiment name/ })).toHaveValue(`${name}-experiment`, { timeout: UI_READY });
    await drawer.getByRole('button', { name: 'Version', exact: true }).click();
    await page.getByRole('menuitem').filter({ has: page.getByText(`v${chosen.version_number}`, { exact: true }) }).click();
    await expect(drawer.getByText('Map on the Data step', { exact: true })).toHaveCount(0);
    await expect(drawer.getByRole('button', { name: 'Add model', exact: true })).toHaveCount(0);
    await drawer.getByTestId('wizard-continue').click();
    await expect(drawer.getByTestId('nothing-to-map')).toHaveText("Set on each run's card — no {{variables}} to map here.", { timeout: UI_READY });
    await expect(drawer.locator('[data-testid^="column-select-"]')).toHaveCount(0);
    await drawer.getByTestId('wizard-continue').click();
    await drawer.getByRole('button', { name: 'Add a group', exact: true }).click();
    await page.getByPlaceholder('Search groups', { exact: true }).fill(`${name}-group`);
    const groupRows = page.getByTestId('add-eval-group-row');
    await expect(groupRows).toHaveCount(1, { timeout: UI_READY });
    await groupRows.click();
    await expect(drawer.getByText(`${name}-judge`, { exact: true }).filter({ visible: true })).toHaveCount(1, { timeout: UI_READY });
    await drawer.getByTestId('wizard-continue').click();
    await model.assertReady();
    const submittedResponse = page.waitForResponse(r => new URL(r.url()).pathname === EXPERIMENTS && r.request().method() === 'POST', { timeout: UI_READY });
    await drawer.getByRole('button', { name: /^Run experiment/ }).click();
    const response = await submittedResponse;
    expect(response.status()).toBe(200);
    const submitted = response.request().postDataJSON();
    expect(submitted.prompt_config).toEqual([{ name: graphName, agent_id: graph.id, agent_version: chosen.id }]);
    expect(submitted.user_eval_metrics.map((metric: { template_id: string }) => metric.template_id)).toEqual([evaluator.result.id]);
    const experimentId = /experiment_id=([0-9a-f-]{36})/.exec((await response.json()).result)?.[1];
    expect(experimentId).toBeTruthy();
    await testInfo.attach('submitted-experiment', { contentType: 'application/json', body: JSON.stringify({ datasetId, experimentId, submitted }) });
    const detailPath = `${EXPERIMENTS}${experimentId}/`;
    const readDetail = async () => (await a.api.get<{ result: Detail }>(detailPath)).result;
    await expect.poll(async () => (await readDetail()).status, POLL.EVAL_RESULT).toBe('Completed');
    const detail = await readDetail();
    expect(detail.name).toBe(`${name}-experiment`);
    expect(detail.dataset_id).toBe(datasetId);
    expect(detail.agent_configs.map(c => ({ agent_id: c.agent_id, agent_version: c.agent_version })))
      .toEqual([{ agent_id: graph.id, agent_version: chosen.id }]);
    expect(await probe.pg('SELECT organization_id,workspace_id FROM model_hub_dataset WHERE id=$1 AND NOT deleted',
      [detail.snapshot_dataset_id])).toEqual([{ organization_id: a.organizationId, workspace_id: a.workspaceId }]);
    const columns = await probe.pg<{ id: string }>(
      "SELECT id FROM model_hub_column WHERE dataset_id=$1 AND source='experiment' AND NOT deleted", [detail.snapshot_dataset_id]);
    expect(columns).toHaveLength(1);
    const inputColumns = await probe.pg<{ id: string; name: string }>(
      "SELECT id,name FROM model_hub_column WHERE dataset_id=$1 AND name IN ('region','quantity') AND NOT deleted ORDER BY name", [detail.snapshot_dataset_id]);
    expect(inputColumns.map(column => column.name)).toEqual(['quantity', 'region']);
    const inputIds = Object.fromEntries(inputColumns.map(column => [column.name, column.id]));
    const inputs = await probe.pg<{ row_id: string; region: string; quantity: string }>(
      `SELECT r.row_id,r.value AS region,q.value AS quantity FROM model_hub_cell r
       JOIN model_hub_cell q ON q.row_id=r.row_id AND q.column_id=$2 AND NOT q.deleted
       WHERE r.column_id=$1 AND NOT r.deleted ORDER BY q.value`, [inputIds.region, inputIds.quantity]);
    // Independent fixture oracle: unique quantities identify the three CSV rows, including both west rows.
    expect(inputs.map(({ region, quantity }) => ({ region, quantity }))).toEqual([
      { region: 'catalog-west', quantity: '2' }, { region: 'catalog-west', quantity: '3' }, { region: 'catalog-east', quantity: '7' },
    ]);
    const outputByQuantity: Record<string, string> = { '2': `${prefix}CATALOG-WEST`, '3': `${prefix}CATALOG-WEST`, '7': `${prefix}CATALOG-EAST` };
    const byRow = (left: { row_id: string }, right: { row_id: string }) => left.row_id.localeCompare(right.row_id);
    const expected = inputs.map(input => ({ row_id: input.row_id, value: outputByQuantity[input.quantity], status: 'pass' })).sort(byRow);
    const readCells = () => probe.pg<{ row_id: string; value: string; status: string }>(
      'SELECT row_id,value,status FROM model_hub_cell WHERE column_id=$1 AND NOT deleted ORDER BY row_id', [columns[0].id]);
    const cells = await readCells();
    expect(cells).toEqual(expected);
    const scores = await probe.pg<{ status: string }>(
      `SELECT c.status FROM model_hub_cell c JOIN model_hub_column col ON col.id=c.column_id
       WHERE c.dataset_id=$1 AND col.source='experiment_evaluation' AND NOT col.deleted AND NOT c.deleted ORDER BY c.id`, [detail.snapshot_dataset_id]);
    expect(scores).toEqual(Array(3).fill({ status: 'pass' }));
    const rows = await scopes.send<{ result: Rows }>(a, 'GET', `${detailPath}rows/`);
    expect(rows.status).toBe(200);
    const apiOutputs = rows.body.result.table.map(row => ({ row_id: row.row_id, value: row[columns[0].id].cell_value, status: row[columns[0].id].status })).sort(byRow);
    expect(apiOutputs).toEqual(expected);
    expect(rows.body.result.table.map(row => ({ row_id: row.row_id, region: String(row[inputIds.region].cell_value), quantity: String(row[inputIds.quantity].cell_value) })).sort(byRow)).toEqual([...inputs].sort(byRow));
    await expect(page).toHaveURL(new RegExp(`/experiment/${experimentId}/data`), { timeout: UI_READY });
    await expect(page.locator(`.ag-center-cols-container .ag-row [col-id="${columns[0].id}"]`)).toHaveCount(3, { timeout: UI_READY });
    for (const input of inputs) {
      const gridRow = page.locator(`.ag-center-cols-container .ag-row[row-id="${input.row_id}"]`);
      // The classic results grid hides source columns; bind its output to the independently read input row identity.
      await expect(gridRow.locator(`[col-id="${columns[0].id}"]`)).toHaveText(outputByQuantity[input.quantity], { timeout: UI_READY });
    }
    await expect(page.getByText('Pass', { exact: true })).toHaveCount(3, { timeout: UI_READY });
    const countExperiments = () => probe.pg('SELECT id FROM model_hub_experimentstable WHERE dataset_id=$1 ORDER BY id', [datasetId]);
    const before = await countExperiments();
    for (const target of [
      { agent_id: foreignGraph.id, agent_version: foreignVersion.id },
      { agent_id: graph.id, agent_version: foreignVersion.id },
    ]) {
      const denied = await scopes.send(a, 'POST', EXPERIMENTS, { ...submitted, name: `${name}-refused`, prompt_config: [target] });
      expect(denied.status).toBe(400);
      expect(await countExperiments()).toEqual(before);
    }
    expect((await scopes.send(b, 'POST', EXPERIMENTS, submitted)).status).toBe(404);
    expect((await scopes.send(b, 'GET', detailPath)).status).toBe(404);
    expect((await scopes.send(b, 'GET', `${detailPath}rows/`)).status).toBe(404);
    expect(await readDetail()).toEqual(detail);
    expect(await readCells()).toEqual(cells);
    expect(await countExperiments()).toEqual(before);
    await testInfo.attach('native-graph-readback', { contentType: 'application/json', body: JSON.stringify({ detail, cells, scores, expected, requests: scopes.evidence() }) });
  } finally {
    await testInfo.attach('last-page-text', { contentType: 'text/plain', body: await page.locator('body').innerText() });
    await context.close();
  }
});
