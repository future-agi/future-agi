import { randomBytes, randomUUID } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { request, type Response } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { sendTrace, type OtlpAttributes } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// useDashboards.js / WidgetEditorView: native discovery, query and persistence.
const DASHBOARDS = '/tracer/dashboard/';
const METRICS = `${DASHBOARDS}metrics/`;
const VALUES = `${DASHBOARDS}filter_values/`;
const QUERY = `${DASHBOARDS}query/`;
const UI_READY = 60_000; // README: one ceiling for each complete browser phase.
const DAY = 86_400_000; // dashboard.py: 7D preset and UTC day buckets.
// dashboard.py:138–140,195 reads this numeric key unchanged and labels it ms.
const TTFT_KEY = 'gen_ai.server.time_to_first_token';
// Assertion-only proof anchors: neither changes ingestion or preview literals.
const EXPECTED_A_CHILD_TTFT = 20;
const EXPECTED_REOPENED_BETA_TTFT = 35;

type Filter = { column_id: string; property_id: string; display_name: string;
  source: string; output_type: string; filter_config: { filter_type: string;
    filter_op: string; filter_value: string[]; col_type: string; attribute_value_types?: string[] } };
type Metric = { id: string; name: string; property_id: string; display_name: string;
  type: string; source: string; aggregation: string; attribute_key?: string;
  attribute_type?: string; filters?: Filter[] };
type Config = { project_ids: string[]; time_range: { preset: string }; granularity: string;
  metrics: Metric[]; filters: Filter[]; breakdowns: Record<string, string>[] };
type Result = { query_complete: boolean; query_exact: boolean; granularity: string;
  time_range: { start: string; end: string }; metrics: { id: string; name: string;
    unit: string; aggregation: string; query_complete: boolean; query_exact: boolean;
    series: { name: string; data: { timestamp: string; value: number | null }[] }[] }[] };
type QueryReceipt = { config: Config; body: { result: Result }; status: number;
  startedAt: number; endedAt: number };
type CatalogProperty = { property_id: string; property_kind: string; name: string;
  category: string; source: string; type: string; output_type: string; role: string };
type ValueOption = { value: string; label: string; type?: string };
type CatalogReceipt = { request: Record<string, unknown>; status: number;
  body: { result: { metrics: CatalogProperty[] } } };
type ValueReceipt = { request: Record<string, unknown>; status: number;
  body: { result: { values: ValueOption[] } } };
type Project = { id: string; name: string; organization_id: string; workspace_id: string };
type Span = { id: string; trace_id: string; project_id: string; org_id: string;
  parent_span_id: string; observation_type: string; name: string; model: string;
  status: string; cost: number; ttft: number; amount: number; cohort: string;
  has_ttft: number; has_amount: number; has_cohort: number; start_us: string;
  end_us: string; version: string; is_deleted: number };
type Widget = { id: string; name: string; query_config: Config;
  chart_config: { chart_type: string; [key: string]: unknown } };

test('DASH-E2E-003: a metric-specific Model filter changes only that metric after save and reopen', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DASH-E2E-003', area: 'dashboards',
    userGoal: 'A metric-specific Model filter changes only that metric after save and reopen.',
    steps: ['ingest independently identified traces with explicit cost, numeric TTFT and a custom number in three scoped projects',
      'create a Table widget and select Cost, TTFT, trace/span counts and the custom sum',
      'select primary Project and cohort, then inspect the two Model groups',
      'apply Model Alpha only inside Cost and verify every other metric retains both groups',
      'save, reload and reopen the widget with exact values, controls and unchanged source facts'],
    backendChecks: [
      "Native catalog and selected property identities preserve this flow's source, types, choices and actor scope.",
      "This flow's exact publicly produced source identities and typed latest facts are present and unchanged outside its authorized UI actions.",
      "The preview, saved binding and reopened widget equal this flow's independently specified filtered and grouped result.",
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe: probe }, testInfo) => {
  test.setTimeout(660_000); // ASYNC_JOB60 + 2 SPAN_VISIBLE15 + 9 UI_READY60 +30 headroom.
  const prefix = `e2e-dash3-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const actor = scopeActors.ownerA;
  const cohortKey = `${prefix}.cohort`;
  const amountKey = `${prefix}.amount`;
  const foreignKey = `${prefix}.foreign-only`;
  const projectNames = [`${prefix}-primary`, `${prefix}-sibling`, `${prefix}-foreign`];
  const models = [`${prefix}-Alpha`, `${prefix}-Beta`, `${prefix}-Gamma`];
  // Check1 red: change ONLY this assertion witness to models[1]. It is not a selector/matcher input.
  const expectedCostModelValue = models[0];
  const dashboardName = `${prefix}-dashboard`;
  const widgetName = `${prefix}-widget`;
  const plantedDay = Math.floor(Date.now() / DAY) * DAY - DAY;
  const start = BigInt(plantedDay + DAY / 2) * 1_000_000n;
  // The approved T2 literals, not results read from dashboard aggregation SQL.
  const seeds = [
    { key: 'A', project: 0, model: models[0], cohort: 'alpha', ms: 50, cost: [.01, .02], ttft: [10, 20], amount: [1, 10] },
    { key: 'B', project: 0, model: models[1], cohort: 'alpha', ms: 100, cost: [.03, .04], ttft: [30, 40], amount: [2, 20] },
    { key: 'C', project: 0, model: models[2], cohort: 'beta', ms: 200, cost: [.05, .06], ttft: [50, 60], amount: [4, 40] },
    { key: 'S', project: 1, model: models[0], cohort: 'alpha', ms: 900, cost: [1, 2], ttft: [900, 1000], amount: [1000, 2000] },
    { key: 'F', project: 2, model: models[0], cohort: 'alpha', ms: 900, cost: [1, 2], ttft: [900, 1000], amount: [1000, 2000] },
  ].map(seed => ({ ...seed, traceId: randomUUID(), spanIds: [randomBytes(8).toString('hex'), randomBytes(8).toString('hex')] }));
  await testInfo.attach('planted-identities', { contentType: 'application/json', body: JSON.stringify({
    seeds, projectNames, models, cohortKey, amountKey, foreignKey, start: String(start), actors: scopeActors.evidence().actors,
  }) });
  const ingestion = await request.newContext();
  try {
    for (const seed of seeds) {
      const owner = seed.project === 2 ? scopeActors.ownerB : actor;
      const keys = scopeActors.owners.find(item => item.organizationId === owner.organizationId && item.workspaceId === owner.workspaceId)!;
      // adapter.go:259 explicit cost disables pricing; converter.go:394 preserves numeric Maps.
      const attributes: OtlpAttributes[] = [0, 1].map(index => ({
        'fi.span.kind': index === 0 ? 'chain' : 'llm', 'gen_ai.request.model': seed.model,
        'gen_ai.cost.total': seed.cost[index], [TTFT_KEY]: seed.ttft[index],
        [amountKey]: seed.amount[index], [cohortKey]: seed.cohort,
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
    return projects.map(p => ({ name: p.name, organization_id: p.organization_id, workspace_id: p.workspace_id }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, POLL.ASYNC_JOB).toEqual(projectNames.map((name, index) => ({ name,
    organization_id: index === 2 ? scopeActors.ownerB.organizationId : actor.organizationId,
    workspace_id: index === 2 ? scopeActors.ownerB.workspaceId : actor.workspaceId })).sort((a, b) => a.name.localeCompare(b.name)));
  const projectIds = projectNames.map(name => projects.find(p => p.name === name)!.id);
  const projectParams = { primary: projectIds[0], sibling: projectIds[1], foreign: projectIds[2] };
  // converter.go:368–405 / v2 columns.py: numeric Maps, physical cost and exact source IDs.
  const sourceSql = `SELECT id, trace_id, project_id, org_id, parent_span_id, observation_type, name, model, status, cost,
    attrs_number[{ttftKey:String}] AS ttft, attrs_number[{amountKey:String}] AS amount,
    attrs_string[{cohortKey:String}] AS cohort, mapContains(attrs_number, {ttftKey:String}) AS has_ttft,
    mapContains(attrs_number, {amountKey:String}) AS has_amount, mapContains(attrs_string, {cohortKey:String}) AS has_cohort,
    toUnixTimestamp64Micro(start_time) AS start_us, toUnixTimestamp64Micro(end_time) AS end_us,
    toString(_version) AS version, is_deleted FROM spans FINAL
    WHERE project_id IN ({primary:UUID}, {sibling:UUID}, {foreign:UUID}) ORDER BY id`;
  const sourceParams = { ...projectParams, ttftKey: TTFT_KEY, amountKey, cohortKey };
  const expectedSources = seeds.flatMap(seed => seed.spanIds.map((id, index) => ({ id, trace_id: seed.traceId,
    project_id: projectIds[seed.project], org_id: seed.project === 2 ? scopeActors.ownerB.organizationId : actor.organizationId,
    parent_span_id: index ? seed.spanIds[0] : '', observation_type: index ? 'llm' : 'chain',
    name: `${prefix}-${seed.key}-${index ? 'child' : 'root'}`, model: seed.model, status: 'OK', cost: seed.cost[index],
    ttft: seed.key === 'A' && index === 1 ? EXPECTED_A_CHILD_TTFT : seed.ttft[index], amount: seed.amount[index],
    cohort: seed.cohort, has_ttft: 1, has_amount: 1, has_cohort: 1,
    start_us: String(start / 1000n), end_us: String(start / 1000n + BigInt(seed.ms) * 1000n), is_deleted: 0,
  }))).sort((a, b) => a.id.localeCompare(b.id));
  await expect.poll(async () => (await probe.ch<Span>(sourceSql, sourceParams)).map(({ version, ...row }) => row),
    POLL.SPAN_VISIBLE).toEqual(expectedSources);
  const initialSources = await probe.ch<Span>(sourceSql, sourceParams);
  const traceSql = 'SELECT id, project_id FROM traces FINAL WHERE project_id IN ({primary:UUID}, {sibling:UUID}, {foreign:UUID}) ORDER BY toString(id)';
  const expectedTraces = seeds.map(seed => ({ id: seed.traceId, project_id: projectIds[seed.project] }))
    .sort((a, b) => a.id.localeCompare(b.id));
  await expect.poll(() => probe.ch(traceSql, projectParams), POLL.SPAN_VISIBLE).toEqual(expectedTraces);
  await testInfo.attach('source-identities', { contentType: 'application/json', body: JSON.stringify({ projects, expectedSources, initialSources, expectedTraces }) });

  // source_adapters.py: system labels; dashboard.py:195–200 units and distinct identity metrics.
  const choices = [
    { id: 'cost', name: 'Cost', aggregation: 'sum', aggregationLabel: 'Sum', unit: '$', custom: false },
    { id: 'time_to_first_token', name: 'Time to First Token', aggregation: 'avg', aggregationLabel: 'Average', unit: 'ms', custom: false },
    { id: 'trace_count', name: 'Traces', aggregation: 'count_distinct', aggregationLabel: 'Distinct Count', unit: '', custom: false },
    { id: 'span_count', name: 'Spans', aggregation: 'count_distinct', aggregationLabel: 'Distinct Count', unit: '', custom: false },
    { id: amountKey, name: amountKey, aggregation: 'sum', aggregationLabel: 'Sum', unit: '', custom: true },
  ];
  // WidgetEditorView:2901–2944; the custom number must not default to string.
  const metrics: Metric[] = choices.map(m => ({ id: m.id, name: m.id,
    property_id: m.custom ? `custom_attribute:${m.id}` : `system_attribute:traces:${m.id}`,
    display_name: m.name, type: m.custom ? 'custom_attribute' : 'system_metric', source: 'traces', aggregation: m.aggregation,
    ...(m.custom ? { attribute_key: m.id, attribute_type: 'number' } : {}),
  }));
  const globalFilters: Filter[] = [{ column_id: 'project', property_id: 'system_attribute:traces:project',
    display_name: 'Project', source: 'traces', output_type: 'string', filter_config: {
      filter_type: 'text', filter_op: 'in', filter_value: [projectIds[0]], col_type: 'SYSTEM_METRIC',
    } }];
  const localFilter: Filter = { column_id: 'model', property_id: 'system_attribute:traces:model',
    display_name: 'Model', source: 'traces', output_type: 'string', filter_config: {
      filter_type: 'text', filter_op: 'in', filter_value: [models[0]], col_type: 'SYSTEM_METRIC',
    } };
  const breakdowns = [{ name: 'model', property_id: 'system_attribute:traces:model', display_name: 'Model', type: 'system_metric', source: 'traces' }];
  const expectedConfig: Config = { project_ids: [], time_range: { preset: '7D' }, granularity: 'day', metrics, filters: globalFilters, breakdowns: [] };
  const context = await scopeActors.openContext(browser, actor);
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  const browserTimeZone = await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone);
  const queries: QueryReceipt[] = [];
  const catalogs: CatalogReceipt[] = [];
  const values: ValueReceipt[] = [];
  const responseErrors: { path: string; error: string }[] = [];
  const pending = new Set<Promise<void>>();
  // Only observe real traffic; the UI owns request bodies and exact-query polling.
  const captureResponse = (response: Response) => {
    const path = new URL(response.url()).pathname;
    if (![QUERY, METRICS, VALUES].includes(path) || response.request().method() !== 'POST') return;
    const capture = (async () => {
      const body = await response.json();
      const sent = response.request().postDataJSON();
      if (path === QUERY) queries.push({ config: sent, body, status: response.status(),
        startedAt: response.request().timing().startTime, endedAt: Date.now() });
      else if (path === METRICS) catalogs.push({ request: sent, body, status: response.status() });
      else values.push({ request: sent, body, status: response.status() });
    })().catch(error => { responseErrors.push({ path, error: String(error) }); });
    pending.add(capture);
    void capture.finally(() => pending.delete(capture));
  };
  page.on('response', captureResponse);
  let dashboardId = '';
  let savedWidget: Widget | undefined;
  try {
    await test.step('create and name a Table widget with 7D and Day selected', async () => {
      await page.goto('/dashboard/dashboards', { waitUntil: 'domcontentloaded' });
      const [created] = await Promise.all([
        page.waitForResponse(r => new URL(r.url()).pathname === DASHBOARDS && r.request().method() === 'POST', { timeout: UI_READY }),
        page.getByRole('button', { name: 'Create Dashboard', exact: true }).click(),
      ]);
      expect(created.status()).toBe(200);
      dashboardId = ((await created.json()) as { result: { id: string } }).result.id;
      await testInfo.attach('dashboard-id', { contentType: 'application/json', body: JSON.stringify({ dashboardId, dashboardName, widgetName }) });
      await page.getByRole('heading', { name: 'Untitled', exact: true }).click();
      await page.getByPlaceholder('Untitled Dashboard').fill(dashboardName);
      const [named] = await Promise.all([
        page.waitForResponse(r => new URL(r.url()).pathname === `${DASHBOARDS}${dashboardId}/` && ['PATCH', 'PUT'].includes(r.request().method()), { timeout: UI_READY }),
        page.getByPlaceholder('Untitled Dashboard').press('Enter'),
      ]);
      expect(named.status()).toBe(200);
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

    await test.step('select five native metric identities and explicit aggregations', async () => {
      for (const [index, choice] of choices.entries()) {
        if (index === 0) await page.getByText('Select Metric', { exact: true }).click();
        else await page.locator('.metric-section-title').click();
        const category = choice.custom ? 'Trace Attributes' : 'Traces';
        const categoryWire = choice.custom ? 'custom_attribute' : 'system_metric';
        await page.getByLabel(new RegExp(`^${category} property count: `)).locator('..').getByText(category, { exact: true }).click();
        const [searched] = await Promise.all([
          page.waitForResponse(r => new URL(r.url()).pathname === METRICS && r.request().method() === 'POST' &&
            r.request().postDataJSON().search === choice.name && r.request().postDataJSON().source === 'traces' &&
            r.request().postDataJSON().category === categoryWire, { timeout: UI_READY }),
          page.getByPlaceholder('Search metrics...').fill(choice.name),
        ]);
        expect(searched.status()).toBe(200);
        expect(searched.request().postDataJSON()).toMatchObject({ source: 'traces', category: categoryWire, role: 'metric', search: choice.name });
        const body = await searched.json() as CatalogReceipt['body'];
        const selected = body.result.metrics.filter(m => m.property_id === metrics[index].property_id);
        expect(selected).toHaveLength(1);
        expect(selected[0]).toMatchObject({ property_id: metrics[index].property_id, name: choice.id,
          property_kind: choice.custom ? 'custom_attribute' : 'system_attribute', category: categoryWire,
          source: 'traces', type: 'number', output_type: 'number', role: 'metric' });
        await testInfo.attach(`metric-${index}-catalog`, { contentType: 'application/json', body: JSON.stringify({ request: searched.request().postDataJSON(), body }) });
        await page.getByRole('button', { name: `${choice.name} (${choice.custom ? 'number, number' : 'number'}, Traces)`, exact: true }).click();
        // Existing metric-card structure and AggregationPicker from WidgetEditorView.
        const card = page.locator(`p[title="${choice.name}"]`).locator('../..');
        await card.locator('.MuiChip-clickable').click();
        await page.getByText(choice.aggregationLabel, { exact: true }).last().click();
      }
    }, { timeout: UI_READY });

    // Seven more phases: one independently seeded journey, not a source-matrix runner.
    for (const stage of ['project', 'cohort', 'model', 'metric-local', 'save', 'reload', 'reopen'] as const) {
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
          if (stage === 'cohort') globalFilters.push({ column_id: cohortKey, property_id: `custom_attribute:${cohortKey}`,
            display_name: cohortKey, source: 'traces', output_type: 'string', filter_config: {
              filter_type: 'text', filter_op: 'in', filter_value: ['alpha'], col_type: 'SPAN_ATTRIBUTE', attribute_value_types: ['string'],
            } });
        } else if (stage === 'model') {
          await page.locator('.breakdown-section-title').click();
          await page.getByPlaceholder('Search breakdown attributes...').fill('Model');
          await page.getByRole('button', { name: 'Model (string, Traces)', exact: true }).click();
          expectedConfig.breakdowns = breakdowns;
        } else if (stage === 'metric-local') {
          // WidgetEditorView:6346,3324,7968: the native button targets Cost, not global filters.
          const costCard = page.locator('p[title="Cost"]').locator('../..');
          await costCard.hover();
          await costCard.getByRole('button', { name: 'Add filter to this metric', exact: true }).click();
          await page.getByPlaceholder('Search filter attributes...').fill('Model');
          await page.getByRole('button', { name: 'Model (string, Traces)', exact: true }).click();
          // String metric-local filters auto-open this popup; no extra toggle or fixed delay.
          await expect(page.getByPlaceholder('Search...', { exact: true })).toBeVisible({ timeout: UI_READY });
          await page.getByPlaceholder('Search...', { exact: true }).fill(models[0]);
          await page.locator(`p[title="${models[0]}"]`).click();
          await page.getByRole('button', { name: 'Add', exact: true }).click();
          metrics[0].filters = [localFilter];
        } else if (stage === 'save') {
          const [saved] = await Promise.all([
            page.waitForResponse(r => new URL(r.url()).pathname === `${DASHBOARDS}${dashboardId}/widgets/` && r.request().method() === 'POST', { timeout: UI_READY }),
            page.getByRole('button', { name: 'Save', exact: true }).click(),
          ]);
          expect(saved.status()).toBe(200);
          const saveRequest = saved.request().postDataJSON() as Omit<Widget, 'id'>;
          savedWidget = ((await saved.json()) as { result: Widget }).result;
          expect(saveRequest.query_config).toEqual(expectedConfig);
          expect(saveRequest.chart_config.chart_type).toBe('table');
          expect(savedWidget).toMatchObject({ name: widgetName, query_config: expectedConfig, chart_config: saveRequest.chart_config });
          const rows = await probe.pg<{ id: string; name: string; dashboard_id: string; query_config: Config; chart_config: Widget['chart_config'] }>(
            'SELECT id, name, dashboard_id, query_config, chart_config FROM tracer_dashboardwidget WHERE id = $1 AND NOT deleted', [savedWidget.id]);
          expect(rows).toEqual([{ id: savedWidget.id, name: widgetName, dashboard_id: dashboardId,
            query_config: expectedConfig, chart_config: saveRequest.chart_config }]);
          expect(await probe.pg('SELECT id, name, workspace_id FROM tracer_dashboard WHERE id = $1 AND NOT deleted', [dashboardId]))
            .toEqual([{ id: dashboardId, name: dashboardName, workspace_id: actor.workspaceId }]);
          const detail = await actor.api.get<{ result: { id: string; name: string; workspace: string; widgets: Widget[] } }>(`${DASHBOARDS}${dashboardId}/`);
          expect(detail.result).toMatchObject({ id: dashboardId, name: dashboardName, workspace: actor.workspaceId });
          expect(detail.result.widgets.map(w => ({ id: w.id, name: w.name, query_config: w.query_config, chart_config: w.chart_config })))
            .toEqual([{ id: savedWidget.id, name: widgetName, query_config: expectedConfig, chart_config: saveRequest.chart_config }]);
          await testInfo.attach('saved-widget', { contentType: 'application/json', body: JSON.stringify({ saveRequest, savedWidget, rows, detail }) });
          await expect(page).toHaveURL(new RegExp(`/dashboard/dashboards/${dashboardId}$`), { timeout: UI_READY });
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
        const locallyFiltered = ['metric-local', 'reload', 'reopen'].includes(stage);
        if (locallyFiltered) {
          // Check1 proof mutates only expectedCostModelValue, never expectedConfig or the UI.
          expect(receipt.config.metrics[0].filters![0].filter_config.filter_value).toEqual([expectedCostModelValue]);
          for (const metric of receipt.config.metrics.slice(1)) expect(metric).not.toHaveProperty('filters');
        }
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
          .toEqual(choices.map(({ id, name, unit, aggregation }) => ({ id, name, unit, aggregation })));
        const grouped = expectedConfig.breakdowns.length > 0;
        // Independent approved result vectors; no production aggregate or readback is an oracle.
        const vectors = grouped ? [[.03, 15, 1, 2, 11], [.07, 35, 1, 2, 22]] :
          [stage === 'project' ? [.21, 35, 3, 6, 77] : [.10, 25, 2, 4, 33]];
        if (stage === 'reopen') vectors[1][1] = EXPECTED_REOPENED_BETA_TTFT;
        const expectedColumns: Record<string, { values: (number | null)[]; unit: string }> = {};
        for (const [metricIndex, metric] of result.metrics.entries()) {
          expect(metric).toMatchObject({ query_complete: true, query_exact: true });
          const names = grouped ? models.slice(0, locallyFiltered && metricIndex === 0 ? 1 : 2) : ['total'];
          expect(metric.series.map(s => s.name).sort()).toEqual([...names].sort());
          for (const [groupIndex, name] of names.entries()) {
            const series = metric.series.find(s => s.name === name)!;
            const expectedValues = buckets.map(bucket => bucket === plantedDay ? vectors[groupIndex][metricIndex] : null);
            expect(series.data.map(p => Date.parse(p.timestamp))).toEqual(buckets);
            if (metricIndex === 0) {
              for (const [index, expected] of expectedValues.entries()) {
                const actual = series.data[index].value;
                if (expected === null) expect(actual).toBeNull();
                else {
                  expect(typeof actual).toBe('number');
                  expect(Number.isFinite(actual)).toBe(true);
                  expect(Math.abs(actual! - expected)).toBeLessThan(1e-9);
                }
              }
            } else expect(series.data.map(p => p.value)).toEqual(expectedValues);
            const choice = choices[metricIndex];
            let label = grouped ? `${choice.name} / ${name} (${choice.aggregation})` : `${choice.name} (${choice.aggregation})`;
            // WidgetChart.jsx:854 adds nonempty units; editor table omits them.
            if (stage === 'reload' && choice.unit) label += ` (${choice.unit})`;
            expectedColumns[label] = { values: expectedValues, unit: choice.unit };
          }
        }
        expect(Object.keys(expectedColumns)).toHaveLength(grouped ? locallyFiltered ? 9 : 10 : 5);
        const table = stage === 'reload' ? page.locator(`[data-widget-id="${savedWidget!.id}"]`).getByRole('table') : page.getByRole('table');
        await expect(table).toBeVisible({ timeout: UI_READY });
        await expect.poll(async () => (await table.locator('thead th').allTextContents()).slice(1).sort(),
          { timeout: UI_READY }).toEqual(Object.keys(expectedColumns).sort());
        const headers = await table.locator('thead th').allTextContents();
        expect(headers[0]).toBe('Time');
        expect(headers.slice(1).sort()).toEqual(Object.keys(expectedColumns).sort());
        await expect(table.locator('tbody tr')).toHaveCount(buckets.length, { timeout: UI_READY });
        await expect(table.locator('tbody tr td:first-child')).toHaveText(buckets.map(bucket =>
          new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: browserTimeZone }).format(bucket)), { timeout: UI_READY });
        for (const [column, label] of headers.slice(1).entries()) {
          const expected = expectedColumns[label];
          // widgetUtils.js: currency prefix, ms suffix, two decimals; no count/custom unit.
          await expect(table.locator(`tbody tr td:nth-child(${column + 2})`)).toHaveText(expected.values.map(value => {
            if (value === null) return '-';
            if (stage !== 'reload') return Number.isInteger(value) ? String(value) : value.toFixed(2);
            if (expected.unit === '$') return `$${value.toFixed(2)}`;
            if (expected.unit === 'ms') return `${value.toFixed(2)} ms`;
            return value.toFixed(2);
          }), { timeout: UI_READY });
        }
        if (stage === 'metric-local' || stage === 'reopen') {
          const costCard = page.locator('p[title="Cost"]').locator('../..');
          await expect(costCard.getByText('Model', { exact: true })).toBeVisible({ timeout: UI_READY });
          await expect(costCard.getByText(models[0], { exact: true })).toBeVisible({ timeout: UI_READY });
          for (const choice of choices.slice(1)) {
            const card = page.locator(`p[title="${choice.name}"]`).locator('../..');
            await expect(card.getByText('Model', { exact: true })).toHaveCount(0, { timeout: UI_READY });
          }
        }
        if (stage === 'reopen') {
          for (const choice of choices) {
            const card = page.locator(`p[title="${choice.name}"]`).locator('../..');
            await expect(card.getByText(choice.aggregationLabel, { exact: true })).toBeVisible({ timeout: UI_READY });
          }
          for (const selected of ['Project', cohortKey, 'alpha']) {
            await expect(page.getByText(selected, { exact: true })).toBeVisible({ timeout: UI_READY });
          }
          // WidgetEditorView's global filter card and FilterValueLabel must resolve the UUID to its name.
          await expect(page.getByText('Project', { exact: true }).locator('../..').locator('.filter-value-name'))
            .toHaveText(projectNames[0], { timeout: UI_READY });
          await expect(page.locator('.breakdown-section-title').locator('../..').getByText('Model', { exact: true })).toBeVisible({ timeout: UI_READY });
          await expect(page.getByRole('combobox').filter({ hasText: 'Day' })).toBeVisible({ timeout: UI_READY });
          // The preset chips have no semantic selected state; exact native query above proves 7D.
          await expect(page.getByText('7D', { exact: true })).toBeVisible({ timeout: UI_READY });
          const finalSources = await probe.ch<Span>(sourceSql, sourceParams);
          const finalTraces = await probe.ch(traceSql, projectParams);
          expect(finalSources).toEqual(initialSources);
          expect(finalTraces).toEqual(expectedTraces);
          await testInfo.attach('unchanged-sources', { contentType: 'application/json', body: JSON.stringify({ initialSources, finalSources, finalTraces }) });
        }
        await testInfo.attach(`${stage}-table`, { contentType: 'image/png', body: await page.screenshot() });

        if (stage === 'cohort' || stage === 'metric-local') {
          await Promise.all(pending);
          const properties = stage === 'cohort' ? [
            { id: 'system_attribute:traces:project', name: 'project', kind: 'system_attribute', category: 'system_metric' },
            { id: `custom_attribute:${cohortKey}`, name: cohortKey, kind: 'custom_attribute', category: 'custom_attribute' },
          ] : [{ id: 'system_attribute:traces:model', name: 'model', kind: 'system_attribute', category: 'system_metric' }];
          for (const property of properties) {
            const selected = catalogs.filter(c => c.status === 200).flatMap(c => c.body.result.metrics).filter(m => m.property_id === property.id);
            expect(selected.length, `catalog ${property.id}`).toBeGreaterThan(0);
            for (const entry of selected) expect(entry).toMatchObject({ property_id: property.id, name: property.name,
              property_kind: property.kind, category: property.category, source: 'traces', type: 'string', output_type: 'string', role: 'dimension' });
          }
          const propertyId = stage === 'cohort' ? `custom_attribute:${cohortKey}` : 'system_attribute:traces:model';
          const relevantValues = values.filter(v => v.request.property_id === propertyId);
          expect(relevantValues.length).toBeGreaterThan(0);
          for (const discovery of relevantValues) {
            expect(discovery.status).toBe(200);
            expect(discovery.request).toMatchObject({ property_id: propertyId, metric_name: stage === 'cohort' ? cohortKey : 'model',
              metric_type: stage === 'cohort' ? 'custom_attribute' : 'system_metric', project_ids: '', source: 'traces' });
            if (stage === 'cohort') expect(discovery.request.attribute_type).toBe('string');
          }
          const expectedValue = stage === 'cohort' ? 'alpha' : models[0];
          const selectedValues = relevantValues.flatMap(v => v.body.result.values).filter(v => v.value === expectedValue);
          expect(selectedValues.length).toBeGreaterThan(0);
          for (const option of selectedValues) {
            expect(option).toMatchObject({ value: expectedValue, label: expectedValue });
            if (stage === 'cohort') expect(option.type).toBe('string');
          }
          if (stage === 'cohort') {
            const projectValues = values.filter(v => v.request.property_id === 'system_attribute:traces:project');
            expect(projectValues.length).toBeGreaterThan(0);
            for (const discovery of projectValues) expect(discovery.status).toBe(200);
            const options = projectValues.flatMap(v => v.body.result.values);
            expect(options.some(v => v.value === projectIds[0] && v.label === projectNames[0])).toBe(true);
            expect(options.filter(v => v.value === projectIds[2] || v.label === projectNames[2])).toEqual([]);
            const scopeResults = [];
            for (const selectedActor of [scopeActors.ownerB, actor, scopeActors.withWorkspace(actor, scopeActors.emptyWorkspace.id)]) {
              const catalog = await selectedActor.api.post<CatalogReceipt['body']>(METRICS,
                { source: 'traces', category: 'custom_attribute', search: foreignKey, cursor_mode: true, page_size: 25 });
              expect(catalog.result.metrics.map(m => m.property_id)).toEqual(selectedActor === scopeActors.ownerB ? [`custom_attribute:${foreignKey}`] : []);
              const scopedValues = await selectedActor.api.post<ValueReceipt['body']>(VALUES,
                { property_id: `custom_attribute:${foreignKey}`, metric_name: foreignKey, metric_type: 'custom_attribute',
                  project_ids: '', source: 'traces', page_size: 10, attribute_type: 'string' });
              expect(scopedValues.result.values).toEqual(selectedActor === scopeActors.ownerB ? [{ value: 'foreign', label: 'foreign', type: 'string' }] : []);
              scopeResults.push({ organizationId: selectedActor.organizationId, workspaceId: selectedActor.workspaceId, catalog, scopedValues });
            }
            await testInfo.attach('foreign-catalog-value-isolation', { contentType: 'application/json', body: JSON.stringify(scopeResults) });
          }
        }
      }, { timeout: UI_READY });
    }
  } finally {
    page.off('response', captureResponse);
    try {
      await Promise.all(pending);
      await testInfo.attach('catalog-preview', { contentType: 'application/json', body: JSON.stringify({ catalogs, values, queries, responseErrors }) });
    } finally { await context.close(); }
  }
});
