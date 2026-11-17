import type { Browser, Page, Request as PwRequest } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import type { ScopeActor, ScopeActors } from '../../lib/scope-actors';
import { POLL } from '../../lib/state-probe';
import type { StateProbe } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';
import { registerMockModel } from '../../lib/managed-mock';
import { MockUpstream } from '../../lib/upstream';
import { MCP_MOCK } from '../../lib/public-network';

// New experiment, step 1, Already running (RunSources/SourcePicker.jsx, CatalogGrid.jsx,
// CatalogTile.jsx). The tiles are the frontend's own list (RunSources/catalogEntries.js);
// what a workspace can pick is the server's report in GET /api/capabilities/
// (experiment_run_sources, model_hub/services/experiment_source_capabilities.py).
// A source runs as an HTTP source of the experiment (services/http_experiment_source.py):
// one request per row through the public-destination transport (tfc/utils/outbound_destination.py).
const UI_READY = 60_000;
// The HTTP source workflow: admission, one request per row, settle, finish.
const RUN_DONE = { timeout: POLL.EVAL_RESULT.timeout + POLL.ASYNC_JOB.timeout, intervals: [2000, 5000] };
// ManualDatasetCreateRequestSerializer: Column 1..n and n empty rows.
const DATASETS = '/model-hub/develops/create-dataset-manually/';
// DatasetUpdateCellValueRequestSerializer.
const cellPath = (dataset: string) => `/model-hub/develops/${dataset}/update_cell_value/`;
// EvalTemplateCreateV2RequestSerializer.
const TEMPLATES = '/model-hub/eval-templates/create-v2/';
// ExperimentCreateV2Serializer, ExperimentDetailV2Serializer (http_configs), ExperimentUpdateV2Serializer.
const EXPERIMENTS = '/model-hub/experiments/v2/';
// tfc/capabilities/views.py CapabilitiesView.
const CAPABILITIES = '/api/capabilities/';
// HttpEndpointTestView: Discover and Test on one row send their request through the server.
const ENDPOINT_TEST = '/model-hub/r4/endpoints/test/';
// Declared in the frontend (utils/axios.js) with no backend route and no caller: never requested.
const RUN_SOURCES_PATHS = '/model-hub/r4/run-sources/';

const RUNNING_KINDS = ['http', 'a2a', 'mcp_tool'] as const;
type RunningKind = typeof RUNNING_KINDS[number];
// RunSources/catalogEntries.js ENDPOINTS.
const TILE_NAME: Record<RunningKind, string> = { http: 'HTTP endpoint', a2a: 'A2A agent card', mcp_tool: 'MCP server tool' };
// experiment_source_capabilities.py: no creation serializer and no execution path for an MCP tool.
const MCP_NOT_BUILT = 'MCP tool experiments are not built in this release.';
// views/http_endpoint_test.py: the transport refuses a private address before any connection.
const PRIVATE_HOST = 'Use a public HTTP(S) endpoint. Private and local hosts cannot be called from Future AGI.';
// http_experiment_source.py execute_http_row: the same refusal, stored on the row.
const ROW_NOT_PUBLIC = 'Endpoint is unavailable or not a public destination. Check the URL and retry.';
// http_experiment_source.py a2a_reply_text: a JSON answer that is no message/send result.
const ROW_NOT_A2A = "The agent's reply is not an A2A message/send result. Check the agent URL, then retry.";
// A private address literal: refused by its class, so nothing leaves the stack.
const PRIVATE_BASE = 'http://10.255.255.1';
// The row's column, mapped into an OpenAI-format body: the stack's mock endpoint answers
// `echo: <last user message>` (e2e/stack/mock-llm/server.mjs) and records what it was sent.
const BODY_TEMPLATE = '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"{{question}}"}]}';
const OUTPUT_PATH = '$.choices[0].message.content';

// What the three wizard walks below wait for.
const HTTPS_GAP = 'the wizard takes only https:// endpoint URLs (Experiment/stepIssues.js) and the server verifies TLS '
  + 'against its bundled CA list only (tfc/utils/outbound_destination.py), while every TLS mock of the stack carries a '
  + 'certificate made at start (e2e/stack/walk-mocks/cert-init.mjs) and the mock endpoint is plain http';
const A2A_GAP = 'the stack serves no A2A agent: no agent card at /.well-known/agent-card.json or /.well-known/agent.json '
  + 'and no JSON-RPC message/send on the e2e-public network';
const MCP_GAP = 'the server reports MCP tool experiments as not built in this release: no creation serializer and no '
  + 'execution path (model_hub/services/experiment_source_capabilities.py), so the tile is greyed and no run can be created';
// Where those mocks would answer once the stack serves them.
const HTTPS_ENDPOINT = 'https://echo.e2e-public.test/reply';
const A2A_AGENT = 'http://a2a.e2e-public.test:8080';

type SourceReport = { available: boolean; reason: string | null };
type RunSources = { workspace_id: string | null; sources: Record<RunningKind | 'prompt' | 'agent', SourceReport> };
type Capabilities = { experiment_run_sources: RunSources };
type HttpConfig = { id: string; name: string; target_type: string; order: number;
  http: { url: string; method: string; body_template: string; output_path: string; protocol?: string };
  variable_mapping: Record<string, string> };
type Detail = { result: { id: string; name: string; http_configs: HttpConfig[] } };
type SourceRow = { id: string; name: string; status: string; phase: string | null; http: unknown };
type CellInfo = { reason?: string | null; http_status?: number | null; error?: { code: string; message: string } | null };
type SourceRead = { query: string; organization: string | null; workspace: string | null; status: number | null;
  report: RunSources | null };
type Seeded = { datasetId: string; rowIds: string[]; columnId: string; columnName: string };
type ChatBody = { model: string; messages: { role: string; content: string }[] };
type SendBody = { jsonrpc: string; id: string; method: string;
  params: { message: { kind: string; role: string; messageId: string; parts: { kind: string; text: string }[] };
    configuration: { blocking: boolean } } };

async function seedDataset(scopes: ScopeActors, probe: StateProbe, actor: ScopeActor, name: string,
  values: string[]): Promise<Seeded> {
  const created = await scopes.send<{ result: { dataset_id: string } }>(actor, 'POST', DATASETS,
    { dataset_name: name, number_of_rows: values.length, number_of_columns: 1 });
  expect(created.status).toBe(200);
  const datasetId = created.body.result.dataset_id;
  const rowIds = (await probe.pg<{ id: string }>(
    'SELECT id::text AS id FROM model_hub_row WHERE dataset_id=$1::uuid AND NOT deleted ORDER BY "order", id',
    [datasetId])).map(r => r.id);
  expect(rowIds).toHaveLength(values.length);
  const columns = await probe.pg<{ id: string; name: string }>(
    'SELECT id::text AS id, name FROM model_hub_column WHERE dataset_id=$1::uuid AND NOT deleted', [datasetId]);
  expect(columns).toHaveLength(1);
  for (const [i, rowId] of rowIds.entries()) {
    const written = await scopes.send(actor, 'POST', cellPath(datasetId),
      { row_id: rowId, column_id: columns[0].id, new_value: values[i] });
    expect(written.status).toBe(200);
  }
  return { datasetId, rowIds, columnId: columns[0].id, columnName: columns[0].name };
}

// An evaluation is required to create an experiment; it runs on the loopback LLM STANDIN.
async function seedEvaluation(scopes: ScopeActors, probe: StateProbe, actor: ScopeActor, name: string) {
  const account = scopes.owners.find(o => o.organizationId === actor.organizationId);
  if (!account) throw new Error('owner provisioning account missing');
  const mock = await registerMockModel(account, probe);
  const template = await actor.api.post<{ result: { id: string } }>(TEMPLATES, {
    name, eval_type: 'llm', model: mock.model, output_type: 'pass_fail',
    instructions: 'Answer Passed when {{output}} is not empty.', pass_threshold: 0.5,
    check_internet: false, error_localizer_enabled: false,
  });
  return { model: mock.model, templateId: template.result.id };
}

// HttpExperimentEntrySerializer: strict keys; the mapping names the dataset's column.
const httpEntry = (name: string, url: string, columnId: string) => ({ name, target_type: 'http',
  http: { url, method: 'POST', headers: [], body_template: BODY_TEMPLATE, output_path: OUTPUT_PATH },
  variable_mapping: { question: columnId } });

// A2aExperimentEntrySerializer: strict keys; the card's url is where each row's message/send goes.
const a2aEntry = (name: string, url: string, columnId: string, extra: Record<string, unknown> = {}) => ({ name,
  target_type: 'a2a', a2a: { card_url: url, card: { name, version: '1', url, skills: [{ id: 'reply', name: 'Reply' }] },
    skill_id: 'reply', text_column: columnId, auth: { kind: 'none' }, ...extra } });

async function createExperiment(scopes: ScopeActors, actor: ScopeActor, body: Record<string, unknown>) {
  const response = await scopes.send<{ result?: unknown }>(actor, 'POST', EXPERIMENTS, body);
  const result = response.body?.result;
  const id = typeof result === 'string' ? /experiment_id=([0-9a-f-]{36})/.exec(result)?.[1] ?? null : null;
  return { status: response.status, id, body: response.body };
}

// The experiment's sources as stored: ExperimentDatasetTable rows that keep an HTTP source.
const sourcesOf = (probe: StateProbe, experimentId: string) => probe.pg<SourceRow>(
  `SELECT id::text AS id, name, status, prompt_config->'_http_execution'->>'phase' AS phase,
     prompt_config->'http' AS http
   FROM model_hub_experimentdatasettable
   WHERE experiment_id=$1::uuid AND NOT deleted AND prompt_config->>'target_type'='http' ORDER BY name`,
  [experimentId]);

// A source's output column in the experiment snapshot (column_service.create_experiment_column) and its cells.
async function outputCells(probe: StateProbe, experimentId: string, sourceId: string) {
  const [experiment] = await probe.pg<{ snapshot: string | null }>(
    'SELECT snapshot_dataset_id::text AS snapshot FROM model_hub_experimentstable WHERE id=$1::uuid', [experimentId]);
  expect(experiment?.snapshot).toBeTruthy();
  const columns = await probe.pg<{ id: string; name: string }>(
    `SELECT id::text AS id, name FROM model_hub_column
     WHERE dataset_id=$1::uuid AND source='experiment' AND source_id=$2 AND NOT deleted`,
    [experiment.snapshot, sourceId]);
  expect(columns).toHaveLength(1);
  const cells = await probe.pg<{ value: string | null; status: string; value_infos: unknown }>(
    'SELECT value, status, value_infos FROM model_hub_cell WHERE column_id=$1::uuid AND NOT deleted ORDER BY id',
    [columns[0].id]);
  return { column: columns[0], cells: cells.map(cell => ({ value: cell.value, status: cell.status,
    info: (typeof cell.value_infos === 'string' ? JSON.parse(cell.value_infos) : cell.value_infos ?? {}) as CellInfo })) };
}

// Every read of the source report the page sends, and any request for the run-sources paths.
function watchSourceReads(page: Page) {
  const requests: PwRequest[] = [];
  const runSources: string[] = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (request.method() === 'GET' && url.pathname === CAPABILITIES) requests.push(request);
    if (url.pathname.includes(RUN_SOURCES_PATHS)) runSources.push(`${request.method()} ${url.pathname}`);
  });
  const reads = () => Promise.all(requests.map(async (request): Promise<SourceRead> => {
    const response = await request.response();
    const headers = request.headers();
    const body = response?.ok() ? await response.json() as Capabilities : null;
    return { query: new URL(request.url()).search, organization: headers['x-organization-id'] ?? null,
      workspace: headers['x-workspace-id'] ?? null, status: response?.status() ?? null,
      report: body?.experiment_run_sources ?? null };
  }));
  return { reads, runSources };
}

async function openPage(scopes: ScopeActors, browser: Browser, actor: ScopeActor) {
  const context = await scopes.openContext(browser, actor);
  // Runtime traffic stays on fixture loopback. External requests are blocked, never mocked.
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    return ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname) || ['data:', 'blob:'].includes(url.protocol)
      ? route.continue() : route.abort('blockedbyclient');
  });
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  return { context, page };
}

async function openNewExperiment(scopes: ScopeActors, browser: Browser, actor: ScopeActor, datasetId: string) {
  const { context, page } = await openPage(scopes, browser, actor);
  // Attached before the first navigation, so no read of the report is missed.
  const watch = watchSourceReads(page);
  await page.goto(`/dashboard/develop/${datasetId}?tab=experiments&new_experiment=1`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByTestId('source-picker')).toBeVisible({ timeout: UI_READY });
  return { context, page, watch };
}

// The Already running list: from step 1 by its card, or as the picker reopens on it.
async function openRunningList(page: Page) {
  // The Experiments tab's empty state shows the same source cards behind the picker (LeaderboardStates.jsx).
  const picker = page.getByTestId('source-picker');
  const list = picker.getByTestId('catalog-running');
  const card = picker.getByTestId('source-card-running');
  await expect(list.or(card).first()).toBeVisible({ timeout: UI_READY });
  if (!(await list.isVisible())) {
    // The card is greyed until the report answers; a greyed card takes no click.
    await expect(card).not.toHaveAttribute('aria-disabled', 'true', { timeout: UI_READY });
    await card.click();
  }
  await expect(list).toBeVisible({ timeout: UI_READY });
}

// The exact request, once: GET /api/capabilities/ with no query, answered for the actor's workspace.
// The page names its workspace in X-Workspace-Id once its landing has set it (useRunSourceAvailability.js).
function expectOneRead(reads: SourceRead[], actor: ScopeActor): RunSources {
  expect(reads).toHaveLength(1);
  expect(reads[0].query).toBe('');
  expect(reads[0].status).toBe(200);
  expect([actor.workspaceId, null]).toContain(reads[0].workspace);
  expect(reads[0].report?.workspace_id).toBe(actor.workspaceId);
  return reads[0].report!;
}

// Each tile shows, pickable or greyed with the server's reason, as the report says for its kind.
async function expectTiles(page: Page, report: RunSources) {
  for (const kind of RUNNING_KINDS) {
    const tile = page.getByTestId(`catalog-tile-${kind}`);
    await expect(tile).toBeVisible({ timeout: UI_READY });
    await expect(tile).toContainText(TILE_NAME[kind]);
    const reported = report.sources[kind];
    if (reported.available) {
      await expect(tile).not.toHaveAttribute('aria-disabled', 'true');
    } else {
      await expect(tile).toHaveAttribute('aria-disabled', 'true');
      await expect(tile).toContainText(String(reported.reason));
    }
  }
}

// The wizard from step 1 to the create request (RunExperiment.jsx: Continue, Continue, Continue, Run experiment).
async function runFromWizard(page: Page, name: string, evalName: string) {
  await page.getByRole('textbox', { name: /^Experiment name/ }).fill(name);
  await page.getByTestId('wizard-continue').click();
  await page.getByTestId('wizard-continue').click();
  await expect(page.getByText(evalName, { exact: true }).first()).toBeVisible({ timeout: UI_READY });
  await page.getByTestId('wizard-continue').click();
  const posted = page.waitForResponse(response => new URL(response.url()).pathname === EXPERIMENTS
    && response.request().method() === 'POST', { timeout: UI_READY });
  await page.getByRole('button', { name: /^Run experiment/ }).click();
  return posted;
}

// A dataset whose saved evaluation the wizard's Evaluations step starts with.
async function seedForWizard(scopes: ScopeActors, probe: StateProbe, actor: ScopeActor, prefix: string, questions: string[]) {
  const seed = await seedDataset(scopes, probe, actor, `${prefix}-data`, questions);
  const evaluation = await seedEvaluation(scopes, probe, actor, `${prefix}-judge`);
  const evalName = `${prefix}-eval`;
  // UserEvalMutationRequestSerializer, then the dataset's experiment defaults.
  await actor.api.post(`/model-hub/develops/${seed.datasetId}/add_user_eval/`, {
    name: evalName, template_id: evaluation.templateId, model: evaluation.model, run: false,
    config: { mapping: { output: 'output' }, config: {}, reason_column: false },
  });
  const metrics = await probe.pg<{ id: string }>(
    'SELECT id::text AS id FROM model_hub_userevalmetric WHERE dataset_id=$1::uuid AND name=$2 AND NOT deleted',
    [seed.datasetId, evalName]);
  expect(metrics).toHaveLength(1);
  const defaults = await scopes.send(actor, 'PUT', `/model-hub/develops/${seed.datasetId}/edit_dataset_behavior/`,
    { experiment_default_eval_ids: [metrics[0].id] });
  expect(defaults.status).toBe(200);
  return { ...seed, evalName };
}

test('EXP-E2E-008: already running sources in a new experiment, across two organizations', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EXP-E2E-008', area: 'experiments',
    userGoal: 'Pick an endpoint or agent I already run as a source in New experiment, see which kinds this server can run, get each row\'s answer or a plain sentence when a call is refused, and keep those runs inside my organization',
    steps: [
      'seed two organizations; in the first, a two-row dataset, an evaluation on the loopback LLM STANDIN and, through the public API, an experiment whose HTTP endpoint source posts each row to the stack\'s mock endpoint with its body mapped from the row\'s column',
      'open New experiment on the dataset, open Already running and read the HTTP endpoint, A2A agent card and MCP server tool tiles',
      'pick the A2A agent card tile and Discover a private address, then the stack\'s mock endpoint, which serves no agent card',
      'add another run, pick the HTTP endpoint tile and read its card',
      'through the public API, send an A2A entry that carries fetched_from, then run an experiment with an HTTP endpoint source at a private address and an A2A agent whose card names the mock endpoint',
      'open the first experiment\'s Data page and read the endpoint\'s outputs',
      'in the second organization, read the source report and the first organization\'s experiment, rows and source, then open Already running on a dataset of its own',
    ],
    backendChecks: [
      'GET /api/capabilities/ reports experiment_run_sources for the caller\'s workspace: HTTP endpoint and A2A available, MCP tool refused as not built in this release; the page reads it once from load through both picks and never requests a run-sources path',
      'the HTTP endpoint experiment is created with 200; the mock endpoint records exactly one POST per row with the mapped body; each row\'s output cell stores the endpoint\'s answer with status pass, and the source ends Completed with its execution finished',
      'an A2A entry that carries fetched_from is refused with 400 and creates no experiment; the same entry without it is created, and the mock endpoint records one message/send per row with the row\'s id and text',
      'a source at a private address stores on every row the sentence that the endpoint is unavailable or not a public destination, and the A2A agent\'s rows store the sentence that the reply is not an A2A message/send result, each with status error, its reason and an error code',
      'the second organization gets its own workspace\'s report with none of the first organization\'s names, 404 reading the experiment and its rows, 404 reading its source\'s table by id and 404 saving its source by id; the first organization\'s sources, experiments and the mock endpoint\'s request counts stay unchanged',
    ],
    browserChecks: [
      'Already running lists HTTP endpoint and A2A agent card as pickable and MCP server tool greyed with the server\'s reason',
      'Discover shows the private-host sentence for a private address and, for the mock endpoint, the no agent card sentence naming both well-known paths',
      'picking the HTTP endpoint tile adds a card called once per example with Test on one row',
      'the experiment\'s Data page shows each row\'s endpoint output',
      'the second organization\'s picker shows the same three tiles and none of the first organization\'s names',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Provisioning + seeding + two runs x RUN_DONE + one settle + three page loads and their steps x UI_READY + headroom.
  test.setTimeout(1_200_000);
  const a = scopes.ownerA;
  const b = scopes.ownerB;
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const prefix = `e2e-exp8-${suffix}`;
  const attach = (name: string, body: unknown) =>
    testInfo.attach(name, { contentType: 'application/json', body: JSON.stringify(body, null, 2) });
  const questions = [0, 1].map(i => `${prefix}-question-${i}`);
  const upstream = new MockUpstream();
  // One scenario per sender, so each count is that sender's own.
  const scenario = { run: `${prefix}-run`, card: `${prefix}-card`, agent: `${prefix}-agent` };
  const endpoint = (name: string) => `${upstream.baseUrl(name)}/v1/chat/completions`;
  const names = { happy: `${prefix}-endpoint`, refused: `${prefix}-private`, agent: `${prefix}-agent-run`,
    first: `${prefix}-experiment`, second: `${prefix}-refusals`, never: `${prefix}-fetched-from` };

  // Seed the first organization.
  const seed = await seedDataset(scopes, probe, a, `${prefix}-data`, questions);
  const evaluation = await seedEvaluation(scopes, probe, a, `${prefix}-judge`);
  const metrics = (name: string) => [{ template_id: evaluation.templateId, name, model: evaluation.model,
    config: { mapping: { output: seed.columnId } } }];
  const experimentsOf = (datasetId: string) => probe.pg<{ id: string; name: string }>(
    'SELECT id::text AS id, name FROM model_hub_experimentstable WHERE dataset_id=$1::uuid AND NOT deleted ORDER BY name',
    [datasetId]);

  // The server's report for the first organization, read through the API as the page reads it.
  const reported = await scopes.send<Capabilities>(a, 'GET', CAPABILITIES);
  expect(reported.status).toBe(200);
  const report = reported.body.experiment_run_sources;
  expect(report.workspace_id).toBe(a.workspaceId);
  expect(report.sources.http).toMatchObject({ available: true, reason: null });
  expect(report.sources.a2a).toMatchObject({ available: true, reason: null });
  expect(report.sources.mcp_tool).toMatchObject({ available: false, reason: MCP_NOT_BUILT });

  const first = await test.step('1. an HTTP endpoint source runs on every row and stores the endpoint\'s answers', async () => {
    const created = await createExperiment(scopes, a, { name: names.first, dataset_id: seed.datasetId,
      experiment_type: 'llm', prompt_config: [httpEntry(names.happy, endpoint(scenario.run), seed.columnId)],
      user_eval_metrics: metrics(`exp8-first-${suffix}`) });
    expect(created.status, JSON.stringify(created.body)).toBe(200);
    expect(created.id).toBeTruthy();
    const firstId = created.id!;
    const detail = await scopes.send<Detail>(a, 'GET', `${EXPERIMENTS}${firstId}/`);
    expect(detail.status).toBe(200);
    expect(detail.body.result.http_configs).toHaveLength(1);
    expect(detail.body.result.http_configs[0]).toMatchObject({ name: names.happy, target_type: 'http',
      http: { url: endpoint(scenario.run), method: 'POST', body_template: BODY_TEMPLATE, output_path: OUTPUT_PATH },
      variable_mapping: { question: seed.columnId } });
    expect(detail.body.result.http_configs[0].http.protocol).toBeUndefined();
    const sourceId = detail.body.result.http_configs[0].id;

    // The run finishes: the source is Completed and its execution phase is finished.
    await expect.poll(async () => (await sourcesOf(probe, firstId)).map(s => [s.id, s.status, s.phase]), RUN_DONE)
      .toEqual([[sourceId, 'Completed', 'finished']]);
    const [source] = await sourcesOf(probe, firstId);

    // Each row's output is the endpoint's answer to that row's mapped body.
    const output = await outputCells(probe, firstId, sourceId);
    expect(output.column.name).toBe(names.happy);
    expect(output.cells.map(c => c.status)).toEqual(questions.map(() => 'pass'));
    expect(output.cells.map(c => String(c.value)).sort()).toEqual(questions.map(q => `echo: ${q}`).sort());
    for (const cell of output.cells) {
      expect(cell.info.http_status).toBe(200);
      expect(cell.info.error ?? null).toBeNull();
    }
    // The endpoint received exactly one POST per row, with the row's value in the mapped body.
    const sent = await upstream.requests(scenario.run);
    expect(sent.map(r => [r.method, r.path])).toEqual(
      questions.map(() => ['POST', `/s/${scenario.run}/-/v1/chat/completions`]));
    expect(sent.map(r => r.body as ChatBody).sort((x, y) => x.messages[0].content < y.messages[0].content ? -1 : 1))
      .toEqual([...questions].sort().map(q => ({ model: 'gpt-4o-mini', messages: [{ role: 'user', content: q }] })));
    // The rows read returns the same outputs.
    const rows = await scopes.send<unknown>(a, 'GET', `${EXPERIMENTS}${firstId}/rows/`);
    expect(rows.status).toBe(200);
    for (const q of questions) expect(JSON.stringify(rows.body)).toContain(`echo: ${q}`);
    await attach('first-experiment', { experimentId: firstId, sourceId, output, sent: sent.length });

    // The experiment itself settles once its evaluation cells do. Soft: the source's own
    // finished state above is the run; this adds the experiment's status to the record.
    const deadline = Date.now() + RUN_DONE.timeout;
    let status = '';
    while (Date.now() < deadline) {
      [{ status }] = await probe.pg<{ status: string }>(
        'SELECT status FROM model_hub_experimentstable WHERE id=$1::uuid', [firstId]);
      if (!['NotStarted', 'Queued', 'Running'].includes(status)) break;
      await new Promise(resolve => setTimeout(resolve, RUN_DONE.intervals[1]));
    }
    await attach('first-experiment-status', { status });
    expect.soft(status, 'the experiment settled').toBe('Completed');
    return { id: firstId, source };
  });
  const firstId = first.id;

  const opened = await openNewExperiment(scopes, browser, a, seed.datasetId);
  await test.step('2. Already running shows its tiles as the report says, read once', async () => {
    await openRunningList(opened.page);
    const reads = await opened.watch.reads();
    await attach('source-report-reads', reads);
    const read = expectOneRead(reads, a);
    expect(read.sources).toEqual(report.sources);
    await expectTiles(opened.page, read);
    // As this build reports them: two pickable, the MCP tool greyed with its reason.
    await expect(opened.page.getByTestId('catalog-tile-http')).not.toHaveAttribute('aria-disabled', 'true');
    await expect(opened.page.getByTestId('catalog-tile-a2a')).not.toHaveAttribute('aria-disabled', 'true');
    await expect(opened.page.getByTestId('catalog-tile-mcp_tool')).toHaveAttribute('aria-disabled', 'true');
    await expect(opened.page.getByTestId('catalog-tile-mcp_tool')).toContainText(MCP_NOT_BUILT);
  }, { timeout: UI_READY * 3 });

  await test.step('3. Discover says in a sentence why it found no agent', async () => {
    const page = opened.page;
    await page.getByTestId('catalog-tile-a2a').click();
    await expect(page.getByTestId('a2a-form')).toBeVisible({ timeout: UI_READY });
    // A private address: the server refuses it before any connection.
    const refused = page.waitForResponse(response => new URL(response.url()).pathname === ENDPOINT_TEST
      && response.request().method() === 'POST', { timeout: UI_READY });
    await page.getByTestId('a2a-url').fill(PRIVATE_BASE);
    await page.getByTestId('a2a-discover').click();
    const answer = await refused;
    expect(answer.status()).toBe(200);
    expect(answer.request().postDataJSON().prepared_request).toMatchObject(
      { url: `${PRIVATE_BASE}/.well-known/agent-card.json`, method: 'GET' });
    await expect(page.getByTestId('a2a-error')).toHaveText(PRIVATE_HOST, { timeout: UI_READY });
    await expect(page.getByTestId('a2a-card')).toHaveCount(0);

    // The stack's mock endpoint: reachable, and it serves no agent card at either path.
    const base = upstream.baseUrl(scenario.card);
    const paths = ['/.well-known/agent-card.json', '/.well-known/agent.json'];
    await page.getByTestId('a2a-url').fill(base);
    await page.getByTestId('a2a-discover').click();
    await expect(page.getByTestId('a2a-error')).toHaveText(
      `No agent card at ${paths.map(p => `${base}${p}`).join(' or ')}. Enter the agent card URL, or a base URL that serves /.well-known/agent-card.json.`,
      { timeout: UI_READY });
    await expect(page.getByTestId('a2a-card')).toHaveCount(0);
    // The server, not the browser, asked the endpoint: one GET per path, in the form's order.
    const asked = await upstream.requests(scenario.card);
    expect(asked.map(r => [r.method, r.path])).toEqual(paths.map(p => ['GET', `/s/${scenario.card}/-${p}`]));
  }, { timeout: UI_READY * 4 });

  await test.step('4. the HTTP endpoint tile adds its card; the report is still read once', async () => {
    const page = opened.page;
    await page.getByTestId('runs-heading').getByRole('button', { name: 'Add run', exact: true }).click();
    await openRunningList(page);
    await page.getByTestId('catalog-tile-http').click();
    await expect(page.getByText('Called once per example', { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByTestId('http-test')).toBeVisible();
    await expect(page.getByTestId('http-test')).toHaveText('Test on one row');
    const reads = await opened.watch.reads();
    await attach('source-report-reads-after-picks', reads);
    expectOneRead(reads, a);
    expect(opened.watch.runSources).toEqual([]);
    await opened.context.close();
  }, { timeout: UI_READY * 3 });

  const secondId = await test.step('5. a refused call is stored on its row as a sentence', async () => {
    const before = await experimentsOf(seed.datasetId);
    // The server takes no fetched_from in an A2A entry: 400, and nothing is created.
    const withFetchedFrom = await createExperiment(scopes, a, { name: names.never, dataset_id: seed.datasetId,
      experiment_type: 'llm', user_eval_metrics: metrics(`exp8-never-${suffix}`),
      prompt_config: [a2aEntry(names.agent, endpoint(scenario.agent), seed.columnId,
        { fetched_from: `${upstream.baseUrl(scenario.agent)}/.well-known/agent-card.json` })] });
    expect(withFetchedFrom.status).toBe(400);
    expect(await experimentsOf(seed.datasetId)).toEqual(before);
    expect(await upstream.requests(scenario.agent)).toEqual([]);

    // The same entry without it is created, beside an HTTP endpoint at a private address.
    const created = await createExperiment(scopes, a, { name: names.second, dataset_id: seed.datasetId,
      experiment_type: 'llm', user_eval_metrics: metrics(`exp8-second-${suffix}`),
      prompt_config: [httpEntry(names.refused, `${PRIVATE_BASE}/reply`, seed.columnId),
        a2aEntry(names.agent, endpoint(scenario.agent), seed.columnId)] });
    expect(created.status, JSON.stringify(created.body)).toBe(200);
    expect(created.id).toBeTruthy();
    const id = created.id!;
    const detail = await scopes.send<Detail>(a, 'GET', `${EXPERIMENTS}${id}/`);
    expect(detail.status).toBe(200);
    const configs = Object.fromEntries(detail.body.result.http_configs.map(c => [c.name, c]));
    expect(Object.keys(configs).sort()).toEqual([names.agent, names.refused].sort());
    // The A2A agent is kept as an HTTP source with the a2a protocol, sending to its card's URL.
    expect(configs[names.agent].http).toMatchObject({ protocol: 'a2a', url: endpoint(scenario.agent), method: 'POST' });
    expect(JSON.stringify(detail.body)).not.toContain('fetched_from');

    await expect.poll(async () => (await sourcesOf(probe, id)).map(s => [s.name, s.status, s.phase]), RUN_DONE)
      .toEqual([names.agent, names.refused].sort().map(name => [name, 'Completed', 'finished']));

    const expected: [string, string, string][] = [
      [names.refused, ROW_NOT_PUBLIC, 'destination'], [names.agent, ROW_NOT_A2A, 'a2a_reply']];
    const stored: Record<string, unknown> = {};
    for (const [name, sentence, code] of expected) {
      const output = await outputCells(probe, id, configs[name].id);
      stored[name] = output;
      expect(output.cells).toHaveLength(questions.length);
      for (const cell of output.cells) {
        expect(cell.status).toBe('error');
        expect(cell.value).toBe(sentence);
        expect(cell.info.reason).toBe(sentence);
        expect(cell.info.error).toMatchObject({ code, message: sentence });
      }
    }
    await attach('second-experiment', { experimentId: id, stored });

    // The agent's URL received one message/send per row, carrying that row's id and text.
    const sent = await upstream.requests(scenario.agent);
    expect(sent.map(r => r.method)).toEqual(questions.map(() => 'POST'));
    const messages = sent.map(r => r.body as SendBody);
    for (const message of messages) {
      expect(message).toMatchObject({ jsonrpc: '2.0', method: 'message/send',
        params: { message: { kind: 'message', role: 'user', messageId: message.id }, configuration: { blocking: true } } });
      expect(message.params.message.parts).toHaveLength(1);
      expect(message.params.message.parts[0].kind).toBe('text');
    }
    expect(messages.map(m => m.params.message.parts[0].text).sort()).toEqual([...questions].sort());
    expect(messages.map(m => m.id).sort()).toEqual([...seed.rowIds].sort());
    return id;
  });

  await test.step('6. the experiment\'s Data page shows each row\'s endpoint output', async () => {
    const { context, page } = await openPage(scopes, browser, a);
    await page.goto(`/dashboard/develop/experiment/${firstId}/data`, { waitUntil: 'domcontentloaded' });
    for (const q of questions)
      await expect(page.locator('.ag-cell').filter({ hasText: `echo: ${q}` }).first()).toBeVisible({ timeout: UI_READY });
    await context.close();
  }, { timeout: UI_READY * 3 });

  await test.step('7. the second organization reads and changes nothing of the first', async () => {
    const privateValues = [firstId, secondId, first.source.id, a.workspaceId, a.organizationId, seed.datasetId,
      ...Object.values(names), ...Object.values(scenario), ...questions];
    const state = async () => ({
      sources: [...await sourcesOf(probe, firstId), ...await sourcesOf(probe, secondId)],
      experiments: await experimentsOf(seed.datasetId),
      sent: [(await upstream.requests(scenario.run)).length, (await upstream.requests(scenario.agent)).length] });
    const before = await state();

    // Its report is its own workspace's, and names kinds only.
    const own = await scopes.send<Capabilities>(b, 'GET', CAPABILITIES);
    expect(own.status).toBe(200);
    expect(own.body.experiment_run_sources.workspace_id).toBe(b.workspaceId);
    expect(own.body.experiment_run_sources.sources).toEqual(report.sources);
    for (const value of privateValues) expect(JSON.stringify(own.body)).not.toContain(value);

    // The first organization's experiment, its rows and its source answer 404.
    const detailPath = `${EXPERIMENTS}${firstId}/`;
    const sourceTablePath = `/model-hub/develops/${first.source.id}/get-experiment-dataset-table/`;
    const refusals = [
      await scopes.send<unknown>(b, 'GET', detailPath),
      await scopes.send<unknown>(b, 'GET', `${detailPath}rows/`),
      await scopes.send<unknown>(b, 'GET', `${EXPERIMENTS}${secondId}/`),
      // A source's table reads by its id; the source is saved by its id inside its experiment.
      await scopes.send<unknown>(b, 'GET', sourceTablePath),
      await scopes.send<unknown>(b, 'PUT', detailPath, { prompt_config: [
        { ...httpEntry(names.happy, `${PRIVATE_BASE}/reply`, seed.columnId), id: first.source.id }] }),
    ];
    await attach('second-organization-answers', refusals.map(r => r.status));
    for (const refusal of refusals) {
      expect(refusal.status).toBe(404);
      for (const value of privateValues) expect(JSON.stringify(refusal.body)).not.toContain(value);
    }
    // The owner still reads it: the refusal is scoping, not a missing experiment.
    expect((await scopes.send<Detail>(a, 'GET', detailPath)).status).toBe(200);
    expect.soft((await scopes.send<unknown>(a, 'GET', sourceTablePath)).status).toBe(200);

    // Its own picker: the same tiles, read once for its workspace, with nothing of the first organization.
    const foreign = await seedDataset(scopes, probe, b, `${prefix}-b-data`, [`${prefix}-b-question`]);
    const mine = await openNewExperiment(scopes, browser, b, foreign.datasetId);
    await openRunningList(mine.page);
    const reads = await mine.watch.reads();
    await attach('second-organization-report-reads', reads);
    const read = expectOneRead(reads, b);
    expect(read.sources).toEqual(report.sources);
    await expectTiles(mine.page, read);
    const shown = await mine.page.locator('body').innerText();
    for (const value of privateValues) expect(shown).not.toContain(value);
    expect(mine.watch.runSources).toEqual([]);
    await mine.context.close();

    // Nothing of the first organization changed, and no request reached its endpoints.
    expect(await state()).toEqual(before);
    expect(await experimentsOf(foreign.datasetId)).toEqual([]);
  }, { timeout: UI_READY * 5 });
});

// The three walks below drive the wizard to Run for each kind. Each waits for what its
// annotation names; the walk above covers what this stack and this build can run today.

test.fixme('EXP-E2E-008 wizard: an HTTP endpoint source is added, mapped and run from New experiment', {
  annotation: { type: 'harness-gap', description: HTTPS_GAP },
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  test.setTimeout(900_000);
  const a = scopes.ownerA;
  const prefix = `e2e-exp8-http-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const questions = [0, 1].map(i => `${prefix}-question-${i}`);
  const seed = await seedForWizard(scopes, probe, a, prefix, questions);
  const { page, context } = await openNewExperiment(scopes, browser, a, seed.datasetId);
  await openRunningList(page);
  await page.getByTestId('catalog-tile-http').click();
  // An https:// URL of the stack whose certificate the server accepts; it answers {"reply": "echo: <message>"}.
  await page.getByRole('textbox', { name: /^Endpoint URL/ }).fill(HTTPS_ENDPOINT);
  // The card's stock body sends the dataset's input column as `message`; the Data step maps it.
  const response = await runFromWizard(page, `${prefix}-experiment`, seed.evalName);
  expect(response.status(), await response.text()).toBe(200);
  const submitted = response.request().postDataJSON();
  expect(submitted.prompt_config).toHaveLength(1);
  expect(submitted.prompt_config[0]).toMatchObject({ target_type: 'http',
    http: { url: HTTPS_ENDPOINT, method: 'POST', output_path: '$.reply' } });
  const id = /experiment_id=([0-9a-f-]{36})/.exec((await response.json()).result)?.[1];
  expect(id).toBeTruthy();
  await expect.poll(async () => (await sourcesOf(probe, id!)).map(s => [s.status, s.phase]), RUN_DONE)
    .toEqual([['Completed', 'finished']]);
  const [source] = await sourcesOf(probe, id!);
  const output = await outputCells(probe, id!, source.id);
  expect(output.cells.map(c => c.status)).toEqual(questions.map(() => 'pass'));
  expect(output.cells.map(c => String(c.value)).sort()).toEqual(questions.map(q => `echo: ${q}`).sort());
  await context.close();
});

test.fixme('EXP-E2E-008 wizard: an A2A agent is discovered and run without fetched_from in the request', {
  annotation: { type: 'harness-gap', description: A2A_GAP },
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  test.setTimeout(900_000);
  const a = scopes.ownerA;
  const prefix = `e2e-exp8-a2a-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const questions = [0, 1].map(i => `${prefix}-question-${i}`);
  const seed = await seedForWizard(scopes, probe, a, prefix, questions);
  const { page, context } = await openNewExperiment(scopes, browser, a, seed.datasetId);
  await openRunningList(page);
  await page.getByTestId('catalog-tile-a2a').click();
  await page.getByTestId('a2a-url').fill(A2A_AGENT);
  await page.getByTestId('a2a-discover').click();
  // The card shows where it was read from; that line is display only.
  await expect(page.getByTestId('a2a-card')).toContainText(`${A2A_AGENT}/.well-known/agent-card.json`, { timeout: UI_READY });
  await expect(page.getByTestId('a2a-error')).toHaveCount(0);
  const response = await runFromWizard(page, `${prefix}-experiment`, seed.evalName);
  // Run is not refused: the request carries the card and no fetched_from
  // (Experiment/serverBody.js A2A_DISPLAY_ONLY; the server's A2A settings take no such field).
  expect(response.status(), await response.text()).toBe(200);
  const submitted = response.request().postDataJSON();
  expect(submitted.prompt_config).toHaveLength(1);
  expect(submitted.prompt_config[0].target_type).toBe('a2a');
  expect(submitted.prompt_config[0].a2a).not.toHaveProperty('fetched_from');
  expect(submitted.prompt_config[0].a2a.card).toMatchObject({ url: expect.any(String) });
  expect(submitted.prompt_config[0].a2a.text_column).toBe(seed.columnId);
  const id = /experiment_id=([0-9a-f-]{36})/.exec((await response.json()).result)?.[1];
  expect(id).toBeTruthy();
  await expect.poll(async () => (await sourcesOf(probe, id!)).map(s => [s.status, s.phase]), RUN_DONE)
    .toEqual([['Completed', 'finished']]);
  const [source] = await sourcesOf(probe, id!);
  // The mock agent answers each message/send with a text part `echo: <text>`.
  const output = await outputCells(probe, id!, source.id);
  expect(output.cells.map(c => c.status)).toEqual(questions.map(() => 'pass'));
  expect(output.cells.map(c => String(c.value)).sort()).toEqual(questions.map(q => `echo: ${q}`).sort());
  await context.close();
});

test.fixme('EXP-E2E-008 wizard: an MCP server tool is connected and run on every row', {
  annotation: { type: 'not-built', description: MCP_GAP },
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  test.setTimeout(900_000);
  const a = scopes.ownerA;
  const prefix = `e2e-exp8-mcp-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const seed = await seedForWizard(scopes, probe, a, prefix, [0, 1].map(i => `${prefix}-question-${i}`));
  // First the server must say it can run one; today it answers MCP_NOT_BUILT.
  const reported = await scopes.send<Capabilities>(a, 'GET', CAPABILITIES);
  expect(reported.body.experiment_run_sources.sources.mcp_tool).toMatchObject({ available: true, reason: null });
  const { page, context } = await openNewExperiment(scopes, browser, a, seed.datasetId);
  await openRunningList(page);
  await expect(page.getByTestId('catalog-tile-mcp_tool')).not.toHaveAttribute('aria-disabled', 'true');
  await page.getByTestId('catalog-tile-mcp_tool').click();
  // Experiment/McpToolRunForm.jsx: the stack's MCP mock (e2e/stack/mcp-mock), its `echo` tool.
  await expect(page.getByTestId('mcp-form')).toBeVisible({ timeout: UI_READY });
  await page.getByTestId('mcp-command').fill(MCP_MOCK.url);
  await page.getByTestId('mcp-connect').click();
  await expect(page.getByTestId('mcp-tool-echo')).toBeVisible({ timeout: UI_READY });
  await page.getByTestId('mcp-tool-echo').click();
  await expect(page.getByTestId('mcp-arg-text')).toBeVisible();
  const response = await runFromWizard(page, `${prefix}-experiment`, seed.evalName);
  expect(response.status(), await response.text()).toBe(200);
  await context.close();
});
