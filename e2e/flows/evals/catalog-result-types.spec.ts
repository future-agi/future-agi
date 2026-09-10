import { request, type Request, type Response } from '@playwright/test';
import { test, expect } from '../../lib/mock-model-fixtures';
import { sendTrace, type SeededTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Requires the separately approved LOCAL background mock and fresh pre-dispatch
// attestation, not merely collection/typecheck. Main owns managed execution and
// secondary-workflow outcome/drain evidence; no skip or softened result matrix.
// Approved external plan: observed-catalog-eval3-plan-20260909.md (core directory).
test.use({ evalBackground: true });

// model_hub/serializers/contracts.py: EvalTemplateCreateV2/UpdateRequestSerializer;
// tracer/serializers/custom_eval_config.py and eval_task.py: public setup writes.
const TEMPLATES = '/model-hub/eval-templates/';
const CONFIGS = '/tracer/custom-eval-config/';
const TASKS = '/tracer/eval-task/';
// TraceGrid/SpanGrid, trace-detail.js, PrimaryGraph and dashboard.py native reads.
const LIST = '/tracer/trace/list_traces_of_session/';
const SPAN_LIST = '/tracer/observation-span/list_spans_observe/';
const GRAPH = '/tracer/trace/get_graph_methods/';
const METRICS = '/tracer/dashboard/metrics/';
const VALUES = '/tracer/dashboard/filter_values/';
const UI_READY = 60_000; // README: first-paint budget; step ceilings bound chaining.
const MOCK_USAGE = { prompt_tokens: 7, completion_tokens: 7, total_tokens: 14 };
// stack/mock-llm/server.mjs: echo of the last user message, fixed token usage.

type Created = { result: { id: string } };
type FamilyKey = 'pf' | 'score' | 'scored_choice' | 'choice' | 'multi';
type TypedValue = {
  output_bool: boolean | null; output_float: number | null;
  output_str: null | { score: number; choice?: string; choices?: string[] };
  output_str_list: string[];
};
type Family = {
  key: FamilyKey; name: string; attribute: string;
  wireType: 'pass_fail' | 'percentage' | 'deterministic';
  catalogType: 'PASS_FAIL' | 'SCORE' | 'CHOICES';
  choiceScores: Record<string, number> | null; choices: string[];
  // Inputs and expected outputs are separate literals: negative proofs must
  // perturb only an expected field, never a value also interpolated into input.
  answers: [string, string]; invalidAnswers: [string, string];
  expected: [TypedValue, TypedValue]; labels: [string[], string[]];
  templateId: string; configId: string;
};
type Fact = {
  id: string; custom_eval_config_id: string; eval_task_id: string;
  trace_id: string; observation_span_id: string; trace_session_id: null;
  target_type: string; status: string; error: boolean; error_message: string | null;
  skipped_reason: string | null; config_hash: string | null; deleted: boolean;
  output_bool: boolean | null; output_float: number | null; output_str: string | null;
  output_str_list: string[]; eval_explanation: string | null;
  output_metadata: { usage?: typeof MOCK_USAGE } | null; created_at: string;
};
type CHFact = Omit<Fact, 'output_str_list' | 'output_metadata'> & {
  output_str_list: string; output_metadata: string | null;
};
type Filter = { column_id: string; property_id: string; display_name: string; filter_config: {
  col_type: string; filter_type: string; filter_op: string; filter_value: unknown;
} };
type ListBody = { result: { table: { trace_id: string }[]; metadata: {
  has_more: boolean; next_cursor: string | null; query_complete: boolean;
  query_status: string; query_error_code: string | null; total_rows_is_lower_bound: boolean;
} } };
// observation_span.py:SpanObserveListResponseSerializer / _list_spans_clickhouse.
type SpanListBody = { status: boolean; result: {
  table: { project_id: string; trace_id: string; span_id: string; span_name: string }[];
  metadata: { total_rows: number } & Partial<ListBody['result']['metadata']>; config: unknown[];
} };
type NativeRead = { startedAt: number; origin: string; path: string; method: string;
  params: ReturnType<typeof listParams> | null; paramsUnreadable: boolean;
  scope: { organizationId: string | null; workspaceId: string | null; authorized: boolean };
  state: string; failedAt?: number; errorText?: string | null; receivedAt?: number;
  status?: number; requestId?: string | null; contentType?: string };
type Metric = { property_id: string; name: string; display_name: string; output_type: string };
type CatalogBody = { result: { metrics: Metric[]; has_more: boolean;
  next_cursor: string | null; query_complete: boolean; query_status: string } };
type ValuesBody = { result: { values: { value: string; label: string }[];
  has_more: boolean; next_cursor: string | null; query_complete: boolean; query_status: string } };
type EvalDetail = {
  eval_config_id: string; eval_name: string; output_type: string; score: number | null;
  score_label: string | null; score_items: string[] | null; result: unknown;
  explanation: string | null; status: string; error: boolean; skipped: boolean;
};
type SpanEntry = { observation_span: { id: string }; eval_scores: EvalDetail[]; children: SpanEntry[] };
type DetailBody = { result: { trace: { id: string }; observation_spans: SpanEntry[] } };
type GraphBody = { result: { query_complete: boolean; query_status: string;
  query_sampled: boolean; query_exact: boolean; query_provenance: string;
  data: { timestamp: string; value: number | null; primary_traffic: number | null }[] } };
type Selection = { label: string; op: string; value: number | number[] | string[]; indexes: number[] };

// Domain parsing only, not a fixture/probe extension. TraceGrid serializes its
// filters even when readQuery changes GET to POST; PrimaryGraph sends an array.
function listParams(outgoing: Request): Record<string, string | number> {
  return outgoing.method() === 'POST' ? outgoing.postDataJSON()
    : Object.fromEntries(new URL(outgoing.url()).searchParams);
}
function requestFilters(outgoing: Request): Filter[] {
  return new URL(outgoing.url()).pathname === GRAPH ? outgoing.postDataJSON().filters
    : JSON.parse(String(listParams(outgoing).filters));
}
// StateProbe.ch accepts scalar bindings only. Expand the small fixture ID set
// into separately bound values; never interpolate the UUID/span values as SQL.
function idBindings(ids: string[], type: 'String' | 'UUID') {
  expect(ids.length).toBeGreaterThan(0);
  return { sql: ids.map((_, i) => `{id${i}:${type}}`).join(', '),
    params: Object.fromEntries(ids.map((id, i) => [`id${i}`, id])) };
}
function selectedEntry(entries: SpanEntry[], id: string): SpanEntry | undefined {
  for (const entry of entries) {
    if (entry.observation_span.id === id) return entry;
    const nested = selectedEntry(entry.children, id);
    if (nested) return nested;
  }
}
// BaseQueryBuilder.time_bucket_expr: actual graph buckets use logger.created_at,
// NOT the input span's time. UTC preserves a run that crosses a bucket boundary.
function bucketAt(created: string, interval: string): string {
  const d = new Date(created);
  d.setUTCSeconds(0, 0);
  if (interval !== 'minute') d.setUTCMinutes(0);
  if (['day', 'week', 'month', 'year'].includes(interval)) d.setUTCHours(0);
  if (interval === 'week') d.setUTCDate(d.getUTCDate() - (d.getUTCDay() + 6) % 7);
  if (interval === 'month' || interval === 'year') d.setUTCDate(1);
  if (interval === 'year') d.setUTCMonth(0);
  expect(['minute', 'hour', 'day', 'week', 'month', 'year']).toContain(interval);
  return d.toISOString();
}

// BaseQueryBuilder.format_time_series/_normalize_timestamp emit UTC bucket
// strings without tzinfo. Do not let the runner's local timezone shift them.
function graphTimestampUTC(timestamp: string): string {
  const zoned = /(?:Z|[+-]\d{2}:\d{2})$/.test(timestamp);
  return new Date(zoned ? timestamp : `${timestamp}Z`).toISOString();
}

test('EVAL-E2E-003: typed executed evaluations retain exact native results and reject invalid output', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-003', area: 'evals',
    userGoal: 'Read typed evaluations on real spans, find exactly their matching traces and reject invalid judge output visibly',
    steps: ['ingest distinct mapped inputs for five mock-backed evaluation families',
      'enable multiple choices through Save Version and bind all five evaluations',
      'run a historical task on exactly the two LLM children and inspect their stored results',
      'inspect each span evaluation and select positive, multiple and zero-match native filters',
      'compare both numeric score graphs with the exact matching result identities',
      'execute malformed and semantic-invalid inputs and require visible errors without scored matches'],
    backendChecks: [
      'the task and exact source/config pairs belong to the actor scope and finish successfully',
      'every typed result, explanation and mock usage reaches latest ClickHouse with its exact Postgres identity',
      'native discovery, detail, filtered rows and numeric graph equal the independent outcome matrix',
      'invalid judge output is visibly errored and cannot masquerade as a successful typed result',
    ],
  }),
}, async ({ page, actor, probe, mockModel }, testInfo) => {
  test.setTimeout(2_520_000);
  // Approved ceiling: source 30 + valid task 90 + CDC 180 + UI edit 60 +
  // 5*(detail + 4 filters)*60 + graph selection 120 + invalid tasks 180 +
  // invalid CDC 180 + invalid UI 120 + setup/source-invalid headroom 60.
  page.setDefaultTimeout(UI_READY);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const prefix = `e2e-eval3-${suffix}`;
  const verdict = `e2e-typed-verdict-${suffix}`;
  const alpha = `Alpha-${suffix}`, beta = `Beta-${suffix}`, gamma = `Gamma-${suffix}`;
  const low = `Low-${suffix}`, high = `High-${suffix}`, missing = `Unknown-${suffix}`;
  const empty: TypedValue = { output_bool: null, output_float: null, output_str: null, output_str_list: [] };
  const families: Family[] = [
    { key: 'pf', wireType: 'pass_fail', catalogType: 'PASS_FAIL', choiceScores: null,
      choices: ['Passed', 'Failed'], answers: ['"Pass"', '"Fail"'],
      invalidAnswers: ['"Maybe"', '"Maybe"'],
      expected: [{ ...empty, output_bool: true }, { ...empty, output_bool: false }], labels: [['Pass'], ['Fail']] },
    { key: 'score', wireType: 'percentage', catalogType: 'SCORE', choiceScores: null,
      choices: [], answers: ['0.2', '0.8'], invalidAnswers: ['"not-a-number"', '"not-a-number"'],
      expected: [{ ...empty, output_float: 0.2 }, { ...empty, output_float: 0.8 }], labels: [['20%'], ['80%']] },
    { key: 'scored_choice', wireType: 'percentage', catalogType: 'SCORE', choiceScores: { [low]: 0.2, [high]: 0.8 },
      choices: [], answers: [JSON.stringify(low), JSON.stringify(high)],
      invalidAnswers: [JSON.stringify(missing), JSON.stringify(missing)],
      expected: [{ ...empty, output_float: 0.2, output_str: { score: 0.2, choice: low } },
        { ...empty, output_float: 0.8, output_str: { score: 0.8, choice: high } }], labels: [['20%'], ['80%']] },
    { key: 'choice', wireType: 'deterministic', catalogType: 'CHOICES',
      choiceScores: { [alpha]: 1, [beta]: 0.5, [gamma]: 0 }, choices: [alpha, beta, gamma],
      answers: [JSON.stringify(alpha), JSON.stringify(gamma)],
      invalidAnswers: [JSON.stringify(missing), JSON.stringify(missing)],
      expected: [{ ...empty, output_str: { score: 1, choice: alpha }, output_str_list: [alpha] },
        { ...empty, output_str: { score: 0, choice: gamma }, output_str_list: [gamma] }], labels: [[alpha], [gamma]] },
    { key: 'multi', wireType: 'deterministic', catalogType: 'CHOICES',
      choiceScores: { [alpha]: 1, [beta]: 0.5, [gamma]: 0 }, choices: [alpha, beta, gamma],
      answers: [JSON.stringify([alpha, beta]), JSON.stringify([gamma])],
      // Preserve BOTH unknown-member and empty-list semantic gates. Other
      // families repeat their invalid witness on these two real child inputs.
      invalidAnswers: [JSON.stringify([alpha, missing]), '[]'],
      expected: [{ ...empty, output_str: { score: 0.75, choices: [alpha, beta] }, output_str_list: [alpha, beta] },
        { ...empty, output_str: { score: 0, choices: [gamma] }, output_str_list: [gamma] }], labels: [[alpha, beta], [gamma]] },
  ].map(f => ({ ...f, name: `${prefix}-${f.key}-judge`, attribute: `eval3_answer_${f.key}`,
    templateId: '', configId: '' } as Family));
  let projectId = '';
  const allTaskIds: string[] = [];
  const seeds: SeededTrace[] = [];
  const markers = [`marker-A-${suffix}`, `marker-B-${suffix}`];
  const rootNames = [`${prefix}-A-root`, `${prefix}-B-root`];
  const childNames = [`${prefix}-A-child`, `${prefix}-B-child`];
  let validFacts: Fact[] = [];
  const attach = (name: string, value: unknown) => testInfo.attach(name, {
    contentType: 'application/json', body: JSON.stringify(value),
  });
  // models/observation_span.py + oss_cdc_source.py: PG bool -> CH Bool,
  // nullable scalar -> Nullable. JSON fields travel as String. Never infer NULL
  // from false/zero/empty values left by a nonnullable retained mirror.
  // nullable bool/float/string MUST remain null, not zero/empty substitutions.
  const columns = `id, custom_eval_config_id, eval_task_id, trace_id, observation_span_id,
    trace_session_id, target_type, status, error, error_message, skipped_reason,
    config_hash, deleted, output_bool, output_float, output_str, output_str_list,
    eval_explanation, output_metadata`;
  const readPG = (taskIds: string[]) => probe.pg<Fact>(`SELECT ${columns},
    to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS created_at
    FROM tracer_eval_logger WHERE eval_task_id = ANY($1) ORDER BY id::text`, [taskIds]);
  const readCH = async (taskIds: string[]) => {
    const bind = idBindings(taskIds, 'String');
    return (await probe.ch<CHFact>(`SELECT ${columns},
    formatDateTime(created_at, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS created_at
    FROM tracer_eval_logger FINAL WHERE eval_task_id IN (${bind.sql}) ORDER BY toString(id)`,
    bind.params)).map(row => {
    expect(typeof row.error).toBe('boolean');
    expect(typeof row.deleted).toBe('boolean');
    expect([null, false, true]).toContain(row.output_bool);
    return { ...row,
      output_str_list: JSON.parse(row.output_str_list),
      output_metadata: row.output_metadata === null ? null : JSON.parse(row.output_metadata),
      created_at: new Date(row.created_at).toISOString() } as Fact;
    });
  };
  const complete = (body: ListBody, soft = false) => (soft ? expect.soft : expect)(body.result.metadata).toMatchObject({
    has_more: false, next_cursor: null, query_complete: true, query_status: 'complete',
    query_error_code: null, total_rows_is_lower_bound: false,
  });
  const traceURL = () => `/dashboard/observe/${projectId}/llm-tracing?selectedTab=trace`;
  const spanURL = () => `/dashboard/observe/${projectId}/llm-tracing?selectedTab=spans`;
  const traceCells = page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]');
  const instructions = `Reply with exactly this JSON: {"result": {{answer}}, "explanation": "${verdict} saw {{marker}}"}`;
  const createConfig = async (templateId: string, name: string, mapping: Record<string, string>) =>
    (await actor.api.post<Created>(CONFIGS, { project: projectId, eval_template: templateId,
      name, model: mockModel.model, mapping, config: { mapping }, error_localizer: false })).result.id;

  const createTask = async (label: string, configs: string[], targets: SeededTrace[]) => {
    // PRE-DISPATCH VETO includes the opted-in background routes/source/pollers.
    await mockModel.assertReady();
    const now = Date.now();
    const body = { name: `${prefix}-${label}`, project: projectId, evals: configs,
      filters: { project_id: projectId,
        date_range: [new Date(now - 3_600_000).toISOString(), new Date(now + 3_600_000).toISOString()],
        span_id: targets.map(t => t.spanIds[1]) },
      run_type: 'historical', row_type: 'spans', spans_limit: targets.length, sampling_rate: 100 };
    const { result: { id } } = await actor.api.post<Created>(TASKS, body);
    allTaskIds.push(id);
    await attach(`task-${label}`, { id, request: body });
    await expect.poll(async () => (await actor.api.get<{ status: string }>(`${TASKS}${id}/`)).status,
      POLL.EVAL_RESULT).toBe('completed');
    return id;
  };

  // Read-only evidence of the IMPLICIT background branch, never trigger it here.
  // Eligibility / deterministic workflow name is NOT a dispatch/completion receipt.
  const clusteringEvidence = async (phase: string) => {
    if (!projectId || !allTaskIds.length) return;
    const facts = await readPG(allTaskIds);
    const eligible = facts.filter(r => r.status === 'completed' && r.eval_explanation &&
      (r.output_bool === false || (r.output_float !== null && r.output_float < 1)));
    const memberships = await probe.pg(`SELECT m.id, m.eval_logger_id, m.trace_id, m.span_id,
      m.cluster_id, g.project_id, g.cluster_id AS cluster_key
      FROM tracer_error_cluster_traces m JOIN tracer_trace_error_group g ON g.id=m.cluster_id
      WHERE g.project_id=$1 AND m.eval_logger_id = ANY($2::uuid[]) ORDER BY m.id::text`,
      [projectId, eligible.map(r => r.id)]);
    // drop_in/runner.py prefixes the dispatch task_id with "task-".
    await attach(`clustering-${phase}`, { projectId, expectedWorkflowId: `task-eval-cluster-${projectId}`,
      eligibleLoggerIds: eligible.map(r => r.id), memberships,
      qualification: 'Observation only. No Temporal probe: dispatch, drain completion and provider routing require main evidence.',
      safetyReview: 'Opted-in background routing is freshly attested; main must still capture actual secondary workflow/run/drain outcome.' });
  };

  try {
    await attach('authoring-and-execution-boundary', { authoringApproved: true, runtimeSafetyGate: 'evalBackground opt-in: awaited before provisioning and every dispatch',
      specProof: 'No managed/negative/restored proof was performed during authoring',
      plannedFamilies: families.map(({ templateId, configId, ...f }) => f) });
    for (const i of [0, 1]) {
      seeds.push(await sendTrace(req, { collectorUrl: E2E.collectorUrl,
        apiKey: actor.apiKey, secretKey: actor.secretKey, projectName: prefix,
        rootName: rootNames[i], childName: childNames[i],
        childAttributes: { ...Object.fromEntries(families.map(f => [f.attribute, f.answers[i]])), eval3_marker: markers[i] } }));
      await attach(`source-${i}`, { seed: seeds[i], rootName: rootNames[i], childName: childNames[i],
        marker: markers[i], organizationId: actor.organizationId, workspaceId: actor.workspaceId });
    }
    const sourceStrings = idBindings(seeds.map(s => s.traceId), 'String');
    const sourceUUIDs = idBindings(seeds.map(s => s.traceId), 'UUID');
    await expect.poll(async () => (await probe.ch<{ id: string }>(
      `SELECT id FROM spans FINAL WHERE trace_id IN (${sourceStrings.sql}) ORDER BY id`,
      sourceStrings.params)).map(r => r.id), POLL.SPAN_VISIBLE)
      .toEqual(seeds.flatMap(s => s.spanIds).sort());
    await expect.poll(async () => (await probe.ch<{ id: string }>(
      `SELECT id FROM traces FINAL WHERE id IN (${sourceUUIDs.sql}) ORDER BY toString(id)`,
      sourceUUIDs.params)).map(r => r.id), POLL.SPAN_VISIBLE)
      .toEqual(seeds.map(s => s.traceId).sort());
    const projects = await probe.pg<{ id: string; organization_id: string; workspace_id: string }>(
      'SELECT id, organization_id, workspace_id FROM tracer_project WHERE name=$1', [prefix]);
    expect(projects).toHaveLength(1);
    expect(projects[0]).toMatchObject({ organization_id: actor.organizationId, workspace_id: actor.workspaceId });
    projectId = projects[0].id;
    // 002_spans_v2.sql: the collector stores these OTLP string inputs in attrs_string.
    for (let i = 0; i < seeds.length; i++) {
      const sources = await probe.ch<{ id: string; project_id: string; trace_id: string; attributes: string }>(
        'SELECT id, project_id, trace_id, toJSONString(attrs_string) AS attributes FROM spans FINAL WHERE trace_id={id:String} ORDER BY id',
        { id: seeds[i].traceId });
      expect(sources.map(r => r.id).sort()).toEqual([...seeds[i].spanIds].sort());
      for (const row of sources) expect(row).toMatchObject({ project_id: projectId, trace_id: seeds[i].traceId });
      const attrs = JSON.parse(sources.find(r => r.id === seeds[i].spanIds[1])!.attributes);
      expect(attrs).toMatchObject({ ...Object.fromEntries(families.map(f => [f.attribute, f.answers[i]])), eval3_marker: markers[i] });
      const root = JSON.parse(sources.find(r => r.id === seeds[i].spanIds[0])!.attributes);
      for (const family of families) expect(root).not.toHaveProperty(family.attribute);
    }

    for (const f of families) {
      await mockModel.assertReady();
      f.templateId = (await actor.api.post<Created>(`${TEMPLATES}create-v2/`, {
        name: f.name, eval_type: 'llm', instructions, model: mockModel.model,
        output_type: f.wireType, pass_threshold: 0.5, choice_scores: f.choiceScores,
        check_internet: false, error_localizer_enabled: false,
        // No multi_choice: create-v2 rejects that field. Only the real UI edit below sets it.
      })).result.id;
      await attach(`template-${f.key}`, { templateId: f.templateId, name: f.name });
      if (f.key === 'multi') {
        await test.step('UI: enable multiple choices with the existing Save Version action', async () => {
          await page.goto(`/dashboard/evaluations/${f.templateId}`, { waitUntil: 'domcontentloaded' });
          await page.getByRole('checkbox', { name: 'Allow multiple choices (LLM can select more than one)', exact: true }).check();
          const update = page.waitForResponse(r => new URL(r.url()).pathname === `${TEMPLATES}${f.templateId}/update/`
            && r.request().method() === 'PUT', { timeout: UI_READY });
          const version = page.waitForResponse(r => new URL(r.url()).pathname === `${TEMPLATES}${f.templateId}/versions/create/`
            && r.request().method() === 'POST', { timeout: UI_READY });
          await mockModel.assertReady();
          await page.getByRole('button', { name: 'Save Version', exact: true }).click();
          const [updated, saved] = await Promise.all([update, version]);
          expect(updated.status()).toBe(200); expect(saved.status()).toBe(200);
          const wire = updated.request().postDataJSON();
          // PromptEditor.getBlocks preserves Quill's terminal newline on native Save Version.
          expect(wire).toMatchObject({ instructions: `${instructions}\n`, model: mockModel.model, output_type: 'deterministic',
            choice_scores: f.choiceScores, pass_threshold: 0.5, multi_choice: true,
            check_internet: false, error_localizer_enabled: false });
          const body = await saved.json() as Created;
          const versions = await probe.pg<{ id: string; eval_template_id: string; config_snapshot: Record<string, unknown> }>(
            'SELECT id, eval_template_id, config_snapshot FROM model_hub_eval_template_version WHERE id=$1', [body.result.id]);
          expect(versions).toHaveLength(1);
          expect(versions[0]).toMatchObject({ eval_template_id: f.templateId,
            config_snapshot: { multi_choice: true, choice_scores: f.choiceScores, model: mockModel.model } });
          await attach('multi-native-version', { update: wire, versionRequest: saved.request().postDataJSON(), body, versions });
        }, { timeout: UI_READY });
      }
      f.configId = await createConfig(f.templateId, f.name, { answer: f.attribute, marker: 'eval3_marker' });
      await attach(`binding-${f.key}`, { templateId: f.templateId, configId: f.configId, projectId,
        modelId: mockModel.id, model: mockModel.model });
    }

    await test.step('backend check 1: exact definition, task and source/config scope', async () => {
      for (const f of families) {
        const defs = await probe.pg<{ id: string; organization_id: string; workspace_id: string; model: string;
          multi_choice: boolean; choice_scores: Record<string, number> | null; config: Record<string, unknown> }>(
          'SELECT id, organization_id, workspace_id, model, multi_choice, choice_scores, config FROM model_hub_evaltemplate WHERE id=$1', [f.templateId]);
        expect(defs).toHaveLength(1);
        expect(defs[0]).toMatchObject({ organization_id: actor.organizationId, workspace_id: actor.workspaceId,
          model: mockModel.model, multi_choice: f.key === 'multi', choice_scores: f.choiceScores,
          config: { eval_type_id: 'CustomPromptEvaluator', rule_prompt: f.key === 'multi' ? `${instructions}\n` : instructions, check_internet: false,
            error_localizer_enabled: false } });
        const configs = await probe.pg<{ id: string; project_id: string; eval_template_id: string; model: string;
          mapping: Record<string, string>; config: { mapping: Record<string, string> }; error_localizer: boolean }>(
          'SELECT id, project_id, eval_template_id, model, mapping, config, error_localizer FROM tracer_custom_eval_config WHERE id=$1', [f.configId]);
        expect(configs).toHaveLength(1);
        expect(configs[0]).toMatchObject({ id: f.configId, project_id: projectId, eval_template_id: f.templateId,
          model: mockModel.model, mapping: { answer: f.attribute, marker: 'eval3_marker' },
          config: { mapping: { answer: f.attribute, marker: 'eval3_marker' } }, error_localizer: false });
      }
      const id = await createTask('valid', families.map(f => f.configId), seeds);
      const task = await probe.pg<{ id: string; project_id: string; status: string; row_type: string }>(
        'SELECT id, project_id, status, row_type FROM tracer_eval_task WHERE id=$1', [id]);
      expect(task).toEqual([{ id, project_id: projectId, status: 'completed', row_type: 'spans' }]);
      validFacts = await readPG([id]);
      await attach('valid-postgres', validFacts);
      expect(validFacts.map(r => `${r.custom_eval_config_id}:${r.observation_span_id}`).sort())
        .toEqual(families.flatMap(f => seeds.map(s => `${f.configId}:${s.spanIds[1]}`)).sort());
      for (const row of validFacts) {
        const source = seeds.find(s => s.spanIds[1] === row.observation_span_id)!;
        expect(row).toMatchObject({ eval_task_id: id, trace_id: source.traceId,
          trace_session_id: null, target_type: 'span', status: 'completed', error: false,
          skipped_reason: null, deleted: false });
        expect(row.config_hash).toMatch(/^[a-f0-9]{64}$/);
      }
    });

    await test.step('backend check 2: typed values, mapped explanations, mock usage and exact CDC identities', async () => {
      for (const f of families) for (const i of [0, 1]) {
        const row = validFacts.find(r => r.custom_eval_config_id === f.configId && r.observation_span_id === seeds[i].spanIds[1])!;
        expect({ output_bool: row.output_bool, output_float: row.output_float,
          output_str: row.output_str === null ? null : JSON.parse(row.output_str), output_str_list: row.output_str_list })
          .toEqual(f.expected[i]);
        expect(row.eval_explanation).toBe(`${verdict} saw ${markers[i]}`);
        expect(row.output_metadata?.usage).toEqual(MOCK_USAGE);
      }
      await expect.poll(() => readCH([allTaskIds[0]]), POLL.CDC_VISIBLE).toEqual(validFacts);
      await attach('valid-cdc', await readCH([allTaskIds[0]]));
      // run_entry.py deliberately dispatches for BOTH .2 and .8 and the false
      // verdict. Assert the five candidates survive; do not edit them to 1.
      const eligiblePairs = validFacts.filter(r => r.output_bool === false || (r.output_float !== null && r.output_float < 1))
        .map(r => `${r.custom_eval_config_id}:${r.observation_span_id}`).sort();
      expect(eligiblePairs).toEqual([
        `${families[0].configId}:${seeds[1].spanIds[1]}`,
        ...families.filter(f => f.catalogType === 'SCORE').flatMap(f => seeds.map(s => `${f.configId}:${s.spanIds[1]}`)),
      ].sort());
      await clusteringEvidence('after-valid');
    });

    let currentDetail: { spanId: string; body: DetailBody } | undefined;
    let detailNavigation = 0;
    const inspectDetail = async (f: Family, seed: SeededTrace, childName: string, expected: TypedValue | null,
      explanation: string | null, soft = false, reuseOpenDetail = false) => {
      const check = soft ? expect.soft : expect;
      if (!reuseOpenDetail) {
        currentDetail = undefined;
        const label = `native-span-${f.configId}-${seed.spanIds[1]}-${++detailNavigation}`;
        const target = { family: f.key, configId: f.configId, projectId, traceId: seed.traceId, spanId: seed.spanIds[1] };
        const detailPath = `/tracer/trace/${seed.traceId}/`;
        const reads = new Map<Request, NativeRead>();
        let active = true, stage = SPAN_LIST, actionAt = Date.now();
        // E4 receipt pattern, scoped to this navigation; never retain credentials.
        const onRequest = (outgoing: Request) => {
          const url = new URL(outgoing.url()), method = outgoing.method();
          if (url.origin !== new URL(E2E.apiUrl).origin ||
            !(url.pathname === SPAN_LIST && ['GET', 'POST'].includes(method) || url.pathname === detailPath && method === 'GET')) return;
          let params: NativeRead['params'] = null, paramsUnreadable = false;
          if (url.pathname === SPAN_LIST) {
            try {
              params = Object.fromEntries(Object.entries(listParams(outgoing)).filter(([key]) =>
                ['project_id', 'page_number', 'page_size', 'filters', 'cursor', 'cursor_mode', 'allow_sampled'].includes(key)));
            } catch { paramsUnreadable = true; }
          }
          const headers = outgoing.headers();
          reads.set(outgoing, { startedAt: Date.now(), origin: url.origin, path: url.pathname, method,
            params, paramsUnreadable, state: 'request_started_no_response', scope: {
              organizationId: headers['x-organization-id'] ?? null, workspaceId: headers['x-workspace-id'] ?? null,
              authorized: /^Bearer \S+$/.test(headers.authorization ?? ''),
            } });
        };
        const onFailed = (outgoing: Request) => {
          const read = reads.get(outgoing);
          if (read) Object.assign(read, { state: 'request_failed', failedAt: Date.now(), errorText: outgoing.failure()?.errorText ?? null });
        };
        const matches = (response: Response, path: string, since: number) => {
          const read = reads.get(response.request());
          return active && read !== undefined && read.startedAt >= since && read.path === path &&
            (path !== SPAN_LIST || (read.params?.project_id === projectId &&
              Number(read.params.page_number ?? 0) === 0 && !read.params.cursor));
        };
        const capture = async (response: Response, part: string) => {
          const read = reads.get(response.request())!;
          if (!active) throw new Error('Span navigation already ended');
          Object.assign(read, { state: 'response_received_body_pending', receivedAt: Date.now(), status: response.status(),
            requestId: response.headers()['x-request-id'] ?? null, contentType: response.headers()['content-type'] ?? '' });
          await attach(`${label}-${part}-head`, { target, actionAt, ...read });
          let body: unknown;
          try {
            if (!/\bapplication\/(?:[\w.-]+\+)?json\b/i.test(read.contentType!)) {
              read.state = 'non_json_body_omitted';
              throw new Error(`${part}: HTTP ${read.status}, non-JSON body omitted; requestId=${read.requestId}`);
            }
            body = await response.json();
          } catch (error) {
            if (read.state !== 'non_json_body_omitted') read.state = 'json_body_unreadable';
            await attach(`${label}-${part}-body-unavailable`, { target, actionAt, ...read });
            throw error;
          }
          if (!active) throw new Error('Span navigation ended during body read');
          read.state = 'json_body_received';
          await attach(`${label}-${part}-body`, { target, actionAt, ...read, body });
          // Infrastructure errors are hard failures even for invalid judge results.
          expect(read.status, `${part} HTTP status; requestId=${read.requestId}`).toBe(200);
          expect(read.scope).toEqual({ organizationId: actor.organizationId, workspaceId: actor.workspaceId, authorized: true });
          return body;
        };
        page.on('request', onRequest);
        page.on('requestfailed', onFailed);
        try {
          // Both branches have rejection consumers; a 503 is captured before a row wait.
          const [list] = await Promise.all([
            page.waitForResponse(r => matches(r, SPAN_LIST, actionAt), { timeout: UI_READY })
              .then(r => capture(r, 'list')),
            page.goto(spanURL(), { waitUntil: 'domcontentloaded' }),
          ]);
          const body = list as SpanListBody;
          expect(body.status).toBe(true);
          expect(Array.isArray(body.result.table)).toBe(true);
          expect(Array.isArray(body.result.config)).toBe(true);
          expect(typeof body.result.metadata.total_rows).toBe('number');
          for (const row of body.result.table) {
            expect(row.project_id).toBe(projectId);
            for (const value of [row.trace_id, row.span_id, row.span_name]) expect(typeof value).toBe('string');
          }
          stage = detailPath; actionAt = Date.now();
          const [detail] = await Promise.all([
            page.waitForResponse(r => matches(r, detailPath, actionAt), { timeout: UI_READY })
              .then(r => capture(r, 'detail')),
            page.locator('.clean-data-table:visible .ag-row [col-id="span_name"]').getByText(childName, { exact: true }).click(),
          ]);
          const detailBody = detail as DetailBody;
          expect(detailBody.result.trace.id).toBe(seed.traceId);
          expect(Array.isArray(detailBody.result.observation_spans)).toBe(true);
          currentDetail = { spanId: seed.spanIds[1], body: detailBody };
        } catch (error) {
          try { await attach(`${label}-failure`, { target, stage, actionAt,
            state: [...reads.values()].some(r => r.path === stage && r.startedAt >= actionAt)
              ? 'see_request_states' : 'no_matching_request', requests: [...reads.values()] }); }
          finally { throw error; }
        } finally {
          active = false;
          page.off('request', onRequest);
          page.off('requestfailed', onFailed);
        }
      }
      expect(currentDetail?.spanId).toBe(seed.spanIds[1]);
      const body = currentDetail!.body;
      const entry = selectedEntry(body.result.observation_spans, seed.spanIds[1]);
      check(entry, 'selected physical child must exist in the native detail tree').toBeDefined();
      const actual = entry?.eval_scores.find(e => e.eval_config_id === f.configId);
      await attach(`detail-${f.configId}-${seed.spanIds[1]}`, { body, targetSpanId: seed.spanIds[1], configId: f.configId });
      check(actual).toMatchObject({ eval_config_id: f.configId, eval_name: f.name,
        status: expected ? 'completed' : 'errored', error: !expected, skipped: false });
      if (expected) {
        check(actual?.explanation).toBe(explanation);
        check(actual?.score).toBe(expected.output_str_list.length ? null
          : expected.output_bool !== null ? (expected.output_bool ? 100 : 0) : expected.output_float! * 100);
        check(actual?.score_items).toEqual(expected.output_str_list.length ? expected.output_str_list : null);
        if (expected.output_str_list.length) check(actual?.result).toEqual(expected.output_str_list);
        else if (expected.output_bool !== null) check(actual?.result).toBe(expected.output_bool);
        else if (expected.output_str) check(JSON.parse(actual?.result as string)).toEqual(expected.output_str);
        else check(actual?.result).toBeNull();
      } else {
        check(actual).toMatchObject({ score: null, score_items: null, score_label: null, result: null });
      }
      await page.getByRole('tab', { name: 'Evals', exact: true }).click();
      await page.getByPlaceholder('Search evals...', { exact: true }).fill(f.name);
      const name = page.getByText(f.name, { exact: true });
      await check(name).toBeVisible({ timeout: UI_READY });
      const row = name.locator('xpath=..'); // EvalsTabView: name Typography is a direct row child.
      const labels = expected ? (expected.output_str_list.length ? expected.output_str_list
        : [expected.output_bool !== null ? (expected.output_bool ? 'Pass' : 'Fail') : `${expected.output_float! * 100}%`]) : ['Error'];
      for (const label of labels) {
        // The row is already rendered. A missing Error badge must record a
        // failure immediately so other invalid families still get inspected.
        if (soft) check(await row.getByText(label, { exact: true }).allTextContents()).toEqual([label]);
        else await check(row.getByText(label, { exact: true })).toBeVisible({ timeout: UI_READY });
      }
      await name.click();
      if (expected) await check(page.getByText(explanation!, { exact: true })).toBeVisible({ timeout: UI_READY });
      await testInfo.attach(`detail-ui-${f.configId}-${seed.spanIds[1]}`, { body: await page.screenshot(), contentType: 'image/png' });
    };

    const verifyGraph = async (response: Response, f: Family, selection: Selection) => {
      expect(response.status()).toBe(200);
      const wire = response.request().postDataJSON();
      expect(wire).toMatchObject({ project_id: projectId, property: 'average', req_data_config: {
        id: f.configId, type: 'EVAL', output_type: 'SCORE', property_id: `eval_config:${f.configId}`, source: 'traces' } });
      const body = await response.json() as GraphBody;
      await attach(`graph-${f.key}-${selection.label}`, { wire, body });
      expect(body.result).toMatchObject({ query_complete: true, query_status: 'complete', query_sampled: false,
        query_exact: true, query_provenance: 'exact_snapshot' });
      const buckets = new Map<string, { scores: number[] }>();
      for (const i of selection.indexes) {
        const fact = validFacts.find(r => r.custom_eval_config_id === f.configId && r.trace_id === seeds[i].traceId)!;
        const timestamp = bucketAt(fact.created_at, wire.interval);
        const bucket = buckets.get(timestamp) ?? { scores: [] };
        bucket.scores.push(f.expected[i].output_float! * 100);
        buckets.set(timestamp, bucket);
      }
      const oracle = [...buckets].map(([timestamp, { scores }]) => ({ timestamp,
        value: scores.reduce((a, b) => a + b, 0) / scores.length, primary_traffic: scores.length }))
        .sort((a, b) => a.timestamp.localeCompare(b.timestamp));
      const positive = body.result.data.filter(p => (p.primary_traffic ?? 0) > 0)
        .map(p => ({ ...p, timestamp: graphTimestampUTC(p.timestamp) }))
        .sort((a, b) => a.timestamp.localeCompare(b.timestamp));
      expect(positive).toEqual(oracle);
      expect(body.result.data.reduce((n, p) => n + (p.primary_traffic ?? 0), 0)).toBe(selection.indexes.length);
      if (selection.indexes.length) {
        const average = positive.reduce((n, p) => n + p.value! * p.primary_traffic!, 0) / selection.indexes.length;
        expect(average).toBe(selection.indexes.length === 2 ? 50 : selection.indexes[0] === 0 ? 20 : 80);
      } else {
        expect(body.result.data.filter(p => p.value !== null && p.value !== 0)).toEqual([]);
      }
      await expect(page.getByTestId('graph-metric-picker-trigger')).toHaveText(f.name, { timeout: UI_READY });
      await expect(page.locator('.apexcharts-canvas').first()).toBeVisible({ timeout: UI_READY });
      // Real rendered chart/tooltip, not Apex's internal series state. PrimaryGraph
      // tooltip's first series uses toFixed(2); UUID eval metrics have unit="".
      // These source-pinned Apex DOM locators still require author's live proof.
      if (positive.length) {
        const bars = page.locator('.apexcharts-bar-series .apexcharts-bar-area');
        const visibleValues: string[] = [];
        for (const bar of await bars.all()) {
          if (Number(await bar.getAttribute('val')) <= 0) continue;
          await bar.hover();
          const tooltip = page.locator('.apexcharts-tooltip.apexcharts-active .apexcharts-tooltip-text-y-value').first();
          await expect(tooltip).toBeVisible({ timeout: UI_READY });
          visibleValues.push((await tooltip.innerText()).trim());
        }
        expect(visibleValues.sort()).toEqual(positive.map(p => p.value!.toFixed(2)).sort());
      }
      await testInfo.attach(`graph-ui-${f.key}-${selection.label}`, { body: await page.screenshot(), contentType: 'image/png' });
    };

    // Native picker/filter driver scoped to this domain. No manufactured browser
    // requests: match the real wire, then replay that exact read for API parity.
    const applySelection = async (f: Family, selection: Selection, graph: boolean, soft = false) => {
      const check = soft ? expect.soft : expect;
      await page.getByRole('button', { name: 'Filter', exact: true }).click();
      await page.getByRole('button', { name: 'Property', exact: true }).first().click();
      await page.getByPlaceholder('Search properties...').fill(f.name);
      await page.locator(`[data-filter-property-option="${f.configId}"]`).click();
      const wanted = { col_type: 'EVAL_METRIC', filter_type: f.catalogType === 'SCORE' ? 'number' : 'text',
        filter_op: selection.op, filter_value: selection.value };
      const matches = (outgoing: Request) => {
        const filter = requestFilters(outgoing).find(item => item.column_id === f.configId);
        return filter?.filter_config.filter_op === selection.op &&
          JSON.stringify(filter.filter_config.filter_value) === JSON.stringify(selection.value);
      };
      const listReady = page.waitForResponse(r => new URL(r.url()).pathname === LIST &&
        r.request().method() !== 'OPTIONS' && matches(r.request()), { timeout: UI_READY });
      const graphReady = graph ? page.waitForResponse(async r => new URL(r.url()).pathname === GRAPH &&
        r.request().method() === 'POST' && r.request().postDataJSON().req_data_config.id === f.configId &&
        matches(r.request()) && r.ok() && (await r.json()).result.query_complete === true, { timeout: UI_READY }) : null;
      if (f.catalogType === 'SCORE') {
        const operator = page.getByRole('combobox').filter({ hasText: 'equals' });
        if (selection.op !== 'equals') {
          await operator.click();
          await page.getByRole('option', { name: selection.op === 'between' ? 'between' : 'greater than', exact: true }).click();
        }
        if (selection.op === 'between') {
          await page.getByPlaceholder('Min', { exact: true }).fill(String((selection.value as number[])[0]));
          await page.getByPlaceholder('Max', { exact: true }).fill(String((selection.value as number[])[1]));
        } else await page.getByPlaceholder('Value', { exact: true }).fill(String(selection.value));
      } else {
        await page.locator(`[data-filter-value-trigger="${f.configId}"]`).click();
        await expect.poll(async () => (await page.locator('[data-filter-value-option][role="checkbox"]').allTextContents())
          .map(t => t.trim()).sort(), { timeout: UI_READY }).toEqual([...f.choices].sort());
        for (const value of selection.value as string[]) {
          await page.getByPlaceholder('Search values...', { exact: true }).fill(value);
          const role = f.choices.includes(value) ? '[role="checkbox"]' : '';
          // TraceFilterPanel.showCustomValueRow supports a minted zero-match value.
          await page.locator(`[data-filter-value-option="${value}"]${role}`).click();
        }
        await page.keyboard.press('Escape');
        await expect(page.getByPlaceholder('Search values...')).toBeHidden({ timeout: UI_READY });
      }
      await page.keyboard.press('Escape');
      await expect(page.getByRole('tab', { name: 'Basic', exact: true })).toBeHidden({ timeout: UI_READY });
      const response = await listReady;
      check(response.status()).toBe(200);
      const body = await response.json() as ListBody;
      const params = listParams(response.request());
      const applied = requestFilters(response.request()).find(item => item.column_id === f.configId)!;
      check(applied).toMatchObject({ column_id: f.configId, property_id: `eval_config:${f.configId}`,
        display_name: f.name, filter_config: wanted });
      const expectedIds = selection.indexes.map(i => seeds[i].traceId).sort();
      complete(body, soft);
      check(body.result.table.map(r => r.trace_id).sort()).toEqual(expectedIds);
      const replay = await actor.api.post<ListBody>(LIST, params);
      complete(replay, soft);
      check(replay.result.table.map(r => r.trace_id).sort()).toEqual(expectedIds);
      await (soft ? expect.soft : expect).poll(async () => (await traceCells.allTextContents()).map(t => t.trim()).sort(),
        { timeout: UI_READY }).toEqual(selection.indexes.map(i => rootNames[i]).sort());
      await attach(`filter-${f.key}-${selection.label}`, { params, body, replay, expectedIds });
      if (graphReady) await verifyGraph(await graphReady, f, selection);
      await testInfo.attach(`filter-ui-${f.key}-${selection.label}`, { body: await page.screenshot(), contentType: 'image/png' });
      await page.getByRole('button', { name: 'Filter', exact: true }).click();
      await page.getByRole('button', { name: 'Clear all', exact: true }).click();
      await expect(page.getByRole('tab', { name: 'Basic', exact: true })).toBeHidden({ timeout: UI_READY });
      return params;
    };

    const positiveRequests: { family: Family; params: Record<string, string | number>; ids: string[] }[] = [];
    for (const f of families) {
      await test.step(`backend check 3: ${f.key} current discovery and both native span results`, async () => {
        const catalog = await actor.api.post<CatalogBody>(METRICS, { source: 'traces', project_ids: projectId,
          cursor_mode: true, per_eval_config: true, category: 'eval_metric', search: f.name, page_size: 25 });
        expect(catalog.result).toMatchObject({ has_more: false, next_cursor: null, query_complete: true, query_status: 'complete' });
        expect(catalog.result.metrics.map(m => ({ property_id: m.property_id, name: m.name,
          display_name: m.display_name, output_type: m.output_type }))).toEqual([{ property_id: `eval_config:${f.configId}`,
          name: f.configId, display_name: f.name, output_type: f.catalogType }]);
        const values = await actor.api.post<ValuesBody>(VALUES, { source: 'traces', project_ids: projectId,
          property_id: `eval_config:${f.configId}`, page_size: 25 });
        expect(values.result).toMatchObject({ has_more: false, next_cursor: null, query_complete: true, query_status: 'complete' });
        expect(values.result.values.map(v => v.value).sort()).toEqual([...f.choices].sort());
        await attach(`catalog-${f.key}`, { catalog, values });
        for (const i of [0, 1]) await inspectDetail(f, seeds[i], childNames[i], f.expected[i], `${verdict} saw ${markers[i]}`);
      }, { timeout: UI_READY });
      await page.goto(traceURL(), { waitUntil: 'domcontentloaded' });
      await expect.poll(async () => (await traceCells.allTextContents()).map(t => t.trim()).sort(), { timeout: UI_READY })
        .toEqual([...rootNames].sort());
      if (f.catalogType === 'SCORE') {
        await test.step(`UI: select ${f.key} as the real graph metric`, async () => {
          const ready = page.waitForResponse(async r => new URL(r.url()).pathname === GRAPH && r.request().method() === 'POST' &&
            r.request().postDataJSON().req_data_config.id === f.configId && r.ok() &&
            (await r.json()).result.query_complete === true, { timeout: UI_READY });
          await page.getByTestId('graph-metric-picker-trigger').click();
          await page.getByPlaceholder('Search metrics...').fill(f.name);
          await page.getByRole('button', { name: f.name, exact: true }).click();
          await verifyGraph(await ready, f, { label: 'unfiltered', op: '', value: [], indexes: [0, 1] });
        }, { timeout: UI_READY });
      }
      const selections: Selection[] = f.catalogType === 'SCORE' ? [
        { label: 'A', op: 'equals', value: 20, indexes: [0] },
        { label: 'B', op: 'greater_than', value: 50, indexes: [1] },
        { label: 'both', op: 'between', value: [20, 80], indexes: [0, 1] },
        { label: 'none', op: 'equals', value: 50, indexes: [] },
      ] : [
        { label: 'A', op: 'in', value: [f.key === 'pf' ? 'Passed' : alpha], indexes: [0] },
        { label: 'B', op: 'in', value: [f.key === 'pf' ? 'Failed' : gamma], indexes: [1] },
        { label: 'both', op: 'in', value: f.key === 'pf' ? ['Passed', 'Failed'] : [alpha, gamma], indexes: [0, 1] },
        { label: 'none', op: 'in', value: [f.key === 'choice' ? beta : missing], indexes: [] },
      ];
      for (const selection of selections) await test.step(`backend check 3: ${f.key} ${selection.label} exact native matches`, async () => {
        const params = await applySelection(f, selection, f.catalogType === 'SCORE');
        if (selection.label === 'both') positiveRequests.push({ family: f, params, ids: seeds.map(s => s.traceId).sort() });
      }, { timeout: UI_READY });
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect.poll(async () => (await traceCells.allTextContents()).map(t => t.trim()).sort(), { timeout: UI_READY })
        .toEqual([...rootNames].sort()); // Clear, then reload: no filter persistence claim.
    }

    await test.step('backend check 4: malformed and every semantic-invalid family are terminal errors', async () => {
      // Same public executor, no manufactured output and no API substitute for UI.
      const malformedTemplate = (await actor.api.post<Created>(`${TEMPLATES}create-v2/`, {
        name: `${prefix}-malformed`, eval_type: 'llm', model: mockModel.model,
        instructions: `Reply with exactly this JSON: {"explanation":"${verdict} saw {{marker}}"}`,
        output_type: 'pass_fail', pass_threshold: 0.5, choice_scores: null,
        check_internet: false, error_localizer_enabled: false,
      })).result.id;
      const malformedConfig = await createConfig(malformedTemplate, `${prefix}-malformed`, { marker: 'eval3_marker' });
      await attach('malformed-definition', { templateId: malformedTemplate, configId: malformedConfig });
      const malformedTask = await createTask('malformed', [malformedConfig], [seeds[0]]);
      const invalidSeeds: SeededTrace[] = [];
      const invalidNames = [`${prefix}-invalid-unknown-child`, `${prefix}-invalid-empty-child`];
      for (const i of [0, 1]) {
        invalidSeeds.push(await sendTrace(req, { collectorUrl: E2E.collectorUrl,
          apiKey: actor.apiKey, secretKey: actor.secretKey, projectName: prefix,
          rootName: `${prefix}-invalid-${i}-root`, childName: invalidNames[i],
          childAttributes: { ...Object.fromEntries(families.map(f => [f.attribute, f.invalidAnswers[i]])),
            eval3_marker: `invalid-${i}-${suffix}` } }));
        await attach(`invalid-source-${i}`, { seed: invalidSeeds[i], childName: invalidNames[i],
          inputs: Object.fromEntries(families.map(f => [f.attribute, f.invalidAnswers[i]])) });
      }
      const invalidBindings = idBindings(invalidSeeds.map(s => s.traceId), 'String');
      await expect.poll(async () => (await probe.ch<{ id: string }>(
        `SELECT id FROM spans FINAL WHERE trace_id IN (${invalidBindings.sql}) ORDER BY id`,
        invalidBindings.params)).map(r => r.id), POLL.SPAN_VISIBLE)
        .toEqual(invalidSeeds.flatMap(s => s.spanIds).sort());
      for (const i of [0, 1]) {
        const source = await probe.ch<{ project_id: string; trace_id: string; attributes: string }>(
          'SELECT project_id, trace_id, toJSONString(attrs_string) AS attributes FROM spans FINAL WHERE id={id:String} AND project_id={p:UUID}',
          { id: invalidSeeds[i].spanIds[1], p: projectId });
        expect(source).toHaveLength(1);
        expect(source[0].trace_id).toBe(invalidSeeds[i].traceId);
        expect(JSON.parse(source[0].attributes)).toMatchObject({
          ...Object.fromEntries(families.map(f => [f.attribute, f.invalidAnswers[i]])), eval3_marker: `invalid-${i}-${suffix}` });
      }
      const semanticTask = await createTask('semantic-invalid', families.map(f => f.configId), invalidSeeds);
      const bad = await readPG([malformedTask, semanticTask]);
      await attach('invalid-postgres-actual', bad); // Capture actual success/error BEFORE assertions.
      expect.soft(bad.map(r => `${r.eval_task_id}:${r.custom_eval_config_id}:${r.observation_span_id}`).sort())
        .toEqual([`${malformedTask}:${malformedConfig}:${seeds[0].spanIds[1]}`,
          ...families.flatMap(f => invalidSeeds.map(s => `${semanticTask}:${f.configId}:${s.spanIds[1]}`))].sort());
      for (const row of bad) {
        // These are REQUIRED errors, not test.fail(), tolerated coercions, skips,
        // or alternate success expectations. Collect all actual family failures.
        expect.soft(row, `invalid ${row.custom_eval_config_id}/${row.observation_span_id}`).toMatchObject({
          status: 'errored', error: true, skipped_reason: null, target_type: 'span', trace_session_id: null,
          output_bool: null, output_float: null, output_str_list: [] });
        expect.soft(row.config_hash).toMatch(/^[a-f0-9]{64}$/);
        expect.soft(Boolean(row.error_message || row.eval_explanation), 'visible invalid-output error explanation').toBe(true);
      }
      // Mirror parity is independent of whether the semantic oracle passes: a
      // faithfully mirrored WRONG completed result must remain a release failure.
      await expect.poll(() => readCH([malformedTask, semanticTask]), POLL.CDC_VISIBLE).toEqual(bad);
      await attach('invalid-clickhouse-actual', await readCH([malformedTask, semanticTask]));
      await clusteringEvidence('after-invalid');

      await test.step('UI: malformed Error and no successful malformed filter match', async () => {
        const f: Family = { ...families[0], key: 'pf', name: `${prefix}-malformed`,
          templateId: malformedTemplate, configId: malformedConfig };
        await inspectDetail(f, seeds[0], childNames[0], null, null, true);
        await page.goto(traceURL(), { waitUntil: 'domcontentloaded' });
        await applySelection(f, { label: 'malformed-no-success', op: 'in', value: ['Passed', 'Failed'], indexes: [] }, false, true);
      }, { timeout: UI_READY });
      await test.step('UI/API: every invalid family is Error and excluded from successful matches', async () => {
        // Reuse the exact union/range requests captured from supported native UI.
        // New invalid trace IDs may NEVER enter the former valid result set.
        for (const saved of positiveRequests) {
          const replay = await actor.api.post<ListBody>(LIST, saved.params);
          complete(replay, true);
          expect.soft(replay.result.table.map(r => r.trace_id).sort(), saved.family.key).toEqual(saved.ids);
          await attach(`invalid-exclusion-${saved.family.key}`, { params: saved.params, replay, expectedIds: saved.ids });
          // TraceFilterPanel STRING_OPS/NUMBER_OPS + query_builders/filters.py:
          // the public is_not_null predicate covers ANY successful typed value,
          // including an invalid scored choice incorrectly coerced to zero.
          // This extra API assertion is explicitly source-pinned, not advertised
          // as a browser-captured action; the positive wire above IS captured.
          const broadFilters = (JSON.parse(String(saved.params.filters)) as Filter[]).map(filter =>
            filter.column_id === saved.family.configId ? { ...filter, filter_config: {
              ...filter.filter_config, filter_op: 'is_not_null', filter_value: null } } : filter);
          const broadParams = { ...saved.params, filters: JSON.stringify(broadFilters) };
          const broad = await actor.api.post<ListBody>(LIST, broadParams);
          complete(broad, true);
          expect.soft(broad.result.table.map(r => r.trace_id).sort(), `${saved.family.key}: no successful invalid value of ANY magnitude`)
            .toEqual(saved.ids);
          await attach(`invalid-nonnull-exclusion-${saved.family.key}`, { provenance: 'source-pinned API predicate',
            params: broadParams, body: broad, expectedIds: saved.ids });
        }
        for (const i of [0, 1]) for (const [index, f] of families.entries()) {
          await inspectDetail(f, invalidSeeds[i], invalidNames[i], null, null, true, index > 0);
        }
      }, { timeout: UI_READY });
    });
  } finally {
    // Preserve background evidence even if a native UI assertion stops the flow.
    // An unavailable read is itself evidence, not an excuse to claim no dispatch.
    try { await clusteringEvidence('final'); }
    catch (error) { await attach('clustering-evidence-unavailable', { error: String(error), qualified: false }); }
    await req.dispose();
  }
});
