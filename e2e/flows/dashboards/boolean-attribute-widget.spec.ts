import { randomBytes, randomUUID } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { request, type Response } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { sendTrace, type OtlpAttributes } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// frontend/src/hooks/useDashboards.js + futureagi/tracer/views/dashboard.py:
// native create/detail/widgets, catalog, values and preview endpoints (no response rewriting).
const DASHBOARDS = '/tracer/dashboard/';
const METRICS = `${DASHBOARDS}metrics/`;
const VALUES = `${DASHBOARDS}filter_values/`;
const QUERY = `${DASHBOARDS}query/`;
const UI_READY = 60_000; // One ceiling per complete approved browser phase.
const DAY = 86_400_000;
// ASSERTION ONLY: mutate one anchor, never its seed, input or independent request matcher.
const EXPECTED_BOOLEAN_FILTER_VALUE: boolean | string = true; // Check1 -> string 'true'.
const EXPECTED_A_CHILD_BOOL_STORAGE = 1; // Check2 -> 0; presence and source true stay unchanged.
const EXPECTED_REOPENED_SUM = 33; // Check3 -> 34, ONLY the reopened M/group1 expectation.

type Filter = { column_id: string; property_id: string; display_name: string;
  source: string; output_type: string; filter_config: { filter_type: string;
    filter_op: string; filter_value: boolean | string[]; col_type: string } };
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
type CatalogBody = { result: { metrics: CatalogProperty[]; has_more: boolean; next_cursor: string | null;
  query_complete: boolean; query_exact: boolean; query_status: string; query_provenance: string } };
type ValueOption = { value: boolean | number | string; label: string; type?: string };
type ValueBody = { result: { values: ValueOption[]; attribute_types: string[] } };
type CatalogReceipt = { request: Record<string, unknown>; status: number; body: CatalogBody };
type ValueReceipt = { request: Record<string, unknown>; status: number; body: ValueBody };
type Project = { id: string; name: string; organization_id: string; workspace_id: string };
type Span = { id: string; trace_id: string; project_id: string; org_id: string;
  parent_span_id: string; observation_type: string; name: string; model: string; status: string; cost: number;
  bool_value: number; has_bool: number; string_value: string; has_string: number;
  number_value: number; has_number: number; amount_value: number; has_amount: number;
  amount_has_bool: number; amount_has_string: number; foreign_value: string; has_foreign: number;
  start_us: string; end_us: string; version: string; is_deleted: number };
type Widget = { id: string; name: string; query_config: Config;
  chart_config: { chart_type: string; [key: string]: unknown } };

test('DASH-E2E-005: a saved widget keeps boolean scope distinct from same-spelled text', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DASH-E2E-005', area: 'dashboards',
    userGoal: 'A saved widget keeps boolean scope distinct from same-spelled text.',
    steps: ['ingest five independent traces with true, false and foreign same-key text witnesses',
      'exclude the boolean from native metrics and select Spans plus an independent numeric Sum',
      'select primary Project, inspect both boolean groups and choose native Equals true',
      'save, reload and reopen the full scoped binding and exact table',
      'select false, restore true and verify unchanged source versions and saved facts'],
    backendChecks: [
      "Native catalog and selected property identities preserve this flow's source, types, choices and actor scope.",
      "This flow's exact publicly produced source identities and typed latest facts are present and unchanged outside its authorized UI actions.",
      "The preview, saved binding and reopened widget equal this flow's independently specified filtered and grouped result.",
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe: probe }, testInfo) => {
  test.setTimeout(600_000); // ASYNC_JOB60 + 2 SPAN_VISIBLE15 + 8 UI_READY60 + 30 headroom.
  const uiExpect = expect.configure({ timeout: UI_READY }); // Browser assertions only, not source/API budgets.
  const prefix = `e2e-dash5-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const actor = scopeActors.ownerA;
  const emptyActor = scopeActors.withWorkspace(actor, scopeActors.emptyWorkspace.id);
  const booleanKey = `${prefix}.enabled`;
  const amountKey = `${prefix}.amount`;
  const foreignKey = `${prefix}.foreign-only`;
  const propertyId = `custom_attribute:${booleanKey}`;
  const amountPropertyId = `custom_attribute:${amountKey}`;
  const projectNames = [`${prefix}-primary`, `${prefix}-sibling`, `${prefix}-foreign`];
  const dashboardName = `${prefix}-dashboard`;
  const widgetName = `${prefix}-widget`;
  const plantedDay = Math.floor(Date.now() / DAY) * DAY - DAY;
  const start = BigInt(plantedDay + DAY / 2) * 1_000_000n;
  // B is boolean-only in A, mixed boolean/string ONLY in B. M is independently number-only.
  const seeds = [
    { key: 'A', project: 0, ms: 50, enabled: [true, true], amount: [1, 10] },
    { key: 'B', project: 0, ms: 100, enabled: [true, true], amount: [2, 20] },
    { key: 'C', project: 0, ms: 200, enabled: [false, false], amount: [4, 40] },
    { key: 'S', project: 1, ms: 900, enabled: [true, true], amount: [1000, 2000] },
    { key: 'F', project: 2, ms: 900, enabled: [true, 'true'], amount: [1000, 2000] },
  ].map(seed => ({ ...seed, model: `${prefix}-${seed.key}-model`, traceId: randomUUID(),
    spanIds: [randomBytes(8).toString('hex'), randomBytes(8).toString('hex')] }));
  for (const seed of seeds) for (const id of seed.spanIds) {
    expect(id).toMatch(/^[0-9a-f]{16}$/);
    expect(id).not.toBe('0000000000000000');
  }
  await testInfo.attach('planted-identities', { contentType: 'application/json', body: JSON.stringify({
    seeds, projectNames, booleanKey, amountKey, foreignKey, start: String(start), actors: scopeActors.evidence().actors,
  }) });
  const ingestion = await request.newContext();
  try {
    for (const seed of seeds) {
      const owner = seed.project === 2 ? scopeActors.ownerB : actor;
      const keys = scopeActors.owners.find(item => item.organizationId === owner.organizationId && item.workspaceId === owner.workspaceId)!;
      // otlp.ts retains boolValue vs stringValue; zero cost prevents model pricing.
      const attributes: OtlpAttributes[] = [0, 1].map(index => ({
        'fi.span.kind': index === 0 ? 'chain' : 'llm', 'gen_ai.request.model': seed.model,
        'gen_ai.cost.total': 0, [booleanKey]: seed.enabled[index], [amountKey]: seed.amount[index],
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
  // adapter.go / 002_spans_v2.sql: bool Map is UInt8; presence distinguishes false from missing.
  const sourceSql = `SELECT id, trace_id, project_id, org_id, parent_span_id, observation_type, name, model, status, cost,
    attrs_bool[{booleanKey:String}] AS bool_value, mapContains(attrs_bool, {booleanKey:String}) AS has_bool,
    attrs_string[{booleanKey:String}] AS string_value, mapContains(attrs_string, {booleanKey:String}) AS has_string,
    attrs_number[{booleanKey:String}] AS number_value, mapContains(attrs_number, {booleanKey:String}) AS has_number,
    attrs_number[{amountKey:String}] AS amount_value, mapContains(attrs_number, {amountKey:String}) AS has_amount,
    mapContains(attrs_bool, {amountKey:String}) AS amount_has_bool, mapContains(attrs_string, {amountKey:String}) AS amount_has_string,
    attrs_string[{foreignKey:String}] AS foreign_value, mapContains(attrs_string, {foreignKey:String}) AS has_foreign,
    toUnixTimestamp64Micro(start_time) AS start_us, toUnixTimestamp64Micro(end_time) AS end_us,
    toString(_version) AS version, is_deleted FROM spans FINAL
    WHERE project_id IN ({primary:UUID}, {sibling:UUID}, {foreign:UUID}) ORDER BY id`;
  const sourceParams = { ...projectParams, booleanKey, amountKey, foreignKey };
  const expectedSources = seeds.flatMap(seed => seed.spanIds.map((id, index) => ({ id, trace_id: seed.traceId,
    project_id: projectIds[seed.project], org_id: seed.project === 2 ? scopeActors.ownerB.organizationId : actor.organizationId,
    parent_span_id: index ? seed.spanIds[0] : '', observation_type: index ? 'llm' : 'chain',
    name: `${prefix}-${seed.key}-${index ? 'child' : 'root'}`, model: seed.model, status: 'OK', cost: 0,
    bool_value: seed.key === 'A' && index === 1 ? EXPECTED_A_CHILD_BOOL_STORAGE : seed.enabled[index] === true ? 1 : 0,
    has_bool: typeof seed.enabled[index] === 'boolean' ? 1 : 0,
    string_value: typeof seed.enabled[index] === 'string' ? seed.enabled[index] : '',
    has_string: typeof seed.enabled[index] === 'string' ? 1 : 0, number_value: 0, has_number: 0,
    amount_value: seed.amount[index], has_amount: 1, amount_has_bool: 0, amount_has_string: 0,
    foreign_value: seed.project === 2 ? 'foreign' : '', has_foreign: seed.project === 2 ? 1 : 0,
    start_us: String(start / 1000n), end_us: String(start / 1000n + BigInt(seed.ms) * 1000n), is_deleted: 0,
  }))).sort((a, b) => a.id.localeCompare(b.id));
  let initialSources: Span[] = [];
  try {
    await expect.poll(async () => {
      initialSources = await probe.ch<Span>(sourceSql, sourceParams);
      return initialSources.map(({ version, ...row }) => row);
    }, POLL.SPAN_VISIBLE).toEqual(expectedSources); // Retain this validated read INCLUDING versions.
  } finally {
    await testInfo.attach('source-identities', { contentType: 'application/json', body: JSON.stringify({ projects, expectedSources, initialSources }) });
  }
  const traceSql = 'SELECT id, project_id FROM traces FINAL WHERE project_id IN ({primary:UUID}, {sibling:UUID}, {foreign:UUID}) ORDER BY toString(id)';
  const expectedTraces = seeds.map(seed => ({ id: seed.traceId, project_id: projectIds[seed.project] })).sort((a, b) => a.id.localeCompare(b.id));
  await expect.poll(() => probe.ch(traceSql, projectParams), POLL.SPAN_VISIBLE).toEqual(expectedTraces);
  await testInfo.attach('trace-identities', { contentType: 'application/json', body: JSON.stringify(expectedTraces) });

  const booleanProperty = { property_id: propertyId, property_kind: 'custom_attribute', category: 'custom_attribute',
    name: booleanKey, display_name: booleanKey, source: 'traces', type: 'boolean', output_type: 'boolean', data_type: 'boolean',
    role: 'dimension', attribute_types: ['boolean'], attribute_types_exact: false, allowed_aggregations: ['count', 'count_distinct'] };
  const mixedProperty = { ...booleanProperty, type: 'json', output_type: 'json', data_type: 'json', attribute_types: ['boolean', 'string'] };
  const numberProperty = { property_id: amountPropertyId, property_kind: 'custom_attribute', category: 'custom_attribute',
    name: amountKey, display_name: amountKey, source: 'traces', type: 'number', output_type: 'number', data_type: 'number',
    role: 'metric', attribute_types: ['number'], attribute_types_exact: false,
    allowed_aggregations: ['avg', 'count', 'count_distinct', 'max', 'min', 'sum'] };
  const spansProperty = { property_id: 'system_attribute:traces:span_count', property_kind: 'system_attribute',
    category: 'system_metric', name: 'span_count', display_name: 'Spans', source: 'traces', type: 'number', output_type: 'number', role: 'metric' };
  const choices = [
    { id: 'span_count', name: 'Spans', aggregation: 'count_distinct', aggregationLabel: 'Distinct Count', custom: false },
    { id: amountKey, name: amountKey, aggregation: 'sum', aggregationLabel: 'Sum', custom: true },
  ];
  const metrics: Metric[] = choices.map(m => ({ id: m.id, name: m.id,
    property_id: m.custom ? amountPropertyId : 'system_attribute:traces:span_count', display_name: m.name,
    type: m.custom ? 'custom_attribute' : 'system_metric', source: 'traces', aggregation: m.aggregation,
    ...(m.custom ? { attribute_key: amountKey, attribute_type: 'number' } : {}),
  }));
  const projectFilter: Filter = { column_id: 'project', property_id: 'system_attribute:traces:project', display_name: 'Project',
    source: 'traces', output_type: 'string', filter_config: {
      filter_type: 'text', filter_op: 'in', filter_value: [projectIds[0]], col_type: 'SYSTEM_METRIC' } };
  const booleanFilter: Filter = { column_id: booleanKey, property_id: propertyId, display_name: booleanKey,
    source: 'traces', output_type: 'boolean', filter_config: {
      filter_type: 'boolean', filter_op: 'equals', filter_value: true, col_type: 'SPAN_ATTRIBUTE' } };
  const breakdown = { name: booleanKey, property_id: propertyId, display_name: booleanKey,
    type: 'custom_attribute', source: 'traces', attribute_type: 'boolean' };
  const expectedConfig: Config = { project_ids: [], time_range: { preset: '7D' }, granularity: 'day',
    metrics, filters: [projectFilter], breakdowns: [] };
  const expectedSavedConfig: Config = structuredClone({ ...expectedConfig, filters: [projectFilter, booleanFilter], breakdowns: [breakdown] });
  const catalogs: CatalogReceipt[] = [];
  const values: ValueReceipt[] = [];
  const queries: QueryReceipt[] = [];
  const scopeResults: unknown[] = [];
  const responseErrors: { path: string; error: string }[] = [];
  const pending = new Set<Promise<void>>();
  const context = await scopeActors.openContext(browser, actor);
  try {
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    const browserTimeZone = await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone);
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
    const booleanCard = globalSection.getByText(booleanKey, { exact: true }).locator('../..');
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

      await test.step('exclude boolean metrics and select Spans and numeric Sum with scoped witnesses', async () => {
        await page.getByText('Select Metric', { exact: true }).click();
        // reader.py's all-number metric eligibility is distinct from backend boolean count support.
        for (const lane of [
          { category: 'All', wire: '', source: '' },
          { category: 'Trace Attributes', wire: 'custom_attribute', source: 'traces' },
        ]) {
          await page.getByLabel(new RegExp(`^${lane.category} property count: `)).locator('..').getByText(lane.category, { exact: true }).click();
          await page.getByPlaceholder('Search metrics...').fill(booleanKey);
          await expect.poll(() => catalogs.some(c => c.status === 200 && c.request.cursor_mode === true && c.request.role === 'metric' &&
            (c.request.category || '') === lane.wire && (c.request.source || '') === lane.source && c.request.search === booleanKey &&
            c.body.result.query_complete === true), { timeout: UI_READY }).toBe(true);
          const receipt = catalogs.find(c => c.status === 200 && c.request.cursor_mode === true && c.request.role === 'metric' &&
            (c.request.category || '') === lane.wire && (c.request.source || '') === lane.source && c.request.search === booleanKey &&
            c.body.result.query_complete === true)!;
          expect(receipt.body.result).toMatchObject({ metrics: [], has_more: false, next_cursor: null, query_complete: true,
            query_exact: false, query_status: 'complete', query_provenance: 'current_property_catalog' });
          expect(Number.isInteger(receipt.request.page_size)).toBe(true);
          expect(Number(receipt.request.page_size)).toBeGreaterThan(0);
          if (!lane.wire) { expect(receipt.request).not.toHaveProperty('category'); expect(receipt.request).not.toHaveProperty('source'); }
          await uiExpect(page.getByRole('button', { name: new RegExp(`^${booleanKey.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} \\(`) })).toHaveCount(0);
          await testInfo.attach(`boolean-metric-exclusion-${lane.category}`, { contentType: 'application/json', body: JSON.stringify(receipt) });
        }
        for (const [index, choice] of choices.entries()) {
          if (index) await page.locator('.metric-section-title').click();
          const category = choice.custom ? 'Trace Attributes' : 'Traces';
          await page.getByLabel(new RegExp(`^${category} property count: `)).locator('..').getByText(category, { exact: true }).click();
          await page.getByPlaceholder('Search metrics...').fill(choice.name);
          await expect.poll(() => catalogs.some(c => c.status === 200 && c.request.role === 'metric' && c.request.search === choice.name &&
            c.request.source === 'traces' && c.request.category === (choice.custom ? 'custom_attribute' : 'system_metric') &&
            c.body.result.query_complete === true), { timeout: UI_READY }).toBe(true);
          const receipt = catalogs.find(c => c.status === 200 && c.request.role === 'metric' && c.request.search === choice.name &&
            c.request.source === 'traces' && c.request.category === (choice.custom ? 'custom_attribute' : 'system_metric') &&
            c.body.result.query_complete === true)!;
          const selected = receipt.body.result.metrics.filter(m => m.property_id === (choice.custom ? amountPropertyId : spansProperty.property_id));
          expect(selected).toHaveLength(1);
          expect(selected[0]).toMatchObject(choice.custom ? numberProperty : spansProperty);
          expect(receipt.body.result).toMatchObject({ query_complete: true, query_exact: false, query_status: 'complete',
            query_provenance: 'current_property_catalog', has_more: false, next_cursor: null });
          expect(receipt.request.cursor_mode).toBe(true);
          expect(Number.isInteger(receipt.request.page_size)).toBe(true);
          expect(Number(receipt.request.page_size)).toBeGreaterThan(0);
          await page.getByRole('button', { name: choice.custom ? `${amountKey} (number, number, Traces)` : 'Spans (number, Traces)', exact: true }).click();
          const card = page.locator(`p[title="${choice.name}"]`).locator('../..');
          await card.locator('.MuiChip-clickable').click();
          await page.getByText(choice.aggregationLabel, { exact: true }).last().click();
        }

        // Supplemental typed value API evidence, NOT a fictitious boolean value popup.
        await Promise.all([actor, scopeActors.ownerB, emptyActor].map(async selectedActor => {
          const isForeign = selectedActor === scopeActors.ownerB;
          const isEmpty = selectedActor === emptyActor;
          for (const key of [booleanKey, amountKey, foreignKey]) {
            for (const role of key === booleanKey ? ['metric', ''] : key === amountKey ? ['metric'] : ['']) {
              const input = { source: 'traces', category: 'custom_attribute', search: key, cursor_mode: true, page_size: 25, ...(role ? { role } : {}) };
              const catalog = await scopeActors.send<CatalogBody>(selectedActor, 'POST', METRICS, input);
              scopeResults.push({ workspaceId: selectedActor.workspaceId, organizationId: selectedActor.organizationId, path: METRICS, input, ...catalog });
              expect(catalog.status).toBe(200);
              const visible = key === booleanKey ? !isEmpty && !role : key === amountKey ? !isEmpty : isForeign;
              expect(catalog.body.result.metrics.map(m => m.property_id)).toEqual(visible ? [`custom_attribute:${key}`] : []);
              expect(catalog.body.result).toMatchObject({ has_more: false, next_cursor: null, query_complete: true,
                query_exact: false, query_status: 'complete', query_provenance: 'current_property_catalog' });
              if (visible && key !== foreignKey) expect(catalog.body.result.metrics[0])
                .toMatchObject(key === amountKey ? numberProperty : isForeign ? mixedProperty : booleanProperty);
            }
          }
          const valueReads: { key: string; attributeType: string; projects: string; expected: (boolean | number | string)[]; types: string[] }[] = [
            { key: booleanKey, attributeType: '', projects: '', expected: isEmpty ? [] : isForeign ? [true, 'true'] : [false, true],
              types: isEmpty ? [] : isForeign ? ['boolean', 'string'] : ['boolean'] },
            { key: amountKey, attributeType: '', projects: '', expected: isEmpty ? [] : isForeign ? [1000, 2000] : [1, 2, 4, 10, 20, 40, 1000, 2000],
              types: isEmpty ? [] : ['number'] },
            { key: foreignKey, attributeType: 'string', projects: '', expected: isForeign ? ['foreign'] : [], types: isForeign ? ['string'] : [] },
            ...(isForeign ? [
              { key: booleanKey, attributeType: 'boolean', projects: '', expected: [true], types: ['boolean', 'string'] },
              { key: booleanKey, attributeType: 'string', projects: '', expected: ['true'], types: ['boolean', 'string'] },
            ] : selectedActor === actor ? [
              { key: amountKey, attributeType: '', projects: projectIds[0], expected: [1, 2, 4, 10, 20, 40], types: ['number'] },
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
              query_provenance: 'current_property_catalog', attribute_types: read.types });
            // Only sorting uses String; equality retains distinct true and "true" JSON types.
            const actual = [...result.body.result.values].sort((a, b) => String(a.type).localeCompare(String(b.type)) ||
              (typeof a.value === 'number' && typeof b.value === 'number' ? a.value - b.value : String(a.value).localeCompare(String(b.value))));
            expect(actual).toEqual(read.expected.map(value => ({ value, label: String(value), type: typeof value })));
          }
        }));
      }, { timeout: UI_READY });

      // Six remaining phases; group/true and false/restore each share ONE whole-phase deadline.
      for (const stage of ['project', 'boolean', 'save', 'reload', 'reopen', 'toggle'] as const) {
        await test.step(stage, async () => {
          const actions = stage === 'boolean' ? ['group', 'true'] : stage === 'toggle' ? ['false', 'restore'] : [stage];
          for (const action of actions) {
            const since = queries.length;
            const actionStartedAt = Date.now();
            if (action === 'project') {
              await page.locator('.filter-section-title').click();
              await page.getByLabel(/^Traces property count: /).locator('..').getByText('Traces', { exact: true }).click();
              await page.getByPlaceholder('Search filter attributes...').fill('Project');
              await page.getByRole('button', { name: 'Project (string, Traces)', exact: true }).click();
              await page.getByText('Select value...', { exact: true }).click();
              await page.getByPlaceholder('Search...', { exact: true }).fill(projectNames[0]);
              await page.locator(`p[title="${projectNames[0]}"]`).click();
              await page.getByRole('button', { name: 'Add', exact: true }).click();
            } else if (action === 'group' || action === 'true') {
              await page.locator(action === 'group' ? '.breakdown-section-title' : '.filter-section-title').click();
              // Cached repeats may reuse an actual settled receipt; no mandatory duplicate HTTP.
              const lanes = action === 'group' ? [
                { category: 'Trace Attributes', wire: 'custom_attribute', source: 'traces', search: '' },
                { category: 'All', wire: '', source: '', search: booleanKey },
                { category: 'Trace Attributes', wire: 'custom_attribute', source: 'traces', search: booleanKey },
                { category: 'Trace Attributes', wire: 'custom_attribute', source: 'traces', search: '' },
                { category: 'Trace Attributes', wire: 'custom_attribute', source: 'traces', search: booleanKey },
              ] : [{ category: 'Trace Attributes', wire: 'custom_attribute', source: 'traces', search: booleanKey }];
              for (const lane of lanes) {
                await page.getByLabel(new RegExp(`^${lane.category} property count: `)).locator('..').getByText(lane.category, { exact: true }).click();
                await page.getByPlaceholder(action === 'group' ? 'Search breakdown attributes...' : 'Search filter attributes...').fill(lane.search);
                const option = page.getByRole('button', { name: `${booleanKey} (boolean, boolean, Traces)`, exact: true });
                await uiExpect(option).toBeVisible();
                await uiExpect(option).toHaveCount(1);
                await uiExpect(page.getByRole('button', { name: `${booleanKey} (string, string, Traces)`, exact: true })).toHaveCount(0);
                await expect.poll(() => catalogs.some(c => c.status === 200 && !('role' in c.request) && c.request.cursor_mode === true &&
                  (c.request.category || '') === lane.wire && (c.request.source || '') === lane.source && (c.request.search || '') === lane.search &&
                  c.body.result.query_complete === true && c.body.result.metrics.some(m => m.property_id === propertyId)), { timeout: UI_READY }).toBe(true);
                const receipt = catalogs.find(c => c.status === 200 && !('role' in c.request) && c.request.cursor_mode === true &&
                  (c.request.category || '') === lane.wire && (c.request.source || '') === lane.source && (c.request.search || '') === lane.search &&
                  c.body.result.query_complete === true && c.body.result.metrics.some(m => m.property_id === propertyId))!;
                const selected = receipt.body.result.metrics.filter(m => m.property_id === propertyId);
                expect(selected).toHaveLength(1);
                expect(selected[0]).toMatchObject(booleanProperty);
                expect(receipt.body.result).toMatchObject({ query_complete: true, query_exact: false, query_status: 'complete', query_provenance: 'current_property_catalog' });
                if (lane.search) expect(receipt.body.result).toMatchObject({ has_more: false, next_cursor: null });
                if (!lane.wire) { expect(receipt.request).not.toHaveProperty('category'); expect(receipt.request).not.toHaveProperty('source'); }
                expect(Number.isInteger(receipt.request.page_size)).toBe(true);
                expect(Number(receipt.request.page_size)).toBeGreaterThan(0);
                await testInfo.attach(`${action}-boolean-lane-${lane.category}-${lane.search || 'unsearched'}`, { contentType: 'application/json', body: JSON.stringify(receipt) });
              }
              await page.getByRole('button', { name: `${booleanKey} (boolean, boolean, Traces)`, exact: true }).click();
              if (action === 'group') expectedConfig.breakdowns = [breakdown];
              else {
                await uiExpect(booleanCard.getByRole('combobox')).toHaveCount(2);
                await booleanCard.getByRole('combobox').nth(0).click();
                await page.getByRole('option', { name: 'Equals', exact: true }).click();
                await uiExpect(booleanCard.getByRole('combobox').nth(1)).toHaveText('Value');
                await booleanCard.getByRole('combobox').nth(1).click();
                await uiExpect(page.getByRole('option')).toHaveText(['Value', 'true', 'false']);
                await uiExpect(page.getByRole('option', { name: 'Value', exact: true })).toBeDisabled();
                await page.getByRole('option', { name: 'true', exact: true }).click();
                expectedConfig.filters.push(booleanFilter);
              }
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
                // Inherited DRF retrieve is a RAW Widget body, unlike wrapped create/detail.
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
              await booleanCard.getByRole('combobox').nth(1).click();
              await page.getByRole('option', { name: action === 'false' ? 'false' : 'true', exact: true }).click();
              booleanFilter.filter_config.filter_value = action !== 'false';
            }

            await expect.poll(() => queries.slice(since).some(q => q.startedAt >= actionStartedAt &&
              isDeepStrictEqual(q.config, expectedConfig) && q.body.result?.query_complete === true), { timeout: UI_READY }).toBe(true);
            const receipt = queries.slice(since).find(q => q.startedAt >= actionStartedAt &&
              isDeepStrictEqual(q.config, expectedConfig) && q.body.result?.query_complete === true)!;
            await testInfo.attach(`${stage}-${action}-query`, { contentType: 'application/json', body: JSON.stringify(receipt) });
            expect(receipt.status).toBe(200);
            expect(receipt.config).toEqual(expectedConfig);
            for (const metric of receipt.config.metrics) expect(metric).not.toHaveProperty('filters');
            if (action === 'true') {
              // Check1: actual boolean true, AFTER the independent literal-true fresh matcher.
              expect(receipt.config.filters[1].filter_config.filter_value).toBe(EXPECTED_BOOLEAN_FILTER_VALUE);
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
            // attrs_bool is UInt8: exact names "1"/"0", not true/false or numeric Float64 strings.
            // Literal group vectors are independent of source reads and per-metric ranking.
            const vectors: Record<string, number[]> = action === 'project' ? { total: [6, 77] } :
              action === 'group' ? { '1': [4, 33], '0': [2, 44] } :
                action === 'false' ? { '0': [2, 44] } : { '1': [4, 33] };
            if (action === 'reopen') vectors['1'][1] = EXPECTED_REOPENED_SUM;
            const expectedColumns: Record<string, (number | null)[]> = {};
            for (const [index, metric] of result.metrics.entries()) {
              expect(metric).toMatchObject({ query_complete: true, query_exact: true });
              expect(metric.series.map(s => s.name).sort()).toEqual(Object.keys(vectors).sort());
              for (const [group, vector] of Object.entries(vectors)) {
                const series = metric.series.find(s => s.name === group)!; // Never zip differently ranked metrics.
                const expectedValues = buckets.map(bucket => bucket === plantedDay ? vector[index] : null);
                expect(series.data.map(p => Date.parse(p.timestamp))).toEqual(buckets);
                expect(series.data.map(p => p.value)).toEqual(expectedValues);
                const choice = choices[index];
                expectedColumns[group === 'total' ? `${choice.name} (${choice.aggregation})` : `${choice.name} / ${group} (${choice.aggregation})`] = expectedValues;
              }
            }
            expect(Object.keys(expectedColumns)).toHaveLength(action === 'group' ? 4 : 2);
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
            if (action === 'project') {
              const selectedId = 'system_attribute:traces:project';
              await expect.poll(() => catalogs.some(c => c.status === 200 && !('role' in c.request) && c.request.search === 'Project' &&
                c.request.source === 'traces' && c.request.category === 'system_metric' && c.body.result.query_complete === true &&
                c.body.result.metrics.some(m => m.property_id === selectedId)), { timeout: UI_READY }).toBe(true);
              const discovery = catalogs.find(c => c.status === 200 && !('role' in c.request) && c.request.search === 'Project' &&
                c.request.source === 'traces' && c.request.category === 'system_metric' && c.body.result.query_complete === true &&
                c.body.result.metrics.some(m => m.property_id === selectedId))!;
              const selected = discovery.body.result.metrics.filter(m => m.property_id === selectedId);
              expect(selected).toHaveLength(1);
              expect(selected[0]).toMatchObject({ property_id: selectedId, property_kind: 'system_attribute', category: 'system_metric',
                name: 'project', source: 'traces', type: 'string', output_type: 'string', role: 'dimension' });
              expect(discovery.body.result).toMatchObject({ query_complete: true, query_exact: false, query_status: 'complete',
                query_provenance: 'current_property_catalog', has_more: false, next_cursor: null });
              expect(discovery.request.cursor_mode).toBe(true);
              expect(Number.isInteger(discovery.request.page_size)).toBe(true);
              expect(Number(discovery.request.page_size)).toBeGreaterThan(0);
              const projectValues = values.filter(v => v.request.property_id === selectedId);
              expect(projectValues.length).toBeGreaterThan(0);
              for (const valueReceipt of projectValues) {
                expect(valueReceipt.status).toBe(200);
                expect(valueReceipt.request).toMatchObject({ property_id: selectedId, metric_name: 'project', metric_type: 'system_metric', project_ids: '', source: 'traces' });
              }
              const options = projectValues.flatMap(v => v.body.result.values);
              expect(options.some(v => v.value === projectIds[0] && v.label === projectNames[0])).toBe(true);
              expect(options.filter(v => v.value === projectIds[2] || v.label === projectNames[2])).toEqual([]);
            }
            if (action === 'true' || action === 'false' || action === 'reopen' || action === 'restore') {
              await uiExpect(booleanCard.getByRole('combobox')).toHaveCount(2);
              await uiExpect(booleanCard.getByRole('combobox').nth(0)).toHaveText('Equals');
              await uiExpect(booleanCard.getByRole('combobox').nth(1)).toHaveText(action === 'false' ? 'false' : 'true');
            }
            if (action === 'reopen' || action === 'restore') {
              for (const choice of choices) {
                const card = page.locator(`p[title="${choice.name}"]`).locator('../..');
                await uiExpect(card.getByText(choice.aggregationLabel, { exact: true })).toBeVisible();
                await uiExpect(card.getByText('Project', { exact: true })).toHaveCount(0);
              }
              await uiExpect(globalSection.getByText('Project', { exact: true }).locator('../..').locator('.filter-value-name')).toHaveText(projectNames[0]);
              await uiExpect(page.locator('.breakdown-section-title').locator('../..').getByText(booleanKey, { exact: true })).toBeVisible();
              await uiExpect(page.getByRole('combobox').filter({ hasText: 'Day' })).toBeVisible();
              await uiExpect(page.getByText('7D', { exact: true })).toBeVisible(); // Full wire independently pins the selected preset.
            }
            await testInfo.attach(`${stage}-${action}-table`, { contentType: 'image/png', body: await page.screenshot() });
          }
          if (stage === 'toggle') {
            const finalSources = await probe.ch<Span>(sourceSql, sourceParams);
            const finalTraces = await probe.ch(traceSql, projectParams);
            const persisted = await probe.pg('SELECT id, name, dashboard_id, query_config, chart_config FROM tracer_dashboardwidget WHERE dashboard_id = $1 AND NOT deleted', [dashboardId]);
            await testInfo.attach('unchanged-source-and-saved-facts', { contentType: 'application/json', body: JSON.stringify({ initialSources, finalSources, finalTraces, persisted }) });
            expect(finalSources).toEqual(initialSources);
            expect(finalTraces).toEqual(expectedTraces);
            expect(persisted).toEqual([{ id: savedWidget!.id, name: widgetName, dashboard_id: dashboardId,
              query_config: expectedSavedConfig, chart_config: savedWidget!.chart_config }]);
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
