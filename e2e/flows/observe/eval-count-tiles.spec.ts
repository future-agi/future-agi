import { request, type Request, type Response } from '@playwright/test';
import { test, expect } from '../../lib/mock-model-fixtures';
import { sendTrace } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// A failing result can dispatch clustering: use the existing background-routing attestation.
test.use({ evalBackground: true });

// model_hub/urls.py and tracer/urls.py; create bodies match the serializers and EVAL-E2E-003.
const TEMPLATES = '/model-hub/eval-templates/';
const CONFIGS = '/tracer/custom-eval-config/';
const TASKS = '/tracer/eval-task/';
// TraceGrid.jsx / SpanGrid.jsx use these same read endpoints.
const TRACE_LIST = '/tracer/trace/list_traces_of_session/';
const SPAN_LIST = '/tracer/observation-span/list_spans_observe/';
// README: one browser action's first-paint budget, not the entire UI stage.
const UI_READY = 60_000;
// stack/mock-llm/server.mjs: evidence that the real judge path reached the managed mock.
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
type Rollup = { scope: string; error?: boolean; evals: Array<{
  eval_config_id: string; eval_name: string; target_type: string; aggregate: Record<string, number>;
  spans: Array<{ span_id: string; value: unknown; explanation: string; status: string; error: boolean }>;
}> };
type DetailEntry = { observation_span: { id: string }; eval_rollup: Rollup; children?: DetailEntry[] };
type Detail = { result: { observation_spans: DetailEntry[] } };
type ListBody = { result: { table: Array<Record<string, unknown>>;
  config: Array<{ id: string; name: string; output_type: string; target_type?: string }>;
  metadata: { query_complete: boolean; query_status: string; has_more: boolean; next_cursor: string | null } } };

// TraceDetailSpanSerializer returns nested children, not a flat list of all spans.
function findSpan(entries: DetailEntry[], id: string): DetailEntry | undefined {
  for (const entry of entries) {
    if (entry.observation_span.id === id) return entry;
    const child = findSpan(entry.children ?? [], id);
    if (child) return child;
  }
  return undefined;
}
const listParams = (req: Request): Record<string, unknown> => req.method() === 'POST'
  ? req.postDataJSON() : Object.fromEntries(new URL(req.url()).searchParams);

test('OBS-E2E-037: Observe count tiles use one latest completed eval per span', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-037', area: 'observe',
    userGoal: 'See only the newest completed result for a rerun span in Observe count tiles and the detail breakdown.',
    steps: [
      'ingest a root and child span and run a real Pass evaluation on the child',
      'replay the same OTLP identities and timestamps with a Fail input and distinct explanation marker',
      'run a second historical task without deleting the first completed result',
      'show the identity and evaluator columns in the trace and span grids and read exactly 1 fail',
      'click the eval cell to open Evals, expand the per-span breakdown and its latest explanation',
    ],
    backendChecks: [
      'the project and config have the exact actor scope; both source spans and the curated trace retain their identities across replay',
      'two distinct completed task/logger rows retain the same config and child span, with Pass then a newer Fail and exact mapped explanations and mock usage',
      'ClickHouse tracer_eval_logger FINAL equals both exact Postgres rows, including task/logger/trace/span IDs and verdicts',
      'the actual trace and span list responses and API replays expose the minted span-target config and exactly {pass:0,fail:1}',
      'trace-root and recursively located child detail rollups contain only the latest Fail and explanation; eval-cell click opens Evals and the expanded breakdown displays them',
    ],
  }),
}, async ({ page, actor, probe, mockModel }, testInfo) => {
  // 2*90 task + 180 CDC + 2*15 source + 10*60 UI + 90 seeding/headroom = 1080s.
  test.setTimeout(1_080_000);
  page.setDefaultTimeout(UI_READY);
  const req = await request.newContext();
  const prefix = `e2e-obs-count-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = prefix, rootName = `${prefix}-root`, childName = `${prefix}-child`;
  const evalName = `${prefix}-judge`;
  const markers = [`${prefix}-first-pass`, `${prefix}-second-fail`];
  const explanations = markers.map(marker => `${prefix} saw ${marker}`);
  const at = Date.now() - 120_000;
  const startTimeUnixNano = BigInt(at) * 1_000_000n;
  const endTimeUnixNano = startTimeUnixNano + 50_000_000n;
  const taskIds: string[] = [];
  const attach = (name: string, value: unknown) => testInfo.attach(name, {
    contentType: 'application/json', body: JSON.stringify(value, null, 2),
  });

  try {
    await mockModel.assertReady();
    const seed = await sendTrace(req, {
      collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
      projectName, rootName, childName, startTimeUnixNano, endTimeUnixNano,
      childAttributes: { answer: '"Pass"', marker: markers[0] },
    });
    await attach('seed', { seed, rootName, childName, at, markers,
      organizationId: actor.organizationId, workspaceId: actor.workspaceId });

    type Source = { id: string; org_id: string; project_id: string; trace_id: string;
      parent_span_id: string; start_us: string; end_us: string; attrs_string: Record<string, string> };
    const readSources = () => probe.ch<Source>(`SELECT id,org_id,project_id,trace_id,parent_span_id,
      toString(toUnixTimestamp64Micro(start_time)) AS start_us,
      toString(toUnixTimestamp64Micro(end_time)) AS end_us,attrs_string
      FROM spans FINAL WHERE trace_id={t:String} ORDER BY id`, { t: seed.traceId });
    await expect.poll(async () => {
      const spans = await readSources();
      const traces = await probe.ch<{ id: string }>('SELECT id FROM traces FINAL WHERE id={t:UUID}', { t: seed.traceId });
      return { ids: spans.map(row => row.id), traces: traces.map(row => row.id) };
    }, POLL.SPAN_VISIBLE).toEqual({ ids: [...seed.spanIds].sort(), traces: [seed.traceId] });
    const projects = await probe.pg<{ id: string; organization_id: string; workspace_id: string }>(
      'SELECT id,organization_id,workspace_id FROM tracer_project WHERE name=$1 AND deleted=false', [projectName]);
    expect(projects).toHaveLength(1);
    expect(projects[0]).toMatchObject({ organization_id: actor.organizationId, workspace_id: actor.workspaceId });
    const projectId = projects[0].id;
    await attach('project', projects[0]);
    const firstSources = await readSources();
    for (const row of firstSources) expect(row).toMatchObject({ org_id: actor.organizationId,
      project_id: projectId, trace_id: seed.traceId, start_us: String(startTimeUnixNano / 1000n),
      end_us: String(endTimeUnixNano / 1000n), parent_span_id: row.id === seed.spanIds[0] ? '' : seed.spanIds[0] });
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
    await attach('eval-definition', { template, config, evalName, mapping });
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
      await attach(`task-${label}`, { id, request: body });
      await expect.poll(async () => (await actor.api.get<{ status: string }>(`${TASKS}${id}/`)).status,
        POLL.EVAL_RESULT).toBe('completed');
      expect(await probe.pg('SELECT id,project_id,status,row_type FROM tracer_eval_task WHERE id=$1', [id]))
        .toEqual([{ id, project_id: projectId, status: 'completed', row_type: 'spans' }]);
      return id;
    };
    // EvalLogger.eval_task_id is varchar, not a UUID foreign key.
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
    await attach('first-postgres-attempt', firstRows);
    expect(firstRows).toHaveLength(1);
    checkAttempt(firstRows[0], firstTask, 0);

    // sendTrace's existing replay API keeps the full source sorting key stable.
    // Changing both mapped inputs makes this a real second evaluation, not a cache no-op.
    expect(await sendTrace(req, {
      collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
      projectName, rootName, childName, traceId: seed.traceId,
      rootSpanId: seed.spanIds[0], childSpanId: seed.spanIds[1], startTimeUnixNano, endTimeUnixNano,
      childAttributes: { answer: '"Fail"', marker: markers[1] },
    })).toEqual(seed);
    const replaySources = firstSources.map(row => row.id === seed.spanIds[1]
      ? { ...row, attrs_string: { ...row.attrs_string, answer: '"Fail"', marker: markers[1] } } : row);
    await expect.poll(readSources, POLL.SPAN_VISIBLE).toEqual(replaySources);
    expect(await probe.ch('SELECT id,project_id FROM traces FINAL WHERE id={t:UUID}', { t: seed.traceId }))
      .toEqual([{ id: seed.traceId, project_id: projectId }]);
    await attach('replayed-sources', replaySources);
    const secondTask = await runTask('second');
    expect(secondTask).not.toBe(firstTask);
    const rows = await readPG();
    await attach('both-postgres-attempts', rows);
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
    await attach('both-clickhouse-attempts', await readCH());

    const counts = { pass: 0, fail: 1 };
    const checkList = (body: ListBody, spans: boolean) => {
      expect(body.result.metadata).toMatchObject({ query_complete: true, query_status: 'complete',
        has_more: false, next_cursor: null });
      expect(body.result.config.filter(column => column.id === config)).toEqual([
        expect.objectContaining({ id: config, name: evalName, output_type: 'Pass/Fail', target_type: 'span' }),
      ]);
      if (spans) {
        expect(body.result.table.map(row => row.span_id).sort()).toEqual([...seed.spanIds].sort());
        expect(body.result.table.find(row => row.span_id === seed.spanIds[1])![config]).toEqual(counts);
      } else {
        expect(body.result.table.map(row => row.trace_id)).toEqual([seed.traceId]);
        expect(body.result.table[0][config]).toEqual(counts);
      }
      for (const row of body.result.table) expect(row).toMatchObject({ project_id: projectId, trace_id: seed.traceId });
    };
    const checkResponse = async (response: Response, spans: boolean) => {
      expect(response.status()).toBe(200);
      const params = listParams(response.request());
      expect(params.project_id).toBe(projectId);
      const body = await response.json() as ListBody;
      await attach(spans ? 'span-list' : 'trace-list', { params, body });
      checkList(body, spans);
      const replay = await actor.api.post<ListBody>(spans ? SPAN_LIST : TRACE_LIST, params);
      checkList(replay, spans);
    };
    const cells = (column: string) => page.locator(`.clean-data-table:visible .ag-row [col-id=${JSON.stringify(column)}]`);
    // DisplayPanel -> ColumnVisibilityDropdown: keep the identity and eval visible
    // instead of assuming a horizontally virtualized far-right column is mounted.
    const showColumns = async (identity: string) => {
      await page.getByRole('button', { name: 'Display', exact: true }).click();
      await page.getByText('View columns', { exact: true }).click();
      const popup = page.locator('.MuiPopover-paper:visible').filter({ has: page.getByPlaceholder('Search', { exact: true }) });
      await expect(popup.getByText(evalName, { exact: true })).toBeVisible({ timeout: UI_READY });
      const all = popup.getByRole('checkbox', { name: 'Toggle Select all', exact: true });
      await all.check(); await all.uncheck();
      for (const name of [identity, evalName])
        await popup.getByText(name, { exact: true }).locator('..').getByRole('checkbox').check();
      await expect(popup.getByRole('checkbox', { checked: true })).toHaveCount(2, { timeout: UI_READY });
      await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
      await expect(page.getByText('View columns', { exact: true })).toBeHidden({ timeout: UI_READY });
    };
    for (const spans of [false, true]) {
      const endpoint = spans ? SPAN_LIST : TRACE_LIST;
      await test.step(`open ${spans ? 'span' : 'trace'} list and check API parity`, async () => {
        const [response] = await Promise.all([
          page.waitForResponse(r => new URL(r.url()).pathname === endpoint && r.request().method() !== 'OPTIONS'
            && listParams(r.request()).project_id === projectId, { timeout: UI_READY }),
          page.goto(`/dashboard/observe/${projectId}/llm-tracing?selectedTab=${spans ? 'spans' : 'trace'}`,
            { waitUntil: 'domcontentloaded' }),
        ]);
        await checkResponse(response, spans);
      }, { timeout: UI_READY });
      await test.step('show identity and eval columns', () => showColumns(spans ? 'Span Name' : 'Trace Name'), { timeout: UI_READY });
      await test.step('read the newest completed count tile', async () => {
        const identity = spans ? 'span_name' : 'trace_name';
        if (!spans) await expect(cells(identity)).toHaveText([rootName], { timeout: UI_READY });
        else await expect.poll(async () => (await cells(identity).allTextContents()).sort(), { timeout: UI_READY })
          .toEqual([rootName, childName].sort());
        const row = page.locator('.clean-data-table:visible .ag-row').filter({
          has: page.locator(`[col-id="${identity}"]`).getByText(spans ? childName : rootName, { exact: true }),
        });
        await expect(row.locator(`[col-id="${config}"]`)).toHaveText('1 fail', { timeout: UI_READY });
      }, { timeout: UI_READY });
    }
    const checkDetail = (body: Detail) => {
      for (const [id, scope] of [[seed.spanIds[0], 'trace'], [seed.spanIds[1], 'span']]) {
        const rollup = findSpan(body.result.observation_spans, id)?.eval_rollup;
        expect(rollup).toMatchObject({ scope });
        expect(rollup?.error).not.toBe(true);
        expect(rollup?.evals).toEqual([expect.objectContaining({ eval_config_id: config, eval_name: evalName,
          target_type: 'span', aggregate: counts, spans: [expect.objectContaining({ span_id: seed.spanIds[1],
            value: 'fail', explanation: explanations[1], status: 'completed', error: false })] })]);
      }
    };
    await test.step('eval-cell click opens Evals and returns the exact nested detail rollup', async () => {
      const detailPath = `/tracer/trace/${seed.traceId}/`;
      const childRow = page.locator('.clean-data-table:visible .ag-row').filter({
        has: page.locator('[col-id="span_name"]').getByText(childName, { exact: true }),
      });
      const [response] = await Promise.all([
        page.waitForResponse(r => new URL(r.url()).pathname === detailPath && r.request().method() === 'GET', { timeout: UI_READY }),
        childRow.locator(`[col-id="${config}"]`).click(),
      ]);
      expect(response.status()).toBe(200);
      const body = await response.json() as Detail;
      await attach('detail-rollups', body);
      checkDetail(body);
      checkDetail(await actor.api.get<Detail>(detailPath, { project_id: projectId }));
      // Do NOT click Evals here: this assertion proves the eval-cell focus handoff.
      await expect(page.getByRole('tab', { name: 'Evals', exact: true })).toHaveAttribute('aria-selected', 'true', { timeout: UI_READY });
    }, { timeout: UI_READY });
    await test.step('expand the latest per-span result and explanation', async () => {
      const toggle = page.getByRole('button', { name: /^(Show per-span breakdown \(1\)|Hide per-span breakdown)$/ });
      // EvalRollupBreakdown owns this Box; scope away from the raw-attempt list below it.
      const breakdown = toggle.locator('..');
      await toggle.click();
      await expect(breakdown.getByText(evalName, { exact: true })).toHaveText(evalName, { timeout: UI_READY });
      await expect(breakdown.getByText('0/1 passed', { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(breakdown.getByText('fail', { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(breakdown.getByRole('button', { name: 'View span', exact: true })).toHaveCount(1, { timeout: UI_READY });
      await breakdown.getByRole('button', { name: 'Show explanation', exact: true }).click();
      await expect(breakdown.getByText(explanations[1], { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(breakdown.getByText(explanations[0], { exact: true })).toHaveCount(0, { timeout: UI_READY });
    }, { timeout: UI_READY });
  } finally {
    await req.dispose();
  }
});
