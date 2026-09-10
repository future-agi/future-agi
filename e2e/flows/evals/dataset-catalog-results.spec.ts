import { type Request, type Response } from '@playwright/test';
import { isDeepStrictEqual } from 'node:util';
import { test, expect } from '../../lib/mock-model-fixtures';
import { uploadFixture } from '../../lib/upload';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Approved core/observed-catalog-eval4-flow-plan-20260909.md. Shared LOCAL
// serving-health route must be attested BEFORE model registration and dispatch.
// Main separately attests eval-evaluation/default/tasks_l: this fixture's
// agent_compass receipt is NOT evidence of the dataset schedule/queues.
test.use({ evalBackground: true });

// model_hub/serializers/contracts.py and EvaluationDrawer.jsx: public wires.
const UPLOAD = '/model-hub/develops/create-dataset-from-local-file/';
const TEMPLATES = '/model-hub/eval-templates/create-v2/';
const DATASETS = '/model-hub/develops/';
// useDashboards.js / WidgetEditorView: native catalog and widget operations.
const METRICS = '/tracer/dashboard/metrics/';
const VALUES = '/tracer/dashboard/filter_values/';
const QUERY = '/tracer/dashboard/query/';
const DASHBOARDS = '/tracer/dashboard/';
const UI_READY = 60_000;

// EXPECTATION-ONLY fallibility anchors. Never interpolate these into a prompt,
// uploaded input, selected UI filter, or API setup body.
const EXPECTED_QUANTITY_MAPPING: 'region' | 'quantity' = 'quantity'; // EVAL004_NEG_BINDING
const EXPECTED_R7_API_NUMBER = 0.7; // EVAL004_NEG_TERMINAL_RESULT
const EXPECTED_R3_CH_VALUE = '0.3'; // EVAL004_NEG_CDC_CELL
const EXPECTED_NUMBER_ROWS: Alias[] = ['R7', 'R3']; // EVAL004_NEG_FILTER_BOUNDARY
const EXPECTED_SUM = 1.2;
type Alias = 'R2' | 'R7' | 'R3';
type FamilyKey = 'choice' | 'number';
// Independently specified from the checked-in CSV, not computed from replies.
const ORACLE = [
  { alias: 'R2', region: 'catalog-west', quantity: '2', choice: 'catalog-west', number: 0.2,
    choiceValue: "{'score': 0.2, 'choice': 'catalog-west'}", numberValue: '0.2',
    choiceReason: 'e4 choice catalog-west/2', numberReason: 'e4 number catalog-west/2' },
  { alias: 'R7', region: 'catalog-east', quantity: '7', choice: 'catalog-east', number: 0.7,
    choiceValue: "{'score': 0.8, 'choice': 'catalog-east'}", numberValue: '0.7',
    choiceReason: 'e4 choice catalog-east/7', numberReason: 'e4 number catalog-east/7' },
  { alias: 'R3', region: 'catalog-west', quantity: '3', choice: 'catalog-west', number: 0.3,
    choiceValue: "{'score': 0.2, 'choice': 'catalog-west'}", numberValue: '0.3',
    choiceReason: 'e4 choice catalog-west/3', numberReason: 'e4 number catalog-west/3' },
] satisfies { alias: Alias; region: string; quantity: string; choice: string; number: number;
  choiceValue: string; numberValue: string; choiceReason: string; numberReason: string }[];
type Outcome = typeof ORACLE[number];
type Family = { key: FamilyKey; name: string; instructions: string;
  wireType: 'deterministic' | 'percentage'; output: 'choices' | 'score'; dataType: 'array' | 'float';
  choiceScores: Record<string, number> | null; templateId: string; bindingId: string;
  columnId: string; reasonColumnId: string };
type Column = { id: string; dataset_id: string; name: string; data_type: string;
  source: string; source_id: string | null; status: string; metadata: unknown; deleted: boolean };
type Cell = { id: string; dataset_id: string; column_id: string; row_id: string;
  value: string | null; value_infos: unknown; feedback_info: unknown; column_metadata: unknown;
  status: string; prompt_tokens: number | null; completion_tokens: number | null;
  response_time: number | null; created_at: string; updated_at: string;
  deleted: boolean; deleted_at: string | null };
type EvalInfo = { name: string; output: string; model: string; data: { result: string | number };
  reason: string; failure: boolean; metrics: { id: string; value: string | number }[];
  runtime: number; metadata: unknown; warnings?: unknown };
type APICell = { cell_id: string; cell_value: string | null; status: string;
  value_infos: unknown; feedback_info: unknown; metadata: {
    response_time_ms: number | null; token_count: number | null; annotation: unknown } };
type TableRow = { row_id: string; order: number } & Record<string, unknown>;
type TableBody = { result: { table: TableRow[]; column_config: {
  id: string; name: string; data_type: string; origin_type: string; source_id: string | null }[];
  metadata: { total_rows: number; has_more: boolean; next_page_index: number | null;
    next_cursor: string | null; current_page_index: number; error_messages: unknown[] } } };
type EvalList = { result: { evals: { id: string; template_id: string; name: string;
  column_id: string | null; status: string; mapping: Record<string, string> }[] } };
type NativePage = { has_more: boolean; next_cursor: string | null;
  query_complete: boolean; query_status: string; browse_status?: string };
type Metric = { property_id: string; name: string; display_name: string; output_type: string };
type CatalogBody = { result: NativePage & { metrics: Metric[] } };
type ValuesBody = { result: NativePage & { values: { value: string; label: string }[] } };
type Filter = { column_id: string; property_id?: string; source?: string;
  filter_config: { filter_type: string; filter_op: string; filter_value: unknown; col_type?: string } };
type QueryConfig = { metrics: { id: string; column_id: string; property_id: string;
  source: string; type: string; aggregation: string }[]; filters: Filter[]; [key: string]: unknown };
type QueryBody = { result: { query_complete: boolean; query_exact: boolean;
  metrics: { id: string; series: { data: { value: number | null }[] }[] }[] } };
type Widget = { id: string; name: string; query_config: QueryConfig; chart_config: Record<string, unknown> };

test('EVAL-E2E-004: executed dataset evaluations retain typed cells, exact filters and a saved numeric widget', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-004', area: 'evals',
    userGoal: 'Evaluate new dataset rows and use their actual results in dataset filters and a saved dashboard widget',
    steps: ['upload the synthetic CSV and attach two local mock-backed evaluations through the dataset UI',
      'use native Run All once and read both completed evaluations and their result/reason cells',
      'verify exact typed Postgres and latest ClickHouse cell identities with unchanged inputs',
      'select discovered choice, numeric boundary and intersection filters and read exact rows',
      'save and reopen a numeric Sum widget scoped to this dataset'],
    backendChecks: [
      'the imported input matrix and two save-only evaluation bindings retain exact tenant, model, version and column mappings',
      'both native Run All requests complete and the public table contains exactly six correct result and six reason cells',
      'latest ClickHouse cells equal exact Postgres identities and independently expected typed values without changing imported inputs',
      'native choice, numeric and intersection filters return exact row identities and the saved/reopened widget retains its scoped sum',
    ],
  }),
}, async ({ page, actor, probe, mockModel }, testInfo) => {
  test.setTimeout(630_000); // ASYNC60 + EVAL90 + CDC180 + 4*UI60 + setup/headroom60.
  page.setDefaultTimeout(UI_READY);
  const ui = expect.configure({ timeout: UI_READY });
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const datasetName = `e2e-eval4-data-${suffix}`;
  const dashboardName = `e2e-eval4-dashboard-${suffix}`;
  const widgetName = `e2e-eval4-sum-${suffix}`;
  const families: Family[] = [
    { key: 'choice', name: `e2e-eval4-choice-${suffix}`, wireType: 'deterministic', output: 'choices', dataType: 'array',
      instructions: 'Reply with exactly this JSON: {"result":"{{region}}","explanation":"e4 choice {{region}}/{{quantity}}"}',
      choiceScores: { 'catalog-west': 0.2, 'catalog-east': 0.8 } },
    { key: 'number', name: `e2e-eval4-number-${suffix}`, wireType: 'percentage', output: 'score', dataType: 'float',
      instructions: 'Reply with exactly this JSON: {"result":0.{{quantity}},"explanation":"e4 number {{region}}/{{quantity}}"}',
      choiceScores: null },
  ].map(f => ({ ...f, templateId: '', bindingId: '', columnId: '', reasonColumnId: '' } as Family));
  const [choice, number] = families;
  const ids = {} as Record<Alias, string>;
  const inputColumns = {} as Record<'region' | 'quantity', string>;
  let datasetId = '';
  let inputCells: Cell[] = [];
  let finalPG: Cell[] = [];
  let finalCH: Cell[] = [];
  let columns: Column[] = [];
  const requests = new Map<Request, number>();
  page.on('request', outgoing => requests.set(outgoing, Date.now()));
  const attach = (name: string, data: unknown) => testInfo.attach(name, {
    contentType: 'application/json', body: JSON.stringify(data),
  });
  const fresh = (r: Response, path: string, method: string, since: number) =>
    new URL(r.url()).pathname === path && r.request().method() === method &&
    (requests.get(r.request()) ?? -1) >= since;
  const scope = (outgoing: Request) => {
    const headers = outgoing.headers();
    expect(headers['x-organization-id']).toBe(actor.organizationId);
    expect(headers['x-workspace-id']).toBe(actor.workspaceId);
    expect(headers.authorization).toMatch(/^Bearer \S+$/);
    return { organizationId: headers['x-organization-id'], workspaceId: headers['x-workspace-id'] };
  };
  const receipt = async <T,>(label: string, response: Response): Promise<T> => {
    const outgoing = response.request();
    const body = await response.json() as T;
    await attach(label, { method: outgoing.method(), path: new URL(response.url()).pathname,
      scope: scope(outgoing), startedAt: requests.get(outgoing),
      params: Object.fromEntries(new URL(outgoing.url()).searchParams),
      request: outgoing.method() === 'GET' ? null : outgoing.postDataJSON(),
      status: response.status(), requestId: response.headers()['x-request-id'] ?? null, body });
    expect(response.status()).toBe(200);
    return body;
  };
  const readColumns = () => probe.pg<Column>(`SELECT id,dataset_id,name,data_type,source,source_id,status,metadata,deleted
    FROM model_hub_column WHERE dataset_id=$1 ORDER BY id::text`, [datasetId]);
  // Cell/BaseModel + oss_cdc_source: retain microseconds, NULL and all payload fields.
  const cellFields = `id,dataset_id,column_id,row_id,value,value_infos,feedback_info,status,column_metadata,
    prompt_tokens,completion_tokens,response_time,deleted`;
  const readPG = () => probe.pg<Cell>(`SELECT ${cellFields},
    to_char(created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS created_at,
    to_char(updated_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS updated_at,
    to_char(deleted_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS deleted_at
    FROM model_hub_cell WHERE dataset_id=$1 ORDER BY id::text`, [datasetId]);
  const readCH = async () => {
    const rows = await probe.ch<Omit<Cell, 'value_infos' | 'feedback_info' | 'column_metadata'> & {
      value_infos: string | null; feedback_info: string | null; column_metadata: string | null; cdc_deleted: number }>(
      `SELECT ${cellFields},toUInt8(_peerdb_is_deleted) AS cdc_deleted,
      formatDateTime(created_at,'%Y-%m-%dT%H:%i:%S.%fZ','UTC') AS created_at,
      formatDateTime(updated_at,'%Y-%m-%dT%H:%i:%S.%fZ','UTC') AS updated_at,
      formatDateTime(deleted_at,'%Y-%m-%dT%H:%i:%S.%fZ','UTC') AS deleted_at
      FROM model_hub_cell FINAL WHERE dataset_id={d:UUID} ORDER BY toString(id)`, { d: datasetId });
    await attach('cell-cdc-raw-observation', rows);
    return rows.map(({ cdc_deleted, value_infos, feedback_info, column_metadata, ...row }) => {
      // Only the CDC system flag is projected as UInt8; never coerce nullable
      // product fields (including the native Bool deleted column).
      expect(cdc_deleted).toBe(0);
      expect(typeof row.deleted).toBe('boolean');
      // One JSONField transport layer only; result value_infos remains a string.
      return { ...row, value_infos: value_infos === null ? null : JSON.parse(value_infos),
        feedback_info: feedback_info === null ? null : JSON.parse(feedback_info),
        column_metadata: column_metadata === null ? null : JSON.parse(column_metadata) } as Cell;
    });
  };
  const evalPath = () => `${DATASETS}${datasetId}/get_evals_list/`;
  const tablePath = () => `${DATASETS}${datasetId}/get-dataset-table/`;
  const readEvals = () => actor.api.get<EvalList>(evalPath(), { eval_type: 'user' });
  const orderedIds = (aliases: Alias[]) => aliases.map(alias => ids[alias]).sort();
  const tableParams = { current_page_index: 0, page_size: 30, filters: '[]', sort: '[]' };
  const assertTablePage = (body: TableBody, aliases: Alias[]) => {
    expect(body.result.metadata).toMatchObject({ total_rows: aliases.length, has_more: false,
      next_page_index: null, next_cursor: null, current_page_index: 0, error_messages: [] });
    // Ordinary native offset pages do not advertise signed-snapshot exactness.
    expect(body.result.table.map(row => row.row_id).sort()).toEqual(orderedIds(aliases));
  };
  const assertInfo = (info: EvalInfo, family: Family, wanted: Outcome, stored: boolean) => {
    const result = family.key === 'choice' ? wanted.choice : wanted.number;
    const reason = family.key === 'choice' ? wanted.choiceReason : wanted.numberReason;
    expect(info).toMatchObject({ name: family.name, output: family.output, model: '',
      data: { result }, reason, failure: false, metrics: [{ id: 'custom_eval_score', value: result }] });
    expect(info.metrics).toHaveLength(1);
    expect(info.warnings).toBeUndefined();
    expect(typeof info.runtime).toBe('number');
    expect(Number.isFinite(info.runtime) && info.runtime >= 0).toBe(true);
    if (stored) expect(typeof info.metadata).toBe('string');
    const metadata = stored ? JSON.parse(info.metadata as string) : info.metadata;
    expect(metadata).toMatchObject({ usage: { prompt_tokens: 7, completion_tokens: 7, total_tokens: 14 },
      explanation: reason, response_time: info.runtime });
  };
  const assertStoredMatrix = (cells: Cell[]) => {
    expect(cells).toHaveLength(18);
    expect(cells.filter(c => Object.values(inputColumns).includes(c.column_id))).toEqual(inputCells);
    for (const family of families) for (const wanted of ORACLE) {
      for (const reason of [false, true]) {
        const matches = cells.filter(c => c.row_id === ids[wanted.alias] &&
          c.column_id === (reason ? family.reasonColumnId : family.columnId));
        expect(matches).toHaveLength(1);
        const cell = matches[0];
        expect(cell).toMatchObject({ dataset_id: datasetId, status: 'pass', deleted: false, deleted_at: null,
          prompt_tokens: null, completion_tokens: null, response_time: null, feedback_info: {}, column_metadata: {},
          value: reason ? (family.key === 'choice' ? wanted.choiceReason : wanted.numberReason)
            : (family.key === 'choice' ? wanted.choiceValue : wanted.numberValue) });
        expect(typeof cell.value_infos).toBe('string');
        assertInfo(JSON.parse(cell.value_infos as string), family, wanted, true);
      }
    }
    expect(new Set(cells.map(c => `${c.row_id}:${c.column_id}`)).size).toBe(18);
  };

  await attach('execution-boundaries', { datasetName, families, fixture: 'catalog-regions.csv',
    fixtureSHA256: '1cd5e33c7579bb5f36dc2c27915c40a67d9e71b6c6cb90dfcd6aa670bc68bbaf',
    expected: ORACLE, organizationId: actor.organizationId, workspaceId: actor.workspaceId,
    prerequisite: 'Main must freshly attest eval-evaluation/default/tasks_l and packaged dataset runner before managed execution.',
    proof: 'Authoring/typecheck are not managed execution or four one-sided fallibility proofs.' });

  // DATA001 / file_upload.py: real upload, never insert equivalent rows ourselves.
  await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Add Dataset', exact: true }).first().click();
  await page.getByText('Upload a file (JSON, CSV)', { exact: true }).click();
  const uploadDialog = page.getByRole('dialog').filter({ has: page.getByText('Upload a File', { exact: true }) });
  await uploadFixture(uploadDialog.locator('input[type="file"]'), 'catalog-regions.csv');
  await uploadDialog.getByPlaceholder('Dataset Name').fill(datasetName);
  const uploadAt = Date.now();
  const [uploaded] = await Promise.all([
    page.waitForResponse(r => fresh(r, UPLOAD, 'POST', uploadAt), { timeout: UI_READY }),
    uploadDialog.getByRole('button', { name: 'Upload', exact: true }).click(),
  ]);
  const uploadBody = await uploaded.json() as { result: { dataset_id: string; processing_status: string } };
  await attach('uploaded-dataset', { status: uploaded.status(), scope: scope(uploaded.request()), body: uploadBody });
  expect(uploaded.status()).toBe(200);
  expect(uploadBody.result.processing_status).toBe('queued');
  datasetId = uploadBody.result.dataset_id;
  await ui(page).toHaveURL(new RegExp(`/dashboard/develop/${datasetId}`));

  await test.step('backend check 1: imported matrix and exact save-only bindings', async () => {
    await expect.poll(async () => (await readPG()).length, POLL.ASYNC_JOB).toBe(6);
    inputCells = await readPG();
    columns = await readColumns();
    expect(columns.map(c => [c.name, c.data_type]).sort()).toEqual([['quantity', 'integer'], ['region', 'text']]);
    for (const key of ['region', 'quantity'] as const) inputColumns[key] = columns.find(c => c.name === key)!.id;
    const datasets = await probe.pg(`SELECT id,name,organization_id,workspace_id,source,dataset_config
      FROM model_hub_dataset WHERE id=$1 AND NOT deleted`, [datasetId]);
    expect(datasets).toHaveLength(1);
    expect(datasets[0]).toMatchObject({ id: datasetId, name: datasetName,
      organization_id: actor.organizationId, workspace_id: actor.workspaceId });
    const rows = await probe.pg<{ id: string; dataset_id: string; order: number }>(
      'SELECT id,dataset_id,"order" FROM model_hub_row WHERE dataset_id=$1 AND NOT deleted ORDER BY "order",id::text', [datasetId]);
    expect(rows).toHaveLength(3);
    expect(rows.map(row => ['region', 'quantity'].map(key => inputCells.find(c => c.row_id === row.id &&
      c.column_id === inputColumns[key as 'region' | 'quantity'])?.value)))
      .toEqual([['catalog-west', '2'], ['catalog-east', '7'], ['catalog-west', '3']]);
    for (const wanted of ORACLE) {
      const row = rows.find(row => inputCells.some(c => c.row_id === row.id && c.column_id === inputColumns.quantity && c.value === wanted.quantity))!;
      expect(row.dataset_id).toBe(datasetId);
      ids[wanted.alias] = row.id;
    }
    for (const cell of inputCells) expect(cell).toMatchObject({ dataset_id: datasetId, status: 'pass', deleted: false, deleted_at: null });
    await attach('imported-input-identities', { datasets, columns, rows, ids, cells: inputCells });

    for (const family of families) {
      expect(family.name.length).toBeLessThanOrEqual(50);
      await mockModel.assertReady();
      const wire = { name: family.name, eval_type: 'llm', instructions: family.instructions, model: mockModel.model,
        output_type: family.wireType, choice_scores: family.choiceScores, pass_threshold: 0.5,
        check_internet: false, error_localizer_enabled: false };
      const created = await actor.api.post<{ result: { id: string; name: string; version: string } }>(TEMPLATES, wire);
      family.templateId = created.result.id;
      expect(created.result).toMatchObject({ name: family.name, version: 'V1' });
      await attach(`template-${family.key}`, { request: wire, body: created });
    }
    await page.getByRole('button', { name: 'Evaluate', exact: true }).click();
    for (const [index, family] of families.entries()) {
      // SavedEvalsList's populated header owns this Add; collapsed picker
      // rows can still contain other Add buttons while the drawer transitions.
      if (index === 0) {
        await page.getByRole('button', { name: 'Add Evaluations', exact: true }).click();
      } else {
        await page.getByText(`Evals (${index})`, { exact: true }).locator('..')
          .getByRole('button', { name: 'Add', exact: true }).click();
      }
      await page.getByPlaceholder('Search evaluations...', { exact: true }).fill(family.name);
      const templateRow = page.getByRole('row').filter({ has: page.getByText(family.name, { exact: true }) });
      await templateRow.getByRole('button', { name: 'Add', exact: true }).click();
      // EvalPickerConfigFull proposes a timestamped dataset name. Choose our
      // independently minted binding name through its real editable control.
      await page.getByPlaceholder('e.g. toxicity-check, my-custom-eval', { exact: true }).fill(family.name);
      // DatasetTestMode auto-maps names; explicitly select both real controls.
      for (const key of ['region', 'quantity'] as const) {
        const mappingRow = page.getByText(key, { exact: true }).locator('xpath=../..')
          .filter({ has: page.getByPlaceholder('Select column', { exact: true }) });
        await mappingRow.getByPlaceholder('Select column', { exact: true }).click();
        await page.getByPlaceholder('Search columns…', { exact: true }).fill(key);
        // Captured managed DOM: ColumnTreeSelect's Popper exposes tooltip role.
        await page.getByRole('tooltip').filter({ has: page.getByPlaceholder('Search columns…', { exact: true }) })
          .getByText(key, { exact: true }).click();
        await ui(mappingRow.getByPlaceholder('Select column', { exact: true })).toHaveValue(key);
      }
      const attachedAt = Date.now();
      const [added] = await Promise.all([
        page.waitForResponse(r => fresh(r, `${DATASETS}${datasetId}/add_user_eval/`, 'POST', attachedAt), { timeout: UI_READY }),
        page.getByRole('button', { name: 'Add Evaluation', exact: true }).click(),
      ]);
      expect((await receipt<{ status: boolean }>(`attach-${family.key}`, added)).status).toBe(true);
      const wire = added.request().postDataJSON();
      expect(wire).toMatchObject({ name: family.name, template_id: family.templateId, model: mockModel.model,
        run: false, error_localizer: false, config: { mapping: { region: inputColumns.region, quantity: inputColumns.quantity } } });
      expect(wire.kb_id).toBeUndefined();
      expect(wire.experiment_id).toBeUndefined();
      // Current EvalPicker carries the resolved template config, not an invented {}.
      expect(wire.config.config).toMatchObject({ eval_type_id: 'CustomPromptEvaluator', output: family.output,
        check_internet: false, error_localizer_enabled: false, template_format: 'mustache' });
      // Hydration wraps this one instruction in one system message; no extra
      // messages or few-shot examples are supplied by this journey.
      expect(wire.config.config.messages).toEqual([{ role: 'system', content: family.instructions }]);
      expect(wire.config.config.few_shot_examples).toBeUndefined();
      expect(wire.config.run_config).toMatchObject({ model: mockModel.model, check_internet: false,
        error_localizer_enabled: false, data_injection: { variables_only: true } });
      // EvalPickerConfigFull.build_tools_payload returns an object, not a list.
      expect(wire.config.run_config.tools).toEqual({});
      expect(wire.config.run_config.knowledge_bases).toEqual([]);
      expect(wire.config.run_config.knowledge_base_id).toBeUndefined();
      expect(wire.config.run_config.data_injection).toEqual({ variables_only: true });
    }
    const bindings = await probe.pg<{ id: string; dataset_id: string; template_id: string; name: string;
      organization_id: string; workspace_id: string; model: string; status: string; kb_id: string | null;
      error_localizer: boolean; pinned_version_id: string | null; config: { mapping: Record<string, string>; reason_column: boolean } }>(
      `SELECT id,dataset_id,template_id,name,organization_id,workspace_id,model,status,kb_id,error_localizer,pinned_version_id,config
      FROM model_hub_userevalmetric WHERE dataset_id=$1 AND NOT deleted ORDER BY name`, [datasetId]);
    expect(bindings).toHaveLength(2);
    for (const family of families) {
      const binding = bindings.find(b => b.template_id === family.templateId)!;
      expect(binding).toMatchObject({ dataset_id: datasetId, name: family.name, organization_id: actor.organizationId,
        workspace_id: actor.workspaceId, model: mockModel.model, status: 'Inactive', kb_id: null,
        error_localizer: false, pinned_version_id: null, config: { reason_column: true } });
      expect(binding.config.mapping, 'EVAL004_NEG_BINDING').toEqual({ region: inputColumns.region,
        quantity: inputColumns[EXPECTED_QUANTITY_MAPPING] });
      family.bindingId = binding.id;
      const templates = await probe.pg(`SELECT id,organization_id,workspace_id,model,eval_type,multi_choice,choice_scores,config
        FROM model_hub_evaltemplate WHERE id=$1 AND NOT deleted`, [family.templateId]);
      const versions = await probe.pg(`SELECT id,eval_template_id,version_number,is_default,model,organization_id,workspace_id,config_snapshot
        FROM model_hub_eval_template_version WHERE eval_template_id=$1 AND NOT deleted`, [family.templateId]);
      expect(templates).toHaveLength(1); expect(versions).toHaveLength(1);
      expect(templates[0]).toMatchObject({ organization_id: actor.organizationId, workspace_id: actor.workspaceId,
        model: mockModel.model, eval_type: 'llm', multi_choice: false, choice_scores: family.choiceScores,
        config: { rule_prompt: family.instructions, output: family.output, eval_type_id: 'CustomPromptEvaluator', check_internet: false } });
      expect(versions[0]).toMatchObject({ eval_template_id: family.templateId, version_number: 1, is_default: true,
        model: mockModel.model, organization_id: actor.organizationId, workspace_id: actor.workspaceId,
        config_snapshot: { rule_prompt: family.instructions, output: family.output } });
      await attach(`binding-and-default-version-${family.key}`, { binding, templates, versions });
    }
    const list = await readEvals();
    expect(list.result.evals.map(e => e.id).sort()).toEqual(families.map(f => f.bindingId).sort());
    for (const item of list.result.evals) expect(item).toMatchObject({ status: 'Inactive', column_id: null });
    expect(await readPG()).toEqual(inputCells);
    expect(await readColumns()).toEqual(columns);
    await attach('save-only-eval-list', list);
  });

  await test.step('backend check 2: native Run All and exact public result/reason cells', async () => {
    await mockModel.assertReady();
    await ui(page.getByRole('button', { name: 'Run All (2)', exact: true })).toBeEnabled();
    const startedAt = Date.now();
    const runPath = `${DATASETS}${datasetId}/start_evals_process/`;
    const responses = families.map(f => page.waitForResponse(r => fresh(r, runPath, 'POST', startedAt) &&
      isDeepStrictEqual(r.request().postDataJSON(), { user_eval_ids: [f.bindingId] }), { timeout: UI_READY }));
    const [choiceRun, numberRun] = await Promise.all([...responses,
      page.getByRole('button', { name: 'Run All (2)', exact: true }).click()]);
    expect((await receipt<{ status: boolean }>('run-all-choice', choiceRun! as Response)).status).toBe(true);
    expect((await receipt<{ status: boolean }>('run-all-number', numberRun! as Response)).status).toBe(true);
    // One shared terminal wall for both bindings, never two sequential 90s polls.
    await expect.poll(async () => {
      const body = await readEvals();
      await attach('eval-terminal-observation', body);
      return body.result.evals.map(e => ({ id: e.id, status: e.status })).sort((a, b) => a.id.localeCompare(b.id));
    }, POLL.EVAL_RESULT).toEqual(families.map(f => ({ id: f.bindingId, status: 'Completed' }))
      .sort((a, b) => a.id.localeCompare(b.id)));
    columns = await readColumns();
    expect(columns).toHaveLength(6);
    for (const family of families) {
      const result = columns.filter(c => c.source === 'evaluation' && c.source_id === family.bindingId);
      expect(result).toHaveLength(1);
      family.columnId = result[0].id;
      expect(result[0]).toMatchObject({ dataset_id: datasetId, name: family.name, data_type: family.dataType, deleted: false });
      const reasons = columns.filter(c => c.source === 'evaluation_reason' && c.source_id === `${family.columnId}-sourceid-${family.bindingId}`);
      expect(reasons).toHaveLength(1);
      family.reasonColumnId = reasons[0].id;
      expect(reasons[0]).toMatchObject({ dataset_id: datasetId, name: `${family.name}-reason`, data_type: 'text', deleted: false });
    }
    await attach('executed-column-identities', { families, columns });
    const body = await actor.api.get<TableBody>(tablePath(), tableParams);
    await attach('public-terminal-table', { path: tablePath(), params: tableParams, body });
    assertTablePage(body, ['R2', 'R7', 'R3']);
    expect(body.result.column_config.map(c => c.id).sort()).toEqual(columns.map(c => c.id).sort());
    for (const row of body.result.table)
      expect(Object.keys(row).filter(key => key !== 'row_id' && key !== 'order').sort())
        .toEqual(columns.map(c => c.id).sort());
    for (const family of families) for (const wanted of ORACLE) {
      const row = body.result.table.find(r => r.row_id === ids[wanted.alias])!;
      const cell = row[family.columnId] as APICell;
      const reason = row[family.reasonColumnId] as APICell;
      expect(cell).toMatchObject({ status: 'pass', cell_value: family.key === 'choice' ? wanted.choiceValue : wanted.numberValue });
      expect(reason).toMatchObject({ status: 'pass', cell_value: family.key === 'choice' ? wanted.choiceReason : wanted.numberReason });
      assertInfo(cell.value_infos as EvalInfo, family, wanted, false);
      assertInfo(reason.value_infos as EvalInfo, family, wanted, false);
      for (const resultCell of [cell, reason]) {
        expect(resultCell.feedback_info).toEqual({});
        expect(resultCell.metadata).toMatchObject({ token_count: 14,
          response_time_ms: (resultCell.value_infos as EvalInfo).runtime });
      }
      if (family.key === 'number' && wanted.alias === 'R7')
        expect((cell.value_infos as EvalInfo).data.result, 'EVAL004_NEG_TERMINAL_RESULT').toBe(EXPECTED_R7_API_NUMBER);
    }
    finalPG = await readPG();
    await attach('executed-postgres-cells', finalPG);
    assertStoredMatrix(finalPG);
    for (const cell of finalPG) {
      const row = body.result.table.find(row => row.row_id === cell.row_id)!;
      expect((row[cell.column_id] as APICell).cell_id).toBe(cell.id);
      expect((row[cell.column_id] as APICell).cell_value).toBe(cell.value);
    }
  });

  await test.step('backend check 3: nullable native CDC and independent cell matrix', async () => {
    const schema = await probe.ch<{ name: string; type: string }>(
      'SELECT name,type FROM system.columns WHERE database={db:String} AND table={table:String} ORDER BY name',
      { db: E2E.chDatabase, table: 'model_hub_cell' });
    await attach('native-cell-schema', schema);
    const types = Object.fromEntries(schema.map(c => [c.name, c.type]));
    expect(types).toMatchObject({ id: 'UUID', dataset_id: 'Nullable(UUID)', column_id: 'UUID', row_id: 'UUID',
      value: 'Nullable(String)', value_infos: 'Nullable(String)', feedback_info: 'Nullable(String)',
      column_metadata: 'Nullable(String)', prompt_tokens: 'Nullable(Int32)', completion_tokens: 'Nullable(Int32)',
      response_time: 'Nullable(Float64)', deleted: 'Bool', created_at: 'DateTime64(6)',
      updated_at: 'DateTime64(6)', deleted_at: 'Nullable(DateTime64(6))' });
    await expect.poll(async () => { finalCH = await readCH(); return finalCH; }, POLL.CDC_VISIBLE).toEqual(finalPG);
    assertStoredMatrix(finalCH);
    expect(finalCH.find(c => c.row_id === ids.R3 && c.column_id === number.columnId)!.value,
      'EVAL004_NEG_CDC_CELL').toBe(EXPECTED_R3_CH_VALUE);
    expect(await readPG()).toEqual(finalPG);
    await attach('native-cell-parity', { postgres: finalPG, clickhouse: finalCH });
  });

  const gridCells = (columnId: string) => page.locator(`.ag-center-cols-container .ag-row [col-id="${columnId}"]`);
  const assertVisibleRows = async (aliases: Alias[]) => {
    await ui.poll(async () => Promise.all((await gridCells(inputColumns.region).all()).map(cell =>
      cell.locator('..').getAttribute('row-id')))).toEqual(ORACLE.filter(o => aliases.includes(o.alias)).map(o => ids[o.alias]));
    await ui(gridCells(inputColumns.region)).toHaveText(ORACLE.filter(o => aliases.includes(o.alias)).map(o => o.region));
  };
  const filterSignature = (filters: Filter[]) => filters.map(f => ({ id: f.column_id, property: f.property_id,
    type: f.filter_config.filter_type, op: f.filter_config.filter_op, value: f.filter_config.filter_value }))
    .sort((a, b) => a.id.localeCompare(b.id));
  const choiceFilter: Filter = { column_id: choice.columnId, property_id: `dataset_column:${choice.columnId}`,
    filter_config: { filter_type: 'array', filter_op: 'contains', filter_value: ['catalog-west'] } };
  const numberFilter: Filter = { column_id: number.columnId, property_id: `dataset_column:${number.columnId}`,
    filter_config: { filter_type: 'number', filter_op: 'greater_than_or_equal', filter_value: 0.3 } };
  const matchesFilters = (outgoing: Request, expected: Filter[]) => isDeepStrictEqual(
    filterSignature(JSON.parse(new URL(outgoing.url()).searchParams.get('filters') ?? '[]')),
    filterSignature(expected));
  const verifyFilteredResponse = async (label: string, response: Response, filters: Filter[], aliases: Alias[]) => {
    const body = await receipt<TableBody>(label, response);
    expect(matchesFilters(response.request(), filters)).toBe(true);
    assertTablePage(body, aliases);
    const params = Object.fromEntries(new URL(response.url()).searchParams);
    const replay = await actor.api.get<TableBody>(tablePath(), params);
    assertTablePage(replay, aliases);
    await assertVisibleRows(aliases);
    await attach(`${label}-replay`, { params, body: replay, expectedIds: orderedIds(aliases) });
  };
  const openProperty = async (family: Family, append = false) => {
    await page.getByRole('button', { name: 'Filter', exact: true }).click();
    // TraceFilterPanel hydrates existing filters without an extra empty row.
    if (append) await page.getByRole('button', { name: 'Add filter', exact: true }).click();
    await page.getByRole('button', { name: 'Property', exact: true }).first().click();
    await page.getByPlaceholder('Search properties...', { exact: true }).fill(family.name);
    await page.locator(`[data-filter-property-option="${family.columnId}"]`).click();
  };
  let choiceValuesReceipt: ValuesBody;
  await test.step('backend check 4 / UI wall 1: typed chips and discovered choice filter', async () => {
    // SavedEvalsList's Run All closes the drawer after both requests resolve.
    await ui(page.getByText('All Evaluations', { exact: true })).not.toBeVisible();
    await assertVisibleRows(['R2', 'R7', 'R3']);
    for (const wanted of ORACLE) {
      await ui(page.locator(`.ag-row[row-id="${ids[wanted.alias]}"] [col-id="${number.columnId}"]`)
        .getByText(String(wanted.number), { exact: true })).toBeVisible();
      await ui(page.locator(`.ag-row[row-id="${ids[wanted.alias]}"] [col-id="${choice.columnId}"]`)
        .getByText(wanted.choice, { exact: true })).toBeVisible();
    }
    const since = Date.now();
    const [valuesResponse] = await Promise.all([
      page.waitForResponse(r => fresh(r, VALUES, 'POST', since) &&
        r.request().postDataJSON().property_id === `dataset_column:${choice.columnId}`, { timeout: UI_READY }),
      openProperty(choice),
    ]);
    choiceValuesReceipt = await receipt<ValuesBody>('native-choice-values', valuesResponse);
    expect(valuesResponse.request().postDataJSON()).toMatchObject({ property_id: `dataset_column:${choice.columnId}`,
      metric_name: choice.columnId, metric_type: 'custom_column', dataset_id: datasetId, source: 'dataset_column', page_size: 50 });
    expect(choiceValuesReceipt.result).toMatchObject({ query_complete: true, query_status: 'complete',
      has_more: false, browse_status: 'exhausted', next_cursor: null });
    expect(choiceValuesReceipt.result.values.map(v => ({ value: v.value, label: v.label }))
      .sort((a, b) => a.value.localeCompare(b.value))).toEqual([
      { value: 'catalog-east', label: 'catalog-east' }, { value: 'catalog-west', label: 'catalog-west' },
    ]);
    await page.getByPlaceholder('Select values...', { exact: true }).click();
    await ui(page.getByRole('option')).toHaveText(['catalog-east', 'catalog-west']);
    const selectedAt = Date.now();
    const [filtered] = await Promise.all([
      page.waitForResponse(r => fresh(r, tablePath(), 'GET', selectedAt) && matchesFilters(r.request(), [choiceFilter]), { timeout: UI_READY }),
      page.getByRole('option', { name: 'catalog-west', exact: true }).click(),
    ]);
    await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
    await verifyFilteredResponse('choice-table', filtered, [choiceFilter], ['R2', 'R3']);
    await testInfo.attach('choice-ui', { contentType: 'image/png', body: await page.screenshot() });
  }, { timeout: UI_READY });

  await test.step('backend check 4 / UI wall 2: inclusive numeric boundary', async () => {
    await page.getByRole('button', { name: 'Filter', exact: true }).click();
    await page.getByRole('button', { name: 'Clear all', exact: true }).click();
    await openProperty(number);
    await page.getByRole('combobox').filter({ hasText: 'equals' }).click();
    await page.getByRole('option', { name: 'greater than or equals', exact: true }).click();
    const since = Date.now();
    const [filtered] = await Promise.all([
      page.waitForResponse(r => fresh(r, tablePath(), 'GET', since) && matchesFilters(r.request(), [numberFilter]), { timeout: UI_READY }),
      page.getByPlaceholder('Value', { exact: true }).fill('0.3'),
    ]);
    await page.keyboard.press('Escape');
    const body = await receipt<TableBody>('number-boundary-table', filtered);
    expect(body.result.table.map(r => r.row_id).sort(), 'EVAL004_NEG_FILTER_BOUNDARY').toEqual(orderedIds(EXPECTED_NUMBER_ROWS));
    await verifyFilteredResponse('number-table', filtered, [numberFilter], ['R7', 'R3']);
  }, { timeout: UI_READY });

  await test.step('backend check 4 / UI wall 3: exact choice and numeric intersection', async () => {
    // Same document/actor/dataset/column/query: native choice cache may reuse
    // the already validated receipt, but the changed table query must be fresh.
    expect(choiceValuesReceipt!.result.values.map(v => v.value).sort()).toEqual(['catalog-east', 'catalog-west']);
    await openProperty(choice, true);
    await page.getByPlaceholder('Select values...', { exact: true }).click();
    await ui(page.getByRole('option')).toHaveText(['catalog-east', 'catalog-west']);
    const since = Date.now();
    const [filtered] = await Promise.all([
      page.waitForResponse(r => fresh(r, tablePath(), 'GET', since) && matchesFilters(r.request(), [numberFilter, choiceFilter]), { timeout: UI_READY }),
      page.getByRole('option', { name: 'catalog-west', exact: true }).click(),
    ]);
    await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
    await verifyFilteredResponse('intersection-table', filtered, [numberFilter, choiceFilter], ['R3']);
    expect(await readPG()).toEqual(finalPG);
  }, { timeout: UI_READY });

  await test.step('backend check 4 / UI wall 4: saved numeric widget and unchanged sources', async () => {
    const metricWire = { source: 'datasets', category: 'custom_column', role: 'metric',
      search: number.name, cursor_mode: true, page_size: 25 };
    const catalog = await actor.api.post<CatalogBody>(METRICS, metricWire);
    await attach('numeric-native-catalog', { request: metricWire, body: catalog });
    expect(catalog.result).toMatchObject({ query_complete: true, query_status: 'complete', has_more: false, next_cursor: null });
    const metricIdentity = (m: Metric) => ({ property_id: m.property_id, name: m.name, display_name: m.display_name, output_type: m.output_type });
    const expectedMetric = { property_id: `dataset_column:${number.columnId}`, name: number.columnId,
      display_name: number.name, output_type: 'float' };
    expect(catalog.result.metrics.map(metricIdentity)).toEqual([expectedMetric]);
    await page.goto('/dashboard/dashboards', { waitUntil: 'domcontentloaded' });
    const createdAt = Date.now();
    const [createdResponse] = await Promise.all([
      page.waitForResponse(r => fresh(r, DASHBOARDS, 'POST', createdAt), { timeout: UI_READY }),
      page.getByRole('button', { name: 'Create Dashboard', exact: true }).click(),
    ]);
    const created = await receipt<{ result: { id: string } }>('dashboard-created', createdResponse);
    const dashboardId = created.result.id;
    await page.getByRole('heading', { name: 'Untitled', exact: true }).click();
    await page.getByPlaceholder('Untitled Dashboard').fill(dashboardName);
    const namedAt = Date.now();
    const [named] = await Promise.all([
      page.waitForResponse(r => ['PATCH', 'PUT'].some(method => fresh(r, `${DASHBOARDS}${dashboardId}/`, method, namedAt)), { timeout: UI_READY }),
      page.getByPlaceholder('Untitled Dashboard').press('Enter'),
    ]);
    await receipt('dashboard-named', named);
    await page.getByRole('button', { name: 'Add Widget', exact: true }).first().click();
    await page.getByText('Untitled widget', { exact: true }).click();
    await page.getByPlaceholder('Untitled widget').fill(widgetName);
    await page.getByPlaceholder('Untitled widget').press('Enter');
    await page.getByRole('combobox').filter({ hasText: 'Line' }).click();
    await page.getByRole('option', { name: 'Metric', exact: true }).click();
    await page.getByText('Select Metric', { exact: true }).click();
    const searchedAt = Date.now();
    const [searched] = await Promise.all([
      page.waitForResponse(r => fresh(r, METRICS, 'POST', searchedAt) && r.request().postDataJSON().search === number.name, { timeout: UI_READY }),
      page.getByPlaceholder('Search metrics...', { exact: true }).fill(number.name),
    ]);
    const search = await receipt<CatalogBody>('numeric-metric-ui-search', searched);
    expect(search.result.metrics.filter(m => m.property_id === expectedMetric.property_id).map(metricIdentity)).toEqual([expectedMetric]);
    // Captured native options include a same-named evaluator and its dataset
    // result column. Select the dataset's typed metric, not either label.
    await page.getByRole('button', { name: `${number.name} (float, Datasets)`, exact: true }).click();
    await page.getByText('Average', { exact: true }).click();
    await page.getByText('Sum', { exact: true }).click();
    await page.getByText('Filter', { exact: true }).click();
    await page.getByLabel(/^Datasets property count: /).locator('..').getByText('Datasets', { exact: true }).click();
    await page.getByPlaceholder('Search filter attributes...').fill('Dataset');
    await page.getByRole('button', { name: 'Dataset (string, Datasets)', exact: true }).click();
    await page.getByText('Select value...', { exact: true }).click();
    await page.getByPlaceholder('Search...', { exact: true }).fill(datasetName);
    await page.locator(`p[title="${datasetName}"]`).click();
    const metricMatch = (q: QueryConfig) => q.metrics.length === 1 && q.metrics[0].id === number.columnId &&
      q.metrics[0].aggregation === 'sum' && q.filters.length === 1 && q.filters[0].column_id === 'dataset' &&
      isDeepStrictEqual(q.filters[0].filter_config.filter_value, [datasetName]);
    const previewAt = Date.now();
    const [previewResponse] = await Promise.all([
      page.waitForResponse(r => fresh(r, QUERY, 'POST', previewAt) && metricMatch(r.request().postDataJSON()), { timeout: UI_READY }),
      page.getByRole('button', { name: 'Add', exact: true }).click(),
    ]);
    const preview = await receipt<QueryBody>('numeric-widget-preview', previewResponse);
    const previewConfig = previewResponse.request().postDataJSON() as QueryConfig;
    expect(previewConfig.metrics).toHaveLength(1);
    expect(previewConfig.metrics[0]).toMatchObject({ id: number.columnId, column_id: number.columnId,
      property_id: `dataset_column:${number.columnId}`, source: 'datasets', type: 'custom_column', aggregation: 'sum' });
    expect(previewConfig.filters[0]).toMatchObject({ column_id: 'dataset', source: 'datasets',
      filter_config: { filter_op: 'in', filter_value: [datasetName], col_type: 'SYSTEM_METRIC' } });
    const assertSum = (body: QueryBody) => {
      expect(body.result).toMatchObject({ query_complete: true, query_exact: true });
      expect(body.result.metrics).toHaveLength(1);
      expect(body.result.metrics[0].id).toBe(number.columnId);
      expect(body.result.metrics[0].series).toHaveLength(1);
      const points = body.result.metrics[0].series[0].data;
      expect(points.length).toBeGreaterThan(0);
      for (const p of points) if (p.value !== null) expect(Number.isFinite(p.value)).toBe(true);
      expect(Number(points.reduce((sum, p) => sum + (p.value ?? 0), 0).toFixed(12))).toBe(EXPECTED_SUM);
    };
    assertSum(preview);
    await ui(page.getByRole('heading', { level: 2, name: '1.20', exact: true })).toBeVisible();
    const savedAt = Date.now();
    const [savedResponse] = await Promise.all([
      page.waitForResponse(r => fresh(r, `${DASHBOARDS}${dashboardId}/widgets/`, 'POST', savedAt), { timeout: UI_READY }),
      page.getByRole('button', { name: 'Save', exact: true }).click(),
    ]);
    const saved = await receipt<{ result: Widget }>('saved-widget', savedResponse);
    const saveWire = savedResponse.request().postDataJSON() as Omit<Widget, 'id'>;
    expect(saveWire.query_config).toEqual(previewConfig);
    expect(saved.result).toMatchObject({ name: widgetName, query_config: saveWire.query_config, chart_config: saveWire.chart_config });
    const dashboard = await probe.pg('SELECT id,name,workspace_id FROM tracer_dashboard WHERE id=$1 AND NOT deleted', [dashboardId]);
    expect(dashboard).toEqual([{ id: dashboardId, name: dashboardName, workspace_id: actor.workspaceId }]);
    const stored = await probe.pg('SELECT id,dashboard_id,name,query_config,chart_config FROM tracer_dashboardwidget WHERE id=$1 AND NOT deleted', [saved.result.id]);
    expect(stored).toEqual([{ id: saved.result.id, dashboard_id: dashboardId, name: widgetName,
      query_config: saveWire.query_config, chart_config: saveWire.chart_config }]);
    const detail = await actor.api.get<{ result: { id: string; name: string; workspace: string; widgets: Widget[] } }>(`${DASHBOARDS}${dashboardId}/`);
    expect(detail.result).toMatchObject({ id: dashboardId, name: dashboardName, workspace: actor.workspaceId });
    expect(detail.result.widgets.map(w => ({ id: w.id, name: w.name, query_config: w.query_config, chart_config: w.chart_config })))
      .toEqual([{ id: saved.result.id, name: widgetName, query_config: saveWire.query_config, chart_config: saveWire.chart_config }]);
    await attach('saved-widget-storage-and-detail', { dashboard, stored, detail });
    await ui(page).toHaveURL(new RegExp(`/dashboard/dashboards/${dashboardId}$`));
    await page.reload({ waitUntil: 'domcontentloaded' });
    const tile = page.locator(`[data-widget-id="${saved.result.id}"]`);
    await ui(tile.getByText('1.20', { exact: true })).toBeVisible();
    const reopenedAt = Date.now();
    const [reopened] = await Promise.all([
      page.waitForResponse(r => fresh(r, QUERY, 'POST', reopenedAt) &&
        isDeepStrictEqual(r.request().postDataJSON(), saveWire.query_config), { timeout: UI_READY }),
      tile.getByText(widgetName, { exact: true }).click(),
    ]);
    assertSum(await receipt<QueryBody>('reopened-widget-query', reopened));
    await ui(page.getByRole('heading', { level: 2, name: '1.20', exact: true })).toBeVisible();
    await ui(page.getByText(number.name, { exact: true }).first()).toBeVisible();
    await ui(page.getByText('Sum', { exact: true })).toBeVisible();
    await ui(page.getByText(datasetName, { exact: true })).toBeVisible();
    expect(await readPG()).toEqual(finalPG);
    expect(await readCH()).toEqual(finalCH);
    await testInfo.attach('reopened-widget-ui', { contentType: 'image/png', body: await page.screenshot() });
  }, { timeout: UI_READY });
});
