import { randomBytes, randomUUID } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { request, type Request, type Response } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { sendTrace } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// OBS008 / ProjectSerializer: public simulator-labelled project, ordinary OTLP;
// dashboard.py has no voice_calls metric adapter. These fixed keys stay custom.
const DASHBOARDS = '/tracer/dashboard/';
const METRICS = `${DASHBOARDS}metrics/`, VALUES = `${DASHBOARDS}filter_values/`, QUERY = `${DASHBOARDS}query/`;
const DURATION = 'call.duration', TURNS = 'call.total_turns', CALL_TYPE = 'call_type';
const UI_READY = 60_000, DAY = 86_400_000;
// Assertion-only red anchors: never used in seed, readiness or receipt matching.
const EXPECTED_CATALOG_TYPE = 'number'; // Check1 -> 'string'.
const EXPECTED_A_ROOT_TURNS = 4; // Check2 -> 5.
const EXPECTED_REOPENED_OUTBOUND_DURATION = 25; // Check3 -> 26.
type Wire = Record<string, unknown>;
type Scope = { organizationId: string; workspaceId: string };
type Filter = { column_id: string; property_id: string; display_name: string; source: string; output_type: string;
  filter_config: { filter_type: string; filter_op: string; filter_value: string[]; col_type: string; attribute_value_types?: string[] } };
type Metric = { id: string; name: string; property_id: string; display_name: string; type: string;
  source: string; aggregation: string; attribute_key: string; attribute_type: string };
type Config = { project_ids: string[]; time_range: { preset: string }; granularity: string;
  metrics: Metric[]; filters: Filter[]; breakdowns: Record<string, string>[] };
type Result = { query_complete: boolean; query_exact: boolean; granularity: string; time_range: { start: string; end: string };
  metrics: { id: string; name: string; unit: string; aggregation: string; query_complete: boolean; query_exact: boolean;
    series: { name: string; data: { timestamp: string; value: number | null }[] }[] }[] };
type QueryBody = { result: Result };
type Vector = [number | null, number | null];
type Groups = Record<string, Vector>;
type Property = { property_id: string; property_kind: string; category: string; name: string; display_name: string;
  source: string; type: string; output_type: string; role: string; data_type?: string;
  attribute_types?: string[]; attribute_types_exact?: boolean; allowed_aggregations?: string[] };
type CatalogBody = { result: { metrics: Property[]; query_complete: boolean; has_more: boolean; next_cursor: string | null } };
type Value = { value: string; label: string; type?: string };
type ValueBody = { result: { values: Value[]; query_complete: boolean; query_status: string;
  has_more: boolean; next_cursor: string | null; browse_status: string; query_window_start?: string; query_window_end?: string } };
type Receipt<T> = { path: string; method: string; input: Wire; body?: T; status: number; scope: Scope; authorized: boolean;
  startedAt: number; endedAt: number; settled: boolean; requestId?: string; contentType?: string; error?: string };
type Project = { id: string; name: string; model_type: string; trace_type: string; source: string;
  organization_id: string; workspace_id: string; deleted: boolean; created_at: unknown; updated_at: unknown };
type Span = { id: string; trace_id: string; project_id: string; org_id: string; parent_span_id: string; name: string;
  observation_type: string; service_name: string; start_us: string; end_us: string; latency_ms: number; status: string;
  cost: number; is_deleted: number; trace_session_id: null; end_user_id: null; version: string;
  attrs_string: Record<string, string>; attrs_number: Record<string, number>; attrs_bool: Record<string, number>; extra: string; resource: string };
type Trace = { id: string; project_id: string; name: string; created_at: string; updated_at: string; version: string; is_deleted: number };
type Widget = { id: string; name: string; query_config: Config; chart_config: { chart_type: string; [key: string]: unknown } };
const sorted = <T>(rows: T[]) => [...rows].sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));

test('DASH-E2E-009: a saved conversation widget excludes child-model call-metric decoys', {
  tag: ['@flow'],
  annotation: flowAnnotation({ id: 'DASH-E2E-009', area: 'dashboards',
    userGoal: 'A saved conversation widget excludes child-model call-metric decoys.',
    steps: [
      'create and name a Table widget with 7D and Day after seeding five scoped traces',
      'discover two numeric custom call metrics and explicitly select Sum with typed scope checks',
      'select primary Project through its native exhausted cursor chain and verify all primary-span values',
      'select conversation, prove the LLM-only contrast and restore conversation',
      'filter and group by native custom call_type, distinguish inbound/outbound and restore both',
      'save the exact both-group binding and inspect its persisted Table and ownership',
      'reload the saved dashboard and read the exact four series again',
      'reopen the same widget, verify controls and results and conserve every seeded source version',
    ],
    backendChecks: [
      "Native catalog and selected property identities preserve this flow's source, types, choices and actor scope.",
      "This flow's exact publicly produced source identities and typed latest facts are present and unchanged outside its authorized UI actions.",
      "The preview, saved binding and reopened widget equal this flow's independently specified filtered and grouped result.",
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe: probe }, testInfo) => {
  test.setTimeout(600_000); // ASYNC_JOB60 + 2×SPAN_VISIBLE15 + 8×UI_READY60 + 30 headroom; V/R8.
  const uiExpect = expect.configure({ timeout: UI_READY });
  const prefix = `e2e-dash9-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const actor = scopeActors.ownerA, emptyActor = scopeActors.withWorkspace(actor, scopeActors.emptyWorkspace.id);
  const actors = [actor, actor, scopeActors.ownerB];
  const projectNames = [`${prefix}-primary`, `${prefix}-sibling`, `${prefix}-foreign`];
  const dashboardName = `${prefix}-dashboard`, widgetName = `${prefix}-widget`, foreignKey = `${prefix}.foreign-only`;
  const plantedDay = Math.floor(Date.now() / DAY) * DAY - DAY, at = plantedDay + DAY / 2;
  const start = BigInt(at) * 1_000_000n;
  // Independent input literals. Aggregate oracles below are separate constants.
  const seeds = [
    { key: 'A', project: 0, kind: 'conversation', duration: 12, turns: 4, childDuration: 1200, childTurns: 400, callType: 'inbound' },
    { key: 'B', project: 0, kind: 'conversation', duration: 25, turns: 8, childDuration: 2500, childTurns: 800, callType: 'outbound' },
    { key: 'N', project: 0, kind: 'chain', duration: 100, turns: 40, childDuration: 10000, childTurns: 4000, callType: 'inbound' },
    { key: 'S', project: 1, kind: 'conversation', duration: 12000, turns: 4000, childDuration: 120000, childTurns: 40000, callType: 'inbound' },
    { key: 'F', project: 2, kind: 'conversation', duration: 25000, turns: 8000, childDuration: 250000, childTurns: 80000, callType: 'inbound' },
  ].map(seed => ({ ...seed, traceId: randomUUID(), spanIds: [randomBytes(8).toString('hex'), randomBytes(8).toString('hex')],
    callId: `${prefix}-${seed.key}-${seed.key === 'N' ? 'not-a-call' : 'call'}`, childCallId: `${prefix}-${seed.key}-child-decoy` }));
  expect(new Set(seeds.map(seed => seed.traceId)).size).toBe(5);
  expect(new Set(seeds.flatMap(seed => seed.spanIds)).size).toBe(10);
  for (const id of seeds.flatMap(seed => seed.spanIds)) { expect(id).toMatch(/^[0-9a-f]{16}$/); expect(id).not.toBe('0000000000000000'); }
  const attach = (name: string, body: unknown) => testInfo.attach(name, { contentType: 'application/json', body: JSON.stringify(body) });
  await attach('planted-identities', { seeds, projectNames, foreignKey, start: String(start), actors: scopeActors.evidence().actors });
  const projectIds: string[] = [], publicProjects: unknown[] = [];
  const projectSql = 'SELECT id,name,model_type,trace_type,source,organization_id,workspace_id,deleted,created_at,updated_at FROM tracer_project WHERE name=ANY($1) OR id=ANY($2::uuid[]) ORDER BY id';
  let initialProjects: Project[] = [];
  await test.step('public simulator project creation and scoped PG readiness', async () => {
    for (const [index, name] of projectNames.entries()) {
      const input = { name, model_type: 'GenerativeLLM', trace_type: 'observe', source: 'simulator' };
      const response = await scopeActors.send<{ result: { project_id: string; name: string } }>(actors[index], 'POST', '/tracer/project/', input);
      publicProjects.push({ input, scope: { organizationId: actors[index].organizationId, workspaceId: actors[index].workspaceId }, ...response });
      await attach('public-project-create-safe', publicProjects);
      expect(response.status).toBe(200); expect(response.body.result.name).toBe(name);
      expect(response.body.result.project_id).toMatch(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/);
      projectIds.push(response.body.result.project_id);
    }
    expect(new Set(projectIds).size).toBe(3);
    await expect.poll(async () => {
      initialProjects = await probe.pg<Project>(projectSql, [projectNames, projectIds]);
      return sorted(initialProjects.map(({ created_at, updated_at, ...row }) => row));
    }, POLL.ASYNC_JOB).toEqual(sorted(projectNames.map((name, index) => ({ id: projectIds[index], name,
      model_type: 'GenerativeLLM', trace_type: 'observe', source: 'simulator', deleted: false,
      organization_id: actors[index].organizationId, workspace_id: actors[index].workspaceId }))));
  }, { timeout: POLL.ASYNC_JOB.timeout });
  const ingestion = await request.newContext();
  try {
    for (const seed of seeds) {
      const owner = actors[seed.project];
      const keys = scopeActors.owners.find(item => item.organizationId === owner.organizationId && item.workspaceId === owner.workspaceId)!;
      const common = { [CALL_TYPE]: seed.callType, 'gen_ai.cost.total': 0, ...(seed.project === 2 ? { [foreignKey]: 'foreign' } : {}) };
      expect(await sendTrace(ingestion, { collectorUrl: E2E.collectorUrl, apiKey: keys.apiKey, secretKey: keys.secretKey,
        projectName: projectNames[seed.project], traceId: seed.traceId, rootSpanId: seed.spanIds[0], childSpanId: seed.spanIds[1],
        rootName: `${prefix}-${seed.key}-root`, childName: `${prefix}-${seed.key}-child`,
        startTimeUnixNano: start, endTimeUnixNano: start + 50_000_000n, resourceAttributes: { project_type: 'observe' },
        rootAttributes: { ...common, 'fi.span.kind': seed.kind, [DURATION]: seed.duration, [TURNS]: seed.turns,
          metadata: { call_execution_id: seed.callId }, 'call.status': 'ended' },
        childAttributes: { ...common, 'fi.span.kind': 'llm', [DURATION]: seed.childDuration, [TURNS]: seed.childTurns,
          metadata: { call_execution_id: seed.childCallId }, 'call.status': 'in-progress' } }))
        .toEqual({ traceId: seed.traceId, spanIds: seed.spanIds, projectName: projectNames[seed.project] });
    }
  } finally { await ingestion.dispose(); }
  const params = { primary: projectIds[0], sibling: projectIds[1], foreign: projectIds[2] };
  // CH schema002 and converter.go; FINAL source rows, not observed-index physical counts.
  const spanSql = `SELECT id,trace_id,project_id,org_id,parent_span_id,name,observation_type,service_name,
    toString(toUnixTimestamp64Micro(start_time)) AS start_us,toString(toUnixTimestamp64Micro(end_time)) AS end_us,
    latency_ms,status,cost,is_deleted,trace_session_id,end_user_id,toString(_version) AS version,
    attrs_string,attrs_number,attrs_bool,toString(attributes_extra) AS extra,toString(resource_attrs) AS resource
    FROM spans FINAL WHERE project_id IN ({primary:UUID},{sibling:UUID},{foreign:UUID}) ORDER BY id SETTINGS readonly=1,max_threads=1`;
  const traceSql = `SELECT id,project_id,name,toString(created_at) AS created_at,toString(updated_at) AS updated_at,
    toString(_version) AS version,is_deleted FROM traces FINAL
    WHERE project_id IN ({primary:UUID},{sibling:UUID},{foreign:UUID}) ORDER BY toString(id) SETTINGS readonly=1,max_threads=1`;
  const expectedSpans = sorted(seeds.flatMap(seed => seed.spanIds.map((id, child) => ({ id, trace_id: seed.traceId,
    project_id: projectIds[seed.project], org_id: actors[seed.project].organizationId, parent_span_id: child ? seed.spanIds[0] : '',
    name: `${prefix}-${seed.key}-${child ? 'child' : 'root'}`, observation_type: child ? 'llm' : seed.kind,
    service_name: projectNames[seed.project], start_us: String(start / 1000n), end_us: String(start / 1000n + 50_000n),
    latency_ms: 50, status: 'OK', cost: 0, is_deleted: 0, trace_session_id: null, end_user_id: null }))));
  let initialSpans: Span[] = [], initialTraces: Trace[] = [];
  try {
    await expect.poll(async () => {
      initialSpans = await probe.ch<Span>(spanSql, params);
      return sorted(initialSpans.map(({ version, attrs_string, attrs_number, attrs_bool, extra, resource, ...row }) => row));
    }, POLL.SPAN_VISIBLE).toEqual(expectedSpans);
    for (const seed of seeds) for (const [child, id] of seed.spanIds.entries()) {
      const row = initialSpans.find(item => item.id === id)!;
      expect(BigInt(row.version)).toBeGreaterThan(0n);
      for (const [key, value] of [[DURATION, child ? seed.childDuration : seed.duration], [TURNS, child ? seed.childTurns : seed.turns]] as const) {
        expect(Object.hasOwn(row.attrs_number, key)).toBe(true); expect(row.attrs_number[key]).toBe(value);
        expect(Object.hasOwn(row.attrs_string, key)).toBe(false); expect(Object.hasOwn(row.attrs_bool, key)).toBe(false);
      }
      for (const [key, value] of [['fi.span.kind', child ? 'llm' : seed.kind], [CALL_TYPE, seed.callType], ['call.status', child ? 'in-progress' : 'ended']]) {
        expect(row.attrs_string[key]).toBe(value); expect(Object.hasOwn(row.attrs_string, key)).toBe(true);
        expect(Object.hasOwn(row.attrs_number, key)).toBe(false); expect(Object.hasOwn(row.attrs_bool, key)).toBe(false);
      }
      expect(JSON.parse(row.extra).metadata).toEqual({ call_execution_id: child ? seed.childCallId : seed.callId });
      // Native resource_attrs JSON expands dotted resource paths; service_name is also checked above.
      expect(JSON.parse(row.resource)).toMatchObject({ project_type: 'observe', project_name: projectNames[seed.project],
        service: { name: projectNames[seed.project] }, fi: { org_id: actors[seed.project].organizationId, project_id: projectIds[seed.project] } });
      expect(Object.hasOwn(row.attrs_string, foreignKey)).toBe(seed.project === 2);
      if (seed.project === 2) expect(row.attrs_string[foreignKey]).toBe('foreign');
      expect(Object.hasOwn(row.attrs_number, foreignKey)).toBe(false); expect(Object.hasOwn(row.attrs_bool, foreignKey)).toBe(false);
    }
    expect(initialSpans.find(row => row.id === seeds[0].spanIds[0])!.attrs_number[TURNS]).toBe(EXPECTED_A_ROOT_TURNS);
  } finally { await attach('source-identities', { initialProjects, expectedSpans, initialSpans }); }
  const expectedTraces = sorted(seeds.map(seed => ({ id: seed.traceId, project_id: projectIds[seed.project], is_deleted: 0 })));
  try {
    await expect.poll(async () => {
      initialTraces = await probe.ch<Trace>(traceSql, params);
      return sorted(initialTraces.map(({ id, project_id, is_deleted }) => ({ id, project_id, is_deleted })));
    }, POLL.SPAN_VISIBLE).toEqual(expectedTraces);
    for (const row of initialTraces) expect(BigInt(row.version)).toBeGreaterThan(0n);
  } finally { await attach('curated-traces', { expectedTraces, initialTraces }); }
  // Freeze PG timestamps after the public producer, not before ingestion has
  // had its allowed opportunity to update its own project. Identities stay exact.
  const postIngestionProjects = await probe.pg<Project>(projectSql, [projectNames, projectIds]);
  expect(postIngestionProjects.map(({ created_at, updated_at, ...row }) => row))
    .toEqual(initialProjects.map(({ created_at, updated_at, ...row }) => row));
  await attach('post-ingestion-projects', { created: initialProjects, postIngestionProjects });
  initialProjects = postIngestionProjects;

  // source_adapters.py / observed reader: exact typed catalog identities, no aliases.
  const numeric = [DURATION, TURNS].map(name => ({ property_id: `custom_attribute:${name}`, property_kind: 'custom_attribute',
    category: 'custom_attribute', source: 'traces', name, display_name: name, type: 'number', output_type: 'number', role: 'metric',
    data_type: 'number', attribute_types: ['number'], attribute_types_exact: false,
    allowed_aggregations: ['avg', 'count', 'count_distinct', 'max', 'min', 'sum'] }));
  const projectProperty: Property = { property_id: 'system_attribute:traces:project', property_kind: 'system_attribute', category: 'system_metric',
    name: 'project', display_name: 'Project', source: 'traces', type: 'string', output_type: 'string', role: 'dimension' };
  const kindProperty: Property = { ...projectProperty, property_id: 'system_attribute:traces:span_kind', name: 'span_kind', display_name: 'Span Kind' };
  const callProperty: Property = { property_id: `custom_attribute:${CALL_TYPE}`, property_kind: 'custom_attribute', category: 'custom_attribute',
    name: CALL_TYPE, display_name: CALL_TYPE, source: 'traces', type: 'string', output_type: 'string', role: 'dimension',
    data_type: 'string', attribute_types: ['string'], attribute_types_exact: false, allowed_aggregations: ['count', 'count_distinct'] };
  const metrics: Metric[] = [DURATION, TURNS].map(name => ({ id: name, name, property_id: `custom_attribute:${name}`, display_name: name,
    type: 'custom_attribute', source: 'traces', aggregation: 'sum', attribute_key: name, attribute_type: 'number' }));
  const filter = (property: Property, values: string[]): Filter => ({ column_id: property.name, property_id: property.property_id,
    display_name: property.display_name, source: 'traces', output_type: 'string', filter_config: {
      filter_type: 'text', filter_op: 'in', filter_value: values, col_type: property.property_kind === 'custom_attribute' ? 'SPAN_ATTRIBUTE' : 'SYSTEM_METRIC',
      ...(property.property_kind === 'custom_attribute' ? { attribute_value_types: values.map(() => 'string') } : {}) } });
  // WidgetEditorView buildQueryConfig: top-level project_ids stays [], typed membership.
  const configFor = (kind?: string, types?: string[], grouped = false, project: string | null = projectIds[0]): Config => ({
    project_ids: [], time_range: { preset: '7D' }, granularity: 'day', metrics,
    filters: [...(project === null ? [] : [filter(projectProperty, [project])]), ...(kind ? [filter(kindProperty, [kind])] : []),
      ...(types ? [filter(callProperty, types)] : [])],
    breakdowns: grouped ? [{ name: CALL_TYPE, property_id: `custom_attribute:${CALL_TYPE}`, display_name: CALL_TYPE,
      type: 'custom_attribute', source: 'traces', attribute_type: 'string' }] : [] });
  const canonical = configFor('conversation', ['inbound', 'outbound'], true);
  const both: Groups = { inbound: [12, 4], outbound: [25, 8] };
  const catalogMetadata = { query_complete: true, query_exact: false, query_status: 'complete', query_provenance: 'current_property_catalog' };
  const receipts: Receipt<unknown>[] = [], scopeReceipts: unknown[] = [], pending = new Set<Promise<void>>();
  const requests = new Map<Request, Receipt<unknown>>();
  const expectedScope = { organizationId: actor.organizationId, workspaceId: actor.workspaceId };
  const context = await scopeActors.openContext(browser, actor);
  try {
    const page = await context.newPage(); page.setDefaultTimeout(UI_READY);
    const locale = await page.evaluate(() => ({ timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone, numbers: Intl.NumberFormat().resolvedOptions().locale }));
    const onRequest = (outgoing: Request) => {
      const url = new URL(outgoing.url()), path = url.pathname, method = outgoing.method();
      if (url.origin !== new URL(E2E.apiUrl).origin || !['POST', 'PATCH', 'PUT'].includes(method) ||
        !([METRICS, VALUES, QUERY, DASHBOARDS].includes(path) || /^\/tracer\/dashboard\/[0-9a-f-]+\/(?:widgets\/(?:[0-9a-f-]+\/)?)?$/.test(path))) return;
      const headers = outgoing.headers();
      const receipt: Receipt<unknown> = { path, method, input: {}, status: 0, startedAt: Date.now(), endedAt: 0, settled: false,
        scope: { organizationId: headers['x-organization-id'], workspaceId: headers['x-workspace-id'] }, authorized: /^Bearer \S+$/.test(headers.authorization ?? '') };
      try { receipt.input = outgoing.postDataJSON() as Wire; } catch { receipt.error = 'request_json_unreadable'; }
      requests.set(outgoing, receipt); receipts.push(receipt);
    };
    const onResponse = (response: Response) => {
      const receipt = requests.get(response.request()); if (!receipt) return;
      Object.assign(receipt, { status: response.status(), endedAt: Date.now(), requestId: response.headers()['x-request-id'], contentType: response.headers()['content-type'] ?? '' });
      // Head is retained before reading JSON; no HTML/error-object dump or detached rejection.
      const capture = (async () => {
        try {
          await attach('native-http-head', { ...receipt });
          if (!/\bapplication\/(?:[\w.-]+\+)?json\b/i.test(receipt.contentType!)) { receipt.error = 'non_json_body_omitted'; return; }
          receipt.body = await response.json();
        } catch { receipt.error = 'response_json_unreadable'; }
        finally { receipt.endedAt = Date.now(); receipt.settled = true; }
      })();
      pending.add(capture); void capture.finally(() => pending.delete(capture));
    };
    const onFailed = (outgoing: Request) => {
      const receipt = requests.get(outgoing); if (receipt) Object.assign(receipt, { error: 'request_failed', settled: true, endedAt: Date.now() });
    };
    page.on('request', onRequest); page.on('response', onResponse); page.on('requestfailed', onFailed);
    const readNative = async <T>(path: string, input: Wire | undefined, since = 0, method = 'POST', complete?: (body: T) => boolean): Promise<Receipt<T>> => {
      const matches = (r: Receipt<unknown>) => r.path === path && r.method === method && r.startedAt >= since &&
        (input === undefined || isDeepStrictEqual(r.input, input)) && r.settled &&
        Boolean(r.error || r.status !== 200 || !complete || complete(r.body as T));
      await expect.poll(() => receipts.some(matches), { timeout: UI_READY }).toBe(true);
      const matching = receipts.filter(matches);
      const receipt = (matching.find(r => r.error || r.status !== 200) ?? matching.at(-1)!) as Receipt<T>;
      await attach(`native-${path.split('/').filter(Boolean).at(-1)}`, receipt);
      expect(receipt.error).toBeUndefined(); expect(receipt.status).toBe(200); expect(receipt.scope).toEqual(expectedScope); expect(receipt.authorized).toBe(true);
      return receipt;
    };
    const globalSection = page.locator('.filter-section-title').locator('../..');
    const card = (name: string) => globalSection.getByText(name, { exact: true }).locator('../..');
    const popup = page.getByRole('tooltip').filter({ has: page.getByPlaceholder('Search...', { exact: true }) });
    const searchBox = popup.getByPlaceholder('Search...', { exact: true });
    const optionRow = (label: string) => popup.locator(`p[title="${label}"]`).locator('..');
    const selectCategory = (name: 'All' | 'Traces' | 'Trace Attributes') => page.getByLabel(new RegExp(`^${name} property count: `))
      .locator('..').getByText(name, { exact: true }).click();
    let dashboardId = '', savedWidget: Widget | undefined, originalChart: Widget['chart_config'] | undefined, lastBuckets: number[] = [];
    const assertCatalog = async (property: Property, metricMode: boolean, category: 'All' | 'Traces' | 'Trace Attributes') => {
      const input = { cursor_mode: true, search: property.display_name, ...(metricMode ? { role: 'metric' } : {}),
        ...(category === 'All' ? {} : { source: 'traces', category: category === 'Traces' ? 'system_metric' : 'custom_attribute' }) };
      // runtime_limits.js allows the native search page size to be configured.
      const matches = (r: Receipt<unknown>) => r.path === METRICS && r.method === 'POST' && r.settled &&
        isDeepStrictEqual(r.input, { ...input, page_size: r.input.page_size });
      await expect.poll(() => receipts.some(matches), { timeout: UI_READY }).toBe(true);
      const pageSize = receipts.filter(matches).at(-1)!.input.page_size;
      expect(Number.isInteger(pageSize)).toBe(true); expect(Number(pageSize)).toBeGreaterThan(0);
      const receipt = await readNative<CatalogBody>(METRICS, { ...input, page_size: pageSize });
      expect(receipt.body!.result).toMatchObject({ ...catalogMetadata, has_more: false, next_cursor: null });
      const selected = receipt.body!.result.metrics.filter(row => row.property_id === property.property_id);
      expect(selected).toHaveLength(1); expect(selected[0]).toMatchObject(property);
      if (property.name === TURNS) expect(selected[0].data_type).toBe(EXPECTED_CATALOG_TYPE);
    };
    const chooseProperty = async (property: Property, breakdown = false) => {
      await page.locator(breakdown ? '.breakdown-section-title' : '.filter-section-title').click();
      const custom = property.property_kind === 'custom_attribute', category = custom ? 'Trace Attributes' : 'Traces';
      await selectCategory(category);
      await page.getByPlaceholder(breakdown ? 'Search breakdown attributes...' : 'Search filter attributes...').fill(property.display_name);
      await assertCatalog(property, false, category);
      await page.getByRole('button', { name: `${property.display_name} (string, ${custom ? 'string, ' : ''}Traces)`, exact: true }).click();
    };
    const valueInput = (property: Property, search = '', project = ''): Wire => ({ property_id: property.property_id,
      metric_name: property.name, metric_type: property.property_kind === 'custom_attribute' ? 'custom_attribute' : 'system_metric',
      source: 'traces', project_ids: project, page_size: 10, ...(search ? { search } : {}),
      ...(property.property_kind === 'custom_attribute' ? { attribute_type: 'string' } : {}) });
    const callOptions = (labels: string[]): Value[] => labels.map(value => ({ value, label: value, type: 'string' }));
    const kindOptions = ['chain', 'conversation', 'llm'].map(value => ({ value, label: value }));
    const assertTypedValues = (body: ValueBody, expected: Value[]) => {
      expect(body.result).toMatchObject({ ...catalogMetadata, has_more: false, next_cursor: null, browse_status: 'exhausted',
        attribute_types: expected.length ? ['string'] : [], attribute_types_exact: false, attribute_type: 'string' });
      expect(sorted(body.result.values)).toEqual(sorted(expected));
    };
    const searchValues = async (property: Property, search: string, expected: Value[]) => {
      await uiExpect(popup).toHaveCount(1); await uiExpect(popup).toBeVisible();
      await searchBox.fill(search); await uiExpect(searchBox).toHaveValue(search);
      const input = valueInput(property, search);
      if (property.name === 'project' || property.name === 'span_kind') {
        // Both approved system properties use physical time slices. Read only
        // the native sentinel's captured cursor chain, including empty pages.
        const root = await readNative<ValueBody>(VALUES, input);
        const window = { start: root.body!.result.query_window_start, end: root.body!.result.query_window_end };
        const chain: Receipt<ValueBody>[] = [], options: Value[] = [], cursors = new Set<string>();
        let receipt = root;
        try {
          while (true) {
            chain.push(receipt);
            const result = receipt.body!.result;
            expect(result).toMatchObject({ query_complete: true, query_status: 'complete',
              query_window_start: window.start, query_window_end: window.end });
            expect(Number.isFinite(Date.parse(window.start!))).toBe(true);
            expect(Date.parse(window.end!)).toBeGreaterThan(Date.parse(window.start!));
            for (const option of result.values) {
              expect(options.some(previous => previous.value === option.value), 'duplicate native value identity').toBe(false);
              options.push(option);
            }
            if (result.has_more === false) {
              expect(result).toMatchObject({ has_more: false, next_cursor: null, browse_status: 'exhausted' }); break;
            }
            expect(result).toMatchObject({ has_more: true, browse_status: 'continuation' });
            const cursor = result.next_cursor;
            expect(typeof cursor).toBe('string'); expect(cursor!.length).toBeGreaterThan(0);
            expect(cursors.has(cursor!), 'repeated native cursor').toBe(false); cursors.add(cursor!);
            receipt = await readNative<ValueBody>(VALUES, { ...input, cursor }, receipt.startedAt);
          }
          expect(sorted(options)).toEqual(sorted(expected));
        } finally { await attach(`native-${property.name}-value-chain`, { input, window, chain, options, expected }); }
      } else {
        const receipt = await readNative<ValueBody>(VALUES, input);
        assertTypedValues(receipt.body!, expected);
      }
      await uiExpect(popup.getByRole('progressbar')).toHaveCount(0);
      const rows = popup.locator('p[title]').filter({ hasNotText: /^Specify: / });
      await uiExpect(rows).toHaveCount(expected.length);
      await expect.poll(async () => (await rows.evaluateAll(elements => elements.map(element => element.getAttribute('title')))).sort(),
        { timeout: UI_READY }).toEqual(expected.map(option => option.label).sort());
    };
    const checkOption = async (label: string, wasChecked: boolean) => {
      const row = optionRow(label); await uiExpect(row).toHaveCount(1);
      await uiExpect(row.getByRole('checkbox')).toBeChecked({ checked: wasChecked });
      await row.click(); await uiExpect(row.getByRole('checkbox')).toBeChecked({ checked: !wasChecked });
    };
    const replaceKind = async (previous: string, next: string) => {
      await uiExpect(popup).toHaveCount(0); await card('Span Kind').locator('.filter-value-name').locator('..').click();
      await searchValues(kindProperty, '', kindOptions); await checkOption(previous, true);
      await uiExpect(popup.getByRole('button', { name: 'Add', exact: true })).toBeDisabled();
      await checkOption(next, false); await popup.getByRole('button', { name: 'Add', exact: true }).click();
    };
    const assertResult = (receipt: Receipt<QueryBody>, expected: Config, groups: Groups, reopened = false): number[] => {
      expect(receipt.error).toBeUndefined(); expect(receipt.status).toBe(200); expect(receipt.input).toEqual(expected);
      const result = receipt.body!.result;
      expect(result).toMatchObject({ query_complete: true, query_exact: true, granularity: 'day' });
      const from = Date.parse(result.time_range.start), to = Date.parse(result.time_range.end);
      expect(to - from).toBe(7 * DAY); expect(to).toBeGreaterThanOrEqual(receipt.startedAt - 1000);
      expect(to).toBeLessThanOrEqual(receipt.endedAt + 1000); expect(at).toBeGreaterThan(from); expect(at).toBeLessThan(to);
      const buckets: number[] = []; for (let day = Math.floor(from / DAY) * DAY; day <= to; day += DAY) buckets.push(day);
      expect(result.metrics.map(({ id, name, unit, aggregation }) => ({ id, name, unit, aggregation })))
        .toEqual([DURATION, TURNS].map(name => ({ id: name, name, unit: '', aggregation: 'sum' })));
      for (const [index, metric] of result.metrics.entries()) {
        expect(metric).toMatchObject({ query_complete: true, query_exact: true }); expect(metric).not.toHaveProperty('error');
        expect(metric.series.map(series => series.name).sort()).toEqual(Object.keys(groups).sort());
        for (const series of metric.series) {
          expect(series.data.map(point => Date.parse(point.timestamp))).toEqual(buckets);
          expect(series.data.map(point => point.value)).toEqual(buckets.map(bucket => bucket === plantedDay ? groups[series.name][index] : null));
          if (reopened && metric.id === DURATION && series.name === 'outbound')
            expect(series.data.find(point => Date.parse(point.timestamp) === plantedDay)!.value).toBe(EXPECTED_REOPENED_OUTBOUND_DURATION);
        }
      }
      return buckets;
    };
    const assertTable = async (groups: Groups, buckets: number[], tile = false) => {
      const columns = Object.fromEntries([DURATION, TURNS].flatMap((metric, index) => Object.entries(groups).map(([group, vector]) => [
        `${metric}${group === 'total' ? '' : ` / ${group}`} (sum)`, buckets.map(bucket => bucket === plantedDay ? vector[index] : null),
      ]))) as Record<string, (number | null)[]>;
      const table = tile ? page.locator(`[data-widget-id="${savedWidget!.id}"]`).getByRole('table') : page.getByRole('table');
      await uiExpect(table).toBeVisible();
      await expect.poll(async () => (await table.locator('thead th').allTextContents()).slice(1).sort(), { timeout: UI_READY }).toEqual(Object.keys(columns).sort());
      const headers = await table.locator('thead th').allTextContents(); expect(headers[0]).toBe('Time');
      await uiExpect(table.locator('tbody tr')).toHaveCount(buckets.length);
      await uiExpect(table.locator('tbody tr td:first-child')).toHaveText(buckets.map(bucket =>
        new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: locale.timeZone }).format(bucket)));
      for (const [index, name] of headers.slice(1).entries()) await uiExpect(table.locator(`tbody tr td:nth-child(${index + 2})`))
        .toHaveText(columns[name].map(value => value === null ? '-' : tile ? value.toFixed(2) : value >= 1000
          ? new Intl.NumberFormat(locale.numbers, { maximumFractionDigits: 0 }).format(value) : String(value)));
    };
    const queryAfter = async (label: string, config: Config, groups: Groups, action: () => Promise<unknown>, tile = false, reopened = false) => {
      const since = Date.now(); await action();
      const receipt = await readNative<QueryBody>(QUERY, config, since, 'POST', body => body?.result?.query_complete === true);
      lastBuckets = assertResult(receipt, config, groups, reopened);
      await attach(`${label}-query`, { receipt, config, groups }); await assertTable(groups, lastBuckets, tile);
      await testInfo.attach(`${label}-table`, { contentType: 'image/png', body: await page.screenshot() });
    };
    const assertControls = async (types: string[]) => {
      for (const [name, value] of [['Project', projectNames[0]], ['Span Kind', 'conversation'], [CALL_TYPE, types[0]]]) {
        await uiExpect(card(name).getByRole('combobox')).toHaveText('Is');
        await uiExpect(card(name).locator('.filter-value-name')).toHaveText(value);
      }
      if (types.length === 2) await uiExpect(card(CALL_TYPE).getByText('+1 call_type', { exact: true })).toBeVisible();
      for (const name of [DURATION, TURNS]) {
        const metricCard = page.locator(`p[title="${name}"]`).locator('../..');
        await uiExpect(metricCard.getByText('Sum', { exact: true })).toBeVisible();
        for (const filterName of ['Project', 'Span Kind', CALL_TYPE]) await uiExpect(metricCard.getByText(filterName, { exact: true })).toHaveCount(0);
      }
      await uiExpect(page.locator('.breakdown-section-title').locator('../..').getByText(CALL_TYPE, { exact: true })).toBeVisible();
      await uiExpect(page.getByRole('combobox').filter({ hasText: 'Day' })).toBeVisible();
      await uiExpect(page.getByText('7D', { exact: true })).toBeVisible();
    };
    const verifySaved = async () => {
      const widget = savedWidget!;
      const rows = await probe.pg('SELECT id,name,dashboard_id,created_by_id,query_config,chart_config,deleted FROM tracer_dashboardwidget WHERE dashboard_id=$1 ORDER BY id', [dashboardId]);
      expect(rows).toEqual([{ id: widget.id, name: widgetName, dashboard_id: dashboardId, created_by_id: actor.userId,
        query_config: canonical, chart_config: originalChart, deleted: false }]);
      const dashboards = await probe.pg('SELECT id,name,workspace_id,created_by_id,deleted FROM tracer_dashboard WHERE id=$1', [dashboardId]);
      expect(dashboards).toEqual([{ id: dashboardId, name: dashboardName, workspace_id: actor.workspaceId, created_by_id: actor.userId, deleted: false }]);
      const detail = await actor.api.get<{ result: { id: string; name: string; workspace: string; widgets: Widget[] } }>(`${DASHBOARDS}${dashboardId}/`);
      expect(detail.result).toMatchObject({ id: dashboardId, name: dashboardName, workspace: actor.workspaceId });
      expect(detail.result.widgets.map(({ id, name, query_config, chart_config }) => ({ id, name, query_config, chart_config })))
        .toEqual([{ id: widget.id, name: widgetName, query_config: canonical, chart_config: originalChart }]);
      for (const selected of [actor, scopeActors.ownerB, emptyActor]) {
        const path = `${DASHBOARDS}${dashboardId}/widgets/${widget.id}/`;
        const response = await scopeActors.send<Widget>(selected, 'GET', path);
        scopeReceipts.push({ path, organizationId: selected.organizationId, workspaceId: selected.workspaceId, ...response });
        expect(response.status).toBe(selected === actor ? 200 : 404);
        if (selected === actor) { // Inherited nested retrieve is RAW, not result-wrapped.
          expect(response.body).toMatchObject({ id: widget.id, name: widgetName });
          expect(response.body.query_config).toEqual(canonical); expect(response.body.chart_config).toEqual(originalChart);
        }
      }
      await attach('saved-widget-PG-GET', { rows, dashboards, detail, widget });
    };
    try {
      await test.step('1 create and name a Table widget with 7D and Day', async () => {
        await page.goto('/dashboard/dashboards', { waitUntil: 'domcontentloaded' });
        let since = Date.now(); await page.getByRole('button', { name: 'Create Dashboard', exact: true }).click();
        const created = await readNative<{ result: { id: string } }>(DASHBOARDS, undefined, since);
        dashboardId = created.body!.result.id; expect(dashboardId).toMatch(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/);
        await attach('dashboard-id', { dashboardId, dashboardName, widgetName });
        await page.getByRole('heading', { name: 'Untitled', exact: true }).click();
        await page.getByPlaceholder('Untitled Dashboard').fill(dashboardName); since = Date.now();
        await page.getByPlaceholder('Untitled Dashboard').press('Enter');
        const namedPath = `${DASHBOARDS}${dashboardId}/`;
        await expect.poll(() => receipts.some(r => r.path === namedPath && ['PATCH', 'PUT'].includes(r.method) && r.startedAt >= since && r.settled), { timeout: UI_READY }).toBe(true);
        const namedMethod = receipts.filter(r => r.path === namedPath && r.startedAt >= since).at(-1)!.method;
        const named = await readNative<unknown>(namedPath, undefined, since, namedMethod); expect(named.input).toMatchObject({ name: dashboardName });
        await page.getByRole('button', { name: 'Add Widget', exact: true }).first().click();
        await page.getByText('Untitled widget', { exact: true }).click(); await page.getByPlaceholder('Untitled widget').fill(widgetName);
        await page.getByPlaceholder('Untitled widget').press('Enter');
        await page.getByRole('combobox').filter({ hasText: 'Line' }).click(); await page.getByRole('option', { name: 'Table', exact: true }).click();
        await page.getByText('7D', { exact: true }).click(); await page.getByRole('combobox').filter({ hasText: 'Day' }).click();
        await page.getByRole('option', { name: 'Day', exact: true }).click();
      }, { timeout: UI_READY });

      await test.step('2 discover typed custom call metrics and select Sum with scope checks', async () => {
        for (const [index, property] of numeric.entries()) {
          if (index) await page.locator('.metric-section-title').click(); else await page.getByText('Select Metric', { exact: true }).click();
          for (const category of ['All', 'Trace Attributes'] as const) {
            await selectCategory(category); await page.getByPlaceholder('Search metrics...').fill('');
            await page.getByPlaceholder('Search metrics...').fill(property.name); await assertCatalog(property, true, category);
            await uiExpect(page.getByRole('button', { name: `${property.name} (number, number, Traces)`, exact: true })).toBeVisible();
          }
          await page.getByRole('button', { name: `${property.name} (number, number, Traces)`, exact: true }).click();
          await page.locator(`p[title="${property.name}"]`).locator('../..').locator('.MuiChip-clickable').click();
          await page.getByText('Sum', { exact: true }).last().click();
        }
        for (const selected of [actor, scopeActors.ownerB, emptyActor]) {
          const foreign = selected === scopeActors.ownerB;
          const input = { cursor_mode: true, category: 'custom_attribute', source: 'traces', search: foreignKey, page_size: 25 };
          const response = await scopeActors.send<CatalogBody>(selected, 'POST', METRICS, input);
          scopeReceipts.push({ path: METRICS, input, organizationId: selected.organizationId, workspaceId: selected.workspaceId, ...response });
          expect(response.status).toBe(200); expect(response.body.result).toMatchObject({ ...catalogMetadata, has_more: false, next_cursor: null });
          expect(response.body.result.metrics.map(row => row.property_id)).toEqual(foreign ? [`custom_attribute:${foreignKey}`] : []);
          if (foreign) expect(response.body.result.metrics[0]).toMatchObject({ ...callProperty, name: foreignKey, display_name: foreignKey, property_id: `custom_attribute:${foreignKey}` });
          const valuesInput = valueInput({ ...callProperty, property_id: `custom_attribute:${foreignKey}`, name: foreignKey });
          const found = await scopeActors.send<ValueBody>(selected, 'POST', VALUES, valuesInput);
          scopeReceipts.push({ path: VALUES, input: valuesInput, organizationId: selected.organizationId, workspaceId: selected.workspaceId, ...found });
          expect(found.status).toBe(200); assertTypedValues(found.body, foreign ? callOptions(['foreign']) : []);
        }
        for (const property of numeric) {
          const input = { cursor_mode: true, category: 'custom_attribute', source: 'traces', search: property.name, role: 'metric', page_size: 25 };
          const response = await scopeActors.send<CatalogBody>(scopeActors.ownerB, 'POST', METRICS, input);
          scopeReceipts.push({ path: METRICS, input, organizationId: scopeActors.ownerB.organizationId, workspaceId: scopeActors.ownerB.workspaceId, ...response });
          expect(response.status).toBe(200); expect(response.body.result).toMatchObject({ ...catalogMetadata, has_more: false, next_cursor: null });
          const rows = response.body.result.metrics.filter(row => row.property_id === property.property_id);
          expect(rows).toHaveLength(1); expect(rows[0]).toMatchObject(property);
        }
      }, { timeout: UI_READY });

      await test.step('3 select primary Project through its native chain and verify source scopes', async () => {
        await queryAfter('primary-all', configFor(), { total: [13837, 5252] }, async () => {
          await chooseProperty(projectProperty); await card('Project').getByText('Select value...', { exact: true }).click();
          await searchValues(projectProperty, projectNames[0], [{ value: projectIds[0], label: projectNames[0] }]);
          await checkOption(projectNames[0], false); await popup.getByRole('button', { name: 'Add', exact: true }).click();
        });
        for (const control of [
          { selected: actor, project: projectIds[1], vector: [12000, 4000] as Vector, label: 'sibling' },
          { selected: scopeActors.ownerB, project: projectIds[2], vector: [25000, 8000] as Vector, label: 'foreign' },
          { selected: emptyActor, project: null, vector: [null, null] as Vector, label: 'empty' },
        ]) {
          const input = configFor('conversation', undefined, false, control.project), startedAt = Date.now();
          const response = await scopeActors.send<QueryBody>(control.selected, 'POST', QUERY, input);
          const receipt: Receipt<QueryBody> = { ...response, input, path: QUERY, method: 'POST', startedAt, endedAt: Date.now(), settled: true,
            scope: { organizationId: control.selected.organizationId, workspaceId: control.selected.workspaceId }, authorized: true };
          scopeReceipts.push(receipt); await attach(`positive-${control.label}`, receipt);
          assertResult(receipt, input, { total: control.vector });
        }
      }, { timeout: UI_READY });

      await test.step('4 select conversation, contrast LLM-only values and restore conversation', async () => {
        await queryAfter('conversation', configFor('conversation'), { total: [37, 12] }, async () => {
          await chooseProperty(kindProperty); // String defaults auto-open this popup; no second trigger.
          await searchValues(kindProperty, '', kindOptions); await checkOption('conversation', false);
          await popup.getByRole('button', { name: 'Add', exact: true }).click();
        });
        await queryAfter('llm-only', configFor('llm'), { total: [13700, 5200] }, () => replaceKind('conversation', 'llm'));
        await queryAfter('conversation-restored', configFor('conversation'), { total: [37, 12] }, () => replaceKind('llm', 'conversation'));
      }, { timeout: UI_READY });

      await test.step('5 select typed call_type, compare both groups and retain canonical membership', async () => {
        await queryAfter('inbound-ungrouped', configFor('conversation', ['inbound']), { total: [12, 4] }, async () => {
          await chooseProperty(callProperty); await searchValues(callProperty, '', callOptions(['inbound', 'outbound']));
          await checkOption('inbound', false); await popup.getByRole('button', { name: 'Add', exact: true }).click();
        });
        await queryAfter('inbound-grouped', configFor('conversation', ['inbound'], true), { inbound: [12, 4] }, () => chooseProperty(callProperty, true));
        await assertControls(['inbound']);
        await queryAfter('outbound-grouped', configFor('conversation', ['outbound'], true), { outbound: [25, 8] }, async () => {
          await card(CALL_TYPE).locator('.filter-value-name').locator('..').click();
          await searchValues(callProperty, 'inbound', callOptions(['inbound'])); await checkOption('inbound', true);
          await uiExpect(popup.getByRole('button', { name: 'Add', exact: true })).toBeDisabled();
          await searchValues(callProperty, 'outbound', callOptions(['outbound'])); await checkOption('outbound', false);
          await popup.getByRole('button', { name: 'Add', exact: true }).click();
        });
        await assertControls(['outbound']);
        await queryAfter('both-grouped', canonical, both, async () => {
          await card(CALL_TYPE).locator('.filter-value-name').locator('..').click();
          await searchValues(callProperty, '', callOptions(['inbound', 'outbound'])); await checkOption('outbound', true);
          await uiExpect(popup.getByRole('button', { name: 'Add', exact: true })).toBeDisabled();
          await checkOption('inbound', false); await checkOption('outbound', false);
          await popup.getByRole('button', { name: 'Add', exact: true }).click();
        });
        await assertControls(['inbound', 'outbound']);
        for (const control of [
          { selected: actor, project: '', labels: ['inbound', 'outbound'] },
          { selected: actor, project: projectIds[0], labels: ['inbound', 'outbound'] },
          { selected: actor, project: projectIds[1], labels: ['inbound'] },
          { selected: scopeActors.ownerB, project: '', labels: ['inbound'] },
          { selected: scopeActors.ownerB, project: projectIds[2], labels: ['inbound'] },
          { selected: emptyActor, project: '', labels: [] },
        ]) {
          const input = valueInput(callProperty, '', control.project);
          const response = await scopeActors.send<ValueBody>(control.selected, 'POST', VALUES, input);
          scopeReceipts.push({ path: VALUES, input, organizationId: control.selected.organizationId, workspaceId: control.selected.workspaceId, ...response });
          expect(response.status).toBe(200); assertTypedValues(response.body, callOptions(control.labels));
        }
      }, { timeout: UI_READY });

      await test.step('6 save canonical binding and verify persisted Table and ownership', async () => {
        const since = Date.now(); await page.getByRole('button', { name: 'Save', exact: true }).click();
        const saved = await readNative<{ result: Widget }>(`${DASHBOARDS}${dashboardId}/widgets/`, undefined, since);
        expect(saved.input.name).toBe(widgetName); expect(saved.input.query_config).toEqual(canonical);
        originalChart = structuredClone(saved.input.chart_config) as Widget['chart_config']; expect(originalChart.chart_type).toBe('table');
        savedWidget = saved.body!.result;
        expect(savedWidget.id).toMatch(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/);
        expect(savedWidget.name).toBe(widgetName); expect(savedWidget.query_config).toEqual(canonical); expect(savedWidget.chart_config).toEqual(originalChart);
        await verifySaved(); await uiExpect(page).toHaveURL(new RegExp(`/dashboard/dashboards/${dashboardId}$`));
        await assertTable(both, lastBuckets, true); await testInfo.attach('saved-table', { contentType: 'image/png', body: await page.screenshot() });
      }, { timeout: UI_READY });

      await test.step('7 reload saved dashboard and verify the exact four series', async () => {
        await queryAfter('reload', canonical, both, () => page.reload({ waitUntil: 'domcontentloaded' }), true);
      }, { timeout: UI_READY });

      await test.step('8 reopen exact controls and values and conserve source versions', async () => {
        await queryAfter('reopen', canonical, both, () => page.locator(`[data-widget-id="${savedWidget!.id}"]`).getByText(widgetName, { exact: true }).click(), false, true);
        await assertControls(['inbound', 'outbound']); await card(CALL_TYPE).locator('.filter-value-name').locator('..').click();
        await searchValues(callProperty, '', callOptions(['inbound', 'outbound']));
        for (const label of ['inbound', 'outbound']) await uiExpect(optionRow(label).getByRole('checkbox')).toBeChecked();
        await testInfo.attach('reopened-controls', { contentType: 'image/png', body: await page.screenshot() });
        await popup.getByRole('button', { name: 'Add', exact: true }).click(); await uiExpect(popup).toHaveCount(0);
        await verifySaved();
        const [finalProjects, finalSpans, finalTraces] = await Promise.all([
          probe.pg<Project>(projectSql, [projectNames, projectIds]), probe.ch<Span>(spanSql, params), probe.ch<Trace>(traceSql, params),
        ]);
        await attach('unchanged-source-and-versions', { initialProjects, finalProjects, initialSpans, finalSpans, initialTraces, finalTraces });
        expect(finalProjects).toEqual(initialProjects); expect(finalSpans).toEqual(initialSpans); expect(finalTraces).toEqual(initialTraces);
        expect(savedWidget!.query_config).toEqual(canonical); expect(savedWidget!.chart_config).toEqual(originalChart);
      }, { timeout: UI_READY });
    } finally {
      page.off('request', onRequest); page.off('response', onResponse); page.off('requestfailed', onFailed);
      await attach('native-request-states', receipts.map(({ body, ...head }) => head));
      await Promise.all(pending); await attach('native-reads-writes-scope', { receipts, scopeReceipts });
    }
  } finally { await context.close(); }
});
