import { randomBytes, randomUUID } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { request, type Response } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { sendTrace, type OtlpAttributes } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// useDashboards.js / tracer/views/dashboard.py: native catalog, values, query,
// wrapped dashboard/create/update and RAW inherited nested-widget GET endpoints.
const DASHBOARDS = '/tracer/dashboard/';
const METRICS = `${DASHBOARDS}metrics/`;
const VALUES = `${DASHBOARDS}filter_values/`;
const QUERY = `${DASHBOARDS}query/`;
const UI_READY = 60_000; // One wall per whole approved phase, not per popup transition.
const DAY = 86_400_000;
// Assertion-only fallibility anchors: never used by seeds or request matchers.
const EXPECTED_ARRAY_FILTER_OP = 'contains'; // Check1: -> 'in', phase4 actual wire only.
const EXPECTED_A_CHILD_NUMERIC_MEMBER = 0; // Check2: -> 1, separate post-poll assertion.
const EXPECTED_REOPENED_SPANS = 4; // Check3: -> 5, phase6 reopened API assertion only.

type Member = string | number | boolean;
type Filter = { column_id: string; property_id: string; display_name: string;
  source: string; output_type: string; filter_config: { filter_type: string;
    filter_op: string; filter_value: Member[]; col_type: string } };
type Metric = { id: string; name: string; property_id: string; display_name: string;
  type: string; source: string; aggregation: string };
type Config = { project_ids: string[]; time_range: { preset: string }; granularity: string;
  metrics: Metric[]; filters: Filter[]; breakdowns: Record<string, string>[] };
type Result = { query_complete: boolean; query_exact: boolean; granularity: string;
  time_range: { start: string; end: string }; metrics: { id: string; name: string;
    unit: string; aggregation: string; query_complete: boolean; query_exact: boolean;
    series: { name: string; data: { timestamp: string; value: number | null }[] }[] }[] };
type Property = { property_id: string; property_kind: string; name: string; display_name: string;
  category: string; source: string; type: string; output_type: string; role: string;
  data_type?: string; attribute_types?: string[]; attribute_types_exact?: boolean; allowed_aggregations?: string[] };
type CatalogBody = { result: { metrics: Property[]; has_more: boolean; next_cursor: string | null;
  query_complete: boolean; query_exact: boolean; query_status: string; query_provenance: string } };
type ValueOption = { value: Member; label: string; type?: string };
type ValueBody = { result: { values: ValueOption[]; attribute_types: string[]; query_complete: boolean } };
type Receipt<T> = { input: Record<string, unknown>; body: T; status: number;
  startedAt: number; endedAt: number; scope: { organizationId: string; workspaceId: string } };
type QueryReceipt = Omit<Receipt<{ result: Result }>, 'input'> & { input: Config };
type Project = { id: string; name: string; organization_id: string; workspace_id: string };
type Span = { id: string; extra: string; version: string; [key: string]: unknown };
type Widget = { id: string; name: string; query_config: Config;
  chart_config: { chart_type: string; [key: string]: unknown } };

test('DASH-E2E-006: a saved widget retains native array membership through popup edits', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DASH-E2E-006', area: 'dashboards',
    userGoal: 'A saved widget retains array membership without treating arrays as scalar metrics.',
    steps: [
      'ingest five independently identified traces with mixed array members and scoped scalar/object controls',
      'create a Table widget and exclude arrays/maps from metrics before selecting Traces and Spans',
      'select primary Project, the native array token and a string cohort breakdown',
      'select both same-labelled scalar pairs, save, reload and reopen the mixed membership',
      'deselect visible and hidden members, Specify a never-observed value and save the same widget',
      'reopen the persisted Specify selection, restore the original token and verify tenant/source invariance',
    ],
    backendChecks: [
      "Native catalog and selected property identities preserve this flow's source, types, choices and actor scope.",
      "This flow's exact publicly produced source identities and typed latest facts are present and unchanged outside its authorized UI actions.",
      "The preview, saved binding and reopened widget equal this flow's independently specified filtered and grouped result.",
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe: probe }, testInfo) => {
  test.setTimeout(600_000); // ASYNC_JOB60 + 2×SPAN_VISIBLE15 + 8×UI_READY60 + 30 headroom.
  const uiExpect = expect.configure({ timeout: UI_READY }); // Source polls retain their own budgets.
  // Requires the managed FRONTEND containing WidgetEditorView's array identity fix;
  // earlier DASH/EVAL images and the 46 offline picker tests are not browser proof.
  const prefix = `e2e-dash6-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const tokenPrefix = prefix.replaceAll('0', 'zero').replaceAll('false', 'falsy');
  const h = `${tokenPrefix}-member`, miss = `${tokenPrefix}-absent`;
  const [a, b, c, s, f] = ['a', 'b', 'c', 's', 'f'].map(key => `${tokenPrefix}-${key}`);
  for (const value of [h, miss, a, b, c, s, f]) expect(value).not.toMatch(/0|false/);
  const arrayKey = `${prefix}.members`, objectKey = `${prefix}.object`, cohortKey = `${prefix}.cohort`;
  const foreignKey = `${prefix}.foreign-only`, propertyId = `custom_attribute:${arrayKey}`;
  const projectNames = [`${prefix}-primary`, `${prefix}-sibling`, `${prefix}-foreign`];
  const dashboardName = `${prefix}-dashboard`, widgetName = `${prefix}-widget`;
  const actor = scopeActors.ownerA;
  const emptyActor = scopeActors.withWorkspace(actor, scopeActors.emptyWorkspace.id);
  const plantedDay = Math.floor(Date.now() / DAY) * DAY - DAY;
  const start = BigInt(plantedDay + DAY / 2) * 1_000_000n;
  // Original A/B2 traces/4 spans witness; foreign scalar h cannot become an array.
  const seeds = [
    { key: 'A', project: 0, ms: 50, members: [[h, a, 0, false], [h, a, 0, false]], cohort: 'alpha' },
    { key: 'B', project: 0, ms: 100, members: [[h, b, '0', 'false'], [h, b, '0', 'false']], cohort: 'alpha' },
    { key: 'C', project: 0, ms: 200, members: [[c], [c]], cohort: 'beta' },
    { key: 'S', project: 1, ms: 900, members: [[h, s], [h, s]], cohort: 'alpha' },
    { key: 'F', project: 2, ms: 900, members: [[h, f, 0, false], h], cohort: 'alpha' },
  ].map(seed => ({ ...seed, traceId: randomUUID(), model: `${prefix}-${seed.key}-model`,
    spanIds: [randomBytes(8).toString('hex'), randomBytes(8).toString('hex')] }));
  expect(new Set(seeds.map(seed => seed.traceId)).size).toBe(5);
  expect(new Set(seeds.flatMap(seed => seed.spanIds)).size).toBe(10);
  for (const id of seeds.flatMap(seed => seed.spanIds)) {
    expect(id).toMatch(/^[0-9a-f]{16}$/); expect(id).not.toBe('0000000000000000');
  }
  await testInfo.attach('planted-identities', { contentType: 'application/json', body: JSON.stringify({
    seeds, projectNames, arrayKey, objectKey, cohortKey, foreignKey, h, miss,
    start: String(start), actors: scopeActors.evidence().actors,
  }) });
  const ingestion = await request.newContext();
  try {
    for (const seed of seeds) {
      const owner = seed.project === 2 ? scopeActors.ownerB : actor;
      const keys = scopeActors.owners.find(item => item.organizationId === owner.organizationId && item.workspaceId === owner.workspaceId)!;
      // otlp.ts's recursive AnyValue encoder; no JSON-string seed or data replay.
      const attributes: OtlpAttributes[] = [0, 1].map(index => ({
        'fi.span.kind': index ? 'llm' : 'chain', 'gen_ai.request.model': seed.model, 'gen_ai.cost.total': 0,
        [arrayKey]: seed.members[index], [objectKey]: { member: h }, [cohortKey]: seed.cohort,
        ...(seed.project === 2 ? { [foreignKey]: 'foreign' } : {}),
      }));
      expect(await sendTrace(ingestion, { collectorUrl: E2E.collectorUrl, apiKey: keys.apiKey, secretKey: keys.secretKey,
        projectName: projectNames[seed.project], traceId: seed.traceId, rootSpanId: seed.spanIds[0], childSpanId: seed.spanIds[1],
        rootName: `${prefix}-${seed.key}-root`, childName: `${prefix}-${seed.key}-child`,
        startTimeUnixNano: start, endTimeUnixNano: start + BigInt(seed.ms) * 1_000_000n,
        resourceAttributes: { project_type: 'observe' }, rootAttributes: attributes[0], childAttributes: attributes[1] }))
        .toEqual({ traceId: seed.traceId, spanIds: seed.spanIds, projectName: projectNames[seed.project] });
    }
  } finally { await ingestion.dispose(); }

  let projects: Project[] = [];
  await expect.poll(async () => {
    projects = await probe.pg<Project>('SELECT id, name, organization_id, workspace_id FROM tracer_project WHERE name = ANY($1) AND NOT deleted', [projectNames]);
    return projects.map(({ name, organization_id, workspace_id }) => ({ name, organization_id, workspace_id }))
      .sort((left, right) => left.name.localeCompare(right.name));
  }, POLL.ASYNC_JOB).toEqual(projectNames.map((name, index) => ({ name,
    organization_id: index === 2 ? scopeActors.ownerB.organizationId : actor.organizationId,
    workspace_id: index === 2 ? scopeActors.ownerB.workspaceId : actor.workspaceId })).sort((left, right) => left.name.localeCompare(right.name)));
  const projectIds = projectNames.map(name => projects.find(project => project.name === name)!.id);
  expect(new Set(projectIds).size).toBe(3);
  const projectParams = { primary: projectIds[0], sibling: projectIds[1], foreign: projectIds[2] };
  // adapter.go / 002_spans_v2.sql / harness/otlp-shapes: arrays/objects live in JSON
  // overflow, scalar strings in attrs_string. Presence, not a Map default, owns type.
  const sourceSql = `SELECT id, trace_id, project_id, org_id, parent_span_id, observation_type, name, model, status, cost,
    attrs_string[{arrayKey:String}] AS member_string, mapContains(attrs_string, {arrayKey:String}) AS has_member_string,
    attrs_number[{arrayKey:String}] AS member_number, mapContains(attrs_number, {arrayKey:String}) AS has_member_number,
    attrs_bool[{arrayKey:String}] AS member_bool, mapContains(attrs_bool, {arrayKey:String}) AS has_member_bool,
    attrs_string[{objectKey:String}] AS object_string, mapContains(attrs_string, {objectKey:String}) AS has_object_string,
    attrs_number[{objectKey:String}] AS object_number, mapContains(attrs_number, {objectKey:String}) AS has_object_number,
    attrs_bool[{objectKey:String}] AS object_bool, mapContains(attrs_bool, {objectKey:String}) AS has_object_bool,
    attrs_string[{cohortKey:String}] AS cohort, mapContains(attrs_string, {cohortKey:String}) AS has_cohort,
    mapContains(attrs_number, {cohortKey:String}) AS cohort_has_number, mapContains(attrs_bool, {cohortKey:String}) AS cohort_has_bool,
    attrs_string[{foreignKey:String}] AS foreign_value, mapContains(attrs_string, {foreignKey:String}) AS has_foreign,
    mapContains(attrs_number, {foreignKey:String}) AS foreign_has_number, mapContains(attrs_bool, {foreignKey:String}) AS foreign_has_bool,
    toString(attributes_extra) AS extra, toUnixTimestamp64Micro(start_time) AS start_us,
    toUnixTimestamp64Micro(end_time) AS end_us, toString(_version) AS version, is_deleted
    FROM spans FINAL WHERE project_id IN ({primary:UUID}, {sibling:UUID}, {foreign:UUID}) ORDER BY id`;
  const sourceParams = { ...projectParams, arrayKey, objectKey, cohortKey, foreignKey };
  const expectedSources = seeds.flatMap(seed => seed.spanIds.map((id, index) => ({
    id, trace_id: seed.traceId, project_id: projectIds[seed.project],
    org_id: seed.project === 2 ? scopeActors.ownerB.organizationId : actor.organizationId,
    parent_span_id: index ? seed.spanIds[0] : '', observation_type: index ? 'llm' : 'chain',
    name: `${prefix}-${seed.key}-${index ? 'child' : 'root'}`, model: seed.model, status: 'OK', cost: 0,
    member_string: seed.key === 'F' && index === 1 ? h : '', has_member_string: seed.key === 'F' && index === 1 ? 1 : 0,
    member_number: 0, has_member_number: 0, member_bool: 0, has_member_bool: 0,
    object_string: '', has_object_string: 0, object_number: 0, has_object_number: 0, object_bool: 0, has_object_bool: 0,
    cohort: seed.cohort, has_cohort: 1, cohort_has_number: 0, cohort_has_bool: 0,
    foreign_value: seed.project === 2 ? 'foreign' : '', has_foreign: seed.project === 2 ? 1 : 0,
    foreign_has_number: 0, foreign_has_bool: 0,
    extra_members: seed.key === 'F' && index === 1 ? null : seed.members[index],
    has_extra_members: !(seed.key === 'F' && index === 1), extra_object: { member: h },
    start_us: String(start / 1000n), end_us: String(start / 1000n + BigInt(seed.ms) * 1000n), is_deleted: 0,
  }))).sort((left, right) => left.id.localeCompare(right.id));
  let initialSources: Span[] = [];
  try {
    await expect.poll(async () => {
      initialSources = await probe.ch<Span>(sourceSql, sourceParams);
      return initialSources.map(({ extra, version, ...row }) => {
        const parsed = JSON.parse(extra);
        return { ...row, extra_members: Object.hasOwn(parsed, arrayKey) ? parsed[arrayKey] : null,
          has_extra_members: Object.hasOwn(parsed, arrayKey), extra_object: parsed[objectKey] };
      });
    }, POLL.SPAN_VISIBLE).toEqual(expectedSources);
  } finally {
    await testInfo.attach('source-identities', { contentType: 'application/json', body: JSON.stringify({ projects, expectedSources, initialSources }) });
  }
  // Check2 is deliberately outside the literal poll oracle and never feeds the input.
  expect(JSON.parse(initialSources.find(row => row.id === seeds[0].spanIds[1])!.extra)[arrayKey][2])
    .toBe(EXPECTED_A_CHILD_NUMERIC_MEMBER);
  const traceSql = 'SELECT id, project_id FROM traces FINAL WHERE project_id IN ({primary:UUID}, {sibling:UUID}, {foreign:UUID}) ORDER BY toString(id)';
  const expectedTraces = seeds.map(seed => ({ id: seed.traceId, project_id: projectIds[seed.project] })).sort((left, right) => left.id.localeCompare(right.id));
  await expect.poll(() => probe.ch(traceSql, projectParams), POLL.SPAN_VISIBLE).toEqual(expectedTraces);
  await testInfo.attach('trace-identities', { contentType: 'application/json', body: JSON.stringify(expectedTraces) });

  // source_adapters.py: count metadata does not make an array/map a native metric.
  const arrayProperty = { property_id: propertyId, property_kind: 'custom_attribute', category: 'custom_attribute',
    name: arrayKey, display_name: arrayKey, source: 'traces', type: 'array', output_type: 'array', data_type: 'array',
    role: 'dimension', attribute_types: ['array'], attribute_types_exact: false, allowed_aggregations: ['count', 'count_distinct'] };
  const objectProperty = { ...arrayProperty, property_id: `custom_attribute:${objectKey}`, name: objectKey,
    display_name: objectKey, type: 'map', output_type: 'map', data_type: 'map', attribute_types: ['map'] };
  const cohortProperty = { ...arrayProperty, property_id: `custom_attribute:${cohortKey}`, name: cohortKey,
    display_name: cohortKey, type: 'string', output_type: 'string', data_type: 'string', attribute_types: ['string'] };
  const foreignProperty = { ...cohortProperty, property_id: `custom_attribute:${foreignKey}`, name: foreignKey, display_name: foreignKey };
  const mixedProperty = { ...arrayProperty, type: 'json', output_type: 'json', data_type: 'json', attribute_types: ['array', 'string'] };
  const choices = [{ id: 'trace_count', name: 'Traces' }, { id: 'span_count', name: 'Spans' }];
  const metrics: Metric[] = choices.map(choice => ({ id: choice.id, name: choice.id,
    property_id: `system_attribute:traces:${choice.id}`, display_name: choice.name,
    type: 'system_metric', source: 'traces', aggregation: 'count_distinct' }));
  // WidgetEditorView buildQueryConfig/buildWidgetFilterConfig: Contains keeps an
  // array wire, with scalar members but NO scalar attribute_value_types tags.
  const projectFilter: Filter = { column_id: 'project', property_id: 'system_attribute:traces:project', display_name: 'Project',
    source: 'traces', output_type: 'string', filter_config: {
      filter_type: 'text', filter_op: 'in', filter_value: [projectIds[0]], col_type: 'SYSTEM_METRIC' } };
  const arrayFilter: Filter = { column_id: arrayKey, property_id: propertyId, display_name: arrayKey,
    source: 'traces', output_type: 'array', filter_config: {
      filter_type: 'array', filter_op: 'contains', filter_value: [h], col_type: 'SPAN_ATTRIBUTE' } };
  const breakdown = { name: cohortKey, property_id: `custom_attribute:${cohortKey}`, display_name: cohortKey,
    type: 'custom_attribute', source: 'traces', attribute_type: 'string' };
  const configFor = (members?: Member[], grouped = false): Config => ({ project_ids: [],
    time_range: { preset: '7D' }, granularity: 'day', metrics,
    filters: members === undefined ? [projectFilter] : [projectFilter,
      { ...arrayFilter, filter_config: { ...arrayFilter.filter_config, filter_value: members } }],
    breakdowns: grouped ? [breakdown] : [] });
  const memberIdentity = (value: Member) => `${typeof value}:${JSON.stringify(value)}`;
  // Only OR-member ordering is nonsemantic. Every other config field is exact;
  // duplicate members fail, and persistence comparisons never use this function.
  const comparableConfig = (config: Config) => {
    const copy = structuredClone(config);
    for (const filter of copy.filters) if (filter.column_id === arrayKey) {
      const members = filter.filter_config.filter_value;
      if (!Array.isArray(members) || new Set(members.map(memberIdentity)).size !== members.length) throw new Error('Invalid/duplicate array membership');
      filter.filter_config.filter_value = [...members].sort((left, right) => memberIdentity(left).localeCompare(memberIdentity(right)));
    }
    return copy;
  };
  const configMatches = (actual: Config, expected: Config) => {
    try { return isDeepStrictEqual(comparableConfig(actual), comparableConfig(expected)); } catch { return false; }
  };
  const readMetadata = { query_complete: true, query_exact: false, query_status: 'complete', query_provenance: 'current_property_catalog' };
  const sortOptions = (options: ValueOption[]) => [...options].sort((left, right) =>
    `${left.type}:${memberIdentity(left.value)}`.localeCompare(`${right.type}:${memberIdentity(right.value)}`));
  const arrayOptions = (members: Member[]) => members.map(value => ({ value, label: String(value), type: 'array' }));
  const catalogs: Receipt<CatalogBody>[] = [], values: Receipt<ValueBody>[] = [], queries: QueryReceipt[] = [];
  const scopeResults: unknown[] = [], saves: unknown[] = [];
  const responseErrors: { path: string; error: string }[] = [];
  const pending = new Set<Promise<void>>();
  const context = await scopeActors.openContext(browser, actor);
  try {
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    const browserTimeZone = await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone);
    const scopeOf = (response: Response) => ({
      organizationId: response.request().headers()['x-organization-id'], workspaceId: response.request().headers()['x-workspace-id'],
    }); // Capture only tenant headers; never tokens/cookies/keys.
    const expectedScope = { organizationId: actor.organizationId, workspaceId: actor.workspaceId };
    const captureResponse = (response: Response) => {
      const path = new URL(response.url()).pathname;
      if (![QUERY, METRICS, VALUES].includes(path) || response.request().method() !== 'POST') return;
      const capture = (async () => {
        const body = await response.json();
        const receipt = { input: response.request().postDataJSON(), body, status: response.status(),
          startedAt: response.request().timing().startTime, endedAt: Date.now(), scope: scopeOf(response) };
        if (path === QUERY) queries.push(receipt);
        else if (path === METRICS) catalogs.push(receipt);
        else values.push(receipt);
      })().catch(error => { responseErrors.push({ path, error: String(error) }); });
      pending.add(capture); void capture.finally(() => pending.delete(capture));
    };
    page.on('response', captureResponse);
    let dashboardId = '', savedWidget: Widget | undefined;
    const globalSection = page.locator('.filter-section-title').locator('../..');
    const arrayCard = globalSection.getByText(arrayKey, { exact: true }).locator('../..');
    // FilterValueLabel.jsx puts onClick on the parent Stack; its first label
    // can collapse beside a multi-selection badge in the narrow editor card.
    const arrayValueControl = arrayCard.locator('.filter-value-name').locator('..');
    // Captured managed DOM: Widget's Popper exposes tooltip, not MuiPopper-root.
    const popup = page.getByRole('tooltip').filter({ has: page.getByPlaceholder('Search...', { exact: true }) });
    const searchBox = popup.getByPlaceholder('Search...', { exact: true });
    const selectCategory = async (name: string) => {
      await page.getByLabel(new RegExp(`^${name} property count: `)).locator('..').getByText(name, { exact: true }).click();
    };
    const assertCatalog = async (role: string, category: string, search: string, property?: Property) => {
      const matches = (receipt: Receipt<CatalogBody>) => receipt.status === 200 && isDeepStrictEqual(receipt.scope, expectedScope) &&
        (role ? receipt.input.role === role : !('role' in receipt.input)) && receipt.input.cursor_mode === true &&
        (receipt.input.category || '') === category && (receipt.input.source || '') === (category ? 'traces' : '') &&
        (receipt.input.search || '') === search && receipt.body.result.query_complete === true &&
        (!property || receipt.body.result.metrics.some(row => row.property_id === property.property_id));
      // A validated same-scope/key/query cache receipt is allowed; no forced cache reset.
      await expect.poll(() => catalogs.some(matches), { timeout: UI_READY }).toBe(true);
      const receipt = catalogs.filter(matches).at(-1)!;
      expect(receipt.body.result).toMatchObject(readMetadata);
      if (search) expect(receipt.body.result).toMatchObject({ has_more: false, next_cursor: null });
      expect(Number.isInteger(receipt.input.page_size)).toBe(true);
      expect(Number(receipt.input.page_size)).toBeGreaterThan(0);
      if (!category) { expect(receipt.input).not.toHaveProperty('category'); expect(receipt.input).not.toHaveProperty('source'); }
      if (property) {
        const selected = receipt.body.result.metrics.filter(row => row.property_id === property.property_id);
        expect(selected).toHaveLength(1); expect(selected[0]).toMatchObject(property);
      } else expect(receipt.body.result.metrics).toEqual([]);
      return receipt;
    };
    const assertValues = (body: ValueBody, expected: ValueOption[], types: string[]) => {
      expect(body.result).toMatchObject({ ...readMetadata, has_more: false, next_cursor: null,
        browse_status: 'exhausted', attribute_types_exact: false, attribute_types: types });
      expect(sortOptions(body.result.values)).toEqual(sortOptions(expected));
    };
    const searchMembers = async (search: string, expected: Member[]) => {
      await uiExpect(popup).toBeVisible();
      await searchBox.fill(search);
      await uiExpect(searchBox).toHaveValue(search);
      const matches = (receipt: Receipt<ValueBody>) => receipt.status === 200 && isDeepStrictEqual(receipt.scope, expectedScope) &&
        receipt.input.property_id === propertyId && receipt.input.attribute_type === 'array' &&
        (receipt.input.search || '') === search && receipt.body.result.query_complete === true;
      await expect.poll(() => values.some(matches), { timeout: UI_READY }).toBe(true);
      const receipt = values.filter(matches).at(-1)!;
      expect(receipt.input).toMatchObject({ property_id: propertyId, metric_name: arrayKey,
        metric_type: 'custom_attribute', source: 'traces', project_ids: '', page_size: 10, attribute_type: 'array' });
      assertValues(receipt.body, arrayOptions(expected), ['array']);
      await uiExpect(popup.getByRole('progressbar')).toHaveCount(0);
      const rows = popup.locator('p[title]').filter({ hasNotText: /^Specify: / });
      await uiExpect(rows).toHaveCount(expected.length);
      // The old pair can also have two rows while a cached search is settling.
      await expect.poll(async () => (await rows.evaluateAll(elements => elements.map(element => element.getAttribute('title')))).sort(),
        { timeout: UI_READY }).toEqual(expected.map(String).sort());
      return popup.locator(`p[title="${search}"]`).locator('..');
    };
    const assertPair = async (search: '0' | 'false', checked: boolean) => {
      const rows = await searchMembers(search, search === '0' ? [0, '0'] : [false, 'false']);
      await uiExpect(rows).toHaveCount(2);
      // No first() guess for indistinguishable labels: require BOTH native checkboxes.
      for (let index = 0; index < 2; index++) await uiExpect(rows.nth(index).getByRole('checkbox')).toBeChecked({ checked });
    };
    const assertEditorControls = async () => {
      await uiExpect(arrayCard.getByRole('combobox')).toHaveText('Contains');
      await uiExpect(globalSection.getByText('Project', { exact: true }).locator('../..').locator('.filter-value-name')).toHaveText(projectNames[0]);
      for (const choice of choices) {
        const card = page.locator(`p[title="${choice.name}"]`).locator('../..');
        await uiExpect(card.getByText('Distinct Count', { exact: true })).toBeVisible();
        await uiExpect(card.getByText('Project', { exact: true })).toHaveCount(0);
      }
      await uiExpect(page.locator('.breakdown-section-title').locator('../..').getByText(cohortKey, { exact: true })).toBeVisible();
      await uiExpect(page.getByRole('combobox').filter({ hasText: 'Day' })).toBeVisible();
      await uiExpect(page.getByText('7D', { exact: true })).toBeVisible();
    };
    const queryAfter = async (label: string, expected: Config, vector: [number | null, number | null],
      action: () => Promise<unknown>, tile = false, rawSaved?: Config, reopenedProof = false) => {
      const since = queries.length, startedAt = Date.now();
      await action();
      const matches = (receipt: QueryReceipt) => receipt.startedAt >= startedAt && configMatches(receipt.input, expected) &&
        receipt.body.result?.query_complete === true;
      await expect.poll(() => queries.slice(since).some(matches), { timeout: UI_READY }).toBe(true);
      const receipt = queries.slice(since).find(matches)!;
      await testInfo.attach(`${label}-query`, { contentType: 'application/json', body: JSON.stringify(receipt) });
      expect(receipt.status).toBe(200); expect(receipt.scope).toEqual(expectedScope);
      expect(comparableConfig(receipt.input)).toEqual(comparableConfig(expected));
      if (rawSaved) expect(receipt.input).toEqual(rawSaved); // No persistence-order normalization.
      for (const metric of receipt.input.metrics) expect(metric).not.toHaveProperty('filters');
      if (receipt.input.filters.length === 2) expect(receipt.input.filters[1].filter_config).not.toHaveProperty('attribute_value_types');
      const result = receipt.body.result;
      expect(result).toMatchObject({ query_complete: true, query_exact: true, granularity: 'day' });
      const from = Date.parse(result.time_range.start), to = Date.parse(result.time_range.end);
      expect(to - from).toBe(7 * DAY);
      expect(to).toBeGreaterThanOrEqual(receipt.startedAt - 1000); expect(to).toBeLessThanOrEqual(receipt.endedAt + 1000);
      expect(Number(start / 1_000_000n)).toBeGreaterThan(from); expect(Number(start / 1_000_000n)).toBeLessThan(to);
      const buckets: number[] = [];
      for (let day = Math.floor(from / DAY) * DAY; day <= to; day += DAY) buckets.push(day);
      expect(result.metrics.map(metric => ({ id: metric.id, name: metric.name, unit: metric.unit, aggregation: metric.aggregation })))
        .toEqual(choices.map(choice => ({ ...choice, unit: '', aggregation: 'count_distinct' })));
      const group = expected.breakdowns.length && vector[0] !== null ? 'alpha' : 'total';
      const columns: Record<string, (number | null)[]> = {};
      for (const [index, metric] of result.metrics.entries()) {
        expect(metric).toMatchObject({ query_complete: true, query_exact: true });
        expect(metric.series.map(series => series.name)).toEqual([group]);
        const series = metric.series.find(item => item.name === group)!;
        expect(series.data.map(point => Date.parse(point.timestamp))).toEqual(buckets);
        expect(series.data.map(point => point.value)).toEqual(buckets.map(bucket => bucket === plantedDay ? vector[index] : null));
        if (reopenedProof && metric.id === 'span_count') {
          // Check3 affects ONLY this first reopened mixed-membership API assertion.
          expect(series.data.find(point => Date.parse(point.timestamp) === plantedDay)!.value).toBe(EXPECTED_REOPENED_SPANS);
        }
        columns[`${choices[index].name}${group === 'total' ? '' : ` / ${group}`} (count_distinct)`] =
          buckets.map(bucket => bucket === plantedDay ? vector[index] : null);
      }
      const table = tile ? page.locator(`[data-widget-id="${savedWidget!.id}"]`).getByRole('table') : page.getByRole('table');
      await uiExpect(table).toBeVisible();
      await expect.poll(async () => (await table.locator('thead th').allTextContents()).slice(1).sort(), { timeout: UI_READY }).toEqual(Object.keys(columns).sort());
      const headers = await table.locator('thead th').allTextContents();
      expect(headers[0]).toBe('Time'); expect(headers.slice(1).sort()).toEqual(Object.keys(columns).sort());
      await uiExpect(table.locator('tbody tr')).toHaveCount(buckets.length);
      await uiExpect(table.locator('tbody tr td:first-child')).toHaveText(buckets.map(bucket =>
        new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: browserTimeZone }).format(bucket)));
      for (const [index, name] of headers.slice(1).entries()) await uiExpect(table.locator(`tbody tr td:nth-child(${index + 2})`))
        .toHaveText(columns[name].map(value => value === null ? '-' : tile ? value.toFixed(2) : String(value)));
      await testInfo.attach(`${label}-table`, { contentType: 'image/png', body: await page.screenshot() });
      return receipt;
    };
    const saveWidget = async (label: string, expected: Config) => {
      const previousId = savedWidget?.id;
      const path = previousId ? `${DASHBOARDS}${dashboardId}/widgets/${previousId}/` : `${DASHBOARDS}${dashboardId}/widgets/`;
      const [response] = await Promise.all([
        page.waitForResponse(item => new URL(item.url()).pathname === path && item.request().method() === (previousId ? 'PATCH' : 'POST'), { timeout: UI_READY }),
        page.getByRole('button', { name: 'Save', exact: true }).click(),
      ]);
      expect(response.status()).toBe(200); expect(scopeOf(response)).toEqual(expectedScope);
      const input = response.request().postDataJSON() as Omit<Widget, 'id'>;
      expect(input.name).toBe(widgetName); expect(input.chart_config.chart_type).toBe('table');
      expect(comparableConfig(input.query_config)).toEqual(comparableConfig(expected));
      const saved = ((await response.json()) as { result: Widget }).result;
      if (previousId) expect(saved.id).toBe(previousId);
      expect(saved).toMatchObject({ name: widgetName, query_config: input.query_config, chart_config: input.chart_config });
      savedWidget = saved;
      const rows = await probe.pg('SELECT id, name, dashboard_id, query_config, chart_config FROM tracer_dashboardwidget WHERE dashboard_id = $1 AND NOT deleted', [dashboardId]);
      expect(rows).toEqual([{ id: saved.id, name: widgetName, dashboard_id: dashboardId, query_config: input.query_config, chart_config: input.chart_config }]);
      expect(await probe.pg('SELECT id, name, workspace_id FROM tracer_dashboard WHERE id = $1 AND NOT deleted', [dashboardId]))
        .toEqual([{ id: dashboardId, name: dashboardName, workspace_id: actor.workspaceId }]);
      const detail = await actor.api.get<{ result: { id: string; name: string; workspace: string; widgets: Widget[] } }>(`${DASHBOARDS}${dashboardId}/`);
      expect(detail.result).toMatchObject({ id: dashboardId, name: dashboardName, workspace: actor.workspaceId });
      expect(detail.result.widgets.map(widget => ({ id: widget.id, name: widget.name, query_config: widget.query_config, chart_config: widget.chart_config })))
        .toEqual([{ id: saved.id, name: widgetName, query_config: input.query_config, chart_config: input.chart_config }]);
      await Promise.all([actor, scopeActors.ownerB, emptyActor].map(async selectedActor => {
        const nestedPath = `${DASHBOARDS}${dashboardId}/widgets/${saved.id}/`;
        const scoped = await scopeActors.send<Widget>(selectedActor, 'GET', nestedPath);
        scopeResults.push({ path: nestedPath, organizationId: selectedActor.organizationId, workspaceId: selectedActor.workspaceId, ...scoped });
        expect(scoped.status).toBe(selectedActor === actor ? 200 : 404);
        if (selectedActor === actor) expect(scoped.body).toMatchObject({ id: saved.id, name: widgetName, query_config: input.query_config, chart_config: input.chart_config });
      }));
      const receipt = { label, path, method: response.request().method(), scope: scopeOf(response),
        startedAt: response.request().timing().startTime, endedAt: Date.now(), input, saved, rows, detail };
      saves.push(receipt);
      await testInfo.attach(`${label}-saved-widget`, { contentType: 'application/json', body: JSON.stringify(receipt) });
      await uiExpect(page).toHaveURL(new RegExp(`/dashboard/dashboards/${dashboardId}$`));
    };
    try {
      await test.step('1 create and name a Table widget with 7D and Day', async () => {
        await page.goto('/dashboard/dashboards', { waitUntil: 'domcontentloaded' });
        const [created] = await Promise.all([
          page.waitForResponse(response => new URL(response.url()).pathname === DASHBOARDS && response.request().method() === 'POST', { timeout: UI_READY }),
          page.getByRole('button', { name: 'Create Dashboard', exact: true }).click(),
        ]);
        expect(created.status()).toBe(200); expect(scopeOf(created)).toEqual(expectedScope);
        dashboardId = ((await created.json()) as { result: { id: string } }).result.id;
        await testInfo.attach('dashboard-id', { contentType: 'application/json', body: JSON.stringify({ dashboardId, dashboardName, widgetName }) });
        await page.getByRole('heading', { name: 'Untitled', exact: true }).click();
        await page.getByPlaceholder('Untitled Dashboard').fill(dashboardName);
        const [named] = await Promise.all([
          page.waitForResponse(response => new URL(response.url()).pathname === `${DASHBOARDS}${dashboardId}/` && ['PATCH', 'PUT'].includes(response.request().method()), { timeout: UI_READY }),
          page.getByPlaceholder('Untitled Dashboard').press('Enter'),
        ]);
        expect(named.status()).toBe(200); expect(scopeOf(named)).toEqual(expectedScope);
        expect(named.request().postDataJSON()).toMatchObject({ name: dashboardName });
        saves.push({ label: 'dashboard-create-rename', created: await created.json(), renamed: await named.json(), scope: scopeOf(named) });
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

      await test.step('2 exclude array/map metrics, select Traces/Spans and verify scoped inventories', async () => {
        await page.getByText('Select Metric', { exact: true }).click();
        for (const category of ['All', 'Trace Attributes']) for (const key of [arrayKey, objectKey]) {
          await selectCategory(category); await page.getByPlaceholder('Search metrics...').fill(key);
          await assertCatalog('metric', category === 'All' ? '' : 'custom_attribute', key);
          await uiExpect(page.getByRole('button', { name: new RegExp(`^${key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} \\(`) })).toHaveCount(0);
        }
        for (const [index, choice] of choices.entries()) {
          if (index) await page.locator('.metric-section-title').click();
          await selectCategory('Traces'); await page.getByPlaceholder('Search metrics...').fill(choice.name);
          await assertCatalog('metric', 'system_metric', choice.name, {
            property_id: `system_attribute:traces:${choice.id}`, property_kind: 'system_attribute', category: 'system_metric',
            name: choice.id, display_name: choice.name, source: 'traces', type: 'number', output_type: 'number', role: 'metric',
          });
          await page.getByRole('button', { name: `${choice.name} (number, Traces)`, exact: true }).click();
          await page.locator(`p[title="${choice.name}"]`).locator('../..').locator('.MuiChip-clickable').click();
          await page.getByText('Distinct Count', { exact: true }).last().click();
        }
        // Supplemental API reads use the existing scoped clients, never replace UI actions.
        await Promise.all([actor, scopeActors.ownerB, emptyActor].map(async selectedActor => {
          const foreign = selectedActor === scopeActors.ownerB, empty = selectedActor === emptyActor;
          for (const property of [arrayProperty, objectProperty, cohortProperty, foreignProperty]) {
            for (const role of property === arrayProperty || property === objectProperty ? ['', 'metric'] : ['']) {
              const input = { source: 'traces', category: 'custom_attribute', search: property.name,
                cursor_mode: true, page_size: 25, ...(role ? { role } : {}) };
              const response = await scopeActors.send<CatalogBody>(selectedActor, 'POST', METRICS, input);
              scopeResults.push({ path: METRICS, organizationId: selectedActor.organizationId, workspaceId: selectedActor.workspaceId, input, ...response });
              expect(response.status).toBe(200);
              expect(response.body.result).toMatchObject({ ...readMetadata, has_more: false, next_cursor: null });
              const visible = !empty && !role && (property !== foreignProperty || foreign);
              expect(response.body.result.metrics.map(row => row.property_id)).toEqual(visible ? [property.property_id] : []);
              if (visible) expect(response.body.result.metrics[0]).toMatchObject(foreign && property === arrayProperty ? mixedProperty : property);
            }
          }
          const reads = [
            { key: arrayKey, type: 'array', projects: '', options: arrayOptions(empty ? [] : foreign ? [h, f, 0, false] : [h, a, b, c, s, 0, '0', false, 'false']), types: empty ? [] : foreign ? ['array', 'string'] : ['array'] },
            { key: objectKey, type: 'map', projects: '', options: [], types: empty ? [] : ['map'] },
            { key: cohortKey, type: 'string', projects: '', options: (empty ? [] : foreign ? ['alpha'] : ['alpha', 'beta']).map(value => ({ value, label: value, type: 'string' })), types: empty ? [] : ['string'] },
            { key: foreignKey, type: 'string', projects: '', options: foreign ? [{ value: 'foreign', label: 'foreign', type: 'string' }] : [], types: foreign ? ['string'] : [] },
            ...(foreign ? [
              { key: arrayKey, type: '', projects: '', options: [...arrayOptions([h, f, 0, false]), { value: h, label: h, type: 'string' }], types: ['array', 'string'] },
              { key: arrayKey, type: 'string', projects: '', options: [{ value: h, label: h, type: 'string' }], types: ['array', 'string'] },
            ] : selectedActor === actor ? [
              { key: arrayKey, type: 'array', projects: projectIds[0], options: arrayOptions([h, a, b, c, 0, '0', false, 'false']), types: ['array'] },
            ] : []),
          ];
          for (const read of reads) {
            const input = { property_id: `custom_attribute:${read.key}`, metric_name: read.key, metric_type: 'custom_attribute',
              source: 'traces', project_ids: read.projects, page_size: 10, ...(read.type ? { attribute_type: read.type } : {}) };
            const response = await scopeActors.send<ValueBody>(selectedActor, 'POST', VALUES, input);
            scopeResults.push({ path: VALUES, organizationId: selectedActor.organizationId, workspaceId: selectedActor.workspaceId, input, ...response });
            expect(response.status).toBe(200); assertValues(response.body, read.options, read.types);
          }
        }));
      }, { timeout: UI_READY });

      await test.step('3 select Project and verify the unfiltered primary 3-trace/6-span control', async () => {
        await queryAfter('project-only', configFor(), [3, 6], async () => {
          await page.locator('.filter-section-title').click(); await selectCategory('Traces');
          await page.getByPlaceholder('Search filter attributes...').fill('Project');
          await assertCatalog('', 'system_metric', 'Project', { property_id: 'system_attribute:traces:project',
            property_kind: 'system_attribute', category: 'system_metric', name: 'project', display_name: 'Project',
            source: 'traces', type: 'string', output_type: 'string', role: 'dimension' });
          await page.getByRole('button', { name: 'Project (string, Traces)', exact: true }).click();
          await page.getByText('Select value...', { exact: true }).click();
          await searchBox.fill(projectNames[0]);
          await popup.locator(`p[title="${projectNames[0]}"]`).click();
          await popup.getByRole('button', { name: 'Add', exact: true }).click();
        });
        await expect.poll(() => values.some(value => value.input.property_id === projectFilter.property_id &&
          value.body.result.values.some(option => option.value === projectIds[0] && option.label === projectNames[0])), { timeout: UI_READY }).toBe(true);
        const projectValues = values.filter(value => value.input.property_id === projectFilter.property_id);
        for (const receipt of projectValues) {
          expect(receipt.status).toBe(200); expect(receipt.scope).toEqual(expectedScope);
          expect(receipt.input).toMatchObject({ property_id: projectFilter.property_id, metric_name: 'project',
            metric_type: 'system_metric', source: 'traces', project_ids: '', page_size: 10 });
          expect(receipt.body.result.values.filter(option => option.value === projectIds[2] || option.label === projectNames[2])).toEqual([]);
        }
      }, { timeout: UI_READY });

      await test.step('4 discover array identity, select only the token and add scalar cohort grouping', async () => {
        const token = await queryAfter('token-only', configFor([h]), [2, 4], async () => {
          await page.locator('.filter-section-title').click();
          for (const lane of [
            { category: 'Trace Attributes', search: '' }, { category: 'All', search: arrayKey },
            { category: 'Trace Attributes', search: arrayKey }, { category: 'Trace Attributes', search: '' },
            { category: 'Trace Attributes', search: arrayKey },
          ]) {
            await selectCategory(lane.category); await page.getByPlaceholder('Search filter attributes...').fill(lane.search);
            await assertCatalog('', lane.category === 'All' ? '' : 'custom_attribute', lane.search, arrayProperty);
            await uiExpect(page.getByRole('button', { name: `${arrayKey} (array, array, Traces)`, exact: true })).toHaveCount(1);
            await uiExpect(page.getByRole('button', { name: `${arrayKey} (string, string, Traces)`, exact: true })).toHaveCount(0);
          }
          await page.getByPlaceholder('Search filter attributes...').fill(objectKey);
          await assertCatalog('', 'custom_attribute', objectKey, objectProperty);
          await uiExpect(page.getByRole('button', { name: `${objectKey} (map, map, Traces)`, exact: true })).toHaveCount(0);
          await page.getByPlaceholder('Search filter attributes...').fill(arrayKey);
          await uiExpect(page.getByRole('button', { name: `${arrayKey} (array, array, Traces)`, exact: true })).toBeVisible();
          await page.getByRole('button', { name: `${arrayKey} (array, array, Traces)`, exact: true }).click();
          // New custom filter auto-opens: do not toggle the trigger and close it.
          const row = await searchMembers(h, [h]); await uiExpect(row.getByRole('checkbox')).not.toBeChecked();
          await row.click(); await popup.getByRole('button', { name: 'Add', exact: true }).click();
        });
        expect(token.input.filters[1].filter_config.filter_op).toBe(EXPECTED_ARRAY_FILTER_OP); // Check1, not a matcher.
        await uiExpect(arrayCard.getByRole('combobox')).toHaveText('Contains');
        await queryAfter('token-cohort', configFor([h], true), [2, 4], async () => {
          await page.locator('.breakdown-section-title').click();
          for (const category of ['All', 'Trace Attributes']) for (const property of [arrayProperty, objectProperty]) {
            await selectCategory(category); await page.getByPlaceholder('Search breakdown attributes...').fill(property.name);
            await assertCatalog('', category === 'All' ? '' : 'custom_attribute', property.name, property);
            await uiExpect(page.getByRole('button', { name: new RegExp(`^${property.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} \\(`) })).toHaveCount(0);
          }
          await page.getByPlaceholder('Search breakdown attributes...').fill(cohortKey);
          await assertCatalog('', 'custom_attribute', cohortKey, cohortProperty);
          await page.getByRole('button', { name: `${cohortKey} (string, string, Traces)`, exact: true }).click();
        });
      }, { timeout: UI_READY });

      await test.step('5 keep hidden token, select both scalar pairs and save five typed members', async () => {
        await queryAfter('mixed-five', configFor([h, false, 'false', 0, '0'], true), [2, 4], async () => {
          await arrayValueControl.click();
          await uiExpect((await searchMembers(h, [h])).getByRole('checkbox')).toBeChecked();
          await assertPair('false', false); await popup.getByText('Select all in list (2)', { exact: true }).click();
          await assertPair('false', true);
          await assertPair('0', false); await popup.getByText('Select all in list (2)', { exact: true }).click();
          await assertPair('0', true);
          await uiExpect((await searchMembers(h, [h])).getByRole('checkbox')).toBeChecked();
          await testInfo.attach('mixed-popup-token-still-checked', { contentType: 'image/png', body: await page.screenshot() });
          await popup.getByRole('button', { name: 'Add', exact: true }).click();
        });
        await saveWidget('mixed', configFor([h, false, 'false', 0, '0'], true));
      }, { timeout: UI_READY });

      await test.step('6 reload and reopen the saved five-member widget and its checked popup rows', async () => {
        const expected = configFor([h, false, 'false', 0, '0'], true), raw = structuredClone(savedWidget!.query_config);
        await queryAfter('mixed-reload', expected, [2, 4], () => page.reload({ waitUntil: 'domcontentloaded' }), true, raw);
        await queryAfter('mixed-reopen', expected, [2, 4], () => page.locator(`[data-widget-id="${savedWidget!.id}"]`)
          .getByText(widgetName, { exact: true }).click(), false, raw, true);
        await assertEditorControls();
        await arrayValueControl.click();
        await uiExpect((await searchMembers(h, [h])).getByRole('checkbox')).toBeChecked();
        await assertPair('false', true); await assertPair('0', true);
        await testInfo.attach('reopened-mixed-checkboxes', { contentType: 'image/png', body: await page.screenshot() });
        // Applying an unchanged selection closes the popup; no artificial query required.
        await popup.getByRole('button', { name: 'Add', exact: true }).click();
        await uiExpect(popup).toHaveCount(0);
      }, { timeout: UI_READY });

      await test.step('7 deselect visible and hidden members, Specify a disjoint value and update the same widget', async () => {
        await queryAfter('deselect-zero-pair', configFor([h, false, 'false'], true), [2, 4], async () => {
          await arrayValueControl.click();
          await assertPair('0', true); await popup.getByText('Select all in list (2)', { exact: true }).click();
          await assertPair('0', false); await assertPair('false', true);
          await uiExpect((await searchMembers(h, [h])).getByRole('checkbox')).toBeChecked();
          await popup.getByRole('button', { name: 'Add', exact: true }).click();
        });
        await queryAfter('specify-only', configFor([miss], true), [null, null], async () => {
          await arrayValueControl.click();
          await assertPair('false', true); await popup.getByText('Select all in list (2)', { exact: true }).click();
          await assertPair('false', false);
          const row = await searchMembers(h, [h]); await uiExpect(row.getByRole('checkbox')).toBeChecked();
          await row.click(); await uiExpect(row.getByRole('checkbox')).not.toBeChecked();
          await uiExpect(popup.getByRole('button', { name: 'Add', exact: true })).toBeDisabled();
          await searchMembers(miss, []);
          const specify = popup.locator(`[data-widget-filter-exact-value="${miss}"]`);
          await uiExpect(specify.getByRole('checkbox')).not.toBeChecked();
          await specify.click(); await uiExpect(specify.getByRole('checkbox')).toBeChecked();
          await testInfo.attach('specify-only-checkbox', { contentType: 'image/png', body: await page.screenshot() });
          await popup.getByRole('button', { name: 'Add', exact: true }).click();
        });
        await saveWidget('specify', configFor([miss], true));
      }, { timeout: UI_READY });

      await test.step('8 reopen persisted Specify, restore the original token and verify scope/source invariance', async () => {
        const expected = configFor([miss], true), raw = structuredClone(savedWidget!.query_config);
        await queryAfter('specify-reload', expected, [null, null], () => page.reload({ waitUntil: 'domcontentloaded' }), true, raw);
        await queryAfter('specify-reopen', expected, [null, null], () => page.locator(`[data-widget-id="${savedWidget!.id}"]`)
          .getByText(widgetName, { exact: true }).click(), false, raw);
        await assertEditorControls();
        await queryAfter('restore-token', configFor([h], true), [2, 4], async () => {
          await arrayValueControl.click(); await searchMembers(miss, []);
          const specify = popup.locator(`[data-widget-filter-exact-value="${miss}"]`);
          await uiExpect(specify.getByRole('checkbox')).toBeChecked();
          await testInfo.attach('persisted-specify-checked', { contentType: 'image/png', body: await page.screenshot() });
          await specify.click(); await uiExpect(specify.getByRole('checkbox')).not.toBeChecked();
          await uiExpect(popup.getByRole('button', { name: 'Add', exact: true })).toBeDisabled();
          const row = await searchMembers(h, [h]); await uiExpect(row.getByRole('checkbox')).not.toBeChecked();
          await row.click(); await popup.getByRole('button', { name: 'Add', exact: true }).click();
        });
        await assertEditorControls(); await saveWidget('restored-token', configFor([h], true));
        const finalSources = await probe.ch<Span>(sourceSql, sourceParams), finalTraces = await probe.ch(traceSql, projectParams);
        await testInfo.attach('unchanged-source-and-versions', { contentType: 'application/json', body: JSON.stringify({ initialSources, finalSources, expectedTraces, finalTraces }) });
        expect(finalSources).toEqual(initialSources); expect(finalTraces).toEqual(expectedTraces);
      }, { timeout: UI_READY });
    } finally { page.off('response', captureResponse); }
  } finally {
    try {
      await Promise.all(pending);
      await testInfo.attach('native-catalog-values-queries-saves-scope', { contentType: 'application/json',
        body: JSON.stringify({ catalogs, values, queries, saves, scopeResults, responseErrors }) });
    } finally { await context.close(); }
  }
});
