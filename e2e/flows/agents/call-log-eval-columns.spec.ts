import { request, type Request, type Response } from '@playwright/test';
import { test, expect } from '../../lib/mock-model-fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { sendTrace } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';

// False verdicts may dispatch clustering; the existing fixture attests those routes too.
test.use({ evalBackground: true });

// ProjectSerializer and OBS-E2E-008: source=simulator selects the supported Observe VOICE host.
// AgentVersionCallExecutionView's legacy list has no eval column config; it is not this host.
const PROJECTS = '/tracer/project/';
// model_hub/urls.py / tracer/urls.py; bodies pinned by their serializers and EVAL-E2E-003.
const TEMPLATES = '/model-hub/eval-templates/';
const CONFIGS = '/tracer/custom-eval-config/';
const TASKS = '/tracer/eval-task/';
// sections/agents/helper.jsx useCallLogs / useVoiceCallDetail.
const LIST = '/tracer/trace/list_voice_calls/';
const DETAIL = '/tracer/trace/voice_call_detail/';
// README first-paint budget and stack/mock-llm/server.mjs usage fingerprint.
const UI_READY = 60_000;
const MOCK_USAGE = { prompt_tokens: 7, completion_tokens: 7, total_tokens: 14 };

type Created = { result: { id: string } };
type EvalRow = {
  id: string; custom_eval_config_id: string; eval_task_id: string; trace_id: string;
  observation_span_id: string; target_type: string; status: string; error: boolean;
  deleted: boolean; output_bool: boolean | null; output_float: number | null;
  output_str: string | null; eval_explanation: string; usage: typeof MOCK_USAGE; created_at: string;
};
type CHRow = Omit<EvalRow, 'usage' | 'error' | 'deleted' | 'output_bool'> & {
  usage: string | null; error: number; deleted: number; output_bool: number | null;
};
type Counts = { pass: number; fail: number };
type CallRow = { id: string; trace_id: string; project_id: string; call_id: string; status: string;
  eval_outputs: Record<string, { name: string; output_type: string; output: number; counts: Counts }>;
  [key: string]: unknown };
// TraceVoiceCallListResponseSerializer: TOP-LEVEL results/config, unlike Observe trace/span lists.
type CallPage = { results: CallRow[]; config: Array<{ id: string; name: string; output_type: string;
  target_type?: string; annotation_label_type: null | string }>;
  count: number; has_more: boolean; next_cursor: string | null; count_is_lower_bound: boolean;
  query_complete: boolean; query_status: string };
type Rollup = { scope: string; error?: boolean; evals: Array<{ eval_config_id: string;
  eval_name: string; target_type: string; aggregate: Counts;
  spans: Array<{ span_id: string; value: string; explanation: string; status: string; error: boolean }> }> };
type CallDetail = { result: { id: string; trace_id: string; project_id: string; eval_rollup: Rollup;
  eval_outputs: Record<string, { name: string; output_type: string; output: string }> } };
type Filter = { column_id: string; property_id?: string; filter_config: {
  col_type: string; filter_type: string; filter_op: string; filter_value: unknown } };
const listParams = (req: Request): Record<string, unknown> => req.method() === 'POST'
  ? req.postDataJSON() : Object.fromEntries(new URL(req.url()).searchParams);
const filters = (params: Record<string, unknown>): Filter[] => typeof params.filters === 'string'
  ? JSON.parse(params.filters) : params.filters as Filter[];

test('AGENT-E2E-001: Observe voice Call Logs show and filter the newest evaluation count', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'AGENT-E2E-001', area: 'agents',
    userGoal: 'Read and filter the newest completed evaluation on a voice call in Observe Call Logs, then inspect its explanation.',
    steps: [
      'create an Observe voice project and ingest a conversation root plus evaluable child through OTLP',
      'run Pass then Fail historical tasks on the same child/config, replaying stable source IDs with distinct input markers',
      'open Observe Call Logs and use Display to show Call ID and the named evaluator',
      'read exactly 1 fail and click the eval cell to open Evals automatically',
      'expand the per-span breakdown and the newest explanation',
      'select native Failed then Passed filters and see exactly the seeded call then no calls',
    ],
    backendChecks: [
      'the simulator-source project/config belong to the actor scope and both OTLP spans plus the curated trace retain exact identities through replay',
      'two distinct completed task/logger rows for the same config/child persist as Pass then newer Fail, with exact mapped explanations and mock usage',
      'ClickHouse tracer_eval_logger FINAL equals both exact Postgres logger/task/trace/span identities and verdicts',
      'the actual voice list and its API replay expose the named span-target config, counts {pass:0,fail:1} and the backward-compatible scalar output 0',
      'voice detail preserves scalar Fail and adds the exact latest per-span rollup; eval-cell click selects Evals and shows its explanation',
      'native Failed and Passed filters send the config-bound predicate; browser and API return exactly the call ID or the empty set, respectively',
    ],
  }),
}, async ({ page, actor, probe, mockModel }, testInfo) => {
  // 2*90 task + 180 CDC + 2*15 source + 10*60 UI + 90 seeding/headroom = 1080s.
  test.setTimeout(1_080_000);
  page.setDefaultTimeout(UI_READY);
  const req = await request.newContext();
  const prefix = `e2e-agent-count-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = prefix, callId = `${prefix}-call`, rootName = `${prefix}-root`, childName = `${prefix}-child`;
  const evalName = `${prefix}-judge`;
  const markers = [`${prefix}-first-pass`, `${prefix}-second-fail`];
  const explanations = markers.map(marker => `${prefix} saw ${marker}`);
  const at = Date.now() - 120_000;
  const startTimeUnixNano = BigInt(at) * 1_000_000n;
  const endTimeUnixNano = startTimeUnixNano + 12_000_000_000n;
  // OBS-E2E-008 / voice_call_list.py: only a conversation root is a voice call;
  // metadata.call_execution_id is the public synthetic call identity, not a seeded CallExecution.
  const rootAttributes = { 'fi.span.kind': 'conversation', metadata: { call_execution_id: callId },
    'call.status': 'ended', 'call.duration': 12 };
  const taskIds: string[] = [];
  const attach = (name: string, value: unknown) => testInfo.attach(name, {
    body: JSON.stringify(value, null, 2), contentType: 'application/json',
  });

  try {
    await mockModel.assertReady();
    const created = await actor.api.post<{ result: { project_id: string; name: string } }>(PROJECTS, {
      name: projectName, model_type: 'GenerativeLLM', trace_type: 'observe', source: 'simulator',
    });
    expect(created.result.name).toBe(projectName);
    const projectId = created.result.project_id;
    await attach('voice-project', { projectId, projectName, callId,
      organizationId: actor.organizationId, workspaceId: actor.workspaceId });
    expect(await probe.pg('SELECT id,name,organization_id,workspace_id,source FROM tracer_project WHERE id=$1 AND deleted=false', [projectId]))
      .toEqual([{ id: projectId, name: projectName, organization_id: actor.organizationId,
        workspace_id: actor.workspaceId, source: 'simulator' }]);
    const seed = await sendTrace(req, {
      collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
      projectName, rootName, childName, startTimeUnixNano, endTimeUnixNano, rootAttributes,
      resourceAttributes: { project_type: 'observe' }, childAttributes: { answer: '"Pass"', marker: markers[0] },
    });
    await attach('voice-source-identities', { seed, rootAttributes, rootName, childName, at, markers });
    type Source = { id: string; org_id: string; project_id: string; trace_id: string; parent_span_id: string;
      observation_type: string; start_us: string; end_us: string; attrs_string: Record<string, string> };
    const readSources = () => probe.ch<Source>(`SELECT id,org_id,project_id,trace_id,parent_span_id,observation_type,
      toString(toUnixTimestamp64Micro(start_time)) AS start_us,
      toString(toUnixTimestamp64Micro(end_time)) AS end_us,attrs_string
      FROM spans FINAL WHERE trace_id={t:String} ORDER BY id`, { t: seed.traceId });
    await expect.poll(async () => {
      const spans = await readSources();
      const traces = await probe.ch('SELECT id,project_id FROM traces FINAL WHERE id={t:UUID}', { t: seed.traceId });
      return { ids: spans.map(row => row.id), traces };
    }, POLL.SPAN_VISIBLE).toEqual({ ids: [...seed.spanIds].sort(), traces: [{ id: seed.traceId, project_id: projectId }] });
    const firstSources = await readSources();
    for (const row of firstSources) expect(row).toMatchObject({ org_id: actor.organizationId,
      project_id: projectId, trace_id: seed.traceId, start_us: String(startTimeUnixNano / 1000n),
      end_us: String(endTimeUnixNano / 1000n), parent_span_id: row.id === seed.spanIds[0] ? '' : seed.spanIds[0],
      observation_type: row.id === seed.spanIds[0] ? 'conversation' : 'llm' });
    expect(firstSources.find(row => row.id === seed.spanIds[1])!.attrs_string)
      .toMatchObject({ answer: '"Pass"', marker: markers[0] });
    expect(firstSources.find(row => row.id === seed.spanIds[0])!.attrs_string).not.toHaveProperty('answer');

    await mockModel.assertReady();
    const template = (await actor.api.post<Created>(`${TEMPLATES}create-v2/`, {
      name: evalName, eval_type: 'llm',
      instructions: `Reply with exactly this JSON: {"result": {{answer}}, "explanation": "${prefix} saw {{marker}}"}`,
      model: mockModel.model, output_type: 'pass_fail', pass_threshold: 0.5,
      check_internet: false, error_localizer_enabled: false,
    })).result.id;
    const mapping = { answer: 'answer', marker: 'marker' };
    const config = (await actor.api.post<Created>(CONFIGS, {
      project: projectId, eval_template: template, name: evalName, model: mockModel.model,
      mapping, config: { mapping }, error_localizer: false,
    })).result.id;
    await attach('voice-eval-definition', { template, config, evalName, mapping });
    expect(await probe.pg(`SELECT c.id,c.project_id,c.eval_template_id,c.mapping,p.organization_id,p.workspace_id
      FROM tracer_custom_eval_config c JOIN tracer_project p ON p.id=c.project_id WHERE c.id=$1`, [config]))
      .toEqual([{ id: config, project_id: projectId, eval_template_id: template, mapping,
        organization_id: actor.organizationId, workspace_id: actor.workspaceId }]);

    const runTask = async (label: string) => {
      await mockModel.assertReady();
      const body = { name: `${evalName}-${label}`, project: projectId, evals: [config],
        filters: { project_id: projectId, span_id: [seed.spanIds[1]],
          date_range: [new Date(at - 3_600_000).toISOString(), new Date(at + 3_600_000).toISOString()] },
        run_type: 'historical', row_type: 'spans', spans_limit: 1, sampling_rate: 100 };
      const id = (await actor.api.post<Created>(TASKS, body)).result.id;
      taskIds.push(id);
      await attach(`voice-task-${label}`, { id, request: body });
      await expect.poll(async () => (await actor.api.get<{ status: string }>(`${TASKS}${id}/`)).status,
        POLL.EVAL_RESULT).toBe('completed');
      expect(await probe.pg('SELECT id,project_id,status,row_type FROM tracer_eval_task WHERE id=$1', [id]))
        .toEqual([{ id, project_id: projectId, status: 'completed', row_type: 'spans' }]);
      return id;
    };
    // EvalLogger.eval_task_id is varchar. Historical tasks materialize separate logger IDs;
    // no direct database writes, delete-and-rerun, or overwriting of the earlier result.
    const columns = `id,custom_eval_config_id,eval_task_id,trace_id,observation_span_id,target_type,status,
      error,deleted,output_bool,output_float,output_str,eval_explanation`;
    const readPG = () => probe.pg<EvalRow>(`SELECT ${columns},output_metadata->'usage' AS usage,
      to_char(created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS created_at
      FROM tracer_eval_logger WHERE eval_task_id=ANY($1::text[]) ORDER BY created_at,id::text`, [taskIds]);
    const checkAttempt = (row: EvalRow, task: string, index: number) => {
      expect(row.id).toMatch(/^[0-9a-f-]{36}$/);
      expect(row).toMatchObject({ custom_eval_config_id: config, eval_task_id: task, trace_id: seed.traceId,
        observation_span_id: seed.spanIds[1], target_type: 'span', status: 'completed', error: false,
        deleted: false, output_bool: index === 0, output_float: null, output_str: null,
        eval_explanation: explanations[index], usage: MOCK_USAGE });
    };
    const firstTask = await runTask('first');
    const firstRows = await readPG();
    await attach('voice-first-postgres-attempt', firstRows);
    expect(firstRows).toHaveLength(1);
    checkAttempt(firstRows[0], firstTask, 0);

    // Stable-ID/time replay is an existing sendTrace capability. New mapped answer
    // AND marker exercise the second judge invocation rather than a cached result.
    expect(await sendTrace(req, {
      collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
      projectName, rootName, childName, traceId: seed.traceId,
      rootSpanId: seed.spanIds[0], childSpanId: seed.spanIds[1], startTimeUnixNano, endTimeUnixNano,
      rootAttributes, resourceAttributes: { project_type: 'observe' },
      childAttributes: { answer: '"Fail"', marker: markers[1] },
    })).toEqual(seed);
    const replaySources = firstSources.map(row => row.id === seed.spanIds[1]
      ? { ...row, attrs_string: { ...row.attrs_string, answer: '"Fail"', marker: markers[1] } } : row);
    await expect.poll(readSources, POLL.SPAN_VISIBLE).toEqual(replaySources);
    expect(await probe.ch('SELECT id,project_id FROM traces FINAL WHERE id={t:UUID}', { t: seed.traceId }))
      .toEqual([{ id: seed.traceId, project_id: projectId }]);
    await attach('voice-replayed-sources', replaySources);
    const secondTask = await runTask('second');
    expect(secondTask).not.toBe(firstTask);
    const rows = await readPG();
    await attach('voice-both-postgres-attempts', rows);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toEqual(firstRows[0]);
    checkAttempt(rows[1], secondTask, 1);
    expect(rows[1].id).not.toBe(rows[0].id);
    expect(Date.parse(rows[1].created_at)).toBeGreaterThan(Date.parse(rows[0].created_at));
    const readCH = async () => (await probe.ch<CHRow>(`SELECT id,custom_eval_config_id,eval_task_id,
      trace_id,observation_span_id,target_type,status,toUInt8(error) AS error,toUInt8(deleted) AS deleted,
      toUInt8(output_bool) AS output_bool,output_float,output_str,eval_explanation,
      nullIf(JSONExtractRaw(output_metadata,'usage'),'') AS usage,
      formatDateTime(created_at,'%Y-%m-%dT%H:%i:%S.%fZ','UTC') AS created_at
      FROM tracer_eval_logger FINAL WHERE eval_task_id IN ({first:String},{second:String})
      ORDER BY created_at,toString(id)`, { first: firstTask, second: secondTask })).map(row => ({ ...row,
        error: row.error === 1, deleted: row.deleted === 1,
        output_bool: row.output_bool === null ? null : row.output_bool === 1,
        // Pending CDC versions need not have usage yet; the terminal equality below requires it.
        usage: row.usage === null ? null : JSON.parse(row.usage), created_at: new Date(row.created_at).toISOString(),
      }));
    await expect.poll(readCH, POLL.CDC_VISIBLE).toEqual(rows);
    await attach('voice-both-clickhouse-attempts', await readCH());

    const counts = { pass: 0, fail: 1 };
    const checkCalls = (body: CallPage, expected: boolean) => {
      expect(body).toMatchObject({ query_complete: true, query_status: 'complete', has_more: false,
        next_cursor: null, count_is_lower_bound: false, count: expected ? 1 : 0 });
      expect(body.results.map(row => row.trace_id)).toEqual(expected ? [seed.traceId] : []);
      if (!expected) return;
      expect(body.config.filter(column => column.id === config)).toEqual([expect.objectContaining({
        id: config, name: evalName, output_type: 'Pass/Fail', target_type: 'span', annotation_label_type: null,
      })]);
      expect(body.results[0]).toMatchObject({ id: seed.traceId, trace_id: seed.traceId,
        project_id: projectId, call_id: callId, status: 'completed' });
      expect(body.results[0][config]).toEqual(counts);
      expect(body.results[0].eval_outputs[config]).toEqual({ name: evalName, output_type: 'Pass/Fail', output: 0, counts });
    };
    const checkResponse = async (response: Response, expected: boolean, label: string) => {
      expect(response.status()).toBe(200);
      const params = listParams(response.request());
      expect(params.project_id).toBe(projectId);
      const body = await response.json() as CallPage;
      await attach(`voice-list-${label}`, { params, body });
      checkCalls(body, expected);
      const replay = await actor.api.post<CallPage>(LIST, params);
      checkCalls(replay, expected);
      return params;
    };
    const url = `/dashboard/observe/${projectId}/llm-tracing?selectedTab=trace`;
    const cells = (column: string) => page.locator(`.clean-data-table:visible .ag-row [col-id=${JSON.stringify(column)}]`);
    const open = async (label: string) => {
      const [response] = await Promise.all([
        page.waitForResponse(r => new URL(r.url()).pathname === LIST && r.request().method() !== 'OPTIONS'
          && listParams(r.request()).project_id === projectId, { timeout: UI_READY }),
        page.goto(url, { waitUntil: 'domcontentloaded' }),
      ]);
      await checkResponse(response, true, label);
    };
    await test.step('open the real Observe voice Call Logs and replay its API read', () => open('initial'), { timeout: UI_READY });
    const showColumns = async () => {
      await page.getByRole('button', { name: 'Display', exact: true }).click();
      await page.getByText('View columns', { exact: true }).click();
      const popup = page.locator('.MuiPopover-paper:visible').filter({ has: page.getByPlaceholder('Search', { exact: true }) });
      // Wait past the Trace fallback config before changing visibility (OBS-E2E-008).
      await expect(popup.getByText('Call ID', { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(popup.getByText('Trace Name', { exact: true })).toHaveCount(0, { timeout: UI_READY });
      await expect(popup.getByText(evalName, { exact: true })).toBeVisible({ timeout: UI_READY });
      const all = popup.getByRole('checkbox', { name: 'Toggle Select all', exact: true });
      await all.check(); await all.uncheck();
      for (const name of ['Call ID', evalName])
        await popup.getByText(name, { exact: true }).locator('..').getByRole('checkbox').check();
      await expect(popup.getByRole('checkbox', { checked: true })).toHaveCount(2, { timeout: UI_READY });
      await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
      await expect(page.getByText('View columns', { exact: true })).toBeHidden({ timeout: UI_READY });
    };
    await test.step('Display: show only Call ID and the named eval', showColumns, { timeout: UI_READY });
    await test.step('the count cell contains only the latest Fail', async () => {
      await expect(cells('call_id')).toHaveText([callId], { timeout: UI_READY });
      await expect(cells(`eval_outputs.${config}`)).toHaveText(['1 fail'], { timeout: UI_READY });
    }, { timeout: UI_READY });
    const checkDetail = (body: CallDetail) => {
      expect(body.result).toMatchObject({ id: seed.traceId, trace_id: seed.traceId, project_id: projectId });
      expect(body.result.eval_outputs[config]).toMatchObject({ name: evalName, output_type: 'Pass/Fail', output: 'Fail' });
      expect(body.result.eval_rollup.error).not.toBe(true);
      expect(body.result.eval_rollup.scope).toBe('trace');
      expect(body.result.eval_rollup.evals).toEqual([expect.objectContaining({ eval_config_id: config,
        eval_name: evalName, target_type: 'span', aggregate: counts,
        spans: [expect.objectContaining({ span_id: seed.spanIds[1], value: 'fail', explanation: explanations[1],
          status: 'completed', error: false })] })]);
    };
    await test.step('clicking the eval opens Evals and loads the latest detail', async () => {
      const [response] = await Promise.all([
        page.waitForResponse(r => new URL(r.url()).pathname === DETAIL && r.request().method() === 'GET'
          && new URL(r.url()).searchParams.get('trace_id') === seed.traceId, { timeout: UI_READY }),
        cells(`eval_outputs.${config}`).click(),
      ]);
      expect(response.status()).toBe(200);
      const body = await response.json() as CallDetail;
      await attach('voice-detail', body);
      checkDetail(body);
      checkDetail(await actor.api.get<CallDetail>(DETAIL, { trace_id: seed.traceId, project_id: projectId }));
      // No manual tab click: exercise CallLogsGrid -> initialEvalFocus.
      await expect(page.getByRole('tab', { name: 'Evals', exact: true })).toHaveAttribute('aria-selected', 'true', { timeout: UI_READY });
    }, { timeout: UI_READY });
    await test.step('expand the per-span breakdown and its explanation', async () => {
      const toggle = page.getByRole('button', { name: /^(Show per-span breakdown \(1\)|Hide per-span breakdown)$/ });
      const breakdown = toggle.locator('..');
      await toggle.click();
      await expect(breakdown.getByText(evalName, { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(breakdown.getByText('0/1 passed', { exact: true })).toBeVisible({ timeout: UI_READY });
      // The bounded voice detail query supplies span_id, not the trace tree's enriched name.
      await expect(breakdown.getByText(seed.spanIds[1], { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(breakdown.getByText('fail', { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(breakdown.getByRole('button', { name: 'View span', exact: true })).toHaveCount(1, { timeout: UI_READY });
      await breakdown.getByRole('button', { name: 'Show explanation', exact: true }).click();
      await expect(breakdown.getByText(explanations[1], { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(breakdown.getByText(explanations[0], { exact: true })).toHaveCount(0, { timeout: UI_READY });
    }, { timeout: UI_READY });
    await test.step('return to unfiltered Call Logs', () => open('before-filters'), { timeout: UI_READY });
    // Column visibility is page state, so reselect after this full navigation.
    await test.step('show Call ID and the evaluator for filtering', showColumns, { timeout: UI_READY });

    // TraceFilterPanel supports config-bound native Pass/Fail options labelled
    // Passed / Failed (EVAL-E2E-003). Read the real request, never intercept it.
    for (const [index, choice] of ['Failed', 'Passed'].entries()) {
      await test.step(`native ${choice} filter`, async () => {
        if (index > 0) {
          await page.getByRole('button', { name: 'Filter', exact: true }).click();
          await page.getByRole('button', { name: 'Clear all', exact: true }).click();
          await expect(page.getByRole('tab', { name: 'Basic', exact: true })).toBeHidden({ timeout: UI_READY });
        }
        await page.getByRole('button', { name: 'Filter', exact: true }).click();
        await page.getByRole('button', { name: 'Property', exact: true }).first().click();
        await page.getByPlaceholder('Search properties...').fill(evalName);
        await page.locator(`[data-filter-property-option="${config}"]`).click();
        await page.locator(`[data-filter-value-trigger="${config}"]`).click();
        await expect.poll(async () => (await page.locator('[data-filter-value-option][role="checkbox"]')
          .allTextContents()).map(text => text.trim()).sort(), { timeout: UI_READY }).toEqual(['Failed', 'Passed']);
        const [response] = await Promise.all([
          page.waitForResponse(r => new URL(r.url()).pathname === LIST && r.request().method() !== 'OPTIONS'
            && listParams(r.request()).project_id === projectId
            && filters(listParams(r.request())).some(f => f.column_id === config
              && JSON.stringify(f.filter_config.filter_value) === JSON.stringify([choice])), { timeout: UI_READY }),
          (async () => {
            await page.locator(`[data-filter-value-option="${choice}"][role="checkbox"]`).click();
            await page.keyboard.press('Escape');
            await expect(page.getByPlaceholder('Search values...')).toBeHidden({ timeout: UI_READY });
            await page.keyboard.press('Escape');
            await expect(page.getByRole('tab', { name: 'Basic', exact: true })).toBeHidden({ timeout: UI_READY });
          })(),
        ]);
        const params = await checkResponse(response, index === 0, choice);
        expect(filters(params).filter(f => f.column_id === config)).toEqual([expect.objectContaining({
          column_id: config, property_id: `eval_config:${config}`, filter_config: {
            col_type: 'EVAL_METRIC', filter_type: 'text', filter_op: 'in', filter_value: [choice],
          },
        })]);
        await expect(cells('call_id')).toHaveText(index === 0 ? [callId] : [], { timeout: UI_READY });
        await expect(cells(`eval_outputs.${config}`)).toHaveText(index === 0 ? ['1 fail'] : [], { timeout: UI_READY });
        if (index > 0) await expect(page.getByText('No calls found', { exact: true })).toBeVisible({ timeout: UI_READY });
      }, { timeout: UI_READY });
    }
  } finally {
    await req.dispose();
  }
});
