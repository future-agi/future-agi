import { randomBytes, randomUUID } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { request } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { sendTrace } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// useDashboards.js: native discovery, preview and dashboard/widget persistence.
const DASHBOARDS = '/tracer/dashboard/';
const METRICS = `${DASHBOARDS}metrics/`;
const VALUES = `${DASHBOARDS}filter_values/`;
const QUERY = `${DASHBOARDS}query/`;
const UI_READY = 60_000; // README: one ceiling for each whole browser phase.
const DAY = 86_400_000;
// source_adapters.py / dashboard.py: canonical IDs, units and aggregations.
const METRIC_CHOICES = [
  { id: 'latency', name: 'Latency', aggregation: 'avg', unit: 'ms' },
  { id: 'error_rate', name: 'Error Rate', aggregation: 'avg', unit: '%' },
  { id: 'tokens', name: 'Tokens', aggregation: 'sum', unit: 'tokens' },
  { id: 'input_tokens', name: 'Input Tokens', aggregation: 'sum', unit: 'tokens' },
  { id: 'output_tokens', name: 'Output Tokens', aggregation: 'sum', unit: 'tokens' },
];
// One-sided proof anchors: none feeds ingestion or the preview expectations.
const EXPECTED_A_CHILD_INPUT = 3;
const EXPECTED_REOPENED_BETA_TOKENS = 16;

type Config = { project_ids: string[]; time_range: { preset: string }; granularity: string;
  metrics: Record<string, unknown>[]; filters: { column_id: string; property_id: string;
    filter_config: { filter_value: string[]; [key: string]: unknown }; [key: string]: unknown }[];
  breakdowns: Record<string, unknown>[] };
type Result = { query_complete: boolean; query_exact: boolean; granularity: string;
  time_range: { start: string; end: string }; metrics: { id: string; name: string; unit: string;
    aggregation: string; query_complete: boolean; query_exact: boolean;
    series: { name: string; data: { timestamp: string; value: number | null }[] }[] }[] };
type QueryReceipt = { config: Config; body: { result: Result }; startedAt: number; endedAt: number; status: number };
type CatalogProperty = { property_id: string; name: string; source: string; type: string; output_type: string };
type DiscoveryResult = { metrics?: CatalogProperty[]; values?: { value: string; label: string; type?: string }[] };
type Project = { id: string; name: string; organization_id: string; workspace_id: string };
type Span = { id: string; trace_id: string; project_id: string; org_id: string; parent_span_id: string;
  observation_type: string; name: string; model: string; status: string; prompt_tokens: number;
  completion_tokens: number; total_tokens: number; latency_ms: number; cohort: string;
  start_us: string; end_us: string; version: string; is_deleted: number };
type Widget = { id: string; name: string; query_config: Config; chart_config: { chart_type: string } };

test('DASH-E2E-002: a saved trace-metric widget preserves its project, cohort and model results', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DASH-E2E-002', area: 'dashboards',
    userGoal: 'A developer saves a trace-metric widget for one project and cohort and sees the same exact model results after reopening it',
    steps: ['ingest five independently identified traces in primary, sibling and foreign projects',
      'create a dashboard and select five native trace metrics',
      'select the primary Project and typed cohort filter through native catalog/value pickers',
      'verify ungrouped and Model-grouped results against the planted facts',
      'save, reload and reopen the widget and verify its table and unchanged sources'],
    backendChecks: [
      'The ten minted spans and five traces retain their exact typed facts and three owned project bindings without source rewrites.',
      'Native catalog choices and Project/cohort filters retain exact property types and actor scope, and the preview contains only the independently expected model facts.',
      'The saved widget and reopened table preserve the full selected query binding and exact model series.',
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe: probe }, testInfo) => {
  test.setTimeout(600_000); // ASYNC_JOB60 + 2 SPAN_VISIBLE15 + 8 UI_READY60 + 30 headroom.
  const prefix = `e2e-dash2-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const cohortKey = `${prefix}.cohort`;
  const foreignKey = `${prefix}.foreign-only`;
  const actor = scopeActors.ownerA;
  const projectNames = [`${prefix}-primary`, `${prefix}-sibling`, `${prefix}-foreign`];
  const models = [`${prefix}-Alpha`, `${prefix}-Beta`, `${prefix}-Gamma`];
  const dashboardName = `${prefix}-dashboard`;
  const widgetName = `${prefix}-widget`;
  const plantedDay = Math.floor(Date.now() / DAY) * DAY - DAY;
  const start = BigInt(plantedDay + DAY / 2) * 1_000_000n;
  // Independent source literals: the last two same-value rows expose scope leaks.
  const seeds = [
    { key: 'A', project: 0, model: models[0], cohort: 'alpha', ms: 50, tokens: [[2, 1], [3, 2]] },
    { key: 'B', project: 0, model: models[1], cohort: 'alpha', ms: 100, tokens: [[4, 2], [6, 4]] },
    { key: 'C', project: 0, model: models[2], cohort: 'beta', ms: 200, tokens: [[8, 4], [12, 8]] },
    { key: 'S', project: 1, model: models[0], cohort: 'alpha', ms: 900, tokens: [[1000, 2000], [1000, 2000]] },
    { key: 'F', project: 2, model: models[0], cohort: 'alpha', ms: 900, tokens: [[1000, 2000], [1000, 2000]] },
  ].map(seed => ({ ...seed, traceId: randomUUID(), spanIds: [randomBytes(8).toString('hex'), randomBytes(8).toString('hex')] }));
  await testInfo.attach('planted-identities', { contentType: 'application/json', body: JSON.stringify({
    seeds, projectNames, models, cohortKey, foreignKey, start: String(start), actors: scopeActors.evidence().actors,
  }) });
  const ingestion = await request.newContext();
  try {
    for (const seed of seeds) {
      const owner = seed.project === 2 ? scopeActors.ownerB : actor;
      const keys = scopeActors.owners.find(item => item.organizationId === owner.organizationId)!;
      const attributes = seed.tokens.map(([input, output], index) => ({
        'fi.span.kind': index === 0 ? 'chain' : 'llm',
        'gen_ai.usage.input_tokens': input, 'gen_ai.usage.output_tokens': output,
        'gen_ai.request.model': seed.model, 'gen_ai.cost.total': 0, [cohortKey]: seed.cohort,
        ...(seed.project === 2 ? { [foreignKey]: 'foreign' } : {}),
      }));
      const sent = await sendTrace(ingestion, { collectorUrl: E2E.collectorUrl,
        apiKey: keys.apiKey, secretKey: keys.secretKey, projectName: projectNames[seed.project],
        traceId: seed.traceId, rootSpanId: seed.spanIds[0], childSpanId: seed.spanIds[1],
        rootName: `${prefix}-${seed.key}-root`, childName: `${prefix}-${seed.key}-child`,
        startTimeUnixNano: start, endTimeUnixNano: start + BigInt(seed.ms) * 1_000_000n,
        resourceAttributes: { project_type: 'observe' }, rootAttributes: attributes[0], childAttributes: attributes[1] });
      expect(sent).toEqual({ traceId: seed.traceId, spanIds: seed.spanIds, projectName: projectNames[seed.project] });
    }
  } finally { await ingestion.dispose(); }

  let projects: Project[] = [];
  await expect.poll(async () => {
    projects = await probe.pg<Project>('SELECT id, name, organization_id, workspace_id FROM tracer_project WHERE name = ANY($1) AND NOT deleted', [projectNames]);
    return projects.map(p => ({ name: p.name, organization_id: p.organization_id, workspace_id: p.workspace_id })).sort((a, b) => a.name.localeCompare(b.name));
  }, POLL.ASYNC_JOB).toEqual(projectNames.map((name, index) => ({ name,
    organization_id: index === 2 ? scopeActors.ownerB.organizationId : actor.organizationId,
    workspace_id: index === 2 ? scopeActors.ownerB.workspaceId : actor.workspaceId })).sort((a, b) => a.name.localeCompare(b.name)));
  const projectIds = projectNames.map(name => projects.find(p => p.name === name)!.id);
  // Check2's independent wire witness; changing this to projectIds[1] must fail.
  const expectedProjectFilterId = projectIds[0];
  // converter.go: current physical span facts, not dashboard aggregate SQL.
  const sourceSql = `SELECT id, trace_id, project_id, org_id, parent_span_id, observation_type, name,
    model, status, prompt_tokens, completion_tokens, total_tokens, latency_ms,
    attrs_string[{key:String}] AS cohort, toUnixTimestamp64Micro(start_time) AS start_us,
    toUnixTimestamp64Micro(end_time) AS end_us, toString(_version) AS version, is_deleted
    FROM spans FINAL WHERE project_id IN ({primary:UUID}, {sibling:UUID}, {foreign:UUID}) ORDER BY id`;
  const projectParams = { primary: projectIds[0], sibling: projectIds[1], foreign: projectIds[2] };
  const sourceParams = { key: cohortKey, ...projectParams };
  const expectedSources = seeds.flatMap(seed => seed.spanIds.map((id, index) => ({ id, trace_id: seed.traceId,
    project_id: projectIds[seed.project], org_id: seed.project === 2 ? scopeActors.ownerB.organizationId : actor.organizationId,
    parent_span_id: index ? seed.spanIds[0] : '', observation_type: index ? 'llm' : 'chain',
    name: `${prefix}-${seed.key}-${index ? 'child' : 'root'}`, model: seed.model, status: 'OK',
    prompt_tokens: seed.key === 'A' && index === 1 ? EXPECTED_A_CHILD_INPUT : seed.tokens[index][0],
    completion_tokens: seed.tokens[index][1], total_tokens: seed.tokens[index][0] + seed.tokens[index][1],
    latency_ms: seed.ms, cohort: seed.cohort, start_us: String(start / 1000n),
    end_us: String(start / 1000n + BigInt(seed.ms) * 1000n), is_deleted: 0,
  }))).sort((a, b) => a.id.localeCompare(b.id));
  await expect.poll(async () => (await probe.ch<Span>(sourceSql, sourceParams)).map(({ version, ...row }) => row),
    POLL.SPAN_VISIBLE).toEqual(expectedSources);
  const initialSources = await probe.ch<Span>(sourceSql, sourceParams);
  const traceSql = 'SELECT id, project_id FROM traces FINAL WHERE project_id IN ({primary:UUID}, {sibling:UUID}, {foreign:UUID}) ORDER BY toString(id)';
  const expectedTraces = seeds.map(seed => ({ id: seed.traceId, project_id: projectIds[seed.project] }))
    .sort((a, b) => a.id.localeCompare(b.id));
  await expect.poll(() => probe.ch(traceSql, projectParams), POLL.SPAN_VISIBLE).toEqual(expectedTraces);
  await testInfo.attach('source-identities', { contentType: 'application/json', body: JSON.stringify({ projects, spans: initialSources, traces: expectedTraces }) });

  const context = await scopeActors.openContext(browser, actor);
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  const browserTimeZone = await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone);
  const queries: QueryReceipt[] = [];
  const discoveries: { path: string; request: Record<string, unknown>; status: number; body: { result: DiscoveryResult } }[] = [];
  const responseErrors: string[] = [];
  // Observe actual traffic only. The UI owns its exact-query polling and bodies.
  const pending = new Set<Promise<void>>();
  page.on('response', response => {
    const path = new URL(response.url()).pathname;
    if (![QUERY, METRICS, VALUES].includes(path) || response.request().method() !== 'POST') return;
    const capture = (async () => {
      const body = await response.json();
      if (path === QUERY) queries.push({ config: response.request().postDataJSON(), body, status: response.status(),
        startedAt: response.request().timing().startTime, endedAt: Date.now() });
      else discoveries.push({ path, request: response.request().postDataJSON(), body, status: response.status() });
    })().catch(error => { responseErrors.push(String(error)); });
    pending.add(capture);
    void capture.finally(() => pending.delete(capture));
  });

  let dashboardId = '';
  let savedWidget: Widget | undefined;
  let savedRequest: Omit<Widget, 'id'> | undefined;
  const metrics = METRIC_CHOICES.map(m => ({ id: m.id, name: m.id, property_id: `system_attribute:traces:${m.id}`,
    display_name: m.name, source: 'traces', type: 'system_metric', aggregation: m.aggregation }));
  const expectedFilters: Config['filters'] = [{ column_id: 'project', property_id: 'system_attribute:traces:project',
    display_name: 'Project', source: 'traces', output_type: 'string',
    filter_config: { filter_type: 'text', filter_op: 'in', filter_value: [projectIds[0]], col_type: 'SYSTEM_METRIC' } }];
  const breakdowns = [{ name: 'model', property_id: 'system_attribute:traces:model', display_name: 'Model', type: 'system_metric', source: 'traces' }];
  const expectedConfig: Config = { metrics, project_ids: [], time_range: { preset: '7D' }, granularity: 'day', filters: expectedFilters, breakdowns: [] };
  try {
    await test.step('create and name the dashboard and Table widget', async () => {
      await page.goto('/dashboard/dashboards', { waitUntil: 'domcontentloaded' });
      const created = page.waitForResponse(r => new URL(r.url()).pathname === DASHBOARDS && r.request().method() === 'POST');
      await page.getByRole('button', { name: 'Create Dashboard', exact: true }).click();
      const response = await created;
      expect(response.status()).toBe(200);
      dashboardId = ((await response.json()) as { result: { id: string } }).result.id;
      await testInfo.attach('dashboard-id', { contentType: 'application/json', body: JSON.stringify({ dashboardId, dashboardName, widgetName }) });
      await page.getByRole('heading', { name: 'Untitled', exact: true }).click();
      await page.getByPlaceholder('Untitled Dashboard').fill(dashboardName);
      const named = page.waitForResponse(r => new URL(r.url()).pathname === `${DASHBOARDS}${dashboardId}/` && ['PATCH', 'PUT'].includes(r.request().method()));
      await page.getByPlaceholder('Untitled Dashboard').press('Enter');
      expect((await named).status()).toBe(200);
      await page.getByRole('button', { name: 'Add Widget', exact: true }).first().click();
      await page.getByText('Untitled widget', { exact: true }).click();
      await page.getByPlaceholder('Untitled widget').fill(widgetName);
      await page.getByPlaceholder('Untitled widget').press('Enter');
      await page.getByRole('combobox').filter({ hasText: 'Line' }).click();
      await page.getByRole('option', { name: 'Table', exact: true }).click();
      await page.getByText('7D', { exact: true }).click();
      await page.getByRole('combobox').filter({ hasText: 'Day' }).click();
      await page.getByRole('option', { name: 'Day', exact: true }).click();
    }, { timeout: UI_READY });

    await test.step('select exact native metric identities and explicit aggregations', async () => {
      for (const [index, metric] of METRIC_CHOICES.entries()) {
        if (index === 0) await page.getByText('Select Metric', { exact: true }).click();
        else await page.locator('.metric-section-title').click();
        const searched = page.waitForResponse(r => new URL(r.url()).pathname === METRICS && r.request().method() === 'POST' &&
          r.request().postDataJSON().search === metric.name, { timeout: UI_READY });
        await page.getByPlaceholder('Search metrics...').fill(metric.name);
        expect((await searched).status()).toBe(200);
        const option = page.getByRole('button', { name: `${metric.name} (number, Traces)`, exact: true });
        await expect(option).toBeVisible({ timeout: UI_READY });
        if (index === 0) {
          await page.getByLabel(/^Traces property count: /).locator('..').getByText('Traces', { exact: true }).click();
          await page.getByPlaceholder('Search metrics...').fill('');
          await expect(option).toBeVisible({ timeout: UI_READY });
          await page.getByPlaceholder('Search metrics...').fill(metric.name);
        }
        await option.click();
        // WidgetEditorView: name's Stack parent is directly inside its metric card.
        const card = page.locator(`p[title="${metric.name}"]`).locator('../..');
        await card.locator('.MuiChip-clickable').click();
        await page.getByText(metric.aggregation === 'avg' ? 'Average' : 'Sum', { exact: true }).last().click();
      }
    }, { timeout: UI_READY });

    // The same native query/UI comparison runs at each actual user transition.
    // These are stages of one saved-widget journey, not parameterized source cases.
    for (const stage of ['project', 'cohort', 'model', 'save', 'reload', 'reopen'] as const) {
      await test.step(stage, async () => {
        const since = queries.length;
        const actionStartedAt = Date.now();
        if (stage === 'project' || stage === 'cohort') {
          await page.getByText('Filter', { exact: true }).click();
          const category = stage === 'project' ? 'Traces' : 'Trace Attributes';
          await page.getByLabel(new RegExp(`^${category} property count: `)).locator('..').getByText(category, { exact: true }).click();
          await page.getByPlaceholder('Search filter attributes...').fill(stage === 'project' ? 'Project' : cohortKey);
          await page.getByRole('button', { name: stage === 'project' ? 'Project (string, Traces)' : `${cohortKey} (string, string, Traces)`, exact: true }).click();
          await page.getByText('Select value...', { exact: true }).click();
          await page.getByPlaceholder('Search...', { exact: true }).fill(stage === 'project' ? projectNames[0] : 'alpha');
          await page.locator(`p[title="${stage === 'project' ? projectNames[0] : 'alpha'}"]`).click();
          await page.getByRole('button', { name: 'Add', exact: true }).click();
          if (stage === 'cohort') expectedFilters.push({ column_id: cohortKey, property_id: `custom_attribute:${cohortKey}`,
            display_name: cohortKey, source: 'traces', output_type: 'string', filter_config: {
              filter_type: 'text', filter_op: 'in', filter_value: ['alpha'], col_type: 'SPAN_ATTRIBUTE', attribute_value_types: ['string'],
            } });
        } else if (stage === 'model') {
          await page.locator('.breakdown-section-title').click();
          await page.getByPlaceholder('Search breakdown attributes...').fill('Model');
          await page.getByRole('button', { name: 'Model (string, Traces)', exact: true }).click();
          expectedConfig.breakdowns = breakdowns;
        } else if (stage === 'save') {
          const saved = page.waitForResponse(r => new URL(r.url()).pathname === `${DASHBOARDS}${dashboardId}/widgets/` && r.request().method() === 'POST');
          await page.getByRole('button', { name: 'Save', exact: true }).click();
          const response = await saved;
          expect(response.status()).toBe(200);
          savedRequest = response.request().postDataJSON();
          savedWidget = ((await response.json()) as { result: Widget }).result;
          expect(savedRequest!.query_config).toEqual(expectedConfig);
          expect(savedRequest!.chart_config.chart_type).toBe('table');
          expect(savedWidget).toMatchObject({ name: widgetName, query_config: expectedConfig, chart_config: savedRequest!.chart_config });
          const rows = await probe.pg<{ id: string; name: string; dashboard_id: string; query_config: Config; chart_config: unknown }>(
            'SELECT id, name, dashboard_id, query_config, chart_config FROM tracer_dashboardwidget WHERE id = $1 AND NOT deleted', [savedWidget.id]);
          expect(rows).toEqual([{ id: savedWidget.id, name: widgetName, dashboard_id: dashboardId,
            query_config: expectedConfig, chart_config: savedRequest!.chart_config }]);
          expect(await probe.pg('SELECT id, name, workspace_id FROM tracer_dashboard WHERE id = $1 AND NOT deleted', [dashboardId]))
            .toEqual([{ id: dashboardId, name: dashboardName, workspace_id: actor.workspaceId }]);
          const detail = await actor.api.get<{ result: { id: string; name: string; workspace: string; widgets: Widget[] } }>(`${DASHBOARDS}${dashboardId}/`);
          expect(detail.result).toMatchObject({ id: dashboardId, name: dashboardName, workspace: actor.workspaceId });
          expect(detail.result.widgets.map(w => ({ id: w.id, name: w.name, query_config: w.query_config, chart_config: w.chart_config })))
            .toEqual([{ id: savedWidget.id, name: widgetName, query_config: expectedConfig, chart_config: savedRequest!.chart_config }]);
          await testInfo.attach('saved-widget', { contentType: 'application/json', body: JSON.stringify({ savedRequest, savedWidget, rows, detail }) });
          await expect(page).toHaveURL(new RegExp(`/dashboard/dashboards/${dashboardId}$`));
          return;
        } else if (stage === 'reload') await page.reload({ waitUntil: 'domcontentloaded' });
        else await page.locator(`[data-widget-id="${savedWidget!.id}"]`).getByText(widgetName, { exact: true }).click();

        await expect.poll(() => queries.slice(since).some(q => q.startedAt >= actionStartedAt && isDeepStrictEqual(q.config, expectedConfig) &&
          q.body.result?.query_complete === true), { timeout: UI_READY }).toBe(true);
        const receipt = queries.slice(since).find(q => q.startedAt >= actionStartedAt && isDeepStrictEqual(q.config, expectedConfig) &&
          q.body.result?.query_complete === true)!;
        await testInfo.attach(`${stage}-query`, { contentType: 'application/json', body: JSON.stringify(receipt) });
        expect(receipt.status).toBe(200);
        expect(receipt.config).toEqual(expectedConfig);
        // A distinct assertion anchor; changing it cannot change the UI selection.
        expect(receipt.config.filters[0].filter_config.filter_value).toEqual([expectedProjectFilterId]);
        const result = receipt.body.result;
        expect(result).toMatchObject({ query_complete: true, query_exact: true, granularity: 'day' });
        const from = Date.parse(result.time_range.start);
        const to = Date.parse(result.time_range.end);
        expect(to - from).toBe(7 * DAY);
        expect(to).toBeGreaterThanOrEqual(receipt.startedAt - 1000);
        expect(to).toBeLessThanOrEqual(receipt.endedAt + 1000);
        expect(Number(start / 1_000_000n)).toBeGreaterThan(from);
        expect(Number(start / 1_000_000n)).toBeLessThan(to);
        const buckets: number[] = [];
        for (let day = Math.floor(from / DAY) * DAY; day <= to; day += DAY) buckets.push(day);
        expect(result.metrics.map(m => ({ id: m.id, name: m.name, unit: m.unit, aggregation: m.aggregation })))
          .toEqual(METRIC_CHOICES);
        const grouped = expectedConfig.breakdowns.length > 0;
        const vectors = grouped ? [[50, 0, 8, 5, 3], [100, 0, 16, 10, 6]] :
          [stage === 'project' ? [116.666667, 0, 56, 35, 21] : [75, 0, 24, 15, 9]];
        if (stage === 'reopen') vectors[1][2] = EXPECTED_REOPENED_BETA_TOKENS;
        const names = grouped ? models.slice(0, 2) : ['total'];
        const expectedColumns: Record<string, (number | null)[]> = {};
        for (const [metricIndex, metric] of result.metrics.entries()) {
          expect(metric).toMatchObject({ query_complete: true, query_exact: true });
          expect(metric.series.map(s => s.name).sort()).toEqual([...names].sort());
          for (const [groupIndex, name] of names.entries()) {
            const series = metric.series.find(s => s.name === name)!;
            const values = buckets.map(bucket => bucket === plantedDay ? vectors[groupIndex][metricIndex] : null);
            expect(series.data.map(p => Date.parse(p.timestamp))).toEqual(buckets);
            expect(series.data.map(p => p.value)).toEqual(values);
            let label = grouped ? `${METRIC_CHOICES[metricIndex].name} / ${name} (${metric.aggregation})` :
              `${METRIC_CHOICES[metricIndex].name} (${metric.aggregation})`;
            // WidgetChart.jsx adds units to saved-table headers; the editor does not.
            if (stage === 'reload') label += ` (${METRIC_CHOICES[metricIndex].unit})`;
            expectedColumns[label] = values;
          }
        }
        const table = stage === 'reload' ? page.locator(`[data-widget-id="${savedWidget!.id}"]`).getByRole('table') : page.getByRole('table');
        await expect(table).toBeVisible({ timeout: UI_READY });
        await expect.poll(async () => (await table.locator('thead th').allTextContents()).slice(1).sort(),
          { timeout: UI_READY }).toEqual(Object.keys(expectedColumns).sort());
        const headers = await table.locator('thead th').allTextContents();
        expect(headers[0]).toBe('Time');
        expect(headers.slice(1).sort()).toEqual(Object.keys(expectedColumns).sort());
        await expect(table.locator('tbody tr')).toHaveCount(buckets.length, { timeout: UI_READY });
        // Both native tables format day buckets as local-calendar "MMM d".
        await expect(table.locator('tbody tr td:first-child')).toHaveText(buckets.map(bucket =>
          new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: browserTimeZone }).format(bucket)),
        { timeout: UI_READY });
        for (const [column, label] of headers.slice(1).entries()) {
          // widgetUtils.js: saved tables show two decimals and a metric unit.
          const unit = stage === 'reload' ? label.match(/\((ms|%|tokens)\)$/)![1] : '';
          await expect(table.locator(`tbody tr td:nth-child(${column + 2})`)).toHaveText(expectedColumns[label].map(value =>
            value === null ? '-' : stage === 'reload' ? `${value.toFixed(2)}${unit === '%' ? '' : ' '}${unit}` :
              Number.isInteger(value) ? String(value) : value.toFixed(2)), { timeout: UI_READY });
        }
        if (stage === 'reopen') {
          for (const metric of METRIC_CHOICES) {
            const card = page.locator(`p[title="${metric.name}"]`).locator('../..');
            await expect(card.getByText(metric.aggregation === 'avg' ? 'Average' : 'Sum', { exact: true })).toBeVisible({ timeout: UI_READY });
          }
          for (const selected of ['Project', cohortKey, 'Model', 'alpha']) {
            await expect(page.getByText(selected, { exact: true })).toBeVisible({ timeout: UI_READY });
          }
          expect(await probe.ch<Span>(sourceSql, sourceParams)).toEqual(initialSources);
          expect(await probe.ch(traceSql, projectParams)).toEqual(expectedTraces);
        }
        await testInfo.attach(`${stage}-table`, { contentType: 'image/png', body: await page.screenshot() });

        if (stage === 'cohort') {
          await Promise.all(pending);
          // Native search receipts must identify the selected types, not merely
          // have a display name that the frontend could have synthesized.
          for (const choice of [...METRIC_CHOICES.map(m => ({ name: m.id, type: 'number', id: `system_attribute:traces:${m.id}` })),
            { name: 'project', type: 'string', id: 'system_attribute:traces:project' },
            { name: cohortKey, type: 'string', id: `custom_attribute:${cohortKey}` }]) {
            const matching = discoveries.filter(d => d.path === METRICS).flatMap(d => d.body.result.metrics!)
              .filter(m => m.property_id === choice.id);
            expect(matching.length, `native catalog identity ${choice.id}`).toBeGreaterThan(0);
            for (const property of matching) expect(property).toMatchObject({ property_id: choice.id, name: choice.name,
              source: 'traces', type: choice.type, output_type: choice.type });
          }
          const values = discoveries.filter(d => d.path === VALUES);
          const projectChoices = values.filter(d => d.request.property_id === 'system_attribute:traces:project')
            .flatMap(d => d.body.result.values!);
          expect(projectChoices.some(v => v.value === projectIds[0] && v.label === projectNames[0])).toBe(true);
          expect(projectChoices.filter(v => v.value === projectIds[2] || v.label === projectNames[2])).toEqual([]);
          const cohortValues = values.filter(d => d.request.property_id === `custom_attribute:${cohortKey}`);
          expect(cohortValues.length).toBeGreaterThan(0);
          for (const discovery of cohortValues) {
            expect(discovery.status).toBe(200);
            expect(discovery.request).toMatchObject({ property_id: `custom_attribute:${cohortKey}`, metric_name: cohortKey,
              metric_type: 'custom_attribute', project_ids: '', source: 'traces', attribute_type: 'string' });
          }
          expect(cohortValues.flatMap(d => d.body.result.values!).some(v => v.value === 'alpha' && v.label === 'alpha' && v.type === 'string')).toBe(true);
          // Definition discovery stays workspace-scoped even when the query uses Project.
          const catalogRequest = { source: 'traces', category: 'custom_attribute', search: foreignKey, cursor_mode: true, page_size: 25 };
          const scopeResults = [];
          for (const selectedActor of [scopeActors.ownerB, actor, scopeActors.withWorkspace(actor, scopeActors.emptyWorkspace.id)]) {
            const response = await selectedActor.api.post<{ result: { metrics: { property_id: string }[] } }>(METRICS, catalogRequest);
            expect(response.result.metrics.map(m => m.property_id)).toEqual(selectedActor === scopeActors.ownerB ? [`custom_attribute:${foreignKey}`] : []);
            // A unique existing foreign value makes negative scope checks non-vacuous.
            // Same value-reader wire as the native typed cohort picker, different key.
            const valueResponse = await selectedActor.api.post<{ result: { values: { value: string; label: string; type: string }[] } }>(VALUES,
              { property_id: `custom_attribute:${foreignKey}`, metric_name: foreignKey, metric_type: 'custom_attribute',
                project_ids: '', source: 'traces', page_size: 10, attribute_type: 'string' });
            expect(valueResponse.result.values).toEqual(selectedActor === scopeActors.ownerB ? [{ value: 'foreign', label: 'foreign', type: 'string' }] : []);
            scopeResults.push({ organizationId: selectedActor.organizationId, workspaceId: selectedActor.workspaceId, response, valueResponse });
          }
          await testInfo.attach('foreign-catalog-isolation', { contentType: 'application/json', body: JSON.stringify(scopeResults) });
        }
      }, { timeout: UI_READY });
    }
  } finally {
    await Promise.all(pending);
    await testInfo.attach('catalog-preview', { contentType: 'application/json', body: JSON.stringify({ discoveries, queries, responseErrors }) });
    await context.close();
  }
});
