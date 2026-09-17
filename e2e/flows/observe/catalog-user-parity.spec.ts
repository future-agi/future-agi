import { createHash, randomUUID } from 'node:crypto';
import { request, type BrowserContext, type Page, type Request, type Response } from '@playwright/test';
import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { authInitScript } from '../../lib/auth';
import { sendTrace, type OtlpAttributes, type SeededTrace } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// UsersGrid.buildBaseParams, ObserveToolbar (users namespace / sessions values /
// traces attributes), and UsersQuerySerializer. POSTs here are public read aliases.
const USERS = '/tracer/users/';
const METRICS = '/tracer/dashboard/metrics/';
const VALUES = '/tracer/dashboard/filter_values/';
const SESSIONS = '/tracer/trace-session/list_sessions/';
const TRACES = '/tracer/trace/list_traces_of_session/';
// accounts/serializers/org_api_key.py and WorkspaceContext.switchWorkspace.
const MINT = '/accounts/key/generate_secret_key/';
const DISABLE = '/accounts/key/disable_key/';
const SWITCH = '/accounts/workspace/switch/';
const UI_READY = 60_000;
// Frozen UUIDv5 namespaces: fi-collector/pkg/detid/detid.go. This independent
// RFC-4122 calculation never imports the application or its query/extractor code.
const USER_NS = '97daafcc-ae7b-5a44-a76b-c85e63059e1c';
const SESSION_NS = '1c4977df-2af9-5330-b34f-7969ffdabf25';
const uuid5 = (namespace: string, key: string): string => {
  const bytes = createHash('sha1').update(Buffer.from(namespace.replaceAll('-', ''), 'hex')).update(key).digest().subarray(0, 16);
  bytes[6] = (bytes[6] & 15) | 80; bytes[8] = (bytes[8] & 63) | 128;
  const hex = bytes.toString('hex');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};
// Complete users manifest: property_catalog/source_adapters.py:249–296.
const USER_SYSTEM = {
  user: 'string', user_id_type: 'string', user_id_hash: 'string',
  activated_at: 'datetime', last_active: 'datetime', num_active_days: 'number',
  total_cost: 'number', total_tokens: 'number', input_tokens: 'number', output_tokens: 'number',
  num_traces: 'number', num_sessions: 'number', avg_session_duration: 'number',
  avg_trace_latency: 'number', num_llm_calls: 'number', num_guardrails_triggered: 'number',
  num_traces_with_errors: 'number', active_users: 'number', avg_cost_per_user: 'number', avg_traces_per_user: 'number',
};
// user_filter_capabilities.py and user_attribute_contract.py; same-name raw
// attributes are distinct identities and must not inherit these exclusions.
const UNSUPPORTED = ['dataset', 'eval_source', 'active_users', 'avg_cost_per_user', 'avg_traces_per_user'];
const RESERVED = ['raw.', 'llm.input_messages', 'llm.output_messages', 'input.value', 'output.value'];
type Wire = Record<string, unknown>;
type Leaf = { column_id: string; property_id: string; filter_config: {
  col_type: 'SYSTEM_METRIC' | 'SPAN_ATTRIBUTE'; filter_type: string; filter_op: string;
  filter_value: unknown; attribute_value_types?: string[] } };
type UserRow = { project_id: string; end_user_id: string; user_id: string;
  user_id_type: string | null; user_id_hash: string | null; activated_at: string; last_active: string;
  [key: string]: unknown };
type UserPage = { result: { table: UserRow[]; has_more: boolean; next_cursor: string | null;
  query_complete: boolean; query_exact: boolean; query_status: string; ordering_exact: boolean } };
type Metric = { property_id: string; name: string; type: string; role: string };
type MetricPage = { result: { metrics: Metric[]; has_more: boolean; next_cursor: string | null } };
type Value = { value: unknown; type?: string; label: string };
type ValuePage = { result: { values: Value[]; has_more: boolean; next_cursor: string | null; query_complete: boolean } };
type Project = { name: string; id: string; owner: ScopeActor; reader: ScopeActor };
type Person = { code: string; label: string; inputType?: string; normalizedType: string | null; hash: string;
  project: Project; id: string; attrs: OtlpAttributes; inputTokens: number; guardrail: boolean };
type Fact = { project: Project; person?: Person; session: string; sessionId: string; startMs: number;
  attributes: OtlpAttributes; seeded: SeededTrace; rootName: string };
type Case = { name: string; leaf: Leaf; codes: string[] };
const ordered = <T>(rows: T[]) => [...rows].sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
const canonicalLeaf = (column: string, type: string, op: string, value: unknown, custom = false,
  valueTypes?: string[]): Leaf => ({ column_id: column,
  property_id: custom ? `custom_attribute:${column}` : `system_attribute:users:${column === 'user_id' ? 'user' : column}`,
  filter_config: { col_type: custom ? 'SPAN_ATTRIBUTE' : 'SYSTEM_METRIC', filter_type: type,
    // filter-contract.coerceFilterValue emits null for presence predicates and
    // buildApiFilterFromPanelRow retains scalar suggestion types for list ops.
    filter_op: op, filter_value: ['is_null', 'is_not_null'].includes(op) ? null : value,
    ...(valueTypes ? { attribute_value_types: valueTypes }
      : custom && ['in', 'not_in'].includes(op) && Array.isArray(value)
        ? { attribute_value_types: value.map(v => typeof v === 'string' ? 'string' : typeof v) } : {}) } });

test('OBS-E2E-007: Users discovery selects only authorized user activity', {
  tag: ['@flow'],
  annotation: flowAnnotation({ id: 'OBS-E2E-007', area: 'observe',
    userGoal: 'Find a user through supported system/custom filters and navigate to exactly their authorized sessions and traces after changing workspace',
    steps: ['inspect empty Users then ingest an isolated multi-scope cohort',
      'choose native identity and metric filters in project Users',
      'discover typed custom values and select positive, multiple and zero matches',
      'inspect structured, missing and colliding attributes in workspace Users',
      'open the user and select Past 7D for their sessions and traces', 'switch workspace, reopen the same spelling and refresh'],
    backendChecks: [
      'curated user and session identities and authoritative spans equal the independent fixture IDs in each organization, workspace and project',
      'native and custom discovery retain exact identities and typed scoped values, with supported value adapters and explicit unsupported contracts',
      'Users filters and paging return exactly the independently expected user IDs for supported native and custom predicates',
      'the selected user opens exactly its authorized sessions and traces across sibling projects',
      'workspace changes, fresh reads and cursor validation preserve exact destination scope without foreign identities',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Approved ceiling: provisioning60 + source15 + catalog60 + matrix180 +
  // 6*UI_READY360 +45. UI actions retain UI_READY; any timeout aborts the flow.
  test.setTimeout(720_000);
  const prefix = `e2e-obs7-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const key = Object.fromEntries(['text', 'region', 'number', 'boolean', 'mixed', 'array', 'map', 'foreign']
    .map(k => [k, `${prefix}.${k}`])) as Record<string, string>;
  key.unicode = `${prefix}.客户,"\\key`;
  const shared = `${prefix}-shared@example.test`;
  const a1 = scopes.ownerA, a2 = scopes.withWorkspace(a1, scopes.emptyWorkspace.id);
  const memberA2 = scopes.withWorkspace(scopes.member, a2.workspaceId);
  const projects: Project[] = [
    { name: `${prefix}-p1`, owner: a1, reader: scopes.member },
    { name: `${prefix}-p2`, owner: a1, reader: scopes.member },
    { name: `${prefix}-p3`, owner: a2, reader: memberA2 },
    { name: `${prefix}-p4`, owner: scopes.ownerB, reader: scopes.ownerB },
  ].map(p => ({ ...p, id: '' }));
  const [p1, p2, p3, p4] = projects;
  const persons: Person[] = [], facts: Fact[] = [], receipts: unknown[] = [];
  const catalogRequests: { path: string; status: number; query: Wire; scope: unknown; body: unknown }[] = [];
  const catalogErrors: string[] = [], catalogPending: Promise<void>[] = [];
  const capabilityFailures: { name: string; error: string }[] = [];
  const minted: { owner: ScopeActor; id: string; enabled: boolean }[] = [];
  const requestContext = await request.newContext();
  let context: BrowserContext | undefined;
  let page: Page;
  let uiActor = scopes.member;
  let baselineSource: unknown[] = [];
  // dateRangeDefaults: Users defaults to 7D, but user-mode detail defaults to
  // Today. Keep this fixed prior-UTC-day cohort; detail() explicitly selects
  // Past 7D and proves its captured range covers every expected span.
  const startMs = Math.floor((Date.now() - 86_400_000) / 86_400_000) * 86_400_000 + 12 * 3_600_000;
  const attach = async (name: string, data: unknown) => testInfo.attach(name,
    { body: JSON.stringify(data), contentType: 'application/json' });
  // These are assertion compositions, not new provisioning/probe/ingestion helpers.
  // A failed independent gate is retained AND fails the final assertion; no soft
  // assertion, skip, retry, or API-green substitution can mark this journey green.
  const gate = async (name: string, check: () => Promise<void>) => {
    try { await test.step(name, check); }
    catch (error) {
      // Only settled assertion mismatches are independent capability evidence.
      // A timed-out assertion or failed page action is fatal: never navigate on
      // the same page while an earlier operation may still be finishing.
      if (!(error instanceof Error) || !('matcherResult' in error)
        || /timeout|timed out/i.test(String(error)) || (context !== undefined && page.isClosed())) throw error;
      capabilityFailures.push({ name, error: String(error) });
    }
  };
  const read = async <T>(actor: ScopeActor, path: string, body: Wire, method: 'GET' | 'POST' = 'POST'): Promise<T> => {
    const params = Object.fromEntries(Object.entries(body).map(([k, v]) => [k, typeof v === 'string' || typeof v === 'number' ? v : JSON.stringify(v)]));
    const r = await scopes.send<T>(actor, method, path, method === 'POST' ? body : undefined, method === 'GET' ? params : undefined);
    receipts.push({ method, path, request: body, workspace: actor.workspaceId, ...r });
    expect(r.status, `${method} ${path}: ${JSON.stringify(r.body)}`).toBe(200);
    return r.body;
  };
  const userBody = (project?: Project, filters: Leaf[] = [], pageSize = 25): Wire => ({
    ...(project ? { project_id: project.id } : {}), page_size: pageSize, cursor_mode: true,
    requested_columns: JSON.stringify(Object.keys(USER_SYSTEM).filter(k => USER_SYSTEM[k as keyof typeof USER_SYSTEM] === 'number'
      && !UNSUPPORTED.includes(k))), attribute_keys: '[]', filters: JSON.stringify(filters),
  });
  const userPages = async (actor: ScopeActor, body: Wire, method: 'GET' | 'POST' = 'POST') => {
    const pages: UserPage[] = [], seen = new Set<string>();
    let cursor: string | undefined;
    for (let n = 0; n < 16; n++) {
      const r = await read<UserPage>(actor, USERS, { ...body, ...(cursor ? { cursor } : {}) }, method);
      expect([r.result.query_complete, r.result.query_status, r.result.query_exact, r.result.ordering_exact])
        .toEqual([true, 'complete', true, true]);
      pages.push(r);
      if (!r.result.has_more) { expect(r.result.next_cursor).toBeNull(); break; }
      expect(r.result.next_cursor).toBeTruthy();
      expect(seen.has(r.result.next_cursor!)).toBe(false);
      seen.add(r.result.next_cursor!); cursor = r.result.next_cursor!;
    }
    expect(pages.at(-1)!.result.has_more, 'finite Users cursor must exhaust').toBe(false);
    const rows = pages.flatMap(p => p.result.table);
    expect(new Set(rows.map(r => `${r.project_id}:${r.end_user_id}`)).size).toBe(rows.length);
    return { rows, pages };
  };
  const valuePages = async (actor: ScopeActor, body: Wire, method: 'GET' | 'POST' = 'POST') => {
    const pages: ValuePage[] = [], seen = new Set<string>();
    let cursor: string | undefined;
    for (let n = 0; n < 32; n++) {
      const r = await read<ValuePage>(actor, VALUES, { ...body, ...(cursor ? { cursor } : {}) }, method);
      expect(r.result.query_complete).toBe(true); pages.push(r);
      if (!r.result.has_more) { expect(r.result.next_cursor).toBeNull(); break; }
      expect(r.result.next_cursor).toBeTruthy(); expect(seen.has(r.result.next_cursor!)).toBe(false);
      seen.add(r.result.next_cursor!); cursor = r.result.next_cursor!;
    }
    expect(pages.at(-1)!.result.has_more).toBe(false);
    const values = pages.flatMap(p => p.result.values);
    expect(new Set(values.map(v => JSON.stringify([v.type, v.value]))).size).toBe(values.length);
    return { values, pages };
  };
  const userIds = (rows: UserRow[]) => rows.map(r => `${r.project_id}:${r.end_user_id}`).sort();
  const expectedIds = (people: Person[]) => people.map(u => `${u.project.id}:${u.id}`).sort();
  const source = () => probe.ch<{ id: string; trace_id: string; project_id: string; org_id: string;
    end_user_id: string | null; trace_session_id: string; parent_span_id: string; start_us: string; end_us: string;
    attrs_string: Record<string, string>; attrs_number: Record<string, number>; attrs_bool: Record<string, number>;
    extra: string; prompt_tokens: number; completion_tokens: number; total_tokens: number; cost: number }>(
    `SELECT id, trace_id, project_id, org_id, end_user_id, trace_session_id, parent_span_id,
     toString(toUnixTimestamp64Micro(start_time)) AS start_us, toString(toUnixTimestamp64Micro(end_time)) AS end_us,
     attrs_string, attrs_number, attrs_bool, toString(attributes_extra) AS extra, prompt_tokens, completion_tokens, total_tokens, cost
     FROM spans FINAL WHERE trace_id IN (SELECT arrayJoin(JSONExtract({ids:String}, 'Array(String)'))) ORDER BY id`,
    { ids: JSON.stringify(facts.map(f => f.seeded.traceId)) });
  const headers = async (response: Response) => {
    const h = await response.request().allHeaders();
    expect([h['x-organization-id'], h['x-workspace-id'], h.authorization])
      .toEqual([uiActor.organizationId, uiActor.workspaceId, `Bearer ${uiActor.tokens.access}`]);
    return { organizationId: h['x-organization-id'], workspaceId: h['x-workspace-id'], authenticated: true };
  };
  const requestBody = (response: Response): Wire => response.request().method() === 'POST'
    ? response.request().postDataJSON() : Object.fromEntries(new URL(response.url()).searchParams);
  type CaptureMode = 'required' | 'switch-metadata';
  type CapturedResponse<Mode extends CaptureMode = 'required'> = Readonly<{ status: number; requestJSON: string;
    scope: Readonly<{ organizationId: string; workspaceId: string; authenticated: true }> }
    & (Mode extends 'required' ? { bodyJSON: string } : {})>;
  // Start evidence reads in the response event, before action/navigation ends.
  // WorkspaceContext.switchWorkspace can invalidate even an eager body read
  // (isolated 10999 receipt); only SWITCH uses metadata plus PG/destination UI.
  // Its receipt omits bodyJSON; every body-reading caller remains body-required.
  const responseAfter = async <Mode extends CaptureMode = 'required'>(matches: (r: Response) => boolean,
    action: () => Promise<unknown>, mode?: Mode): Promise<CapturedResponse<Mode>> => {
    const expected = { organizationId: uiActor.organizationId, workspaceId: uiActor.workspaceId,
      authorization: `Bearer ${uiActor.tokens.access}` };
    const responses: { outcome?: { receipt: CapturedResponse<Mode> } | { error: unknown } }[] = [];
    const listener = (r: Response) => {
      if (r.request().method() === 'OPTIONS' || !matches(r)) return;
      const capture: typeof responses[number] = {};
      responses.push(capture); // Arrival order, never body-completion order.
      void (async (): Promise<CapturedResponse<Mode>> => {
        const status = r.status(), requestJSON = JSON.stringify(requestBody(r));
        if (mode === 'switch-metadata')
          expect([new URL(r.url()).pathname, r.request().method()]).toEqual([SWITCH, 'POST']);
        const body = mode === 'switch-metadata' ? undefined : r.json().then(value => JSON.stringify(value));
        const [bodyJSON, h] = await Promise.all([body, r.request().allHeaders()]);
        expect([h['x-organization-id'], h['x-workspace-id'], h.authorization === expected.authorization])
          .toEqual([expected.organizationId, expected.workspaceId, true]);
        const metadata = { status, requestJSON, scope: Object.freeze({ organizationId: h['x-organization-id'],
          workspaceId: h['x-workspace-id'], authenticated: true as const }) };
        return Object.freeze(mode === 'switch-metadata' ? metadata : { ...metadata, bodyJSON }) as CapturedResponse<Mode>;
      })().then(receipt => { capture.outcome = { receipt }; }, error => { capture.outcome = { error }; });
    };
    page.on('response', listener);
    try {
      await action();
      const deadline = Date.now() + UI_READY;
      for (;;) {
        const remaining = deadline - Date.now();
        expect(remaining, 'Latest response capture timed out').toBeGreaterThan(0);
        await expect.poll(() => responses.at(-1)?.outcome !== undefined, { timeout: remaining }).toBe(true);
        // A newer response may arrive while poll finishes; never use the older
        // successful receipt while its replacement is unreadable or pending.
        const outcome = responses.at(-1)?.outcome;
        if (!outcome) continue;
        if ('error' in outcome) throw outcome.error;
        return outcome.receipt;
      }
    } finally { page.off('response', listener); }
  };
  try {
    // Preserve empty scope, before any trace/key writes to A2. A temporary context
    // closes before the switching context opens: never two simultaneous browsers.
    const empty = await userPages(memberA2, userBody());
    expect(empty.rows).toEqual([]);
    expect((await read<MetricPage>(memberA2, METRICS, { source: 'traces', category: 'custom_attribute',
      search: prefix, cursor_mode: true, page_size: 25 })).result.metrics).toEqual([]);
    context = await scopes.openContext(browser, memberA2); page = await context.newPage();
    page.setDefaultTimeout(UI_READY); uiActor = memberA2;
    await test.step('UI stage 1: genuinely empty Users', async () => {
      const response = await responseAfter(r => new URL(r.url()).pathname === USERS,
        () => page.goto('/dashboard/users', { waitUntil: 'domcontentloaded' }));
      expect(response.status).toBe(200);
      const emptyUI = JSON.parse(response.bodyJSON) as UserPage;
      expect(emptyUI.result).toMatchObject({ query_complete: true, query_status: 'complete',
        query_exact: true, ordering_exact: true, has_more: false, next_cursor: null });
      expect(emptyUI.result.table).toEqual([]);
      await expect(page.getByText('Setup Telemetry', { exact: true })).toBeVisible({ timeout: UI_READY });
      await testInfo.attach('empty-users', { body: await page.screenshot(), contentType: 'image/png' });
    });
    await context.close(); context = undefined;

    const shapes: OtlpAttributes[] = [
      { [key.text]: '00123', [key.region]: 'west', [key.number]: 7, [key.boolean]: true, [key.mixed]: '7',
        [key.array]: ['west', 7, true], [key.map]: { tier: 'west', active: true, quota: 7 }, [key.unicode]: '00123', user_id: 'custom-A' },
      { [key.text]: 'alpha', [key.region]: 'east', [key.number]: 0, [key.boolean]: false, [key.mixed]: 7,
        [key.array]: ['west', 0, false], [key.map]: { tier: 'west', active: false, quota: 0 }, [key.unicode]: 'other', user_id: 'custom-B' },
      { [key.text]: 'omega', [key.region]: 'north', [key.number]: 9, [key.boolean]: true, [key.mixed]: '007',
        [key.array]: ['east', 7], [key.map]: { tier: 'east', active: true, quota: 7 } },
      { [key.text]: '', [key.region]: 'south', [key.number]: -2, [key.boolean]: false, [key.mixed]: false,
        [key.array]: [], [key.map]: {} },
      {},
    ];
    const labels = [shared, `${prefix}-phone`, randomUUID(), `${prefix}-custom`, `${prefix}-untyped`];
    const types: (string | undefined)[] = ['email', 'phone', 'uuid', 'unknown-type', undefined];
    for (const [i, label] of labels.entries()) persons.push({ code: 'ABCDE'[i], label, inputType: types[i],
      normalizedType: i === 3 ? 'custom' : types[i] ?? null, hash: i === 4 ? '' : `${prefix}-hash-${i}`,
      project: p1, id: '', attrs: shapes[i], inputTokens: i + 2, guardrail: i === 1 });
    for (const project of [p2, p3, p4]) persons.push({ ...persons[0], project, code: `${project.name}-shared` });
    for (const project of [p3, p4]) persons.push({ ...persons[1], label: `${prefix}-${project === p3 ? 'a2' : 'b1'}-only`,
      code: `${project.name}-only`, project, attrs: { ...shapes[1], [key.foreign]: project === p3 ? 'A2-only' : 'B-only' } });
    const originals = await probe.pg('SELECT id, workspace_id, enabled, deleted FROM accounts_orgapikey WHERE organization_id = ANY($1) ORDER BY id::text',
      [[a1.organizationId, p4.owner.organizationId]]);
    for (const owner of [a1, a2, p4.owner]) {
      const mintedResponse = await scopes.send<{ result: { key_id: string; api_key: string; secret_key: string } }>(owner, 'POST', MINT,
        { key_name: `${prefix}-${owner.workspaceId.slice(0, 8)}` });
      expect(mintedResponse.status).toBe(200);
      const credential = mintedResponse.body.result;
      minted.push({ owner, id: credential.key_id, enabled: true });
      expect(await probe.pg('SELECT organization_id, workspace_id, type FROM accounts_orgapikey WHERE id=$1', [credential.key_id]))
        .toEqual([{ organization_id: owner.organizationId, workspace_id: owner.workspaceId, type: 'user' }]);
      const assignments: { person?: Person; project: Project; repeat: number; unattributed?: string | number | boolean }[] =
        persons.filter(u => u.project.owner.workspaceId === owner.workspaceId).flatMap(person =>
          Array.from({ length: person.code === 'A' ? 3 : 1 }, (_, repeat) => ({ person, project: person.project, repeat })));
      if (owner === a1) assignments.push(...[undefined, '', 0, false].map(unattributed => ({ project: p1, repeat: 0, unattributed })));
      for (const item of assignments) {
        const session = item.person?.code === 'A' ? `${prefix}-primary-session-${item.repeat < 2 ? 1 : 2}`
          : `${prefix}-session-${facts.length}`;
        const at = startMs + facts.length * 1000;
        const attributes: OtlpAttributes = { 'session.id': session, 'gen_ai.cost.total': 0,
          ...(item.person ? { ...item.person.attrs, 'user.id': item.person.label,
            ...(item.person.inputType === undefined ? {} : { 'user.id.type': item.person.inputType }),
            ...(item.person.hash ? { 'user.id.hash': item.person.hash } : {}),
            ...(item.person.code === 'A' && item.repeat === 2 ? { [key.region]: 'east' } : {}) }
            : item.unattributed === undefined ? {} : { 'user.id': item.unattributed }) };
        const rootName = `${prefix}-trace-${facts.length}`;
        const seeded = await sendTrace(requestContext, { collectorUrl: E2E.collectorUrl,
          apiKey: credential.api_key, secretKey: credential.secret_key, projectName: item.project.name, rootName,
          resourceAttributes: { project_type: 'observe' }, startTimeUnixNano: BigInt(at) * 1_000_000n,
          endTimeUnixNano: BigInt(at + 50) * 1_000_000n,
          rootAttributes: { ...attributes, ...(item.person?.guardrail ? { 'fi.span.kind': 'guardrail' } : {}) },
          childAttributes: { ...attributes, 'gen_ai.usage.input_tokens': item.person?.inputTokens ?? 0,
            'gen_ai.usage.output_tokens': item.person ? 1 : 0 } });
        facts.push({ project: item.project, person: item.person, session, sessionId: '', startMs: at, attributes, seeded, rootName });
        await attach(`seed-${facts.length}`, { ...seeded, at, session, user: item.person?.label ?? null, attributes });
      }
    }
    for (const project of projects) {
      const rows = await probe.pg<{ id: string; organization_id: string; workspace_id: string }>(
        'SELECT id, organization_id, workspace_id FROM tracer_project WHERE name=$1 AND NOT deleted', [project.name]);
      expect(rows).toHaveLength(1); project.id = rows[0].id;
      expect(rows[0]).toEqual({ id: project.id, organization_id: project.owner.organizationId, workspace_id: project.owner.workspaceId });
    }
    for (const person of persons) person.id = uuid5(USER_NS, `${person.project.id}|${person.project.owner.organizationId}|${person.label}|${person.normalizedType ?? ''}`);
    for (const fact of facts) fact.sessionId = uuid5(SESSION_NS, `${fact.project.id}|${fact.session}`);
    for (const k of minted) { expect((await scopes.send(k.owner, 'POST', DISABLE, { key_id: k.id })).status).toBe(200); k.enabled = false; }
    expect(await probe.pg('SELECT id, enabled FROM accounts_orgapikey WHERE id=ANY($1) ORDER BY id::text', [minted.map(k => k.id)]))
      .toEqual(minted.map(k => ({ id: k.id, enabled: false })).sort((a, b) => a.id.localeCompare(b.id)));
    expect(await probe.pg('SELECT id, workspace_id, enabled, deleted FROM accounts_orgapikey WHERE organization_id=ANY($1) AND NOT (id=ANY($2)) ORDER BY id::text',
      [[a1.organizationId, p4.owner.organizationId], minted.map(k => k.id)])).toEqual(originals);
    await attach('independent-identities', { projects: projects.map(p => ({ id: p.id, name: p.name, org: p.owner.organizationId, workspace: p.owner.workspaceId })),
      users: persons.map(({ project, ...u }) => ({ ...u, projectId: project.id })),
      facts: facts.map(f => ({ ...f.seeded, sessionId: f.sessionId, userId: f.person?.id ?? null })) });

    await test.step('check 1: independent identities and complete authoritative facts', async () => {
      // These collector-owned nullable columns have their own contract; do not
      // copy a PG NULL -> non-null String CDC convention from annotation scores.
      const identitySchema = await probe.ch<{ table: string; name: string; type: string }>(
        `SELECT table, name, type FROM system.columns WHERE database={db:String}
         AND ((table='end_users' AND name IN ('end_user_id','user_id','user_id_type','user_id_hash'))
           OR (table='spans' AND name IN ('end_user_id','trace_session_id','parent_span_id'))
           OR (table='traces' AND name='id')) ORDER BY table, name`, { db: E2E.chDatabase });
      await attach('identity-source-schema', identitySchema);
      expect(ordered(identitySchema)).toEqual(ordered([
        { table: 'end_users', name: 'end_user_id', type: 'UUID' },
        { table: 'end_users', name: 'user_id', type: 'String' },
        { table: 'end_users', name: 'user_id_type', type: 'LowCardinality(Nullable(String))' },
        { table: 'end_users', name: 'user_id_hash', type: 'String' },
        { table: 'spans', name: 'end_user_id', type: 'Nullable(UUID)' },
        { table: 'spans', name: 'trace_session_id', type: 'Nullable(UUID)' },
        { table: 'spans', name: 'parent_span_id', type: 'String' },
        { table: 'traces', name: 'id', type: 'UUID' },
      ]));
      await expect.poll(async () => {
        const spans = await source();
        const users = await probe.ch('SELECT project_id, end_user_id, organization_id, user_id, user_id_type, user_id_hash FROM end_users FINAL WHERE project_id IN (SELECT arrayJoin(JSONExtract({p:String}, \'Array(UUID)\'))) AND is_deleted=0',
          { p: JSON.stringify(projects.map(p => p.id)) });
        const sessions = await probe.ch('SELECT project_id, trace_session_id, external_session_id FROM trace_sessions FINAL WHERE project_id IN (SELECT arrayJoin(JSONExtract({p:String}, \'Array(UUID)\'))) AND is_deleted=0',
          { p: JSON.stringify(projects.map(p => p.id)) });
        // traces.id is UUID, while the independent expected set uses lexical
        // dashed strings. Order by its String representation, not UUID storage.
        const traces = await probe.ch<{ id: string }>('SELECT id FROM traces FINAL WHERE id IN (SELECT arrayJoin(JSONExtract({p:String}, \'Array(UUID)\'))) ORDER BY toString(id)',
          { p: JSON.stringify(facts.map(f => f.seeded.traceId)) });
        return { spanIds: spans.map(s => s.id), users: ordered(users), sessions: ordered(sessions), traceIds: traces.map(t => t.id) };
      }, POLL.SPAN_VISIBLE).toEqual({ spanIds: facts.flatMap(f => f.seeded.spanIds).sort(),
        users: ordered(persons.map(u => ({ project_id: u.project.id, end_user_id: u.id, organization_id: u.project.owner.organizationId,
          user_id: u.label, user_id_type: u.normalizedType, user_id_hash: u.hash }))),
        sessions: ordered([...new Map(facts.map(f => [f.sessionId, { project_id: f.project.id, trace_session_id: f.sessionId, external_session_id: f.session }])).values()]),
        traceIds: facts.map(f => f.seeded.traceId).sort() });
      const rows = await source(); baselineSource = rows;
      for (const f of facts) for (const [i, id] of f.seeded.spanIds.entries()) {
        const row = rows.find(s => s.id === id)!;
        expect([row.trace_id, row.project_id, row.org_id, row.end_user_id, row.trace_session_id, row.parent_span_id,
          row.start_us, row.end_us, row.prompt_tokens, row.completion_tokens, row.total_tokens, row.cost])
          .toEqual([f.seeded.traceId, f.project.id, f.project.owner.organizationId, f.person?.id ?? null, f.sessionId,
            i ? f.seeded.spanIds[0] : '', String(f.startMs * 1000), String((f.startMs + 50) * 1000),
            i ? f.person?.inputTokens ?? 0 : 0, i && f.person ? 1 : 0, i && f.person ? f.person.inputTokens + 1 : 0, 0]);
        for (const [k, value] of Object.entries(f.attributes)) {
          if (typeof value === 'string') expect(row.attrs_string[k]).toBe(value);
          else if (typeof value === 'number') expect(row.attrs_number[k]).toBe(value);
          else if (typeof value === 'boolean') expect(row.attrs_bool[k]).toBe(value ? 1 : 0);
          else expect(JSON.parse(row.extra)[k]).toEqual(value);
        }
      }
      expect(facts).toHaveLength(16); expect(persons).toHaveLength(10);
    });

    await test.step('check 2: exact native manifest and typed observation discovery', async () => {
      const native = await read<MetricPage>(scopes.member, METRICS, { source: 'users', category: 'system_metric',
        cursor_mode: true, page_size: 25, project_ids: p1.id });
      expect(native.result.has_more).toBe(false);
      expect(ordered(native.result.metrics.filter(m => m.property_id.startsWith('system_attribute:users:')).map(m => ({ id: m.property_id, type: m.type, role: m.role }))))
        .toEqual(ordered(Object.entries(USER_SYSTEM).map(([name, type]) => ({ id: `system_attribute:users:${name}`, type,
          role: ['string', 'datetime'].includes(type) ? 'dimension' : 'metric' }))));
      expect((await read<MetricPage>(scopes.member, METRICS, { source: 'users', category: 'system_metric',
        cursor_mode: true, page_size: 25, project_ids: p1.id }, 'GET')).result.metrics).toEqual(native.result.metrics);
      // Grouped AMT identity, including fingerprint and canonical value bytes.
      const expected = [{ type: 'string', value: '7' }, { type: 'number', value: 7 },
        { type: 'string', value: '007' }, { type: 'boolean', value: false }];
      await expect.poll(async () => {
        const rows = await probe.ch<{ attribute_type: string; value_json: string; value_fingerprint: string }>(
          `SELECT organization_id, workspace_id, project_id, source_kind, attribute_key, attribute_type, value_fingerprint, value_json
           FROM property_catalog.observed_attribute_values WHERE organization_id={o:String} AND workspace_id={w:String}
           AND project_id={p:String} AND source_kind='custom_attribute' AND attribute_key={k:String}
           GROUP BY organization_id, workspace_id, project_id, source_kind, attribute_key, attribute_type, value_fingerprint, value_json`,
          { o: a1.organizationId, w: a1.workspaceId, p: p1.id, k: key.mixed });
        return ordered(rows.map(r => ({ type: r.attribute_type, value: JSON.parse(r.value_json), fingerprint: r.value_fingerprint })));
      }, POLL.ASYNC_JOB).toEqual(ordered(expected.map(({ type, value }) => ({ type, value,
        fingerprint: createHash('sha256').update(`futureagi.span-attribute-catalog.scalar.v1\0${type}\0${JSON.stringify(value)}`).digest('hex') }))));
      for (const project of projects) {
        const scopedPeople = persons.filter(u => u.project === project);
        for (const [field, expectedValues] of [['user', scopedPeople.map(u => u.label)],
          ['user_id_type', scopedPeople.flatMap(u => u.normalizedType ? [u.normalizedType] : [])]] as const) {
          const body = { property_id: `system_attribute:users:${field}`, source: 'sessions', project_ids: project.id, page_size: 1 };
          const result = await valuePages(project.reader, body);
          expect(result.values.map(v => v.value).sort()).toEqual([...new Set(expectedValues)].sort());
          const repeated = await read<ValuePage>(project.reader, VALUES, body);
          expect(repeated.result.values).toEqual(result.pages[0].result.values);
          const legacy = await valuePages(project.reader, { ...body, source: 'users' }, 'GET');
          expect(legacy.values).toEqual(result.values);
          const hit = field === 'user' ? shared : 'email';
          expect((await valuePages(project.reader, { ...body, search: hit })).values.map(v => v.value)).toEqual([hit]);
          expect((await valuePages(project.reader, { ...body, search: 'definitely-absent-' + prefix })).values).toEqual([]);
        }
      }
      const mixed = await valuePages(scopes.member, { source: 'traces', project_ids: p1.id,
        property_id: `custom_attribute:${key.mixed}`, page_size: 1 });
      expect(ordered(mixed.values.map(({ type, value }) => ({ type, value })))).toEqual(ordered(expected));
      for (const [property, choices] of [[key.text, ['', '00123', 'alpha', 'omega']],
        [key.number, [-2, 0, 7, 9]], [key.boolean, [false, true]], [key.array, ['west', 'east', 0, 7, false, true]],
        [key.map, []], [key.unicode, ['00123', 'other']]] as [string, unknown[]][]) {
        const values = await valuePages(scopes.member, { source: 'traces', project_ids: p1.id, property_id: `custom_attribute:${property}`, page_size: 25 });
        expect(ordered(values.values.map(v => v.value)), property).toEqual(ordered(choices));
      }
      const properties = await read<MetricPage>(scopes.member, METRICS, { source: 'traces', category: 'custom_attribute',
        project_ids: p1.id, search: prefix, cursor_mode: true, page_size: 25 });
      expect(properties.result.metrics.map(m => m.property_id).sort()).toEqual(Object.entries(key).filter(([name]) => name !== 'foreign')
        .map(([, k]) => `custom_attribute:${k}`).sort());
      for (const project of [p3, p4]) expect((await valuePages(project.reader, { source: 'traces', project_ids: project.id,
        property_id: `custom_attribute:${key.foreign}`, page_size: 25 })).values.map(v => v.value))
        .toEqual([project === p3 ? 'A2-only' : 'B-only']);
      expect((await valuePages(scopes.member, { source: 'traces', property_id: `custom_attribute:${key.foreign}`, page_size: 25 })).values).toEqual([]);
      // Source-advertised hash discovery stays a failure gate if its adapter is
      // absent. Record actual response now; still exercise the UI before verdict.
      await gate('native hash vocabulary capability', async () => {
        expect((await valuePages(scopes.member, { source: 'sessions', project_ids: p1.id,
          property_id: 'system_attribute:users:user_id_hash', page_size: 25 })).values.map(v => v.value).sort())
          .toEqual(persons.filter(u => u.project === p1 && u.hash).map(u => u.hash).sort());
      });
    });

    const own = persons.filter(u => u.project === p1);
    const byCodes = (codes: string[]) => own.filter(u => codes.includes(u.code));
    const cases: Case[] = [];
    const add = (name: string, column: string, type: string, op: string, value: unknown, codes: string[], custom = false, types?: string[]) =>
      cases.push({ name, leaf: canonicalLeaf(column, type, op, value, custom, types), codes });
    // Hand-enumerated expected cohorts; these do not use the product predicate
    // implementation or filter an API response to calculate its own expected set.
    for (const [op, value, codes] of [
      ['in', ['00123'], ['A']], ['in', ['00123', 'alpha'], ['A', 'B']], ['not_in', ['00123'], ['B', 'C', 'D']],
      ['contains', 'a', ['B', 'C']], ['not_contains', 'a', ['A', 'D']], ['starts_with', '00', ['A']],
      ['ends_with', 'ga', ['C']], ['is_null', '', ['E']], ['is_not_null', '', ['A', 'B', 'C', 'D']],
      ['in', ['not-seeded'], []],
    ] as [string, unknown, string[]][]) add(`text ${op} ${JSON.stringify(value)}`, key.text, 'text', op, value, codes, true);
    for (const [op, value, codes] of [
      ['equals', 7, ['A']], ['not_equals', 7, ['B', 'C', 'D']], ['greater_than', 0, ['A', 'C']],
      ['greater_than_or_equal', 7, ['A', 'C']], ['less_than', 0, ['D']], ['less_than_or_equal', 0, ['B', 'D']],
      ['between', [0, 7], ['A', 'B']], ['not_between', [0, 7], ['C', 'D']], ['is_null', '', ['E']],
      ['is_not_null', '', ['A', 'B', 'C', 'D']],
    ] as [string, unknown, string[]][]) add(`number ${op}`, key.number, 'number', op, value, codes, true);
    for (const [op, value, codes] of [['equals', false, ['B', 'D']], ['not_equals', false, ['A', 'C']],
      ['is_null', '', ['E']], ['is_not_null', '', ['A', 'B', 'C', 'D']]] as [string, unknown, string[]][])
      add(`boolean ${op}`, key.boolean, 'boolean', op, value, codes, true);
    for (const [op, value, codes] of [['contains', ['west'], ['A', 'B']], ['not_contains', ['west'], ['C', 'D']],
      ['is_null', '', ['E']], ['is_not_null', '', ['A', 'B', 'C', 'D']]] as [string, unknown, string[]][])
      add(`array ${op}`, key.array, 'array', op, value, codes, true);
    for (const [op, value, codes] of [
      ['equals', { tier: 'west', active: true, quota: 7 }, ['A']],
      ['not_equals', { tier: 'west', active: true, quota: 7 }, ['B', 'C', 'D']],
      ['contains', { tier: 'west' }, ['A', 'B']], ['not_contains', { tier: 'west' }, ['C', 'D']],
      ['is_null', '', ['E']], ['is_not_null', '', ['A', 'B', 'C', 'D']],
    ] as [string, unknown, string[]][]) add(`map ${op}`, key.map, 'map', op, value, codes, true);
    add('mixed string only', key.mixed, 'text', 'in', ['7'], ['A'], true, ['string']);
    add('mixed number only', key.mixed, 'text', 'in', [7], ['B'], true, ['number']);
    add('mixed both types', key.mixed, 'text', 'in', ['7', 7], ['A', 'B'], true, ['string', 'number']);
    add('mixed false', key.mixed, 'text', 'in', [false], ['D'], true, ['boolean']);
    add('earlier span witness', key.region, 'text', 'in', ['west'], ['A'], true);
    add('native name collision', 'user_id', 'text', 'in', ['custom-B'], ['B'], true);
    add('opaque Unicode key', key.unicode, 'text', 'in', ['00123'], ['A'], true);
    for (const [op, value, codes] of [
      ['in', [shared], ['A']], ['in', [shared, labels[1]], ['A', 'B']], ['not_in', [shared], ['B', 'C', 'D', 'E']],
      ['contains', '-shared@', ['A']], ['not_contains', '-shared@', ['B', 'C', 'D', 'E']],
      ['starts_with', prefix, ['A', 'B', 'D', 'E']], ['ends_with', '@example.test', ['A']],
      ['is_null', '', []], ['is_not_null', '', ['A', 'B', 'C', 'D', 'E']],
    ] as [string, unknown, string[]][]) add(`native label ${op}`, 'user_id', 'text', op, value, codes);
    add('native type', 'user_id_type', 'text', 'in', ['email'], ['A']);
    add('native type negative', 'user_id_type', 'text', 'not_in', ['email'], ['B', 'C', 'D']);
    add('native type absent', 'user_id_type', 'text', 'is_null', '', ['E']);
    add('native hash', 'user_id_hash', 'text', 'in', [own[0].hash], ['A']);
    add('native hash negative', 'user_id_hash', 'text', 'not_in', [own[0].hash], ['B', 'C', 'D']);
    add('native hash absent', 'user_id_hash', 'text', 'is_null', '', ['E']);
    for (const [op, value, codes] of [
      ['equals', 3, ['A']], ['not_equals', 3, ['B', 'C', 'D', 'E']], ['greater_than', 1, ['A']],
      ['greater_than_or_equal', 3, ['A']], ['less_than', 3, ['B', 'C', 'D', 'E']],
      ['less_than_or_equal', 1, ['B', 'C', 'D', 'E']], ['between', [1, 3], ['A', 'B', 'C', 'D', 'E']],
      ['not_between', [1, 3], []], ['is_null', '', []], ['is_not_null', '', ['A', 'B', 'C', 'D', 'E']],
    ] as [string, unknown, string[]][]) add(`native number ${op}`, 'num_traces', 'number', op, value, codes);
    const metricsExpected: Record<string, number[]> = { num_active_days: [1, 1, 1, 1, 1], total_cost: [0, 0, 0, 0, 0],
      total_tokens: [9, 4, 5, 6, 7], input_tokens: [6, 3, 4, 5, 6], output_tokens: [3, 1, 1, 1, 1],
      num_traces: [3, 1, 1, 1, 1], num_sessions: [2, 1, 1, 1, 1], avg_session_duration: [0.55, 0.05, 0.05, 0.05, 0.05],
      avg_trace_latency: [50, 50, 50, 50, 50], num_llm_calls: [3, 1, 1, 1, 1],
      num_guardrails_triggered: [0, 1, 0, 0, 0], num_traces_with_errors: [0, 0, 0, 0, 0] };
    for (const [metric, values] of Object.entries(metricsExpected)) add(`native metric ${metric}`, metric, 'number', 'equals', values[0],
      own.filter((_, i) => values[i] === values[0]).map(u => u.code));
    add('native guardrail positive', 'num_guardrails_triggered', 'number', 'equals', 1, ['B']);
    add('native error zero/nonmatch only', 'num_traces_with_errors', 'number', 'greater_than', 0, []);
    // activated_at is curatedwriter's insertion time, not the SDK event time.
    // Its independently read CH source is the oracle; last_active is exact seed end.
    const dates = await probe.ch<{ end_user_id: string; first_seen: string }>(
      'SELECT end_user_id, formatDateTime(first_seen, \'%Y-%m-%dT%H:%i:%S.%fZ\') AS first_seen FROM end_users FINAL WHERE project_id={p:UUID} AND is_deleted=0', { p: p1.id });
    for (const field of ['activated_at', 'last_active']) {
      const values = own.map(u => field === 'activated_at' ? dates.find(d => d.end_user_id === u.id)!.first_seen
        : new Date(Math.max(...facts.filter(f => f.person === u).map(f => f.startMs + 50))).toISOString());
      const from = values[0], until = values[0];
      add(`native date ${field} boundary`, field, 'datetime', 'between', [from, until],
        own.filter((_, i) => values[i] === values[0]).map(u => u.code));
      add(`native date ${field} range`, field, 'datetime', 'between', [[...values].sort()[0], [...values].sort().at(-1)], own.map(u => u.code));
      add(`native date ${field} absent`, field, 'datetime', 'is_null', '', []);
    }
    await attach('finite-case-oracle', cases);
    await test.step('check 3: finite exact Users filter and pagination matrix', async () => {
      for (const c of cases) await gate(`API ${c.name}`, async () => {
        const result = await userPages(scopes.member, userBody(p1, [c.leaf]));
        expect(userIds(result.rows), c.name).toEqual(expectedIds(byCodes(c.codes)));
      });
      const baseline = await userPages(scopes.member, userBody(p1, [], 1));
      expect(userIds(baseline.rows)).toEqual(expectedIds(own));
      for (const row of baseline.rows) {
        const i = own.findIndex(u => u.id === row.end_user_id), u = own[i];
        // user_list.format_rows preserves nullable type, but explicitly maps
        // the CH hash String sentinel '' to API null. Never normalize actuals.
        expect([row.user_id, row.user_id_type, row.user_id_hash]).toEqual([u.label, u.normalizedType, u.hash || null]);
        for (const [metric, values] of Object.entries(metricsExpected)) expect(Number(row[metric]), `${u.code}.${metric}`).toBeCloseTo(values[i], 5);
        expect(Date.parse(row.last_active)).toBe(Math.max(...facts.filter(f => f.person === u).map(f => f.startMs + 50)));
        expect(Date.parse(row.activated_at)).toBe(Date.parse(dates.find(d => d.end_user_id === u.id)!.first_seen));
      }
      expect(userIds((await userPages(scopes.member, userBody(p1), 'GET')).rows)).toEqual(expectedIds(own));
      const { cursor_mode, ...numbered } = userBody(p1);
      const legacy = await read<UserPage>(scopes.member, USERS, { ...numbered, current_page_index: 0 }, 'GET');
      expect(userIds(legacy.result.table)).toEqual(expectedIds(own));
      for (const project of projects) expect(userIds((await userPages(project.reader, userBody(project))).rows))
        .toEqual(expectedIds(persons.filter(u => u.project === project)));
      expect(userIds((await userPages(scopes.member, userBody())).rows)).toEqual(expectedIds(persons.filter(u => [p1, p2].includes(u.project))));
      expect(userIds((await userPages(scopes.viewer, userBody(p1))).rows)).toEqual(expectedIds(own));
      const conjunction = [canonicalLeaf(key.region, 'text', 'in', ['west'], true), canonicalLeaf(key.number, 'number', 'equals', 0, true)];
      expect((await userPages(scopes.member, userBody(p1, conjunction))).rows).toEqual([]);
      expect(userIds((await userPages(scopes.member, { ...userBody(p1), search: shared })).rows)).toEqual(expectedIds([own[0]]));
      expect((await userPages(scopes.member, { ...userBody(p1), search: 'absent-' + prefix })).rows).toEqual([]);
      for (const name of UNSUPPORTED) {
        const global = name === 'dataset' || name === 'eval_source';
        const leaf = canonicalLeaf(name, global ? 'text' : 'number', global ? 'in' : 'equals', global ? ['unused'] : 1);
        if (global) leaf.property_id = `system_attribute:all:${name}`;
        const r = await scopes.send(scopes.member, 'POST', USERS, userBody(p1, [leaf]));
        receipts.push({ rejection: name, ...r }); expect(r.status).toBe(400);
        expect(JSON.stringify(r.body)).toContain('Observe Users does not support these system fields as filters');
      }
      for (const reserved of RESERVED) for (const projection of [false, true]) {
        const r = await scopes.send(scopes.member, 'POST', USERS, { ...userBody(p1,
          projection ? [] : [canonicalLeaf(reserved, 'text', 'in', ['x'], true)]), ...(projection ? { attribute_keys: JSON.stringify([reserved]) } : {}) });
        receipts.push({ reserved, projection, ...r }); expect(r.status).toBe(400);
      }
      for (const leaf of [
        { ...canonicalLeaf('user_id', 'text', 'in', [shared]), property_id: 'custom_attribute:wrong-column' },
        canonicalLeaf(key.text, 'text', 'unsupported-op', 'x', true),
      ]) expect((await scopes.send(scopes.member, 'POST', USERS, userBody(p1, [leaf]))).status).toBe(400);
      for (const sourceName of ['users', 'sessions']) expect((await scopes.send(scopes.member, 'POST', VALUES,
        { property_id: `custom_attribute:${key.text}`, source: sourceName, project_ids: p1.id, page_size: 1 })).status).toBe(400);
      for (const method of ['PUT', 'PATCH', 'DELETE']) expect((await scopes.send(scopes.member, method, USERS)).status).toBe(405);
    }, { timeout: 180_000 });

    // One-time public auth bootstrap; the real app owns all later navigation and
    // workspace writes. The H1 per-navigation script would overwrite a UI switch.
    context = await browser.newContext({ baseURL: E2E.appUrl }); page = await context.newPage();
    page.setDefaultTimeout(UI_READY); uiActor = scopes.member;
    await page.goto('/auth/jwt/login', { waitUntil: 'domcontentloaded' });
    await page.evaluate(authInitScript, { ...uiActor.tokens, organizationId: uiActor.organizationId, workspaceId: uiActor.workspaceId });
    await page.evaluate(org => sessionStorage.setItem('workspaceOrgId', org), uiActor.organizationId);
    // useDashboards caches properties and values by query. Keep only real browser
    // receipts, never API replays, owned by this page/context and document. A
    // main-frame document request invalidates ownership; SPA gestures do not.
    const catalogPage = page, catalogContext = context;
    let catalogDocument = 0;
    const catalogScope = (actor: ScopeActor) => JSON.stringify([actor.userId, actor.organizationId,
      actor.workspaceId, createHash('sha256').update(actor.tokens.access).digest('hex')]);
    type CatalogRead = { request: Request; document: number; actor: ScopeActor; scope: string;
      path: string; query: Wire; response?: Response; receipt?: (typeof catalogRequests)[number];
      validated: boolean; error?: string };
    const catalogReads: CatalogRead[] = [];
    const catalogByRequest = new WeakMap<Request, CatalogRead>();
    page.on('request', r => {
      if (r.isNavigationRequest() && r.frame() === catalogPage.mainFrame()) {
        catalogDocument++; catalogReads.length = 0;
      }
      const path = new URL(r.url()).pathname;
      if (![METRICS, VALUES].includes(path) || r.method() === 'OPTIONS') return;
      const read: CatalogRead = { request: r, document: catalogDocument, actor: uiActor,
        scope: catalogScope(uiActor), path, validated: false,
        query: r.method() === 'POST' ? r.postDataJSON() : Object.fromEntries(new URL(r.url()).searchParams) };
      catalogReads.push(read); catalogByRequest.set(r, read);
    });
    page.on('requestfailed', r => {
      const read = catalogByRequest.get(r);
      if (read) read.error = 'Catalog request failed before a complete response';
    });
    page.on('response', r => {
      const path = new URL(r.url()).pathname;
      if (![METRICS, VALUES].includes(path) || r.request().method() === 'OPTIONS') return;
      // Bind expected headers at request time, before a later real workspace switch.
      const read = catalogByRequest.get(r.request()), actor = read?.actor ?? uiActor;
      catalogPending.push((async () => {
        const h = await r.request().allHeaders();
        expect([h['x-organization-id'], h['x-workspace-id'], h.authorization])
          .toEqual([actor.organizationId, actor.workspaceId, `Bearer ${actor.tokens.access}`]);
        // Navigation may cancel an incidental vocabulary body after headers.
        // Preserve that fact, without treating an uncaptured body as success;
        // required picker responses below are independently awaited/asserted.
        const body = await r.json().catch(error => ({ unavailable: String(error) }));
        const receipt = { path, status: r.status(), query: requestBody(r), body,
          scope: { organizationId: h['x-organization-id'], workspaceId: h['x-workspace-id'], authenticated: true } };
        catalogRequests.push(receipt);
        if (read) {
          read.response = r; read.receipt = receipt;
          read.validated = r.status() === 200 && body.result?.query_complete === true
            && body.result?.query_status === 'complete' && body.result?.has_more === false
            && body.result?.next_cursor === null && !read.query.cursor;
        }
      })().catch(error => {
        if (read) read.error = 'Catalog response capture failed';
        catalogErrors.push(String(error));
      }));
    });
    const catalogAfter = async (path: typeof METRICS | typeof VALUES,
      expected: { source: string; project_ids: string; search: string; property_id?: string },
      action: () => Promise<unknown>, settled: (response: Response) => Promise<void>) => {
      const document = catalogDocument, scope = catalogScope(uiActor), mark = catalogReads.length;
      const latest = () => catalogReads.filter(r => r.document === document && r.scope === scope && r.path === path
        && (r.query.search ?? '') === expected.search
        // Retain the old wrong-namespace failure witness; assert exact identity below.
        && (!expected.property_id || [expected.property_id, expected.property_id.replace('system_attribute:users:',
          'system_attribute:traces:')].includes(String(r.query.property_id)))).at(-1);
      await action();
      let read: CatalogRead;
      do {
        // A new pending/failed request wins over any older success. Without a
        // prior validated receipt the first lookup still needs an actual response.
        await expect.poll(() => Boolean(latest()?.receipt || latest()?.error), { timeout: UI_READY }).toBe(true);
        read = latest()!;
        expect(page === catalogPage && page.context() === catalogContext && catalogDocument === document
          && catalogScope(uiActor) === scope, 'Catalog receipt belongs to the current browser document/auth scope').toBe(true);
        expect(read.error).toBeUndefined();
        const response = read.response!;
        await headers(response); expect(response.status()).toBe(200);
        expect({ source: read.query.source, project_ids: read.query.project_ids ?? '', search: read.query.search ?? '',
          ...(expected.property_id ? { property_id: read.query.property_id } : {}) }).toEqual(expected);
        expect(read.query.cursor ?? '').toBe('');
        expect((await response.json()).result).toMatchObject({ query_complete: true, query_status: 'complete',
          has_more: false, next_cursor: null });
        expect(read.validated, 'Cache reuse requires a captured successful complete catalog receipt').toBe(true);
        await settled(response);
        // A retained picker must not certify stale auth/workspace storage. Return
        // only a digest from the browser; never attach or echo its bearer token.
        expect(await page.evaluate(async () => {
          const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(localStorage.getItem('accessToken') ?? ''));
          return [sessionStorage.getItem('organizationId'), sessionStorage.getItem('workspaceId'),
            Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('')];
        })).toEqual([uiActor.organizationId, uiActor.workspaceId,
          createHash('sha256').update(uiActor.tokens.access).digest('hex')]);
        expect(catalogDocument).toBe(document);
      } while (latest() !== read);
      await attach('catalog-receipt-use', { document, path, query: read.query, scope: read.receipt!.scope,
        receiptIndex: catalogRequests.indexOf(read.receipt!), reused: catalogReads.indexOf(read) < mark });
      return read.response!;
    };
    const settledValues = async (response: Response, search: string) => {
      await expect(page.getByPlaceholder('Search values...')).toHaveValue(search, { timeout: UI_READY });
      await expect(page.getByText('Loading values…', { exact: true })).toBeHidden({ timeout: UI_READY });
      const body = await response.json() as ValuePage;
      // TraceFilterPanel's option identity is String(value), with role retaining
      // real suggestions separately from + Specify. Keep duplicate typed labels.
      await expect.poll(() => page.locator('[data-filter-value-option][role="checkbox"]:visible, [data-filter-value-option][role="radio"]:visible')
        .evaluateAll(options => options.map(o => o.getAttribute('data-filter-value-option')).sort()), { timeout: UI_READY })
        .toEqual(body.result.values.map(v => String(v.value)).sort());
    };
    const visibleUserIds = () => page.locator('.clean-data-table:visible .ag-row [col-id="user_id"]').evaluateAll(cells =>
      cells.map(c => c.closest('.ag-row')!.getAttribute('row-id')).sort());
    let filtered = false;
    const openUsers = async (project?: Project) => {
      await page.goto(project ? `/dashboard/observe/${project.id}/users` : '/dashboard/users', { waitUntil: 'domcontentloaded' });
      filtered = false;
      const people = persons.filter(u => project ? u.project === project : u.project.owner.workspaceId === uiActor.workspaceId);
      await expect.poll(visibleUserIds, { timeout: UI_READY }).toEqual(expectedIds(people));
    };
    const searchProperty = async (field: string, project: Project | null, category = 'attribute', present = true) => {
      const response = await catalogAfter(METRICS, { source: 'traces', project_ids: project?.id ?? '', search: field },
        () => page.getByPlaceholder('Search properties...').fill(field), async () => {
          await expect(page.getByPlaceholder('Search properties...')).toHaveValue(field, { timeout: UI_READY });
          await expect(page.getByText('Searching property catalog…', { exact: true })).toBeHidden({ timeout: UI_READY });
          const option = page.locator(`[data-filter-property-option=${JSON.stringify(field)}][data-filter-property-category="${category}"]`);
          expect(await option.count(), 'Exact property option after settled catalog search').toBe(present ? 1 : 0);
          if (present) await expect(option).toBeVisible({ timeout: UI_READY });
        });
      await headers(response);
      const query = requestBody(response), body = await response.json();
      await attach(`property-search-${field}`, { status: response.status(), query, body });
      expect(response.status()).toBe(200);
      expect(query.source).toBe('traces'); expect(query.project_ids ?? '').toBe(project?.id ?? '');
      // Retain query_exact as returned: complete/exhausted does not imply an
      // exact native inventory. A missing mandatory option still fails below.
      expect(body.result).toMatchObject({ query_complete: true, query_status: 'complete', has_more: false, next_cursor: null });
      // TraceFilterPanel renders this state until the debounced search settles.
      await expect(page.getByText('Searching property catalog…', { exact: true })).toBeHidden({ timeout: UI_READY });
    };
    const select = async (c: Case, project: Project | null = p1, expectedPeople = byCodes(c.codes)) => {
      await page.getByRole('button', { name: 'Filter', exact: true }).click();
      if (filtered) {
        await page.getByRole('button', { name: 'Clear all', exact: true }).click();
        await expect(page.getByRole('tab', { name: 'Basic', exact: true })).toBeHidden({ timeout: UI_READY });
        await page.getByRole('button', { name: 'Filter', exact: true }).click();
      }
      await page.getByRole('button', { name: 'Property', exact: true }).first().click();
      const field = c.leaf.column_id, config = c.leaf.filter_config;
      await searchProperty(field, project, config.col_type === 'SPAN_ATTRIBUTE' ? 'attribute' : 'system');
      const option = page.locator(`[data-filter-property-option=${JSON.stringify(field)}][data-filter-property-category="${config.col_type === 'SPAN_ATTRIBUTE' ? 'attribute' : 'system'}"]`);
      // Do not poll a deterministic missing property for another full minute.
      // This immediate assertion follows a validated receipt and settled search.
      expect(await option.count(), `Mandatory ${c.name} option absent after completed property search`).toBe(1);
      await option.click();
      // Operator menu labels and inputs are source-pinned in TraceFilterPanel.
      const panel = page.locator('.MuiPopover-paper:visible').filter({ has: page.getByRole('tab', { name: 'Basic', exact: true }) });
      const operatorLabels: Record<string, string> = { in: 'equals', not_in: 'not equals', equals: 'equals', not_equals: 'not equals',
        contains: config.filter_type === 'map' ? 'contains entries' : 'contains', not_contains: 'not contains',
        is_null: ['array', 'map'].includes(config.filter_type) ? 'is empty' : 'is null',
        is_not_null: ['array', 'map'].includes(config.filter_type) ? 'is not empty' : 'is not null',
        greater_than: 'greater than' };
      const captured: { status: number; query: Wire; body: UserPage; scope: unknown }[] = [];
      const pending: Promise<void>[] = [], errors: string[] = [];
      const listener = (r: Response) => {
        if (new URL(r.url()).pathname !== USERS || r.request().method() === 'OPTIONS') return;
        const query = requestBody(r), leaves = JSON.parse(String(query.filters ?? '[]')) as Leaf[];
        if (!leaves.some(l => l.column_id === field && l.filter_config.filter_op === config.filter_op)) return;
        pending.push((async () => { captured.push({ status: r.status(), query, body: await r.json(), scope: await headers(r) }); })()
          .catch(error => { errors.push(String(error)); }));
      };
      page.on('response', listener);
      try {
        await panel.getByRole('combobox').first().click();
        await page.getByRole('option', { name: operatorLabels[config.filter_op] ?? config.filter_op, exact: true }).click();
        if (!['is_null', 'is_not_null'].includes(config.filter_op)) {
          if (config.filter_type === 'number') await page.getByPlaceholder('Value', { exact: true }).fill(String(config.filter_value));
          else if (config.filter_type === 'boolean') {
            await panel.getByRole('combobox').last().click();
            await page.getByRole('option', { name: String(config.filter_value), exact: true }).click();
          } else if (config.filter_type === 'map') await page.getByPlaceholder('{"key":"value"}', { exact: true }).fill(JSON.stringify(config.filter_value));
          else {
            const valueScope = { source: config.col_type === 'SPAN_ATTRIBUTE' ? 'traces' : 'sessions',
              project_ids: project?.id ?? '', property_id: c.leaf.property_id };
            const r = await catalogAfter(VALUES, { ...valueScope, search: '' },
              () => page.locator(`[data-filter-value-trigger=${JSON.stringify(field)}]`).click(), response => settledValues(response, ''));
            await headers(r); expect(r.status()).toBe(200);
            const q = requestBody(r);
            expect(q.source).toBe(config.col_type === 'SPAN_ATTRIBUTE' ? 'traces' : 'sessions');
            expect(q.property_id).toBe(c.leaf.property_id);
            expect(q.project_ids ?? '').toBe(project?.id ?? '');
            const vocabulary = await r.json() as ValuePage;
            if (field === 'user_id_hash') expect(vocabulary.result.values.map(v => v.value), 'Native hash vocabulary must not be silently replaced by manual entry')
              .toContain(own[0].hash);
            const values = Array.isArray(config.filter_value) ? config.filter_value : [config.filter_value];
            for (const [i, value] of values.entries()) {
              // Both same-label typed selections and later cases can reuse this
              // exact query, but never another document, scope or property receipt.
              const searched = await catalogAfter(VALUES, { ...valueScope, search: String(value) },
                () => page.getByPlaceholder('Search values...').fill(String(value)), response => settledValues(response, String(value)));
              await headers(searched); expect(searched.status()).toBe(200);
              expect((await searched.json()).result).toMatchObject({ query_complete: true, query_status: 'complete',
                has_more: false, next_cursor: null });
              await expect(page.getByText('Loading values…', { exact: true })).toBeHidden({ timeout: UI_READY });
              expect(await page.getByPlaceholder('Search values...').inputValue()).toBe(String(value));
              let choice = page.locator(`[data-filter-value-option=${JSON.stringify(String(value))}][role="checkbox"]`);
              if (!['in', 'not_in', 'contains'].includes(config.filter_op)) choice = page.locator(`[data-filter-value-option=${JSON.stringify(String(value))}][role="radio"]`);
              // No invented type hook or positional choice: same-label options
              // currently have no description/type text (filterValuePickerUtils).
              // A real ambiguity is a capability failure, not a string fallback.
              if (field === key.mixed && String(value) === '7') {
                await expect(choice).toHaveCount(2, { timeout: UI_READY });
                const texts = await choice.allTextContents();
                expect(new Set(texts).size, `Cannot choose ${config.attribute_value_types?.[i]} 7 from indistinguishable catalog labels: ${JSON.stringify(texts)}`).toBe(2);
                choice = choice.filter({ hasText: new RegExp(config.attribute_value_types![i], 'i') });
              }
              if (c.name === 'text in ["not-seeded"]') {
                // Only this deliberate zero-match case uses ValuePicker's real
                // + Specify row. Retained typed values must use suggestions.
                choice = page.locator(`[data-filter-value-option=${JSON.stringify(String(value))}]`).filter({ hasText: '+ Specify:' });
              }
              await choice.click();
            }
            await page.keyboard.press('Escape');
          }
        }
        await page.keyboard.press('Escape'); filtered = true;
        await expect(page.getByRole('tab', { name: 'Basic', exact: true })).toBeHidden({ timeout: UI_READY });
        await expect.poll(() => captured.length, { timeout: UI_READY }).toBeGreaterThan(0);
        await Promise.all(pending);
        expect(errors).toEqual([]);
        await expect.poll(() => {
          const latest = captured.at(-1);
          return latest ? (JSON.parse(String(latest.query.filters)) as Leaf[])
            .find(leaf => leaf.column_id === field)?.filter_config : undefined;
        }, { timeout: UI_READY }).toEqual(config);
        const last = captured.at(-1)!; expect(last.status).toBe(200);
        const leaf = (JSON.parse(String(last.query.filters)) as Leaf[]).find(l => l.column_id === field)!;
        expect(leaf.property_id).toBe(c.leaf.property_id); expect(leaf.filter_config).toEqual(config);
        expect(last.query.project_id ?? '').toBe(project?.id ?? '');
        const expected = expectedIds(expectedPeople);
        expect(expected.length).toBeLessThanOrEqual(25);
        expect(Number(last.query.page_size)).toBe(25);
        // This finite fixture fits one UI page. An incomplete empty chunk is
        // not zero matches, even if a separately replayed API query is green.
        expect(last.body.result).toMatchObject({ query_complete: true, query_status: 'complete',
          query_exact: true, ordering_exact: true, has_more: false, next_cursor: null });
        expect(userIds(last.body.result.table), `UI captured ${c.name}`).toEqual(expected);
        const { cursor, ...freshQuery } = last.query;
        expect(userIds((await userPages(uiActor, freshQuery)).rows)).toEqual(expected);
        await expect.poll(visibleUserIds, { timeout: UI_READY }).toEqual(expected);
      } finally { page.off('response', listener); await Promise.all(pending); await attach(`ui-${c.name}`, { captured, errors }); }
    };
    await gate('UI stage 2: native Users identity, type, hash and metric capability', async () => {
      await test.step('native controls', async () => {
        await openUsers(p1);
        for (const name of ['native label in', 'native type', 'native metric total_tokens',
          'native date activated_at absent', 'native date last_active absent', 'native hash']) {
          const before = capabilityFailures.length;
          await gate(`UI ${name}`, () => select(cases.find(c => c.name === name)!));
          if (capabilityFailures.length > before) await openUsers(p1);
        }
      });
    });
    await gate('UI stage 3: scalar catalog selections and exact typed result sets', async () => {
      await test.step('scalar controls', async () => {
        await openUsers(p1);
        for (const name of ['mixed string only', 'mixed number only', 'mixed both types', 'mixed false', 'boolean equals', 'number equals', 'opaque Unicode key']) {
          const before = capabilityFailures.length;
          await gate(`UI ${name}`, () => select(cases.find(c => c.name === name)!));
          if (capabilityFailures.length > before) await openUsers(p1);
        }
      });
    });
    await gate('UI stage 4: structured, missing, collision and zero matches', async () => {
      await test.step('structured controls', async () => {
        await openUsers(p1);
        for (const name of ['array contains', 'map contains', 'text is_null ""', 'native name collision', 'text in ["not-seeded"]']) {
          const before = capabilityFailures.length;
          await gate(`UI ${name}`, () => select(cases.find(c => c.name === name)!));
          if (capabilityFailures.length > before) await openUsers(p1);
        }
        await openUsers();
        await gate('UI workspace custom filter includes the matching sibling-project identity', () =>
          select(cases.find(c => c.name === 'earlier span witness')!, null,
            [own[0], persons.find(u => u.project === p2 && u.label === shared)!]));
        await openUsers();
        await page.getByRole('button', { name: 'Filter', exact: true }).click();
        await page.getByRole('button', { name: 'Property', exact: true }).first().click();
        // Real search gestures; no delayed/mocked response or forced race. Retain
        // actual transport, and require only the final query's exact UI set.
        const propertySearch = page.getByPlaceholder('Search properties...');
        const outgoing: Request[] = [];
        const onOutgoing = (r: Request) => { if (r.method() === 'POST' && [METRICS, VALUES].includes(new URL(r.url()).pathname)) outgoing.push(r); };
        page.on('request', onOutgoing);
        try {
          await propertySearch.fill(key.foreign);
          await expect.poll(() => outgoing.some(r => new URL(r.url()).pathname === METRICS
            && r.postDataJSON().search === key.foreign), { timeout: UI_READY }).toBe(true);
          await searchProperty(key.text, null);
          await expect.poll(() => page.locator('[data-filter-property-option][data-filter-property-category="attribute"]:visible')
            .evaluateAll(options => options.map(o => o.getAttribute('data-filter-property-option')).sort()), { timeout: UI_READY }).toEqual([key.text]);
          await page.locator(`[data-filter-property-option=${JSON.stringify(key.text)}][data-filter-property-category="attribute"]`).click();
          await page.locator(`[data-filter-value-trigger=${JSON.stringify(key.text)}]`).click();
          await page.getByPlaceholder('Search values...').fill('alpha');
          await expect.poll(() => outgoing.some(r => new URL(r.url()).pathname === VALUES
            && r.postDataJSON().search === 'alpha'), { timeout: UI_READY }).toBe(true);
          const valueResponse = await catalogAfter(VALUES, { source: 'traces', project_ids: '',
            property_id: `custom_attribute:${key.text}`, search: 'omega' },
          () => page.getByPlaceholder('Search values...').fill('omega'), response => settledValues(response, 'omega'));
          await headers(valueResponse); expect(valueResponse.status()).toBe(200);
          const valueBody = await valueResponse.json();
          expect(valueBody.result).toMatchObject({ query_complete: true, query_status: 'complete', has_more: false, next_cursor: null });
          expect(valueBody.result.values.map((v: Value) => [v.type, v.value])).toEqual([['string', 'omega']]);
          await expect.poll(() => page.locator('[data-filter-value-option][role="checkbox"]:visible').evaluateAll(options =>
            options.map(o => o.getAttribute('data-filter-value-option'))), { timeout: UI_READY }).toEqual(['omega']);
        } finally { page.off('request', onOutgoing); }
        await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
        await openUsers();
        await page.getByRole('button', { name: 'Filter', exact: true }).click();
        await page.getByRole('button', { name: 'Property', exact: true }).first().click();
        for (const forbidden of UNSUPPORTED) {
          await searchProperty(forbidden, null, 'system', false);
          await expect(page.locator(`[data-filter-property-option=${JSON.stringify(forbidden)}][data-filter-property-category="system"]`)).toHaveCount(0, { timeout: UI_READY });
        }
        await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
      });
    });
    const detail = async (allowed: Project[]) => {
      const userFacts = facts.filter(f => f.person?.label === shared && allowed.includes(f.project));
      const sessionIds = [...new Set(userFacts.map(f => f.sessionId))].sort();
      // ObserveToolbar.DATE_OPTIONS / useLLMTracingFilters: real menu gestures,
      // no URL-filter or storage injection to hide the user-mode Today default.
      const pastWeek = async (path: string) => {
        await page.getByRole('button', { name: 'Today', exact: true }).click();
        const response = await responseAfter(r => {
          if (new URL(r.url()).pathname !== path || r.request().method() === 'OPTIONS') return false;
          const date = (JSON.parse(String(requestBody(r).filters)) as Leaf[]).find(f => f.column_id === 'created_at');
          const range = date?.filter_config.filter_value;
          return Array.isArray(range) && Date.parse(range[1]) - Date.parse(range[0]) >= 6 * 86_400_000;
        }, () => page.getByRole('menuitem', { name: 'Past 7D', exact: true }).click());
        const date = (JSON.parse(String(JSON.parse(response.requestJSON).filters)) as Leaf[]).find(f => f.column_id === 'created_at')!;
        expect(date.filter_config.filter_type).toBe('datetime');
        expect(date.filter_config.filter_op).toBe('between');
        const range = date.filter_config.filter_value as string[];
        for (const fact of userFacts) {
          expect(fact.startMs).toBeGreaterThanOrEqual(Date.parse(range[0]));
          expect(fact.startMs + 50).toBeLessThanOrEqual(Date.parse(range[1]));
        }
        return response;
      };
      const person = persons.find(u => u.label === shared && u.project === allowed[0])!;
      await page.locator(`.ag-row[row-id=${JSON.stringify(`${person.project.id}:${person.id}`)}] [col-id="user_id"]`).click();
      await expect(page).toHaveURL(url => url.pathname === `/dashboard/users/${encodeURIComponent(shared)}`, { timeout: UI_READY });
      const sessions = await pastWeek(SESSIONS); expect(sessions.status).toBe(200);
      const query = JSON.parse(sessions.requestJSON) as Wire;
      expect(query.project_id).toBeUndefined();
      expect((JSON.parse(String(query.filters)) as Leaf[]).find(f => f.column_id === 'user_id')!.filter_config.filter_value).toBe(shared);
      expect(JSON.parse(sessions.bodyJSON).result.table.map((s: { session_id: string }) => s.session_id).sort()).toEqual(sessionIds);
      await expect.poll(() => page.locator('.ag-row [col-id="session_id"]:visible').evaluateAll(cells =>
        cells.map(c => c.closest('.ag-row')!.getAttribute('row-id')).sort()), { timeout: UI_READY }).toEqual(sessionIds);
      const chosen = userFacts[0].sessionId;
      const response = await responseAfter(r => new URL(r.url()).pathname === `/tracer/trace-session/${chosen}/query/`,
        () => page.locator(`.ag-row[row-id=${JSON.stringify(chosen)}] [col-id="session_id"]`).click());
      expect(response.status).toBe(200);
      const body = JSON.parse(response.bodyJSON);
      expect(body.result.response.map((t: { trace_id: string }) => t.trace_id).sort()).toEqual(userFacts.filter(f => f.sessionId === chosen).map(f => f.seeded.traceId).sort());
      const drawer = page.locator('.MuiDrawer-paper:visible').filter({ has: page.getByRole('tablist', { name: 'session drawer tabs' }) });
      const firstTrace = body.result.response[0].trace_id;
      await drawer.getByRole('button', { name: 'View Trace', exact: true }).first().click();
      await expect(page.getByText(userFacts.find(f => f.seeded.traceId === firstTrace)!.seeded.spanIds[0], { exact: true }).first()).toBeVisible({ timeout: UI_READY });
      // Return through the actual user detail and choose its Traces tab.
      await page.goto(`/dashboard/users/${encodeURIComponent(shared)}`, { waitUntil: 'domcontentloaded' });
      // FixedTab's inactive tooltip sets aria-label="Press 2"; match its exact visible Trace label within MAIN.
      await page.getByRole('main').getByRole('button').filter({ hasText: /^Trace$/ }).click();
      const traces = await pastWeek(TRACES); expect(traces.status).toBe(200);
      expect(JSON.parse(traces.bodyJSON).result.table.map((t: { trace_id: string }) => t.trace_id).sort()).toEqual(userFacts.map(f => f.seeded.traceId).sort());
      const traceQuery = JSON.parse(traces.requestJSON) as Wire;
      expect(traceQuery.project_id).toBeUndefined();
      expect((JSON.parse(String(traceQuery.filters)) as Leaf[]).find(f => f.column_id === 'user_id')!.filter_config.filter_value).toBe(shared);
      await expect.poll(() => page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]').allTextContents(), { timeout: UI_READY })
        .toEqual([...userFacts].sort((a, b) => b.startMs - a.startMs).map(f => f.rootName));
      await expect.poll(() => page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]').evaluateAll(cells =>
        cells.map(c => c.closest('.ag-row')!.getAttribute('row-id')).sort()), { timeout: UI_READY })
        .toEqual(userFacts.map(f => f.seeded.traceId).sort());
      await attach(`detail-${uiActor.workspaceId}`, { sessionIds, expectedTraceIds: userFacts.map(f => f.seeded.traceId), query, traceQuery, body });
    };
    await gate('check 4 / UI stage 5: label-based sibling-project session and trace union', async () => {
      await test.step('user journey', async () => { await openUsers(p1); await detail([p1, p2]); });
    });
    await test.step('check 5: foreign scope and real cursor rejection', async () => {
      for (const foreign of [p3, p4]) {
        expect((await userPages(scopes.member, userBody(foreign))).rows).toEqual([]);
        for (const ids of [foreign.id, `${p1.id},${foreign.id}`]) for (const path of [METRICS, VALUES]) {
          const r = await scopes.send<{ code: string }>(scopes.member, 'POST', path, path === METRICS
            ? { source: 'traces', category: 'custom_attribute', cursor_mode: true, page_size: 1, project_ids: ids }
            : { source: 'traces', property_id: `custom_attribute:${key.text}`, page_size: 1, project_ids: ids });
          receipts.push({ crossScope: ids, path, ...r }); expect(r.status).toBe(400); expect(r.body.code).toBe('invalid');
        }
      }
      expect((await scopes.send(scopes.withWorkspace(scopes.viewer, a2.workspaceId), 'POST', USERS, userBody())).status).toBe(403);
      for (const path of [METRICS, VALUES, USERS]) {
        const body = path === METRICS ? { source: 'users', category: 'system_metric', project_ids: p1.id, cursor_mode: true, page_size: 1 }
          : path === VALUES ? { source: 'sessions', property_id: 'system_attribute:users:user', project_ids: p1.id, page_size: 1 }
            : userBody(p1, [], 1);
        const first = await read<{ result: { has_more: boolean; next_cursor: string } }>(scopes.member, path, body);
        expect(first.result.has_more).toBe(true); expect(first.result.next_cursor).toBeTruthy();
        // Prove the issued continuation works before using it as a denial anchor.
        await read(scopes.member, path, { ...body, cursor: first.result.next_cursor });
        for (const [actor, project] of [[scopes.member, p2], [memberA2, p3], [scopes.viewer, p1], [p4.reader, p4]] as const) {
          const rejected = await scopes.send<{ code: string }>(actor, 'POST', path, { ...body,
            ...(path === USERS ? { project_id: project.id } : { project_ids: project.id }), cursor: first.result.next_cursor });
          receipts.push({ cursor: path, ...rejected }); expect(rejected.status).toBe(400); expect(rejected.body.code).toBe('cursor_mismatch');
        }
        const changed = await scopes.send<{ code: string }>(scopes.member, 'POST', path,
          { ...body, search: 'changed-query', cursor: first.result.next_cursor });
        expect(changed.status).toBe(400); expect(changed.body.code).toBe('cursor_mismatch');
        const invalid = await scopes.send<{ code: string }>(scopes.member, 'POST', path, { ...body, cursor: 'tampered-' + first.result.next_cursor });
        expect(invalid.status).toBe(400); expect(invalid.body.code).toBe('invalid_cursor');
      }
    });
    await gate('check 5 / UI stage 6: real switch, destination journey and refresh', async () => {
      await test.step('destination workspace', async () => {
        await openUsers();
        const labels = await probe.pg<{ id: string; label: string }>(
          "SELECT id, coalesce(nullif(display_name, ''), name) AS label FROM accounts_workspace WHERE id=ANY($1)", [[a1.workspaceId, a2.workspaceId]]);
        const trigger = page.getByText(new RegExp(`^(${labels.map(l => l.label).join('|')})$`)).filter({ visible: true });
        await expect(trigger).toHaveCount(1, { timeout: UI_READY }); const displayed = (await trigger.textContent())!.trim();
        expect(await page.evaluate(() => sessionStorage.getItem('workspaceId'))).toBe(a1.workspaceId);
        await trigger.click(); await page.locator('.MuiPopover-root:visible').getByText(displayed, { exact: true }).hover();
        const response = await responseAfter(r => new URL(r.url()).pathname === SWITCH && r.request().method() === 'POST',
          () => page.getByRole('tooltip').getByText(labels.find(l => l.id === a2.workspaceId)!.label, { exact: true }).click(), 'switch-metadata');
        expect(response.status).toBe(200);
        expect(JSON.parse(response.requestJSON)).toEqual({ old_workspace_id: a1.workspaceId, new_workspace_id: a2.workspaceId });
        expect(await probe.pg("SELECT config->>'currentWorkspaceId' AS current_workspace FROM accounts_user WHERE id=$1", [scopes.member.userId]))
          .toEqual([{ current_workspace: a2.workspaceId }]);
        uiActor = memberA2;
        await expect.poll(() => page.evaluate(() => sessionStorage.getItem('workspaceId')), { timeout: UI_READY }).toBe(a2.workspaceId);
        await openUsers(); await detail([p3]);
        await page.reload({ waitUntil: 'domcontentloaded' });
        await openUsers();
        expect(userIds((await userPages(memberA2, userBody())).rows)).toEqual(expectedIds(persons.filter(u => u.project === p3)));
        await testInfo.attach('destination-users', { body: await page.screenshot(), contentType: 'image/png' });
      });
    });
    await Promise.all(catalogPending);
    await attach('ui-catalog-transport', { catalogRequests, catalogErrors });
    expect(catalogErrors).toEqual([]);
    expect(await source(), 'read-only qualification must not mutate authoritative spans').toEqual(baselineSource);
    expect(capabilityFailures, 'Every UI/native capability gate is mandatory; API parity alone is not OBS007 qualification').toEqual([]);
  } finally {
    await context?.close();
    await Promise.all(catalogPending);
    await attach('ui-catalog-transport-final', { catalogRequests, catalogErrors });
    for (const k of minted.filter(k => k.enabled)) {
      const disabled = await scopes.send(k.owner, 'POST', DISABLE, { key_id: k.id });
      expect(disabled.status).toBe(200); k.enabled = false;
    }
    await attach('obs007-receipts', { prefix, receipts, capabilityFailures, keys: minted.map(k => ({ id: k.id, enabled: k.enabled })),
      limitations: [...scopes.limitations, 'Explicit scope only; stale workspace/sidebar cache is not qualified',
        'End-user CRUD, historical corrections, population graphs, export and positive ERROR status remain separate release gates',
        'Literal OTLP null unavailable; missing keys cover null predicates; no serving, model or paid executions'] });
    await requestContext.dispose();
  }
});
