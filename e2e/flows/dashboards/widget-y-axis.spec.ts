import { request, type APIRequestContext, type Page } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import type { TestActor } from '../../lib/provisioning';
import { POLL, type StateProbe } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { randomBytes, randomUUID } from 'node:crypto';

// playwright.config.ts caps `expect` at 10s, which a widget's first paint can
// outrun: the SPA boots, the dashboard detail loads, and only then does the
// widget fire its own ClickHouse-backed query. Same constant, same reason, as
// the observe flows.
const UI_READY = 60_000;
// WidgetChart enables ApexCharts animations at speed 400 (WidgetChart.jsx
// `animations: { enabled: true, easing: "easeinout", speed: 400 }`), so markers
// slide into place after the axis is already final. Marker assertions are read
// once, after this settle — never polled, because a polled "no marker is
// clipped" would pass on a mid-animation frame and prove nothing.
const ANIMATION_SETTLE = 1_000;

// The seeded shape, chosen so every expected axis number is arithmetic rather
// than observation. Two one-minute buckets:
//
//   bucket A (4 min ago)  9 root spans, latencies [7043, 11 x 8]
//   bucket B (2 min ago)  2 root spans, latencies [219, 11]
//
//   latency/max     -> [7043, 219]   drives the LEFT axis
//   span_count      -> [9, 2]        a deliberately tiny series
//   trace_count     -> [9, 2]        driven onto the RIGHT axis
//
// getAutoYAxisBounds on [219 .. 7043] with tickAmount 5: floor 219 is under
// 0.3 x peak so the axis is zero-anchored, step = niceCeil(7043/5) =
// niceCeil(1408.6) = 1500, max = 1500 x 5 = 7500. That 7500 is the whole point
// of TH-7680 — ApexCharts' own {1,2,5,10} ladder rounds the step to 2000 and
// draws a 10000 axis over a 7043 peak.
const PEAK_MS = 7043;
const TROUGH_MS = 219;
const FILLER_MS = 11;
const BUCKET_A_LATENCIES = [PEAK_MS, ...Array<number>(8).fill(FILLER_MS)];
const BUCKET_B_LATENCIES = [TROUGH_MS, FILLER_MS];
const SEEDED_SPANS = BUCKET_A_LATENCIES.length + BUCKET_B_LATENCIES.length;
const AUTO_TICKS = [7500, 6000, 4500, 3000, 1500, 0];
const COUNT_TICKS = [10, 8, 6, 4, 2, 0];
// latency/max per bucket, newest bucket last — the exact numbers the axis is
// derived from, asserted in the API lane so a seeding drift is diagnosable
// there instead of showing up as a mystery axis.
const EXPECTED_LATENCY_SERIES = [PEAK_MS, TROUGH_MS];
const EXPECTED_COUNT_SERIES = [BUCKET_A_LATENCIES.length, BUCKET_B_LATENCIES.length];

/**
 * One OTLP export of `latencies.length` single-span traces, all sharing one
 * start instant so they land in the same `toStartOfMinute` bucket.
 *
 * Every span is a root (no parentSpanId) deliberately: the dashboard's
 * `latency` metric appends `(parent_span_id IS NULL OR parent_span_id = '')`
 * to its WHERE clause, so a child span would be silently absent from the peak.
 */
async function seedBucket(
  req: APIRequestContext,
  cfg: { collectorUrl: string; apiKey: string; secretKey: string; projectName: string },
  startMsAgo: number,
  latencies: number[],
): Promise<string[]> {
  const start = BigInt(Date.now() - startMsAgo) * 1_000_000n;
  const traceIds = latencies.map(() => randomUUID());
  const spans = latencies.map((ms, i) => ({
    traceId: traceIds[i].replaceAll('-', ''),
    spanId: randomBytes(8).toString('hex'),
    name: 'e2e.root',
    kind: 1,
    startTimeUnixNano: String(start),
    endTimeUnixNano: String(start + BigInt(ms) * 1_000_000n),
    attributes: [],
    status: { code: 1 },
  }));
  const stringAttr = (key: string, value: string) => ({ key, value: { stringValue: value } });
  const res = await req.post(`${cfg.collectorUrl}/v1/traces`, {
    headers: {
      'X-Api-Key': cfg.apiKey,
      'X-Secret-Key': cfg.secretKey,
      'Content-Type': 'application/json',
    },
    data: {
      resourceSpans: [{
        resource: {
          attributes: [
            stringAttr('project_name', cfg.projectName),
            stringAttr('service.name', cfg.projectName),
          ],
        },
        scopeSpans: [{ scope: { name: 'e2e-harness' }, spans }],
      }],
    },
  });
  if (res.status() >= 300) throw new Error(`collector ${res.status()}: ${await res.text()}`);
  return traceIds;
}

interface Seeded { projectId: string; traceIds: string[] }

/** Seed both buckets under a fresh project and wait for every span to be readable. */
async function seedLatencyBuckets(
  req: APIRequestContext,
  actor: TestActor,
  probe: StateProbe,
  projectName: string,
): Promise<Seeded> {
  const cfg = {
    collectorUrl: E2E.collectorUrl,
    apiKey: actor.apiKey,
    secretKey: actor.secretKey,
    projectName,
  };
  const traceIds = [
    ...(await seedBucket(req, cfg, 4 * 60_000, BUCKET_A_LATENCIES)),
    ...(await seedBucket(req, cfg, 2 * 60_000, BUCKET_B_LATENCIES)),
  ];
  const params = Object.fromEntries(traceIds.map((t, i) => [`t${i}`, t]));
  const placeholders = traceIds.map((_, i) => `{t${i}:String}`).join(',');
  await expect
    .poll(async () => {
      const rows = await probe.ch<{ n: string }>(
        `SELECT count() AS n FROM spans FINAL WHERE trace_id IN (${placeholders})`,
        params,
      );
      return Number(rows[0].n);
    }, POLL.SPAN_VISIBLE)
    .toBe(SEEDED_SPANS);

  const [{ project_id: projectId }] = await probe.ch<{ project_id: string }>(
    'SELECT DISTINCT project_id FROM spans FINAL WHERE trace_id = {t:String}',
    { t: traceIds[0] },
  );
  return { projectId, traceIds };
}

const systemMetric = (id: string, aggregation: string) => ({
  id,
  name: id,
  type: 'system_metric',
  source: 'traces',
  aggregation,
});

const queryConfig = (projectId: string, metrics: ReturnType<typeof systemMetric>[]) => ({
  workflow: 'observability',
  project_ids: [projectId],
  // One-minute buckets over a 30-minute window: the two seeded instants (2 and
  // 4 minutes back) land in separate buckets, and every other bucket comes back
  // null-padded and is skipped by getSeriesExtent.
  time_range: { preset: '30m' },
  granularity: 'minute',
  metrics,
});

/** The widget editor's own axis defaults, in the snake_case the API stores. */
const axisPayload = (overrides: Record<string, unknown> = {}) => ({
  visible: true,
  label: '',
  unit: '',
  prefix_suffix: 'prefix',
  // Plain integers on the axis: `abbreviation` would render 7500 as "7.50K" and
  // the default 2 decimals as "7500.00". Neither is what this flow is about.
  abbreviation: false,
  decimals: 0,
  min: '',
  max: '',
  out_of_bounds: 'visible',
  scale: 'linear',
  ...overrides,
});

const chartConfig = ({
  leftY = {},
  rightY = {},
  seriesAxis = {},
  visibleSeries = null,
}: {
  leftY?: Record<string, unknown>;
  rightY?: Record<string, unknown>;
  seriesAxis?: Record<string, string>;
  visibleSeries?: string[] | null;
} = {}) => ({
  chart_type: 'line',
  axis_config: {
    left_y: axisPayload(leftY),
    // The editor's right-axis default: hidden, and "Out of Bounds: Hidden".
    right_y: axisPayload({ visible: false, out_of_bounds: 'hidden', ...rightY }),
    x_axis: { visible: true, label: '' },
    series_axis: seriesAxis,
  },
  visible_series: visibleSeries,
});

interface RawChart {
  gridTop: number;
  gridBottom: number;
  canvasMid: number;
  axes: { left: number; labels: string[] }[];
  series: { markers: number[] }[];
}
interface Chart {
  gridTop: number;
  gridBottom: number;
  canvasMid: number;
  axes: { left: number; ticks: number[] }[];
  series: { markers: number[] }[];
}

/**
 * Read the rendered axis out of the SVG rather than trusting the config.
 *
 * Only axis groups that actually carry labels are returned: ApexCharts emits a
 * `.apexcharts-yaxis` group for every `yaxis` entry including the ones with
 * `show: false`, and those render empty. They come back sorted left to right
 * because their DOM order is not: an opposite axis is emitted before or after
 * the primary one depending on which series index carries it, so the sort is
 * what makes "axes[0] is the left-hand axis" true. Marker circles are grouped
 * per series in `chartSeries` order — the order of `query_config.metrics` minus
 * anything `visible_series` hides.
 */
async function readChart(page: Page): Promise<Chart | null> {
  const raw = await page.evaluate((): RawChart | null => {
    // null, not a throw: the widget fires its query after the SPA boots and
    // ApexCharts tears the canvas down and rebuilds it on every re-render, so
    // "not there yet" is an ordinary state for the caller to poll through.
    const canvas = document.querySelector('.apexcharts-canvas');
    if (!canvas) return null;
    const grid = canvas.querySelector('.apexcharts-grid');
    if (!grid) return null;
    const canvasRect = canvas.getBoundingClientRect();
    const gridRect = grid.getBoundingClientRect();
    const centreY = (el: Element) => {
      const r = el.getBoundingClientRect();
      return Math.round(r.top + r.height / 2);
    };
    return {
      gridTop: Math.round(gridRect.top),
      gridBottom: Math.round(gridRect.bottom),
      canvasMid: Math.round((canvasRect.left + canvasRect.right) / 2),
      axes: [...canvas.querySelectorAll('.apexcharts-yaxis')]
        .map((g) => ({
          left: Math.round(g.getBoundingClientRect().left),
          labels: [...g.querySelectorAll('text')].map((t) => t.textContent ?? ''),
        }))
        .filter((a) => a.labels.length > 0),
      series: [...canvas.querySelectorAll('.apexcharts-series')].map((g) => ({
        markers: [...g.querySelectorAll('circle.apexcharts-marker')].map(centreY),
      })),
    };
  });
  if (!raw) return null;
  return {
    ...raw,
    axes: raw.axes
      .map((a) => ({ left: a.left, ticks: a.labels.map(tickValue) }))
      .sort((a, b) => a.left - b.left),
  };
}

/**
 * One tick label's numeric value.
 *
 * Each `<text>` holds both a `<tspan>` and a `<title>` with the same string, so
 * `textContent` comes back doubled ("7500" reads as "75007500"); the
 * backreference collapses that. What is left still carries the unit the
 * formatter prepends ("ms 7500"), which is a formatting concern, not an axis
 * one, so it is stripped before parsing.
 */
function tickValue(text: string): number {
  const once = text.replace(/(.+)\1/, '$1');
  const numeric = once.replace(/[^\d.-]/g, '');
  const n = Number(numeric);
  if (numeric === '' || !Number.isFinite(n)) throw new Error(`unparseable y-axis tick: ${text}`);
  return n;
}

/**
 * Distinct plotted heights of one series, top of the chart first.
 *
 * ApexCharts draws two concentric `circle.apexcharts-marker` elements per data
 * point, so the raw count is double the number of points; de-duplicating by
 * position keeps every assertion in terms of points.
 */
const plottedPoints = (chart: Chart, seriesIndex: number) =>
  [...new Set(chart.series[seriesIndex].markers)].sort((a, b) => a - b);

/** Points drawn above the plot area, i.e. data ApexCharts is clipping off the top. */
function clippedAboveGrid(chart: Chart): number {
  // 2px of slack: a point sitting exactly on the axis maximum lands on the grid
  // line, and its rounded centre can read one pixel high without being clipped.
  return chart.series
    .map((_, i) => plottedPoints(chart, i).filter((cy) => cy < chart.gridTop - 2).length)
    .reduce((a, b) => a + b, 0);
}

/**
 * Wait for the axis to reach `ticks`, then let the marker animation finish.
 *
 * Polling the ticks is what proves the widget's query resolved and re-rendered;
 * everything else is read from the single settled snapshot the caller gets back.
 */
async function chartWithTicks(page: Page, ticks: number[][]): Promise<Chart> {
  await expect
    .poll(async () => (await readChart(page))?.axes.map((a) => a.ticks) ?? null, {
      timeout: UI_READY,
    })
    .toEqual(ticks);
  await page.waitForTimeout(ANIMATION_SETTLE);
  const chart = await readChart(page);
  if (!chart) throw new Error('chart disappeared after its axis settled');
  return chart;
}

interface QueryEnvelope {
  result: { metrics: { id: string; series: { name: string; data: { value: number | null }[] }[] }[] };
}
interface WidgetEnvelope { result: { id: string } }
interface DashboardEnvelope { result: { id: string } }
// GET .../widgets/{id}/ is plain DRF and returns the widget unwrapped, unlike
// every other dashboard route. Asserted through this shape on purpose.
interface WidgetDetail {
  chart_config: {
    axis_config: {
      left_y: Record<string, unknown>;
      right_y: Record<string, unknown>;
      series_axis: Record<string, string>;
    };
    visible_series: string[] | null;
  };
}

/** Non-null values of one metric's only series, in bucket order. */
const seriesValues = (body: QueryEnvelope, metricId: string) => {
  const metric = body.result.metrics.find((m) => m.id === metricId);
  if (!metric) throw new Error(`metric ${metricId} missing from query result`);
  const [series] = metric.series;
  return series.data.map((p) => p.value).filter((v): v is number => v !== null);
};

interface Fixture {
  projectId: string;
  dashboardId: string;
  widgetId: string;
  query: ReturnType<typeof queryConfig>;
}

/** Seed the data, then a dashboard holding exactly one widget over it. */
async function seedWidget(
  req: APIRequestContext,
  actor: TestActor,
  probe: StateProbe,
  name: string,
  metrics: ReturnType<typeof systemMetric>[],
  chart: ReturnType<typeof chartConfig>,
): Promise<Fixture> {
  const { projectId } = await seedLatencyBuckets(req, actor, probe, `${name}-project`);
  const query = queryConfig(projectId, metrics);
  const dashboard = await actor.api.post<DashboardEnvelope>('/tracer/dashboard/', {
    name,
    description: 'TH-7680 y-axis scaling',
  });
  const dashboardId = dashboard.result.id;
  const widget = await actor.api.post<WidgetEnvelope>(`/tracer/dashboard/${dashboardId}/widgets/`, {
    name,
    description: '',
    query_config: query,
    chart_config: chart,
    width: 12,
    height: 320,
    position: 0,
  });
  return { projectId, dashboardId, widgetId: widget.result.id, query };
}

const uniqueName = (flow: string, testInfo: { workerIndex: number }) =>
  `e2e-${flow}-${testInfo.workerIndex}-${Date.now().toString(36)}`;

test('DASH-E2E-001: widget y-axis fits its data unless a bound is typed', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DASH-E2E-001',
    area: 'dashboards',
    userGoal:
      'A user reading a dashboard widget gets a y-axis sized to the data, and can override it by typing a Threshold Bound',
    steps: [
      'seed traces whose per-minute latency peaks are 7043 ms and 219 ms',
      'create a dashboard holding one latency widget with no typed bounds',
      'open the dashboard and read the rendered y-axis',
      'save a Threshold Bound maximum of 10000 on the widget and re-read the axis',
      'save a non-numeric maximum and re-read the axis',
    ],
    backendChecks: [
      'the widget query returns exactly the seeded per-bucket peaks (7043, 219) for latency/max',
      'chart_config.axis_config.left_y round-trips each typed maximum through the widget detail endpoint',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Collector round-trip (SPAN_VISIBLE, 15s), an SPA boot and three
  // UI_READY-budgeted chart renders chain past the 120s project default; the
  // observe flows set 240s for less.
  test.setTimeout(240_000);
  const req = await request.newContext();
  const name = uniqueName('dash1', testInfo);
  const metrics = [systemMetric('latency', 'max')];
  const fixture = await seedWidget(req, actor, probe, name, metrics, chartConfig());

  await test.step('API: the widget query returns the peaks the axis is derived from', async () => {
    const body = await actor.api.post<QueryEnvelope>('/tracer/dashboard/query/', fixture.query);
    expect(seriesValues(body, 'latency')).toEqual(EXPECTED_LATENCY_SERIES);
  });

  await page.goto(`/dashboard/dashboards/${fixture.dashboardId}`, { waitUntil: 'domcontentloaded' });

  await test.step('UI: with no typed bound the axis fits the 7043 peak', async () => {
    // 7500, not the 10000 ApexCharts' own ladder produces. One axis only —
    // the right axis is not visible.
    const chart = await chartWithTicks(page, [AUTO_TICKS]);
    expect(clippedAboveGrid(chart)).toBe(0);
  });

  await test.step('UI: a typed maximum wins over the fitted one', async () => {
    await actor.api.patch(`/tracer/dashboard/${fixture.dashboardId}/widgets/${fixture.widgetId}/`, {
      chart_config: chartConfig({ leftY: { max: '10000' } }),
    });
    await page.reload({ waitUntil: 'domcontentloaded' });
    // 10000 is above the peak, so "Out of Bounds: Visible" has nothing to widen
    // and the typed bound is used exactly as given.
    const chart = await chartWithTicks(page, [[10000, 8000, 6000, 4000, 2000, 0]]);
    expect(clippedAboveGrid(chart)).toBe(0);

    const detail = await actor.api.get<WidgetDetail>(
      `/tracer/dashboard/${fixture.dashboardId}/widgets/${fixture.widgetId}/`,
    );
    expect(detail.chart_config.axis_config.left_y.max).toBe('10000');
  });

  await test.step('UI: a non-numeric maximum is ignored, not passed through as NaN', async () => {
    await actor.api.patch(`/tracer/dashboard/${fixture.dashboardId}/widgets/${fixture.widgetId}/`, {
      chart_config: chartConfig({ leftY: { max: 'abc' } }),
    });
    await page.reload({ waitUntil: 'domcontentloaded' });
    // Back to the fitted axis. The marker count is asserted alongside because a
    // NaN bound does not produce a wrong axis — it produces no chart at all, and
    // an axis-only assertion would not tell those apart.
    const chart = await chartWithTicks(page, [AUTO_TICKS]);
    expect(chart.series).toHaveLength(1);
    expect(plottedPoints(chart, 0)).toHaveLength(EXPECTED_LATENCY_SERIES.length);
    expect(clippedAboveGrid(chart)).toBe(0);

    const detail = await actor.api.get<WidgetDetail>(
      `/tracer/dashboard/${fixture.dashboardId}/widgets/${fixture.widgetId}/`,
    );
    expect(detail.chart_config.axis_config.left_y.max).toBe('abc');
  });

  await req.dispose();
});

test('DASH-E2E-002: Out of Bounds decides whether a typed bound clips the data', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DASH-E2E-002',
    area: 'dashboards',
    userGoal:
      'A user who typed a Threshold Bound tighter than their data chooses whether the chart widens to keep every point visible or clips at the bound',
    steps: [
      'seed traces whose per-minute latency peaks are 7043 ms and 219 ms',
      'create a dashboard holding one latency widget whose maximum is 5000 and Out of Bounds is Visible',
      'open the dashboard and read the axis and the plotted points',
      'save the same maximum with Out of Bounds set to Hidden and re-read',
      'clear the maximum, save a minimum of 1000, and re-read',
    ],
    backendChecks: [
      'the widget query returns exactly the seeded per-bucket peaks (7043, 219) for latency/max',
      'chart_config.axis_config.left_y round-trips out_of_bounds and the typed min/max for each setting',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(240_000);
  const req = await request.newContext();
  const name = uniqueName('dash2', testInfo);
  const metrics = [systemMetric('latency', 'max')];
  const fixture = await seedWidget(req, actor, probe, name, metrics,
    chartConfig({ leftY: { max: '5000', out_of_bounds: 'visible' } }));

  await test.step('API: the widget query returns the peaks the bounds are compared against', async () => {
    const body = await actor.api.post<QueryEnvelope>('/tracer/dashboard/query/', fixture.query);
    expect(seriesValues(body, 'latency')).toEqual(EXPECTED_LATENCY_SERIES);
  });

  await page.goto(`/dashboard/dashboards/${fixture.dashboardId}`, { waitUntil: 'domcontentloaded' });

  await test.step('UI: Visible widens a maximum that would cut the peak off', async () => {
    // The typed 5000 sits under the 7043 peak, so it is dropped in favour of the
    // fitted 7500 and nothing is clipped.
    const chart = await chartWithTicks(page, [AUTO_TICKS]);
    expect(clippedAboveGrid(chart)).toBe(0);
  });

  await test.step('UI: Hidden keeps the same maximum as a hard cap and clips', async () => {
    await actor.api.patch(`/tracer/dashboard/${fixture.dashboardId}/widgets/${fixture.widgetId}/`, {
      chart_config: chartConfig({ leftY: { max: '5000', out_of_bounds: 'hidden' } }),
    });
    await page.reload({ waitUntil: 'domcontentloaded' });
    const chart = await chartWithTicks(page, [[5000, 4000, 3000, 2000, 1000, 0]]);
    // Same typed bound, opposite outcome: exactly one of the two seeded points —
    // the 7043 peak — is now drawn above the plot area, and the 219 one is not.
    expect(clippedAboveGrid(chart)).toBe(1);
    expect(plottedPoints(chart, 0)).toHaveLength(EXPECTED_LATENCY_SERIES.length);

    const detail = await actor.api.get<WidgetDetail>(
      `/tracer/dashboard/${fixture.dashboardId}/widgets/${fixture.widgetId}/`,
    );
    expect(detail.chart_config.axis_config.left_y).toMatchObject({
      max: '5000',
      out_of_bounds: 'hidden',
    });
  });

  await test.step('UI: min and max resolve independently — a typed floor with a fitted ceiling', async () => {
    await actor.api.patch(`/tracer/dashboard/${fixture.dashboardId}/widgets/${fixture.widgetId}/`, {
      chart_config: chartConfig({ leftY: { min: '1000', max: '', out_of_bounds: 'hidden' } }),
    });
    await page.reload({ waitUntil: 'domcontentloaded' });
    // The floor is the typed 1000; the ceiling is still the fitted 7500, which
    // is what "per side" means. ApexCharts then spaces the five ticks between
    // them: 1000 + n x 1300.
    const chart = await chartWithTicks(page, [[7500, 6200, 4900, 3600, 2300, 1000]]);
    expect(clippedAboveGrid(chart)).toBe(0);

    const detail = await actor.api.get<WidgetDetail>(
      `/tracer/dashboard/${fixture.dashboardId}/widgets/${fixture.widgetId}/`,
    );
    expect(detail.chart_config.axis_config.left_y).toMatchObject({ min: '1000', max: '' });
  });

  await req.dispose();
});

test('DASH-E2E-003: a dual-axis widget keeps one scale per side and keeps it when a series is hidden', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DASH-E2E-003',
    area: 'dashboards',
    userGoal:
      'A user plotting a large and a small metric together assigns one of them to the right axis, reads both off their own scale, and keeps that layout after hiding a series',
    steps: [
      'seed traces giving a 7043 ms latency peak alongside per-minute counts of 9 and 2',
      'create a dashboard holding one widget over latency, span count and trace count',
      'assign the trace-count series to the right axis',
      'open the dashboard and read both axes and where every series is plotted',
      'save a visible-series list that hides the latency series, and re-read both axes',
    ],
    backendChecks: [
      'the widget query returns all three metrics in the configured order with the seeded values',
      'chart_config.axis_config.series_axis and chart_config.visible_series round-trip through the widget detail endpoint',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(240_000);
  const req = await request.newContext();
  const name = uniqueName('dash3', testInfo);
  const metrics = [
    systemMetric('latency', 'max'),
    systemMetric('span_count', 'count'),
    systemMetric('trace_count', 'count'),
  ];
  // series_axis is keyed by the index in the UNFILTERED series list, which is
  // exactly what the hidden-series step exercises.
  const dualAxis = { rightY: { visible: true }, seriesAxis: { '2': 'right' } };
  const fixture = await seedWidget(req, actor, probe, name, metrics, chartConfig(dualAxis));

  await test.step('API: all three metrics come back in order with the seeded values', async () => {
    const body = await actor.api.post<QueryEnvelope>('/tracer/dashboard/query/', fixture.query);
    expect(body.result.metrics.map((m) => m.id)).toEqual(['latency', 'span_count', 'trace_count']);
    expect(seriesValues(body, 'latency')).toEqual(EXPECTED_LATENCY_SERIES);
    expect(seriesValues(body, 'span_count')).toEqual(EXPECTED_COUNT_SERIES);
    expect(seriesValues(body, 'trace_count')).toEqual(EXPECTED_COUNT_SERIES);
  });

  await page.goto(`/dashboard/dashboards/${fixture.dashboardId}`, { waitUntil: 'domcontentloaded' });

  await test.step('UI: each side carries one shared scale', async () => {
    // Left axis 0..7500 (latency drives it), right axis 0..10 (the counts do).
    const chart = await chartWithTicks(page, [AUTO_TICKS, COUNT_TICKS]);
    const [left, right] = chart.axes;
    expect(left.left).toBeLessThan(chart.canvasMid);
    expect(right.left).toBeGreaterThan(chart.canvasMid);
    expect(chart.series).toHaveLength(3);

    // This is the regression the shared scale fixes. span_count (9 and 2) shares
    // the left axis with latency, so on a 0..7500 scale both of its points sit
    // within a couple of pixels of the plot floor. Given its own scale — what
    // ApexCharts does when a yaxis entry carries no bounds — those two points
    // would be spread across the plot instead.
    const plotHeight = chart.gridBottom - chart.gridTop;
    for (const cy of plottedPoints(chart, 1)) {
      expect(chart.gridBottom - cy).toBeLessThan(0.03 * plotHeight);
    }
    // The right-hand series carries the same two numbers and is read off its own
    // 0..10 scale, so there they are far apart — which is what makes the flat
    // left-hand pair evidence of a shared scale rather than of flat data.
    const rightPoints = plottedPoints(chart, 2);
    expect(rightPoints.at(-1)! - rightPoints[0]).toBeGreaterThan(0.5 * plotHeight);
    expect(clippedAboveGrid(chart)).toBe(0);
  });

  await test.step('UI: hiding a series keeps the right-axis assignment', async () => {
    // Series keys are `${metric.id}|${metric.aggregation}|${bucketName}`, and
    // with no breakdown the bucket is "total". Keeping the last two hides
    // latency, so the right-hand series moves from index 2 to index 1 in the
    // filtered list while series_axis still keys it at 2.
    await actor.api.patch(`/tracer/dashboard/${fixture.dashboardId}/widgets/${fixture.widgetId}/`, {
      chart_config: chartConfig({
        ...dualAxis,
        visibleSeries: ['span_count|count|total', 'trace_count|count|total'],
      }),
    });
    await page.reload({ waitUntil: 'domcontentloaded' });

    // Both remaining series are 9-and-2, so both sides fit to 0..10 — but there
    // must still be TWO axes, one of them opposite. Reading series_axis with the
    // filtered index puts everything on the left and leaves a single axis.
    const chart = await chartWithTicks(page, [COUNT_TICKS, COUNT_TICKS]);
    const [left, right] = chart.axes;
    expect(left.left).toBeLessThan(chart.canvasMid);
    expect(right.left).toBeGreaterThan(chart.canvasMid);
    expect(chart.series).toHaveLength(2);

    const detail = await actor.api.get<WidgetDetail>(
      `/tracer/dashboard/${fixture.dashboardId}/widgets/${fixture.widgetId}/`,
    );
    expect(detail.chart_config.axis_config.series_axis).toEqual({ '2': 'right' });
    expect(detail.chart_config.visible_series).toEqual([
      'span_count|count|total',
      'trace_count|count|total',
    ]);
  });

  await req.dispose();
});
