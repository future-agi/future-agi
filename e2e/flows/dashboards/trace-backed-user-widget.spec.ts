import { createHash, randomBytes, randomUUID } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { request, type Response } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { sendTrace } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// useDashboards.js / tracer/views/dashboard.py: native read aliases, wrapped
// create/update/dashboard GET, and RAW inherited nested-widget GET.
const DASHBOARDS = '/tracer/dashboard/';
const METRICS = `${DASHBOARDS}metrics/`, VALUES = `${DASHBOARDS}filter_values/`, QUERY = `${DASHBOARDS}query/`;
const UI_READY = 60_000; // README: one wall for each whole approved UI stage.
const DAY = 86_400_000;
// Assertion-only anchors. Never used by inputs, UUID derivation or request matchers.
const EXPECTED_USER_TYPE_WIRE = 'email'; // Check1 -> 'phone', stage4 emitted type filter.
const EXPECTED_A_CHILD_TYPE = 'email'; // Check2 -> 'phone', post-poll stored A child.
const EXPECTED_REOPENED_SPANS = 4; // Check3 -> 5, stage6 reopened U1 only.
// fi-collector/pkg/detid/detid.go; same independent RFC-4122 oracle as OBS007.
const USER_NS = '97daafcc-ae7b-5a44-a76b-c85e63059e1c';
const SESSION_NS = '1c4977df-2af9-5330-b34f-7969ffdabf25';
const uuid5 = (namespace: string, key: string): string => {
  const bytes = createHash('sha1').update(Buffer.from(namespace.replaceAll('-', ''), 'hex')).update(key).digest().subarray(0, 16);
  bytes[6] = (bytes[6] & 15) | 80; bytes[8] = (bytes[8] & 63) | 128;
  const hex = bytes.toString('hex');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};

type Filter = { column_id: string; property_id: string; display_name: string; source: string; output_type: string;
  filter_config: { filter_type: string; filter_op: string; filter_value: string[]; col_type: string } };
type Metric = { id: string; name: string; property_id: string; display_name: string; type: string; source: string; aggregation: string };
type Config = { project_ids: string[]; time_range: { preset: string }; granularity: string;
  metrics: Metric[]; filters: Filter[]; breakdowns: Record<string, string>[] };
type Vector = [number | null, number | null, number | null];
type Groups = Record<string, Vector>;
type Result = { query_complete: boolean; query_exact: boolean; granularity: string;
  time_range: { start: string; end: string }; metrics: { id: string; name: string; unit: string; aggregation: string;
    query_complete: boolean; query_exact: boolean; series: { name: string; data: { timestamp: string; value: number | null }[] }[] }[] };
type Property = { property_id: string; property_kind: string; name: string; display_name: string;
  category: string; source: string; type: string; output_type: string; role: string };
type CatalogBody = { result: { metrics: Property[]; has_more: boolean; next_cursor: string | null;
  query_complete: boolean; query_exact: boolean; query_status: string; query_provenance: string } };
type ValueOption = { value: string; label: string; type?: string };
type ValueBody = { result: { values: ValueOption[]; query_complete: boolean; query_status: string;
  has_more: boolean; next_cursor: string | null; browse_status: string;
  query_window_start?: string; query_window_end?: string } };
type Scope = { organizationId: string; workspaceId: string };
type Receipt<T> = { input: Record<string, unknown>; body?: T; status: number; method: string; path: string;
  startedAt: number; endedAt: number; scope: Scope; contentType: string; settled: boolean; parseError?: string };
type QueryReceipt = Omit<Receipt<{ result: Result }>, 'input'> & { input: Config };
type Project = { id: string; name: string; organization_id: string; workspace_id: string; deleted: boolean };
type Span = { id: string; user_type: string; version: string; [key: string]: unknown };
type EndUser = { end_user_id: string; user_id: string; user_id_type: string; metadata: string;
  first_seen: string; version: string; [key: string]: unknown };
type Session = { trace_session_id: string; first_seen: string; version: string; [key: string]: unknown };
type Trace = { id: string; version: string; updated_at: string; [key: string]: unknown };
type Widget = { id: string; name: string; query_config: Config; chart_config: { chart_type: string; [key: string]: unknown } };

test('DASH-E2E-008: a saved User widget keeps project and identifier-type scope', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DASH-E2E-008', area: 'dashboards',
    userGoal: 'A saved User widget keeps project and identifier-type scope.',
    steps: [
      'create and name a Table widget with 7D and Day after ingesting five independently identified user traces',
      'discover Users, Traces and Spans and explicitly select Distinct Count with typed scoped catalog checks',
      'select primary Project and User breakdown, verify both groups and positive sibling and foreign controls',
      'select native User U1 and email type, verify the phone intersection is empty, and restore email',
      'save U1 and email and verify the exact native persisted binding and table',
      'reload and reopen U1 and email with exact controls and the independent 1/2/4 result',
      'replace User with U2, prove email is empty, select phone and save the same widget with 1/1/2',
      'reload and reopen U2 and phone with exact selected controls, stored configuration and 1/1/2',
      'restore and save U1 and email, verify the intermediate empty intersection and unchanged scoped source versions',
    ],
    backendChecks: [
      "Native catalog and selected property identities preserve this flow's source, types, choices and actor scope.",
      "This flow's exact publicly produced source identities and typed latest facts are present and unchanged outside its authorized UI actions.",
      "The preview, saved binding and reopened widget equal this flow's independently specified filtered and grouped result.",
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe: probe }, testInfo) => {
  test.setTimeout(660_000); // Master U/R9: ASYNC_JOB60 + 2×SPAN_VISIBLE15 + 9×UI_READY60 + 30 headroom.
  const uiExpect = expect.configure({ timeout: UI_READY });
  // Approved observed-catalog-dash8-plan-20260910.md. Source authoring is not
  // image/runtime proof. No native users adapter or provider execution is claimed.
  const prefix = `e2e-dash8-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const U1 = `${prefix}-u1@example.test`, U2 = `${prefix}-u2-phone`;
  const S1 = `${prefix}-S1`, S2 = `${prefix}-S2`, foreignKey = `${prefix}.foreign-only`;
  const projectNames = [`${prefix}-primary`, `${prefix}-sibling`, `${prefix}-foreign`];
  const dashboardName = `${prefix}-dashboard`, widgetName = `${prefix}-widget`;
  const actor = scopeActors.ownerA, emptyActor = scopeActors.withWorkspace(actor, scopeActors.emptyWorkspace.id);
  const plantedDay = Math.floor(Date.now() / DAY) * DAY - DAY;
  const start = BigInt(plantedDay + DAY / 2) * 1_000_000n;
  const seeds = [
    { key: 'A', project: 0, user: U1, userType: 'email', session: S1, ms: 50 },
    { key: 'B', project: 0, user: U1, userType: 'email', session: S1, ms: 100 },
    { key: 'C', project: 0, user: U2, userType: 'phone', session: S2, ms: 200 },
    { key: 'S', project: 1, user: U1, userType: 'email', session: S1, ms: 900 },
    { key: 'F', project: 2, user: U1, userType: 'email', session: S1, ms: 900 },
  ].map(seed => ({ ...seed, traceId: randomUUID(), spanIds: [randomBytes(8).toString('hex'), randomBytes(8).toString('hex')] }));
  expect(new Set(seeds.map(seed => seed.traceId)).size).toBe(5);
  expect(new Set(seeds.flatMap(seed => seed.spanIds)).size).toBe(10);
  for (const id of seeds.flatMap(seed => seed.spanIds)) {
    expect(id).toMatch(/^[0-9a-f]{16}$/); expect(id).not.toBe('0000000000000000');
  }
  await testInfo.attach('planted-identities', { contentType: 'application/json', body: JSON.stringify({
    seeds, projectNames, U1, U2, S1, S2, foreignKey, start: String(start), actors: scopeActors.evidence().actors,
  }) });
  const ingestion = await request.newContext();
  try {
    for (const seed of seeds) {
      const owner = seed.project === 2 ? scopeActors.ownerB : actor;
      const keys = scopeActors.owners.find(item => item.organizationId === owner.organizationId && item.workspaceId === owner.workspaceId)!;
      // otlp.ts + converter.newSpanIdentity: observe resource gates EndUsers;
      // both spans receive the real identity attributes, not a fabricated trace FK.
      const attributes = { 'user.id': seed.user, 'user.id.type': seed.userType, 'session.id': seed.session,
        'gen_ai.cost.total': 0, ...(seed.project === 2 ? { [foreignKey]: 'foreign' } : {}) };
      expect(await sendTrace(ingestion, { collectorUrl: E2E.collectorUrl, apiKey: keys.apiKey, secretKey: keys.secretKey,
        projectName: projectNames[seed.project], traceId: seed.traceId, rootSpanId: seed.spanIds[0], childSpanId: seed.spanIds[1],
        rootName: `${prefix}-${seed.key}-root`, childName: `${prefix}-${seed.key}-child`,
        startTimeUnixNano: start, endTimeUnixNano: start + BigInt(seed.ms) * 1_000_000n,
        resourceAttributes: { project_type: 'observe' }, rootAttributes: { ...attributes, 'fi.span.kind': 'chain' },
        childAttributes: { ...attributes, 'fi.span.kind': 'llm' } }))
        .toEqual({ traceId: seed.traceId, spanIds: seed.spanIds, projectName: projectNames[seed.project] });
    }
  } finally { await ingestion.dispose(); }

  const projectSql = 'SELECT id, name, organization_id, workspace_id, deleted FROM tracer_project WHERE name = ANY($1) ORDER BY name';
  let projects: Project[] = [];
  await expect.poll(async () => {
    projects = await probe.pg<Project>(projectSql, [projectNames]);
    return projects.map(({ id, ...row }) => row);
  }, POLL.ASYNC_JOB).toEqual(projectNames.map((name, index) => ({ name, deleted: false,
    organization_id: index === 2 ? scopeActors.ownerB.organizationId : actor.organizationId,
    workspace_id: index === 2 ? scopeActors.ownerB.workspaceId : actor.workspaceId })).sort((a, b) => a.name.localeCompare(b.name)));
  const projectIds = projectNames.map(name => projects.find(project => project.name === name)!.id);
  expect(new Set(projectIds).size).toBe(3);
  const projectParams = { primary: projectIds[0], sibling: projectIds[1], foreign: projectIds[2] };
  // Independent UUID oracle uses only publicly resolved, PG-verified project scope
  // and planted labels/types. No CH/API result value is used to invent an expected ID.
  const userIds = [[0, U1, 'email'], [0, U2, 'phone'], [1, U1, 'email'], [2, U1, 'email']].map(([index, user, type]) => {
    const project = Number(index), org = project === 2 ? scopeActors.ownerB.organizationId : actor.organizationId;
    return { project, user: String(user), type: String(type), org,
      id: uuid5(USER_NS, `${projectIds[project]}|${org}|${user}|${type}`) };
  });
  const sessionIds = [[0, S1], [0, S2], [1, S1], [2, S1]].map(([index, label]) => ({
    project: Number(index), label: String(label), id: uuid5(SESSION_NS, `${projectIds[Number(index)]}|${label}`),
  }));
  expect(new Set(userIds.map(user => user.id)).size).toBe(4);
  expect(new Set(sessionIds.map(session => session.id)).size).toBe(4);
  await testInfo.attach('independent-typed-uuids', { contentType: 'application/json', body: JSON.stringify({ projects, userIds, sessionIds }) });
  // CH25 schemas 002/015/017/018: scalar Map presence, nullable UUID FKs, typed
  // curated rows and versions. All reads are confined to the three owned projects.
  const sourceSql = `SELECT id, trace_id, project_id, org_id, parent_span_id, observation_type, name, cost,
    end_user_id, trace_session_id, attrs_string['user.id'] AS user_label, attrs_string['user.id.type'] AS user_type,
    attrs_string['session.id'] AS session_label,
    mapContains(attrs_string, 'user.id') AS has_user, mapContains(attrs_number, 'user.id') AS user_number,
    mapContains(attrs_bool, 'user.id') AS user_bool, mapContains(attrs_string, 'user.id.type') AS has_type,
    mapContains(attrs_number, 'user.id.type') AS type_number, mapContains(attrs_bool, 'user.id.type') AS type_bool,
    mapContains(attrs_string, 'session.id') AS has_session, mapContains(attrs_number, 'session.id') AS session_number,
    mapContains(attrs_bool, 'session.id') AS session_bool, attrs_string[{foreignKey:String}] AS foreign_value,
    mapContains(attrs_string, {foreignKey:String}) AS has_foreign, mapContains(attrs_number, {foreignKey:String}) AS foreign_number,
    mapContains(attrs_bool, {foreignKey:String}) AS foreign_bool,
    toUnixTimestamp64Micro(start_time) AS start_us, toUnixTimestamp64Micro(end_time) AS end_us,
    toString(_version) AS version, is_deleted FROM spans FINAL
    WHERE project_id IN ({primary:UUID}, {sibling:UUID}, {foreign:UUID}) ORDER BY id`;
  const sourceParams = { ...projectParams, foreignKey };
  const userSql = `SELECT project_id, end_user_id, organization_id, user_id, user_id_type, user_id_hash, metadata,
    toString(first_seen) AS first_seen, toString(version) AS version, is_deleted FROM end_users FINAL
    WHERE project_id IN ({primary:UUID}, {sibling:UUID}, {foreign:UUID}) ORDER BY toString(end_user_id)`;
  const sessionSql = `SELECT project_id, trace_session_id, external_session_id, toString(first_seen) AS first_seen,
    toString(version) AS version, is_deleted FROM trace_sessions FINAL
    WHERE project_id IN ({primary:UUID}, {sibling:UUID}, {foreign:UUID}) ORDER BY toString(trace_session_id)`;
  const expectedUsers = userIds.map(user => ({ project_id: projectIds[user.project], end_user_id: user.id,
    organization_id: user.org, user_id: user.user, user_id_type: user.type, user_id_hash: '', metadata: '{}', is_deleted: 0 }))
    .sort((a, b) => a.end_user_id.localeCompare(b.end_user_id));
  const expectedSessions = sessionIds.map(session => ({ project_id: projectIds[session.project], trace_session_id: session.id,
    external_session_id: session.label, is_deleted: 0 })).sort((a, b) => a.trace_session_id.localeCompare(b.trace_session_id));
  const expectedSources = seeds.flatMap(seed => seed.spanIds.map((id, index) => ({ id, trace_id: seed.traceId,
    project_id: projectIds[seed.project], org_id: seed.project === 2 ? scopeActors.ownerB.organizationId : actor.organizationId,
    parent_span_id: index ? seed.spanIds[0] : '', observation_type: index ? 'llm' : 'chain',
    name: `${prefix}-${seed.key}-${index ? 'child' : 'root'}`, cost: 0,
    end_user_id: userIds.find(user => user.project === seed.project && user.user === seed.user && user.type === seed.userType)!.id,
    trace_session_id: sessionIds.find(session => session.project === seed.project && session.label === seed.session)!.id,
    user_label: seed.user, user_type: seed.userType, session_label: seed.session,
    has_user: 1, user_number: 0, user_bool: 0, has_type: 1, type_number: 0, type_bool: 0,
    has_session: 1, session_number: 0, session_bool: 0, foreign_value: seed.project === 2 ? 'foreign' : '',
    has_foreign: seed.project === 2 ? 1 : 0, foreign_number: 0, foreign_bool: 0,
    start_us: String(start / 1000n), end_us: String(start / 1000n + BigInt(seed.ms) * 1000n), is_deleted: 0,
  }))).sort((a, b) => a.id.localeCompare(b.id));
  let initialSources: Span[] = [], initialUsers: EndUser[] = [], initialSessions: Session[] = [];
  try {
    await expect.poll(async () => {
      [initialSources, initialUsers, initialSessions] = await Promise.all([
        probe.ch<Span>(sourceSql, sourceParams), probe.ch<EndUser>(userSql, projectParams), probe.ch<Session>(sessionSql, projectParams),
      ]);
      return { spans: initialSources.map(({ version, ...row }) => row),
        users: initialUsers.map(({ first_seen, version, ...row }) => row),
        sessions: initialSessions.map(({ first_seen, version, ...row }) => row) };
    }, POLL.SPAN_VISIBLE).toEqual({ spans: expectedSources, users: expectedUsers, sessions: expectedSessions });
  } finally {
    await testInfo.attach('source-identities', { contentType: 'application/json', body: JSON.stringify({
      expectedSources, expectedUsers, expectedSessions, initialSources, initialUsers, initialSessions,
    }) });
  }
  for (const row of initialSources) expect(BigInt(row.version)).toBeGreaterThan(0n);
  for (const row of [...initialUsers, ...initialSessions]) {
    expect(row.first_seen).toMatch(/^\d{4}-\d{2}-\d{2} /); expect(row.version).toMatch(/^\d{4}-\d{2}-\d{2} /);
  }
  for (const user of initialUsers) expect(JSON.parse(user.metadata)).toEqual({});
  // Check2 remains independent of the readiness oracle and the producer input.
  expect(initialSources.find(row => row.id === seeds[0].spanIds[1])!.user_type,
    `Check2: A child ${seeds[0].spanIds[1]} stored user.id.type`).toBe(EXPECTED_A_CHILD_TYPE);
  const schema = await probe.ch(`SELECT table, name, type FROM system.columns WHERE database={db:String}
    AND ((table='end_users' AND name IN ('end_user_id','user_id','user_id_type'))
      OR (table='spans' AND name IN ('trace_id','end_user_id','trace_session_id')) OR (table='traces' AND name='id')) ORDER BY table, name`, { db: E2E.chDatabase });
  await testInfo.attach('typed-identity-schema', { contentType: 'application/json', body: JSON.stringify(schema) });
  expect(schema).toEqual([
    { table: 'end_users', name: 'end_user_id', type: 'UUID' }, { table: 'end_users', name: 'user_id', type: 'String' },
    { table: 'end_users', name: 'user_id_type', type: 'LowCardinality(Nullable(String))' },
    { table: 'spans', name: 'end_user_id', type: 'Nullable(UUID)' }, { table: 'spans', name: 'trace_id', type: 'String' },
    { table: 'spans', name: 'trace_session_id', type: 'Nullable(UUID)' }, { table: 'traces', name: 'id', type: 'UUID' },
  ]);
  const traceSql = `SELECT id, project_id, name, session_id, toUnixTimestamp64Micro(created_at) AS created_us,
    toString(updated_at) AS updated_at, toString(_version) AS version, is_deleted FROM traces FINAL
    WHERE project_id IN ({primary:UUID}, {sibling:UUID}, {foreign:UUID}) ORDER BY toString(id)`;
  const expectedTraces = seeds.map(seed => ({ id: seed.traceId, project_id: projectIds[seed.project], name: `${prefix}-${seed.key}-root`,
    session_id: sessionIds.find(session => session.project === seed.project && session.label === seed.session)!.id,
    created_us: String(start / 1000n), is_deleted: 0 })).sort((a, b) => a.id.localeCompare(b.id));
  let initialTraces: Trace[] = [];
  try {
    await expect.poll(async () => {
      initialTraces = await probe.ch<Trace>(traceSql, projectParams);
      return initialTraces.map(({ version, updated_at, ...row }) => row);
    }, POLL.SPAN_VISIBLE).toEqual(expectedTraces);
  } finally {
    await testInfo.attach('curated-traces', { contentType: 'application/json', body: JSON.stringify({ expectedTraces, initialTraces }) });
  }
  for (const row of initialTraces) expect(BigInt(row.version)).toBeGreaterThan(0n);
  // 019_id_remap: fresh identities have no historical aliases. Inspect only
  // these independently computed IDs; never populate/refresh a remap or dictionary.
  const remapParams = { users: JSON.stringify(userIds.map(user => user.id)), sessions: JSON.stringify(sessionIds.map(session => session.id)) };
  const remapSql = `SELECT 'end_user' AS kind, old_id, new_id, toString(version) AS version FROM end_user_id_remap FINAL
    WHERE old_id IN (SELECT arrayJoin(JSONExtract({users:String}, 'Array(UUID)')))
       OR new_id IN (SELECT arrayJoin(JSONExtract({users:String}, 'Array(UUID)')))
    UNION ALL SELECT 'session' AS kind, old_id, new_id, toString(version) AS version FROM trace_session_id_remap FINAL
    WHERE old_id IN (SELECT arrayJoin(JSONExtract({sessions:String}, 'Array(UUID)')))
       OR new_id IN (SELECT arrayJoin(JSONExtract({sessions:String}, 'Array(UUID)')))`;
  const initialRemaps = await probe.ch(remapSql, remapParams);
  expect(initialRemaps).toEqual([]);

  // source_adapters.py: native display label Users, but canonical name user_count.
  const choices = [{ id: 'user_count', name: 'Users' }, { id: 'trace_count', name: 'Traces' }, { id: 'span_count', name: 'Spans' }];
  const properties: Property[] = [...choices.map(choice => ({ id: choice.id, label: choice.name, type: 'number', role: 'metric' })),
    { id: 'project', label: 'Project', type: 'string', role: 'dimension' },
    { id: 'user', label: 'User', type: 'string', role: 'dimension' },
    { id: 'user_id_type', label: 'User ID Type', type: 'string', role: 'dimension' }].map(item => ({
    property_id: `system_attribute:traces:${item.id}`, property_kind: 'system_attribute', category: 'system_metric',
    name: item.id, display_name: item.label, source: 'traces', type: item.type, output_type: item.type, role: item.role,
  }));
  const metrics: Metric[] = choices.map(choice => ({ id: choice.id, name: choice.id, property_id: `system_attribute:traces:${choice.id}`,
    display_name: choice.name, type: 'system_metric', source: 'traces', aggregation: 'count_distinct' }));
  // WidgetEditorView buildQueryConfig/buildWidgetFilterConfig: Is -> text/in;
  // Project is UUID, User is EXTERNAL text, User ID Type is its normalized token.
  const configFor = (project: number, user?: string, userType?: string, grouped = true): Config => ({
    project_ids: [], time_range: { preset: '7D' }, granularity: 'day', metrics,
    filters: [{ id: 'project', label: 'Project', value: projectIds[project] },
      ...(user === undefined ? [] : [{ id: 'user', label: 'User', value: user }]),
      ...(userType === undefined ? [] : [{ id: 'user_id_type', label: 'User ID Type', value: userType }])].map(item => ({
      column_id: item.id, property_id: `system_attribute:traces:${item.id}`, display_name: item.label, source: 'traces', output_type: 'string',
      filter_config: { filter_type: 'text', filter_op: 'in', filter_value: [item.value], col_type: 'SYSTEM_METRIC' },
    })),
    breakdowns: grouped ? [{ name: 'user', property_id: 'system_attribute:traces:user', display_name: 'User', type: 'system_metric', source: 'traces' }] : [],
  });
  const canonicalU1 = configFor(0, U1, 'email'), canonicalU2 = configFor(0, U2, 'phone');
  // Independent literal result matrix, not computed from a returned count/group.
  const u1Groups: Groups = { [U1]: [1, 2, 4] }, u2Groups: Groups = { [U2]: [1, 1, 2] };
  const bothGroups: Groups = { [U1]: [1, 2, 4], [U2]: [1, 1, 2] }, emptyGroups: Groups = { total: [null, null, null] };
  const catalogMetadata = { query_complete: true, query_exact: false, query_status: 'complete', query_provenance: 'current_property_catalog' };
  const catalogs: Receipt<CatalogBody>[] = [], values: Receipt<ValueBody>[] = [], queries: QueryReceipt[] = [];
  const scopeResults: unknown[] = [], saves: unknown[] = [], pending = new Set<Promise<void>>();
  const context = await scopeActors.openContext(browser, actor);
  try {
    const page = await context.newPage(); page.setDefaultTimeout(UI_READY);
    const browserTimeZone = await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone);
    const scopeOf = (response: Response): Scope => ({ organizationId: response.request().headers()['x-organization-id'],
      workspaceId: response.request().headers()['x-workspace-id'] }); // Deliberately excludes credentials.
    const expectedScope = { organizationId: actor.organizationId, workspaceId: actor.workspaceId };
    const captureResponse = (response: Response) => {
      const path = new URL(response.url()).pathname, method = response.request().method();
      const nativeRead = [METRICS, VALUES, QUERY].includes(path) && method === 'POST';
      const nativeWrite = ['POST', 'PATCH', 'PUT'].includes(method) && (path === DASHBOARDS ||
        /^\/tracer\/dashboard\/[0-9a-f-]+\/(?:widgets\/(?:[0-9a-f-]+\/)?)?$/.test(path));
      if (!nativeRead && !nativeWrite) return;
      const receipt: Receipt<unknown> = { input: response.request().postDataJSON(), status: response.status(), path,
        method, startedAt: response.request().timing().startTime, endedAt: Date.now(),
        scope: scopeOf(response), contentType: response.headers()['content-type'] || '', settled: false };
      // Store HTTP head before JSON. A malformed/non-JSON body is a retained
      // parse failure, never an empty successful result or an unhandled rejection.
      if (path === QUERY) queries.push(receipt as QueryReceipt);
      else if (path === METRICS) catalogs.push(receipt as Receipt<CatalogBody>);
      else if (path === VALUES) values.push(receipt as Receipt<ValueBody>);
      else saves.push(receipt);
      const capture = (async () => {
        try { receipt.body = await response.json(); }
        catch (error) { receipt.parseError = error instanceof Error ? error.name : 'UnknownError'; }
        finally { receipt.endedAt = Date.now(); receipt.settled = true; }
      })();
      pending.add(capture); void capture.finally(() => pending.delete(capture));
    };
    page.on('response', captureResponse);
    let dashboardId = '', savedWidget: Widget | undefined, originalChart: Widget['chart_config'] | undefined;
    let lastBuckets: number[] = [];
    const globalSection = page.locator('.filter-section-title').locator('../..');
    const projectCard = globalSection.getByText('Project', { exact: true }).locator('../..');
    const userCard = globalSection.getByText('User', { exact: true }).locator('../..');
    const typeCard = globalSection.getByText('User ID Type', { exact: true }).locator('../..');
    // Captured DASH006 tooltip/Search DOM; first User/type auto-open remains
    // source-derived until this flow runs. No invented listbox/dialog role.
    const popup = page.getByRole('tooltip').filter({ has: page.getByPlaceholder('Search...', { exact: true }) });
    const searchBox = popup.getByPlaceholder('Search...', { exact: true });
    const selectCategory = async (name: 'Traces' | 'Users') => {
      await page.getByLabel(new RegExp(`^${name} property count: `)).locator('..').getByText(name, { exact: true }).click();
    };
    const assertCatalog = async (property: Property, metricMode: boolean, search = property.display_name) => {
      const matches = (r: Receipt<CatalogBody>) => isDeepStrictEqual(r.scope, expectedScope) && r.input.cursor_mode === true &&
        r.input.category === 'system_metric' && r.input.source === 'traces' && r.input.search === search &&
        (metricMode ? r.input.role === 'metric' : !('role' in r.input)) && r.settled &&
        Boolean(r.parseError || r.status !== 200 || r.body?.result?.query_complete === true);
      await expect.poll(() => catalogs.some(matches), { timeout: UI_READY }).toBe(true);
      const receipt = catalogs.filter(matches).at(-1)!;
      expect(receipt.parseError).toBeUndefined(); expect(receipt.status).toBe(200);
      expect(receipt.input).not.toHaveProperty('project_ids');
      expect(Number.isInteger(receipt.input.page_size)).toBe(true); expect(Number(receipt.input.page_size)).toBeGreaterThan(0);
      expect(receipt.body!.result).toMatchObject({ ...catalogMetadata, has_more: false, next_cursor: null });
      const selected = receipt.body!.result.metrics.filter(row => row.property_id === property.property_id);
      expect(selected).toHaveLength(1); expect(selected[0]).toMatchObject(property);
    };
    const assertValues = (body: ValueBody, expected: ValueOption[]) => {
      expect(body.result).toMatchObject({ query_complete: true, query_status: 'complete', browse_status: 'exhausted', has_more: false, next_cursor: null });
      expect([...body.result.values].sort((a, b) => a.value.localeCompare(b.value)))
        .toEqual([...expected].sort((a, b) => a.value.localeCompare(b.value)));
    };
    const searchOptions = async (name: 'project' | 'user' | 'user_id_type', search: string, expected: ValueOption[]) => {
      await uiExpect(popup).toHaveCount(1); await uiExpect(popup).toBeVisible();
      await searchBox.fill(search); await uiExpect(searchBox).toHaveValue(search);
      const matches = (r: Receipt<ValueBody>) => isDeepStrictEqual(r.scope, expectedScope) &&
        r.input.property_id === `system_attribute:traces:${name}` && (r.input.search || '') === search &&
        r.input.metric_name === name && r.input.metric_type === 'system_metric' && r.input.source === 'traces' &&
        r.input.project_ids === '' && r.input.page_size === 10;
      // Same-identity/search/scope immutable values may come from the native cache.
      if (name === 'project') {
        // Approved Project-only correction, as in DASH007 searchOptions:
        // dashboard.py hydrates PG labels after physical time-slice discovery.
        // Inspect a complete native chain, including empty continuation pages.
        const rootMatches = (r: Receipt<ValueBody>) => matches(r) && !('cursor' in r.input) && r.settled;
        await expect.poll(() => values.some(rootMatches), { timeout: UI_READY }).toBe(true);
        const root = values.filter(rootMatches).at(-1)!;
        let receipt = root;
        const chain: Receipt<ValueBody>[] = [], options: ValueOption[] = [];
        const cursors = new Set<string>();
        const window = { start: root.body?.result?.query_window_start, end: root.body?.result?.query_window_end };
        const input = { property_id: 'system_attribute:traces:project', metric_name: 'project', metric_type: 'system_metric',
          source: 'traces', project_ids: '', page_size: 10, ...(search ? { search } : {}) };
        let requestedCursor: string | undefined;
        try {
          while (true) {
            chain.push(receipt);
            expect(receipt.parseError, 'Project value page JSON').toBeUndefined(); expect(receipt.status).toBe(200);
            expect(receipt.input).toEqual({ ...input, ...(requestedCursor ? { cursor: requestedCursor } : {}) });
            const result = receipt.body!.result;
            expect(result).toMatchObject({ query_complete: true, query_status: 'complete',
              query_window_start: window.start, query_window_end: window.end });
            expect(Number.isFinite(Date.parse(window.start!))).toBe(true);
            expect(Date.parse(window.end!)).toBeGreaterThan(Date.parse(window.start!));
            for (const option of result.values) {
              expect(options.some(previous => previous.value === option.value), 'Project chain duplicate UUID').toBe(false);
              options.push(option);
            }
            if (result.has_more === false) {
              expect(result).toMatchObject({ has_more: false, next_cursor: null, browse_status: 'exhausted' });
              break;
            }
            expect(result).toMatchObject({ has_more: true, browse_status: 'continuation' });
            const cursor = result.next_cursor;
            expect(typeof cursor).toBe('string'); expect(cursor!.length).toBeGreaterThan(0);
            expect(cursors.has(cursor!), 'Project chain repeated cursor').toBe(false); cursors.add(cursor!);
            const previousStartedAt = receipt.startedAt;
            const nextMatches = (r: Receipt<ValueBody>) => matches(r) && r.settled &&
              r.startedAt >= previousStartedAt && r.input.cursor === cursor;
            // The native visible sentinel follows automatically. This poll only
            // reads captured traffic, within stage3's existing60s wall.
            await expect.poll(() => values.some(nextMatches), { timeout: UI_READY }).toBe(true);
            requestedCursor = cursor!; receipt = values.find(nextMatches)!;
          }
          expect([...options].sort((a, b) => a.value.localeCompare(b.value)))
            .toEqual([...expected].sort((a, b) => a.value.localeCompare(b.value)));
        } finally {
          await testInfo.attach('native-project-value-chain', { contentType: 'application/json',
            body: JSON.stringify({ chain, options, expected, window }) });
        }
      } else {
        // Native User/type finite exhaustion is unchanged; neither adapter uses
        // the physical time-slice Project contract for this small inventory.
        const ready = (r: Receipt<ValueBody>) => matches(r) && r.settled &&
          Boolean(r.parseError || r.status !== 200 || r.body?.result?.query_complete === true);
        await expect.poll(() => values.some(ready), { timeout: UI_READY }).toBe(true);
        const receipt = values.filter(ready).at(-1)!;
        expect(receipt.parseError).toBeUndefined(); expect(receipt.status).toBe(200); expect(receipt.input).not.toHaveProperty('attribute_type');
        assertValues(receipt.body!, expected);
      }
      await uiExpect(popup.getByRole('progressbar')).toHaveCount(0);
      const rows = popup.locator('p[title]').filter({ hasNotText: /^Specify: / });
      await uiExpect(rows).toHaveCount(expected.length);
      await expect.poll(async () => (await rows.evaluateAll(elements => elements.map(element => element.getAttribute('title')))).sort(),
        { timeout: UI_READY }).toEqual(expected.map(option => option.label).sort());
    };
    const optionRow = (label: string) => popup.locator(`p[title="${label}"]`).locator('..');
    const assertControls = async (user: string, type: string) => {
      for (const [card, value] of [[projectCard, projectNames[0]], [userCard, user], [typeCard, type]] as const) {
        await uiExpect(card.getByRole('combobox')).toHaveText('Is');
        await uiExpect(card.locator('.filter-value-name')).toHaveText(value);
      }
      for (const choice of choices) {
        const card = page.locator(`p[title="${choice.name}"]`).locator('../..');
        await uiExpect(card.getByText('Distinct Count', { exact: true })).toBeVisible();
        for (const label of ['Project', 'User', 'User ID Type']) await uiExpect(card.getByText(label, { exact: true })).toHaveCount(0);
      }
      await uiExpect(page.locator('.breakdown-section-title').locator('../..').getByText('User', { exact: true })).toBeVisible();
      await uiExpect(page.getByRole('combobox').filter({ hasText: 'Table' })).toBeVisible();
      await uiExpect(page.getByRole('combobox').filter({ hasText: 'Day' })).toBeVisible();
      await uiExpect(page.getByText('7D', { exact: true })).toBeVisible();
    };
    const assertResult = (result: Result, groups: Groups, startedAt: number, endedAt: number, reopenedProof = false) => {
      expect(result).toMatchObject({ query_complete: true, query_exact: true, granularity: 'day' });
      const from = Date.parse(result.time_range.start), to = Date.parse(result.time_range.end);
      expect(to - from).toBe(7 * DAY); expect(to).toBeGreaterThanOrEqual(startedAt - 1000); expect(to).toBeLessThanOrEqual(endedAt + 1000);
      expect(Number(start / 1_000_000n)).toBeGreaterThan(from); expect(Number(start / 1_000_000n)).toBeLessThan(to);
      const buckets: number[] = [];
      for (let day = Math.floor(from / DAY) * DAY; day <= to; day += DAY) buckets.push(day);
      expect(result.metrics.map(metric => ({ id: metric.id, name: metric.name, unit: metric.unit, aggregation: metric.aggregation })))
        .toEqual(choices.map(choice => ({ ...choice, unit: '', aggregation: 'count_distinct' })));
      for (const [index, metric] of result.metrics.entries()) {
        expect(metric).toMatchObject({ query_complete: true, query_exact: true });
        expect(metric.series.map(series => series.name).sort()).toEqual(Object.keys(groups).sort());
        for (const series of metric.series) {
          expect(series.data.map(point => Date.parse(point.timestamp))).toEqual(buckets);
          expect(series.data.map(point => point.value)).toEqual(buckets.map(bucket => bucket === plantedDay ? groups[series.name][index] : null));
          if (reopenedProof && metric.id === 'span_count') {
            expect(series.name).toBe(U1);
            expect(series.data.find(point => Date.parse(point.timestamp) === plantedDay)!.value,
              'Check3: reopened U1 Spans on the planted UTC day').toBe(EXPECTED_REOPENED_SPANS);
          }
        }
      }
      return buckets;
    };
    const assertTable = async (groups: Groups, buckets: number[], tile = false) => {
      // widgetUtils / WidgetChart: three metrics keep metric + external group
      // labels; empty is one null-filled total series, never an unchecked [].
      const columns: Record<string, (number | null)[]> = {};
      for (const [index, choice] of choices.entries()) for (const [group, vector] of Object.entries(groups)) {
        columns[`${choice.name}${group === 'total' ? '' : ` / ${group}`} (count_distinct)`] =
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
    };
    const queryAfter = async (label: string, expected: Config, groups: Groups, action: () => Promise<unknown>, tile = false, reopenedProof = false) => {
      const since = queries.length, startedAt = Date.now();
      const matches = (r: QueryReceipt) => r.startedAt >= startedAt && isDeepStrictEqual(r.input, expected) && r.settled &&
        Boolean(r.parseError || r.status !== 200 || r.body?.result?.query_complete === true);
      await Promise.all([
        expect.poll(() => queries.slice(since).some(matches), { timeout: UI_READY }).toBe(true), action(),
      ]);
      const receipt = queries.slice(since).find(matches)!;
      await testInfo.attach(`${label}-query`, { contentType: 'application/json', body: JSON.stringify(receipt) });
      expect(receipt.parseError).toBeUndefined(); expect(receipt.status).toBe(200); expect(receipt.scope).toEqual(expectedScope);
      expect(receipt.input).toEqual(expected);
      for (const metric of receipt.input.metrics) expect(metric).not.toHaveProperty('filters');
      for (const filter of receipt.input.filters) expect(filter.filter_config).not.toHaveProperty('attribute_value_types');
      lastBuckets = assertResult(receipt.body!.result, groups, receipt.startedAt, receipt.endedAt, reopenedProof);
      await assertTable(groups, lastBuckets, tile);
      await testInfo.attach(`${label}-table`, { contentType: 'image/png', body: await page.screenshot() });
      return receipt;
    };
    const saveWidget = async (label: string, expected: Config, groups: Groups) => {
      const previousId = savedWidget?.id, startedAt = Date.now();
      const path = previousId ? `${DASHBOARDS}${dashboardId}/widgets/${previousId}/` : `${DASHBOARDS}${dashboardId}/widgets/`;
      const [response] = await Promise.all([
        page.waitForResponse(r => new URL(r.url()).pathname === path && r.request().method() === (previousId ? 'PATCH' : 'POST'), { timeout: UI_READY }),
        page.getByRole('button', { name: 'Save', exact: true }).click(),
      ]);
      const head = { path, status: response.status(), method: response.request().method(), scope: scopeOf(response),
        startedAt: response.request().timing().startTime, endedAt: Date.now() };
      saves.push({ label, ...head });
      await testInfo.attach(`${label}-save-head`, { contentType: 'application/json', body: JSON.stringify(head) });
      expect(head.status).toBe(200); expect(head.scope).toEqual(expectedScope); expect(head.startedAt).toBeGreaterThanOrEqual(startedAt);
      const input = response.request().postDataJSON() as Omit<Widget, 'id'>;
      expect(input.name).toBe(widgetName); expect(input.query_config).toEqual(expected); expect(input.chart_config.chart_type).toBe('table');
      if (originalChart) expect(input.chart_config).toEqual(originalChart); else originalChart = structuredClone(input.chart_config);
      const saved = ((await response.json()) as { result: Widget }).result;
      expect(saved.id).toMatch(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/);
      if (previousId) expect(saved.id).toBe(previousId);
      expect(saved).toMatchObject({ name: widgetName, query_config: expected, chart_config: originalChart }); savedWidget = saved;
      expect(saved.query_config).toEqual(expected); expect(saved.chart_config).toEqual(originalChart);
      const rows = await probe.pg('SELECT id, name, dashboard_id, query_config, chart_config, deleted FROM tracer_dashboardwidget WHERE dashboard_id=$1', [dashboardId]);
      expect(rows).toEqual([{ id: saved.id, name: widgetName, dashboard_id: dashboardId, query_config: expected, chart_config: originalChart, deleted: false }]);
      const dashboardRows = await probe.pg('SELECT id, name, workspace_id, deleted FROM tracer_dashboard WHERE id=$1', [dashboardId]);
      expect(dashboardRows).toEqual([{ id: dashboardId, name: dashboardName, workspace_id: actor.workspaceId, deleted: false }]);
      const detail = await actor.api.get<{ result: { id: string; name: string; workspace: string; widgets: Widget[] } }>(`${DASHBOARDS}${dashboardId}/`);
      expect(detail.result).toMatchObject({ id: dashboardId, name: dashboardName, workspace: actor.workspaceId });
      expect(detail.result.widgets.map(widget => ({ id: widget.id, name: widget.name, query_config: widget.query_config, chart_config: widget.chart_config })))
        .toEqual([{ id: saved.id, name: widgetName, query_config: expected, chart_config: originalChart }]);
      await Promise.all([actor, scopeActors.ownerB, emptyActor].map(async selectedActor => {
        const nestedPath = `${DASHBOARDS}${dashboardId}/widgets/${saved.id}/`;
        const scoped = await scopeActors.send<Widget>(selectedActor, 'GET', nestedPath);
        scopeResults.push({ path: nestedPath, organizationId: selectedActor.organizationId, workspaceId: selectedActor.workspaceId, ...scoped });
        expect(scoped.status).toBe(selectedActor === actor ? 200 : 404);
        if (selectedActor === actor) {
          expect(scoped.body).toMatchObject({ id: saved.id, name: widgetName });
          expect(scoped.body.query_config).toEqual(expected); expect(scoped.body.chart_config).toEqual(originalChart);
        }
      }));
      const receipt = { label, ...head, input, saved, rows, dashboardRows, detail };
      saves.push(receipt); await testInfo.attach(`${label}-saved-widget`, { contentType: 'application/json', body: JSON.stringify(receipt) });
      await uiExpect(page).toHaveURL(new RegExp(`/dashboard/dashboards/${dashboardId}$`));
      await assertTable(groups, lastBuckets, true); // Native saved render need not issue a new query before explicit reload.
    };
    const replaceIdentity = async (name: 'user' | 'user_id_type', previous: string, next: string) => {
      await uiExpect(popup).toHaveCount(0);
      await (name === 'user' ? userCard : typeCard).locator('.filter-value-name').locator('..').click();
      await searchOptions(name, previous, [{ value: previous, label: previous }]);
      const oldRow = optionRow(previous); await uiExpect(oldRow).toHaveCount(1);
      await uiExpect(oldRow.getByRole('checkbox')).toBeChecked(); await oldRow.click();
      await uiExpect(oldRow.getByRole('checkbox')).not.toBeChecked(); await uiExpect(popup.getByRole('button', { name: 'Add', exact: true })).toBeDisabled();
      await searchOptions(name, next, [{ value: next, label: next }]);
      const row = optionRow(next); await uiExpect(row).toHaveCount(1); await uiExpect(row.getByRole('checkbox')).not.toBeChecked();
      await row.click(); await uiExpect(row.getByRole('checkbox')).toBeChecked();
      await popup.getByRole('button', { name: 'Add', exact: true }).click(); await uiExpect(popup).toHaveCount(0);
    };
    const assertCheckedIdentities = async (user: string, type: string) => {
      for (const [name, value, card] of [['user', user, userCard], ['user_id_type', type, typeCard]] as const) {
        await uiExpect(popup).toHaveCount(0); await card.locator('.filter-value-name').locator('..').click();
        await searchOptions(name, value, [{ value, label: value }]);
        await uiExpect(optionRow(value).getByRole('checkbox')).toBeChecked();
        await testInfo.attach(`reopened-${name}-${value}-checked`, { contentType: 'image/png', body: await page.screenshot() });
        await popup.getByRole('button', { name: 'Add', exact: true }).click(); await uiExpect(popup).toHaveCount(0);
      }
    };
    try {
      await test.step('1 create and name a Table widget with 7D and Day', async () => {
        await page.goto('/dashboard/dashboards', { waitUntil: 'domcontentloaded' });
        const [created] = await Promise.all([
          page.waitForResponse(r => new URL(r.url()).pathname === DASHBOARDS && r.request().method() === 'POST', { timeout: UI_READY }),
          page.getByRole('button', { name: 'Create Dashboard', exact: true }).click(),
        ]);
        expect(created.status()).toBe(200); expect(scopeOf(created)).toEqual(expectedScope);
        dashboardId = ((await created.json()) as { result: { id: string } }).result.id;
        await testInfo.attach('dashboard-id', { contentType: 'application/json', body: JSON.stringify({ dashboardId, dashboardName, widgetName }) });
        await page.getByRole('heading', { name: 'Untitled', exact: true }).click();
        await page.getByPlaceholder('Untitled Dashboard').fill(dashboardName);
        const [named] = await Promise.all([
          page.waitForResponse(r => new URL(r.url()).pathname === `${DASHBOARDS}${dashboardId}/` && ['PATCH', 'PUT'].includes(r.request().method()), { timeout: UI_READY }),
          page.getByPlaceholder('Untitled Dashboard').press('Enter'),
        ]);
        expect(named.status()).toBe(200); expect(scopeOf(named)).toEqual(expectedScope); expect(named.request().postDataJSON()).toMatchObject({ name: dashboardName });
        saves.push({ label: 'dashboard-create-rename', created: await created.json(), named: await named.json(), scope: scopeOf(named) });
        await page.getByRole('button', { name: 'Add Widget', exact: true }).first().click();
        await page.getByText('Untitled widget', { exact: true }).click();
        await page.getByPlaceholder('Untitled widget').fill(widgetName); await page.getByPlaceholder('Untitled widget').press('Enter');
        await page.getByRole('combobox').filter({ hasText: 'Line' }).click(); await page.getByRole('option', { name: 'Table', exact: true }).click();
        await page.getByText('7D', { exact: true }).click();
        await page.getByRole('combobox').filter({ hasText: 'Day' }).click(); await page.getByRole('option', { name: 'Day', exact: true }).click();
      }, { timeout: UI_READY });

      await test.step('2 discover native typed metrics and scoped definitions', async () => {
        for (const [index, choice] of choices.entries()) {
          if (index) await page.locator('.metric-section-title').click(); else await page.getByText('Select Metric', { exact: true }).click();
          await selectCategory(index === 0 ? 'Users' : 'Traces');
          if (index === 0) {
            await page.getByPlaceholder('Search metrics...').fill('');
            await assertCatalog(properties[index], true, 'user'); // Users category's real default nameFilter.
          }
          await page.getByPlaceholder('Search metrics...').fill(choice.name); await assertCatalog(properties[index], true);
          await page.getByRole('button', { name: `${choice.name} (number, Traces)`, exact: true }).click();
          await page.locator(`p[title="${choice.name}"]`).locator('../..').locator('.MuiChip-clickable').click();
          await page.getByText('Distinct Count', { exact: true }).last().click();
        }
        await Promise.all([actor, scopeActors.ownerB, emptyActor].map(async selectedActor => {
          for (const property of properties) {
            const input = { cursor_mode: true, category: 'system_metric', source: 'traces', search: property.display_name,
              page_size: 25, ...(property.role === 'metric' ? { role: 'metric' } : {}) };
            const response = await scopeActors.send<CatalogBody>(selectedActor, 'POST', METRICS, input);
            scopeResults.push({ path: METRICS, organizationId: selectedActor.organizationId, workspaceId: selectedActor.workspaceId, input, ...response });
            expect(response.status).toBe(200); expect(response.body.result).toMatchObject({ ...catalogMetadata, has_more: false, next_cursor: null });
            const selected = response.body.result.metrics.filter(row => row.property_id === property.property_id);
            expect(selected).toHaveLength(1); expect(selected[0]).toMatchObject(property);
          }
          // Fresh foreign-only observed string proves a real positive, not merely
          // static manifests or a label shared legitimately by A and B.
          const foreign = selectedActor === scopeActors.ownerB;
          const input = { cursor_mode: true, category: 'custom_attribute', source: 'traces', search: foreignKey, page_size: 25 };
          const catalog = await scopeActors.send<CatalogBody>(selectedActor, 'POST', METRICS, input);
          scopeResults.push({ path: METRICS, organizationId: selectedActor.organizationId, workspaceId: selectedActor.workspaceId, input, ...catalog });
          expect(catalog.status).toBe(200); expect(catalog.body.result).toMatchObject({ ...catalogMetadata, has_more: false, next_cursor: null });
          expect(catalog.body.result.metrics.map(row => row.property_id)).toEqual(foreign ? [`custom_attribute:${foreignKey}`] : []);
          if (foreign) expect(catalog.body.result.metrics[0]).toMatchObject({ property_id: `custom_attribute:${foreignKey}`,
            name: foreignKey, display_name: foreignKey, property_kind: 'custom_attribute', category: 'custom_attribute', source: 'traces',
            type: 'string', output_type: 'string', role: 'dimension', data_type: 'string', attribute_types: ['string'], attribute_types_exact: false });
          const valueInput = { property_id: `custom_attribute:${foreignKey}`, metric_name: foreignKey, metric_type: 'custom_attribute',
            source: 'traces', project_ids: '', page_size: 10, attribute_type: 'string' };
          const found = await scopeActors.send<ValueBody>(selectedActor, 'POST', VALUES, valueInput);
          scopeResults.push({ path: VALUES, organizationId: selectedActor.organizationId, workspaceId: selectedActor.workspaceId, input: valueInput, ...found });
          expect(found.status).toBe(200); assertValues(found.body, foreign ? [{ value: 'foreign', label: 'foreign', type: 'string' }] : []);
          expect(found.body.result).toMatchObject({ ...catalogMetadata, attribute_types: foreign ? ['string'] : [], attribute_types_exact: false });
        }));
      }, { timeout: UI_READY });

      await test.step('3 select Project and User grouping with positive scoped source controls', async () => {
        await queryAfter('project-only', configFor(0, undefined, undefined, false), { total: [2, 3, 6] }, async () => {
          await page.locator('.filter-section-title').click(); await selectCategory('Traces');
          await page.getByPlaceholder('Search filter attributes...').fill('Project'); await assertCatalog(properties[3], false);
          await page.getByRole('button', { name: 'Project (string, Traces)', exact: true }).click();
          await projectCard.getByText('Select value...', { exact: true }).click(); // Captured DASH006 initial Project control.
          await searchOptions('project', projectNames[0], [{ value: projectIds[0], label: projectNames[0] }]);
          const row = optionRow(projectNames[0]); await uiExpect(row.getByRole('checkbox')).not.toBeChecked(); await row.click();
          await uiExpect(row.getByRole('checkbox')).toBeChecked(); await popup.getByRole('button', { name: 'Add', exact: true }).click();
        });
        await queryAfter('both-users', configFor(0), bothGroups, async () => {
          await page.locator('.breakdown-section-title').click(); await selectCategory('Users');
          await page.getByPlaceholder('Search breakdown attributes...').fill('User'); await assertCatalog(properties[4], false);
          await page.getByRole('button', { name: 'User (string, Traces)', exact: true }).click();
        });
        const reads = [
          { actor, project: '', users: [U1, U2], types: ['email', 'phone'] },
          { actor, project: projectIds[0], users: [U1, U2], types: ['email', 'phone'] },
          { actor, project: projectIds[1], users: [U1], types: ['email'] },
          { actor: scopeActors.ownerB, project: '', users: [U1], types: ['email'] },
          { actor: scopeActors.ownerB, project: projectIds[2], users: [U1], types: ['email'] },
          { actor: emptyActor, project: '', users: [], types: [] },
        ];
        await Promise.all(reads.map(async read => {
          for (const [name, options] of [['user', read.users], ['user_id_type', read.types]] as const) {
            const input = { property_id: `system_attribute:traces:${name}`, metric_name: name, metric_type: 'system_metric',
              source: 'traces', project_ids: read.project, page_size: 10 };
            const response = await scopeActors.send<ValueBody>(read.actor, 'POST', VALUES, input);
            scopeResults.push({ path: VALUES, organizationId: read.actor.organizationId, workspaceId: read.actor.workspaceId, input, ...response });
            expect(response.status).toBe(200); assertValues(response.body, options.map(value => ({ value, label: value })));
          }
        }));
        // Approved Q/R positives are native dashboard read assertions, not widget
        // creation or replacements for P's user actions. No retry/dispatch helper.
        for (const project of [1, 2]) {
          const selectedActor = project === 2 ? scopeActors.ownerB : actor;
          const input = configFor(project, U1, 'email'), startedAt = Date.now();
          const response = await scopeActors.send<{ result: Result }>(selectedActor, 'POST', QUERY, input);
          const endedAt = Date.now();
          const receipt = { path: QUERY, input, organizationId: selectedActor.organizationId, workspaceId: selectedActor.workspaceId,
            startedAt, endedAt, ...response };
          scopeResults.push(receipt); await testInfo.attach(`positive-${project === 1 ? 'sibling' : 'foreign'}`, { contentType: 'application/json', body: JSON.stringify(receipt) });
          expect(response.status).toBe(200); assertResult(response.body.result, { [U1]: [1, 1, 2] }, startedAt, endedAt);
        }
      }, { timeout: UI_READY });

      await test.step('4 select U1 and email, prove phone is disjoint and restore email', async () => {
        const selected = await queryAfter('U1-email', canonicalU1, u1Groups, async () => {
          for (const [property, value] of [[properties[4], U1], [properties[5], 'email']] as const) {
            await page.locator('.filter-section-title').click(); await selectCategory('Users');
            await page.getByPlaceholder('Search filter attributes...').fill(property.display_name); await assertCatalog(property, false);
            await page.getByRole('button', { name: `${property.display_name} (string, Traces)`, exact: true }).click();
            // getWidgetFilterDefaults(string) / pendingFilterOpen: await native
            // first-open, no speculative second click or fallback UI route.
            await searchOptions(property.name as 'user' | 'user_id_type', value, [{ value, label: value }]);
            const row = optionRow(value); await uiExpect(row.getByRole('checkbox')).not.toBeChecked(); await row.click();
            await uiExpect(row.getByRole('checkbox')).toBeChecked(); await popup.getByRole('button', { name: 'Add', exact: true }).click();
          }
        });
        expect(selected.input.filters.find(filter => filter.column_id === 'user_id_type')!.filter_config.filter_value,
          'Check1: native User ID Type filter wire').toEqual([EXPECTED_USER_TYPE_WIRE]);
        await assertControls(U1, 'email');
        await queryAfter('U1-phone-empty', configFor(0, U1, 'phone'), emptyGroups, () => replaceIdentity('user_id_type', 'email', 'phone'));
        await queryAfter('U1-email-restored-before-save', canonicalU1, u1Groups, () => replaceIdentity('user_id_type', 'phone', 'email'));
      }, { timeout: UI_READY });

      await test.step('5 save U1 and email with exact persisted binding and table', async () => {
        await assertControls(U1, 'email'); await saveWidget('U1-email', canonicalU1, u1Groups);
      }, { timeout: UI_READY });

      await test.step('6 reload and reopen U1 and email with exact 1/2/4 and selected controls', async () => {
        expect(savedWidget!.query_config).toEqual(canonicalU1);
        await queryAfter('U1-reload', canonicalU1, u1Groups, () => page.reload({ waitUntil: 'domcontentloaded' }), true);
        await queryAfter('U1-reopen', canonicalU1, u1Groups, () => page.locator(`[data-widget-id="${savedWidget!.id}"]`)
          .getByText(widgetName, { exact: true }).click(), false, true);
        await assertControls(U1, 'email'); await assertCheckedIdentities(U1, 'email');
      }, { timeout: UI_READY });

      await test.step('7 replace U1 with U2, prove email is empty, select phone and save', async () => {
        await queryAfter('U2-email-empty', configFor(0, U2, 'email'), emptyGroups, () => replaceIdentity('user', U1, U2));
        await queryAfter('U2-phone', canonicalU2, u2Groups, () => replaceIdentity('user_id_type', 'email', 'phone'));
        await assertControls(U2, 'phone'); await saveWidget('U2-phone', canonicalU2, u2Groups);
      }, { timeout: UI_READY });

      await test.step('8 reload and reopen U2 and phone with exact 1/1/2 and selected controls', async () => {
        expect(savedWidget!.query_config).toEqual(canonicalU2);
        await queryAfter('U2-reload', canonicalU2, u2Groups, () => page.reload({ waitUntil: 'domcontentloaded' }), true);
        await queryAfter('U2-reopen', canonicalU2, u2Groups, () => page.locator(`[data-widget-id="${savedWidget!.id}"]`)
          .getByText(widgetName, { exact: true }).click());
        await assertControls(U2, 'phone'); await assertCheckedIdentities(U2, 'phone');
      }, { timeout: UI_READY });

      await test.step('9 restore and save U1 and email with unchanged typed source versions and scope', async () => {
        await queryAfter('restoration-U1-phone-empty', configFor(0, U1, 'phone'), emptyGroups, () => replaceIdentity('user', U2, U1));
        await queryAfter('restored-U1-email', canonicalU1, u1Groups, () => replaceIdentity('user_id_type', 'phone', 'email'));
        await assertControls(U1, 'email'); await saveWidget('restored-U1-email', canonicalU1, u1Groups);
        const [finalSources, finalUsers, finalSessions, finalTraces, finalRemaps, finalProjects] = await Promise.all([
          probe.ch<Span>(sourceSql, sourceParams), probe.ch<EndUser>(userSql, projectParams), probe.ch<Session>(sessionSql, projectParams),
          probe.ch<Trace>(traceSql, projectParams), probe.ch(remapSql, remapParams), probe.pg<Project>(projectSql, [projectNames]),
        ]);
        await testInfo.attach('unchanged-source-and-versions', { contentType: 'application/json', body: JSON.stringify({
          projects, finalProjects, initialSources, finalSources, initialUsers, finalUsers, initialSessions, finalSessions,
          initialTraces, finalTraces, initialRemaps, finalRemaps,
        }) });
        expect(finalSources).toEqual(initialSources); expect(finalUsers).toEqual(initialUsers); expect(finalSessions).toEqual(initialSessions);
        expect(finalTraces).toEqual(initialTraces); expect(finalRemaps).toEqual(initialRemaps); expect(finalProjects).toEqual(projects);
        expect(savedWidget!.query_config).toEqual(canonicalU1); expect(savedWidget!.chart_config).toEqual(originalChart);
      }, { timeout: UI_READY });
    } finally { page.off('response', captureResponse); }
  } finally {
    try {
      await Promise.all(pending);
      await testInfo.attach('native-catalog-values-queries-saves-scope', { contentType: 'application/json',
        body: JSON.stringify({ catalogs, values, queries, saves, scopeResults }) });
    } finally { await context.close(); }
  }
});
