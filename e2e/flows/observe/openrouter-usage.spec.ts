import { request, type Request, type Page } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace, type OtlpAttributes, type SendTraceConfig, type SeededTrace } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';
import { selectPageSize, pagerButton, waitForCurrentPage } from '../../lib/pager';

// TraceGrid.buildParams / tracer/serializers/trace.py: numbered sorted reads and
// cursor reads share this endpoint, envelope and SYSTEM_METRIC numeric filter.
const LIST = '/tracer/trace/list_traces_of_session/';
const SESSIONS = '/tracer/trace-session/list_sessions/';
const USERS = '/tracer/users/';
const UI_READY = 60_000;
// Existing UI-supported small page size, pinned from lib/pager and CursorGridPagination.
const PAGE_SIZE = 10;
const ELIGIBLE = PAGE_SIZE + 3;
// Normal desktop viewport: reach horizontally virtualized columns by scrolling,
// not by making the viewport wider than a customer's screen.
test.use({ viewport: { width: 1440, height: 900 } });

type TraceRow = { trace_id: string; input: string; output: string; total_tokens: number; cost: number };
type TracePage = { result: { table: TraceRow[]; metadata: { total_rows: number; total_rows_is_lower_bound?: boolean; next_cursor?: string; has_more?: boolean } } };
type Shape = 'or' | 'nested' | 'parent-only' | 'siblings' | 'independent-root' | 'explicit' | 'experiment';
type Case = { key: string; shape: Shape; total: number; cost: number; display?: string; user: string; session: string; start: bigint; input: string; output: string; seeded: SeededTrace };
const wire = (r: Request): Record<string, any> => r.method() === 'POST' ? r.postDataJSON() : Object.fromEntries(new URL(r.url()).searchParams);
const ids = (rows: TraceRow[]) => rows.map(row => row.trace_id);
const gridIds = (page: Page) => page.locator('.clean-data-table:visible .ag-center-cols-container .ag-row[row-id]')
  .evaluateAll(rows => rows.map(row => row.getAttribute('row-id')));
const near = (actual: number, expected: number) => expect(Math.abs(actual - expected)).toBeLessThanOrEqual(Math.max(1e-18, Math.abs(expected) * 1e-9));

// Wire attributes pinned from fi-collector's recorded OR1 fixture at
// 1b2e14baf31a7b9304dbc40e8003621e4d86c921. These are synthetic OR-shaped
// exports through the REAL collector, not a live OpenRouter Broadcast claim.
const usage = (tokens: number, cost: number): OtlpAttributes => ({
  'gen_ai.usage.input_tokens': tokens ? tokens - 7 : 0,
  'gen_ai.usage.output_tokens': tokens ? 7 : 0,
  'gen_ai.usage.total_tokens': tokens,
  'gen_ai.usage.total_cost': cost,
  'gen_ai.request.model': 'openai/gpt-4o-mini',
});

test('OBS-E2E-052: OpenRouter-shaped traces keep coherent usage through filters, sorting and details', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-052', area: 'observe',
    userGoal: 'A developer inspects OpenRouter-shaped traces and trusts content, identities and usage through filtering, sorting and trace/session details',
    steps: [
      'ingest empty-root generation/attempt trees, tiny/zero costs, nested summaries, parent-only and independent-call controls',
      'select Total Tokens greater than 20 in the real Filter panel and cross a real page boundary',
      'scroll to Tokens and Total Cost, sort each ascending and descending, and compare complete UI/API pages with the independent oracle',
      'inspect tiny-cost and usage-control trace details',
      'inspect first-generation session messages and exact Users identities',
    ],
    backendChecks: [
      'A: exact latest source hierarchy, usage, project type and user linkage retain the scoped fixture identities',
      'B: actual filter/sort requests and two complete pages equal the canonical usage oracle with exact total, stable ties and no duplicates',
      'C: trace/span/session API precision and UI displays retain positive subprecision costs, nested 18, equal independent calls and parent-only usage',
      'D: session first-generation messages and Users UI/API identities equal the scoped storage facts',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // A: SPAN_VISIBLE 15s; B: initial navigation/filter/resize/next/back plus
  // four sorts * three page actions <= 17 UI_READY; C: 7 detail navigations;
  // D: 3 navigations. 15 + (17+7+3)*60 + 105s headroom = 1,740s. No retries.
  test.setTimeout(1_740_000);
  page.setDefaultTimeout(UI_READY);
  const req = await request.newContext();
  const prefix = `e2e-obs52-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `${prefix}-project`;
  const experimentName = `${prefix}-experiment`;
  const orUser = `${prefix}-or-user`, plainUser = `${prefix}-plain-user`;
  const session = `${prefix}-session`;
  const start = BigInt(Date.now() - 30_000) * 1_000_000n;
  const cases: Case[] = [];
  try {
    // ProjectSerializer / ProjectView.create: trace_type, model_type, name;
    // organization/workspace are read-only, bound by actor headers. Model type
    // is AIModel.ModelTypes.GENERATIVE_LLM (not the span kind "llm").
    await actor.api.post('/tracer/project/', { name: experimentName, trace_type: 'experiment', model_type: 'GenerativeLLM' });
    const inputs: { key: string; shape: Shape; total: number; cost: number; display?: string }[] = [
      ...Array.from({ length: ELIGIBLE - 2 }, (_, i) => ({ key: `or-${i}`, shape: 'or' as const, total: 21 + i, cost: (ELIGIBLE - 2 - i) * 1e-6 })),
      { key: 'equal-calls', shape: 'siblings', total: 36, cost: 1.17e-5 },
      { key: 'root-content', shape: 'independent-root', total: 36, cost: 1.17e-5 },
      { key: 'nested', shape: 'nested', total: 18, cost: 5.85e-6 },
      { key: 'parent-only', shape: 'parent-only', total: 18, cost: 5.85e-6 },
      { key: 'tiny', shape: 'or', total: 18, cost: 3e-8, display: '< $0.0000001' },
      { key: 'small', shape: 'or', total: 18, cost: 2.37e-5, display: '$0.0000237' },
      { key: 'zero', shape: 'or', total: 18, cost: 0, display: '$0.00' },
      { key: 'explicit', shape: 'explicit', total: 0, cost: 0 },
      { key: 'experiment', shape: 'experiment', total: 0, cost: 0 },
    ];
    for (const [index, item] of inputs.entries()) {
      const isOR = ['or', 'explicit', 'experiment'].includes(item.shape);
      const user = isOR ? orUser : plainUser;
      const sessionName = ['or-0', 'or-1', 'or-2'].includes(item.key) ? session : `${prefix}-${item.key}-session`;
      const identity = { 'user.id': user, 'session.id': sessionName };
      const prompt = `${prefix}-${item.key}-input`, output = `${prefix}-${item.key}-output`;
      // Equal-call controls share a timestamp to exercise the trace-ID tie break.
      const began = start + BigInt(item.total === 36 ? 12 : index) * 100_000_000n;
      const cfg: SendTraceConfig = {
        collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
        projectName: item.shape === 'experiment' ? experimentName : projectName,
        rootName: `${prefix}-${item.key}`, rootSpanId: '0000000000000001', childSpanId: '0000000000000002',
        startTimeUnixNano: began, endTimeUnixNano: began + 50_000_000n,
        resourceAttributes: item.shape === 'explicit' ? { project_type: 'experiment' } : {},
        rootAttributes: { ...identity, ...(isOR ? { 'trace.metadata.openrouter.request_id': `${prefix}-${item.key}` } : {}) },
        childAttributes: { ...identity, ...usage(item.total, item.cost), 'input.value': prompt, 'output.value': output },
        additionalSpans: isOR ? [{ spanId: '0000000000000003', parentSpanId: '0000000000000002', name: 'Provider Attempt', attributes: identity }] : [],
      };
      if (item.shape === 'nested') {
        cfg.rootName = 'pydantic_ai.agent.run';
        cfg.childName = 'pydantic_ai.agent.run';
        cfg.rootAttributes = { ...identity, ...usage(18, 5.85e-6) };
        cfg.childAttributes = { ...identity, ...usage(18, 5.85e-6), 'fi.span.kind': 'unknown' };
        cfg.additionalSpans = [{ spanId: '0000000000000003', parentSpanId: '0000000000000002', name: 'ChatCompletion',
          attributes: { ...identity, ...usage(18, 5.85e-6), 'fi.span.kind': 'llm', 'input.value': prompt, 'output.value': output } }];
      } else if (item.shape === 'parent-only') {
        cfg.rootName = 'pydantic_ai.agent.run';
        cfg.rootAttributes = { ...identity, ...usage(18, 5.85e-6), 'input.value': prompt, 'output.value': output };
        cfg.childAttributes = { ...identity, 'fi.span.kind': 'tool' };
      } else if (item.shape === 'siblings' || item.shape === 'independent-root') {
        cfg.childAttributes = { ...identity, ...usage(18, 5.85e-6), 'input.value': 'child does not replace root', 'output.value': output };
        if (item.shape === 'siblings') {
          cfg.rootAttributes = { ...identity, 'input.value': prompt, 'output.value': output };
          cfg.additionalSpans = [{ spanId: '0000000000000003', parentSpanId: '0000000000000001', name: 'independent LLM', attributes: { ...identity, ...usage(18, 5.85e-6), 'fi.span.kind': 'llm' } }];
        } else cfg.rootAttributes = { ...identity, ...usage(18, 5.85e-6), 'fi.span.kind': 'llm', 'input.value': prompt, 'output.value': output };
      }
      cases.push({ ...item, user, session: sessionName, start: began, input: prompt, output, seeded: await sendTrace(req, cfg) });
    }
    await testInfo.attach('seeded-or-shaped-fixtures', { body: JSON.stringify(cases, (_, v) => typeof v === 'bigint' ? String(v) : v), contentType: 'application/json' });
    const projects = await probe.pg<{ id: string; name: string; trace_type: string }>(
      'SELECT id, name, trace_type FROM tracer_project WHERE name = ANY($1) AND organization_id = $2 AND workspace_id = $3',
      [[projectName, experimentName], actor.organizationId, actor.workspaceId]);
    expect(projects.map(p => [p.name, p.trace_type]).sort()).toEqual([[projectName, 'observe'], [experimentName, 'experiment']].sort());
    const projectId = projects.find(p => p.name === projectName)!.id;
    const experimentId = projects.find(p => p.name === experimentName)!.id;
    const primary = cases.filter(c => c.shape !== 'experiment');
    const eligible = primary.filter(c => c.total > 20);
    expect(eligible.length).toBe(ELIGIBLE);
    type Stored = { trace_id: string; id: string; parent_span_id: string; project_id: string; observation_type: string; total_tokens: number; cost: number; end_user_id: string | null; trace_session_id: string | null; project_type: string };
    const readSources = () => probe.ch<Stored>(`SELECT trace_id, id, parent_span_id, project_id, observation_type, total_tokens, cost, end_user_id, trace_session_id,
      JSONExtractString(toJSONString(resource_attrs), 'project_type') AS project_type
      FROM spans FINAL WHERE project_id IN ({p:UUID}, {e:UUID}) ORDER BY trace_id, id`, { p: projectId, e: experimentId });
    let source: Stored[] = [];
    await test.step('A: exact scoped source identities and accounting shape', async () => {
      await expect.poll(async () => { source = await readSources(); return source.map(r => `${r.trace_id}:${r.id}`).sort(); }, POLL.SPAN_VISIBLE)
        .toEqual(cases.flatMap(c => c.seeded.spanIds.map(id => `${c.seeded.traceId}:${id}`)).sort());
      for (const c of cases) {
        const rows = source.filter(r => r.trace_id === c.seeded.traceId);
        expect(rows.map(r => r.parent_span_id)).toEqual(c.shape === 'siblings' ? ['', '0000000000000001', '0000000000000001']
          : c.seeded.spanIds.map((_, i) => i === 0 ? '' : `000000000000000${i}`));
        for (const row of rows) {
          expect(row.project_id).toBe(c.shape === 'experiment' ? experimentId : projectId);
          if (['explicit', 'experiment'].includes(c.shape)) expect(row.end_user_id).toBeNull();
          else expect(row.end_user_id).toMatch(/^[0-9a-f-]{36}$/);
          expect(row.project_type).toBe(c.shape === 'experiment' ? '' : c.shape === 'explicit' ? 'experiment' : 'observe');
        }
        if (c.shape === 'or') {
          expect(rows.map(r => r.observation_type)).toEqual(['chain', 'llm', 'chain']);
          expect(rows.map(r => r.total_tokens)).toEqual([0, c.total, 0]);
          near(rows[1].cost, c.cost);
        }
        if (c.shape === 'nested') expect(rows.map(r => r.total_tokens)).toEqual([18, 18, 18]);
        if (c.shape === 'siblings') expect(rows.map(r => r.total_tokens)).toEqual([0, 18, 18]);
      }
      await testInfo.attach('source-facts', { body: JSON.stringify(source), contentType: 'application/json' });
    });
    const filtered = (r: Request) => {
      if (new URL(r.url()).pathname !== LIST || r.method() !== 'POST') return false;
      const q = wire(r);
      return q.project_id === projectId && JSON.parse(q.filters).some((f: any) => f.column_id === 'total_tokens'
        && f.filter_config.filter_op === 'greater_than' && f.filter_config.filter_value === 20);
    };
    let lastQuery: Record<string, any> = {};
    const capturePage = async (action: () => Promise<unknown>, predicate: (r: Request) => boolean = filtered) => {
      const pending = page.waitForResponse(r => predicate(r.request()), { timeout: UI_READY });
      await action();
      const response = await pending;
      expect(response.status()).toBe(200);
      lastQuery = wire(response.request());
      const body = await response.json() as TracePage;
      await expect.poll(() => gridIds(page), { timeout: UI_READY }).toEqual(ids(body.result.table));
      const replay = await actor.api.post<TracePage>(LIST, lastQuery);
      expect(ids(replay.result.table)).toEqual(ids(body.result.table));
      for (const row of body.result.table) {
        const c = cases.find(c => c.seeded.traceId === row.trace_id)!;
        expect(row.total_tokens).toBe(c.total); near(row.cost, c.cost);
        expect([row.input, row.output]).toEqual([c.input, c.output]);
      }
      return body;
    };
    await test.step('B: actual Total Tokens greater than 20 filter', async () => {
      await page.goto(`/dashboard/observe/${projectId}/llm-tracing?tab=traces`, { waitUntil: 'domcontentloaded' });
      await expect.poll(() => gridIds(page), { timeout: UI_READY }).toHaveLength(primary.length);
      await page.getByRole('button', { name: 'Filter', exact: true }).click();
      await page.getByRole('button', { name: 'Property', exact: true }).click();
      await page.getByPlaceholder('Search properties...').fill('Total Tokens');
      await page.locator('[data-filter-property-option="total_tokens"]').click();
      const panel = page.locator('.MuiPopover-paper:visible').filter({ has: page.getByRole('tab', { name: 'Basic', exact: true }) });
      await panel.getByRole('combobox').first().click();
      await page.getByRole('option', { name: 'greater than', exact: true }).click();
      const result = await capturePage(async () => {
        await page.getByPlaceholder('Value', { exact: true }).fill('20');
        await page.keyboard.press('Tab');
      });
      expect(ids(result.result.table).sort()).toEqual(eligible.map(c => c.seeded.traceId).sort());
      await page.keyboard.press('Escape');
      await expect(page.getByRole('tab', { name: 'Basic', exact: true })).toBeHidden({ timeout: UI_READY });
    }, { timeout: UI_READY });
    let first: TracePage;
    await test.step('B: page size 10 is sent on the wire', async () => {
      first = await capturePage(() => selectPageSize(page, PAGE_SIZE), r => filtered(r) && wire(r).page_size === PAGE_SIZE);
      expect(first.result.table).toHaveLength(PAGE_SIZE);
    }, { timeout: UI_READY });
    await test.step('B: complete second page has no duplicate or omission', async () => {
      const second = await capturePage(() => pagerButton(page, 'Next page').click());
      await waitForCurrentPage(page, 2);
      const union = [...ids(first.result.table), ...ids(second.result.table)];
      expect(union.sort()).toEqual(eligible.map(c => c.seeded.traceId).sort());
      expect(second.result.metadata.total_rows).toBe(ELIGIBLE);
      expect(second.result.metadata.total_rows_is_lower_bound).toBe(false);
      await expect(pagerButton(page, 'Next page')).toBeDisabled({ timeout: UI_READY });
      await pagerButton(page, 'Previous page').click();
      await waitForCurrentPage(page, 1);
    }, { timeout: UI_READY });
    for (const [column, label, direction] of [
      ['total_tokens', 'Tokens', 'asc'], ['total_tokens', 'Tokens', 'desc'],
      ['cost', 'Total Cost', 'asc'], ['cost', 'Total Cost', 'desc'],
    ] as const) {
      let sortedFirst: TracePage;
      const expected = [...eligible].sort((a, b) => b.start > a.start ? 1 : b.start < a.start ? -1 : b.seeded.traceId.localeCompare(a.seeded.traceId));
      const value = (c: Case) => column === 'cost' ? c.cost : c.total;
      expected.sort((a, b) => direction === 'asc' ? value(a) - value(b) : value(b) - value(a));
      await test.step(`B: real ${direction} ${label} sort request and first page`, async () => {
        await page.locator('.clean-data-table:visible .ag-center-cols-viewport').hover();
        await page.mouse.wheel(1800, 0);
        sortedFirst = await capturePage(() => page.getByRole('button', { name: `Sort ${label}`, exact: true }).click(),
          r => filtered(r) && JSON.parse(wire(r).sort_params || '[]')[0]?.direction === direction
            && JSON.parse(wire(r).sort_params || '[]')[0]?.column_id === column);
        expect(lastQuery.cursor).toBeUndefined();
        expect(ids(sortedFirst.result.table)).toEqual(expected.slice(0, PAGE_SIZE).map(c => c.seeded.traceId));
        expect(sortedFirst.result.metadata.total_rows).toBe(ELIGIBLE);
      }, { timeout: UI_READY });
      await test.step(`B: ${direction} second page and stable return`, async () => {
        const second = await capturePage(() => pagerButton(page, 'Next page').click());
        expect([...ids(sortedFirst.result.table), ...ids(second.result.table)]).toEqual(expected.map(c => c.seeded.traceId));
        await pagerButton(page, 'Previous page').click();
        await waitForCurrentPage(page, 1);
        await expect.poll(() => gridIds(page), { timeout: UI_READY }).toEqual(ids(sortedFirst.result.table));
      }, { timeout: UI_READY });
    }
    await testInfo.attach('filtered-sorted-request', { body: JSON.stringify(lastQuery), contentType: 'application/json' });
    for (const key of ['tiny', 'small', 'zero', 'nested', 'parent-only', 'equal-calls', 'root-content']) {
      const c = cases.find(c => c.key === key)!;
      await test.step(`C: ${key} trace and span precision`, async () => {
        await page.goto(`/dashboard/observe/${projectId}/llm-tracing?tab=traces`, { waitUntil: 'domcontentloaded' });
        const detailResponse = page.waitForResponse(r => new URL(r.url()).pathname === `/tracer/trace/${c.seeded.traceId}/` && r.request().method() === 'GET', { timeout: UI_READY });
        const row = page.locator(`.clean-data-table:visible .ag-center-cols-container .ag-row[row-id="${c.seeded.traceId}"]`);
        await expect(row).toBeVisible({ timeout: UI_READY });
        if (c.display) {
          await page.locator('.clean-data-table:visible .ag-center-cols-viewport').hover();
          await page.mouse.wheel(1800, 0);
          await expect(row.locator('[col-id="cost"]')).toHaveText(c.display, { timeout: UI_READY });
          await testInfo.attach(`cost-${key}-desktop`, { body: await page.screenshot(), contentType: 'image/png' });
        }
        await page.locator('.clean-data-table:visible .ag-center-cols-viewport').hover();
        await page.mouse.wheel(-10000, 0);
        await row.locator('[col-id="trace_name"]').click();
        const response = await detailResponse; expect(response.status()).toBe(200);
        const body = await response.json();
        expect(body.result.summary.total_tokens).toBe(c.total); near(body.result.summary.total_cost, c.cost);
        // DrawerHeader renders the full trace ID. Scope all detail assertions
        // there: the still-mounted list must not satisfy a cost assertion.
        const drawer = page.locator('.MuiDrawer-paper:visible').filter({
          has: page.getByText(c.seeded.traceId, { exact: true }),
        });
        await expect(drawer.getByText(c.seeded.spanIds[0], { exact: true })).toBeVisible({ timeout: UI_READY });
        // TraceTreeV2 toggles the selected root off on a second click; the
        // no-selection panel then renders the trace's canonical summary.
        const rootName = c.shape === 'nested' || c.shape === 'parent-only' ? 'pydantic_ai.agent.run' : `${prefix}-${c.key}`;
        await drawer.getByText(rootName, { exact: true }).first().click();
        await expect(drawer.getByText('Select a span to view details', { exact: true })).toBeVisible({ timeout: UI_READY });
        await expect(drawer.getByText(`${c.total} tokens`, { exact: true })).toBeVisible({ timeout: UI_READY });
        if (c.display) {
          await expect(drawer.getByText(c.display, { exact: true })).toBeVisible({ timeout: UI_READY });
          // The summary graph repeats the name; the first match is the tree
          // row. The selected span-ID assertion below proves the target.
          await drawer.getByText('e2e.llm-call', { exact: true }).first().click();
          await expect(drawer.getByText(c.seeded.spanIds[1], { exact: true })).toBeVisible({ timeout: UI_READY });
          // SpanDetailPane.MetricChip renders label and value in one text node.
          await expect(drawer.getByText(`Cost : ${c.display}`, { exact: true })).toBeVisible({ timeout: UI_READY });
          await testInfo.attach(`span-${key}-desktop`, { body: await page.screenshot(), contentType: 'image/png' });
        }
        const walk = (entries: any[]): any[] => entries.flatMap(entry => [entry.observation_span, ...walk(entry.children)]);
        const spans = walk(body.result.observation_spans);
        expect(spans.map(s => s.id).sort()).toEqual(c.seeded.spanIds.slice().sort());
        if (c.shape === 'or') near(spans.find(s => s.observation_type === 'llm').cost, c.cost);
        await testInfo.attach(`detail-${key}`, { body: JSON.stringify(body.result), contentType: 'application/json' });
      }, { timeout: UI_READY });
    }
    await test.step('C/D: session history uses the first generation messages and canonical precise usage', async () => {
      await page.goto(`/dashboard/observe/${projectId}/sessions`, { waitUntil: 'domcontentloaded' });
      const sessionId = source.find(r => r.trace_id === cases[0].seeded.traceId)!.trace_session_id!;
      const sessionRows = await actor.api.get<{ result: { table: { session_id: string; total_cost: number; total_tokens: number; first_message: string; last_message: string }[] } }>(SESSIONS,
        { project_id: projectId, page_size: 25, page_number: 0 });
      const sessionRow = sessionRows.result.table.find(r => r.session_id === sessionId)!;
      const shared = cases.filter(c => c.session === session);
      expect([sessionRow.first_message, sessionRow.last_message]).toEqual([shared[0].input, shared[shared.length - 1].input]);
      near(sessionRow.total_cost, shared.reduce((sum, c) => sum + c.cost, 0));
      expect(sessionRow.total_tokens).toBe(shared.reduce((sum, c) => sum + c.total, 0));
      const pending = page.waitForResponse(r => new URL(r.url()).pathname === `/tracer/trace-session/${sessionId}/query/` && r.request().method() !== 'OPTIONS', { timeout: UI_READY });
      await page.locator(`.ag-row[row-id="${sessionId}"] [col-id="session_id"]:visible`).click();
      const history = await (await pending).json();
      expect(history.result.response.map((r: any) => r.trace_id)).toEqual(shared.map(c => c.seeded.traceId));
      expect(history.result.response.map((r: any) => [r.input, r.output])).toEqual(shared.map(c => [c.input, c.output]));
      const drawer = page.locator('.MuiDrawer-paper:visible').filter({
        has: page.getByRole('tablist', { name: 'session drawer tabs' }),
      });
      // The session header uses the same cost formatter as the trace drawer;
      // 11 + 10 + 9 microdollars must not expose Float64 accumulation noise.
      await expect(drawer.getByText('Total Cost: $0.00003', { exact: true })).toBeVisible({ timeout: UI_READY });
      for (const c of shared) {
        for (const message of [c.input, c.output]) {
          const bubble = drawer.getByText(message, { exact: true });
          await bubble.scrollIntoViewIfNeeded();
          await expect(bubble).toBeVisible({ timeout: UI_READY });
        }
      }
      await testInfo.attach('session-history-desktop', { body: await page.screenshot(), contentType: 'image/png' });
      for (const c of cases.filter(c => c.display)) {
        const sid = source.find(r => r.trace_id === c.seeded.traceId)!.trace_session_id!;
        near(sessionRows.result.table.find(r => r.session_id === sid)!.total_cost, c.cost);
      }
    }, { timeout: UI_READY });
    await test.step('D: Users UI/API equals exact stored identities', async () => {
      const expected = [orUser, plainUser].sort();
      const stored = await probe.ch<{ user_id: string }>('SELECT user_id FROM end_users FINAL WHERE project_id = {p:UUID} AND is_deleted = 0 ORDER BY user_id', { p: projectId });
      expect(stored.map(r => r.user_id)).toEqual(expected);
      const api = await actor.api.get<{ result: { table: { user_id: string }[] } }>(USERS, { project_id: projectId, page_size: 25 });
      expect(api.result.table.map(r => r.user_id).sort()).toEqual(expected);
      await page.goto(`/dashboard/observe/${projectId}/users`, { waitUntil: 'domcontentloaded' });
      await expect.poll(async () => (await page.locator('.ag-row [col-id="user_id"]:visible').allTextContents()).map(text => text.trim()).sort(), { timeout: UI_READY }).toEqual(expected);
    }, { timeout: UI_READY });
    await testInfo.attach('final-observe', { body: await page.screenshot(), contentType: 'image/png' });
  } finally { await req.dispose(); }
});
