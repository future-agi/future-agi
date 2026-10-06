import { request, type Request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// TraceGrid.buildParams and PrimaryGraph.queryFn, via utils/axios.js endpoints.
const LIST_PATH = '/tracer/trace/list_traces_of_session/';
const GRAPH_PATH = '/tracer/trace/get_graph_methods/';
const VALUES_PATH = '/tracer/dashboard/filter_values/';
const UI_READY = 60_000;
type Filter = { column_id: string; filter_config: { filter_type: string; filter_op: string;
  filter_value: unknown[]; attribute_value_types?: string[] } };

test('OBS-E2E-005: discovered attributes filter traces and graph traffic consistently', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-005', area: 'observe',
    userGoal: 'Select typed attribute suggestions and see the same exact traces in the list and graph',
    steps: ['ingest two traces with string and numeric customer IDs',
      'discover both typed values', 'select each value in trace view',
      'select both values and then a nonexistent value', 'clear the filter and refresh'],
    backendChecks: ['observed customer ID suggestions preserve string and number types',
      'both authoritative trace identities belong to the seeded project',
      'each UI filter returns the exact trace IDs and matching span traffic and latency, including multiple and zero matches'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // ASYNC_JOB 60 + SPAN_VISIBLE 15 + four UI_READY 240 + 45s headroom.
  test.setTimeout(360_000);
  page.setDefaultTimeout(UI_READY);
  const req = await request.newContext();
  try {
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const projectName = `e2e-obs5-${suffix}`;
    const cfg = { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
      secretKey: actor.secretKey, projectName };
    const stringName = `e2e-string-${suffix}`;
    const numberName = `e2e-number-${suffix}`;
    const start = BigInt(Date.now()) * 1_000_000n;
    const stringTrace = await sendTrace(req, { ...cfg, rootName: stringName,
      startTimeUnixNano: start, endTimeUnixNano: start + 50_000_000n,
      rootAttributes: { customer_id: '00123456' } });
    const numberTrace = await sendTrace(req, { ...cfg, rootName: numberName,
      startTimeUnixNano: start, endTimeUnixNano: start + 120_000_000n,
      rootAttributes: { customer_id: 123456 } });
    await testInfo.attach('seeded-traces', { body: JSON.stringify({ stringTrace, numberTrace }),
      contentType: 'application/json' });

    await expect.poll(() => probe.ch<{ id: string }>(
      'SELECT id FROM traces FINAL WHERE id IN ({a:UUID}, {b:UUID}) ORDER BY toString(id)',
      { a: stringTrace.traceId, b: numberTrace.traceId }), POLL.SPAN_VISIBLE)
      .toEqual([stringTrace.traceId, numberTrace.traceId].sort().map(id => ({ id })));
    const projects = await probe.pg<{ id: string }>(
      'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2 AND workspace_id = $3',
      [projectName, actor.organizationId, actor.workspaceId]);
    expect(projects).toHaveLength(1);
    const projectId = projects[0].id;
    expect(await probe.ch<{ project_id: string }>(
      'SELECT DISTINCT project_id FROM traces FINAL WHERE id IN ({a:UUID}, {b:UUID})',
      { a: stringTrace.traceId, b: numberTrace.traceId })).toEqual([{ project_id: projectId }]);
    await expect.poll(async () => {
      const body = await actor.api.post<{ result: { values: { value: unknown; type: string }[] } }>(
        VALUES_PATH, { property_id: 'custom_attribute:customer_id', source: 'traces',
          project_ids: projectId, page_size: 25 });
      return body.result.values.map(v => `${v.type}:${JSON.stringify(v.value)}`).sort();
    }, POLL.ASYNC_JOB).toEqual(['number:123456', 'string:"00123456"']);

    // LLMTracingView uses selectedTab=trace for curated trace rows and their graph.
    await page.goto(`/dashboard/observe/${projectId}/llm-tracing?selectedTab=trace`,
      { waitUntil: 'domcontentloaded' });
    const traceNames = page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]');
    const allNames = [stringName, numberName].sort();
    await expect.poll(async () => (await traceNames.allTextContents()).sort(), { timeout: UI_READY })
      .toEqual(allNames);
    const cases = [
      { name: 'string', values: ['00123456'], typedValues: ['00123456'], types: ['string'],
        ids: [stringTrace.traceId], names: [stringName], spans: stringTrace.spanIds.length, latency: 50 },
      { name: 'number', values: ['123456'], typedValues: [123456], types: ['number'],
        ids: [numberTrace.traceId], names: [numberName], spans: numberTrace.spanIds.length, latency: 120 },
      { name: 'both', values: ['00123456', '123456'],
        typedValues: ['00123456', 123456], types: ['string', 'number'],
        ids: [stringTrace.traceId, numberTrace.traceId], names: allNames,
        spans: stringTrace.spanIds.length + numberTrace.spanIds.length, latency: 85 },
      { name: 'missing', values: [`absent-${suffix}`], typedValues: [`absent-${suffix}`],
        types: ['string'], ids: [], names: [], spans: 0, latency: 0 },
    ];
    for (const scenario of cases) {
      await page.getByRole('button', { name: 'Filter', exact: true }).click();
      if (scenario.name !== 'string') {
        // TraceFilterPanel.handleClear calls onClose after clearing the rows.
        await page.getByRole('button', { name: 'Clear all', exact: true }).click();
        await expect(page.getByRole('tab', { name: 'Basic', exact: true })).toBeHidden({ timeout: UI_READY });
        await page.getByRole('button', { name: 'Filter', exact: true }).click();
      }
      await page.getByRole('button', { name: 'Property', exact: true }).first().click();
      await page.getByPlaceholder('Search properties...').fill('customer_id');
      await page.locator('[data-filter-property-option="customer_id"]').click();
      await page.locator('[data-filter-value-trigger="customer_id"]').click();

      // Pin the actual requests made by this UI, not a separately invented filter.
      const matchesSelection = (outgoing: Request) => {
        // TraceGrid serializes filters before readQuery moves params to POST;
        // PrimaryGraph sends its filter array directly. Captured on this stack.
        const filters: Filter[] = outgoing.url().includes(GRAPH_PATH)
          ? outgoing.postDataJSON().filters
          : JSON.parse(outgoing.method() === 'POST' ? outgoing.postDataJSON().filters
            : new URL(outgoing.url()).searchParams.get('filters')!);
        const values = filters.find(f => f.column_id === 'customer_id')?.filter_config.filter_value;
        return values !== undefined && JSON.stringify(values.map(String).sort())
          === JSON.stringify([...scenario.values].sort());
      };
      const listReady = page.waitForResponse(r => r.url().includes(LIST_PATH)
        && r.request().method() !== 'OPTIONS' && matchesSelection(r.request()), { timeout: UI_READY });
      const graphReady = page.waitForResponse(async r => r.url().includes(GRAPH_PATH)
        && r.request().method() === 'POST' && matchesSelection(r.request())
        && r.ok() && (await r.json()).result.query_complete === true, { timeout: UI_READY });
      for (const value of scenario.values) {
        await page.getByPlaceholder('Search values...').fill(value);
        // Suggestions have checkbox semantics; the separate "+ Specify" row
        // creates a string while remote search is pending. Do not select that
        // row when this step claims to select a typed catalog suggestion.
        const suggestionRole = scenario.name === 'missing' ? '' : '[role="checkbox"]';
        await page.locator(`[data-filter-value-option="${value}"]${suggestionRole}`).click();
      }
      await page.keyboard.press('Escape');
      // ValuePicker and TraceFilterPanel are nested MUI Popovers. The first
      // Escape closes only the values; close the panel before its outer button.
      await expect(page.getByPlaceholder('Search values...')).toBeHidden({ timeout: UI_READY });
      await page.keyboard.press('Escape');
      await expect(page.getByRole('tab', { name: 'Basic', exact: true })).toBeHidden({ timeout: UI_READY });
      const [listResponse, graphResponse] = await Promise.all([listReady, graphReady]);
      expect(listResponse.status()).toBe(200);
      const list = await listResponse.json();
      const graph = await graphResponse.json();
      const applied = (graphResponse.request().postDataJSON().filters as Filter[])
        .find(filter => filter.column_id === 'customer_id')!;
      expect(applied.filter_config).toMatchObject({ filter_type: 'text', filter_op: 'in',
        filter_value: scenario.typedValues, attribute_value_types: scenario.types });
      await testInfo.attach(`filter-${scenario.name}`, { body: JSON.stringify({
        listRequest: listResponse.request().postData() ?? listResponse.request().url(),
        graphRequest: graphResponse.request().postDataJSON(), list, graph }), contentType: 'application/json' });
      expect(list.result.table.map((row: { trace_id: string }) => row.trace_id).sort())
        .toEqual([...scenario.ids].sort());
      expect(graph.result.query_status).toBe('complete');
      expect(graph.result.query_sampled).toBe(false);
      expect(graph.result.data.reduce((sum: number, point: { primary_traffic: number | null }) =>
        sum + (point.primary_traffic ?? 0), 0)).toBe(scenario.spans);
      // TimeSeriesQueryBuilder aggregates all spans of a matched trace. These
      // fresh two-span trees have one time bucket and distinct mean latencies.
      expect(graph.result.data.filter((point: { primary_traffic: number }) => point.primary_traffic > 0)
        .map((point: { value: number }) => point.value)).toEqual(scenario.spans ? [scenario.latency] : []);
      await expect.poll(async () => (await traceNames.allTextContents()).sort(), { timeout: UI_READY })
        .toEqual([...scenario.names].sort());
      await expect(page.locator('.apexcharts-canvas').first()).toBeVisible({ timeout: UI_READY });
    }
    await page.getByRole('button', { name: 'Filter', exact: true }).click();
    await page.getByRole('button', { name: 'Clear all', exact: true }).click();
    await expect(page.getByRole('tab', { name: 'Basic', exact: true })).toBeHidden({ timeout: UI_READY });
    await expect.poll(async () => (await traceNames.allTextContents()).sort(), { timeout: UI_READY })
      .toEqual(allNames);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect.poll(async () => (await traceNames.allTextContents()).sort(), { timeout: UI_READY })
      .toEqual(allNames);
  } finally {
    await req.dispose();
  }
});
