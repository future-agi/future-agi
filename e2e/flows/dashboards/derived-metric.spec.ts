import { request, type Locator } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace, type SeededTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// frontend/src/routes/paths.js (dashboard.metrics, dashboard.dashboards) and
// frontend/src/utils/axios.js (derivedMetrics, dashboard, project.createMonitor).
const NEW_METRIC_PAGE = '/dashboard/metrics/new';
const METRICS = '/tracer/derived-metrics/';
const RUNS = '/tracer/derived-metric-runs/';
const MONITORS = '/tracer/user-alerts/';
const DASHBOARDS = '/tracer/dashboard/';
const DASH_QUERY = '/tracer/dashboard/query/';
// The user's input. ratio(numerator, denominator) is one of the four top-level
// calls tracer/services/derived_metrics/formula.py AGGREGATES accepts.
const FORMULA = 'ratio(resolved == 1 and escalated == 0, resolved >= 0)';
const THRESHOLD = 0.75;
// README: one browser action's first-paint wall, not the suite timeout.
const UI_READY = 60_000;
// Server-time waits, pinned from the product. A conversation unit stays open
// until settle_minutes after its LAST arrival (tracer/services/derived_metrics/
// settle.py evaluate_settle; arrival is spans.updated_at, DEFAULT now64() in
// clickhouse/v2/schema/002_spans_v2.sql, so it cannot be aged on the wire).
// SETTLE_MINUTES_DEFAULT and CADENCE_SECONDS_DEFAULT: tracer/models/derived_metric.py.
// A pass writes next_due_at = the earliest open due_at (admission.py), so the
// settling pass normally runs at settle + one dispatch tick; the cadence term
// covers the incremental_last_success_at + cadence_seconds fallback.
const SETTLE_MS = 30 * 60_000;
const CADENCE_MS = 900_000;
// tfc/temporal/schedules/tracer.py: dispatch-derived-metrics and check-alerts.
const DISPATCH_TICK_MS = 60_000;
const ALERT_CHECK_TICK_MS = 60_000;
const PASS_MARGIN_MS = 120_000;
const VALUE_FINAL = { timeout: SETTLE_MS + CADENCE_MS + DISPATCH_TICK_MS + PASS_MARGIN_MS, intervals: [30_000] };
// UserAlertMonitor.alert_frequency MinValueValidator(5) (tracer/models/monitor.py).
// The product's last create body used 60; the first check can land before the
// snapshot capture commits, and the next one is alert_frequency later.
const ALERT_FREQUENCY_MINUTES = 5;
const ALERT_RECORDED = { timeout: ALERT_FREQUENCY_MINUTES * 60_000 + 2 * ALERT_CHECK_TICK_MS, intervals: [10_000] };
const IO_MARGIN_MS = 90_000;
const QUARTER_MS = 15 * 60_000;
// Assertion-only fallibility anchors. None feeds a seed, formula, request,
// threshold or response matcher. Perturb each independently, then restore.
const EXPECTED_SOURCE_SPANS = 4; // backendCheck 1 -> 5 must fail.
const EXPECTED_UNITS = 2; // backendChecks 3/4 -> 3 must fail.
const EXPECTED_VALUE = 0.5; // backendChecks 3/4/5 -> 0.75 must fail.
const EXPECTED_ALERTS = 1; // backendCheck 6 -> 2 must fail.

// Wire shapes pinned from tracer/serializers/derived_metric.py, dashboard.py and
// monitor.py; GeneralMethods.success_response wraps as { status, result }.
type Version = { id: string; version: number; formula: string; kind: string; level: string; selection_unit: string;
  weighting: string; definition_hash: string; history_mode: string | null; inputs: { alias: string; source: string;
  field: string; attribute_key: string; reducer: string }[] };
type Metric = { id: string; project_id: string; name: string; current_version: number;
  definition: Version | null; draft_definition: Version | null; snapshot_supported: boolean };
type Validation = { valid: boolean; kind: string; required_inputs: string[] };
type Point = { value: number | null; point_status: string; eligible_count: number; evaluated_count: number; missing_count: number };
type Kpi = { effective_window: { window_start: string; window_end: string }; partial: boolean; current: Point };
type Run = { id: string; metric_id: string; metric_version: number; kind: string; status: string; error: string;
  snapshot_id: string | null; result_id: string | null; window_start: string; window_end: string };
type Result = { group_key: string; value: number | null; numerator: number | null; denominator: number | null;
  status: string; unit_count: number; valid_count: number; missing_count: number; invalid_probability_count: number };
type ResultPage = { results: Result[]; next_cursor: string | null };
type Envelope<T> = { status: boolean; result: T };
type DashboardResult = Envelope<{ metrics: { series: { data: { timestamp: string; value: number | null }[] }[] }[] }>;
type MonitorDetails = Envelope<{ logs: { results: { type: string; time_window_start: string; time_window_end: string }[];
  metadata: { total_rows: number } } }>;
type Span = { id: string; trace_id: string; project_id: string; trace_session_id: string;
  has_resolved: number; has_escalated: number; resolved: number; escalated: number };

test('DASH-E2E-017: a saved custom-formula metric persists a correct value, renders on a dashboard and records an alert', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DASH-E2E-017', area: 'dashboards',
    userGoal: 'Save a custom-formula conversation metric, read its settled value on the Overview, freeze it as a snapshot, '
      + 'and see that snapshot render on a dashboard and record a threshold alert. The Add to dashboard and Create alert '
      + 'menu items are disabled in the product, so the widget and monitor are created through the API the product used.',
    steps: [
      'ingest two minted conversations, each a root and a child span with numeric resolved/escalated outcomes, aged an hour so their 15-minute bucket is closed',
      'create the metric through New metric: name, project, Custom formula over conversations, two span-attribute inputs and the formula; Save metric validates, creates and queues the first calculation',
      'wait for the conversations to settle (30 minutes after their last arrival) and the next refresh to publish them',
      'reload the metric page and read the Overview value on the default 7-day window and on a Custom window around the seeded bucket',
      'create a static below-threshold monitor through POST /tracer/user-alerts/, then Save snapshot on the Custom window',
      'create a dashboard with a metric widget pinned to the snapshot through the dashboard and widget APIs, then open it in the browser',
      'wait for the 60-second alert check to record the monitor alert for the snapshot window',
    ],
    backendChecks: [
      'The exact four seeded spans, their typed outcomes and two distinct conversations belong to the minted project in the actor organization/default workspace.',
      'Validate accepts the formula, and the saved metric is version 1 of a custom conversation recipe in PG and in the metric API, with the same definition hash.',
      'The KPI API returns value 0.5 over exactly two evaluated conversations for the Custom window, not partial, and the Overview shows 0.5 on both windows.',
      'The snapshot run completes; it froze exactly the two seeded conversations, its committed overall result is numerator 1, denominator 2, value 0.5, and its commit row names that result.',
      'The persisted widget binds only the snapshot run, and the dashboard query the browser sends returns 0.5, which the widget renders.',
      'The actor-scoped monitor records exactly one critical alert log for the snapshot window, one critical delivery for that run, marks it consumed, and the monitor details API lists that log.',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Every chained budget, in order: source visibility, three builder steps, the
  // settled value, two Overview steps, Save snapshot, its capture, the alert,
  // the dashboard load, and API/storage I/O. About 65 minutes as a ceiling; a
  // healthy run spends about 32-40 of them, almost all in VALUE_FINAL.
  test.setTimeout(POLL.SPAN_VISIBLE.timeout + 3 * UI_READY + VALUE_FINAL.timeout + 2 * UI_READY + UI_READY
    + POLL.ASYNC_JOB.timeout + ALERT_RECORDED.timeout + UI_READY + IO_MARGIN_MS);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `e2e-derived17-${suffix}`;
  const metricName = `${projectName}-metric`;
  const monitorName = `${projectName}-monitor`;
  const dashboardName = `${projectName}-dashboard`;
  // Buckets use min(start_time) (queries.py units CTE). Start the spans 55-70
  // minutes ago inside one closed 15-minute bucket, and read a 30-minute window
  // that ends 30 minutes before the current bucket: cut_terms (reads.py) only
  // calls a bucket final once it ends at or before floor15(watermark - 30 min),
  // and a snapshot needs every bucket in its window final.
  const currentBucket = Math.floor(Date.now() / QUARTER_MS) * QUARTER_MS;
  const windowStart = new Date(currentBucket - 4 * QUARTER_MS).toISOString();
  const windowEnd = new Date(currentBucket - 2 * QUARTER_MS).toISOString();
  const start = BigInt(Date.parse(windowStart) + 5 * 60_000) * 1_000_000n;
  const traces: SeededTrace[] = [];
  const kpiStates: { at: string; state: string }[] = [];
  let projectId = '';
  // Additive UI evidence only: never asserts, and neither a slow render nor a
  // failed capture can fail the flow. `ready` is a best-effort settle, not a check.
  const screenshot = async (name: string, ready?: Locator) => {
    if (ready) await ready.first().waitFor({ state: 'visible', timeout: 5_000 }).catch(() => undefined);
    const body = await page.screenshot({ fullPage: true }).catch(() => null);
    if (body) await testInfo.attach(name, { body, contentType: 'image/png' });
  };
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
      body: JSON.stringify({ projectName, metricName, monitorName, dashboardName, windowStart, windowEnd, traces }) });

    const sessions = await test.step('backendCheck 1: exact source identities, values and project scope', async () => {
      await expect.poll(async () => Number((await probe.ch<{ n: string }>(
        'SELECT count() AS n FROM spans FINAL WHERE trace_id IN ({a:String}, {b:String})',
        { a: traces[0].traceId, b: traces[1].traceId }))[0].n), POLL.SPAN_VISIBLE).toBe(EXPECTED_SOURCE_SPANS);
      const projects = await probe.pg<{ id: string }>(
        // project_workspace_scope_q includes null-workspace legacy projects
        // when the actor is in its default workspace.
        'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2 AND (workspace_id = $3 OR workspace_id IS NULL) AND NOT deleted',
        [projectName, actor.organizationId, actor.workspaceId]);
      expect(projects).toHaveLength(1);
      projectId = projects[0].id;
      const rows = await probe.ch<Span>(`SELECT id, trace_id, project_id, toString(trace_session_id) AS trace_session_id,
        mapContains(attrs_number, 'resolved') AS has_resolved, mapContains(attrs_number, 'escalated') AS has_escalated,
        attrs_number['resolved'] AS resolved, attrs_number['escalated'] AS escalated
        FROM spans FINAL WHERE trace_id IN ({a:String}, {b:String})`,
      { a: traces[0].traceId, b: traces[1].traceId });
      expect(rows.map(row => row.id).sort()).toEqual(traces.flatMap(trace => trace.spanIds).sort());
      expect([...new Set(rows.map(row => row.project_id))]).toEqual([projectId]);
      for (const row of rows) {
        expect(row.has_resolved).toBe(1); expect(row.has_escalated).toBe(1);
        expect(row.resolved).toBe(1);
        expect(row.escalated).toBe(row.trace_id === traces[0].traceId ? 0 : 1);
      }
      // One conversation per trace: the unit ids the snapshot must freeze.
      const byTrace = Object.fromEntries(rows.map(row => [row.trace_id, row.trace_session_id]));
      const ids = traces.map(trace => byTrace[trace.traceId]);
      expect(new Set(ids).size).toBe(EXPECTED_UNITS);
      return { resolved: ids[0], escalated: ids[1] };
    });

    await test.step('UI: open New metric, name it and choose the project', async () => {
      await page.goto(NEW_METRIC_PAGE, { waitUntil: 'domcontentloaded' });
      await expect(page.getByRole('heading', { name: 'New metric', exact: true })).toBeVisible({ timeout: UI_READY });
      await page.getByLabel('Metric name', { exact: true }).fill(metricName);
      // Choosing a project resets the recipe (MetricBuilderPage newBuilder), so it comes first.
      await page.getByRole('combobox', { name: 'Project', exact: true }).fill(projectName);
      await page.getByRole('option', { name: projectName, exact: true }).click({ timeout: UI_READY });
    }, { timeout: UI_READY });

    await test.step('UI: build the custom formula over conversations', async () => {
      // MUI selects are named by label plus displayed value, hence no exact match.
      await page.getByRole('combobox', { name: 'Calculation' }).click({ timeout: UI_READY });
      await page.getByRole('option', { name: 'Custom formula', exact: true }).click();
      await expect(page.getByRole('combobox', { name: 'Unit' })).toHaveText('conversations', { timeout: UI_READY });
      // Custom formula starts with no inputs; each new one is a span number attribute (metricForm.newInput).
      for (const [index, alias] of ['resolved', 'escalated'].entries()) {
        await page.getByRole('button', { name: 'Add input', exact: true }).click();
        await page.getByLabel(`Input ${index + 1} alias`, { exact: true }).fill(alias);
        await page.getByLabel(`Input ${index + 1} attribute key`, { exact: true }).fill(alias);
      }
      await page.getByLabel('Formula', { exact: true }).fill(FORMULA);
      await screenshot('ui-custom-formula');
    }, { timeout: UI_READY });

    const metric = await test.step('UI/backendCheck 2: Save metric validates, then creates version 1', async () => {
      const validating = page.waitForResponse(r => new URL(r.url()).pathname.endsWith(`${METRICS}validate/`)
        && r.request().method() === 'POST', { timeout: UI_READY });
      const saving = page.waitForResponse(r => new URL(r.url()).pathname.endsWith(METRICS)
        && r.request().method() === 'POST', { timeout: UI_READY });
      await page.getByRole('button', { name: 'Save metric', exact: true }).click();
      const validation = await validating;
      expect(validation.status()).toBe(200);
      const verdict: Validation = await validation.json();
      expect([verdict.valid, verdict.kind, [...verdict.required_inputs].sort()]).toEqual([true, 'custom', ['escalated', 'resolved']]);
      const response = await saving;
      expect(response.status()).toBe(201);
      const saved: Metric = await response.json();
      expect([saved.project_id, saved.name, saved.current_version, saved.definition]).toEqual([projectId, metricName, 1, null]);
      const draft = saved.draft_definition!;
      expect([draft.version, draft.kind, draft.level, draft.selection_unit, draft.weighting, draft.formula])
        .toEqual([1, 'custom', 'conversation', 'session', 'none', FORMULA]);
      expect(draft.inputs.map(input => [input.alias, input.source, input.field, input.attribute_key, input.reducer]))
        .toEqual([['resolved', 'span', 'attribute_number', 'resolved', 'latest'], ['escalated', 'span', 'attribute_number', 'escalated', 'latest']]);
      expect(draft.definition_hash).toMatch(/^[a-f0-9]{64}$/);
      await expect(page).toHaveURL(new RegExp(`/dashboard/metrics/${saved.id}$`), { timeout: UI_READY });
      const versions = await probe.pg<{ id: string; version: number; kind: string; level: string; formula: string; definition_hash: string }>(
        `SELECT v.id::text AS id, v.version, v.kind, v.level, v.formula, v.definition_hash FROM tracer_derivedmetricversion v
         JOIN tracer_derivedmetric m ON v.metric_id = m.id
         WHERE m.id = $1 AND m.name = $2 AND m.project_id = $3 AND m.organization_id = $4 AND m.workspace_id = $5 AND NOT m.deleted`,
      [saved.id, metricName, projectId, actor.organizationId, actor.workspaceId]);
      expect(versions).toEqual([{ id: draft.id, version: 1, kind: 'custom', level: 'conversation', formula: FORMULA,
        definition_hash: draft.definition_hash }]);
      await testInfo.attach('metric', { contentType: 'application/json',
        body: JSON.stringify({ projectId, metricId: saved.id, versionId: draft.id }) });
      return saved;
    }, { timeout: UI_READY });
    const kpiPath = `${METRICS}${metric.id}/kpi/`;

    await test.step('backendCheck 3: the settled conversations publish an exact, final value', async () => {
      // 503 is the product's "not calculated / not final yet" (reads.metric_read,
      // require_calculated_window) and 429 its "busy" (api.js metricErrorState).
      // Anything else is a failure, not a wait.
      let kpi: Kpi | undefined;
      await expect.poll(async () => {
        const { status, body } = await actor.api.request<Kpi & { code?: string }>('get', kpiPath,
          { params: { window_start: windowStart, window_end: windowEnd } });
        if (![200, 429, 503].includes(status)) throw new Error(`KPI ${status}: ${JSON.stringify(body).slice(0, 300)}`);
        kpi = status === 200 ? body : undefined;
        const state = status === 200 ? `${body.current.point_status}:partial=${body.partial}` : `${status}:${body.code}`;
        if (kpiStates.at(-1)?.state !== state) kpiStates.push({ at: new Date().toISOString(), state });
        return state;
      }, VALUE_FINAL).toBe('ok:partial=false');
      if (!kpi) throw new Error('The final KPI read was not kept.');
      expect([Date.parse(kpi.effective_window.window_start), Date.parse(kpi.effective_window.window_end)])
        .toEqual([Date.parse(windowStart), Date.parse(windowEnd)]);
      expect([kpi.current.value, kpi.current.eligible_count, kpi.current.evaluated_count, kpi.current.missing_count])
        .toEqual([EXPECTED_VALUE, EXPECTED_UNITS, EXPECTED_UNITS, 0]);
      const active = await actor.api.get<Metric>(`${METRICS}${metric.id}/`);
      expect([active.definition?.id, active.definition?.definition_hash, active.definition?.history_mode, active.snapshot_supported])
        .toEqual([metric.draft_definition!.id, metric.draft_definition!.definition_hash, 'history', true]);
    });

    await test.step('UI: the Overview shows the value on the default 7-day window', async () => {
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(page.getByRole('heading', { name: metricName, exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(page.getByTestId('metric-hero')).toHaveText(String(EXPECTED_VALUE), { timeout: UI_READY });
    }, { timeout: UI_READY });

    await test.step('UI: the Overview shows the same value on a Custom window around the seeded bucket', async () => {
      // CustomWindow parses datetime-local input in the browser's own zone.
      const local = (iso: string) => page.evaluate(value => {
        const date = new Date(value);
        return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
      }, iso);
      await page.getByRole('button', { name: 'Custom', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: 'Custom window' });
      await dialog.getByLabel('Window start', { exact: true }).fill(await local(windowStart));
      await dialog.getByLabel('Window end', { exact: true }).fill(await local(windowEnd));
      const reading = page.waitForResponse(r => {
        const url = new URL(r.url());
        return url.pathname.endsWith(kpiPath) && url.searchParams.get('window_start') === windowStart
          && url.searchParams.get('window_end') === windowEnd;
      }, { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Apply window', exact: true }).click();
      const response = await reading;
      expect(response.status()).toBe(200);
      expect(((await response.json()) as Kpi).current.value).toBe(EXPECTED_VALUE);
      await expect(page.getByTestId('metric-hero')).toHaveText(String(EXPECTED_VALUE), { timeout: UI_READY });
      await screenshot('ui-overview-value');
    }, { timeout: UI_READY });

    // Snapshot completion does not notify monitors; the alert check consumes a
    // run only if it committed after the monitor existed (integrations.py
    // process_derived_monitor), so the monitor is created first.
    const monitorId = await test.step('API: create the below-threshold monitor the product used to create', async () => {
      // Body copied from the retired DerivedMetricsView.jsx createMonitor
      // (git show ae6f536de6^), except alert_frequency (see ALERT_FREQUENCY_MINUTES).
      const created = await actor.api.post<Envelope<string>>(MONITORS, { organization: actor.organizationId,
        name: monitorName, project: projectId, metric_type: 'derived_metric', metric: metric.id,
        threshold_operator: 'less_than', threshold_type: 'static', critical_threshold_value: THRESHOLD,
        alert_frequency: ALERT_FREQUENCY_MINUTES, is_mute: false, filters: {} });
      expect([created.status, typeof created.result]).toEqual([true, 'string']);
      const monitors = await probe.pg<{ id: string }>(
        `SELECT id::text AS id FROM tracer_useralertmonitor WHERE name = $1 AND metric_type = 'derived_metric'
         AND metric = $2 AND project_id = $3 AND organization_id = $4 AND workspace_id = $5 AND NOT deleted`,
      [monitorName, metric.id, projectId, actor.organizationId, actor.workspaceId]);
      expect(monitors).toHaveLength(1);
      return monitors[0].id;
    });

    const snapshot = await test.step('UI: Save snapshot freezes the Custom window', async () => {
      await page.getByRole('button', { name: 'Save snapshot', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: 'Save snapshot' });
      // The dialog quotes the Overview's settled read for this window.
      await expect(dialog.getByText(String(EXPECTED_VALUE), { exact: true })).toBeVisible({ timeout: UI_READY });
      const saving = page.waitForResponse(r => new URL(r.url()).pathname.endsWith(`${METRICS}${metric.id}/snapshots/`)
        && r.request().method() === 'POST', { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Save snapshot', exact: true }).click();
      const response = await saving;
      expect(response.status()).toBe(202);
      const run: Run = await response.json();
      expect([run.kind, run.metric_id, run.metric_version, Date.parse(run.window_start), Date.parse(run.window_end)])
        .toEqual(['snapshot', metric.id, 1, Date.parse(windowStart), Date.parse(windowEnd)]);
      await testInfo.attach('snapshot-run', { contentType: 'application/json',
        body: JSON.stringify({ metricId: metric.id, runId: run.id, snapshotId: run.snapshot_id, monitorId }) });
      return run;
    }, { timeout: UI_READY });

    await expect.poll(async () => {
      const run = await actor.api.get<Run>(`${RUNS}${snapshot.id}/`);
      return `${run.status}:${run.error}`;
    }, POLL.ASYNC_JOB).toBe('complete:');
    const run = await actor.api.get<Run>(`${RUNS}${snapshot.id}/`);
    expect(run.result_id).not.toBeNull();

    await test.step('backendCheck 4: the snapshot froze the two conversations and committed the exact result', async () => {
      // derived_metric_inputs is an immutable MergeTree (028_derived_metrics.sql); no FINAL.
      const frozen = await probe.ch<{ unit_id: string }>(
        `SELECT unit_id FROM derived_metric_inputs WHERE org_id = {o:UUID} AND workspace_id = {w:UUID}
         AND project_id = {p:UUID} AND snapshot_id = {s:UUID} ORDER BY unit_id`,
      { o: actor.organizationId, w: actor.workspaceId, p: projectId, s: run.snapshot_id! });
      expect(frozen).toHaveLength(EXPECTED_UNITS);
      expect(frozen.map(row => row.unit_id)).toEqual([sessions.resolved, sessions.escalated].sort());
      const persisted = await actor.api.get<ResultPage>(`${RUNS}${run.id}/results/`, { rollup: 'overall' });
      expect(persisted.results).toEqual([{ group_key: '', value: EXPECTED_VALUE, numerator: 1, denominator: 2, status: 'ok',
        unit_count: EXPECTED_UNITS, valid_count: EXPECTED_UNITS, missing_count: 0, invalid_probability_count: 0 }]);
      const commits = await probe.ch<{ result_id: string }>(
        'SELECT toString(result_id) AS result_id FROM derived_metric_commits FINAL WHERE run_id = {r:UUID} AND project_id = {p:UUID}',
      { r: run.id, p: projectId });
      expect(commits).toEqual([{ result_id: run.result_id }]);
    });

    const dashboardId = await test.step('API: create the dashboard and the widget pinned to the snapshot', async () => {
      // Bodies copied from the retired DerivedMetricsView.jsx publishDashboard (git show ae6f536de6^).
      const created = await actor.api.post<Envelope<{ id: string }>>(DASHBOARDS, { name: dashboardName,
        description: `${metricName} snapshot. A point represents the complete run window, not an average of daily ratios.` });
      await actor.api.post<Envelope<{ id: string }>>(`${DASHBOARDS}${created.result.id}/widgets/`, {
        name: metricName,
        description: `Version ${run.metric_version} snapshot`,
        query_config: {
          project_ids: [projectId],
          time_range: { custom_start: run.window_start, custom_end: run.window_end },
          granularity: 'day',
          metrics: [{ id: run.id, name: metricName, display_name: metricName, type: 'derived_metric',
            source: 'derived_metrics', derived_run_id: run.id, aggregation: 'avg' }],
          filters: [],
          breakdowns: [],
        },
        chart_config: { chart_type: 'metric' },
      });
      await testInfo.attach('dashboard', { contentType: 'application/json',
        body: JSON.stringify({ dashboardId: created.result.id, runId: run.id }) });
      return created.result.id;
    });

    await test.step('UI/backendCheck 5: the dashboard renders the snapshot value', async () => {
      const widgets = await probe.pg<{ query_config: { project_ids: string[]; metrics: { derived_run_id: string; source: string }[] } }>(
        `SELECT w.query_config FROM tracer_dashboardwidget w JOIN tracer_dashboard d ON w.dashboard_id = d.id
         WHERE d.id = $1 AND d.workspace_id = $2 AND d.name = $3 AND w.name = $4 AND NOT w.deleted`,
      [dashboardId, actor.workspaceId, dashboardName, metricName]);
      expect(widgets).toHaveLength(1);
      expect(widgets[0].query_config.project_ids).toEqual([projectId]);
      expect(widgets[0].query_config.metrics.map(item => [item.derived_run_id, item.source])).toEqual([[run.id, 'derived_metrics']]);
      const querying = page.waitForResponse(r => {
        if (!new URL(r.url()).pathname.endsWith(DASH_QUERY) || r.request().method() !== 'POST') return false;
        const data = r.request().postDataJSON() as { metrics: { derived_run_id?: string }[] };
        return data.metrics.some(item => item.derived_run_id === run.id);
      }, { timeout: UI_READY });
      await page.goto(`/dashboard/dashboards/${dashboardId}`, { waitUntil: 'domcontentloaded' });
      const response = await querying;
      expect(response.status()).toBe(200);
      const body: DashboardResult = await response.json();
      expect(body.result.metrics.map(item => item.series.flatMap(series => series.data.map(point => point.value))))
        .toEqual([[EXPECTED_VALUE]]);
      await expect(page.getByRole('heading', { name: new RegExp(`^${String(EXPECTED_VALUE).replace('.', '\\.')}0*$`) }))
        .toBeVisible({ timeout: UI_READY });
      await screenshot('ui-dashboard-widget');
    }, { timeout: UI_READY });

    await test.step('backendCheck 6: one critical alert for the snapshot window', async () => {
      await expect.poll(async () => Number((await probe.pg<{ n: string }>(
        `SELECT count(*) AS n FROM tracer_useralertmonitorlog WHERE alert_id = $1
         AND time_window_start = $2::timestamptz AND time_window_end = $3::timestamptz AND NOT deleted`,
      [monitorId, run.window_start, run.window_end]))[0].n), ALERT_RECORDED).toBe(EXPECTED_ALERTS);
      const logs = await probe.pg<{ type: string; last_derived_run_id: string }>(
        `SELECT l.type, m.last_derived_run_id::text AS last_derived_run_id FROM tracer_useralertmonitorlog l
         JOIN tracer_useralertmonitor m ON l.alert_id = m.id WHERE m.id = $1 AND NOT l.deleted`,
      [monitorId]);
      expect(logs).toEqual([{ type: 'critical', last_derived_run_id: run.id }]);
      const deliveries = await probe.pg<{ run_id: string; alert_type: string }>(
        'SELECT run_id::text AS run_id, alert_type FROM tracer_derivedmetricmonitordelivery WHERE monitor_id = $1',
        [monitorId]);
      expect(deliveries).toEqual([{ run_id: run.id, alert_type: 'critical' }]);
      const details = await actor.api.get<MonitorDetails>(`${MONITORS}${monitorId}/details/`);
      expect(details.result.logs.metadata.total_rows).toBe(EXPECTED_ALERTS);
      expect(details.result.logs.results.map(log => [log.type, Date.parse(log.time_window_start), Date.parse(log.time_window_end)]))
        .toEqual([['critical', Date.parse(run.window_start), Date.parse(run.window_end)]]);
      await testInfo.attach('persisted-consumers', { contentType: 'application/json',
        body: JSON.stringify({ metricId: metric.id, runId: run.id, dashboardId, monitorId, logs, deliveries }) });
    });
  } finally {
    // A timeout in the settle wait must be readable from the report alone.
    await testInfo.attach('kpi-poll-states', { contentType: 'application/json', body: JSON.stringify(kpiStates) });
    if (projectId) {
      const refresh = await probe.pg(
        `SELECT settle_minutes, cadence_seconds, pending_backfill, next_due_at, incremental_last_success_at,
         last_success_at, last_error_at, last_error_code, consecutive_failures, paused_reason, watermark_ns::text AS watermark_ns
         FROM tracer_metricprojectrefresh WHERE project_id = $1`, [projectId]).catch((error: Error) => error.message);
      await testInfo.attach('metric-refresh-state', { contentType: 'application/json', body: JSON.stringify(refresh) });
    }
    // metric_refresh_failed carries no cause. Record whether the stack provisioned
    // the Metrics ClickHouse schema (028-038), the first suspect for a refresh
    // that never publishes.
    const schema = await probe.ch<{ name: string }>(
      `SELECT name FROM system.tables WHERE database = currentDatabase()
       AND (startsWith(name, 'derived_metric') OR startsWith(name, 'metric_') OR startsWith(name, 'list_current_'))
       ORDER BY name`)
      .then(rows => rows.map(row => row.name)).catch((error: Error) => error.message);
    await testInfo.attach('metric-schema-tables', { contentType: 'application/json', body: JSON.stringify(schema) });
    await req.dispose();
  }
});
