import { request, type Locator, type Page, type Request } from '@playwright/test';
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
  time_range?: { start: string; end: string }; query_refreshing?: boolean; query_completed_at?: string;
  metrics?: { id: string; series: { data: { value: number | null }[] }[] }[] };
type QueryBody = { result?: QueryResult };
type Seen = { url: URL; input: QueryInput; raw: string; receipt: Receipt<unknown>; at: number };
type Dispatch = { requestId: string; url: string; method: string; raw: string; metric: Metric; range: string; timestamp: number };

const rangeKey = (input: QueryInput) => {
  const tr = input.time_range ?? {};
  return tr.preset ? `preset:${tr.preset}` : `${tr.custom_start}|${tr.custom_end}`;
};
const total = (result: QueryResult | undefined, metric: Metric) =>
  (result?.metrics?.find(m => m.id === metric)?.series ?? [])
    .flatMap(s => s.data).reduce((sum, p) => sum + (p.value ?? 0), 0);
const isExact = (result: QueryResult | undefined) =>
  result?.query_complete === true && result?.query_exact === true && result?.query_refreshing !== true;

function chip(page: Page, label: string): Locator {
  return page.locator('.MuiChip-root').filter({ has: page.getByText(label, { exact: true }) });
}

// A non-UTC, non-DST zone distinguishes local midnight from UTC midnight.
test.use({ timezoneId: 'Asia/Kolkata' });

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
      'each completed, exact 30D response spans the expected start and end and contains 3 traces or 6 spans',
      'custom Done sends exactly one range per widget, [local midnight today, local midnight tomorrow), dispatched under 450 ms after Done',
      'the custom-range responses are complete and exact with the seeded totals',
      'Refresh sends exactly one POST ?refresh=true per widget with the custom range, and a later response completes exactly with query_completed_at after the click and the seeded totals; Refreshing returns to Refresh and Last updated shows that completion',
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

  // Yesterday at noon UTC (17:30 browser-local): never a future trace or a midnight seed.
  const anchor = new Date(Date.now() - DAY_MS);
  anchor.setUTCHours(12, 0, 0, 0);
  const bounds30 = { start: anchor.getTime() - 30 * DAY_MS, end: anchor.getTime() };
  const key30 = `${new Date(bounds30.start).toISOString()}|${anchor.toISOString()}`;
  const ingest = await request.newContext();
  const seeded = [];
  try {
    for (let i = 0; i < 3; i++) {
      const start = BigInt(anchor.getTime() - 60_000) * 1_000_000n;
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
  const seen: Seen[] = [], dispatches: Dispatch[] = [], attributionErrors: string[] = [];
  const pendingRequests = new Map<Request, string>();
  const signature = (url: string, method: string, raw: string) => JSON.stringify([url, method, raw]);
  const isQuery = (url: URL, method: string) => method === 'POST' &&
    url.origin === new URL(E2E.apiUrl).origin && url.pathname === QUERY;
  const mine = (input: QueryInput, metric: Metric) =>
    input?.project_ids?.length === 1 && input.project_ids[0] === projectId && input.metrics?.[0]?.id === metric;
  page.on('request', req => {
    const url = new URL(req.url());
    if (!isQuery(url, req.method())) return;
    // captureAnnotationHttp's listener runs first and appends this very event's receipt synchronously.
    const receipt = http.receipts.at(-1), raw = req.postData();
    if (!receipt || seen.at(-1)?.receipt === receipt || receipt.path !== QUERY || receipt.method !== req.method() || !raw) {
      attributionErrors.push('no matching Playwright query receipt'); return;
    }
    const key = signature(req.url(), req.method(), raw);
    if ([...pendingRequests.values()].includes(key)) attributionErrors.push('overlapping identical query occurrences');
    pendingRequests.set(req, key);
    try {
      const input = req.postDataJSON();
      if (!METRICS.some(m => mine(input, m)) || JSON.stringify(input) !== JSON.stringify(receipt.input)) {
        attributionErrors.push('unattributed Playwright query receipt'); return;
      }
      seen.push({ url, raw, input, receipt, at: receipt.startedAt });
    } catch { attributionErrors.push('unreadable Playwright query'); }
  });
  const forget = (req: Request) => { pendingRequests.delete(req); };
  page.on('requestfinished', forget);
  page.on('requestfailed', forget);
  // Start both streams before navigation, including all occurrences of repeated polling requests.
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Performance.enable');
  cdp.on('Network.requestWillBeSent', ({ requestId, request, timestamp }) => {
    if (!isQuery(new URL(request.url), request.method)) return;
    try {
      const raw = request.postData ?? Buffer.concat((request.postDataEntries ?? [])
        .map(e => Buffer.from(e.bytes ?? '', 'base64'))).toString();
      const input: QueryInput = JSON.parse(raw), metric = METRICS.find(m => mine(input, m));
      if (!metric || !(input.time_range?.preset || (input.time_range?.custom_start && input.time_range.custom_end))) {
        attributionErrors.push(`unattributed CDP query ${requestId}`); return;
      }
      dispatches.push({ requestId, url: request.url, method: request.method, raw, timestamp, metric, range: rangeKey(input) });
    } catch { attributionErrors.push(`unreadable CDP query ${requestId}`); }
  });
  await cdp.send('Network.enable', { maxPostDataSize: 65536 });
  const pageErrors: string[] = [];
  page.on('pageerror', err => pageErrors.push(err.message));
  // Runner wall clock: receipt filtering only; never compared to a browser duration.
  const queries = (metric: Metric, since: number) => {
    expect(attributionErrors, 'every query has an unambiguous receipt').toEqual([]);
    return (http.receipts as Receipt<QueryBody>[]).filter(r => r.path === QUERY && r.method === 'POST' &&
      r.startedAt >= since && mine(r.input as QueryInput, metric));
  };
  const distinctRanges = (metric: Metric, since: number) =>
    [...new Set(queries(metric, since).map(r => rangeKey(r.input as QueryInput)))];
  /** Latest settled receipt for a widget's range, reduced to what the checks compare. */
  const latest = (metric: Metric, since: number, key: string) => {
    const r = queries(metric, since).filter(q => q.settled && rangeKey(q.input as QueryInput) === key).at(-1);
    const result = r?.body?.result;
    return r && { status: r.status, exact: isExact(result), total: total(result, metric),
      bounds: result?.time_range && { start: Date.parse(result.time_range.start), end: Date.parse(result.time_range.end) },
      completedAt: result?.query_completed_at ?? null };
  };
  const expectedTotal = (metric: Metric) =>
    metric === 'trace_count' ? EXPECTED_TRACES : EXPECTED_TRACES * EXPECTED_SPANS_PER_TRACE;
  const settledExact = (metric: Metric, since: number, key: string, bounds?: typeof bounds30) =>
    expect.poll(() => latest(metric, since, key), { ...POLL.ASYNC_JOB,
      message: `${metric} ${key} completes exactly` })
      .toMatchObject({ status: 200, exact: true, total: expectedTotal(metric), ...(bounds ? { bounds } : {}) });

  const loadStart = Date.now();
  await page.goto(`/dashboard/dashboards/${dashboardId}`, { waitUntil: 'domcontentloaded' });

  await test.step('UI: the dashboard opens on Default and both widgets load their stored 7D range', async () => {
    await uiExpect(chip(page, 'Default')).toHaveClass(PRIMARY);
    for (const metric of METRICS) {
      await uiExpect.poll(() => distinctRanges(metric, loadStart)).toEqual(['preset:7D']);
      await settledExact(metric, loadStart, 'preset:7D');
    }
  });

  // NavigationStart is in CDP monotonic seconds. Click and highlight are already
  // in milliseconds since that origin. Bind the browser clock before fixing Date.
  const origin = async () => (await cdp.send('Performance.getMetrics')).metrics
    .find(m => m.name === 'NavigationStart')?.value ?? NaN;
  const navigationStart = await origin();
  expect(Number.isFinite(navigationStart) && navigationStart > 0, 'valid NavigationStart').toBe(true);
  const sameNavigation = async () => expect(await origin(), 'navigation changed; clock origin reset').toBe(navigationStart);
  const navTime = (n: number, label: string) => {
    expect(Number.isFinite(n) && n >= 0, `missing, nonfinite or negative ${label}: ${n}`).toBe(true);
    return n;
  };
  const timing = await page.evaluateHandle(() => ({ nav: performance.now.bind(performance), click: NaN }));
  const chip30 = chip(page, '30D');
  const highlight = await chip30.evaluateHandle((el, t) => {
    const state = { at: NaN };
    const observer = new MutationObserver(() => {
      if (el.classList.contains('MuiChip-colorPrimary')) {
        state.at = t.nav();
        observer.disconnect();
      }
    });
    observer.observe(el, { attributes: true, attributeFilter: ['class'] });
    return state;
  }, timing);
  await page.clock.setFixedTime(anchor);
  const readNav = async (read: () => Promise<number>, label: string) => {
    await sameNavigation(); // Check before evaluating a handle bound to the original document.
    return navTime(await read(), label);
  };
  const sinceNav = () => readNav(() => timing.evaluate(t => t.nav()), 'window start');
  const clickDispatch = async (target: Locator) => {
    await sameNavigation();
    await target.evaluate((el, t) => {
      t.click = NaN;
      el.addEventListener('click', () => { t.click = t.nav(); }, { capture: true, once: true });
    }, timing);
    await target.click();
    return readNav(() => timing.evaluate(t => t.click), 'click');
  };
  const dispatchNav = (d: Dispatch) => navTime((d.timestamp - navigationStart) * 1000, `dispatch ${d.requestId}`);
  // Within each exact URL/method/body, match the nth CDP event to the nth
  // Playwright event. Simultaneous indistinguishable requests are an error.
  const correlated = async () => {
    await sameNavigation();
    const cdpKey = (d: Dispatch) => signature(d.url, d.method, d.raw);
    const seenKey = (s: Seen) => signature(s.url.href, s.receipt.method, s.raw);
    await uiExpect.poll(() => dispatches.map(cdpKey).sort().join('\n') === seen.map(seenKey).sort().join('\n'),
      { message: 'CDP and Playwright query occurrences agree' }).toBe(true);
    expect(attributionErrors, 'every query has an unambiguous receipt').toEqual([]);
    expect(new Set(dispatches.map(d => d.requestId)).size, 'unique CDP request IDs').toBe(dispatches.length);
    const counts = new Map<string, number>();
    return dispatches.map(d => {
      const key = cdpKey(d), index = counts.get(key) ?? 0;
      counts.set(key, index + 1);
      const { receipt } = seen.filter(s => seenKey(s) === key)[index];
      return { requestId: d.requestId, url: d.url, method: d.method, input: receipt.input, metric: d.metric,
        range: d.range, occurrence: index + 1, dispatchNavMs: dispatchNav(d) };
    });
  };
  const firstNav = async (metric: Metric, range: string, since: number) => {
    await sameNavigation();
    const matches = (d: Dispatch) => d.metric === metric && d.range === range && dispatchNav(d) >= since;
    await uiExpect.poll(() => attributionErrors[0] ?? dispatches.some(matches),
      { message: `no attributed ${metric} ${range} dispatch` }).toBe(true);
    const window = (await correlated()).filter(d => d.metric === metric && d.range === range && d.dispatchNavMs >= since);
    await attach(`dispatch-${metric}-${range === key30 ? 'burst' : 'custom'}`, window);
    return navTime(Math.min(...window.map(d => d.dispatchNavMs)), 'first attributed dispatch');
  };
  let burstStart = 0, burstNav = NaN, clickedNav30 = NaN, highlightedNav30 = NaN;
  await test.step('UI: Today, 7D and 30D clicked back to back; 30D highlights before any 30D query', async () => {
    burstStart = Date.now(); // Runner-clock receipt filter.
    burstNav = await sinceNav();
    await chip(page, 'Today').click();
    await chip(page, '7D').click();
    clickedNav30 = await clickDispatch(chip30);
    await expect(chip30).toHaveClass(PRIMARY);
    highlightedNav30 = await readNav(() => highlight.evaluate(state => state.at), '30D primary transition');
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
      await attach(`burst-${metric}`, sent.map(r => ({ input: r.input, runnerStartedAt: r.startedAt })));
      // Exactly one range on the wire, and it is the 30-day one: Today and 7D never left the browser.
      expect(distinctRanges(metric, burstStart), `${metric} burst ranges`).toHaveLength(1);
      expect(distinctRanges(metric, burstStart)).toEqual([key30]);
      const first = await firstNav(metric, key30, burstNav);
      await attach(`ordering-${metric}`, { navigationStart, highlightedNav30, clickedNav30, dispatchNavMs: first });
      expect(first, `${metric} 30D query after the highlight`).toBeGreaterThan(highlightedNav30);
      expect(first - clickedNav30, `${metric} 30D query waits out the debounce`).toBeGreaterThanOrEqual(DEBOUNCE_FLOOR_MS);
    }
  });

  await test.step('API: both 30D responses complete exactly with the seeded totals', async () => {
    for (const metric of METRICS) {
      await settledExact(metric, burstStart, key30, bounds30);
      await attach(`completed-30D-${metric}`, latest(metric, burstStart, key30));
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
  await attach('calendar', { anchor: anchor.toISOString(), seed: new Date(anchor.getTime() - 60_000).toISOString(), ...today });
  const customKey = `${today.start}|${today.end}`;
  let doneAt = 0, doneNav = NaN, clickedNavDone = NaN;
  await test.step("UI: a custom range of today's date applies on Done", async () => {
    await chip(page, 'Custom').click();
    await page.getByLabel('Start Date').fill(today.input);
    await page.getByLabel('End Date').fill(today.input);
    const done = page.getByRole('button', { name: 'Done', exact: true });
    await expect(done).toBeEnabled();
    doneAt = Date.now();
    doneNav = await sinceNav();
    clickedNavDone = await clickDispatch(done);
    await expect(chip(page, `${today.label} - ${today.label}`)).toHaveClass(PRIMARY);
  });

  await test.step('API: Done sends exactly the custom range per widget, dispatched at once', async () => {
    for (const metric of METRICS) {
      const custom = () => queries(metric, doneAt).filter(r => rangeKey(r.input as QueryInput) === customKey);
      await uiExpect.poll(() => custom().length, { message: `${metric} custom query sent` }).toBeGreaterThan(0);
      const sent = queries(metric, doneAt);
      await attach(`custom-${metric}`, sent.map(r => ({ input: r.input, runnerStartedAt: r.startedAt })));
      // Dispatch time first: a debounced Done would only send the range ~500 ms later.
      const elapsed = await firstNav(metric, customKey, doneNav) - clickedNavDone;
      expect(elapsed, `${metric} custom query follows Done`).toBeGreaterThanOrEqual(0);
      expect(elapsed, `${metric} custom query is not debounced`).toBeLessThan(CUSTOM_DISPATCH_MS);
      expect(distinctRanges(metric, doneAt), `${metric} custom ranges`).toEqual([customKey]);
    }
    for (const metric of METRICS) await settledExact(metric, doneAt, customKey);
  });

  let refreshAt = 0;
  await test.step('UI + API: Refresh sends one refresh=true query per widget and completes', async () => {
    const refresh = page.getByRole('button', { name: 'Refresh', exact: true });
    await expect(refresh).toBeEnabled();
    const observed = await refresh.evaluateHandle(el => {
      const state = { refreshing: false };
      const observer = new MutationObserver(() => {
        if (el.textContent?.trim() === 'Refreshing' && (el as HTMLButtonElement).disabled) {
          state.refreshing = true;
          observer.disconnect();
        }
      });
      observer.observe(el, { attributes: true, childList: true, subtree: true, characterData: true });
      return state;
    });
    refreshAt = Date.now();
    await refresh.click();
    const refreshes = (metric: Metric) =>
      seen.filter(s => s.at >= refreshAt && s.url.searchParams.get('refresh') === 'true' && mine(s.input, metric));
    for (const metric of METRICS) {
      await uiExpect.poll(() => refreshes(metric).length, { message: `${metric} refresh=true sent` }).toBeGreaterThan(0);
    }
    await uiExpect.poll(() => observed.evaluate(state => state.refreshing)).toBe(true);
    for (const metric of METRICS) {
      await expect.poll(() => {
        const r = latest(metric, refreshAt, customKey);
        return r && { ...r, afterClick: r.completedAt !== null && Date.parse(r.completedAt) >= refreshAt };
      }, { ...POLL.ASYNC_JOB, message: `${metric} refresh completes exactly after the click` })
        .toMatchObject({ status: 200, exact: true, total: expectedTotal(metric), afterClick: true });
    }
    await uiExpect(refresh).toBeEnabled();
    const completed = Math.max(...METRICS.map(metric => Date.parse(latest(metric, refreshAt, customKey)!.completedAt!)));
    const label = await page.evaluate(ms => {
      const d = new Date(ms);
      return `Last updated ${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}, ${d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}`;
    }, completed);
    await uiExpect(page.getByText(label, { exact: true })).toBeVisible();
    await attach('refresh-completion', { label, completed, refreshAtRunnerEpochMs: refreshAt });
    for (const metric of METRICS) {
      const sent = refreshes(metric);
      await attach(`refresh-${metric}`, sent.map(s => ({ url: s.url.search, input: s.input, runnerStartedAt: s.at })));
      expect(sent.map(s => rangeKey(s.input)), `${metric} refresh=true queries`).toEqual([customKey]);
    }
  });

  await attach('timings-nav-ms', { navigationStartSeconds: navigationStart, burstNav, clickedNav30,
    highlightedNav30, doneNav, clickedNavDone, dispatches: await correlated() });
  await Promise.all([...http.pending]);
  http.stop();
  expect(pageErrors, 'no page errors').toEqual([]);
});
