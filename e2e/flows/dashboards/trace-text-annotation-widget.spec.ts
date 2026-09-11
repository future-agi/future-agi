import { randomBytes, randomUUID } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { request, type Request, type Response } from '@playwright/test';
import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { sendTrace } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// Pins: AnnotationSidebarContent/scores.js, annotation_queues.py, WidgetEditorView.
// Endpoints: api/scores/scores.js, hooks/useDashboards.js and the scoped DRF views.
// Native Count + Is only. Text aggregation-menu and presence-control gaps remain
// unqualified; neither their visibility nor a query error is a passing witness.
const LABELS = '/model-hub/annotations-labels/', QUEUES = '/model-hub/annotation-queues/';
const BULK = '/model-hub/scores/bulk/', LIST = '/tracer/trace/list_traces_of_session/';
const DASHBOARDS = '/tracer/dashboard/', METRICS = `${DASHBOARDS}metrics/`;
const VALUES = `${DASHBOARDS}filter_values/`, QUERY = `${DASHBOARDS}query/`;
// README Writing a flow: UI_READY; dashboard.py formats complete UTC-day buckets.
const UI_READY = 60_000, DAY = 86_400_000;
// Assertion-only anchors: not used by producers, readiness, config or matchers.
const EXPECTED_CATALOG_TEXT_TYPE = 'text'; // Check1 -> 'star', assertion only.
const EXPECTED_REOPENED_TEXT_COUNT = 1; // Check3 -> 2, fresh reopened A-series only.
type Wire = Record<string, unknown>;
type Scope = { organizationId: string; workspaceId: string };
type Receipt<T> = { path: string; method: string; input: Wire; scope: Scope; authorized: boolean;
  status: number; body?: T; error?: string; requestId?: string; contentType?: string;
  startedAt: number; endedAt: number; settled: boolean };
type Config = { project_ids: string[]; time_range: { preset: string }; granularity: string;
  metrics: Wire[]; filters: Wire[]; breakdowns: Wire[] };
type Result = { query_complete: boolean; query_exact: boolean; granularity: string; time_range: { start: string; end: string };
  metrics: { id: string; name: string; unit: string; aggregation: string; query_complete: boolean; query_exact: boolean;
    series: { name: string; data: { timestamp: string; value: number | null }[] }[] }[] };
type QueryBody = { result: Result };
type Property = { property_id: string; property_kind: string; category: string; name: string; display_name: string;
  source: string; type: string; output_type: string; role: string; data_type?: string; sources?: string[];
  choices?: string[]; choice_options?: Value[] };
type CatalogBody = { result: { metrics: Property[]; query_complete: boolean; has_more: boolean; next_cursor: string | null } };
type Value = { value: string; label: string };
type ValueBody = { result: { values: Value[]; query_complete: boolean; query_status: string; has_more: boolean;
  next_cursor: string | null; browse_status: string; query_window_start?: string; query_window_end?: string } };
type Project = { id: string; name: string; model_type: string; trace_type: string; source: string;
  organization_id: string; workspace_id: string; deleted: boolean; created_at: unknown; updated_at: unknown };
type Span = { id: string; trace_id: string; project_id: string; org_id: string; parent_span_id: string; name: string;
  observation_type: string; service_name: string; start_us: string; end_us: string; latency_ms: number; status: string;
  cost: number; is_deleted: number; trace_session_id: null; end_user_id: null; version: string;
  attrs_string: Record<string, string>; attrs_number: Record<string, number>; attrs_bool: Wire; extra: string; resource: string };
type Trace = { id: string; project_id: string; name: string; created_us: string; updated_us: string; version: string; is_deleted: number };
type Score = { id: string; label_id: string; source_type: string; trace_id: string; tracer_project_id: string;
  organization_id: string; workspace_id: string; annotator_id: string; queue_item_id: string; score_source: string; notes: string;
  observation_span_id: string | null; trace_session_id: string | null; call_execution_id: string | null;
  prototype_run_id: string | null; dataset_row_id: string | null; project_id: string | null;
  value: { text: string }; value_history: unknown[]; deleted: number; deleted_us: string | null; created_us: string; updated_us: string };
type CHScore = Omit<Score, 'value' | 'value_history'> & { value: string; value_history: string; version: string; peerdb_deleted: number };
type NativeScore = { id: string; source_type: string; source_id: string; label_id: string; label_name: string; label_type: string;
  label_settings: Wire; label_allow_notes: boolean; value: { text: string }; value_history: unknown[]; score_source: string;
  notes: string; annotator: string; queue_item: string; queue_id: string; created_at: string; updated_at: string };
type Queue = { id: string; name: string };
type Widget = { id: string; name: string; query_config: Config; chart_config: { chart_type: string; [key: string]: unknown } };
type Oracle = { value: number | null; traces: number | null; scoreDay: number; grouped: boolean };
const sorted = <T>(rows: T[]) => [...rows].sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));

test('DASH-E2E-012: a saved trace annotation widget retains exact text membership', {
  tag: ['@flow'],
  annotation: flowAnnotation({ id: 'DASH-E2E-012', area: 'dashboards',
    userGoal: 'A saved trace annotation widget retains exact text membership.',
    steps: [
      'submit three native text trace Scores in independently seeded primary, sibling and foreign projects',
      'create and name a Table widget with 7D and Day',
      'discover the text annotation and Traces metrics and explicitly choose Count and Distinct Count',
      'select primary Project through its native exhausted cursor chain and verify scoped unfiltered facts',
      'select native Is A and the same-label breakdown, prove disjoint B and substring C empty, and restore A',
      'save the exact binding and inspect its persisted Table and ownership',
      'reload the saved dashboard and read the exact independent-clock series',
      'reopen the same widget, verify controls and results and conserve source and Score versions',
    ],
    backendChecks: [
      "Native catalog and selected property identities preserve this flow's source, types, choices and actor scope.",
      "This flow's exact publicly produced source identities and typed latest facts are present and unchanged outside its authorized UI actions.",
      "The preview, saved binding and reopened widget equal this flow's independently specified filtered and grouped result.",
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe: probe }, testInfo) => {
  test.setTimeout(780_000); // A8: ASYNC60 + 2×SPAN15 + CDC180 + 8×UI60 + 30 headroom.
  const uiExpect = expect.configure({ timeout: UI_READY });
  const prefix = `e2e-dash12-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const actor = scopeActors.ownerA, foreignActor = scopeActors.ownerB;
  const emptyActor = scopeActors.withWorkspace(actor, scopeActors.emptyWorkspace.id);
  const actors = [actor, actor, foreignActor], projectNames = [`${prefix}-primary`, `${prefix}-sibling`, `${prefix}-foreign`];
  const labelName = `${prefix}-text`, dashboardName = `${prefix}-dashboard`, widgetName = `${prefix}-widget`;
  // Producer and independent expected literals are separate; B/C never enter a Score.
  const submittedText = `${prefix}-alpha`, placeholder = `${prefix}-enter-text`;
  const mintedA = `${prefix}-alpha`, mintedB = `${prefix}-beta`, mintedC = `${prefix}-alph`;
  const EXPECTED_STORED_PRIMARY_TEXT = `${prefix}-alpha`; // Check2 -> `${prefix}-beta`, assertion only.
  const plantedDay = Math.floor(Date.now() / DAY) * DAY - DAY, at = plantedDay + DAY / 2, start = BigInt(at) * 1_000_000n;
  // Input values and later result literals are deliberately separate.
  const seeds = [{ key: 'a', project: 0 }, { key: 'b', project: 0 },
    { key: 's', project: 1 }, { key: 'f', project: 2 }].map(seed => ({ ...seed,
    traceId: randomUUID(), spanIds: [randomBytes(8).toString('hex'), randomBytes(8).toString('hex')] }));
  expect(new Set(seeds.map(seed => seed.traceId)).size).toBe(4);
  for (const seed of seeds) expect(seed.traceId).not.toBe('00000000-0000-0000-0000-000000000000');
  expect(mintedA).not.toBe(mintedB); expect(mintedA).not.toBe(mintedC); expect(mintedA.startsWith(mintedC)).toBe(true);
  expect(new Set(seeds.flatMap(seed => seed.spanIds)).size).toBe(8);
  for (const id of seeds.flatMap(seed => seed.spanIds)) { expect(id).toMatch(/^[0-9a-f]{16}$/); expect(id).not.toBe('0000000000000000'); }
  const attach = (name: string, body: unknown) => testInfo.attach(name, { contentType: 'application/json', body: JSON.stringify(body) });
  await attach('minted-identities', { prefix, seeds, projectNames, labelName, mintedA, mintedB, mintedC,
    placeholder, start: String(start), actors: scopeActors.evidence().actors });
  const projectIds: string[] = [], labelIds: string[] = [], queues: Queue[] = [], publicReceipts: unknown[] = [];
  const projectSql = 'SELECT id,name,model_type,trace_type,source,organization_id,workspace_id,deleted,created_at,updated_at FROM tracer_project WHERE name=ANY($1) OR id=ANY($2::uuid[]) ORDER BY id';
  const labelSql = 'SELECT id,name,type,settings,allow_notes,project_id,organization_id,workspace_id,deleted,created_at,updated_at FROM model_hub_annotationslabels WHERE name=$1 OR id=ANY($2::uuid[]) ORDER BY id';
  const queueSql = 'SELECT id,name,project_id,organization_id,workspace_id,created_by_id,is_default,status,deleted FROM model_hub_annotationqueue WHERE project_id=ANY($1::uuid[]) OR id=ANY($2::uuid[]) ORDER BY id';
  // AnnotationQueueLabel.order exists (model:247, migration0060); add-label assigns max+1.
  const linkSql = 'SELECT id,queue_id,label_id,"order" AS label_order,required,deleted FROM model_hub_annotationqueuelabel WHERE queue_id=ANY($1::uuid[]) ORDER BY id';
  const itemSql = `SELECT id,queue_id,source_type,status,trace_id,observation_span_id,trace_session_id,call_execution_id,
    prototype_run_id,dataset_row_id,organization_id,workspace_id,deleted FROM model_hub_queueitem WHERE queue_id=ANY($1::uuid[]) ORDER BY id`;
  let initialProjects: Project[] = [], initialLabels: Wire[] = [], initialQueues: Wire[] = [], initialLinks: Wire[] = [];
  await test.step('public telemetry and scoped label/default-queue setup', async () => {
    const ingestion = await request.newContext();
    try {
      for (const seed of seeds) {
        const owner = actors[seed.project];
        const keys = scopeActors.owners.find(row => row.organizationId === owner.organizationId && row.workspaceId === owner.workspaceId)!;
        expect(await sendTrace(ingestion, { collectorUrl: E2E.collectorUrl, apiKey: keys.apiKey, secretKey: keys.secretKey,
          projectName: projectNames[seed.project], traceId: seed.traceId, rootSpanId: seed.spanIds[0], childSpanId: seed.spanIds[1],
          rootName: `${prefix}-${seed.key}-root`, childName: `${prefix}-${seed.key}-child`,
          startTimeUnixNano: start, endTimeUnixNano: start + 50_000_000n, resourceAttributes: { project_type: 'observe' },
          rootAttributes: { 'fi.span.kind': 'chain', 'gen_ai.cost.total': 0 },
          childAttributes: { 'fi.span.kind': 'llm', 'gen_ai.cost.total': 0 } }))
          .toEqual({ traceId: seed.traceId, spanIds: seed.spanIds, projectName: projectNames[seed.project] });
      }
    } finally { await ingestion.dispose(); }
    await expect.poll(async () => {
      initialProjects = await probe.pg<Project>(projectSql, [projectNames, projectIds]);
      return sorted(initialProjects.map(({ id, created_at, updated_at, ...row }) => row));
    }, POLL.ASYNC_JOB).toEqual(sorted(projectNames.map((name, index) => ({ name, model_type: 'GenerativeLLM', trace_type: 'observe',
      source: 'prototype', organization_id: actors[index].organizationId, workspace_id: actors[index].workspaceId, deleted: false }))));
    projectIds.push(...projectNames.map(name => initialProjects.find(row => row.name === name)!.id));
    expect(new Set(projectIds).size).toBe(3);
    // develop_annotations serializer: omit optional project, do not send null.
    for (const owner of [actor, foreignActor]) {
      const input = { name: labelName, type: 'text', settings: { placeholder, min_length: 0, max_length: 500 }, allow_notes: false };
      const response = await scopeActors.send<{ result: { id: string } }>(owner, 'POST', LABELS, input);
      publicReceipts.push({ path: LABELS, input, organizationId: owner.organizationId, workspaceId: owner.workspaceId, ...response });
      await attach('public-definitions', publicReceipts); expect(response.status).toBe(200);
      expect(response.body.result.id).toMatch(/^[0-9a-f-]{36}$/); labelIds.push(response.body.result.id);
      expect(response.body.result).toMatchObject(input);
    }
    expect(new Set(labelIds).size).toBe(2);
    for (const [index, project_id] of projectIds.entries()) {
      const response = await scopeActors.send<{ result: { queue: Queue } }>(actors[index], 'POST', `${QUEUES}get-or-create-default/`, { project_id });
      publicReceipts.push({ path: `${QUEUES}get-or-create-default/`, input: { project_id }, ...response });
      await attach('public-definitions', publicReceipts); expect(response.status).toBe(200);
      const queue = response.body.result.queue; expect(queue.id).toMatch(/^[0-9a-f-]{36}$/);
      expect(queue.name).toBe(`Default - ${projectNames[index]}`); queues.push(queue);
      const input = { label_id: labelIds[index === 2 ? 1 : 0] };
      const added = await scopeActors.send<unknown>(actors[index], 'POST', `${QUEUES}${queue.id}/add-label/`, input);
      publicReceipts.push({ path: `${QUEUES}${queue.id}/add-label/`, input, ...added });
      await attach('public-definitions', publicReceipts); expect(added.status).toBe(200);
    }
    expect(new Set(queues.map(row => row.id)).size).toBe(3);
    initialLabels = await probe.pg(labelSql, [labelName, labelIds]);
    await attach('initial-labels-PG', initialLabels);
    expect(sorted(initialLabels.map(({ created_at, updated_at, ...row }) => row))).toEqual(sorted([actor, foreignActor].map((owner, index) => ({
      id: labelIds[index], name: labelName, type: 'text', settings: { placeholder, min_length: 0, max_length: 500 },
      allow_notes: false, project_id: null, organization_id: owner.organizationId, workspace_id: owner.workspaceId, deleted: false }))));
    initialQueues = await probe.pg(queueSql, [projectIds, queues.map(row => row.id)]);
    await attach('initial-queues-PG', initialQueues);
    expect(sorted(initialQueues)).toEqual(sorted(queues.map((queue, index) => ({ id: queue.id, name: `Default - ${projectNames[index]}`,
      project_id: projectIds[index], organization_id: actors[index].organizationId, workspace_id: actors[index].workspaceId,
      created_by_id: actors[index].userId, is_default: true, status: 'active', deleted: false }))));
    initialLinks = await probe.pg(linkSql, [queues.map(row => row.id)]);
    await attach('initial-queue-links-PG', initialLinks);
    expect(sorted(initialLinks.map(({ id, ...row }) => row))).toEqual(sorted(queues.map((queue, index) => ({ queue_id: queue.id,
      label_id: labelIds[index === 2 ? 1 : 0], label_order: 1, required: false, deleted: false }))));
    expect(await probe.pg(itemSql, [queues.map(row => row.id)])).toEqual([]);
  }, { timeout: POLL.ASYNC_JOB.timeout });
  await attach('post-ingestion-projects-labels-queues', { initialProjects, initialLabels, initialQueues, initialLinks, projectIds, labelIds });
  // Source schema002, converter.go + adapter.Split: exact typed maps and native nested JSON.
  const params = { primary: projectIds[0], sibling: projectIds[1], foreign: projectIds[2] };
  const spanSql = `SELECT id,trace_id,project_id,org_id,parent_span_id,name,observation_type,service_name,
    toString(toUnixTimestamp64Micro(start_time)) AS start_us,toString(toUnixTimestamp64Micro(end_time)) AS end_us,
    latency_ms,status,cost,is_deleted,trace_session_id,end_user_id,toString(_version) AS version,
    attrs_string,attrs_number,attrs_bool,toString(attributes_extra) AS extra,toString(resource_attrs) AS resource
    FROM spans FINAL WHERE project_id IN ({primary:UUID},{sibling:UUID},{foreign:UUID}) ORDER BY id SETTINGS readonly=1,max_threads=1`;
  const traceSql = `SELECT id,project_id,name,toString(toUnixTimestamp64Micro(created_at)) AS created_us,toString(toUnixTimestamp64Micro(updated_at)) AS updated_us,
    toString(_version) AS version,is_deleted FROM traces FINAL
    WHERE project_id IN ({primary:UUID},{sibling:UUID},{foreign:UUID}) ORDER BY toString(id) SETTINGS readonly=1,max_threads=1`;
  const expectedSpans = sorted(seeds.flatMap(seed => seed.spanIds.map((id, child) => ({ id, trace_id: seed.traceId,
    project_id: projectIds[seed.project], org_id: actors[seed.project].organizationId, parent_span_id: child ? seed.spanIds[0] : '',
    name: `${prefix}-${seed.key}-${child ? 'child' : 'root'}`, observation_type: child ? 'llm' : 'chain',
    service_name: projectNames[seed.project], start_us: String(start / 1000n), end_us: String(start / 1000n + 50_000n),
    latency_ms: 50, status: 'OK', cost: 0, is_deleted: 0, trace_session_id: null, end_user_id: null }))));
  let initialSpans: Span[] = [], initialTraces: Trace[] = [];
  try {
    await expect.poll(async () => {
      initialSpans = await probe.ch<Span>(spanSql, params);
      return sorted(initialSpans.map(({ version, attrs_string, attrs_number, attrs_bool, extra, resource, ...row }) => row));
    }, POLL.SPAN_VISIBLE).toEqual(expectedSpans);
    for (const seed of seeds) for (const [child, id] of seed.spanIds.entries()) {
      const row = initialSpans.find(span => span.id === id)!;
      expect(BigInt(row.version)).toBeGreaterThan(0n);
      expect(row.attrs_string).toEqual({ 'fi.span.kind': child ? 'llm' : 'chain' });
      expect(row.attrs_number).toEqual({ 'gen_ai.cost.total': 0 }); expect(row.attrs_bool).toEqual({}); expect(JSON.parse(row.extra)).toEqual({});
      expect(JSON.parse(row.resource)).toEqual({ project_type: 'observe', project_name: projectNames[seed.project],
        service: { name: projectNames[seed.project] }, fi: { org_id: actors[seed.project].organizationId, project_id: projectIds[seed.project] } });
    }
    await expect.poll(async () => {
      initialTraces = await probe.ch<Trace>(traceSql, params);
      // converter.collectTrace copies the root name/start_time; versions remain storage-generated.
      return sorted(initialTraces.map(({ updated_us, version, ...row }) => row));
    }, POLL.SPAN_VISIBLE).toEqual(sorted(seeds.map(seed => ({ id: seed.traceId, project_id: projectIds[seed.project],
      name: `${prefix}-${seed.key}-root`, created_us: String(start / 1000n), is_deleted: 0 }))));
    for (const row of initialTraces) expect(BigInt(row.version)).toBeGreaterThan(0n);
  } finally { await attach('initial-source-facts', { expectedSpans, initialSpans, initialTraces }); }
  // Score model/serializer + CH schema.py: timestamp text avoids pg Date microsecond loss.
  const scoreFields = `id,label_id,source_type,trace_id,tracer_project_id,organization_id,workspace_id,annotator_id,queue_item_id,
    score_source,notes,observation_span_id,trace_session_id,call_execution_id,prototype_run_id,dataset_row_id,project_id`;
  const readPG = () => probe.pg<Score>(`SELECT ${scoreFields},value,value_history,deleted::integer AS deleted,
    (extract(epoch from deleted_at)*1000000)::bigint::text AS deleted_us,
    (extract(epoch from created_at)*1000000)::bigint::text AS created_us,
    (extract(epoch from updated_at)*1000000)::bigint::text AS updated_us
    FROM model_hub_score WHERE label_id=ANY($1::uuid[]) ORDER BY id`, [labelIds]);
  const readCH = () => probe.ch<CHScore>(`SELECT ${scoreFields},value,value_history,toUInt8(deleted) AS deleted,
    toString(toUnixTimestamp64Micro(deleted_at)) AS deleted_us,toString(toUnixTimestamp64Micro(created_at)) AS created_us,
    toString(toUnixTimestamp64Micro(updated_at)) AS updated_us,toString(_peerdb_version) AS version,_peerdb_is_deleted AS peerdb_deleted
    FROM model_hub_score FINAL WHERE label_id IN ({own:UUID},{foreign:UUID}) ORDER BY toString(id) SETTINGS readonly=1,max_threads=1`,
  { own: labelIds[0], foreign: labelIds[1] });
  const semanticScore = ({ version, peerdb_deleted, value, value_history, ...row }: CHScore): Score => {
    expect(peerdb_deleted).toBe(0);
    const absent = (v: string | null) => v === null || v === '' || v === '00000000-0000-0000-0000-000000000000' ? null : v;
    return { ...row, value: JSON.parse(value), value_history: JSON.parse(value_history),
      observation_span_id: absent(row.observation_span_id), trace_session_id: absent(row.trace_session_id),
      call_execution_id: absent(row.call_execution_id), prototype_run_id: absent(row.prototype_run_id),
      dataset_row_id: absent(row.dataset_row_id), project_id: absent(row.project_id),
      deleted_us: row.deleted_us !== null && BigInt(row.deleted_us) <= 0n ? null : row.deleted_us };
  };
  expect(await readPG()).toEqual([]);
  let initialScores: Score[] = [], initialCHScores: CHScore[] = [], initialItems: Wire[] = [];
  const submissions: { seed: typeof seeds[number]; receipt: Receipt<{ result: { scores: NativeScore[]; errors: unknown[] } }> }[] = [];
  const receipts: Receipt<unknown>[] = [], scopeReceipts: unknown[] = [], pending = new Set<Promise<void>>();
  const requests = new Map<Request, Receipt<unknown>>();
  const context = await scopeActors.openContext(browser, actor), foreignContext = await scopeActors.openContext(browser, foreignActor);
  try {
    const page = await context.newPage(), foreignPage = await foreignContext.newPage();
    for (const surface of [page, foreignPage]) surface.setDefaultTimeout(UI_READY);
    const scopeOf = (owner: ScopeActor): Scope => ({ organizationId: owner.organizationId, workspaceId: owner.workspaceId });
    const onRequest = (outgoing: Request) => {
      const url = new URL(outgoing.url()), path = url.pathname, method = outgoing.method();
      if (url.origin !== new URL(E2E.apiUrl).origin || !['GET', 'POST', 'PATCH', 'PUT'].includes(method) ||
        !([LIST, BULK, `${QUEUES}for-source/`, METRICS, VALUES, QUERY, DASHBOARDS].includes(path) ||
          /^\/tracer\/trace\/[0-9a-f-]+\/$/.test(path) || /^\/tracer\/dashboard\/[0-9a-f-]+\/(?:widgets\/(?:[0-9a-f-]+\/)?)?$/.test(path))) return;
      const headers = outgoing.headers();
      const receipt: Receipt<unknown> = { path, method, input: {}, status: 0, startedAt: Date.now(), endedAt: 0, settled: false,
        scope: { organizationId: headers['x-organization-id'], workspaceId: headers['x-workspace-id'] }, authorized: /^Bearer \S+$/.test(headers.authorization ?? '') };
      try { receipt.input = method === 'GET' ? Object.fromEntries(url.searchParams) : outgoing.postDataJSON() as Wire; }
      catch { receipt.error = 'request_json_unreadable'; }
      requests.set(outgoing, receipt); receipts.push(receipt);
    };
    const onResponse = (response: Response) => {
      const receipt = requests.get(response.request()); if (!receipt) return;
      Object.assign(receipt, { status: response.status(), endedAt: Date.now(), requestId: response.headers()['x-request-id'], contentType: response.headers()['content-type'] ?? '' });
      const capture = (async () => {
        try {
          await attach('native-http-head', { ...receipt });
          if (!/\bapplication\/(?:[\w.-]+\+)?json\b/i.test(receipt.contentType!)) { receipt.error = 'non_json_body_omitted'; return; }
          receipt.body = await response.json();
          // scores.py can return HTTP200 with per-score errors. Classify them
          // before settling so a later success cannot hide this failed Save.
          const body = receipt.body as { status?: boolean; result?: { errors?: unknown } } | null;
          if (body?.status === false) receipt.error ??= 'application_error';
          if (receipt.path === BULK &&
            (!Array.isArray(body?.result?.errors) || body.result.errors.length !== 0)) {
            receipt.error ??= 'bulk_score_errors';
          }
        } catch { receipt.error = 'response_json_unreadable'; }
        finally { receipt.endedAt = Date.now(); receipt.settled = true; }
      })();
      pending.add(capture); void capture.then(() => pending.delete(capture));
    };
    const onFailed = (outgoing: Request) => {
      const receipt = requests.get(outgoing); if (receipt) Object.assign(receipt, { error: 'request_failed', settled: true, endedAt: Date.now() });
    };
    for (const surface of [page, foreignPage]) { surface.on('request', onRequest); surface.on('response', onResponse); surface.on('requestfailed', onFailed); }
    // Approved D12 receipt contract: a matching failure outranks a later200; unrelated inputs/scopes do not.
    const readNative = async <T>(path: string, input: Wire | ((wire: Wire) => boolean) | undefined, since: number,
      method = 'POST', owner = actor, complete?: (body: T) => boolean): Promise<Receipt<T>> => {
      let chosen: Receipt<unknown> | undefined;
      await expect.poll(() => {
        const matching = receipts.filter(r => r.path === path && r.method === method && r.startedAt >= since &&
          isDeepStrictEqual(r.scope, scopeOf(owner)) && (input === undefined || (typeof input === 'function' ? input(r.input) : isDeepStrictEqual(r.input, input))));
        chosen = matching.find(r => r.settled && (r.error || r.status !== 200));
        if (!chosen) {
          const good = matching.filter(r => r.settled && (!complete || complete(r.body as T))).at(-1);
          if (good && matching.every(r => r.startedAt > good.startedAt || r.settled)) chosen = good;
        }
        return Boolean(chosen);
      }, { timeout: UI_READY }).toBe(true);
      const receipt = chosen! as Receipt<T>; await attach(`native-${path.split('/').filter(Boolean).at(-1)}`, receipt);
      expect(receipt.error).toBeUndefined(); expect(receipt.status).toBe(200); expect(receipt.scope).toEqual(scopeOf(owner)); expect(receipt.authorized).toBe(true);
      return receipt;
    };
    // source_adapters._annotation_definition: text/both metric, with no configured choices.
    const annotationProperty = (id: string): Property => ({ property_id: `annotation:${id}`, property_kind: 'annotation',
      category: 'annotation_metric', name: id, display_name: labelName, source: 'both', type: 'text', output_type: 'text',
      role: 'metric', data_type: 'text', sources: ['annotation', 'datasets', 'traces'] });
    const ownProperty = annotationProperty(labelIds[0]);
    const traceProperty: Property = { property_id: 'system_attribute:traces:trace_count', property_kind: 'system_attribute',
      category: 'system_metric', name: 'trace_count', display_name: 'Traces', source: 'traces', type: 'number', output_type: 'number', role: 'metric' };
    const projectProperty: Property = { ...traceProperty, property_id: 'system_attribute:traces:project', name: 'project',
      display_name: 'Project', type: 'string', output_type: 'string', role: 'dimension' };
    // WidgetEditorView buildMetric/Filter/BreakdownPayload, not normalized DRF defaults.
    const configFor = (value?: string, grouped = false, project: string | null = projectIds[0], label = labelIds[0]): Config => ({
      project_ids: [], time_range: { preset: '7D' }, granularity: 'day', metrics: [
        { id: label, name: labelName, property_id: `annotation:${label}`, display_name: labelName, type: 'annotation_metric',
          source: 'both', aggregation: 'count', label_id: label, output_type: 'text' },
        { id: 'trace_count', name: 'trace_count', property_id: 'system_attribute:traces:trace_count', display_name: 'Traces',
          type: 'system_metric', source: 'traces', aggregation: 'count_distinct' },
      ], filters: [
        ...(project === null ? [] : [{ column_id: 'project', property_id: 'system_attribute:traces:project', display_name: 'Project',
          source: 'traces', output_type: 'string', filter_config: { filter_type: 'text', filter_op: 'in', filter_value: [project], col_type: 'SYSTEM_METRIC' } }]),
        ...(value === undefined ? [] : [{ column_id: label, property_id: `annotation:${label}`, display_name: labelName,
          source: 'both', output_type: 'text', filter_config: { filter_type: 'text', filter_op: 'in', filter_value: [value], col_type: 'ANNOTATION' } }]),
      ], breakdowns: grouped ? [{ name: label, property_id: `annotation:${label}`, display_name: labelName,
        type: 'annotation_metric', source: 'both', label_id: label, output_type: 'text' }] : [],
    });
    const canonical = configFor(mintedA, true), metadata = { query_complete: true, query_exact: false, query_status: 'complete', query_provenance: 'current_property_catalog' };
    const globalSection = page.locator('.filter-section-title').locator('../..');
    const card = (name: string) => globalSection.getByText(name, { exact: true }).locator('../..');
    const popup = page.getByRole('tooltip').filter({ has: page.getByPlaceholder('Search...', { exact: true }) });
    const selectCategory = (category: 'All' | 'Traces' | 'Annotations') => page.getByLabel(new RegExp(`^${category} property count: `))
      .locator('..').getByText(category, { exact: true }).click();
    const optionName = (property: Property) => `${property.display_name} (${property.output_type}, ${property.source === 'both' ? 'Traces, Datasets' : 'Traces'})`;
    const searchCatalog = async (property: Property, mode: 'metric' | 'filter' | 'breakdown', category: 'All' | 'Traces' | 'Annotations') => {
      const since = Date.now(); await selectCategory(category);
      const search = page.getByPlaceholder(mode === 'metric' ? 'Search metrics...' : mode === 'filter' ? 'Search filter attributes...' : 'Search breakdown attributes...');
      await search.fill(''); await search.fill(property.display_name);
      const input = { cursor_mode: true, search: property.display_name, ...(mode === 'metric' ? { role: 'metric' } : {}),
        ...(category === 'All' ? {} : category === 'Annotations' ? { category: 'annotation_metric' } : { category: 'system_metric', source: 'traces' }) };
      const receipt = await readNative<CatalogBody>(METRICS, wire => Number.isInteger(wire.page_size) && Number(wire.page_size) > 0 &&
        isDeepStrictEqual(wire, { ...input, page_size: wire.page_size }), since);
      expect(receipt.body!.result).toMatchObject({ ...metadata, has_more: false, next_cursor: null });
      const rows = receipt.body!.result.metrics.filter(row => row.property_id === property.property_id);
      expect(rows).toHaveLength(1); expect(rows[0]).toMatchObject(property);
      if (property.property_kind === 'annotation') {
        expect(receipt.body!.result.metrics.map(row => row.property_id)).toEqual([property.property_id]);
        expect(rows[0]).not.toHaveProperty('choices'); expect(rows[0]).not.toHaveProperty('choice_options');
        expect(rows[0].type).toBe(EXPECTED_CATALOG_TEXT_TYPE);
      }
      await uiExpect(page.getByRole('button', { name: optionName(property), exact: true })).toBeVisible();
    };
    const chooseProperty = async (property: Property, breakdown = false) => {
      await page.locator(breakdown ? '.breakdown-section-title' : '.filter-section-title').click();
      await searchCatalog(property, breakdown ? 'breakdown' : 'filter', property.property_kind === 'annotation' ? 'Annotations' : 'Traces');
      await page.getByRole('button', { name: optionName(property), exact: true }).click();
    };
    const primaryOptions = [{ value: projectIds[0], label: projectNames[0] }];
    const selectProject = async () => {
      await chooseProperty(projectProperty); await card('Project').getByText('Select value...', { exact: true }).click();
      await uiExpect(popup).toHaveCount(1); const since = Date.now();
      await popup.getByPlaceholder('Search...', { exact: true }).fill(projectNames[0]);
      const input = { property_id: projectProperty.property_id, metric_name: 'project', metric_type: 'system_metric', source: 'traces',
        project_ids: '', page_size: 10, search: projectNames[0] };
      let receipt = await readNative<ValueBody>(VALUES, input, since);
      const window = { start: receipt.body!.result.query_window_start, end: receipt.body!.result.query_window_end };
      const chain: Receipt<ValueBody>[] = [], options: Value[] = [], cursors = new Set<string>();
      try {
        while (true) {
          chain.push(receipt); const result = receipt.body!.result;
          expect(result).toMatchObject({ query_complete: true, query_status: 'complete', query_window_start: window.start, query_window_end: window.end });
          expect(Number.isFinite(Date.parse(window.start!))).toBe(true); expect(Date.parse(window.end!)).toBeGreaterThan(Date.parse(window.start!));
          for (const value of result.values) { expect(options.some(row => row.value === value.value)).toBe(false); options.push(value); }
          if (!result.has_more) { expect(result).toMatchObject({ has_more: false, next_cursor: null, browse_status: 'exhausted' }); break; }
          expect(result.browse_status).toBe('continuation'); const cursor = result.next_cursor;
          expect(typeof cursor).toBe('string'); expect(cursor!.length).toBeGreaterThan(0); expect(cursors.has(cursor!)).toBe(false); cursors.add(cursor!);
          receipt = await readNative<ValueBody>(VALUES, { ...input, cursor }, since); // Native automatic sentinel only.
        }
        expect(sorted(options)).toEqual(primaryOptions);
      } finally { await attach('native-project-chain', { input, window, chain, options, expected: primaryOptions }); }
      await uiExpect(popup.getByRole('progressbar')).toHaveCount(0);
      await uiExpect(popup.locator('p[title]').filter({ hasNotText: /^Specify: / })).toHaveText([projectNames[0]]);
      const row = popup.locator(`p[title="${projectNames[0]}"]`).locator('..');
      await uiExpect(row.getByRole('checkbox')).not.toBeChecked(); await row.click(); await uiExpect(row.getByRole('checkbox')).toBeChecked();
      await popup.getByRole('button', { name: 'Add', exact: true }).click();
    };
    const emptyTextValues = { values: [], query_complete: true, query_status: 'complete', has_more: false, next_cursor: null, browse_status: 'exhausted' };
    const textValuesInput = { property_id: ownProperty.property_id, metric_name: labelIds[0], metric_type: 'annotation_metric',
      source: 'traces', project_ids: '', page_size: 10 };
    const verifiedTextSearches = new Set<string>();
    const searchText = async (value: string) => {
      const since = Date.now(); await popup.getByPlaceholder('Search...', { exact: true }).fill(value);
      if (!verifiedTextSearches.has(value)) {
        const receipt = await readNative<ValueBody>(VALUES, { ...textValuesInput, search: value }, since);
        expect(receipt.body!.result).toEqual(emptyTextValues); verifiedTextSearches.add(value);
      } // Repeated searches may use the already verified finite cache.
      await uiExpect(popup.getByRole('progressbar')).toHaveCount(0); await uiExpect(popup.getByRole('status')).toHaveCount(0);
      await uiExpect(popup.getByRole('button', { name: /^Retry/ })).toHaveCount(0);
      await uiExpect(popup.locator('p[title]').filter({ hasNotText: /^Specify: / })).toHaveCount(0);
      await uiExpect(popup.getByText('Select all in list (0)', { exact: true })).toBeVisible();
      const row = popup.locator(`[data-widget-filter-exact-value="${value}"]`);
      await uiExpect(row).toHaveCount(1); await uiExpect(row.locator(`p[title="Specify: ${value}"]`)).toHaveText(`Specify: ${value}`);
      return row;
    };
    const selectText = async (next: string, previous?: string) => {
      if (previous !== undefined) await card(labelName).locator('.filter-value-name').click();
      await uiExpect(popup).toHaveCount(1);
      if (previous !== undefined) {
        const old = await searchText(previous); await uiExpect(old.getByRole('checkbox')).toBeChecked();
        await old.click(); await uiExpect(old.getByRole('checkbox')).not.toBeChecked();
        await uiExpect(popup.getByRole('button', { name: 'Add', exact: true })).toBeDisabled();
      }
      const row = await searchText(next); await uiExpect(row.getByRole('checkbox')).not.toBeChecked();
      await row.click(); await uiExpect(row.getByRole('checkbox')).toBeChecked();
      await testInfo.attach(`Specify-${next}`, { contentType: 'image/png', body: await page.screenshot() });
      await popup.getByRole('button', { name: 'Add', exact: true }).click();
      await uiExpect(card(labelName).locator('.filter-value-name')).toHaveText(next);
    };
    const scoreDay = (traceId: string) => Math.floor(Number(BigInt(initialScores.find(row => row.trace_id === traceId)!.created_us) / 1000n) / DAY) * DAY;
    // dashboard.py own-label text grouping counts Scores on created_at; companion
    // Traces groups the same exact text on planted span.start_time. No total fallback for A.
    const assertResult = (receipt: Receipt<QueryBody>, input: Config, oracle: Oracle, reopened = false): number[] => {
      expect(receipt.status).toBe(200); expect(receipt.error).toBeUndefined(); expect(receipt.input).toEqual(input);
      const result = receipt.body!.result; expect(result).toMatchObject({ query_complete: true, query_exact: true, granularity: 'day' });
      expect(result).not.toHaveProperty('error');
      const from = Date.parse(result.time_range.start), to = Date.parse(result.time_range.end);
      expect(to - from).toBe(7 * DAY); expect(to).toBeGreaterThanOrEqual(receipt.startedAt - 1000); expect(to).toBeLessThanOrEqual(receipt.endedAt + 1000);
      expect(at).toBeGreaterThan(from); expect(at).toBeLessThan(to);
      if (oracle.value !== null) {
        const label = String(input.metrics[0].id), project = (input.filters[0].filter_config as { filter_value: string[] }).filter_value[0];
        const fact = initialScores.find(row => row.label_id === label && row.tracer_project_id === project)!;
        const createdAt = Number(BigInt(fact.created_us) / 1000n); expect(createdAt).toBeGreaterThan(from); expect(createdAt).toBeLessThan(to);
      }
      const buckets: number[] = []; for (let day = Math.floor(from / DAY) * DAY; day <= to; day += DAY) buckets.push(day);
      expect(result.metrics.map(({ id, name, unit, aggregation }) => ({ id, name, unit, aggregation }))).toEqual([
        { id: input.metrics[0].id, name: labelName, unit: '', aggregation: 'count' },
        { id: 'trace_count', name: 'Traces', unit: '', aggregation: 'count_distinct' },
      ]);
      for (const [index, metric] of result.metrics.entries()) {
        expect(metric).toMatchObject({ query_complete: true, query_exact: true }); expect(metric).not.toHaveProperty('error');
        expect(metric.series.map(series => series.name)).toEqual([oracle.grouped && (index === 0 ? oracle.value : oracle.traces) !== null ? mintedA : 'total']);
        const series = metric.series[0], targetDay = index === 0 ? oracle.scoreDay : plantedDay, value = index === 0 ? oracle.value : oracle.traces;
        expect(series.data.map(point => Date.parse(point.timestamp))).toEqual(buckets);
        expect(series.data.map(point => point.value)).toEqual(buckets.map(bucket => bucket === targetDay ? value : null));
        if (reopened && index === 0) expect(series.data.find(point => Date.parse(point.timestamp) === oracle.scoreDay)!.value).toBe(EXPECTED_REOPENED_TEXT_COUNT);
      }
      return buckets;
    };
    let dashboardId = '', savedWidget: Widget | undefined, originalChart: Widget['chart_config'] | undefined, lastBuckets: number[] = [];
    const locale = await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone);
    const assertTable = async (oracle: Oracle, buckets: number[], tile = false) => {
      const columns: Record<string, (number | null)[]> = {
        [`${labelName}${oracle.grouped && oracle.value !== null ? ` / ${mintedA}` : ''} (count)`]: buckets.map(bucket => bucket === oracle.scoreDay ? oracle.value : null),
        [`Traces${oracle.grouped && oracle.traces !== null ? ` / ${mintedA}` : ''} (count_distinct)`]: buckets.map(bucket => bucket === plantedDay ? oracle.traces : null),
      };
      const table = tile ? page.locator(`[data-widget-id="${savedWidget!.id}"]`).getByRole('table') : page.getByRole('table');
      await uiExpect(table).toBeVisible();
      await expect.poll(async () => (await table.locator('thead th').allTextContents()).slice(1).sort(), { timeout: UI_READY }).toEqual(Object.keys(columns).sort());
      const headers = await table.locator('thead th').allTextContents(); expect(headers[0]).toBe('Time');
      await uiExpect(table.locator('tbody tr')).toHaveCount(buckets.length);
      await uiExpect(table.locator('tbody tr td:first-child')).toHaveText(buckets.map(bucket => new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: locale }).format(bucket)));
      for (const [index, name] of headers.slice(1).entries()) await uiExpect(table.locator(`tbody tr td:nth-child(${index + 2})`))
        .toHaveText(columns[name].map(value => value === null ? '-' : tile ? value.toFixed(2) : String(value)));
    };
    const queryAfter = async (name: string, input: Config, oracle: Oracle, action: () => Promise<unknown>, tile = false, reopened = false) => {
      const since = Date.now(); await action();
      const receipt = await readNative<QueryBody>(QUERY, input, since, 'POST', actor, body => body?.result?.query_complete === true);
      lastBuckets = assertResult(receipt, input, oracle, reopened);
      await attach(`${name}-query`, { receipt, input, oracle }); await assertTable(oracle, lastBuckets, tile);
      await testInfo.attach(`${name}-table`, { contentType: 'image/png', body: await page.screenshot() });
    };
    const assertControls = async () => {
      await uiExpect(card('Project').getByRole('combobox')).toHaveText('Is');
      await uiExpect(card('Project').locator('.filter-value-name')).toHaveText(projectNames[0]);
      await uiExpect(card(labelName).getByRole('combobox')).toHaveText('Is');
      await uiExpect(card(labelName).locator('.filter-value-name')).toHaveText(mintedA);
      await uiExpect(card(labelName).getByRole('spinbutton')).toHaveCount(0);
      for (const [name, aggregation] of [[labelName, 'Count'], ['Traces', 'Distinct Count']]) {
        const metricCard = page.locator(`p[title="${name}"]`).locator('../..');
        await uiExpect(metricCard.getByText(aggregation, { exact: true })).toBeVisible();
        await uiExpect(metricCard.locator('.filter-value-name')).toHaveCount(0);
      }
      await uiExpect(page.locator('.breakdown-section-title').locator('../..').getByText(labelName, { exact: true })).toBeVisible();
      await uiExpect(page.getByRole('combobox').filter({ hasText: 'Table' })).toBeVisible();
      await uiExpect(page.getByRole('combobox').filter({ hasText: 'Day' })).toBeVisible(); await uiExpect(page.getByText('7D', { exact: true })).toBeVisible();
    };
    const verifySaved = async () => {
      const widget = savedWidget!;
      const rows = await probe.pg('SELECT id,name,dashboard_id,created_by_id,query_config,chart_config,deleted FROM tracer_dashboardwidget WHERE dashboard_id=$1 ORDER BY id', [dashboardId]);
      await attach('saved-widget-PG', rows);
      expect(rows).toEqual([{ id: widget.id, name: widgetName, dashboard_id: dashboardId, created_by_id: actor.userId, query_config: canonical, chart_config: originalChart, deleted: false }]);
      // Dashboard has no organization column: models/dashboard.py + migrations/0058.
      const dashboards = await probe.pg(`SELECT d.id,d.name,d.workspace_id,d.created_by_id,d.deleted,
        w.organization_id,w.deleted AS workspace_deleted FROM tracer_dashboard d
        JOIN accounts_workspace w ON w.id=d.workspace_id WHERE d.id=$1`, [dashboardId]);
      await attach('saved-dashboard-PG', dashboards);
      expect(dashboards).toEqual([{ id: dashboardId, name: dashboardName, workspace_id: actor.workspaceId,
        created_by_id: actor.userId, deleted: false, organization_id: actor.organizationId, workspace_deleted: false }]);
      const detail = await actor.api.get<{ result: { id: string; name: string; workspace: string; widgets: Widget[] } }>(`${DASHBOARDS}${dashboardId}/`);
      await attach('saved-dashboard-GET', detail);
      expect(detail.result).toMatchObject({ id: dashboardId, name: dashboardName, workspace: actor.workspaceId });
      expect(detail.result.widgets.map(({ id, name, query_config, chart_config }) => ({ id, name, query_config, chart_config })))
        .toEqual([{ id: widget.id, name: widgetName, query_config: canonical, chart_config: originalChart }]);
      for (const owner of [actor, foreignActor, emptyActor]) {
        const path = `${DASHBOARDS}${dashboardId}/widgets/${widget.id}/`, response = await scopeActors.send<Widget>(owner, 'GET', path);
        scopeReceipts.push({ path, scope: scopeOf(owner), ...response }); expect(response.status).toBe(owner === actor ? 200 : 404);
        if (owner === actor) { expect(response.body).toMatchObject({ id: widget.id, name: widgetName });
          expect(response.body.query_config).toEqual(canonical); expect(response.body.chart_config).toEqual(originalChart); }
      }
      await attach('saved-PG-GET', { rows, dashboards, detail, widget });
    };
    const conserveSource = async (name: string) => {
      const [projects, spans, traces] = await Promise.all([probe.pg<Project>(projectSql, [projectNames, projectIds]),
        probe.ch<Span>(spanSql, params), probe.ch<Trace>(traceSql, params)]);
      await attach(name, { initialProjects, projects, initialSpans, spans, initialTraces, traces });
      expect(projects).toEqual(initialProjects); expect(spans).toEqual(initialSpans); expect(traces).toEqual(initialTraces);
    };
    try {
      await test.step('1 submit three native text trace Scores', async () => {
        for (const seed of [seeds[0], seeds[2], seeds[3]]) {
          const owner = actors[seed.project], surface = seed.project === 2 ? foreignPage : page, queue = queues[seed.project];
          const labelId = labelIds[seed.project === 2 ? 1 : 0], projectId = projectIds[seed.project];
          const expected = seeds.filter(row => row.project === seed.project), since = Date.now();
          await surface.goto(`/dashboard/observe/${projectId}/llm-tracing?tab=traces&viewMode=%22graph%22`, { waitUntil: 'domcontentloaded' });
          type ListBody = { result: { table: { trace_id: string; trace_name: string }[]; metadata: {
            query_complete: boolean; query_status: string; query_error_code: string | null; has_more: boolean;
            next_cursor: string | null; total_rows_is_lower_bound: boolean } } };
          let list = await readNative<ListBody>(LIST, wire => wire.project_id === projectId && !wire.cursor, since, 'POST', owner,
            body => body?.result?.metadata?.query_complete === true);
          expect(list.input.cursor_mode).toBe(true); expect(list.input.page_number).toBe(0);
          // dateRangeDefaults + useLLMTracingFilters: the default has one date predicate.
          const filters = JSON.parse(String(list.input.filters));
          expect(filters).toEqual([{ column_id: 'created_at', filter_config: {
            filter_type: 'datetime', filter_op: 'between', filter_value: [expect.any(String), expect.any(String)] } }]);
          const [from, to] = filters[0].filter_config.filter_value.map(Date.parse);
          const bounds = await surface.evaluate(([navigation, requested]) => {
            const weekAgo = (ms: number) => { const day = new Date(ms); day.setDate(day.getDate() - 7); day.setMilliseconds(0); return day.getTime(); };
            const end = new Date(requested); end.setHours(23, 59, 59, 0);
            return { earliest: weekAgo(navigation), latest: weekAgo(requested), end: end.getTime() };
          }, [since, list.startedAt]);
          expect(from).toBeGreaterThanOrEqual(bounds.earliest); expect(from).toBeLessThanOrEqual(bounds.latest);
          expect(to).toBe(bounds.end); expect(at).toBeGreaterThan(from); expect(at).toBeLessThan(to);
          expect(Number.isInteger(list.input.page_size)).toBe(true);
          expect(Number(list.input.page_size)).toBeGreaterThanOrEqual(expected.length);
          const { page_number, ...listBase } = list.input, chain: Receipt<ListBody>[] = [], seen = new Set<string>();
          const listed: { trace_id: string; trace_name: string }[] = [];
          // TraceGrid's native loadExactListPage may traverse physical pages before painting.
          try {
            while (true) {
              chain.push(list); const result = list.body!.result;
              expect(result.metadata).toMatchObject({ query_complete: true, query_status: 'complete', query_error_code: null });
              for (const row of result.table) {
                expect(listed.some(previous => previous.trace_id === row.trace_id)).toBe(false);
                listed.push({ trace_id: row.trace_id, trace_name: row.trace_name });
              }
              if (!result.metadata.has_more) {
                expect(result.metadata).toMatchObject({ has_more: false, next_cursor: null, total_rows_is_lower_bound: false }); break;
              }
              const cursor = result.metadata.next_cursor; expect(typeof cursor).toBe('string'); expect(cursor!.length).toBeGreaterThan(0);
              expect(seen.has(cursor!)).toBe(false); seen.add(cursor!);
              list = await readNative<ListBody>(LIST, { ...listBase, cursor }, since, 'POST', owner, body => body?.result?.metadata?.query_complete === true);
            }
            expect(sorted(listed)).toEqual(sorted(expected.map(row => ({ trace_id: row.traceId, trace_name: `${prefix}-${row.key}-root` }))));
          } finally { await attach(`native-${seed.key}-trace-list-chain`, { chain, listed, expected }); }
          const cells = surface.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]');
          await expect.poll(() => cells.evaluateAll(nodes => nodes.map(node => node.closest('.ag-row')!.getAttribute('row-id')).sort()), { timeout: UI_READY })
            .toEqual(expected.map(row => row.traceId).sort());
          await expect.poll(async () => (await cells.allTextContents()).map(text => text.trim()).sort(), { timeout: UI_READY })
            .toEqual(expected.map(row => `${prefix}-${row.key}-root`).sort());
          const detailSince = Date.now(); await cells.getByText(`${prefix}-${seed.key}-root`, { exact: true }).click();
          type DetailNode = { observation_span: Wire; children: DetailNode[] };
          const detail = await readNative<{ result: { trace: { id: string; project: string; name: string }; observation_spans: DetailNode[] } }>(
            `/tracer/trace/${seed.traceId}/`, {}, detailSince, 'GET', owner);
          expect(detail.body!.result.trace).toMatchObject({ id: seed.traceId, project: projectId, name: `${prefix}-${seed.key}-root` });
          // Trace detail is a root/children tree, not the flat source-storage list.
          expect(detail.body!.result.observation_spans).toHaveLength(1);
          const root = detail.body!.result.observation_spans[0]; expect(root.children).toHaveLength(1);
          expect(root.observation_span).toMatchObject({ id: seed.spanIds[0], project: projectId, trace: seed.traceId,
            parent_span_id: null, name: `${prefix}-${seed.key}-root`, observation_type: 'chain' });
          expect(root.children[0].observation_span).toMatchObject({ id: seed.spanIds[1], project: projectId, trace: seed.traceId,
            parent_span_id: seed.spanIds[0], name: `${prefix}-${seed.key}-child`, observation_type: 'llm' });
          expect(root.children[0].children).toEqual([]);
          const annotateSince = Date.now(); await surface.getByRole('button', { name: 'Actions', exact: true }).click();
          await surface.getByRole('menuitem', { name: 'Annotate', exact: true }).click();
          const sourceInput = { sources: JSON.stringify([{ source_type: 'trace', source_id: seed.traceId, span_notes_source_id: seed.spanIds[0] },
            { source_type: 'observation_span', source_id: seed.spanIds[0] }]) };
          type QueueBody = { result: { queue: Queue; item: null; labels: Wire[]; existing_scores: Wire; existing_notes: string; existing_label_notes: Wire }[] };
          const forSource = await readNative<QueueBody>(`${QUEUES}for-source/`, sourceInput, annotateSince, 'GET', owner);
          expect(forSource.body!.result).toHaveLength(1); expect(forSource.body!.result[0]).toMatchObject({
            queue: { id: queue.id, name: `Default - ${projectNames[seed.project]}`, is_default: true }, item: null,
            existing_scores: {}, existing_notes: '', existing_label_notes: {}, span_notes: [], span_notes_source_id: seed.spanIds[0] });
          expect(forSource.body!.result[0].labels).toEqual([{ id: labelId, name: labelName, type: 'text',
            settings: { placeholder, min_length: 0, max_length: 500 }, description: '', allow_notes: false, required: false, order: 1 }]);
          const drawer = surface.locator('.MuiDrawer-paper:visible').filter({ has: surface.getByText(queue.name, { exact: true }) });
          await uiExpect(drawer).toHaveCount(1); const heading = drawer.getByText(`${labelName}*`, { exact: true });
          await uiExpect(heading).toHaveCount(1); await heading.click();
          // label-input.jsx: 300ms parent debounce; this drawer supplies no flush ref.
          // A fresh disabled->enabled Save observes publication of the one text edit.
          const save = drawer.getByRole('button', { name: /^Save(?:\s|$)/ });
          await uiExpect(save).toHaveCount(1); await uiExpect(save).toBeDisabled();
          const input = drawer.getByPlaceholder(placeholder, { exact: true });
          await uiExpect(input).toHaveValue(''); await input.fill(submittedText);
          await uiExpect(input).toHaveValue(mintedA); await uiExpect(save).toBeEnabled();
          const saveSince = Date.now(); await save.click();
          const payload = { source_type: 'trace', source_id: seed.traceId,
            scores: [{ label_id: labelId, value: { text: mintedA }, notes: '', score_source: 'human' }], notes: '' };
          const receipt = await readNative<{ result: { scores: NativeScore[]; errors: unknown[] } }>(BULK, undefined, saveSince, 'POST', owner);
          expect(receipt.input).toEqual(payload); expect(receipt.body!.result.errors).toEqual([]); expect(receipt.body!.result.scores).toHaveLength(1);
          const score = receipt.body!.result.scores[0]; expect(score.id).toMatch(/^[0-9a-f-]{36}$/); expect(score.queue_item).toMatch(/^[0-9a-f-]{36}$/);
          expect(score).toMatchObject({ source_type: 'trace', source_id: seed.traceId, label_id: labelId, label_name: labelName,
            label_type: 'text', label_settings: { placeholder, min_length: 0, max_length: 500 }, label_allow_notes: false,
            value: { text: mintedA }, value_history: [], score_source: 'human', notes: '', annotator: owner.userId, queue_id: queue.id });
          expect(score.value).toEqual({ text: mintedA }); expect(score.label_settings).toEqual({ placeholder, min_length: 0, max_length: 500 });
          submissions.push({ seed, receipt }); await attach('native-submissions', submissions);
        }
      }, { timeout: UI_READY });

      await test.step('combined PG definitions and latest PG-to-CH Score barrier', async () => {
        initialScores = await readPG(); await attach('native-scores-PG', initialScores); expect(initialScores).toHaveLength(3);
        expect(new Set(initialScores.map(row => row.id)).size).toBe(3); expect(new Set(initialScores.map(row => row.queue_item_id)).size).toBe(3);
        for (const submission of submissions) {
          const { seed, receipt } = submission, wire = receipt.body!.result.scores[0], owner = actors[seed.project];
          const fact = initialScores.find(row => row.id === wire.id)!;
          expect(fact).toMatchObject({ id: wire.id, label_id: labelIds[seed.project === 2 ? 1 : 0], source_type: 'trace', trace_id: seed.traceId,
            tracer_project_id: projectIds[seed.project], organization_id: owner.organizationId, workspace_id: owner.workspaceId, annotator_id: owner.userId,
            queue_item_id: wire.queue_item, score_source: 'human', notes: '', value: { text: `${prefix}-alpha` }, value_history: [], deleted: 0, deleted_us: null,
            observation_span_id: null, trace_session_id: null, call_execution_id: null, prototype_run_id: null, dataset_row_id: null, project_id: null });
          expect(fact.value).toEqual({ text: `${prefix}-alpha` }); expect(fact.value_history).toEqual([]);
          for (const [micros, iso] of [[fact.created_us, wire.created_at], [fact.updated_us, wire.updated_at]]) {
            const timestamp = Number(BigInt(micros) / 1000n); expect(timestamp).toBeGreaterThanOrEqual(receipt.startedAt - 1000);
            expect(timestamp).toBeLessThanOrEqual(receipt.endedAt + 1000); expect(Date.parse(iso)).toBe(timestamp);
          }
        }
        // scores._auto_complete_queue_items skips queues with no required labels; fresh items stay pending.
        initialItems = await probe.pg(itemSql, [queues.map(row => row.id)]);
        await attach('native-queue-items-PG', initialItems);
        expect(sorted(initialItems)).toEqual(sorted(submissions.map(({ seed, receipt }) => ({ id: receipt.body!.result.scores[0].queue_item,
          queue_id: queues[seed.project].id, source_type: 'trace', status: 'pending', trace_id: seed.traceId, observation_span_id: null, trace_session_id: null,
          call_execution_id: null, prototype_run_id: null, dataset_row_id: null, organization_id: actors[seed.project].organizationId,
          workspace_id: actors[seed.project].workspaceId, deleted: false }))));
        try {
          await expect.poll(async () => {
            initialCHScores = await readCH();
            return { scores: initialCHScores.map(semanticScore), labels: await probe.pg(labelSql, [labelName, labelIds]),
              queues: await probe.pg(queueSql, [projectIds, queues.map(row => row.id)]), links: await probe.pg(linkSql, [queues.map(row => row.id)]) };
          }, POLL.CDC_VISIBLE).toEqual({ scores: initialScores, labels: initialLabels, queues: initialQueues, links: initialLinks });
          for (const row of initialCHScores) expect(BigInt(row.version)).toBeGreaterThan(0n);
          expect(initialScores.find(row => row.trace_id === seeds[0].traceId)!.value.text).toBe(EXPECTED_STORED_PRIMARY_TEXT);
          expect(semanticScore(initialCHScores.find(row => row.trace_id === seeds[0].traceId)!).value.text).toBe(EXPECTED_STORED_PRIMARY_TEXT);
          await conserveSource('post-annotation-source-conservation');
        } finally { await attach('combined-label-score-barrier', { initialLabels, initialQueues, initialLinks, initialScores, initialCHScores, initialItems }); }
      }, { timeout: POLL.CDC_VISIBLE.timeout });
      const positive: Oracle = { value: 1, traces: 1, scoreDay: scoreDay(seeds[0].traceId), grouped: true };

      await test.step('2 create and name a Table widget with 7D and Day', async () => {
        await page.goto('/dashboard/dashboards', { waitUntil: 'domcontentloaded' });
        let since = Date.now(); await page.getByRole('button', { name: 'Create Dashboard', exact: true }).click();
        const created = await readNative<{ result: { id: string } }>(DASHBOARDS, undefined, since);
        dashboardId = created.body!.result.id; expect(dashboardId).toMatch(/^[0-9a-f-]{36}$/); await attach('dashboard-id', { dashboardId, dashboardName, widgetName });
        await page.getByRole('heading', { name: 'Untitled', exact: true }).click(); await page.getByPlaceholder('Untitled Dashboard').fill(dashboardName);
        since = Date.now(); await page.getByPlaceholder('Untitled Dashboard').press('Enter');
        const path = `${DASHBOARDS}${dashboardId}/`;
        await expect.poll(() => receipts.some(r => r.path === path && ['PATCH', 'PUT'].includes(r.method) && r.startedAt >= since && r.settled), { timeout: UI_READY }).toBe(true);
        // Inspect every observed write verb, so a failed PATCH cannot hide behind a PUT200.
        for (const method of new Set(receipts.filter(r => r.path === path && r.startedAt >= since && ['PATCH', 'PUT'].includes(r.method)).map(r => r.method))) {
          const named = await readNative<unknown>(path, undefined, since, method); expect(named.input).toMatchObject({ name: dashboardName });
        }
        await page.getByRole('button', { name: 'Add Widget', exact: true }).first().click();
        await page.getByText('Untitled widget', { exact: true }).click(); await page.getByPlaceholder('Untitled widget').fill(widgetName);
        await page.getByPlaceholder('Untitled widget').press('Enter');
        await page.getByRole('combobox').filter({ hasText: 'Line' }).click(); await page.getByRole('option', { name: 'Table', exact: true }).click();
        await page.getByText('7D', { exact: true }).click(); await page.getByRole('combobox').filter({ hasText: 'Day' }).click();
        await page.getByRole('option', { name: 'Day', exact: true }).click();
      }, { timeout: UI_READY });

      await test.step('3 discover text annotation and Traces with exact scope', async () => {
        for (const [index, property] of [ownProperty, traceProperty].entries()) {
          if (index) await page.locator('.metric-section-title').click(); else await page.getByText('Select Metric', { exact: true }).click();
          for (const category of ['All', index ? 'Traces' : 'Annotations'] as const) await searchCatalog(property, 'metric', category);
          await page.getByRole('button', { name: optionName(property), exact: true }).click();
          await page.locator(`p[title="${property.display_name}"]`).locator('../..').locator('.MuiChip-clickable').click();
          await page.getByText(index ? 'Distinct Count' : 'Count', { exact: true }).last().click();
        }
        for (const owner of [actor, foreignActor, emptyActor]) {
          const input = { cursor_mode: true, category: 'annotation_metric', search: labelName, role: 'metric', page_size: 25 };
          const response = await scopeActors.send<CatalogBody>(owner, 'POST', METRICS, input);
          scopeReceipts.push({ path: METRICS, input, scope: scopeOf(owner), ...response });
          expect(response.status).toBe(200); expect(response.body.result).toMatchObject({ ...metadata, has_more: false, next_cursor: null });
          const expected = owner === emptyActor ? [] : [annotationProperty(labelIds[owner === foreignActor ? 1 : 0])];
          expect(response.body.result.metrics.map(row => row.property_id)).toEqual(expected.map(row => row.property_id));
          for (const [index, property] of expected.entries()) {
            const row = response.body.result.metrics[index]; expect(row).toMatchObject(property);
            expect(row).not.toHaveProperty('choices'); expect(row).not.toHaveProperty('choice_options');
          }
        }
      }, { timeout: UI_READY });

      await test.step('4 select primary Project and prove positive and empty scopes', async () => {
        await queryAfter('primary-unfiltered', configFor(), { ...positive, traces: 2, grouped: false }, selectProject);
        for (const control of [
          { owner: actor, project: projectIds[1], label: labelIds[0], value: 1, traces: 1, day: scoreDay(seeds[2].traceId), name: 'sibling' },
          { owner: foreignActor, project: projectIds[2], label: labelIds[1], value: 1, traces: 1, day: scoreDay(seeds[3].traceId), name: 'foreign' },
          { owner: emptyActor, project: null, label: labelIds[0], value: null, traces: null, day: positive.scoreDay, name: 'empty' },
        ]) {
          const input = configFor(undefined, false, control.project, control.label), startedAt = Date.now();
          const response = await scopeActors.send<QueryBody>(control.owner, 'POST', QUERY, input);
          const receipt: Receipt<QueryBody> = { ...response, path: QUERY, method: 'POST', input, scope: scopeOf(control.owner), authorized: true,
            startedAt, endedAt: Date.now(), settled: true };
          scopeReceipts.push(receipt); await attach(`scope-${control.name}-query`, receipt);
          assertResult(receipt, input, { value: control.value, traces: control.traces, scoreDay: control.day, grouped: false });
        }
      }, { timeout: UI_READY });

      await test.step('5 Specify exact A, prove disjoint B and substring C empty, and restore grouped A', async () => {
        await queryAfter('is-A', configFor(mintedA), { ...positive, grouped: false }, async () => {
          const since = Date.now(); await chooseProperty(ownProperty);
          await uiExpect(card(labelName).getByRole('combobox')).toHaveText('Is');
          await uiExpect(popup).toHaveCount(1);
          const receipt = await readNative<ValueBody>(VALUES, textValuesInput, since);
          expect(receipt.body!.result).toEqual(emptyTextValues);
          await uiExpect(popup.getByText('No values found', { exact: true })).toBeVisible();
          await uiExpect(popup.getByRole('button', { name: 'Add', exact: true })).toBeDisabled();
          await selectText(mintedA);
        });
        await queryAfter('label-breakdown', canonical, positive, () => chooseProperty(ownProperty, true)); await assertControls();
        await queryAfter('disjoint-B', configFor(mintedB, true), { ...positive, value: null, traces: null }, () => selectText(mintedB, mintedA));
        await queryAfter('substring-C', configFor(mintedC, true), { ...positive, value: null, traces: null }, () => selectText(mintedC, mintedB));
        await queryAfter('restored-A', canonical, positive, () => selectText(mintedA, mintedC)); await assertControls();
        expect([...verifiedTextSearches].sort()).toEqual([mintedA, mintedB, mintedC].sort());
        // Text has no configured choices. Empty VALUES alone is not positive isolation
        // evidence; catalog identities, typed Scores and scoped query controls supply it.
        for (const control of [
          { owner: actor, project: '', label: labelIds[0] }, { owner: actor, project: projectIds[0], label: labelIds[0] },
          { owner: actor, project: projectIds[1], label: labelIds[0] }, { owner: foreignActor, project: projectIds[2], label: labelIds[1] },
          { owner: emptyActor, project: '', label: labelIds[0] }, { owner: actor, project: '', label: labelIds[1] },
          { owner: foreignActor, project: '', label: labelIds[0] },
        ]) {
          const input = { property_id: `annotation:${control.label}`, metric_name: control.label, metric_type: 'annotation_metric',
            source: 'traces', project_ids: control.project, page_size: 10 };
          const response = await scopeActors.send<ValueBody>(control.owner, 'POST', VALUES, input);
          scopeReceipts.push({ path: VALUES, input, scope: scopeOf(control.owner), ...response }); expect(response.status).toBe(200);
          expect(response.body.result).toEqual(emptyTextValues);
        }
      }, { timeout: UI_READY });

      await test.step('6 save canonical binding and verify persisted Table and ownership', async () => {
        const since = Date.now(); await page.getByRole('button', { name: 'Save', exact: true }).click();
        const saved = await readNative<{ result: Widget }>(`${DASHBOARDS}${dashboardId}/widgets/`, undefined, since);
        expect(saved.input.name).toBe(widgetName); expect(saved.input.query_config).toEqual(canonical);
        originalChart = structuredClone(saved.input.chart_config) as Widget['chart_config']; expect(originalChart.chart_type).toBe('table');
        savedWidget = saved.body!.result; expect(savedWidget.id).toMatch(/^[0-9a-f-]{36}$/); expect(savedWidget.name).toBe(widgetName);
        expect(savedWidget.query_config).toEqual(canonical); expect(savedWidget.chart_config).toEqual(originalChart);
        await verifySaved(); await uiExpect(page).toHaveURL(new RegExp(`/dashboard/dashboards/${dashboardId}$`));
        await assertTable(positive, lastBuckets, true); await testInfo.attach('saved-table', { contentType: 'image/png', body: await page.screenshot() });
      }, { timeout: UI_READY });
      await test.step('7 reload saved dashboard and verify both independent-clock series', async () => {
        await queryAfter('reload', canonical, positive, () => page.reload({ waitUntil: 'domcontentloaded' }), true);
      }, { timeout: UI_READY });
      await test.step('8 reopen exact controls and values and conserve all versions', async () => {
        await queryAfter('reopen', canonical, positive, () => page.locator(`[data-widget-id="${savedWidget!.id}"]`).getByText(widgetName, { exact: true }).click(), false, true);
        await assertControls(); await verifySaved(); await conserveSource('final-source-conservation');
        const finalScores = await readPG(), finalCHScores = await readCH(), finalLabels = await probe.pg(labelSql, [labelName, labelIds]);
        const finalQueues = await probe.pg(queueSql, [projectIds, queues.map(row => row.id)]), finalLinks = await probe.pg(linkSql, [queues.map(row => row.id)]);
        const finalItems = await probe.pg(itemSql, [queues.map(row => row.id)]);
        await attach('final-definition-score-version-conservation', { initialScores, finalScores, initialCHScores, finalCHScores,
          initialLabels, finalLabels, initialQueues, finalQueues, initialLinks, finalLinks, initialItems, finalItems });
        expect(finalScores).toEqual(initialScores); expect(finalCHScores).toEqual(initialCHScores); expect(finalCHScores.map(semanticScore)).toEqual(initialScores);
        expect(finalLabels).toEqual(initialLabels); expect(finalQueues).toEqual(initialQueues); expect(finalLinks).toEqual(initialLinks); expect(finalItems).toEqual(initialItems);
      }, { timeout: UI_READY });
    } finally {
      for (const surface of [page, foreignPage]) { surface.off('request', onRequest); surface.off('response', onResponse); surface.off('requestfailed', onFailed); }
      await Promise.all(pending); // Settle captured semantic errors before publishing final receipt states.
      await attach('native-request-states', receipts.map(({ body, ...head }) => head));
      await attach('native-reads-writes-scope', { receipts, scopeReceipts });
    }
  } finally { await context.close(); await foreignContext.close(); }
});
