import { request, type Request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Captured from TraceGrid.buildParams / SpanGrid.buildParams in the running Observe app.
const TRACES = '/tracer/trace/list_traces_of_session/';
const SPANS = '/tracer/observation-span/list_spans_observe/';
const UI_READY = 60_000;
type SpanRow = { id: string; parent_span_id: string; name: string; trace_name: string };
const wire = (r: Request): Record<string, unknown> => r.method() === 'POST'
  ? r.postDataJSON() : Object.fromEntries(new URL(r.url()).searchParams);

test('OBS-E2E-063: collector trace is found by its root Trace Name', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-063', area: 'observe',
    userGoal: 'A developer finds a new collector trace by its root name and sees that name on its spans',
    steps: ['send two distinct collector traces', 'open the new Observe project',
      'filter the trace list by a substring of the first root name',
      'open the spans view and filter by the root Trace Name; see root and child'],
    backendChecks: ['both collector traces have two spans in CH FINAL, with root and child stored trace_name values',
      'the UI trace-name filter reaches the trace-list endpoint and returns only the matching trace',
      'the UI spans Trace Name filter reaches the span-list endpoint and returns both spans of only the matching trace'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(300_000);
  page.setDefaultTimeout(UI_READY);
  const req = await request.newContext();
  try {
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const projectName = `e2e-obs63-${suffix}`;
    const rootA = `e2e-alpha-${suffix}`;
    const rootB = `e2e-zulu-${suffix}`;
    const cfg = { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
      secretKey: actor.secretKey, projectName };
    const a = await sendTrace(req, { ...cfg, rootName: rootA });
    const b = await sendTrace(req, { ...cfg, rootName: rootB });
    await testInfo.attach('seeded-traces', { body: JSON.stringify({ a, b, rootA, rootB, projectName }),
      contentType: 'application/json' });
    const rows = (traceId: string) => probe.ch<SpanRow>(
      'SELECT id, parent_span_id, name, trace_name FROM spans FINAL WHERE trace_id = {t:String} ORDER BY parent_span_id, id',
      { t: traceId });
    for (const trace of [a, b]) {
      await expect.poll(async () => (await rows(trace.traceId)).length, POLL.SPAN_VISIBLE).toBe(2);
    }
    // Schema 039 stores the explicit name on the root and an empty string on children;
    // readers resolve children through their root instead of persisting a dictionary lookup.
    for (const [trace, rootName] of [[a, rootA], [b, rootB]] as const) {
      const stored = await rows(trace.traceId);
      expect(stored.map(r => r.id).sort()).toEqual([...trace.spanIds].sort());
      expect(stored.find(r => r.name === rootName)).toMatchObject({
        id: trace.spanIds[0], parent_span_id: '', trace_name: rootName,
      });
      expect(stored.find(r => r.name === 'e2e.llm-call')).toMatchObject({
        id: trace.spanIds[1], parent_span_id: trace.spanIds[0], trace_name: '',
      });
    }
    const projects = await probe.pg<{ id: string }>(
      'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2',
      [projectName, actor.organizationId]);
    expect(projects).toHaveLength(1);
    const projectId = projects[0].id;
    await page.goto(`/dashboard/observe/${projectId}/llm-tracing?tab=traces&selectedTab=trace`,
      { waitUntil: 'domcontentloaded' });
    const names = page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]');
    await expect.poll(async () => (await names.allTextContents()).sort(), { timeout: UI_READY })
      .toEqual([rootA, rootB]);
    await page.getByRole('button', { name: 'Filter', exact: true }).click();
    await page.getByRole('button', { name: 'Property', exact: true }).first().click();
    await page.getByPlaceholder('Search properties...').fill('Trace Name');
    await page.locator('[data-filter-property-option="name"]').click();
    // TraceFilterPanel's text operator choices are equals/contains/... .
    await page.getByRole('combobox').last().click();
    await page.getByRole('option', { name: 'contains', exact: true }).click();
    const needle = `alpha-${suffix}`;
    await page.locator('[data-filter-value-trigger="name"]').click();
    await page.getByPlaceholder('Search values...').fill(needle);
    const filtered = page.waitForResponse(r => r.url().includes(TRACES) && r.ok() &&
      JSON.stringify(wire(r.request())).includes(needle), { timeout: UI_READY });
    await page.locator(`[data-filter-value-option="${needle}"]`).filter({ hasText: '+ Specify:' }).click();
    await page.keyboard.press('Escape');
    await page.keyboard.press('Escape');
    const response = await filtered;
    const filterRequest = wire(response.request());
    const parsed = JSON.parse(String(filterRequest.filters));
    await testInfo.attach('trace-filter-wire', { body: JSON.stringify({ request: filterRequest, response: await response.json() }),
      contentType: 'application/json' });
    expect(parsed).toEqual(expect.arrayContaining([expect.objectContaining({
      column_id: 'name', property_id: 'system_attribute:traces:trace_name',
      filter_config: expect.objectContaining({ filter_op: 'contains', filter_value: needle }),
    })]));
    expect((await response.json()).result.table.map((r: { trace_id: string }) => r.trace_id)).toEqual([a.traceId]);
    await expect(names).toHaveText([rootA], { timeout: UI_READY });

    // The exact span-list path is captured above. The root-name predicate must
    // resolve both members of the trace even though the child's stored name is empty.
    const spanFilter = [{ column_id: 'trace_name', display_name: 'Trace Name',
      filter_config: { filter_type: 'text', filter_op: 'in', filter_value: [rootA], col_type: 'SYSTEM_METRIC' } }];
    const matching = await probe.apiList<{ span_id: string; trace_id: string; trace_name: string }>(SPANS, {
      project_id: projectId, page_number: 0, page_size: 25, filters: JSON.stringify(spanFilter),
    });
    expect(matching.map(s => s.span_id).sort()).toEqual([...a.spanIds].sort());
    expect(matching.map(s => s.trace_id)).toEqual([a.traceId, a.traceId]);
    await testInfo.attach('span-filter-wire', { body: JSON.stringify({ spanFilter, matching }),
      contentType: 'application/json' });
    await page.goto(`/dashboard/observe/${projectId}/llm-tracing?tab=traces&selectedTab=spans`,
      { waitUntil: 'domcontentloaded' });
    const spanNames = page.locator('.clean-data-table:visible .ag-row [col-id="span_name"]');
    await expect.poll(async () => (await spanNames.allTextContents()).sort(), { timeout: UI_READY })
      .toEqual(['e2e.llm-call', 'e2e.llm-call', rootA, rootB].sort());
    await page.getByRole('button', { name: 'Filter', exact: true }).click();
    await page.getByRole('button', { name: 'Property', exact: true }).first().click();
    await page.getByPlaceholder('Search properties...').fill('Trace Name');
    await page.locator('[data-filter-property-option="trace_name"]').click();
    await page.locator('[data-filter-value-trigger="trace_name"]').click();
    await page.getByPlaceholder('Search values...').fill(rootA);
    const spanReady = page.waitForResponse(r => r.url().includes(SPANS) && r.ok() &&
      JSON.stringify(wire(r.request())).includes(rootA), { timeout: UI_READY });
    await page.locator(`[data-filter-value-option="${rootA}"]`).first().click();
    await page.keyboard.press('Escape');
    await page.keyboard.press('Escape');
    const spanResponse = await spanReady;
    const spanWire = wire(spanResponse.request());
    const spanFilters = JSON.parse(String(spanWire.filters));
    expect(spanFilters).toEqual(expect.arrayContaining([expect.objectContaining({
      column_id: 'trace_name', property_id: 'system_attribute:traces:trace_name',
      filter_config: expect.objectContaining({ filter_op: 'in', filter_value: [rootA] }),
    })]));
    const uiSpans = (await spanResponse.json()).result.table as { span_id: string; trace_id: string; span_name: string }[];
    expect(uiSpans.map(s => s.span_id).sort()).toEqual([...a.spanIds].sort());
    expect(uiSpans.map(s => s.trace_id)).toEqual([a.traceId, a.traceId]);
    await expect.poll(async () => (await spanNames.allTextContents()).sort(), { timeout: UI_READY })
      .toEqual(['e2e.llm-call', rootA].sort());
    await testInfo.attach('span-ui-wire', { body: JSON.stringify({ request: spanWire, uiSpans }),
      contentType: 'application/json' });
  } finally {
    await req.dispose();
  }
});
