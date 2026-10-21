import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace, type SeededTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// frontend/routes/paths.js + tracer/urls.py: production UI and API paths.
const METRIC_PAGE = '/dashboard/derived-metrics';
const METRICS = '/tracer/derived-metrics/';
const RUNS = '/tracer/derived-metric-runs/';
const MONITORS = '/tracer/user-alerts/';
const DASH_QUERY = '/tracer/dashboard/query/';
// metricForm.js + the safe formula grammar; used only as the user's input.
const FORMULA = 'ratio(resolved == 1 and escalated == 0, resolved >= 0)';
// README: one browser phase's first-paint wall, not the suite timeout.
const UI_READY = 60_000;
// Assertion-only fallibility anchors. None feeds a seed, formula, request,
// threshold or response matcher. Perturb each independently, then restore.
const EXPECTED_SOURCE_SPANS = 4; // backendCheck 1 -> 5 must fail.
const EXPECTED_FROZEN_UNITS = 2; // backendCheck 2 -> 3 must fail.
const EXPECTED_DASHBOARD_VALUE = 0.5; // backendCheck 3 -> 0.75 must fail.
const EXPECTED_ALERTS = 1; // backendCheck 4 -> 2 must fail.

// Wire shapes pinned from tracer/serializers/derived_metric.py and dashboard.py.
type Metric = { id: string; project_id: string; current_version: number;
  definition: { formula: string; definition_hash: string; selection_unit: string; weighting: string } };
type Run = { id: string; metric_id: string; metric_version: number; status: string; error: string;
  snapshot_id: string | null; result_id: string | null; window_start: string; window_end: string };
type Result = { group_key: string; value: number | null; numerator: number | null; denominator: number | null;
  status: string; unit_count: number; valid_count: number; missing_count: number; invalid_probability_count: number };
type ResultPage = { results: Result[]; next_cursor: string | null };
type DashboardResult = { result: { metrics: { series: { data: { timestamp: string; value: number | null }[] }[] }[] } };
type Span = { id: string; trace_id: string; project_id: string; trace_session_id: string;
  has_resolved: number; has_escalated: number; resolved: number; escalated: number };

test('DASH-E2E-017: an unweighted resolution formula persists, renders on a dashboard and records an alert', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DASH-E2E-017', area: 'dashboards',
    userGoal: 'Save an unweighted resolution-ratio formula, freeze a run, inspect its dashboard value and record a threshold alert.',
    steps: [
      'ingest two independently identified sessions with explicit numeric outcomes on both root and child spans',
      'create, validate and save a session-level resolution formula through Derived metrics',
      'create a static monitor, preview a closed window and run the saved formula',
      'inspect the persisted result and frozen one-row-per-session evidence',
      'add the saved run to a dashboard through the UI, then reload that dashboard',
      'verify the scoped monitor recorded one critical alert for the exact frozen run window',
    ],
    backendChecks: [
      'The exact four seeded spans and typed outcomes belong to the minted project in the actor organization/default workspace.',
      'The saved version and completed run own exactly two frozen session units, with persisted numerator 1, denominator 2 and value 0.5.',
      'The saved dashboard binding and its actual initial/reloaded query responses return the independent expected run value.',
      'The actor-scoped monitor records exactly one critical alert for this run window and marks this run as consumed.',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // 15s source visibility + 2*60s async jobs + 6*60s browser phases + 45s I/O.
  // This is standalone/unweighted; it does not claim sibling sampling integration.
  test.setTimeout(540_000);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `e2e-derived17-${suffix}`;
  const metricName = `${projectName}-metric`;
  const monitorName = `${projectName}-monitor`;
  const dashboardName = `${projectName}-dashboard`;
  // lib/otlp.ts supports stable timestamps. Seed already-aged data so the UI's
  // minute-precision closed-window end includes it; no clock control or sleeps.
  const start = BigInt(Date.now() - 5 * 60_000) * 1_000_000n;
  const traces: SeededTrace[] = [];
  const resultReads = new Set<string>();
  page.on('request', outgoing => {
    const pathname = new URL(outgoing.url()).pathname;
    if (outgoing.method() === 'GET' && /\/derived-metric-runs\/[^/]+\/results\/$/.test(pathname)) resultReads.add(pathname);
  });
  try {
    for (const outcome of [{ session: 'resolved', escalated: 0 }, { session: 'escalated', escalated: 1 }]) {
      // Identity keys match the collector contract pinned by DASH-E2E-008.
      const attributes = { 'session.id': `${projectName}-${outcome.session}`, 'user.id': `${projectName}-user`,
        resolved: 1, escalated: outcome.escalated };
      traces.push(await sendTrace(req, { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
        secretKey: actor.secretKey, projectName, rootName: `${projectName}-${outcome.session}`,
        rootAttributes: attributes, childAttributes: attributes, resourceAttributes: { project_type: 'observe' },
        startTimeUnixNano: start, endTimeUnixNano: start + 50_000_000n }));
    }
    await testInfo.attach('seeded-identities', { contentType: 'application/json',
      body: JSON.stringify({ projectName, metricName, monitorName, dashboardName, traces }) });

    const projectId = await test.step('backendCheck 1: exact source identities, values and project scope', async () => {
      await expect.poll(async () => Number((await probe.ch<{ n: string }>(
        'SELECT count() AS n FROM spans FINAL WHERE trace_id IN ({a:String}, {b:String})',
        { a: traces[0].traceId, b: traces[1].traceId }))[0].n), POLL.SPAN_VISIBLE).toBe(EXPECTED_SOURCE_SPANS);
      const projects = await probe.pg<{ id: string }>(
        // project_workspace_scope_q explicitly includes null-workspace legacy
        // projects when the actor is in their default workspace.
        'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2 AND (workspace_id = $3 OR workspace_id IS NULL) AND NOT deleted',
        [projectName, actor.organizationId, actor.workspaceId]);
      expect(projects).toHaveLength(1);
      const rows = await probe.ch<Span>(`SELECT id, trace_id, project_id, toString(trace_session_id) AS trace_session_id,
        mapContains(attrs_number, 'resolved') AS has_resolved, mapContains(attrs_number, 'escalated') AS has_escalated,
        attrs_number['resolved'] AS resolved, attrs_number['escalated'] AS escalated
        FROM spans FINAL WHERE trace_id IN ({a:String}, {b:String})`,
      { a: traces[0].traceId, b: traces[1].traceId });
      expect(rows.map(row => row.id).sort()).toEqual(traces.flatMap(trace => trace.spanIds).sort());
      expect([...new Set(rows.map(row => row.project_id))]).toEqual([projects[0].id]);
      expect(new Set(rows.map(row => row.trace_session_id)).size).toBe(2);
      for (const row of rows) {
        expect(row.has_resolved).toBe(1); expect(row.has_escalated).toBe(1);
        expect(row.resolved).toBe(1);
        expect(row.escalated).toBe(row.trace_id === traces[0].traceId ? 0 : 1);
      }
      return projects[0].id;
    });

    const metric = await test.step('UI: create, validate and save the formula', async () => {
      await page.goto(METRIC_PAGE, { waitUntil: 'domcontentloaded' });
      await expect(page.getByRole('heading', { name: 'Derived metrics', exact: true })).toBeVisible({ timeout: UI_READY });
      await page.getByLabel('Metric name', { exact: true }).fill(metricName);
      await page.getByLabel('Search projects', { exact: true }).fill(projectName);
      await page.getByLabel('Project', { exact: true }).click();
      await page.getByRole('option', { name: projectName, exact: true }).click({ timeout: UI_READY });
      await page.getByLabel('Selection unit', { exact: true }).click();
      await page.getByRole('option', { name: 'session', exact: true }).click();
      await page.getByLabel('Weighting', { exact: true }).click();
      await page.getByRole('option', { name: 'Unweighted · explicit probability 1', exact: true }).click();
      await page.getByLabel('Input 1 alias', { exact: true }).fill('resolved');
      await page.getByLabel('Input 1 attribute key', { exact: true }).fill('resolved');
      await page.getByLabel('Input 2 alias', { exact: true }).fill('escalated');
      await page.getByLabel('Input 2 attribute key', { exact: true }).fill('escalated');
      await page.getByLabel('Formula', { exact: true }).fill(FORMULA);
      const validation = page.waitForResponse(r => new URL(r.url()).pathname.endsWith(`${METRICS}validate/`) && r.request().method() === 'POST', { timeout: UI_READY });
      await page.getByRole('button', { name: 'Validate formula', exact: true }).click();
      expect((await validation).status()).toBe(200);
      const saving = page.waitForResponse(r => new URL(r.url()).pathname.endsWith(METRICS) && r.request().method() === 'POST', { timeout: UI_READY });
      await page.getByRole('button', { name: 'Save metric', exact: true }).click();
      const response = await saving;
      expect(response.status()).toBe(201);
      const saved: Metric = await response.json();
      expect(saved.project_id).toBe(projectId);
      expect(saved.current_version).toBe(1);
      expect(saved.definition.formula).toBe(FORMULA);
      expect(saved.definition.selection_unit).toBe('session');
      expect(saved.definition.weighting).toBe('none');
      expect(saved.definition.definition_hash).toMatch(/^[a-f0-9]{64}$/);
      await expect(page.getByText('Saved version 1.', { exact: true })).toBeVisible({ timeout: UI_READY });
      return saved;
    }, { timeout: UI_READY });

    await test.step('UI: create the monitor and preview the closed window', async () => {
      await page.getByLabel('Monitor name', { exact: true }).fill(monitorName);
      await page.getByLabel('Threshold', { exact: true }).fill('0.75');
      const creating = page.waitForResponse(r => new URL(r.url()).pathname.endsWith(MONITORS) && r.request().method() === 'POST', { timeout: UI_READY });
      await page.getByRole('button', { name: 'Create monitor', exact: true }).click();
      expect((await creating).status()).toBe(200);
      const previewing = page.waitForResponse(r => new URL(r.url()).pathname.endsWith(`${METRICS}${metric.id}/preview/`), { timeout: UI_READY });
      await page.getByRole('button', { name: 'Preview window', exact: true }).click();
      const previewResponse = await previewing;
      expect(previewResponse.status()).toBe(200);
      const preview: ResultPage = await previewResponse.json();
      expect(preview.results.map(row => [row.value, row.numerator, row.denominator, row.status])).toEqual([[0.5, 1, 2, 'ok']]);
    }, { timeout: UI_READY });

    const runId = await test.step('UI: run and persist the saved formula', async () => {
      const starting = page.waitForResponse(r => new URL(r.url()).pathname.endsWith(`${METRICS}${metric.id}/runs/`) && r.request().method() === 'POST', { timeout: UI_READY });
      await page.getByRole('button', { name: 'Run and persist', exact: true }).click();
      const response = await starting;
      expect(response.status()).toBe(202);
      const run: Run = await response.json();
      await testInfo.attach('metric-run', { body: JSON.stringify({ projectId, metricId: metric.id, runId: run.id }), contentType: 'application/json' });
      return run.id;
    }, { timeout: UI_READY });

    await expect.poll(async () => (await actor.api.get<Run>(`${RUNS}${runId}/`)).status, POLL.ASYNC_JOB).toBe('complete');
    const run = await actor.api.get<Run>(`${RUNS}${runId}/`);
    expect(run.metric_id).toBe(metric.id); expect(run.metric_version).toBe(1);
    expect(run.snapshot_id).not.toBeNull(); expect(run.result_id).not.toBeNull();

    await test.step('backendCheck 2: versioned persistence, exact unit population and sufficient statistics', async () => {
      const versions = await probe.pg<{ definition_hash: string; formula: string; version: number }>(
        `SELECT v.definition_hash, v.formula, v.version FROM tracer_derivedmetricversion v
         JOIN tracer_derivedmetric m ON v.metric_id = m.id JOIN tracer_derivedmetricrun r ON r.definition_id = v.id
         WHERE r.id = $1 AND m.id = $2 AND m.project_id = $3 AND m.organization_id = $4 AND m.workspace_id = $5`,
      [runId, metric.id, projectId, actor.organizationId, actor.workspaceId]);
      expect(versions).toEqual([{ definition_hash: metric.definition.definition_hash, formula: FORMULA, version: 1 }]);
      // These tables are immutable MergeTree attempt rows, not CDC/RMT: FINAL
      // is intentionally used only for the source spans and commit pointer.
      const frozen = await probe.ch<{ unit_id: string; source_rows: string }>(
        `SELECT unit_id, source_rows FROM derived_metric_inputs WHERE org_id = {o:UUID} AND workspace_id = {w:UUID}
         AND project_id = {p:UUID} AND snapshot_id = {s:UUID} ORDER BY unit_id`,
      { o: actor.organizationId, w: actor.workspaceId, p: projectId, s: run.snapshot_id! });
      expect(frozen).toHaveLength(EXPECTED_FROZEN_UNITS);
      expect(new Set(frozen.map(row => row.unit_id)).size).toBe(EXPECTED_FROZEN_UNITS);
      expect(frozen.map(row => Number(row.source_rows))).toEqual([2, 2]);
      const persisted = await actor.api.get<ResultPage>(`${RUNS}${runId}/results/`, { rollup: 'overall' });
      expect(persisted.results).toEqual([{ group_key: '', value: 0.5, numerator: 1, denominator: 2, status: 'ok',
        unit_count: EXPECTED_FROZEN_UNITS, valid_count: 2, missing_count: 0, invalid_probability_count: 0 }]);
      const commits = await probe.ch<{ result_id: string }>(
        'SELECT toString(result_id) AS result_id FROM derived_metric_commits FINAL WHERE run_id = {r:UUID} AND project_id = {p:UUID}',
      { r: runId, p: projectId });
      expect(commits).toEqual([{ result_id: run.result_id }]);
    });

    await test.step('UI: inspect the persisted result rather than a preview', async () => {
      await expect(page.getByRole('button', { name: 'Recompute frozen run', exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(page.getByRole('table', { name: 'Metric results' }).locator('tbody tr')).toHaveCount(1, { timeout: UI_READY });
      await expect(page.getByRole('table', { name: 'Metric results' }).locator('tbody td'))
        .toHaveText(['All units', '0.5', '1', '2', 'ok', '2', '2', '0', '0'], { timeout: UI_READY });
      expect([...resultReads].some(pathname => pathname.endsWith(`${RUNS}${runId}/results/`))).toBe(true);
    }, { timeout: UI_READY });

    const dashboardQuery = () => page.waitForResponse(r => {
      if (!new URL(r.url()).pathname.endsWith(DASH_QUERY) || r.request().method() !== 'POST') return false;
      const data = r.request().postDataJSON() as { metrics: { derived_run_id?: string }[] };
      return data.metrics.some(metricConfig => metricConfig.derived_run_id === runId);
    }, { timeout: UI_READY });
    const dashboardId = await test.step('UI/backendCheck 3: add the saved run to an actual dashboard', async () => {
      await page.getByLabel('New dashboard name', { exact: true }).fill(dashboardName);
      const querying = dashboardQuery();
      await page.getByRole('button', { name: 'Add run to dashboard', exact: true }).click();
      await expect(page).toHaveURL(/\/dashboard\/dashboards\/[0-9a-f-]+$/, { timeout: UI_READY });
      const response = await querying;
      expect(response.status()).toBe(200);
      const body: DashboardResult = await response.json();
      expect(body.result.metrics.map(metricResult => metricResult.series.flatMap(series => series.data.map(point => point.value))))
        .toEqual([[EXPECTED_DASHBOARD_VALUE]]);
      await expect(page.getByRole('heading', { name: new RegExp(`^${String(EXPECTED_DASHBOARD_VALUE).replace('.', '\\.')}0*$`) })).toBeVisible({ timeout: UI_READY });
      return new URL(page.url()).pathname.split('/').at(-1)!;
    }, { timeout: UI_READY });

    await test.step('UI/backendCheck 3: reload the persisted dashboard binding', async () => {
      const widgets = await probe.pg<{ query_config: { project_ids: string[]; metrics: { derived_run_id: string; source: string }[] } }>(
        `SELECT w.query_config FROM tracer_dashboardwidget w JOIN tracer_dashboard d ON w.dashboard_id = d.id
         WHERE d.id = $1 AND d.workspace_id = $2 AND d.name = $3 AND w.name = $4`,
      [dashboardId, actor.workspaceId, dashboardName, metricName]);
      expect(widgets).toHaveLength(1);
      expect(widgets[0].query_config.project_ids).toEqual([projectId]);
      expect(widgets[0].query_config.metrics.map(item => [item.derived_run_id, item.source])).toEqual([[runId, 'derived_metrics']]);
      const querying = dashboardQuery();
      await page.reload({ waitUntil: 'domcontentloaded' });
      const response = await querying;
      expect(response.status()).toBe(200);
      const body: DashboardResult = await response.json();
      expect(body.result.metrics.map(metricResult => metricResult.series.flatMap(series => series.data.map(point => point.value))))
        .toEqual([[EXPECTED_DASHBOARD_VALUE]]);
      await expect(page.getByRole('heading', { name: new RegExp(`^${String(EXPECTED_DASHBOARD_VALUE).replace('.', '\\.')}0*$`) })).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });

    await test.step('backendCheck 4: one actor-scoped critical alert consumes this exact run window', async () => {
      const monitors = await probe.pg<{ id: string }>(
        `SELECT id FROM tracer_useralertmonitor WHERE name = $1 AND metric_type = 'derived_metric'
         AND metric = $2 AND project_id = $3 AND organization_id = $4 AND workspace_id = $5 AND NOT deleted`,
      [monitorName, metric.id, projectId, actor.organizationId, actor.workspaceId]);
      expect(monitors).toHaveLength(1);
      await expect.poll(async () => Number((await probe.pg<{ n: string }>(
        `SELECT count(*) AS n FROM tracer_useralertmonitorlog WHERE alert_id = $1
         AND time_window_start = $2::timestamptz AND time_window_end = $3::timestamptz AND NOT deleted`,
      [monitors[0].id, run.window_start, run.window_end]))[0].n), POLL.ASYNC_JOB).toBe(EXPECTED_ALERTS);
      const logs = await probe.pg<{ type: string; last_derived_run_id: string }>(
        `SELECT l.type, m.last_derived_run_id FROM tracer_useralertmonitorlog l JOIN tracer_useralertmonitor m ON l.alert_id = m.id
         WHERE m.id = $1 AND l.time_window_start = $2::timestamptz AND l.time_window_end = $3::timestamptz AND NOT l.deleted`,
      [monitors[0].id, run.window_start, run.window_end]);
      expect(logs).toEqual([{ type: 'critical', last_derived_run_id: runId }]);
      await testInfo.attach('persisted-consumers', { contentType: 'application/json',
        body: JSON.stringify({ metricId: metric.id, runId, dashboardId, monitorId: monitors[0].id, logs }) });
    });
  } finally {
    await req.dispose();
  }
});
