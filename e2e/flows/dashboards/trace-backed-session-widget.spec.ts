import { randomBytes, randomUUID } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { request, type Response } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { sendTrace } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// useDashboards.js / tracer/urls.py: native catalog, values, query, wrapped
// dashboard/create/update and RAW inherited nested-widget GET endpoints.
const DASHBOARDS = '/tracer/dashboard/';
const METRICS = `${DASHBOARDS}metrics/`;
const VALUES = `${DASHBOARDS}filter_values/`;
const QUERY = `${DASHBOARDS}query/`;
const UI_READY = 60_000; // One wall per whole approved stage; README Writing a flow.
const DAY = 86_400_000;
const SESSION_PROPERTY = 'system_attribute:traces:session'; // source_adapters.py, not source sessions.
const EXPECTED_REOPENED_SPANS = 4; // Check3 only: -> 5, never a seed/config matcher.

type Filter = { column_id: string; property_id: string; display_name: string;
  source: string; output_type: string; filter_config: { filter_type: string;
    filter_op: string; filter_value: string[]; col_type: string } };
type Metric = { id: string; name: string; property_id: string; display_name: string;
  type: string; source: string; aggregation: string };
type Config = { project_ids: string[]; time_range: { preset: string }; granularity: string;
  metrics: Metric[]; filters: Filter[]; breakdowns: Record<string, string>[] };
type Vector = [number | null, number | null, number | null];
type Result = { query_complete: boolean; query_exact: boolean; granularity: string;
  time_range: { start: string; end: string }; metrics: { id: string; name: string;
    unit: string; aggregation: string; query_complete: boolean; query_exact: boolean;
    series: { name: string; data: { timestamp: string; value: number | null }[] }[] }[] };
type Property = { property_id: string; property_kind: string; name: string; display_name: string;
  category: string; source: string; type: string; output_type: string; role: string };
type CatalogBody = { result: { metrics: Property[]; has_more: boolean; next_cursor: string | null;
  query_complete: boolean; query_exact: boolean; query_status: string; query_provenance: string } };
type ValueOption = { value: string; label: string };
type ValueBody = { result: { values: ValueOption[]; query_complete: boolean; query_status: string;
  has_more: boolean; next_cursor: string | null; browse_status: string;
  query_window_start?: string; query_window_end?: string } };
type Receipt<T> = { input: Record<string, unknown>; body: T; status: number;
  startedAt: number; endedAt: number; scope: { organizationId: string; workspaceId: string } };
type QueryReceipt = Omit<Receipt<{ result: Result }>, 'input'> & { input: Config };
type Project = { id: string; name: string; organization_id: string; workspace_id: string };
type Span = { id: string; project_id: string; trace_session_id: string; session: string;
  version: string; [key: string]: unknown };
type Session = { project_id: string; trace_session_id: string; external_session_id: string;
  first_seen: string; version: string; is_deleted: number };
type Trace = { id: string; project_id: string; version: string; is_deleted: number; [key: string]: unknown };
type Widget = { id: string; name: string; query_config: Config;
  chart_config: { chart_type: string; [key: string]: unknown } };

test('DASH-E2E-007: a saved Session widget counts only the selected project session traces', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DASH-E2E-007', area: 'dashboards',
    userGoal: "A saved Session widget counts only the selected project's session traces.",
    steps: [
      'create and name a Table widget with 7D and Day after ingesting five independently identified traces',
      'discover Sessions, Traces and Spans and select Distinct Count for each',
      'select primary Project and verify its two sessions, three traces and six spans',
      'distinguish same-labelled sessions by UUID, select primary S1 and add native Session grouping',
      'save S1 and verify the exact persisted binding and saved table',
      'reload and reopen S1 with its exact selected UUID, label, controls and 1/2/4 result',
      'replace S1 with S2, verify 1/1/2 and save the same widget',
      'reload and reopen S2, then Specify a never-ingested UUID and verify exact empty results',
      'restore and save S1 and verify tenant isolation and unchanged source versions',
    ],
    backendChecks: [
      "Native catalog and selected property identities preserve this flow's source, types, choices and actor scope.",
      "This flow's exact publicly produced source identities and typed latest facts are present and unchanged outside its authorized UI actions.",
      "The preview, saved binding and reopened widget equal this flow's independently specified filtered and grouped result.",
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe: probe }, testInfo) => {
  test.setTimeout(660_000); // ASYNC_JOB60 + 2×SPAN_VISIBLE15 + 9×UI_READY60 + 30 headroom; master S/R9.
  const uiExpect = expect.configure({ timeout: UI_READY }); // Source polls keep their named ceilings.
  // Approved observed-catalog-dash7-plan-20260910.md. Requires main-qualified
  // serving images, including popup tooltip suppression; prior runs are not proof.
  const prefix = `e2e-dash7-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const S1 = `${prefix}-S1`, S2 = `${prefix}-S2`, miss = randomUUID();
  const projectNames = [`${prefix}-primary`, `${prefix}-sibling`, `${prefix}-foreign`];
  const dashboardName = `${prefix}-dashboard`, widgetName = `${prefix}-widget`;
  const actor = scopeActors.ownerA;
  const emptyActor = scopeActors.withWorkspace(actor, scopeActors.emptyWorkspace.id);
  const plantedDay = Math.floor(Date.now() / DAY) * DAY - DAY;
  const start = BigInt(plantedDay + DAY / 2) * 1_000_000n;
  // Both spans carry string session.id. Sibling/foreign S1 spellings intentionally
  // collide, while the collector's project-scoped session UUIDs must not.
  const seeds = [
    { key: 'A', project: 0, session: S1 }, { key: 'B', project: 0, session: S1 },
    { key: 'C', project: 0, session: S2 }, { key: 'S', project: 1, session: S1 },
    { key: 'F', project: 2, session: S1 },
  ].map(seed => ({ ...seed, traceId: randomUUID(),
    spanIds: [randomBytes(8).toString('hex'), randomBytes(8).toString('hex')] }));
  expect(new Set(seeds.map(seed => seed.traceId)).size).toBe(5);
  expect(new Set(seeds.flatMap(seed => seed.spanIds)).size).toBe(10);
  for (const id of seeds.flatMap(seed => seed.spanIds)) {
    expect(id).toMatch(/^[0-9a-f]{16}$/); expect(id).not.toBe('0000000000000000');
  }
  await testInfo.attach('planted-identities', { contentType: 'application/json', body: JSON.stringify({
    seeds, projectNames, S1, S2, miss, start: String(start), actors: scopeActors.evidence().actors,
  }) });
  const ingestion = await request.newContext();
  try {
    for (const seed of seeds) {
      const owner = seed.project === 2 ? scopeActors.ownerB : actor;
      const keys = scopeActors.owners.find(item => item.organizationId === owner.organizationId && item.workspaceId === owner.workspaceId)!;
      // otlp.ts encoder and converter.go:newSpanIdentity; no alternate identity producer/replay.
      expect(await sendTrace(ingestion, { collectorUrl: E2E.collectorUrl, apiKey: keys.apiKey, secretKey: keys.secretKey,
        projectName: projectNames[seed.project], traceId: seed.traceId, rootSpanId: seed.spanIds[0], childSpanId: seed.spanIds[1],
        rootName: `${prefix}-${seed.key}-root`, childName: `${prefix}-${seed.key}-child`,
        startTimeUnixNano: start, endTimeUnixNano: start + 50_000_000n,
        resourceAttributes: { project_type: 'observe' },
        rootAttributes: { 'session.id': seed.session, 'fi.span.kind': 'chain' },
        childAttributes: { 'session.id': seed.session, 'fi.span.kind': 'llm' } }))
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
  // Physical schema 002/015/018: spans UUID FK, typed Map presence, microsecond
  // timestamps; curated sessions have no org column. Keep versions verbatim.
  const sourceSql = `SELECT id, trace_id, project_id, org_id, parent_span_id, observation_type, name,
    trace_session_id, attrs_string['session.id'] AS session, mapContains(attrs_string, 'session.id') AS has_session,
    mapContains(attrs_number, 'session.id') AS has_number, mapContains(attrs_bool, 'session.id') AS has_bool,
    toUnixTimestamp64Micro(start_time) AS start_us, toUnixTimestamp64Micro(end_time) AS end_us,
    toString(_version) AS version, is_deleted FROM spans FINAL
    WHERE project_id IN ({primary:UUID}, {sibling:UUID}, {foreign:UUID}) ORDER BY id`;
  const sessionSql = `SELECT project_id, trace_session_id, external_session_id, toString(first_seen) AS first_seen,
    toString(version) AS version, is_deleted FROM trace_sessions FINAL
    WHERE project_id IN ({primary:UUID}, {sibling:UUID}, {foreign:UUID}) ORDER BY project_id, trace_session_id`;
  const expectedSessionFacts = [[0, S1], [0, S2], [1, S1], [2, S1]].map(([project, label]) => ({
    project_id: projectIds[Number(project)], external_session_id: String(label), is_deleted: 0,
  })).sort((left, right) => `${left.project_id}:${left.external_session_id}`.localeCompare(`${right.project_id}:${right.external_session_id}`));
  const expectedSpanFacts = seeds.flatMap(seed => seed.spanIds.map((id, index) => ({
    id, trace_id: seed.traceId, project_id: projectIds[seed.project],
    org_id: seed.project === 2 ? scopeActors.ownerB.organizationId : actor.organizationId,
    parent_span_id: index ? seed.spanIds[0] : '', observation_type: index ? 'llm' : 'chain',
    name: `${prefix}-${seed.key}-${index ? 'child' : 'root'}`, session: seed.session,
    has_session: 1, has_number: 0, has_bool: 0, session_identity: seed.session,
    start_us: String(start / 1000n), end_us: String(start / 1000n + 50_000n), is_deleted: 0,
  }))).sort((left, right) => left.id.localeCompare(right.id));
  let initialSources: Span[] = [], initialSessions: Session[] = [];
  try {
    await expect.poll(async () => {
      [initialSources, initialSessions] = await Promise.all([
        probe.ch<Span>(sourceSql, projectParams), probe.ch<Session>(sessionSql, projectParams),
      ]);
      return {
        sessions: initialSessions.map(({ project_id, external_session_id, is_deleted }) => ({ project_id, external_session_id, is_deleted }))
          .sort((left, right) => `${left.project_id}:${left.external_session_id}`.localeCompare(`${right.project_id}:${right.external_session_id}`)),
        spans: initialSources.map(({ trace_session_id, version, ...row }) => ({ ...row,
          session_identity: initialSessions.find(session => session.project_id === row.project_id && session.trace_session_id === trace_session_id)?.external_session_id ?? null,
        })),
      };
    }, POLL.SPAN_VISIBLE).toEqual({ sessions: expectedSessionFacts, spans: expectedSpanFacts });
  } finally {
    await testInfo.attach('source-identities', { contentType: 'application/json', body: JSON.stringify({
      projects, expectedSessionFacts, expectedSpanFacts, initialSources, initialSessions,
    }) });
  }
  expect(new Set(initialSessions.map(session => session.trace_session_id)).size).toBe(4);
  for (const session of initialSessions) {
    expect(session.trace_session_id).toMatch(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/);
    expect(session.trace_session_id).not.toBe('00000000-0000-0000-0000-000000000000');
    expect(session.trace_session_id).not.toBe(miss);
    expect(session.first_seen).toMatch(/^\d{4}-\d{2}-\d{2} /); expect(session.version).toMatch(/^\d{4}-\d{2}-\d{2} /);
  }
  for (const span of initialSources) expect(BigInt(span.version)).toBeGreaterThan(0n);
  // Check2: change ONLY this expected S1 to S2. The seed/readiness/query oracles stay canonical.
  expect(initialSources.find(row => row.id === seeds[0].spanIds[1])!.session).toBe(S1);
  const sessionId = (project: number, label: string) => initialSessions.find(row => row.project_id === projectIds[project] && row.external_session_id === label)!.trace_session_id;
  const primaryS1 = sessionId(0, S1), primaryS2 = sessionId(0, S2);
  const siblingS1 = sessionId(1, S1), foreignS1 = sessionId(2, S1);
  const traceSql = `SELECT id, project_id, name, session_id, toString(created_at) AS created_at,
    toString(updated_at) AS updated_at, toString(_version) AS version, is_deleted FROM traces FINAL
    WHERE project_id IN ({primary:UUID}, {sibling:UUID}, {foreign:UUID}) ORDER BY toString(id)`;
  const expectedTraces = seeds.map(seed => ({ id: seed.traceId, project_id: projectIds[seed.project], is_deleted: 0 }))
    .sort((left, right) => left.id.localeCompare(right.id));
  let initialTraces: Trace[] = [];
  try {
    await expect.poll(async () => {
      initialTraces = await probe.ch<Trace>(traceSql, projectParams);
      return initialTraces.map(({ id, project_id, is_deleted }) => ({ id, project_id, is_deleted }));
    }, POLL.SPAN_VISIBLE).toEqual(expectedTraces);
  } finally {
    await testInfo.attach('curated-traces', { contentType: 'application/json', body: JSON.stringify({ expectedTraces, initialTraces }) });
  }
  for (const trace of initialTraces) expect(BigInt(trace.version)).toBeGreaterThan(0n);
  // 019_id_remap and models/trace_session.py: fresh sessions have no historical
  // survivor or user overlay. Query only owned soft identities, without mutation.
  const ids = { s1: primaryS1, s2: primaryS2, sibling: siblingS1, foreign: foreignS1 };
  const remapSql = `SELECT old_id, new_id, toString(version) AS version FROM trace_session_id_remap FINAL
    WHERE old_id IN ({s1:UUID}, {s2:UUID}, {sibling:UUID}, {foreign:UUID})
       OR new_id IN ({s1:UUID}, {s2:UUID}, {sibling:UUID}, {foreign:UUID}) ORDER BY old_id`;
  const overlaySql = 'SELECT id, project_id, trace_session_id, display_name, bookmarked FROM trace_session_overlay WHERE project_id = ANY($1::uuid[]) OR trace_session_id = ANY($2::uuid[]) ORDER BY id';
  const sessionIds = Object.values(ids);
  const initialRemaps = await probe.ch(remapSql, ids);
  const initialOverlays = await probe.pg(overlaySql, [projectIds, sessionIds]);
  expect(initialRemaps).toEqual([]); expect(initialOverlays).toEqual([]);
  await testInfo.attach('bound-session-identities', { contentType: 'application/json', body: JSON.stringify({
    projectIds, primaryS1, primaryS2, siblingS1, foreignS1, initialRemaps, initialOverlays,
  }) });

  // source_adapters.py: native counts/dimensions retain source traces and registry IDs.
  const choices = [{ id: 'session_count', name: 'Sessions' }, { id: 'trace_count', name: 'Traces' }, { id: 'span_count', name: 'Spans' }];
  const properties: Property[] = [...choices.map(choice => ({ id: choice.id, label: choice.name, type: 'number', role: 'metric' })),
    { id: 'project', label: 'Project', type: 'string', role: 'dimension' },
    { id: 'session', label: 'Session', type: 'string', role: 'dimension' }].map(item => ({
    property_id: `system_attribute:traces:${item.id}`, property_kind: 'system_attribute', category: 'system_metric',
    name: item.id, display_name: item.label, source: 'traces', type: item.type, output_type: item.type, role: item.role,
  }));
  const metrics: Metric[] = choices.map(choice => ({ id: choice.id, name: choice.id,
    property_id: `system_attribute:traces:${choice.id}`, display_name: choice.name,
    type: 'system_metric', source: 'traces', aggregation: 'count_distinct' }));
  // WidgetEditorView buildQueryConfig/buildWidgetFilterConfig and dashboardDateRange:
  // Is -> text/in UUID membership; uppercase 7D; no local filters or type-tag invention.
  const projectFilter: Filter = { column_id: 'project', property_id: 'system_attribute:traces:project', display_name: 'Project',
    source: 'traces', output_type: 'string', filter_config: {
      filter_type: 'text', filter_op: 'in', filter_value: [projectIds[0]], col_type: 'SYSTEM_METRIC' } };
  const configFor = (selected?: string, grouped = false): Config => ({ project_ids: [],
    time_range: { preset: '7D' }, granularity: 'day', metrics,
    filters: selected === undefined ? [projectFilter] : [projectFilter, {
      column_id: 'session', property_id: SESSION_PROPERTY, display_name: 'Session', source: 'traces', output_type: 'string',
      filter_config: { filter_type: 'text', filter_op: 'in', filter_value: [selected], col_type: 'SYSTEM_METRIC' },
    }], breakdowns: grouped ? [{ name: 'session', property_id: SESSION_PROPERTY, display_name: 'Session',
      type: 'system_metric', source: 'traces' }] : [] });
  const canonicalS1 = configFor(primaryS1, true), canonicalS2 = configFor(primaryS2, true);
  const catalogMetadata = { query_complete: true, query_exact: false, query_status: 'complete', query_provenance: 'current_property_catalog' };
  const sortOptions = (options: ValueOption[]) => [...options].sort((left, right) => left.value.localeCompare(right.value));
  const catalogs: Receipt<CatalogBody>[] = [], values: Receipt<ValueBody>[] = [], queries: QueryReceipt[] = [];
  const scopeResults: unknown[] = [], saves: unknown[] = [];
  const responseErrors: { path: string; status: number; startedAt: number; error: string }[] = [];
  const pending = new Set<Promise<void>>();
  const context = await scopeActors.openContext(browser, actor);
  try {
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    const browserTimeZone = await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone);
    const scopeOf = (response: Response) => ({ organizationId: response.request().headers()['x-organization-id'],
      workspaceId: response.request().headers()['x-workspace-id'] }); // Never capture credential headers.
    const expectedScope = { organizationId: actor.organizationId, workspaceId: actor.workspaceId };
    const captureResponse = (response: Response) => {
      const path = new URL(response.url()).pathname;
      if (![QUERY, METRICS, VALUES].includes(path) || response.request().method() !== 'POST') return;
      const capture = (async () => {
        const receipt = { input: response.request().postDataJSON(), body: await response.json(), status: response.status(),
          startedAt: response.request().timing().startTime, endedAt: Date.now(), scope: scopeOf(response) };
        if (path === QUERY) queries.push(receipt); else if (path === METRICS) catalogs.push(receipt); else values.push(receipt);
      })().catch(error => { responseErrors.push({ path, status: response.status(), startedAt: response.request().timing().startTime,
        error: error instanceof Error ? error.name : 'UnknownError' }); });
      pending.add(capture); void capture.finally(() => pending.delete(capture));
    };
    page.on('response', captureResponse);
    let dashboardId = '', savedWidget: Widget | undefined;
    let lastBuckets: number[] = [];
    const globalSection = page.locator('.filter-section-title').locator('../..');
    const projectCard = globalSection.getByText('Project', { exact: true }).locator('../..');
    const sessionCard = globalSection.getByText('Session', { exact: true }).locator('../..');
    // FilterValueLabel owns onClick on the parent Stack; the heading opens catalog.
    const sessionValueControl = sessionCard.locator('.filter-value-name').locator('..');
    // Captured DASH006 native DOM, distinct from the selected-values summary tooltip.
    const popup = page.getByRole('tooltip').filter({ has: page.getByPlaceholder('Search...', { exact: true }) });
    const searchBox = popup.getByPlaceholder('Search...', { exact: true });
    const selectTraces = async () => {
      await page.getByLabel(/^Traces property count: /).locator('..').getByText('Traces', { exact: true }).click();
    };
    const assertCatalog = async (property: Property, metricMode: boolean) => {
      const matches = (receipt: Receipt<CatalogBody>) => isDeepStrictEqual(receipt.scope, expectedScope) &&
        receipt.input.cursor_mode === true && receipt.input.category === 'system_metric' && receipt.input.source === 'traces' &&
        receipt.input.search === property.display_name && (metricMode ? receipt.input.role === 'metric' : !('role' in receipt.input));
      await expect.poll(() => catalogs.some(receipt => matches(receipt) && (receipt.status !== 200 || receipt.body.result?.query_complete === true)), { timeout: UI_READY }).toBe(true);
      const receipt = catalogs.filter(matches).at(-1)!;
      expect(receipt.status).toBe(200); expect(receipt.input).not.toHaveProperty('project_ids');
      expect(Number.isInteger(receipt.input.page_size)).toBe(true); expect(Number(receipt.input.page_size)).toBeGreaterThan(0);
      expect(receipt.body.result).toMatchObject({ ...catalogMetadata, has_more: false, next_cursor: null });
      const selected = receipt.body.result.metrics.filter(row => row.property_id === property.property_id);
      expect(selected).toHaveLength(1); expect(selected[0]).toMatchObject(property);
    };
    const assertValues = (body: ValueBody, expected: ValueOption[]) => {
      // dashboard.py system adapter deliberately has no query_exact/type tags.
      expect(body.result).toMatchObject({ query_complete: true, query_status: 'complete', has_more: false, next_cursor: null });
      expect(sortOptions(body.result.values)).toEqual(sortOptions(expected));
    };
    const searchOptions = async (name: 'project' | 'session', search: string, expected: ValueOption[]) => {
      await uiExpect(popup).toHaveCount(1); await uiExpect(popup).toBeVisible();
      await searchBox.fill(search); await uiExpect(searchBox).toHaveValue(search);
      const matches = (receipt: Receipt<ValueBody>) => isDeepStrictEqual(receipt.scope, expectedScope) &&
        receipt.input.property_id === `system_attribute:traces:${name}` && (receipt.input.search || '') === search &&
        receipt.input.metric_name === name && receipt.input.metric_type === 'system_metric' &&
        receipt.input.source === 'traces' && receipt.input.project_ids === '' && receipt.input.page_size === 10;
      // A same-property/search/scope cache receipt is valid for immutable fresh facts.
      // Project discovery scans exact retained-window slices then hydrates PG
      // labels (dashboard.py). Validate one native chain, not its last empty page.
      let receipt: Receipt<ValueBody>;
      if (name === 'project') {
        await expect.poll(() => values.some(item => matches(item) && !item.input.cursor), { timeout: UI_READY }).toBe(true);
        const root = values.filter(item => matches(item) && !item.input.cursor).at(-1)!;
        receipt = root;
        const chain: Receipt<ValueBody>[] = [], options: ValueOption[] = [];
        const cursors = new Set<string>();
        const window = { start: root.body.result?.query_window_start, end: root.body.result?.query_window_end };
        try {
          while (true) {
            chain.push(receipt);
            expect(receipt.status).toBe(200);
            expect(receipt.input).not.toHaveProperty('attribute_type');
            const result = receipt.body.result;
            expect(result).toMatchObject({ query_complete: true, query_status: 'complete',
              query_window_start: window.start, query_window_end: window.end });
            expect(Number.isFinite(Date.parse(window.start!))).toBe(true);
            expect(Date.parse(window.end!)).toBeGreaterThan(Date.parse(window.start!));
            for (const option of result.values) {
              expect(options.some(previous => previous.value === option.value)).toBe(false);
              options.push(option);
            }
            if (!result.has_more) {
              expect(result).toMatchObject({ has_more: false, next_cursor: null, browse_status: 'exhausted' });
              break;
            }
            expect(result).toMatchObject({ has_more: true, browse_status: 'continuation' });
            const cursor = result.next_cursor;
            expect(typeof cursor).toBe('string'); expect(cursor!.length).toBeGreaterThan(0);
            expect(cursors.has(cursor!)).toBe(false); cursors.add(cursor!);
            // Existing visible widget sentinel advances the chain. No direct
            // API follower, invented Load more button or timeout override.
            await expect.poll(() => values.some(item => matches(item) && item.startedAt >= root.startedAt && item.input.cursor === cursor),
              { timeout: UI_READY }).toBe(true);
            receipt = values.find(item => matches(item) && item.startedAt >= root.startedAt && item.input.cursor === cursor)!;
          }
          expect(sortOptions(options)).toEqual(sortOptions(expected));
        } finally {
          await testInfo.attach('native-project-value-chain', { contentType: 'application/json', body: JSON.stringify({ chain, options, expected }) });
        }
      } else {
        await expect.poll(() => values.some(item => matches(item) && (item.status !== 200 || item.body.result?.query_complete === true)), { timeout: UI_READY }).toBe(true);
        receipt = values.filter(matches).at(-1)!;
        expect(receipt.status).toBe(200); expect(receipt.input).not.toHaveProperty('attribute_type');
        assertValues(receipt.body, expected);
      }
      await uiExpect(popup.getByRole('progressbar')).toHaveCount(0);
      const rows = popup.locator('p[title]').filter({ hasNotText: /^Specify: / });
      await uiExpect(rows).toHaveCount(expected.length);
      await expect.poll(async () => (await rows.evaluateAll(elements => elements.map(element => element.getAttribute('title')))).sort(),
        { timeout: UI_READY }).toEqual(expected.map(option => option.label).sort());
      return receipt;
    };
    const sessionRow = (label: string) => popup.locator(`p[title="${label}"]`).locator('..');
    const reopenValues = async () => { await uiExpect(popup).toHaveCount(0); await sessionValueControl.click(); };
    const assertEditorControls = async (label: string) => {
      await uiExpect(projectCard.getByRole('combobox')).toHaveText('Is');
      await uiExpect(projectCard.locator('.filter-value-name')).toHaveText(projectNames[0]);
      await uiExpect(sessionCard.getByRole('combobox')).toHaveText('Is');
      await uiExpect(sessionCard.locator('.filter-value-name')).toHaveText(label);
      for (const choice of choices) {
        const card = page.locator(`p[title="${choice.name}"]`).locator('../..');
        await uiExpect(card.getByText('Distinct Count', { exact: true })).toBeVisible();
        await uiExpect(card.getByText('Project', { exact: true })).toHaveCount(0);
        await uiExpect(card.getByText('Session', { exact: true })).toHaveCount(0);
      }
      await uiExpect(page.locator('.breakdown-section-title').locator('../..').getByText('Session', { exact: true })).toBeVisible();
      await uiExpect(page.getByRole('combobox').filter({ hasText: 'Day' })).toBeVisible();
      await uiExpect(page.getByText('7D', { exact: true })).toBeVisible();
    };
    const groupFor = (config: Config, vector: Vector) => config.breakdowns.length && vector[0] !== null
      ? config.filters[1].filter_config.filter_value[0] : 'total';
    const assertTable = async (config: Config, vector: Vector, buckets: number[], tile = false) => {
      const group = groupFor(config, vector);
      const columns: Record<string, (number | null)[]> = Object.fromEntries(choices.map((choice, index) => [
        `${choice.name}${group === 'total' ? '' : ` / ${group}`} (count_distinct)`,
        buckets.map(bucket => bucket === plantedDay ? vector[index] : null),
      ]));
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
    };
    const queryAfter = async (label: string, expected: Config, vector: Vector,
      action: () => Promise<unknown>, tile = false, reopenedProof = false) => {
      const since = queries.length, startedAt = Date.now();
      await action();
      const matches = (receipt: QueryReceipt) => receipt.startedAt >= startedAt && isDeepStrictEqual(receipt.input, expected) &&
        (receipt.status !== 200 || receipt.body.result?.query_complete === true);
      await expect.poll(() => queries.slice(since).some(matches), { timeout: UI_READY }).toBe(true);
      const receipt = queries.slice(since).find(matches)!;
      await testInfo.attach(`${label}-query`, { contentType: 'application/json', body: JSON.stringify(receipt) });
      expect(receipt.status).toBe(200); expect(receipt.scope).toEqual(expectedScope); expect(receipt.input).toEqual(expected);
      for (const metric of receipt.input.metrics) expect(metric).not.toHaveProperty('filters');
      for (const filter of receipt.input.filters) expect(filter.filter_config).not.toHaveProperty('attribute_value_types');
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
      const group = groupFor(expected, vector);
      for (const [index, metric] of result.metrics.entries()) {
        expect(metric).toMatchObject({ query_complete: true, query_exact: true });
        expect(metric.series.map(series => series.name)).toEqual([group]);
        const series = metric.series[0];
        expect(series.data.map(point => Date.parse(point.timestamp))).toEqual(buckets);
        expect(series.data.map(point => point.value)).toEqual(buckets.map(bucket => bucket === plantedDay ? vector[index] : null));
        if (reopenedProof && metric.id === 'span_count') {
          // Check3 changes ONLY this first reopened S1 assertion, after persistence.
          expect(series.data.find(point => Date.parse(point.timestamp) === plantedDay)!.value).toBe(EXPECTED_REOPENED_SPANS);
        }
      }
      lastBuckets = buckets;
      await assertTable(expected, vector, buckets, tile);
      await testInfo.attach(`${label}-table`, { contentType: 'image/png', body: await page.screenshot() });
      return receipt;
    };
    const saveWidget = async (label: string, expected: Config, vector: Vector) => {
      const previousId = savedWidget?.id, startedAt = Date.now();
      const path = previousId ? `${DASHBOARDS}${dashboardId}/widgets/${previousId}/` : `${DASHBOARDS}${dashboardId}/widgets/`;
      const [response] = await Promise.all([
        page.waitForResponse(item => new URL(item.url()).pathname === path && item.request().method() === (previousId ? 'PATCH' : 'POST'), { timeout: UI_READY }),
        page.getByRole('button', { name: 'Save', exact: true }).click(),
      ]);
      expect(response.status()).toBe(200); expect(scopeOf(response)).toEqual(expectedScope);
      expect(response.request().timing().startTime).toBeGreaterThanOrEqual(startedAt);
      const input = response.request().postDataJSON() as Omit<Widget, 'id'>;
      expect(input.name).toBe(widgetName); expect(input.chart_config.chart_type).toBe('table');
      expect(input.query_config).toEqual(expected);
      const saved = ((await response.json()) as { result: Widget }).result;
      if (previousId) expect(saved.id).toBe(previousId);
      expect(saved).toMatchObject({ name: widgetName, query_config: expected, chart_config: input.chart_config });
      expect(saved.query_config).toEqual(expected); expect(saved.chart_config).toEqual(input.chart_config);
      savedWidget = saved;
      const rows = await probe.pg('SELECT id, name, dashboard_id, query_config, chart_config FROM tracer_dashboardwidget WHERE dashboard_id = $1 AND NOT deleted', [dashboardId]);
      expect(rows).toEqual([{ id: saved.id, name: widgetName, dashboard_id: dashboardId, query_config: expected, chart_config: input.chart_config }]);
      expect(await probe.pg('SELECT id, name, workspace_id FROM tracer_dashboard WHERE id = $1 AND NOT deleted', [dashboardId]))
        .toEqual([{ id: dashboardId, name: dashboardName, workspace_id: actor.workspaceId }]);
      const detail = await actor.api.get<{ result: { id: string; name: string; workspace: string; widgets: Widget[] } }>(`${DASHBOARDS}${dashboardId}/`);
      expect(detail.result).toMatchObject({ id: dashboardId, name: dashboardName, workspace: actor.workspaceId });
      expect(detail.result.widgets.map(widget => ({ id: widget.id, name: widget.name, query_config: widget.query_config, chart_config: widget.chart_config })))
        .toEqual([{ id: saved.id, name: widgetName, query_config: expected, chart_config: input.chart_config }]);
      await Promise.all([actor, scopeActors.ownerB, emptyActor].map(async selectedActor => {
        const nestedPath = `${DASHBOARDS}${dashboardId}/widgets/${saved.id}/`;
        const scoped = await scopeActors.send<Widget>(selectedActor, 'GET', nestedPath);
        scopeResults.push({ path: nestedPath, organizationId: selectedActor.organizationId, workspaceId: selectedActor.workspaceId, ...scoped });
        expect(scoped.status).toBe(selectedActor === actor ? 200 : 404);
        if (selectedActor === actor) {
          expect(scoped.body).toMatchObject({ id: saved.id, name: widgetName });
          expect(scoped.body.query_config).toEqual(expected); expect(scoped.body.chart_config).toEqual(input.chart_config);
        }
      }));
      const receipt = { label, path, scope: scopeOf(response), input, saved, rows, detail,
        startedAt: response.request().timing().startTime, endedAt: Date.now() };
      saves.push(receipt);
      await testInfo.attach(`${label}-saved-widget`, { contentType: 'application/json', body: JSON.stringify(receipt) });
      await uiExpect(page).toHaveURL(new RegExp(`/dashboard/dashboards/${dashboardId}$`));
      // Native saved tile is checked independently; unchanged/cached rendering is
      // not forced to issue a new query. Reload/reopen below require fresh queries.
      await assertTable(expected, vector, lastBuckets, true);
    };
    const replaceSession = async (oldId: string, oldLabel: string, nextId: string, nextLabel: string) => {
      await reopenValues();
      await searchOptions('session', oldId, [{ value: oldId, label: oldLabel }]);
      const oldRow = sessionRow(oldLabel);
      await uiExpect(oldRow).toHaveCount(1); await uiExpect(oldRow.getByRole('checkbox')).toBeChecked();
      await oldRow.click(); await uiExpect(oldRow.getByRole('checkbox')).not.toBeChecked();
      await uiExpect(popup.getByRole('button', { name: 'Add', exact: true })).toBeDisabled();
      await searchOptions('session', nextId, nextId === miss ? [] : [{ value: nextId, label: nextLabel }]);
      const nextRow = nextId === miss ? popup.locator(`[data-widget-filter-exact-value="${miss}"]`) : sessionRow(nextLabel);
      await uiExpect(nextRow).toHaveCount(1); await uiExpect(nextRow.getByRole('checkbox')).not.toBeChecked();
      await nextRow.click(); await uiExpect(nextRow.getByRole('checkbox')).toBeChecked();
      await popup.getByRole('button', { name: 'Add', exact: true }).click();
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
        await page.getByPlaceholder('Untitled widget').fill(widgetName); await page.getByPlaceholder('Untitled widget').press('Enter');
        await page.getByRole('combobox').filter({ hasText: 'Line' }).click();
        await page.getByRole('option', { name: 'Table', exact: true }).click();
        await page.getByText('7D', { exact: true }).click();
        await page.getByRole('combobox').filter({ hasText: 'Day' }).click();
        await page.getByRole('option', { name: 'Day', exact: true }).click();
      }, { timeout: UI_READY });

      await test.step('2 discover Sessions, Traces and Spans and select distinct counts', async () => {
        for (const [index, choice] of choices.entries()) {
          if (index) await page.locator('.metric-section-title').click(); else await page.getByText('Select Metric', { exact: true }).click();
          await selectTraces(); await page.getByPlaceholder('Search metrics...').fill(choice.name);
          await assertCatalog(properties[index], true);
          await page.getByRole('button', { name: `${choice.name} (number, Traces)`, exact: true }).click();
          await page.locator(`p[title="${choice.name}"]`).locator('../..').locator('.MuiChip-clickable').click();
          await page.getByText('Distinct Count', { exact: true }).last().click();
        }
        // Static definitions remain valid even in the genuinely empty workspace.
        await Promise.all([actor, scopeActors.ownerB, emptyActor].map(async selectedActor => {
          for (const property of properties) {
            const input = { cursor_mode: true, category: 'system_metric', source: 'traces', search: property.display_name,
              page_size: 25, ...(property.role === 'metric' ? { role: 'metric' } : {}) };
            const response = await scopeActors.send<CatalogBody>(selectedActor, 'POST', METRICS, input);
            scopeResults.push({ path: METRICS, organizationId: selectedActor.organizationId, workspaceId: selectedActor.workspaceId, input, ...response });
            expect(response.status).toBe(200);
            expect(response.body.result).toMatchObject({ ...catalogMetadata, has_more: false, next_cursor: null });
            const selected = response.body.result.metrics.filter(row => row.property_id === property.property_id);
            expect(selected).toHaveLength(1); expect(selected[0]).toMatchObject(property);
          }
        }));
      }, { timeout: UI_READY });

      await test.step('3 select primary Project and verify counts and scoped Session values', async () => {
        await queryAfter('project-only', configFor(), [2, 3, 6], async () => {
          await page.locator('.filter-section-title').click(); await selectTraces();
          await page.getByPlaceholder('Search filter attributes...').fill('Project'); await assertCatalog(properties[3], false);
          await page.getByRole('button', { name: 'Project (string, Traces)', exact: true }).click();
          // Captured DASH006 Project journey explicitly opens this control.
          await projectCard.getByText('Select value...', { exact: true }).click();
          await searchOptions('project', projectNames[0], [{ value: projectIds[0], label: projectNames[0] }]);
          const row = sessionRow(projectNames[0]); await uiExpect(row.getByRole('checkbox')).not.toBeChecked();
          await row.click(); await uiExpect(row.getByRole('checkbox')).toBeChecked();
          await popup.getByRole('button', { name: 'Add', exact: true }).click();
        });
        const reads = [
          { actor, project: '', options: [{ value: primaryS1, label: S1 }, { value: primaryS2, label: S2 }, { value: siblingS1, label: S1 }] },
          { actor, project: projectIds[0], options: [{ value: primaryS1, label: S1 }, { value: primaryS2, label: S2 }] },
          { actor, project: projectIds[1], options: [{ value: siblingS1, label: S1 }] },
          { actor: scopeActors.ownerB, project: '', options: [{ value: foreignS1, label: S1 }] },
          { actor: emptyActor, project: '', options: [] },
        ];
        await Promise.all(reads.map(async read => {
          const input = { property_id: SESSION_PROPERTY, metric_name: 'session', metric_type: 'system_metric',
            source: 'traces', project_ids: read.project, page_size: 10 };
          const response = await scopeActors.send<ValueBody>(read.actor, 'POST', VALUES, input);
          scopeResults.push({ path: VALUES, organizationId: read.actor.organizationId, workspaceId: read.actor.workspaceId, input, ...response });
          expect(response.status).toBe(200); assertValues(response.body, read.options);
        }));
      }, { timeout: UI_READY });

      await test.step('4 select primary Session by UUID despite sibling label collision and group it', async () => {
        await queryAfter('S1-selected', configFor(primaryS1), [1, 2, 4], async () => {
          await page.locator('.filter-section-title').click(); await selectTraces();
          await page.getByPlaceholder('Search filter attributes...').fill('Session'); await assertCatalog(properties[4], false);
          await page.getByRole('button', { name: 'Session (string, Traces)', exact: true }).click();
          await uiExpect(sessionCard.getByRole('combobox')).toHaveText('Is');
          // Source getWidgetFilterDefaults(string) -> pendingFilterOpen effect.
          // Await auto-open, no speculative second trigger or fallback branch.
          await searchOptions('session', S1, [{ value: primaryS1, label: S1 }, { value: siblingS1, label: S1 }]);
          await uiExpect(sessionRow(S1)).toHaveCount(2); // Do not choose either collision by order.
          const selected = await searchOptions('session', primaryS1, [{ value: primaryS1, label: S1 }]);
          // Check1: change ONLY this label to `${S1}-wrong`; seed/search/matchers stay canonical.
          expect(selected.body.result.values).toEqual([{ value: primaryS1, label: S1 }]);
          const row = sessionRow(S1); await uiExpect(row).toHaveCount(1);
          await uiExpect(row.getByRole('checkbox')).not.toBeChecked(); await row.click();
          await uiExpect(row.getByRole('checkbox')).toBeChecked();
          await popup.getByRole('button', { name: 'Add', exact: true }).click();
        });
        await queryAfter('S1-grouped', canonicalS1, [1, 2, 4], async () => {
          await page.locator('.breakdown-section-title').click(); await selectTraces();
          await page.getByPlaceholder('Search breakdown attributes...').fill('Session'); await assertCatalog(properties[4], false);
          await page.getByRole('button', { name: 'Session (string, Traces)', exact: true }).click();
        });
        await assertEditorControls(S1);
      }, { timeout: UI_READY });

      await test.step('5 save S1 and verify the persisted binding and saved table', async () => {
        await saveWidget('S1', canonicalS1, [1, 2, 4]);
      }, { timeout: UI_READY });

      await test.step('6 reload and reopen S1 with exact controls and checked ID/display pair', async () => {
        expect(savedWidget!.query_config).toEqual(canonicalS1);
        await queryAfter('S1-reload', canonicalS1, [1, 2, 4], () => page.reload({ waitUntil: 'domcontentloaded' }), true);
        await queryAfter('S1-reopen', canonicalS1, [1, 2, 4], () => page.locator(`[data-widget-id="${savedWidget!.id}"]`)
          .getByText(widgetName, { exact: true }).click(), false, true);
        await assertEditorControls(S1); await reopenValues();
        await searchOptions('session', primaryS1, [{ value: primaryS1, label: S1 }]);
        await uiExpect(sessionRow(S1).getByRole('checkbox')).toBeChecked();
        await testInfo.attach('reopened-S1-checked', { contentType: 'image/png', body: await page.screenshot() });
        // Unchanged Add closes; do not require a new request for unchanged config.
        await popup.getByRole('button', { name: 'Add', exact: true }).click(); await uiExpect(popup).toHaveCount(0);
      }, { timeout: UI_READY });

      await test.step('7 replace S1 with S2, verify 1/1/2 and save the same widget', async () => {
        await queryAfter('S2-selected', canonicalS2, [1, 1, 2], () => replaceSession(primaryS1, S1, primaryS2, S2));
        await assertEditorControls(S2); await saveWidget('S2', canonicalS2, [1, 1, 2]);
      }, { timeout: UI_READY });

      await test.step('8 reload and reopen S2 then Specify a disjoint UUID', async () => {
        expect(savedWidget!.query_config).toEqual(canonicalS2);
        await queryAfter('S2-reload', canonicalS2, [1, 1, 2], () => page.reload({ waitUntil: 'domcontentloaded' }), true);
        await queryAfter('S2-reopen', canonicalS2, [1, 1, 2], () => page.locator(`[data-widget-id="${savedWidget!.id}"]`)
          .getByText(widgetName, { exact: true }).click());
        await assertEditorControls(S2);
        await queryAfter('disjoint', configFor(miss, true), [null, null, null], () => replaceSession(primaryS2, S2, miss, miss));
      }, { timeout: UI_READY });

      await test.step('9 restore and save primary S1 with unchanged source versions and tenant scope', async () => {
        await queryAfter('restored-S1', canonicalS1, [1, 2, 4], async () => {
          await reopenValues(); await searchOptions('session', miss, []);
          const specify = popup.locator(`[data-widget-filter-exact-value="${miss}"]`);
          await uiExpect(specify.getByRole('checkbox')).toBeChecked(); await specify.click();
          await uiExpect(specify.getByRole('checkbox')).not.toBeChecked();
          await uiExpect(popup.getByRole('button', { name: 'Add', exact: true })).toBeDisabled();
          await searchOptions('session', primaryS1, [{ value: primaryS1, label: S1 }]);
          const row = sessionRow(S1); await uiExpect(row.getByRole('checkbox')).not.toBeChecked();
          await row.click(); await uiExpect(row.getByRole('checkbox')).toBeChecked();
          await popup.getByRole('button', { name: 'Add', exact: true }).click();
        });
        await assertEditorControls(S1); await saveWidget('restored-S1', canonicalS1, [1, 2, 4]);
        const [finalSources, finalSessions, finalTraces, finalRemaps, finalOverlays, finalProjects] = await Promise.all([
          probe.ch<Span>(sourceSql, projectParams), probe.ch<Session>(sessionSql, projectParams), probe.ch<Trace>(traceSql, projectParams),
          probe.ch(remapSql, ids), probe.pg(overlaySql, [projectIds, sessionIds]),
          probe.pg<Project>('SELECT id, name, organization_id, workspace_id FROM tracer_project WHERE name = ANY($1) AND NOT deleted', [projectNames]),
        ]);
        await testInfo.attach('unchanged-source-and-versions', { contentType: 'application/json', body: JSON.stringify({
          initialSources, finalSources, initialSessions, finalSessions, initialTraces, finalTraces,
          initialRemaps, finalRemaps, initialOverlays, finalOverlays, projects, finalProjects,
        }) });
        expect(finalSources).toEqual(initialSources); expect(finalSessions).toEqual(initialSessions);
        expect(finalTraces).toEqual(initialTraces); expect(finalRemaps).toEqual(initialRemaps); expect(finalOverlays).toEqual(initialOverlays);
        expect([...finalProjects].sort((left, right) => left.id.localeCompare(right.id)))
          .toEqual([...projects].sort((left, right) => left.id.localeCompare(right.id)));
        expect(savedWidget!.query_config).toEqual(canonicalS1);
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
