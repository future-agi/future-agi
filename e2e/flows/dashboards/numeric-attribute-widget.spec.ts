import { randomBytes, randomUUID } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { request, type Response } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { sendTrace, type OtlpAttributes } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// frontend/src/hooks/useDashboards.js + futureagi/tracer/views/dashboard.py:
// native dashboard create/detail/widgets, property discovery, values and preview query.
const DASHBOARDS = '/tracer/dashboard/';
const METRICS = `${DASHBOARDS}metrics/`;
const VALUES = `${DASHBOARDS}filter_values/`;
const QUERY = `${DASHBOARDS}query/`;
const UI_READY = 60_000; // One ceiling for each complete approved browser phase.
const DAY = 86_400_000; // dashboard.py: 7D preset and UTC day buckets.
// ASSERTION ONLY: change one expected value to 11 for its one-sided proof.
const EXPECTED_NUMERIC_FILTER_VALUE = 10; // Check1: emitted scalar, not the request matcher.
const EXPECTED_A_CHILD_NUMBER = 10; // Check2: source expectation, not ingestion.
const EXPECTED_REOPENED_SUM = 10; // Check3: reopened output, not preview/input/config.

type Filter = { column_id: string; property_id: string; display_name: string;
  source: string; output_type: string; filter_config: { filter_type: string;
    filter_op: string; filter_value: number | string[]; col_type: string } };
type Metric = { id: string; name: string; property_id: string; display_name: string;
  type: string; source: string; aggregation: string; attribute_key?: string; attribute_type?: string };
type Config = { project_ids: string[]; time_range: { preset: string }; granularity: string;
  metrics: Metric[]; filters: Filter[]; breakdowns: Record<string, string>[] };
type Result = { query_complete: boolean; query_exact: boolean; granularity: string;
  time_range: { start: string; end: string }; metrics: { id: string; name: string;
    unit: string; aggregation: string; query_complete: boolean; query_exact: boolean;
    series: { name: string; data: { timestamp: string; value: number | null }[] }[] }[] };
type QueryReceipt = { config: Config; body: { result: Result }; status: number;
  startedAt: number; endedAt: number };
type CatalogProperty = { property_id: string; property_kind: string; name: string; display_name: string;
  category: string; source: string; type: string; output_type: string; role: string;
  data_type?: string; attribute_types?: string[]; attribute_types_exact?: boolean; allowed_aggregations?: string[] };
type CatalogBody = { result: { metrics: CatalogProperty[]; has_more: boolean; next_cursor: string | null } };
type ValueOption = { value: number | string; label: string; type?: string };
type ValueBody = { result: { values: ValueOption[]; attribute_types: string[] } };
type CatalogReceipt = { request: Record<string, unknown>; status: number; body: CatalogBody };
type ValueReceipt = { request: Record<string, unknown>; status: number; body: ValueBody };
type Project = { id: string; name: string; organization_id: string; workspace_id: string };
type Span = { id: string; trace_id: string; project_id: string; org_id: string;
  parent_span_id: string; observation_type: string; name: string; model: string;
  status: string; cost: number; number_value: number; string_value: string;
  has_number: number; has_string: number; foreign_value: string; has_foreign: number;
  start_us: string; end_us: string; version: string; is_deleted: number };
type Widget = { id: string; name: string; query_config: Config;
  chart_config: { chart_type: string; [key: string]: unknown } };

test('DASH-E2E-004: a saved numeric attribute widget preserves numeric filtering and groups', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DASH-E2E-004', area: 'dashboards',
    userGoal: 'A saved numeric attribute widget preserves numeric filtering and groups.',
    steps: ['ingest five independently identified traces with native numbers and a foreign numeric-looking string',
      'create a Table widget and discover the scoped numeric sum and Spans metrics',
      'select primary Project, native numeric Equals 10 and the same numeric breakdown',
      'save, reload and reopen the exact binding, groups and native controls',
      'try disjoint 999, restore 10 and verify unchanged source and saved facts'],
    backendChecks: [
      "Native catalog and selected property identities preserve this flow's source, types, choices and actor scope.",
      "This flow's exact publicly produced source identities and typed latest facts are present and unchanged outside its authorized UI actions.",
      "The preview, saved binding and reopened widget equal this flow's independently specified filtered and grouped result.",
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe: probe }, testInfo) => {
  test.setTimeout(600_000); // ASYNC_JOB60 + 2 SPAN_VISIBLE15 + 8 UI_READY60 +30 headroom.
  const uiExpect = expect.configure({ timeout: UI_READY }); // Browser assertions only; source/API polls keep their own bounds.
  const prefix = `e2e-dash4-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const actor = scopeActors.ownerA;
  const emptyActor = scopeActors.withWorkspace(actor, scopeActors.emptyWorkspace.id);
  const amountKey = `${prefix}.amount`;
  const foreignKey = `${prefix}.foreign-only`;
  const propertyId = `custom_attribute:${amountKey}`;
  const projectNames = [`${prefix}-primary`, `${prefix}-sibling`, `${prefix}-foreign`];
  const dashboardName = `${prefix}-dashboard`;
  const widgetName = `${prefix}-widget`;
  const plantedDay = Math.floor(Date.now() / DAY) * DAY - DAY;
  const start = BigInt(plantedDay + DAY / 2) * 1_000_000n;
  // reader.py:189 requires all-number across A's workspace. Mixed K lives ONLY in B.
  const seeds = [
    { key: 'A', project: 0, ms: 50, amount: [1, 10] },
    { key: 'B', project: 0, ms: 100, amount: [2, 20] },
    { key: 'C', project: 0, ms: 200, amount: [4, 40] },
    { key: 'S', project: 1, ms: 900, amount: [10, 1000] },
    { key: 'F', project: 2, ms: 900, amount: [10, '10'] },
  ].map(seed => ({ ...seed, model: `${prefix}-${seed.key}-model`, traceId: randomUUID(),
    spanIds: [randomBytes(8).toString('hex'), randomBytes(8).toString('hex')] }));
  await testInfo.attach('planted-identities', { contentType: 'application/json', body: JSON.stringify({
    seeds, projectNames, amountKey, foreignKey, start: String(start), actors: scopeActors.evidence().actors,
  }) });
  const ingestion = await request.newContext();
  try {
    for (const seed of seeds) {
      const owner = seed.project === 2 ? scopeActors.ownerB : actor;
      const keys = scopeActors.owners.find(item => item.organizationId === owner.organizationId && item.workspaceId === owner.workspaceId)!;
      // otlp.ts preserves number vs string; explicit zero cost disables model pricing.
      const attributes: OtlpAttributes[] = [0, 1].map(index => ({
        'fi.span.kind': index === 0 ? 'chain' : 'llm', 'gen_ai.request.model': seed.model,
        'gen_ai.cost.total': 0, [amountKey]: seed.amount[index],
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
  // converter.go:368–406 / 002_spans_v2.sql: typed Maps, exact physical identities and version.
  const sourceSql = `SELECT id, trace_id, project_id, org_id, parent_span_id, observation_type, name, model, status, cost,
    attrs_number[{amountKey:String}] AS number_value, attrs_string[{amountKey:String}] AS string_value,
    mapContains(attrs_number, {amountKey:String}) AS has_number, mapContains(attrs_string, {amountKey:String}) AS has_string,
    attrs_string[{foreignKey:String}] AS foreign_value, mapContains(attrs_string, {foreignKey:String}) AS has_foreign,
    toUnixTimestamp64Micro(start_time) AS start_us, toUnixTimestamp64Micro(end_time) AS end_us,
    toString(_version) AS version, is_deleted FROM spans FINAL
    WHERE project_id IN ({primary:UUID}, {sibling:UUID}, {foreign:UUID}) ORDER BY id`;
  const sourceParams = { ...projectParams, amountKey, foreignKey };
  const expectedSources = seeds.flatMap(seed => seed.spanIds.map((id, index) => ({ id, trace_id: seed.traceId,
    project_id: projectIds[seed.project], org_id: seed.project === 2 ? scopeActors.ownerB.organizationId : actor.organizationId,
    parent_span_id: index ? seed.spanIds[0] : '', observation_type: index ? 'llm' : 'chain',
    name: `${prefix}-${seed.key}-${index ? 'child' : 'root'}`, model: seed.model, status: 'OK', cost: 0,
    number_value: seed.key === 'A' && index === 1 ? EXPECTED_A_CHILD_NUMBER : typeof seed.amount[index] === 'number' ? seed.amount[index] : 0,
    string_value: typeof seed.amount[index] === 'string' ? seed.amount[index] : '',
    has_number: typeof seed.amount[index] === 'number' ? 1 : 0, has_string: typeof seed.amount[index] === 'string' ? 1 : 0,
    foreign_value: seed.project === 2 ? 'foreign' : '', has_foreign: seed.project === 2 ? 1 : 0,
    start_us: String(start / 1000n), end_us: String(start / 1000n + BigInt(seed.ms) * 1000n), is_deleted: 0,
  }))).sort((a, b) => a.id.localeCompare(b.id));
  let initialSources: Span[] = [];
  await expect.poll(async () => {
    initialSources = await probe.ch<Span>(sourceSql, sourceParams);
    return initialSources.map(({ version, ...row }) => row);
  }, POLL.SPAN_VISIBLE).toEqual(expectedSources); // Keep THIS validated read, including versions.
  const traceSql = 'SELECT id, project_id FROM traces FINAL WHERE project_id IN ({primary:UUID}, {sibling:UUID}, {foreign:UUID}) ORDER BY toString(id)';
  const expectedTraces = seeds.map(seed => ({ id: seed.traceId, project_id: projectIds[seed.project] }))
    .sort((a, b) => a.id.localeCompare(b.id));
  await expect.poll(() => probe.ch(traceSql, projectParams), POLL.SPAN_VISIBLE).toEqual(expectedTraces);
  await testInfo.attach('source-identities', { contentType: 'application/json', body: JSON.stringify({ projects, expectedSources, initialSources, expectedTraces }) });

  const numberProperty = { property_id: propertyId, property_kind: 'custom_attribute', category: 'custom_attribute',
    name: amountKey, display_name: amountKey, source: 'traces', type: 'number', output_type: 'number', data_type: 'number',
    role: 'metric', attribute_types: ['number'], attribute_types_exact: false,
    allowed_aggregations: ['avg', 'count', 'count_distinct', 'max', 'min', 'sum'] };
  const mixedProperty = { ...numberProperty, type: 'json', output_type: 'json', data_type: 'json', role: 'dimension',
    attribute_types: ['number', 'string'], allowed_aggregations: ['count', 'count_distinct'] };
  const choices = [
    { id: amountKey, name: amountKey, aggregation: 'sum', aggregationLabel: 'Sum', custom: true },
    { id: 'span_count', name: 'Spans', aggregation: 'count_distinct', aggregationLabel: 'Distinct Count', custom: false },
  ];
  // Independent raw native config: no serializer defaults, response-derived fields or local filters.
  const metrics: Metric[] = choices.map(m => ({ id: m.id, name: m.id,
    property_id: m.custom ? propertyId : 'system_attribute:traces:span_count', display_name: m.name,
    type: m.custom ? 'custom_attribute' : 'system_metric', source: 'traces', aggregation: m.aggregation,
    ...(m.custom ? { attribute_key: amountKey, attribute_type: 'number' } : {}),
  }));
  const projectFilter: Filter = { column_id: 'project', property_id: 'system_attribute:traces:project',
    display_name: 'Project', source: 'traces', output_type: 'string', filter_config: {
      filter_type: 'text', filter_op: 'in', filter_value: [projectIds[0]], col_type: 'SYSTEM_METRIC' } };
  const numericFilter: Filter = { column_id: amountKey, property_id: propertyId, display_name: amountKey,
    source: 'traces', output_type: 'number', filter_config: {
      filter_type: 'number', filter_op: 'equals', filter_value: 10, col_type: 'SPAN_ATTRIBUTE' } };
  const breakdown = { name: amountKey, property_id: propertyId, display_name: amountKey,
    type: 'custom_attribute', source: 'traces', attribute_type: 'number' };
  const expectedConfig: Config = { project_ids: [], time_range: { preset: '7D' }, granularity: 'day',
    metrics, filters: [projectFilter], breakdowns: [] };
  // This independent positive snapshot never aliases the temporary 999 edits below.
  const expectedSavedConfig: Config = structuredClone({ ...expectedConfig, filters: [projectFilter, numericFilter], breakdowns: [breakdown] });
  const context = await scopeActors.openContext(browser, actor);
  const queries: QueryReceipt[] = [];
  const catalogs: CatalogReceipt[] = [];
  const values: ValueReceipt[] = [];
  const scopeResults: unknown[] = [];
  const responseErrors: { path: string; error: string }[] = [];
  const pending = new Set<Promise<void>>();
  try {
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    const browserTimeZone = await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone);
    // Observe real native requests only. Fresh queries match request start, not response arrival.
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
    const globalSection = page.locator('.filter-section-title').locator('../..');
    const numericCard = globalSection.getByText(amountKey, { exact: true }).locator('../..');
    try {
      await test.step('create and name a Table widget with 7D and Day selected', async () => {
        await page.goto('/dashboard/dashboards', { waitUntil: 'domcontentloaded' });
        const [created] = await Promise.all([
          page.waitForResponse(r => new URL(r.url()).pathname === DASHBOARDS && r.request().method() === 'POST'),
          page.getByRole('button', { name: 'Create Dashboard', exact: true }).click(),
        ]);
        expect(created.status()).toBe(200);
        dashboardId = ((await created.json()) as { result: { id: string } }).result.id;
        await testInfo.attach('dashboard-id', { contentType: 'application/json', body: JSON.stringify({ dashboardId, dashboardName, widgetName }) });
        await page.getByRole('heading', { name: 'Untitled', exact: true }).click();
        await page.getByPlaceholder('Untitled Dashboard').fill(dashboardName);
        const [named] = await Promise.all([
          page.waitForResponse(r => new URL(r.url()).pathname === `${DASHBOARDS}${dashboardId}/` && ['PATCH', 'PUT'].includes(r.request().method())),
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

      await test.step('discover numeric and Spans metrics with scoped type and value witnesses', async () => {
        await page.getByText('Select Metric', { exact: true }).click();
        // Native cached lanes may deduplicate a repeated search; require both the visible
        // option and its actual settled lane receipt, not an invented second HTTP request.
        for (const lane of [
          { category: 'Trace Attributes', wire: 'custom_attribute', source: 'traces', search: '' },
          { category: 'All', wire: '', source: '', search: amountKey },
          { category: 'Trace Attributes', wire: 'custom_attribute', source: 'traces', search: amountKey },
          { category: 'Trace Attributes', wire: 'custom_attribute', source: 'traces', search: '' },
          { category: 'Trace Attributes', wire: 'custom_attribute', source: 'traces', search: amountKey },
        ]) {
          await page.getByLabel(new RegExp(`^${lane.category} property count: `)).locator('..').getByText(lane.category, { exact: true }).click();
          await page.getByPlaceholder('Search metrics...').fill(lane.search);
          await uiExpect(page.getByRole('button', { name: `${amountKey} (number, number, Traces)`, exact: true })).toBeVisible();
          await uiExpect(page.getByRole('button', { name: `${amountKey} (number, number, Traces)`, exact: true })).toHaveCount(1);
          await uiExpect(page.getByRole('button', { name: `${amountKey} (string, string, Traces)`, exact: true })).toHaveCount(0);
          await expect.poll(() => catalogs.some(c => c.status === 200 && c.request.cursor_mode === true &&
            c.request.role === 'metric' && (c.request.category || '') === lane.wire && (c.request.source || '') === lane.source &&
            (c.request.search || '') === lane.search && c.body.result.metrics.some(m => m.property_id === propertyId)), { timeout: UI_READY }).toBe(true);
          const receipt = catalogs.find(c => c.status === 200 && c.request.cursor_mode === true && c.request.role === 'metric' &&
            (c.request.category || '') === lane.wire && (c.request.source || '') === lane.source &&
            (c.request.search || '') === lane.search && c.body.result.metrics.some(m => m.property_id === propertyId))!;
          const selected = receipt.body.result.metrics.filter(m => m.property_id === propertyId);
          expect(selected).toHaveLength(1);
          expect(selected[0]).toMatchObject(numberProperty);
          expect(receipt.body.result).toMatchObject({ query_complete: true, query_exact: false, query_status: 'complete', query_provenance: 'current_property_catalog' });
          expect(Number.isInteger(receipt.request.page_size)).toBe(true);
          expect(Number(receipt.request.page_size)).toBeGreaterThan(0);
          await testInfo.attach(`metric-lane-${lane.category}-${lane.search || 'unsearched'}`, { contentType: 'application/json', body: JSON.stringify(receipt) });
        }
        await page.getByRole('button', { name: `${amountKey} (number, number, Traces)`, exact: true }).click();
        await page.locator('.metric-section-title').click();
        await page.getByLabel(/^Traces property count: /).locator('..').getByText('Traces', { exact: true }).click();
        await page.getByPlaceholder('Search metrics...').fill('Spans');
        await expect.poll(() => catalogs.some(c => c.status === 200 && c.request.role === 'metric' && c.request.search === 'Spans' &&
          c.request.source === 'traces' && c.request.category === 'system_metric'), { timeout: UI_READY }).toBe(true);
        const spanCatalog = catalogs.find(c => c.status === 200 && c.request.role === 'metric' && c.request.search === 'Spans' &&
          c.request.source === 'traces' && c.request.category === 'system_metric')!;
        const spansProperty = spanCatalog.body.result.metrics.filter(m => m.property_id === 'system_attribute:traces:span_count');
        expect(spansProperty).toHaveLength(1);
        expect(spansProperty[0]).toMatchObject({ property_id: 'system_attribute:traces:span_count', property_kind: 'system_attribute',
          category: 'system_metric', name: 'span_count', display_name: 'Spans', source: 'traces', type: 'number', output_type: 'number', role: 'metric' });
        await page.getByRole('button', { name: 'Spans (number, Traces)', exact: true }).click();
        for (const choice of choices) {
          const card = page.locator(`p[title="${choice.name}"]`).locator('../..');
          await card.locator('.MuiChip-clickable').click();
          await page.getByText(choice.aggregationLabel, { exact: true }).last().click();
        }

        // Additional API witnesses, NOT a fictitious numeric UI value popup.
        await Promise.all([actor, scopeActors.ownerB, emptyActor].map(async selectedActor => {
          const isForeign = selectedActor === scopeActors.ownerB;
          const isEmpty = selectedActor === emptyActor;
          for (const key of [amountKey, foreignKey]) {
            for (const role of key === amountKey ? ['metric', ''] : ['']) {
              const input = { source: 'traces', category: 'custom_attribute', search: key, cursor_mode: true, page_size: 25,
                ...(role ? { role } : {}) };
              const catalog = await scopeActors.send<CatalogBody>(selectedActor, 'POST', METRICS, input);
              scopeResults.push({ workspaceId: selectedActor.workspaceId, organizationId: selectedActor.organizationId, path: METRICS, input, ...catalog });
              expect(catalog.status).toBe(200);
              const visible = key === amountKey ? !isEmpty && (!role || !isForeign) : isForeign;
              expect(catalog.body.result.metrics.map(m => m.property_id)).toEqual(visible ? [`custom_attribute:${key}`] : []);
              expect(catalog.body.result).toMatchObject({ has_more: false, next_cursor: null, query_complete: true,
                query_exact: false, query_status: 'complete', query_provenance: 'current_property_catalog' });
              if (visible && key === amountKey) expect(catalog.body.result.metrics[0]).toMatchObject(isForeign ? mixedProperty : numberProperty);
            }
          }
          const valueReads = [
            { key: amountKey, attributeType: '', projects: '', expected: isEmpty ? [] : isForeign ? [10, '10'] : [1, 2, 4, 10, 20, 40, 1000] },
            { key: foreignKey, attributeType: 'string', projects: '', expected: isForeign ? ['foreign'] : [] },
            ...(isForeign ? [
              { key: amountKey, attributeType: 'number', projects: '', expected: [10] },
              { key: amountKey, attributeType: 'string', projects: '', expected: ['10'] },
            ] : selectedActor === actor ? [
              { key: amountKey, attributeType: '', projects: projectIds[0], expected: [1, 2, 4, 10, 20, 40] },
            ] : []),
          ];
          for (const read of valueReads) {
            const input = { property_id: `custom_attribute:${read.key}`, metric_name: read.key, metric_type: 'custom_attribute',
              source: 'traces', project_ids: read.projects, page_size: 10, ...(read.attributeType ? { attribute_type: read.attributeType } : {}) };
            const result = await scopeActors.send<ValueBody>(selectedActor, 'POST', VALUES, input);
            scopeResults.push({ workspaceId: selectedActor.workspaceId, organizationId: selectedActor.organizationId, path: VALUES, input, ...result });
            expect(result.status).toBe(200);
            expect(result.body.result).toMatchObject({ query_complete: true, query_status: 'complete', query_exact: false,
              has_more: false, next_cursor: null, browse_status: 'exhausted', attribute_types_exact: false,
              query_provenance: 'current_property_catalog', attribute_types: read.key === foreignKey ? isForeign ? ['string'] : [] :
                isEmpty ? [] : isForeign ? ['number', 'string'] : ['number'] });
            // Preserve JSON types; number 10 and string "10" cannot collapse into one witness.
            const actual = [...result.body.result.values].sort((a, b) => String(a.type).localeCompare(String(b.type)) ||
              (typeof a.value === 'number' && typeof b.value === 'number' ? a.value - b.value : String(a.value).localeCompare(String(b.value))));
            expect(actual).toEqual(read.expected.map(value => ({ value, label: String(value), type: typeof value })));
          }
        }));
      }, { timeout: UI_READY });

      // Six remaining phases of one native journey; numeric/disjoint each share ONE 60s ceiling.
      for (const stage of ['project', 'numeric', 'save', 'reload', 'reopen', 'disjoint'] as const) {
        await test.step(stage, async () => {
          const actions = stage === 'numeric' ? ['equals', 'group'] : stage === 'disjoint' ? ['999', 'restore'] : [stage];
          for (const action of actions) {
            const since = queries.length;
            const actionStartedAt = Date.now();
            const catalogSince = catalogs.length;
            if (action === 'project') {
              await page.locator('.filter-section-title').click();
              await page.getByLabel(/^Traces property count: /).locator('..').getByText('Traces', { exact: true }).click();
              await page.getByPlaceholder('Search filter attributes...').fill('Project');
              await page.getByRole('button', { name: 'Project (string, Traces)', exact: true }).click();
              await page.getByText('Select value...', { exact: true }).click();
              await page.getByPlaceholder('Search...', { exact: true }).fill(projectNames[0]);
              await page.locator(`p[title="${projectNames[0]}"]`).click();
              await page.getByRole('button', { name: 'Add', exact: true }).click();
            } else if (action === 'equals' || action === 'group') {
              await page.locator(action === 'equals' ? '.filter-section-title' : '.breakdown-section-title').click();
              await page.getByLabel(/^Trace Attributes property count: /).locator('..').getByText('Trace Attributes', { exact: true }).click();
              await page.getByPlaceholder(action === 'equals' ? 'Search filter attributes...' : 'Search breakdown attributes...').fill(amountKey);
              await page.getByRole('button', { name: `${amountKey} (number, number, Traces)`, exact: true }).click();
              if (action === 'equals') {
                await numericCard.getByRole('combobox').click();
                await page.getByRole('option', { name: 'Equals', exact: true }).click();
                await uiExpect(numericCard.getByRole('spinbutton')).toHaveAttribute('type', 'number');
                await uiExpect(numericCard.getByRole('spinbutton')).toHaveAttribute('placeholder', 'Value');
                await numericCard.getByRole('spinbutton').fill('10');
                expectedConfig.filters.push(numericFilter);
              } else expectedConfig.breakdowns = [breakdown];
            } else if (action === 'save') {
              const [saved] = await Promise.all([
                page.waitForResponse(r => new URL(r.url()).pathname === `${DASHBOARDS}${dashboardId}/widgets/` && r.request().method() === 'POST'),
                page.getByRole('button', { name: 'Save', exact: true }).click(),
              ]);
              expect(saved.status()).toBe(200);
              const saveRequest = saved.request().postDataJSON() as Omit<Widget, 'id'>;
              savedWidget = ((await saved.json()) as { result: Widget }).result;
              expect(saveRequest.query_config).toEqual(expectedSavedConfig);
              expect(saveRequest.chart_config.chart_type).toBe('table');
              expect(savedWidget).toMatchObject({ name: widgetName, query_config: expectedSavedConfig, chart_config: saveRequest.chart_config });
              const rows = await probe.pg('SELECT id, name, dashboard_id, query_config, chart_config FROM tracer_dashboardwidget WHERE dashboard_id = $1 AND NOT deleted', [dashboardId]);
              expect(rows).toEqual([{ id: savedWidget.id, name: widgetName, dashboard_id: dashboardId,
                query_config: expectedSavedConfig, chart_config: saveRequest.chart_config }]);
              expect(await probe.pg('SELECT id, name, workspace_id FROM tracer_dashboard WHERE id = $1 AND NOT deleted', [dashboardId]))
                .toEqual([{ id: dashboardId, name: dashboardName, workspace_id: actor.workspaceId }]);
              const detail = await actor.api.get<{ result: { id: string; name: string; workspace: string; widgets: Widget[] } }>(`${DASHBOARDS}${dashboardId}/`);
              expect(detail.result).toMatchObject({ id: dashboardId, name: dashboardName, workspace: actor.workspaceId });
              expect(detail.result.widgets.map(w => ({ id: w.id, name: w.name, query_config: w.query_config, chart_config: w.chart_config })))
                .toEqual([{ id: savedWidget.id, name: widgetName, query_config: expectedSavedConfig, chart_config: saveRequest.chart_config }]);
              for (const selectedActor of [actor, scopeActors.ownerB, emptyActor]) {
                const path = `${DASHBOARDS}${dashboardId}/widgets/${savedWidget.id}/`;
                // DashboardWidgetViewSet inherits DRF retrieve: raw serializer, unlike wrapped create.
                const scoped = await scopeActors.send<Widget>(selectedActor, 'GET', path);
                scopeResults.push({ workspaceId: selectedActor.workspaceId, organizationId: selectedActor.organizationId, path, ...scoped });
                expect(scoped.status).toBe(selectedActor === actor ? 200 : 404);
                if (selectedActor === actor) expect(scoped.body).toMatchObject({ id: savedWidget.id, name: widgetName, query_config: expectedSavedConfig });
              }
              await testInfo.attach('saved-widget', { contentType: 'application/json', body: JSON.stringify({ saveRequest, savedWidget, rows, detail }) });
              await uiExpect(page).toHaveURL(new RegExp(`/dashboard/dashboards/${dashboardId}$`));
              return;
            } else if (action === 'reload') await page.reload({ waitUntil: 'domcontentloaded' });
            else if (action === 'reopen') await page.locator(`[data-widget-id="${savedWidget!.id}"]`).getByText(widgetName, { exact: true }).click();
            else {
              await numericCard.getByRole('spinbutton').fill(action === '999' ? '999' : '10');
              numericFilter.filter_config.filter_value = action === '999' ? 999 : 10;
            }

            await expect.poll(() => queries.slice(since).some(q => q.startedAt >= actionStartedAt &&
              isDeepStrictEqual(q.config, expectedConfig) && q.body.result?.query_complete === true), { timeout: UI_READY }).toBe(true);
            const receipt = queries.slice(since).find(q => q.startedAt >= actionStartedAt &&
              isDeepStrictEqual(q.config, expectedConfig) && q.body.result?.query_complete === true)!;
            await testInfo.attach(`${stage}-${action}-query`, { contentType: 'application/json', body: JSON.stringify(receipt) });
            expect(receipt.status).toBe(200);
            expect(receipt.config).toEqual(expectedConfig);
            for (const metric of receipt.config.metrics) expect(metric).not.toHaveProperty('filters');
            if (action === 'equals') {
              // Check1 fails here on actual scalar10, AFTER the independent fresh-config matcher.
              expect(receipt.config.filters[1].filter_config.filter_value).toBe(EXPECTED_NUMERIC_FILTER_VALUE);
              expect(receipt.config.filters[1].filter_config).not.toHaveProperty('attribute_value_types');
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
              .toEqual(choices.map(({ id, name, aggregation }) => ({ id, name, unit: '', aggregation })));
            // Float64 str is "10.0"; an empty query produces total/null, NOT zero or no series.
            const grouped = expectedConfig.breakdowns.length > 0 && action !== '999';
            const group = grouped ? '10.0' : 'total';
            const vector = action === 'project' ? [77, 6] : action === '999' ? [null, null] : [10, 1];
            if (action === 'reopen') vector[0] = EXPECTED_REOPENED_SUM;
            const expectedColumns: Record<string, (number | null)[]> = {};
            for (const [index, metric] of result.metrics.entries()) {
              expect(metric).toMatchObject({ query_complete: true, query_exact: true });
              expect(metric.series.map(s => s.name)).toEqual([group]);
              const expectedValues = buckets.map(bucket => bucket === plantedDay ? vector[index] : null);
              expect(metric.series[0].data.map(p => Date.parse(p.timestamp))).toEqual(buckets);
              expect(metric.series[0].data.map(p => p.value)).toEqual(expectedValues);
              const choice = choices[index];
              expectedColumns[grouped ? `${choice.name} / ${group} (${choice.aggregation})` : `${choice.name} (${choice.aggregation})`] = expectedValues;
            }
            expect(Object.keys(expectedColumns)).toHaveLength(2);
            const table = action === 'reload' ? page.locator(`[data-widget-id="${savedWidget!.id}"]`).getByRole('table') : page.getByRole('table');
            await uiExpect(table).toBeVisible();
            await expect.poll(async () => (await table.locator('thead th').allTextContents()).slice(1).sort(),
              { timeout: UI_READY }).toEqual(Object.keys(expectedColumns).sort());
            const headers = await table.locator('thead th').allTextContents();
            expect(headers[0]).toBe('Time');
            expect(headers.slice(1).sort()).toEqual(Object.keys(expectedColumns).sort());
            await uiExpect(table.locator('tbody tr')).toHaveCount(buckets.length);
            await uiExpect(table.locator('tbody tr td:first-child')).toHaveText(buckets.map(bucket =>
              new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: browserTimeZone }).format(bucket)));
            for (const [column, label] of headers.slice(1).entries()) {
              await uiExpect(table.locator(`tbody tr td:nth-child(${column + 2})`)).toHaveText(expectedColumns[label].map(value =>
                value === null ? '-' : action === 'reload' ? value.toFixed(2) : String(value)));
            }

            if (action === 'project' || action === 'equals' || action === 'group') {
              const selectedId = action === 'project' ? 'system_attribute:traces:project' : propertyId;
              await expect.poll(() => catalogs.slice(catalogSince).some(c => c.status === 200 && !('role' in c.request) &&
                c.request.search === (action === 'project' ? 'Project' : amountKey) && c.request.source === 'traces' &&
                c.body.result.metrics.some(m => m.property_id === selectedId)), { timeout: UI_READY }).toBe(true);
              const discoveries = catalogs.slice(catalogSince).filter(c => c.status === 200 && !('role' in c.request) &&
                c.request.search === (action === 'project' ? 'Project' : amountKey) && c.request.source === 'traces');
              for (const discovery of discoveries) {
                expect(discovery.request.category).toBe(action === 'project' ? 'system_metric' : 'custom_attribute');
                const selected = discovery.body.result.metrics.filter(m => m.property_id === selectedId);
                expect(selected).toHaveLength(1);
                expect(selected[0]).toMatchObject(action === 'project' ? { property_id: selectedId, property_kind: 'system_attribute',
                  category: 'system_metric', name: 'project', source: 'traces', type: 'string', output_type: 'string', role: 'dimension' } : numberProperty);
              }
              if (action === 'project') {
                const projectValues = values.filter(v => v.request.property_id === selectedId);
                expect(projectValues.length).toBeGreaterThan(0);
                for (const discovery of projectValues) {
                  expect(discovery.status).toBe(200);
                  expect(discovery.request).toMatchObject({ property_id: selectedId, metric_name: 'project', metric_type: 'system_metric', project_ids: '', source: 'traces' });
                }
                const options = projectValues.flatMap(v => v.body.result.values);
                expect(options.some(v => v.value === projectIds[0] && v.label === projectNames[0])).toBe(true);
                expect(options.filter(v => v.value === projectIds[2] || v.label === projectNames[2])).toEqual([]);
              }
            }
            if (action === 'reopen' || action === 'restore') {
              for (const choice of choices) {
                const card = page.locator(`p[title="${choice.name}"]`).locator('../..');
                await uiExpect(card.getByText(choice.aggregationLabel, { exact: true })).toBeVisible();
                await uiExpect(card.getByText('Project', { exact: true })).toHaveCount(0);
              }
              await uiExpect(numericCard.getByRole('combobox')).toHaveText('Equals');
              await uiExpect(numericCard.getByRole('spinbutton')).toHaveAttribute('type', 'number');
              await uiExpect(numericCard.getByRole('spinbutton')).toHaveValue('10');
              await uiExpect(globalSection.getByText('Project', { exact: true }).locator('../..').locator('.filter-value-name')).toHaveText(projectNames[0]);
              await uiExpect(page.locator('.breakdown-section-title').locator('../..').getByText(amountKey, { exact: true })).toBeVisible();
              await uiExpect(page.getByRole('combobox').filter({ hasText: 'Day' })).toBeVisible();
              // Preset chips lack semantic selected state; the full native query independently pins 7D.
              await uiExpect(page.getByText('7D', { exact: true })).toBeVisible();
            }
            await testInfo.attach(`${stage}-${action}-table`, { contentType: 'image/png', body: await page.screenshot() });
          }
          if (stage === 'disjoint') {
            const finalSources = await probe.ch<Span>(sourceSql, sourceParams);
            const finalTraces = await probe.ch(traceSql, projectParams);
            expect(finalSources).toEqual(initialSources);
            expect(finalTraces).toEqual(expectedTraces);
            const persisted = await probe.pg('SELECT id, name, dashboard_id, query_config, chart_config FROM tracer_dashboardwidget WHERE dashboard_id = $1 AND NOT deleted', [dashboardId]);
            expect(persisted).toEqual([{ id: savedWidget!.id, name: widgetName, dashboard_id: dashboardId,
              query_config: expectedSavedConfig, chart_config: savedWidget!.chart_config }]);
            await testInfo.attach('unchanged-source-and-saved-facts', { contentType: 'application/json', body: JSON.stringify({ initialSources, finalSources, finalTraces, persisted }) });
          }
        }, { timeout: UI_READY });
      }
    } finally { page.off('response', captureResponse); }
  } finally {
    try {
      await Promise.all(pending);
      await testInfo.attach('catalog-preview-and-scope', { contentType: 'application/json', body: JSON.stringify({ catalogs, values, queries, scopeResults, responseErrors }) });
    } finally { await context.close(); }
  }
});
