import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned off the running app: the Observe span table's list request and the
// exact filter item its panel puts on the wire for the "Span Name" property.
const SPAN_LIST_PATH = '/tracer/observation-span/list_spans_observe/';
const SPAN_PAGE_SIZE = 25;
// Browser-side waits. The local stack slows several-fold when specs run in
// parallel; sized off this flow's whole-test wall time (~12s alone, ~48s beside
// one other spec — first paint was not measured separately), not the 10s
// expect default.
const UI_READY = 60_000;

const spanNameFilter = (value: string) => [{
  column_id: 'span_name',
  display_name: 'Span Name',
  filter_config: {
    filter_type: 'text', filter_op: 'in', filter_value: [value], col_type: 'SYSTEM_METRIC',
  },
}];

interface SpanRow { span_id: string; span_name: string }

test('OBS-E2E-002: span table filter matches the API for the same query', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-002', area: 'observe',
    userGoal: 'A developer narrows the span table to one operation and trusts the result set',
    steps: ['seed two traces with distinct root-span names',
            "open the project's span table by its span-view URL",
            'filter by one span name', 'read the filtered table'],
    backendChecks: ['all four spans of both traces present in CH `spans` (FINAL)',
                    'project row auto-created in PG tracer_project, scoped to the actor org',
                    'UI row set equals the span-list API result for the equivalent filter (same CH dispatch)'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Bounded waits chain to POLL.SPAN_VISIBLE + navigation + 3x UI_READY, past
  // the config's 120s default; without this a slow run ends as a bare timeout
  // instead of the assertion that actually ran out.
  test.setTimeout(240_000);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `e2e-obs2-${suffix}`;
  const alpha = `e2e.alpha-${suffix}`;
  const beta = `e2e.beta-${suffix}`;
  const cfg = { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
                secretKey: actor.secretKey, projectName };
  const a = await sendTrace(req, { ...cfg, rootName: alpha });
  const b = await sendTrace(req, { ...cfg, rootName: beta });

  await expect.poll(async () => {
    const rows = await probe.ch<{ n: string }>(
      'SELECT count() AS n FROM spans FINAL WHERE trace_id IN ({a:String}, {b:String})',
      { a: a.traceId, b: b.traceId });
    return Number(rows[0].n);
  }, POLL.SPAN_VISIBLE).toBe(a.spanIds.length + b.spanIds.length);

  const projects = await probe.pg<{ id: string }>(
    'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2',
    [projectName, actor.organizationId]);
  expect(projects).toHaveLength(1);
  const projectId = projects[0].id;

  const spanNames = page.locator('.ag-row [col-id="span_name"]');

  await test.step('UI: open the project span table', async () => {
    // The URL the app navigates to itself when the table is grouped by span
    // (LLMTracingView.jsx handleGroupByChange).
    await page.goto(`/dashboard/observe/${projectId}/llm-tracing?tab=traces&selectedTab=spans`,
      { waitUntil: 'domcontentloaded' });
    await expect(spanNames).toHaveCount(a.spanIds.length + b.spanIds.length, { timeout: UI_READY });
  });

  await test.step('UI: filter the span table down to one span name', async () => {
    const filtered = page.waitForResponse(
      (r) => r.url().includes(SPAN_LIST_PATH) && r.url().includes(alpha) && r.ok(),
      { timeout: UI_READY });
    await page.getByRole('button', { name: 'Filter' }).click();
    await page.getByRole('button', { name: 'Property' }).first().click();
    await page.locator('[data-filter-property-option="span_name"]').click();
    await page.locator('[data-filter-value-trigger="span_name"]').click();
    await page.locator(`[data-filter-value-option="${alpha}"]`).click();
    await page.keyboard.press('Escape');
    await filtered;

    // Exact list: the beta trace's spans are gone, the alpha root is all that stays.
    await expect(spanNames).toHaveText([alpha], { timeout: UI_READY });
  });

  await test.step('API: the same endpoint and filter returns the same row set', async () => {
    const rows = await probe.apiList<SpanRow>(SPAN_LIST_PATH, {
      project_id: projectId, page_number: 0, page_size: SPAN_PAGE_SIZE,
      filters: JSON.stringify(spanNameFilter(alpha)),
    });
    expect(rows.map((r) => r.span_id)).toEqual([a.spanIds[0]]);
    expect(await spanNames.allInnerTexts()).toEqual(rows.map((r) => r.span_name));
  });

  await req.dispose();
});
