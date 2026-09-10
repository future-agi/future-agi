import { createHash, randomBytes, randomUUID } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { request as playwrightRequest, type Request, type Response } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace, type OtlpAttributes } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// Public routes: tracer/urls.py; ProjectSerializer; TraceVoiceCallListQuerySerializer;
// dashboard serializers. CallLogs/helper.jsx uses readQuery on this same list.
const PROJECT = '/tracer/project/';
const LIST = '/tracer/trace/list_voice_calls/';
const METRICS = '/tracer/dashboard/metrics/';
const VALUES = '/tracer/dashboard/filter_values/';
// accounts/views/user.py CustomTokenRefreshView; jwt/utils.js refreshTokenRequest.
const REFRESH = '/accounts/token/refresh/';
const UI_READY = 60_000;
const VOICE_ID = 'system_attribute:voice_calls:';
// Independent frozen source_adapters.py voice manifest, not an import of the
// product implementation. Trace/shared definitions may also be returned.
const DIMENSIONS = ['call_status', 'call_id', 'call_type', 'ended_reason'];
const NUMBERS = ['cost_cents', 'duration', 'turn_count', 'agent_talk_percentage',
  'avg_agent_latency_ms', 'bot_wpm', 'user_wpm', 'user_interruption_count', 'user_interruption_rate',
  'ai_interruption_count', 'ai_interruption_rate', 'talk_ratio', 'agent_latency', 'ai_interruptions',
  'user_interruptions', 'stop_time_after_interruption', 'llm_cost', 'stt_cost', 'tts_cost',
  'total_cost', 'customer_cost', 'llm_latency', 'stt_latency', 'tts_latency', 'response_time'];
// voiceCallFilterFields.js: static fields and aliases owned by those fields.
const STATIC_FIELDS = ['call_id', 'call_status', 'duration', 'avg_agent_latency_ms', 'turn_count',
  'talk_ratio', 'gen_ai.usage.total_tokens', 'cost_cents', 'user_interruption_count',
  'ai_interruption_count', 'ended_reason', 'call_type', 'user_wpm', 'bot_wpm', 'agent_talk_percentage'];
const ALIASES = ['agent_latency', 'user_interruptions', 'ai_interruptions', 'total_cost'];
const STATUS_CHOICES = ['completed', 'in-progress', 'failed', 'dropped', 'not-connected'];
const sorted = <T>(rows: T[]) => [...rows].sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
type Wire = Record<string, unknown>;
type Leaf = { column_id: string; property_id?: string; filter_config: {
  col_type: string; filter_type: string; filter_op: string; filter_value: unknown;
  attribute_value_types?: string[] } };
type Project = { id: string; name: string };
type Fact = { code: string; project: Project; traceId: string; root: string; child: string; at: number;
  callId: string; rootAttrs: OtlpAttributes; childAttrs: OtlpAttributes; status: string;
  duration: number; cost: number; latency: number; eligible: boolean };
type Source = { org_id: string; project_id: string; trace_id: string; id: string; parent_span_id: string;
  observation_type: string; start_us: string; end_us: string; is_deleted: number;
  attrs_string: Record<string, string>; attrs_number: Record<string, number>; extra: string };
type CallRow = { id: string; trace_id: string; project_id: string; call_id: string; status: string;
  duration_seconds: number; cost_cents: number; avg_agent_latency_ms: number;
  observation_span: { id: string; observation_type: string; parent_span_id: null }[];
  [key: string]: unknown };
// Unlike Users/Trace, this endpoint has a TOP-LEVEL results envelope and no
// query_exact/ordering_exact. TraceVoiceCallListResponseSerializer is authoritative.
type CallPage = { results: CallRow[]; count: number; count_is_lower_bound: boolean;
  has_more: boolean; next_cursor: string | null; query_complete: boolean; query_status: string;
  query_applied_filter_version?: string; query_applied_filter_count?: number };
type Metric = { property_id: string; type: string; role: string; unit?: string; source: string };
type MetricPage = { result: { metrics: Metric[]; has_more: boolean; next_cursor: string | null;
  query_complete: boolean; query_exact: boolean; query_status: string } };
type Value = { value: unknown; type?: string; label: string };
type ValuePage = { result: { values: Value[]; has_more: boolean; next_cursor: string | null;
  query_complete: boolean; query_status: string } };
type Capture = { path: string; method: string; query: Wire; status: number; document: number; order: number; credential?: string;
  organization?: string; workspace?: string; authenticated: boolean; body?: unknown; error?: string };
type RefreshProof = { document: number; requestOrder: number; responseOrder?: number; status: number;
  sameActor: boolean; credential?: string; error?: string };
const credentialFingerprint = (token: unknown): string | undefined => typeof token === 'string' && token.length > 0
  ? createHash('sha256').update(token).digest('hex') : undefined;
const usesSeedRefresh = (body: unknown, seed: string): boolean => Boolean(seed) && typeof body === 'object'
  && body !== null && !Array.isArray(body) && (body as Wire).refresh === seed;
const credentialProved = (credential: string | undefined, order: number, seed: string | undefined,
  refreshes: RefreshProof[]): boolean => Boolean(credential && (credential === seed || refreshes.some(r =>
    r.sameActor && r.status === 200 && r.error === undefined && r.credential === credential
    && r.responseOrder !== undefined && r.requestOrder < r.responseOrder && r.responseOrder < order)));
type Case = { name: string; leaf: Leaf; expected: Fact[]; control: 'choices' | 'text' | 'number'; manual?: boolean };
const requestBody = (request: Request): Wire => request.method() === 'POST'
  ? request.postDataJSON() as Wire : Object.fromEntries(new URL(request.url()).searchParams);
const leaves = (query: Wire): Leaf[] => typeof query.filters === 'string'
  ? JSON.parse(query.filters) as Leaf[] : query.filters as Leaf[];
// filter-contract.js normalizes UI string -> text, preserves typed custom
// suggestions and emits scalar numeric/text equals versus arrays for in.
const leaf = (column: string, type: 'text' | 'number', op: 'in' | 'equals', value: unknown, custom = false): Leaf => ({
  column_id: column, property_id: custom ? `custom_attribute:${column}` : VOICE_ID + column,
  filter_config: { col_type: custom ? 'SPAN_ATTRIBUTE' : 'SYSTEM_METRIC', filter_type: type,
    filter_op: op, filter_value: value, ...(custom && op === 'in' ? { attribute_value_types: (value as unknown[]).map(() => 'string') } : {}) },
});

test('OBS-E2E-008: native and custom voice filters select only the current project calls', {
  tag: ['@flow'],
  annotation: flowAnnotation({ id: 'OBS-E2E-008', area: 'observe',
    userGoal: 'Discover voice-call properties and use their native and custom filters to find exactly the calls belonging to the selected project',
    steps: ['ingest two distinct synthetic calls and scoped non-call/sibling controls',
      "open the project's call table and discover native and custom filter properties",
      'choose call status, identity and numeric filters and read exact matching calls',
      'choose root and child custom suggestions, including multiple and zero matches',
      'clear the filter, inspect the sibling project and refresh the primary call table'],
    backendChecks: [
      'authoritative conversation roots, child spans and non-call controls retain their exact project and actor scope',
      'voice definitions and scoped native/custom values preserve their canonical identities, types and metric eligibility',
      'actual voice UI requests and complete call-list pages return exactly the independently expected calls for positive, multiple, zero and refreshed scopes',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // 15 source + 60 catalog + 15*60 UI (opening, eligibility, 7 native, 5 custom, final scope) + 45 headroom.
  test.setTimeout(1_020_000); // Every individual UI stage/case still has UI_READY=60s.
  page.setDefaultTimeout(UI_READY);
  const prefix = `e2e-obs8-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const routeKey = `${prefix}.route`, childKey = `${prefix}.child`;
  const missing = `${prefix}-not-seeded`, childValue = `${prefix}-child-b`;
  const projects: Project[] = [], facts: Fact[] = [], evidence: unknown[] = [], captures: Capture[] = [];
  const pending: Promise<void>[] = [];
  const requests = new Map<Request, Capture>();
  const refreshes = new Map<Request, RefreshProof>();
  const refreshReads: { proof: RefreshProof; done: Promise<void> }[] = [];
  const authFailures: { path: string; status: number; responseOrder: number }[] = [];
  const seedCredential = credentialFingerprint(actor.tokens.access);
  let document = 0, sequence = 0;
  const authenticate = (c: Capture) => {
    c.authenticated = credentialProved(c.credential, c.order, seedCredential, [...refreshes.values()]);
  };
  const priorRefreshReads = (order: number) => Promise.all(refreshReads
    .filter(r => r.proof.responseOrder! < order).map(r => r.done));
  const attach = (name: string, value: unknown) => testInfo.attach(name, {
    body: JSON.stringify(value, null, 2), contentType: 'application/json',
  });
  const started = (request: Request) => {
    const order = ++sequence;
    if (request.isNavigationRequest() && request.frame() === page.mainFrame()) document++;
    const url = new URL(request.url()), path = url.pathname;
    if (url.origin !== new URL(E2E.apiUrl).origin || request.method() === 'OPTIONS') return;
    if (path === REFRESH && request.method() === 'POST') {
      let sameActor = false;
      try { sameActor = usesSeedRefresh(request.postDataJSON(), actor.tokens.refresh); } catch { /* Unproved, never log bodies. */ }
      refreshes.set(request, { document, requestOrder: order, status: 0, sameActor });
      return;
    }
    if (![LIST, METRICS, VALUES, '/tracer/eval-attributes/'].includes(path)) return;
    const h = request.headers();
    const entry: Capture = { path, method: request.method(), query: requestBody(request), document, order,
      status: 0, organization: h['x-organization-id'], workspace: h['x-workspace-id'],
      credential: credentialFingerprint(h.authorization?.startsWith('Bearer ') ? h.authorization.slice(7) : undefined),
      authenticated: false };
    authenticate(entry);
    requests.set(request, entry); captures.push(entry);
  };
  const listener = (response: Response) => {
    const responseOrder = ++sequence, url = new URL(response.url());
    // Keep infrastructure-induced 401s visible even when the app subsequently
    // refreshes. No error bodies, headers or credentials enter these receipts.
    if (url.origin === new URL(E2E.apiUrl).origin && response.status() === 401)
      authFailures.push({ path: url.pathname, status: 401, responseOrder });
    const refresh = refreshes.get(response.request());
    if (refresh) {
      refresh.status = response.status(); refresh.responseOrder = responseOrder;
      // Order is recorded at the observed response, NOT at async JSON completion.
      // A later issuance must never retroactively authorize an earlier request.
      const done = response.json().then(body => { refresh.credential = credentialFingerprint(body?.access); },
        () => { refresh.error = 'refresh-body-unreadable'; });
      refreshReads.push({ proof: refresh, done }); pending.push(done);
      return;
    }
    const entry = requests.get(response.request());
    if (!entry) return;
    entry.status = response.status();
    pending.push(response.json().then(async body => {
      await priorRefreshReads(entry.order); authenticate(entry); entry.body = body;
    }, () => { entry.error = 'response-body-unreadable'; }));
  };
  const failed = (request: Request) => {
    const entry = requests.get(request);
    if (entry) entry.error = 'request-failed'; // Never serialize exception bodies or headers.
    const refresh = refreshes.get(request);
    if (refresh) refresh.error = 'request-failed';
  };
  page.on('request', started); page.on('requestfailed', failed);
  page.on('response', listener);
  const req = await playwrightRequest.newContext();
  let outcome = 'running', failure: string | undefined;
  try {
    const headers = { Authorization: `Bearer ${actor.tokens.access}`,
      'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId };
    // pgresolver.go permits an unambiguous original default workspace for a
    // workspace-less system key. Verify that relationship; never retarget keys.
    const keys = await probe.pg<{ id: string; organization_id: string; workspace_id: string | null; type: string }>(
      'SELECT id,organization_id,workspace_id,type FROM accounts_orgapikey WHERE api_key=$1 AND enabled=true AND deleted=false', [actor.apiKey]);
    expect(keys).toHaveLength(1); expect(keys[0].organization_id).toBe(actor.organizationId);
    if (keys[0].workspace_id === null) {
      expect(keys[0].type).toBe('system');
      const defaults = await probe.pg<{ id: string }>('SELECT id FROM accounts_workspace WHERE organization_id=$1 AND is_default=true AND is_active=true AND deleted=false', [actor.organizationId]);
      expect(defaults).toStrictEqual([{ id: actor.workspaceId }]);
    } else expect(keys[0].workspace_id).toBe(actor.workspaceId);
    await attach('key-scope-no-secrets', keys);
    for (const label of ['primary', 'sibling']) {
      const payload = { name: `${prefix}-${label}`, model_type: 'GenerativeLLM', trace_type: 'observe', source: 'simulator' };
      const response = await req.post(E2E.apiUrl + PROJECT, { headers, data: payload });
      const body = await response.json() as { result: { project_id: string; name: string } };
      evidence.push({ phase: 'public project create', status: response.status(), payload, body });
      expect(response.status()).toBe(200); expect(body.result.name).toBe(payload.name);
      expect(body.result.project_id).toMatch(/^[0-9a-f-]{36}$/);
      projects.push({ id: body.result.project_id, name: payload.name });
    }
    const [primary, sibling] = projects;
    const baseTime = Math.floor((Date.now() - 300_000) / 1000) * 1000;
    for (const [i, code] of ['A', 'B', 'N', 'S'].entries()) {
      const b = code === 'B', project = code === 'S' ? sibling : primary;
      const callId = `${prefix}-${code === 'N' ? 'not-a-call' : b ? 'call-b' : 'call-a'}`;
      const duration = b ? 25 : 12, cost = b ? 250 : 125, latency = b ? 800 : 200;
      const rootAttrs: OtlpAttributes = { 'fi.span.kind': code === 'N' ? 'chain' : 'conversation',
        metadata: { call_execution_id: callId }, 'call.status': b ? 'failed' : 'ended',
        'call.duration': duration, combined_cost: cost, avg_agent_latency_ms: latency,
        call_type: b ? 'outbound' : 'inbound', ended_reason: `${prefix}-${b ? 'error' : 'normal'}`,
        'call.total_turns': b ? 8 : 4, 'call.talk_ratio': b ? 3 : 1,
        'call.user_wpm': b ? 140 : 100, 'call.bot_wpm': b ? 160 : 120,
        user_interruption_count: b ? 2 : 0, ai_interruption_count: b ? 3 : 1,
        [routeKey]: code === 'S' ? `${prefix}-sibling-only` : b ? 'beta' : 'alpha' };
      const childAttrs: OtlpAttributes = { 'fi.span.kind': 'llm', ...(b ? {
        [childKey]: childValue, metadata: { call_execution_id: `${prefix}-child-decoy` }, 'call.status': 'in-progress',
      } : {}) };
      facts.push({ code, project, traceId: randomUUID(), root: randomBytes(8).toString('hex'),
        child: randomBytes(8).toString('hex'), at: baseTime + i * 30_000, callId,
        rootAttrs, childAttrs, status: b ? 'failed' : 'completed', duration, cost, latency, eligible: code !== 'N' });
    }
    await attach('seed-identities-before-ingestion', { prefix, projects, facts });
    for (const f of facts) {
      expect(await sendTrace(req, { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
        projectName: f.project.name, rootName: `${prefix}.${f.code}`, childName: `${prefix}.${f.code}.child`,
        traceId: f.traceId, rootSpanId: f.root, childSpanId: f.child,
        startTimeUnixNano: BigInt(f.at) * 1_000_000n, endTimeUnixNano: BigInt(f.at + f.duration * 1000) * 1_000_000n,
        rootAttributes: f.rootAttrs, childAttributes: f.childAttrs, resourceAttributes: { project_type: 'observe' },
      })).toStrictEqual({ traceId: f.traceId, spanIds: [f.root, f.child], projectName: f.project.name });
    }
    const [a, b, , s] = facts;
    const projectRows = () => probe.pg<{ id: string; name: string; organization_id: string; workspace_id: string; source: string }>(
      'SELECT id,name,organization_id,workspace_id,source FROM tracer_project WHERE id=ANY($1::uuid[]) AND deleted=false ORDER BY id', [projects.map(p => p.id)]);
    await test.step('authoritative scoped source facts', async () => {
      await expect.poll(async () => {
        const spans = await probe.ch<Source>('SELECT org_id,project_id,trace_id,id,parent_span_id,observation_type,'
          + 'toString(toUnixTimestamp64Micro(start_time)) AS start_us,toString(toUnixTimestamp64Micro(end_time)) AS end_us,'
          + 'is_deleted,attrs_string,attrs_number,toString(attributes_extra) AS extra FROM spans FINAL'
          + ' WHERE project_id IN (SELECT arrayJoin(JSONExtract({p:String},\'Array(UUID)\'))) ORDER BY id'
          + ' SETTINGS readonly=1,max_threads=1', { p: JSON.stringify(projects.map(p => p.id)) });
        const traces = await probe.ch<{ id: string; project_id: string; is_deleted: number }>(
          'SELECT id,project_id,is_deleted FROM traces FINAL WHERE project_id IN (SELECT arrayJoin(JSONExtract({p:String},\'Array(UUID)\')))'
          + ' ORDER BY toString(id) SETTINGS readonly=1,max_threads=1', { p: JSON.stringify(projects.map(p => p.id)) });
        evidence.push({ phase: 'source poll', spans, traces });
        return { spans: sorted(spans.map(r => ({ org_id: r.org_id, project_id: r.project_id, trace_id: r.trace_id,
          id: r.id, parent_span_id: r.parent_span_id, observation_type: r.observation_type,
          start_us: r.start_us, end_us: r.end_us, is_deleted: r.is_deleted }))), traces };
      }, POLL.SPAN_VISIBLE).toStrictEqual({ spans: sorted(facts.flatMap(f => [false, true].map(child => ({
        org_id: actor.organizationId, project_id: f.project.id, trace_id: f.traceId, id: child ? f.child : f.root,
        parent_span_id: child ? f.root : '', observation_type: child ? 'llm' : f.eligible ? 'conversation' : 'chain',
        start_us: String(BigInt(f.at) * 1000n), end_us: String(BigInt(f.at + f.duration * 1000) * 1000n), is_deleted: 0,
      })))), traces: facts.map(f => ({ id: f.traceId, project_id: f.project.id, is_deleted: 0 })).sort((x, y) => x.id.localeCompare(y.id)) });
      const rows = (evidence.at(-1) as { spans: Source[] }).spans;
      for (const f of facts) for (const child of [false, true]) {
        const row = rows.find(r => r.id === (child ? f.child : f.root))!;
        const attrs = child ? f.childAttrs : f.rootAttrs, extra = JSON.parse(row.extra) as Wire;
        for (const [key, value] of Object.entries(attrs)) {
          if (typeof value === 'string') expect(row.attrs_string[key]).toBe(value);
          else if (typeof value === 'number') expect(row.attrs_number[key]).toBe(value);
          else expect(extra[key]).toStrictEqual(value);
        }
        if (child) expect(row.attrs_string[routeKey]).toBeUndefined();
        else expect(row.attrs_string[childKey]).toBeUndefined();
        expect(extra.raw_log).toBeUndefined();
      }
      const pg = await projectRows();
      expect(sorted(pg)).toStrictEqual(sorted(projects.map(p => ({ ...p, organization_id: actor.organizationId,
        workspace_id: actor.workspaceId, source: 'simulator' }))));
      await attach('source-and-projects', { rows, pg });
    }, { timeout: POLL.SPAN_VISIBLE.timeout });

    const expectedRows = (expected: Fact[]) => [...expected].sort((x, y) => y.at - x.at || y.traceId.localeCompare(x.traceId))
      .map(f => ({ id: f.traceId, trace_id: f.traceId, project_id: f.project.id, call_id: f.callId, status: f.status,
        duration_seconds: f.duration, cost_cents: f.cost, avg_agent_latency_ms: f.latency,
        observation_span: [{ id: f.root, observation_type: 'conversation', parent_span_id: null }] }));
    const rowIdentity = (row: CallRow) => ({ id: row.id, trace_id: row.trace_id, project_id: row.project_id, call_id: row.call_id,
      status: row.status, duration_seconds: row.duration_seconds, cost_cents: row.cost_cents,
      avg_agent_latency_ms: row.avg_agent_latency_ms, observation_span: row.observation_span });
    const checkPage = (body: CallPage, terminal = false) => {
      expect(body.query_complete).toBe(true); expect(body.query_status).toBe('complete');
      expect(Array.isArray(body.results)).toBe(true);
      expect(typeof body.has_more).toBe('boolean'); expect(typeof body.count_is_lower_bound).toBe('boolean');
      expect(Number.isSafeInteger(body.count) && body.count >= 0).toBe(true);
      if (terminal) expect(body.has_more).toBe(false);
      if (!body.has_more) { expect(body.next_cursor).toBe(null); expect(body.count_is_lower_bound).toBe(false); }
      else expect(body.next_cursor).toEqual(expect.any(String));
    };
    const walkCalls = async (query: Wire, expected: Fact[]) => {
      const rows: CallRow[] = [], pages: CallPage[] = [], seen = new Set<string>();
      let cursor: string | undefined;
      for (let i = 0; i < 4; i++) {
        const body = await actor.api.post<CallPage>(LIST, { ...query, ...(cursor ? { cursor } : {}) });
        pages.push(body); checkPage(body); rows.push(...body.results);
        if (!body.has_more) break;
        expect(body.next_cursor).toBeTruthy(); expect(seen.has(body.next_cursor!)).toBe(false);
        seen.add(body.next_cursor!); cursor = body.next_cursor!;
      }
      evidence.push({ phase: 'call-list API', query, pages });
      checkPage(pages.at(-1)!, true);
      expect(rows.map(rowIdentity)).toStrictEqual(expectedRows(expected));
      expect(pages.at(-1)!.count).toBe(expected.length);
      expect(new Set(rows.map(r => r.trace_id)).size).toBe(rows.length);
      return pages;
    };
    const catalog = async (project: Project, category: string, role = '', search = '') => {
      const metrics: Metric[] = [], pages: MetricPage[] = [], seen = new Set<string>();
      let cursor: string | undefined;
      for (let i = 0; i < 4; i++) {
        const body = await actor.api.post<MetricPage>(METRICS, { source: 'voice_calls', project_ids: project.id,
          category, cursor_mode: true, page_size: 50, ...(role ? { role } : {}), search, ...(cursor ? { cursor } : {}) });
        pages.push(body); metrics.push(...body.result.metrics);
        expect(body.result.query_complete).toBe(true); expect(body.result.query_status).toBe('complete');
        if (!body.result.has_more) { expect(body.result.next_cursor).toBe(null); break; }
        expect(body.result.next_cursor).toBeTruthy(); expect(seen.has(body.result.next_cursor!)).toBe(false);
        seen.add(body.result.next_cursor!); cursor = body.result.next_cursor!;
      }
      expect(pages.at(-1)!.result.has_more).toBe(false);
      expect(new Set(metrics.map(m => m.property_id)).size).toBe(metrics.length);
      evidence.push({ phase: 'definitions', project, category, role, search, pages });
      return metrics;
    };
    const values = async (project: Project, property: string, source: string, pageSize = 25) => {
      const options: Value[] = [], pages: ValuePage[] = [], seen = new Set<string>();
      let cursor: string | undefined;
      for (let i = 0; i < 4; i++) {
        const body = await actor.api.post<ValuePage>(VALUES, { project_ids: project.id, property_id: property,
          source, page_size: pageSize, ...(cursor ? { cursor } : {}) });
        pages.push(body); options.push(...body.result.values);
        expect(body.result.query_complete).toBe(true); expect(body.result.query_status).toBe('complete');
        if (!body.result.has_more) { expect(body.result.next_cursor).toBe(null); break; }
        expect(body.result.next_cursor).toBeTruthy(); expect(seen.has(body.result.next_cursor!)).toBe(false);
        seen.add(body.result.next_cursor!); cursor = body.result.next_cursor!;
      }
      expect(pages.at(-1)!.result.has_more).toBe(false);
      expect(new Set(options.map(o => JSON.stringify([o.value, o.type]))).size).toBe(options.length);
      evidence.push({ phase: 'values', project, property, source, pageSize, pages });
      return options;
    };
    await test.step('native/custom catalog identity, types and role eligibility', async () => {
      // Readiness polling is only for the newly ingested custom keys. HTTP
      // contract errors are thrown immediately; they are not retried as empty.
      let ready: Value[] = [];
      const until = Date.now() + POLL.ASYNC_JOB.timeout;
      do {
        ready = await values(primary, `custom_attribute:${routeKey}`, 'traces');
        if (ready.length) break;
        expect(Date.now()).toBeLessThan(until);
        await new Promise(resolve => setTimeout(resolve, 500));
      } while (true);
      expect(sorted(ready.map(v => ({ value: v.value, type: v.type })))).toStrictEqual([
        { value: 'alpha', type: 'string' }, { value: 'beta', type: 'string' }]);
      for (const role of ['', 'metric', 'dimension']) {
        const rows = await catalog(primary, 'system_metric', role), voice = rows.filter(m => m.property_id.startsWith(VOICE_ID));
        const expected = [...(role !== 'metric' ? DIMENSIONS.map(name => ({ property_id: VOICE_ID + name, type: 'string', role: 'dimension' })) : []),
          ...(role !== 'dimension' ? NUMBERS.map(name => ({ property_id: VOICE_ID + name, type: 'number', role: 'metric' })) : [])];
        expect(sorted(voice.map(({ property_id, type, role: r }) => ({ property_id, type, role: r })))).toStrictEqual(sorted(expected));
        expect(rows.every(m => ['voice_calls', 'traces', 'all', 'both'].includes(m.source))).toBe(true);
        for (const [name, unit] of Object.entries({ duration: 's', cost_cents: 'cents', avg_agent_latency_ms: 'ms', agent_talk_percentage: '%' })) {
          if (role !== 'dimension') expect(voice.find(m => m.property_id === VOICE_ID + name)!.unit).toBe(unit);
        }
      }
      for (const project of projects) {
        const expected = project === primary ? [a, b] : [s];
        for (const source of ['voice_calls', 'traces']) for (const name of DIMENSIONS) {
          const actual = await values(project, VOICE_ID + name, source);
          const exact = expected.map(f => name === 'call_status' ? f.status : name === 'call_id' ? f.callId : f.rootAttrs[name]);
          expect(sorted(actual.map(v => v.value))).toStrictEqual(sorted([...new Set(exact)]));
          expect(actual.every(v => typeof v.value === 'string')).toBe(true);
        }
        expect((await catalog(project, 'custom_attribute', '', routeKey)).map(m => m.property_id)).toStrictEqual([`custom_attribute:${routeKey}`]);
        expect((await catalog(project, 'custom_attribute', '', childKey)).map(m => m.property_id))
          .toStrictEqual(project === primary ? [`custom_attribute:${childKey}`] : []);
        expect(sorted((await values(project, `custom_attribute:${routeKey}`, 'voice_calls', 1)).map(v => ({ value: v.value, type: v.type }))))
          .toStrictEqual(sorted((project === primary ? ['alpha', 'beta'] : [`${prefix}-sibling-only`]).map(value => ({ value, type: 'string' }))));
        expect((await values(project, `custom_attribute:${childKey}`, 'traces')).map(v => ({ value: v.value, type: v.type })))
          .toStrictEqual(project === primary ? [{ value: childValue, type: 'string' }] : []);
      }
      await walkCalls({ project_id: primary.id, page_size: 1, cursor_mode: true, remove_simulation_calls: false, filters: [] }, [a, b]);
      // This is a LOAD-BEARING independent native no-match witness: the child
      // has this raw status, but only conversation roots define call status.
      await walkCalls({ project_id: primary.id, page_size: 25, cursor_mode: true, remove_simulation_calls: false,
        filters: [leaf('call_status', 'text', 'in', ['in-progress'])] }, []);
    }, { timeout: POLL.ASYNC_JOB.timeout });

    const checkScope = (c: Capture, project: Project) => {
      expect([c.status, c.organization, c.workspace, c.authenticated, c.error])
        .toStrictEqual([200, actor.organizationId, actor.workspaceId, true, undefined]);
      expect(c.query.project_id).toBe(project.id);
    };
    const provenReads = new Set<Capture>();
    const readCapture = async (mark: number, path: string, predicate: (c: Capture) => boolean, cached?: Capture) => {
      const fresh = () => captures.slice(mark).filter(c => c.document === document && c.path === path && predicate(c));
      // Requests enter captures BEFORE a response exists. Never skip an in-flight
      // or failed newer request to select an older successful response.
      await expect.poll(() => {
        const reads = fresh();
        return reads.length ? reads.every(c => c.body !== undefined || c.error) : Boolean(cached);
      }, { timeout: UI_READY }).toBe(true);
      const scope = cached ? await browserScope(currentProject) : undefined;
      const reads = fresh(); // Include requests that started while browserScope awaited its proof.
      for (const c of reads.length ? reads : [cached!]) {
        authenticate(c);
        expect([c.document, c.status, c.authenticated, c.organization, c.workspace, c.error])
          .toStrictEqual([document, 200, true, actor.organizationId, actor.workspaceId, undefined]);
        if (cached) {
          expect(c.method).toBe(cached.method);
          expect(c.query).toStrictEqual(cached.query); // FULL query, not just property/project.
          expect(c.credential).toBe(cached.credential);
          expect(cached.credential).toBe(scope!.credential); // Never reuse across token rotation.
        }
      }
      const result = reads.at(-1) ?? cached!;
      for (const c of reads.length ? reads : [result]) provenReads.add(c);
      evidence.push({ phase: 'browser receipt', transport: reads.length ? 'fresh' : 'same-document cache',
        document, path, query: result.query });
      return result;
    };
    const browserScope = async (project: Project) => {
      expect(new URL(page.url()).pathname).toBe(`/dashboard/observe/${project.id}/llm-tracing`);
      const order = ++sequence;
      const state = await page.evaluate(async () => {
        const token = localStorage.getItem('accessToken');
        const digest = token ? await crypto.subtle.digest('SHA-256', new TextEncoder().encode(token)) : undefined;
        return { timeOrigin: performance.timeOrigin,
          credential: digest ? [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, '0')).join('') : undefined,
          organization: sessionStorage.getItem('organizationId'), workspace: sessionStorage.getItem('workspaceId'),
          autoRefresh: JSON.parse(localStorage.getItem('autoRefresh') ?? 'false') };
      });
      await priorRefreshReads(order);
      expect([credentialProved(state.credential, order, seedCredential, [...refreshes.values()]), state.organization, state.workspace])
        .toStrictEqual([true, actor.organizationId, actor.workspaceId]);
      // ObserveHeader reads this preference; this flow never toggles it or
      // invokes a grid refresh, the only in-document revision boundaries.
      expect(state.autoRefresh ?? false).toBe(false);
      return { document, ...state };
    };
    const checkDate = (query: Wire, project: Project) => {
      const date = leaves(query).find(f => f.column_id === 'created_at');
      expect(date?.filter_config.filter_type).toBe('datetime'); expect(date?.filter_config.filter_op).toBe('between');
      const window = date!.filter_config.filter_value as string[];
      expect(window).toHaveLength(2);
      const [from, to] = window.map(Date.parse);
      expect(Number.isFinite(from) && Number.isFinite(to)).toBe(true);
      for (const f of facts.filter(f => f.project === project)) {
        expect(from).toBeLessThanOrEqual(f.at); expect(to).toBeGreaterThan(f.at + f.duration * 1000);
      }
      expect(to - from).toBeLessThanOrEqual(8 * 86_400_000);
    };
    const cells = (column: string) => page.locator(`.clean-data-table:visible .ag-row [col-id=${JSON.stringify(column)}]`);
    const showIdentityColumns = async () => {
      // helper.jsx hides call_id by default. Use real Display -> View columns;
      // no AG Grid API, DOM state injection or imagined immutable row-id hook.
      await page.getByRole('button', { name: 'Display', exact: true }).click();
      await page.getByText('View columns', { exact: true }).click();
      const dropdown = page.locator('.MuiPopover-paper:visible').filter({ has: page.getByPlaceholder('Search', { exact: true }) });
      // A cold entry briefly exposes Trace fallback columns. In the observed
      // failure, bulk-clear hit that fallback before voice activation; Call ID
      // stayed checked among voice defaults in a 2920px-wide virtualized grid.
      // Wait for the actual voice config, not merely an actionable Display UI.
      await expect(dropdown.getByText('Call ID', { exact: true }).locator('..').getByRole('checkbox'))
        .toBeVisible({ timeout: UI_READY });
      await expect(dropdown.getByText('Trace Name', { exact: true })).toHaveCount(0, { timeout: UI_READY });
      const all = dropdown.getByRole('checkbox', { name: 'Toggle Select all', exact: true });
      // SOME is rendered unchecked, so check then uncheck to establish NONE.
      await all.check(); await all.uncheck();
      await expect(dropdown.getByRole('checkbox', { checked: true })).toHaveCount(0, { timeout: UI_READY });
      for (const label of ['Call ID', 'Status', 'Avg Latency', 'Cost'])
        await dropdown.getByText(label, { exact: true }).locator('..').getByRole('checkbox').check();
      for (const label of ['Call ID', 'Status', 'Avg Latency', 'Cost'])
        await expect(dropdown.getByText(label, { exact: true }).locator('..').getByRole('checkbox'))
          .toBeChecked({ timeout: UI_READY });
      await expect(all).not.toBeChecked({ timeout: UI_READY });
      await expect(dropdown.getByRole('checkbox', { checked: true })).toHaveCount(4, { timeout: UI_READY });
      await page.keyboard.press('Escape');
      await expect(page.getByPlaceholder('Search', { exact: true })).toBeHidden({ timeout: UI_READY });
      await page.keyboard.press('Escape');
      await expect(page.getByText('View columns', { exact: true })).toBeHidden({ timeout: UI_READY });
    };
    const checkGrid = async (expected: Fact[]) => {
      const rows = expectedRows(expected);
      await expect(cells('call_id')).toHaveText(rows.map(r => r.call_id), { timeout: UI_READY });
      await expect(cells('status')).toHaveText(rows.map(r => r.status === 'completed' ? 'Completed' : 'Failed'), { timeout: UI_READY });
      await expect(cells('avg_agent_latency_ms')).toHaveText(rows.map(r => `${r.avg_agent_latency_ms}ms`), { timeout: UI_READY });
      await expect(cells('cost_cents')).toHaveText(rows.map(r => `$${(r.cost_cents / 100).toFixed(2)}`), { timeout: UI_READY });
      if (!expected.length) await expect(page.getByText('No calls found', { exact: true })).toBeVisible({ timeout: UI_READY });
    };
    const captureComplete = async (mark: number, project: Project, predicate: (c: Capture) => boolean,
      expected: Fact[], cached?: Capture) => {
      const c = await readCapture(mark, LIST, predicate, cached);
      checkScope(c, project); checkDate(c.query, project);
      expect(String(c.query.cursor_mode)).toBe('true'); expect(Number(c.query.page_size)).toBe(25);
      // TraceView.list_voice_calls defaults an omitted flag to false; the UI
      // omits its unset URL state rather than sending the literal string.
      expect(String(c.query.remove_simulation_calls ?? false)).toBe('false');
      const body = c.body as CallPage; checkPage(body, true);
      expect(body.results.map(rowIdentity)).toStrictEqual(expectedRows(expected)); expect(body.count).toBe(expected.length);
      await checkGrid(expected);
      const { cursor: _cursor, ...query } = c.query;
      await walkCalls(query, expected);
      // Recheck after UI/API assertions too: a delayed new read must not hide
      // behind the immediately rendered cached rows.
      await readCapture(mark, LIST, predicate, c);
      return c;
    };
    let currentProject = primary, filtered = false;
    let unfiltered: Capture, currentList: Capture, openScope: Awaited<ReturnType<typeof browserScope>>;
    const valueBases = new Map<string, Capture>();
    const open = async (project: Project, reload = false) => {
      currentProject = project; filtered = false;
      valueBases.clear(); // Never carry browser-cache evidence across a new document/project.
      const mark = captures.length;
      if (reload) await page.reload({ waitUntil: 'domcontentloaded', timeout: UI_READY });
      else await page.goto(`/dashboard/observe/${project.id}/llm-tracing?selectedTab=trace`, { waitUntil: 'domcontentloaded', timeout: UI_READY });
      await expect(page.getByRole('button', { name: 'Display', exact: true })).toBeVisible({ timeout: UI_READY });
      await showIdentityColumns();
      currentList = unfiltered = await captureComplete(mark, project,
        c => c.query.project_id === project.id && leaves(c.query).every(l => l.column_id === 'created_at'),
        project === primary ? [a, b] : [s]);
      openScope = await browserScope(project);
      expect(openScope.credential).toBe(unfiltered.credential);
    };
    const clear = async () => {
      expect(await browserScope(currentProject)).toStrictEqual(openScope);
      // helper.jsx key: project/module, complete params, page/limit, transport
      // revision. This flow stays on page 1, changes ONLY extraFilters, and
      // never uses auto/manual refresh; explicit goto/reload re-proves the base.
      // A changed date/sort/cursor/flag or document therefore fails, not reuses.
      expect({ ...currentList.query, filters: JSON.stringify(leaves(currentList.query)
        .filter(l => l.column_id === 'created_at')) }).toStrictEqual(unfiltered.query);
      expect(unfiltered.query.page).toBe(1); expect(unfiltered.query.cursor).toBeUndefined();
      const mark = captures.length;
      await page.getByRole('button', { name: 'Filter', exact: true }).click();
      await page.getByRole('button', { name: 'Clear all', exact: true }).click(); filtered = false;
      await expect(page.getByRole('tab', { name: 'Basic', exact: true })).toBeHidden({ timeout: UI_READY });
      // Exact rows alone cannot distinguish Clear all from the both-statuses
      // predicate. Reopen through the UI and prove the sole row is unselected.
      await page.getByRole('button', { name: 'Filter', exact: true }).click();
      const panel = page.locator('.MuiPopover-paper:visible').filter({ has: page.getByRole('tab', { name: 'Basic', exact: true }) });
      await expect(panel.getByRole('button', { name: 'Property', exact: true })).toHaveCount(1);
      await expect(panel.getByRole('button', { name: 'Select property first', exact: true })).toHaveCount(1);
      await expect(panel.getByRole('combobox')).toHaveCount(1);
      await page.keyboard.press('Escape');
      await expect(page.getByRole('tab', { name: 'Basic', exact: true })).toBeHidden({ timeout: UI_READY });
      currentList = await captureComplete(mark, currentProject, c => leaves(c.query).every(l => l.column_id === 'created_at'),
        currentProject === primary ? [a, b] : [s], unfiltered);
      expect(await browserScope(currentProject)).toStrictEqual(openScope);
    };
    const suggestions = async (mark: number, field: string, search = '') => {
      expect(await browserScope(currentProject)).toStrictEqual(openScope);
      const base = valueBases.get(field);
      if (search) expect(base, 'search must follow a proven browser base lookup').toBeDefined();
      const query = base && { ...base.query, ...(search ? { search } : {}) };
      const cached = query && [...provenReads].find(c => c.document === document && c.path === VALUES
        && isDeepStrictEqual(c.query, query));
      const receipt = await readCapture(mark, VALUES, c => c.query.property_id === `custom_attribute:${field}`
        && (c.query.search ?? '') === search, cached);
      // useDashboards.js filterValues key: metric/property/type, project IDs,
      // dataset/source/workflow, search, page size and attribute type. This
      // fixture's entire vocabulary fits page one; no cursor chain is inferred.
      expect(receipt.query).toStrictEqual(query ?? {
        property_id: `custom_attribute:${field}`, metric_name: field, metric_type: 'custom_attribute',
        project_ids: currentProject.id, source: 'traces', page_size: receipt.query.page_size,
        ...(receipt.query.attribute_type === undefined ? {} : { attribute_type: 'string' }),
      });
      expect(Number(receipt.query.page_size)).toBe(50); // runtime_limits.js FILTER_VALUE_PAGE_SIZE.
      const body = (receipt.body as ValuePage).result;
      expect(body).toMatchObject({ query_complete: true, query_status: 'complete', has_more: false, next_cursor: null });
      const vocabulary = field === childKey ? [childValue]
        : currentProject === primary ? ['alpha', 'beta'] : [`${prefix}-sibling-only`];
      expect(sorted(body.values.map(({ value, type }) => ({ value, type }))))
        .toStrictEqual(sorted(vocabulary.filter(value => value.includes(search)).map(value => ({ value, type: 'string' }))));
      if (!search) valueBases.set(field, receipt);
      // Do not consume visible old options while their replacement is loading,
      // failed or incomplete. New filtered LIST proof below is still mandatory.
      await expect(page.locator('[data-filter-value-options-list] [role="status"]')).toHaveCount(0);
      return receipt;
    };
    const select = async (c: Case) => {
      if (filtered) await clear();
      expect(await browserScope(currentProject)).toStrictEqual(openScope);
      await page.getByRole('button', { name: 'Filter', exact: true }).click();
      await page.getByRole('button', { name: 'Property', exact: true }).first().click();
      const field = c.leaf.column_id, config = c.leaf.filter_config;
      await page.getByPlaceholder('Search properties...').fill(field);
      await page.locator(`[data-filter-property-option=${JSON.stringify(field)}][data-filter-property-category="${config.col_type === 'SPAN_ATTRIBUTE' ? 'attribute' : 'system'}"]`).click();
      const mark = captures.length;
      const panel = page.locator('.MuiPopover-paper:visible').filter({ has: page.getByRole('tab', { name: 'Basic', exact: true }) });
      await panel.getByRole('combobox').first().click();
      await page.getByRole('option', { name: 'equals', exact: true }).click();
      if (c.control === 'number') await page.getByPlaceholder('Value', { exact: true }).fill(String(config.filter_value));
      else if (c.control === 'text') {
        await expect(page.locator(`[data-filter-value-trigger=${JSON.stringify(field)}]`)).toHaveCount(0);
        await page.getByPlaceholder('Enter text...', { exact: true }).fill(String(config.filter_value));
      }
      else {
        await page.locator(`[data-filter-value-trigger=${JSON.stringify(field)}]`).click();
        if (config.col_type === 'SPAN_ATTRIBUTE' && !c.manual) await suggestions(mark, field);
        const vocabulary = page.locator('[data-filter-value-option][role="checkbox"]:visible');
        if (field === 'call_status') {
          expect(sorted(await vocabulary.evaluateAll(nodes => nodes.map(n => n.getAttribute('data-filter-value-option')))))
            .toStrictEqual(sorted(STATUS_CHOICES));
          await expect(page.getByText('+ Specify:', { exact: false })).toBeHidden({ timeout: UI_READY });
        }
        for (const value of config.filter_value as string[]) {
          const searchMark = captures.length;
          await page.getByPlaceholder('Search values...').fill(value);
          if (config.col_type === 'SPAN_ATTRIBUTE' && !c.manual) await suggestions(searchMark, field, value);
          const option = page.locator(`[data-filter-value-option=${JSON.stringify(value)}]`);
          if (c.manual) await option.filter({ hasText: '+ Specify:' }).click();
          else await option.and(page.locator('[role="checkbox"]')).click();
        }
        await page.keyboard.press('Escape');
        await expect(page.getByPlaceholder('Search values...')).toBeHidden({ timeout: UI_READY });
      }
      await page.keyboard.press('Escape'); filtered = true;
      await expect(page.getByRole('tab', { name: 'Basic', exact: true })).toBeHidden({ timeout: UI_READY });
      // Match the requested values, then assert exact wire type and identity.
      // Wrong type/namespace must fail, not become an uncaptured timeout.
      const actual = currentList = await captureComplete(mark, currentProject, x => leaves(x.query).some(l => l.column_id === field
        && JSON.stringify(l.filter_config.filter_value) === JSON.stringify(config.filter_value)), c.expected);
      const selected = leaves(actual.query).find(l => l.column_id === field)!;
      expect(selected.property_id).toBe(c.leaf.property_id); expect(selected.filter_config).toStrictEqual(config);
      expect((actual.body as CallPage).query_applied_filter_version).toBe('canonical-json-sha256-v1');
      // filter_attestation.applied_filter_leaves excludes the positive base
      // datetime window, which checkDate above proves independently.
      expect((actual.body as CallPage).query_applied_filter_count)
        .toBe(leaves(actual.query).filter(l => l.column_id !== 'created_at').length);
      expect(await browserScope(currentProject)).toStrictEqual(openScope);
      evidence.push({ phase: 'UI case', name: c.name, expected: expectedRows(c.expected), selected });
    };
    await test.step('UI 1a: call table and identity columns', () => open(primary), { timeout: UI_READY });
    await test.step('UI 1b: native/custom property eligibility', async () => {
      const mark = captures.length;
      await page.getByRole('button', { name: 'Filter', exact: true }).click();
      await page.getByRole('button', { name: 'Property', exact: true }).first().click();
      const checkPropertySearch = async (searchMark: number, name: string) => {
        const matching = (c: Capture) => (c.query.search ?? '') === name;
        const response = await readCapture(searchMark, METRICS, matching); // Fresh only; no cache fallback.
        for (const c of captures.slice(searchMark).filter(c => c.document === document && c.path === METRICS && matching(c))) {
          expect(c.method).toBe('POST'); expect(c.credential).toBe(openScope.credential);
          // usePropertyCatalog/PROPERTY_CATALOG_SEARCH_PAGE_SIZE; captured All-search wire.
          expect(c.query).toStrictEqual({ cursor_mode: true, page_size: 20, source: 'voice_calls',
            project_ids: primary.id, per_eval_config: true, ...(name ? { search: name } : {}) });
          const result = (c.body as MetricPage).result;
          expect(result).toMatchObject({ query_complete: true, query_status: 'complete' });
          expect(Array.isArray(result.metrics)).toBe(true);
          if (name) expect(result).toMatchObject({ has_more: false, next_cursor: null });
          else {
            // Initial empty search is a completed PAGE, not the exhausted catalog.
            expect(typeof result.has_more).toBe('boolean');
            if (result.has_more) expect(result.next_cursor).toEqual(expect.any(String));
            else expect(result.next_cursor).toBe(null);
          }
        }
        return response;
      };
      // Do not cancel a captured initial page by typing before its response settles.
      if (captures.some(c => c.document === document && c.path === METRICS && (c.query.search ?? '') === ''))
        await checkPropertySearch(0, '');
      const completePropertySearch = async (name: string, category: 'system' | 'attribute', visible = true) => {
        const searchMark = captures.length;
        await page.getByPlaceholder('Search properties...').fill(name);
        // A static option can render before this debounced, abortable request.
        // Every tested search must finish before the next fill or popup close.
        await checkPropertySearch(searchMark, name);
        // TraceFilterPanel's debounced search/loading labels and pagination
        // sentinel must settle too. An alias may leave a canonical option, so
        // "No properties found" is not the contract; exhausted search is.
        await expect(page.getByText('Searching property catalog…', { exact: true })).toBeHidden({ timeout: UI_READY });
        await expect(page.getByText('Loading properties…', { exact: true })).toBeHidden({ timeout: UI_READY });
        await expect(page.locator('[data-filter-property-page-sentinel]')).toHaveCount(0, { timeout: UI_READY });
        const option = page.locator(`[data-filter-property-option=${JSON.stringify(name)}][data-filter-property-category="${category}"]`);
        if (visible) await expect(option).toBeVisible({ timeout: UI_READY });
        else await expect(option).toHaveCount(0, { timeout: UI_READY });
        const response = await checkPropertySearch(searchMark, name); // Include replacements during the UI assertions.
        evidence.push({ phase: 'completed property search', name, category, visible, response });
        return response;
      };
      for (const name of STATIC_FIELDS) await completePropertySearch(name, 'system');
      // Search each excluded canonical alias/foreign system key to ensure
      // absence isn't merely a not-yet-rendered first property page.
      for (const name of [...ALIASES, 'dataset', 'eval_source', 'span_count', 'active_users']) {
        const response = await completePropertySearch(name, 'system', false);
        evidence.push({ phase: 'completed excluded-property search', name, response });
      }
      for (const name of [routeKey, childKey]) await completePropertySearch(name, 'attribute');
      await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
      await expect(page.getByRole('tab', { name: 'Basic', exact: true })).toBeHidden({ timeout: UI_READY });
      await Promise.all(pending);
      const discovery = captures.slice(mark).filter(c => c.path === METRICS && c.query.project_ids === primary.id && String(c.query.cursor_mode) === 'true');
      expect(discovery.length).toBeGreaterThan(0);
      for (const c of discovery) expect([c.status, c.authenticated, c.organization, c.workspace, c.query.source])
        .toStrictEqual([200, true, actor.organizationId, actor.workspaceId, 'voice_calls']);
      // Compatibility attributes, when requested, must use spans independently
      // of voice definitions and trace value transport. No request is forced.
      for (const c of captures.slice(mark).filter(c => c.path === '/tracer/eval-attributes/')) expect(c.query.source).toBe('spans');
    }, { timeout: UI_READY });
    await test.step('UI 2: root-native status, call ID, latency and cost', async () => {
      const cases: Case[] = [
        { name: 'completed', leaf: leaf('call_status', 'text', 'in', ['completed']), expected: [a], control: 'choices' },
        { name: 'failed', leaf: leaf('call_status', 'text', 'in', ['failed']), expected: [b], control: 'choices' },
        { name: 'both statuses', leaf: leaf('call_status', 'text', 'in', ['completed', 'failed']), expected: [a, b], control: 'choices' },
        { name: 'dropped zero', leaf: leaf('call_status', 'text', 'in', ['dropped']), expected: [], control: 'choices' },
        // voiceCallFilterFields.js owns a direct text control for high-cardinality
        // provider IDs. The captured list wire still uses exact membership.
        { name: 'call ID', leaf: leaf('call_id', 'text', 'in', [a.callId]), expected: [a], control: 'text' },
        { name: 'latency milliseconds', leaf: leaf('avg_agent_latency_ms', 'number', 'equals', 200), expected: [a], control: 'number' },
        { name: 'cost cents', leaf: leaf('cost_cents', 'number', 'equals', 250), expected: [b], control: 'number' },
      ];
      for (const c of cases) await test.step(c.name, () => select(c), { timeout: UI_READY });
    });
    await test.step('UI 3: root/child suggestions, multiple and complete zero matches', async () => {
      for (const [name, choices, expected, manual] of [
        ['route alpha excludes ordinary root', ['alpha'], [a], false], ['route beta', ['beta'], [b], false],
        ['both routes', ['alpha', 'beta'], [a, b], false], ['route zero', [missing], [], true],
      ] as [string, string[], Fact[], boolean][]) {
        await test.step(name, () => select({ name, leaf: leaf(routeKey, 'text', 'in', choices, true),
          expected, control: 'choices', manual }), { timeout: UI_READY });
      }
      // Load-bearing: B's root does NOT carry childKey; the real call list
      // must match its child-inclusive trace custom predicate without promoting
      // that same child's native call status/ID into the voice-root vocabulary.
      await test.step('child-only custom matches B', () => select({ name: 'child-only custom matches B',
        leaf: leaf(childKey, 'text', 'in', [childValue], true), expected: [b], control: 'choices' }), { timeout: UI_READY });
    });
    await test.step('UI 4: clear, sibling scope and fresh primary refresh', async () => {
      await clear(); await open(sibling);
      await select({ name: 'sibling same call spelling', leaf: leaf(routeKey, 'text', 'in', [`${prefix}-sibling-only`], true), expected: [s], control: 'choices' });
      await open(primary); await open(primary, true);
      await attach('final-project-source', await projectRows());
    }, { timeout: UI_READY });
    // Audit replacements that started after a cached option/row was consumed,
    // including debounced searches. Pending, aborted, failed or changed bodies
    // cannot be hidden by a previously completed receipt, even after navigation.
    await Promise.all(pending);
    for (const proof of provenReads) for (const c of captures.filter(c => c.document === proof.document
      && c.path === proof.path && isDeepStrictEqual(c.query, proof.query))) {
      authenticate(c);
      expect([c.method, c.status, c.authenticated, c.organization, c.workspace, c.error])
        .toStrictEqual([proof.method, 200, true, actor.organizationId, actor.workspaceId, undefined]);
      expect(c.credential).toBe(proof.credential);
      if (c.path === LIST) {
        const body = c.body as CallPage, original = proof.body as CallPage;
        checkPage(body, true);
        expect(body.results.map(rowIdentity)).toStrictEqual(original.results.map(rowIdentity));
        expect(body.count).toBe(original.count);
      } else {
        type CatalogResult = (MetricPage | ValuePage)['result'];
        const compare = (actual: CatalogResult, original: CatalogResult) => {
          const { next_cursor: actualCursor, ...actualPage } = actual;
          const { next_cursor: originalCursor, ...originalPage } = original;
          expect(actualPage).toStrictEqual(originalPage);
          expect(actual.query_complete).toBe(true); expect(actual.query_status).toBe('complete');
          expect(typeof actual.has_more).toBe('boolean');
          for (const cursor of [actualCursor, originalCursor]) {
            if (actual.has_more) expect(typeof cursor === 'string' && cursor.length > 0).toBe(true);
            else expect(cursor).toBe(null);
          }
        };
        let actual = (c.body as MetricPage | ValuePage).result;
        let original = (proof.body as MetricPage | ValuePage).result;
        compare(actual, original);
        if (actual.next_cursor !== original.next_cursor) {
          // cursor.py uses TimestampSigner: opaque tokens may differ at the
          // same boundary. Prove equivalent continuations through the public
          // API, not by parsing tokens or ignoring changed paging semantics.
          const resume = async (cursor: string) => {
            expect(['GET', 'POST']).toContain(c.method);
            const query = { ...c.query, cursor };
            const response = c.method === 'POST'
              ? await req.post(E2E.apiUrl + c.path, { headers, data: query })
              : await req.get(E2E.apiUrl + c.path, { headers, params: Object.fromEntries(
                Object.entries(query).map(([name, value]) => [name, String(value)])) });
            expect(response.status()).toBe(200);
            return (await response.json() as MetricPage | ValuePage).result;
          };
          const continuations: { actual: CatalogResult; original: CatalogResult }[] = [];
          for (let i = 0; i < 8 && actual.has_more; i++) {
            [actual, original] = await Promise.all([resume(actual.next_cursor!), resume(original.next_cursor!)]);
            continuations.push({ actual, original });
            compare(actual, original);
          }
          evidence.push({ phase: 'opaque cursor replay equivalence', path: c.path, query: c.query, continuations });
          expect(actual.has_more).toBe(false);
        }
      }
    }
    outcome = 'passed';
  } catch (error) {
    outcome = 'failed'; failure = String(error); throw error;
  } finally {
    page.off('request', started); page.off('requestfailed', failed); page.off('response', listener);
    await Promise.all(pending); await req.dispose();
    for (const c of captures) authenticate(c);
    await attach('obs008-evidence', { outcome, failure, prefix, projects, facts, evidence, captures,
      refreshes: [...refreshes.values()], authFailures,
      exclusions: ['telephony/providers/media', 'voice detail/export', 'voice-native foreign-organization/revocation security',
        'all extended metric expressions/dashboard aggregates', 'implicit workspace fallback authentication'] });
  }
});
