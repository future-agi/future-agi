import { request, type Locator, type Page } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { captureAnnotationHttp, type Receipt } from '../../lib/annotation-http';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// DashboardDetailView.jsx: a preset chip sets `datePreset` (the highlight) at
// once and `appliedDatePreset` (what the widgets query) after 500 ms; custom
// Done sets both at once. useDashboards.js posts every widget read to QUERY and
// adds `?refresh=true` only for a manual Refresh.
const QUERY = '/tracer/dashboard/query/';
const UI_READY = 60_000; // SPA boot + dashboard detail + each widget's own read.
const DEBOUNCE_FLOOR_MS = 450; // The 500 ms debounce, less timer slack.
const CUSTOM_DISPATCH_MS = 450; // Done must not wait out that same debounce.
const DAY_MS = 86_400_000;
const PRIMARY = /MuiChip-colorPrimary/;
// Assertion-only anchors (fallibility): never used by seeding or matchers.
const EXPECTED_TRACES = 3;
const EXPECTED_SPANS_PER_TRACE = 2; // lib/otlp sendTrace: one root + one child.

type Metric = 'trace_count' | 'span_count';
const METRICS: Metric[] = ['trace_count', 'span_count'];
type QueryInput = { project_ids?: string[]; metrics?: { id: string }[];
  time_range?: { preset?: string; custom_start?: string; custom_end?: string } };
type QueryResult = { query_complete?: boolean; query_exact?: boolean; query_status?: string;
  query_refreshing?: boolean; query_completed_at?: string;
  metrics?: { id: string; series: { data: { value: number | null }[] }[] }[] };
type QueryBody = { result?: QueryResult };
type Seen = { url: URL; input: QueryInput; at: number };

const rangeKey = (input: QueryInput) => {
  const tr = input.time_range ?? {};
  return tr.preset ? `preset:${tr.preset}` : `${tr.custom_start}|${tr.custom_end}`;
};
/** Whole days a custom range spans, so Today (<1), 7D (7) and 30D (30) are told apart. */
const rangeDays = (input: QueryInput) => {
  const tr = input.time_range ?? {};
  if (!tr.custom_start || !tr.custom_end) return null;
  return Math.round((Date.parse(tr.custom_end) - Date.parse(tr.custom_start)) / DAY_MS);
};
const total = (result: QueryResult | undefined, metric: Metric) =>
  (result?.metrics?.find(m => m.id === metric)?.series ?? [])
    .flatMap(s => s.data).reduce((sum, p) => sum + (p.value ?? 0), 0);
const isExact = (result: QueryResult | undefined) =>
  result?.query_complete === true && result?.query_exact === true && result?.query_refreshing !== true;

function chip(page: Page, label: string): Locator {
  return page.locator('.MuiChip-root').filter({ has: page.getByText(label, { exact: true }) });
}

test('DASH-E2E-018: rapid date presets query only the final range, custom Done and Refresh apply at once', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DASH-E2E-018', area: 'dashboards',
    userGoal: 'A user flicking through date presets sees each chip highlight as they click, while the widgets load only the range they land on; a custom range applies as soon as they press Done, and Refresh still brings the widgets to a completed result.',
    steps: [
      'seed 3 traces into a minted project and a dashboard with trace_count and span_count widgets stored at 7D',
      'open the dashboard on the Default chip and wait for both widgets to load',
      'click Today, 7D and 30D back to back',
      'see the 30D chip highlighted, then both widgets load the 30-day range with the seeded totals',
      "open Custom, type today's date as Start Date and End Date and press Done",
      'click Refresh with the custom range applied and wait for it to finish',
    ],
    backendChecks: [
      'the preset burst sends exactly one time range per widget on POST /tracer/dashboard/query/, the 30-day one; no Today or 7D range is ever sent, the 30D chip is primary before the first 30D query, and that query starts at least 450 ms after the 30D click',
      'each widget’s 30D response is query_complete and query_exact with 3 traces and 6 spans',
      'custom Done sends exactly one range per widget, [local midnight today, local midnight tomorrow), dispatched under 450 ms after Done',
      'the custom-range responses are complete and exact with the seeded totals',
      'Refresh sends exactly one POST ?refresh=true per widget with the custom range, and a later response completes exactly with query_completed_at after the click and the seeded totals',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Seed ~25 + load 60 + 3 stages × (UI_READY 60 + ASYNC_JOB 60) = 445 s worst case.
  test.setTimeout(480_000);
  const uiExpect = expect.configure({ timeout: UI_READY });
  const attach = (name: string, body: unknown) =>
    testInfo.attach(name, { contentType: 'application/json', body: JSON.stringify(body, null, 2) });
  const prefix = `e2e-dash18-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `${prefix}-project`;

  // ---- seed: 3 traces 60 s ago, so they fall inside Today, 7D and 30D ----
  const ingest = await request.newContext();
  const seeded = [];
  try {
    for (let i = 0; i < 3; i++) {
      const start = BigInt(Date.now() - 60_000) * 1_000_000n;
      seeded.push(await sendTrace(ingest, { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
        secretKey: actor.secretKey, projectName, rootName: `${prefix}-root-${i}`, childName: `${prefix}-child-${i}`,
        startTimeUnixNano: start, endTimeUnixNano: start + 50_000_000n }));
    }
  } finally { await ingest.dispose(); }
  const traceIds = seeded.map(s => s.traceId), spanIds = seeded.flatMap(s => s.spanIds);
  const params = Object.fromEntries(traceIds.map((t, i) => [`t${i}`, t]));
  const inList = traceIds.map((_, i) => `{t${i}:String}`).join(',');
  await expect.poll(async () => Number((await probe.ch<{ n: string }>(
    `SELECT count() AS n FROM spans FINAL WHERE trace_id IN (${inList})`, params))[0].n),
  POLL.SPAN_VISIBLE).toBe(spanIds.length);
  const [{ project_id: projectId }] = await probe.ch<{ project_id: string }>(
    'SELECT DISTINCT project_id FROM spans FINAL WHERE trace_id = {t0:String}', params);

  const dashboard = await actor.api.post<{ result: { id: string } }>('/tracer/dashboard/',
    { name: `${prefix}-dashboard`, description: 'DASH-E2E-018 preset burst' });
  const dashboardId = dashboard.result.id;
  const widgetIds: Record<string, string> = {};
  for (const [position, metric] of METRICS.entries()) {
    const widget = await actor.api.post<{ result: { id: string } }>(`/tracer/dashboard/${dashboardId}/widgets/`, {
      name: `${prefix}-${metric === 'trace_count' ? 'traces' : 'spans'}`, description: '',
      query_config: { workflow: 'observability', project_ids: [projectId], time_range: { preset: '7D' },
        granularity: 'day', metrics: [{ id: metric, name: metric, type: 'system_metric', source: 'traces', aggregation: 'count' }] },
      chart_config: { chart_type: 'line' }, width: 12, height: 320, position,
    });
    widgetIds[metric] = widget.result.id;
  }
  await attach('seeded-ids', { projectName, projectId, traceIds, spanIds, dashboardId, widgetIds,
    organizationId: actor.organizationId, workspaceId: actor.workspaceId });

  // ---- recording: receipts (bodies) plus raw requests (the refresh query param) ----
  const http = captureAnnotationHttp([page], E2E.apiUrl, attach);
  const seen: Seen[] = [];
  page.on('request', req => {
    const url = new URL(req.url());
    if (url.origin !== new URL(E2E.apiUrl).origin || url.pathname !== QUERY || req.method() !== 'POST') return;
    try { seen.push({ url, input: req.postDataJSON() as QueryInput, at: Date.now() }); } catch { /* not JSON */ }
  });
  const pageErrors: string[] = [];
  page.on('pageerror', err => pageErrors.push(err.message));
  const mine = (input: QueryInput, metric: Metric) =>
    input.project_ids?.length === 1 && input.project_ids[0] === projectId && input.metrics?.[0]?.id === metric;
  const queries = (metric: Metric, since: number) => (http.receipts as Receipt<QueryBody>[])
    .filter(r => r.path === QUERY && r.method === 'POST' && r.startedAt >= since && mine(r.input as QueryInput, metric));
  const distinctRanges = (metric: Metric, since: number) =>
    [...new Set(queries(metric, since).map(r => rangeKey(r.input as QueryInput)))];
  /** Latest settled receipt for a widget's range, reduced to what the checks compare. */
  const latest = (metric: Metric, since: number, key: string) => {
    const r = queries(metric, since).filter(q => q.settled && rangeKey(q.input as QueryInput) === key).at(-1);
    const result = r?.body?.result;
    return r && { status: r.status, exact: isExact(result), total: total(result, metric),
      completedAt: result?.query_completed_at ?? null };
  };
  const expectedTotal = (metric: Metric) =>
    metric === 'trace_count' ? EXPECTED_TRACES : EXPECTED_TRACES * EXPECTED_SPANS_PER_TRACE;
  const settledExact = (metric: Metric, since: number, key: string) =>
    expect.poll(() => latest(metric, since, key), { ...POLL.ASYNC_JOB,
      message: `${metric} ${key} completes exactly` })
      .toMatchObject({ status: 200, exact: true, total: expectedTotal(metric) });

  const loadStart = Date.now();
  await page.goto(`/dashboard/dashboards/${dashboardId}`, { waitUntil: 'domcontentloaded' });

  await test.step('UI: the dashboard opens on Default and both widgets load their stored 7D range', async () => {
    await uiExpect(chip(page, 'Default')).toHaveClass(PRIMARY);
    for (const metric of METRICS) {
      await uiExpect.poll(() => distinctRanges(metric, loadStart)).toEqual(['preset:7D']);
      await settledExact(metric, loadStart, 'preset:7D');
    }
  });

  const chip30 = chip(page, '30D');
  let burstStart = 0, clicked30 = 0, highlighted30 = 0;
  await test.step('UI: Today, 7D and 30D clicked back to back; 30D highlights before any 30D query', async () => {
    burstStart = Date.now(); // Every query from here on belongs to the burst.
    await chip(page, 'Today').click();
    await chip(page, '7D').click();
    clicked30 = Date.now();
    await chip30.click();
    await expect(chip30).toHaveClass(PRIMARY);
    highlighted30 = Date.now();
    await expect(chip(page, 'Today')).not.toHaveClass(PRIMARY);
    await expect(chip(page, '7D')).not.toHaveClass(PRIMARY);
  });

  await test.step('API: the burst sends only the 30-day range per widget, at least 450 ms after the click', async () => {
    for (const metric of METRICS) {
      await uiExpect.poll(() => queries(metric, burstStart).length, { message: `${metric} 30D query sent` })
        .toBeGreaterThan(0);
    }
    await Promise.all([...http.pending]);
    for (const metric of METRICS) {
      const sent = queries(metric, burstStart);
      await attach(`burst-${metric}`, sent.map(r => ({ input: r.input, startedAt: r.startedAt })));
      // Exactly one range on the wire, and it is the 30-day one: Today and 7D never left the browser.
      expect(distinctRanges(metric, burstStart), `${metric} burst ranges`).toHaveLength(1);
      expect(sent.map(r => rangeDays(r.input as QueryInput)), `${metric} burst range days`)
        .toEqual(sent.map(() => 30));
      // Ordering: the chip was already primary when the first 30D query left.
      expect(sent[0].startedAt, `${metric} 30D query after the highlight`).toBeGreaterThanOrEqual(highlighted30);
      expect(sent[0].startedAt - clicked30, `${metric} 30D query waits out the debounce`)
        .toBeGreaterThanOrEqual(DEBOUNCE_FLOOR_MS);
    }
  });

  await test.step('API: both 30D responses complete exactly with the seeded totals', async () => {
    for (const metric of METRICS) {
      await settledExact(metric, clicked30, distinctRanges(metric, clicked30)[0]);
    }
  });

  // The browser's own local midnight today and tomorrow: DatePicker.jsx handleCustomDate.
  const today = await page.evaluate(() => {
    const d = new Date(), pad = (n: number) => String(n).padStart(2, '0');
    const start = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const end = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1);
    return { input: `${start.getFullYear()}-${pad(start.getMonth() + 1)}-${pad(start.getDate())}`,
      label: `${start.toLocaleString('en-US', { month: 'short' })} ${pad(start.getDate())}`,
      start: start.toISOString(), end: end.toISOString() };
  });
  const customKey = `${today.start}|${today.end}`;
  let doneAt = 0;
  await test.step("UI: a custom range of today's date applies on Done", async () => {
    await chip(page, 'Custom').click();
    await page.getByLabel('Start Date').fill(today.input);
    await page.getByLabel('End Date').fill(today.input);
    const done = page.getByRole('button', { name: 'Done', exact: true });
    await expect(done).toBeEnabled();
    doneAt = Date.now();
    await done.click();
    await expect(chip(page, `${today.label} - ${today.label}`)).toHaveClass(PRIMARY);
  });

  await test.step('API: Done sends exactly the custom range per widget, dispatched at once', async () => {
    for (const metric of METRICS) {
      const custom = () => queries(metric, doneAt).filter(r => rangeKey(r.input as QueryInput) === customKey);
      await uiExpect.poll(() => custom().length, { message: `${metric} custom query sent` }).toBeGreaterThan(0);
      const sent = queries(metric, doneAt);
      await attach(`custom-${metric}`, sent.map(r => ({ input: r.input, startedAt: r.startedAt })));
      // Dispatch time first: a debounced Done would only send the range ~500 ms later.
      expect(custom()[0].startedAt - doneAt, `${metric} custom query is not debounced`).toBeLessThan(CUSTOM_DISPATCH_MS);
      expect(distinctRanges(metric, doneAt), `${metric} custom ranges`).toEqual([customKey]);
    }
    for (const metric of METRICS) await settledExact(metric, doneAt, customKey);
  });

  let refreshAt = 0;
  await test.step('UI + API: Refresh sends one refresh=true query per widget and completes', async () => {
    const refresh = page.getByRole('button', { name: 'Refresh', exact: true });
    await expect(refresh).toBeEnabled();
    refreshAt = Date.now();
    await refresh.click();
    const refreshes = (metric: Metric) =>
      seen.filter(s => s.at >= refreshAt && s.url.searchParams.get('refresh') === 'true' && mine(s.input, metric));
    for (const metric of METRICS) {
      await uiExpect.poll(() => refreshes(metric).length, { message: `${metric} refresh=true sent` }).toBeGreaterThan(0);
    }
    for (const metric of METRICS) {
      await expect.poll(() => {
        const r = latest(metric, refreshAt, customKey);
        return r && { ...r, afterClick: r.completedAt !== null && Date.parse(r.completedAt) >= refreshAt };
      }, { ...POLL.ASYNC_JOB, message: `${metric} refresh completes exactly after the click` })
        .toMatchObject({ status: 200, exact: true, total: expectedTotal(metric), afterClick: true });
    }
    await uiExpect(refresh).toBeEnabled();
    await uiExpect(page.getByText(/^Last updated /)).toBeVisible();
    for (const metric of METRICS) {
      const sent = refreshes(metric);
      await attach(`refresh-${metric}`, sent.map(s => ({ url: s.url.search, input: s.input, at: s.at })));
      expect(sent.map(s => rangeKey(s.input)), `${metric} refresh=true queries`).toEqual([customKey]);
    }
  });

  const firstAfter = (metric: Metric, since: number) => queries(metric, since)[0]?.startedAt - since;
  await attach('timings-ms', Object.fromEntries(METRICS.map(metric => [metric, {
    burstClickToFirst30D: firstAfter(metric, clicked30), highlightBeforeFirst30D: queries(metric, burstStart)[0]?.startedAt - highlighted30,
    doneToFirstCustom: firstAfter(metric, doneAt), refreshClickToFirst: firstAfter(metric, refreshAt) }])));
  await Promise.all([...http.pending]);
  http.stop();
  expect(pageErrors, 'no page errors').toEqual([]);
});
