import { randomBytes, randomUUID } from 'node:crypto';
import type { TestInfo } from '@playwright/test';
import type { ApiClient } from './api-client';
import { E2E } from './env';

// Seeded ClickHouse fixtures: ONE internal trace-session UUID in several projects of
// one organization, for controls that must prove a lookup stays keyed to a record's
// own project (F54 row-source liveness: model_hub/services/row_sources.py
// _live_records -> trace_session_dict_reader.resolve_session_fields(project_ids=...)).
//
// The collector cannot produce this state: it derives trace_session_id as
// UUIDv5(project|session.id) (fi-collector/pkg/detid/detid.go TraceSessionID), and PG
// tracer_trace_session keys sessions by id. The stores the reads use CAN hold it:
// trace_sessions is ReplacingMergeTree(version) ORDER BY (project_id, trace_session_id)
// (v2/schema/018_trace_sessions.sql), and spans carry trace_session_id as a plain
// column under a project-prefixed key (002_spans_v2.sql). The loader checks both
// sorting keys before every write and refuses if either stops holding.
//
// Writes exactly two tables, through the stack's ClickHouse HTTP endpoint (E2E.chUrl):
//   spans          a root and an llm child per turn, every one labelled
//                  attrs_string['e2e.seeded'] = seedId (an attribute no read acts on);
//                  inserting them feeds the spans_* materialized views the way a
//                  collector insert does.
//   trace_sessions one row per project, carrying this plan's nonce in e2e_seed_id.
//                  The loader adds this fixture-only String column (default '') on
//                  the loopback stack, after refusing any occupied foreign project.
//                  Product schema and sorting keys are unchanged; collector rows
//                  default to an empty marker and can never be claimed by a plan.
// Never written: PG tracer_trace_session (its primary key cannot hold the state),
// trace_sessions_dict (PRIMARY KEY trace_session_id; F54 liveness reads the table with
// FINAL, not the dict), trace_session_id_remap.
//
// FINAL-safe and idempotent per minted id: every row carries the plan's explicit
// version, rows already written by the plan are skipped (so the count-state rollups
// are never fed twice), and a (project, id) pair holding a row the plan did not write
// is refused before anything is written. Fabricated rows go only to a loopback stack.

/** The span attribute (attrs_string) every seeded span carries, valued with the seed id. */
export const SEEDED_ATTRIBUTE = 'e2e.seeded';

const MAX_PROJECTS = 10;
const MAX_TURNS = 20;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const NIL_UUID = '00000000-0000-0000-0000-000000000000';
const LOOPBACK = new Set(['localhost', '127.0.0.1', '::1', '[::1]']);
const SESSION_COLUMNS = 'project_id, trace_session_id, external_session_id, first_seen, version, is_deleted, e2e_seed_id';
// A marker plus the exact live/tombstone signature, never an open-ended version range.
const OWN_SESSION = `ts.e2e_seed_id = {seed:String} AND ts.external_session_id = {ext:String}
  AND ts.first_seen = {first:DateTime64(6, 'UTC')}
  AND ((ts.is_deleted = 0 AND ts.version = {v:DateTime64(6, 'UTC')})
    OR (ts.is_deleted = 1 AND ts.version = addMicroseconds({v:DateTime64(6, 'UTC')}, 1)))`;
const SPAN_COLUMNS = 'project_id, observation_type, service_name, start_time, trace_id, id, parent_span_id, '
  + 'name, end_time, latency_ms, org_id, trace_session_id, status, input, output, attrs_string, is_deleted, _version';

export interface SeededClickHouse { url: string; database: string; password: string }

/** The managed or attached stack's ClickHouse (E2E_CH_URL, E2E_CH_DB, E2E_CH_PASSWORD). */
export function stackClickHouse(): SeededClickHouse {
  return { url: E2E.chUrl, database: E2E.chDatabase, password: E2E.chPassword };
}

export interface SeedProjectSpec {
  /** An existing tracer project of `orgId` (its PG row is the product's own). */
  projectId: string;
  /** Turns (a trace of a root and an llm child) in this project: 1-20, default 1. */
  turns?: number;
}

export interface SharedSessionSpec {
  orgId: string;
  /** 1-10 distinct projects; two for the same-id-in-two-projects state. */
  projects: readonly SeedProjectSpec[];
  /** Default: a minted UUID. Pass a collector session's id to shadow it into OTHER projects. */
  traceSessionId?: string;
  /** trace_sessions.external_session_id in every project. Default: a minted e2e-seeded-<hex>.
   *  When shadowing, pass the collector's own value so an id-keyed label read agrees. */
  externalSessionId?: string;
  /** First turn's root start. Default: one minute before planning. */
  startedAt?: Date;
}

export interface SeededTurn {
  traceId: string; rootSpanId: string; childSpanId: string;
  rootStart: string; rootEnd: string; childStart: string; childEnd: string;
  input: string; output: string; childInput: string; childOutput: string;
}

export interface SeededProject { projectId: string; turns: SeededTurn[]; traceIds: string[]; spanIds: string[] }

export interface SharedSessionPlan {
  seedId: string; orgId: string; traceSessionId: string; externalSessionId: string;
  /** trace_sessions.first_seen of every seeded row (the first root start). */
  firstSeen: string;
  /** trace_sessions.version of every seeded row, DateTime64(6) text. */
  version: string;
  /** The same instant in nanoseconds: spans._version of every seeded span. */
  versionNs: string;
  projects: SeededProject[];
}

export interface SeedReceipt {
  kind: 'seeded-clickhouse'; action: 'seed' | 'tombstone';
  seedId: string; orgId: string; traceSessionId: string; externalSessionId: string;
  database: string;
  projects: { projectId: string; traceIds: string[]; spanIds: string[] }[];
  tables: {
    spans: { label: string; planned: number; inserted: number; alreadyPresent: number };
    trace_sessions: { label: string; version: string; planned: number; inserted: number; alreadyPresent: number };
  };
  notWritten: string[];
}

export interface SeededSharedSession { plan: SharedSessionPlan; receipt: SeedReceipt }

export interface LiveSessionRow { project_id: string; trace_session_id: string; external_session_id: string }

function canonicalUuid(value: unknown, name: string): string {
  const text = typeof value === 'string' ? value.trim().toLowerCase() : '';
  if (!UUID_RE.test(text) || text === NIL_UUID) throw new Error(`${name} must be a non-nil canonical UUID`);
  return text;
}

/** DateTime64(6, 'UTC') text: epoch milliseconds plus 0-999 extra microseconds. */
export function clickHouseTime(ms: number, micros = 0): string {
  if (!Number.isInteger(ms) || !Number.isInteger(micros) || micros < 0 || micros > 999) {
    throw new Error('clickHouseTime takes integer milliseconds and 0-999 microseconds');
  }
  const iso = new Date(ms).toISOString();
  return `${iso.slice(0, 10)} ${iso.slice(11, 23)}${String(micros).padStart(3, '0')}`;
}

function spanId(used: Set<string>): string {
  for (;;) {
    const id = randomBytes(8).toString('hex');
    if (!/^0+$/.test(id) && !used.has(id)) { used.add(id); return id; }
  }
}

/** Mints every id and time of a seed; pure apart from randomness. Seed it with seedSharedSession. */
export function planSharedSession(spec: SharedSessionSpec, now: Date = new Date()): SharedSessionPlan {
  const orgId = canonicalUuid(spec.orgId, 'orgId');
  const listed: readonly SeedProjectSpec[] = spec.projects ?? [];
  if (listed.length < 1 || listed.length > MAX_PROJECTS) throw new Error(`projects must list 1-${MAX_PROJECTS} projects`);
  const seen = new Set<string>();
  const projectSpecs = listed.map((p, i) => {
    const projectId = canonicalUuid(p?.projectId, `projects[${i}].projectId`);
    if (seen.has(projectId)) throw new Error(`projects[${i}] repeats ${projectId}`);
    seen.add(projectId);
    const turns = p.turns ?? 1;
    if (!Number.isInteger(turns) || turns < 1 || turns > MAX_TURNS) {
      throw new Error(`projects[${i}].turns must be an integer 1-${MAX_TURNS}`);
    }
    return { projectId, turns };
  });
  const traceSessionId = spec.traceSessionId === undefined
    ? randomUUID() : canonicalUuid(spec.traceSessionId, 'traceSessionId');
  const externalSessionId = spec.externalSessionId ?? `e2e-seeded-${randomBytes(4).toString('hex')}`;
  if (typeof externalSessionId !== 'string' || !externalSessionId || externalSessionId.length > 200) {
    throw new Error('externalSessionId must be 1-200 characters');
  }
  const nowMs = now.getTime();
  const baseMs = spec.startedAt === undefined ? nowMs - 60_000 : spec.startedAt.getTime();
  if (!Number.isFinite(nowMs) || !Number.isFinite(baseMs) || baseMs > nowMs) {
    throw new Error('startedAt must be a valid time no later than now');
  }
  const versionMicros = randomBytes(2).readUInt16BE(0) % 1000;
  const used = new Set<string>();
  const projects = projectSpecs.map(({ projectId, turns }, p) => {
    const label = `${externalSessionId} p${p + 1}`;
    const seeded: SeededTurn[] = Array.from({ length: turns }, (_, t) => {
      const root = baseMs + t * 1000;
      // The child starts after its root so argMin/argMax(input, start_time) reads are exact.
      return {
        traceId: randomUUID(), rootSpanId: spanId(used), childSpanId: spanId(used),
        rootStart: clickHouseTime(root), rootEnd: clickHouseTime(root + 50),
        childStart: clickHouseTime(root + 10), childEnd: clickHouseTime(root + 40),
        input: `${label} turn ${t + 1} asks`, output: `${label} turn ${t + 1} answers`,
        childInput: `${label} turn ${t + 1} calls`, childOutput: `${label} turn ${t + 1} returns`,
      };
    });
    return { projectId, turns: seeded, traceIds: seeded.map(t => t.traceId),
      spanIds: seeded.flatMap(t => [t.rootSpanId, t.childSpanId]) };
  });
  return {
    seedId: `seed-${randomUUID()}`, orgId, traceSessionId, externalSessionId,
    firstSeen: clickHouseTime(baseMs), version: clickHouseTime(nowMs, versionMicros),
    versionNs: String(BigInt(nowMs) * 1_000_000n + BigInt(versionMicros) * 1_000n),
    projects,
  };
}

/** JSONEachRow lines for every planned span; `_version` is spliced in as an exact UInt64. */
export function spanRowLines(plan: SharedSessionPlan, only?: ReadonlySet<string>): string[] {
  if (!/^\d+$/.test(plan.versionNs)) throw new Error('plan.versionNs must be decimal nanoseconds');
  const lines: string[] = [];
  for (const project of plan.projects) {
    for (const turn of project.turns) {
      const rows = [
        { id: turn.rootSpanId, parent_span_id: '', observation_type: 'chain', name: 'e2e.seeded.turn',
          start_time: turn.rootStart, end_time: turn.rootEnd, latency_ms: 50, input: turn.input, output: turn.output },
        { id: turn.childSpanId, parent_span_id: turn.rootSpanId, observation_type: 'llm', name: 'e2e.seeded.llm',
          start_time: turn.childStart, end_time: turn.childEnd, latency_ms: 30, input: turn.childInput, output: turn.childOutput },
      ];
      for (const row of rows) {
        if (only && !only.has(`${project.projectId}/${row.id}`)) continue;
        const json = JSON.stringify({
          project_id: project.projectId, observation_type: row.observation_type, service_name: 'e2e-seeded',
          start_time: row.start_time, trace_id: turn.traceId, id: row.id, parent_span_id: row.parent_span_id,
          name: row.name, end_time: row.end_time, latency_ms: row.latency_ms, org_id: plan.orgId,
          trace_session_id: plan.traceSessionId, status: 'OK', input: row.input, output: row.output,
          attrs_string: { [SEEDED_ATTRIBUTE]: plan.seedId }, is_deleted: 0,
        });
        lines.push(`${json.slice(0, -1)},"_version":${plan.versionNs}}`);
      }
    }
  }
  return lines;
}

/** JSONEachRow lines for the trace_sessions row of each listed (default: every) project. */
export function sessionRowLines(plan: SharedSessionPlan, projectIds?: readonly string[]): string[] {
  const wanted = projectIds ?? plan.projects.map(p => p.projectId);
  return wanted.map(projectId => JSON.stringify({
    project_id: projectId, trace_session_id: plan.traceSessionId, external_session_id: plan.externalSessionId,
    first_seen: plan.firstSeen, version: plan.version, is_deleted: 0, e2e_seed_id: plan.seedId,
  }));
}

async function chRequest(ch: SeededClickHouse, query: string,
  opts: { params?: Record<string, string>; data?: string } = {}): Promise<string> {
  const url = new URL(ch.url);
  if (!LOOPBACK.has(url.hostname)) {
    throw new Error(`seeded ClickHouse refuses ${url.hostname}: fabricated rows go only to a loopback e2e stack`);
  }
  url.searchParams.set('database', ch.database);
  for (const [key, value] of Object.entries(opts.params ?? {})) url.searchParams.set(`param_${key}`, value);
  let body = query;
  if (opts.data !== undefined) {
    url.searchParams.set('query', query);
    body = opts.data;
  } else {
    url.searchParams.set('default_format', 'JSONEachRow');
  }
  const res = await fetch(url, { method: 'POST', body,
    headers: { 'X-ClickHouse-User': 'default', 'X-ClickHouse-Key': ch.password } });
  const text = await res.text();
  if (!res.ok) throw new Error(`seeded ClickHouse ${res.status}: ${text.slice(0, 500)}`);
  return text;
}

async function chRows<T>(ch: SeededClickHouse, query: string, params: Record<string, string> = {}): Promise<T[]> {
  const text = (await chRequest(ch, query, { params })).trim();
  return text ? text.split('\n').map(line => JSON.parse(line) as T) : [];
}

const uuidArray = (ids: readonly string[]) => `[${ids.map(id => `'${canonicalUuid(id, 'id')}'`).join(',')}]`;

/** Refuses unless both stores still hold one id in two projects (Stage-1 premise, re-checked live). */
async function assertRepresentable(ch: SeededClickHouse): Promise<void> {
  const rows = await chRows<{ name: string; engine: string; sorting_key: string }>(ch,
    "SELECT name, engine, sorting_key FROM system.tables WHERE database = currentDatabase() AND name IN ('spans', 'trace_sessions')");
  const table = new Map(rows.map(r => [r.name, r]));
  const sessions = table.get('trace_sessions');
  const spans = table.get('spans');
  const replacing = (engine?: string) => !!engine && /ReplacingMergeTree$/.test(engine);
  if (!sessions || !replacing(sessions.engine) || sessions.sorting_key !== 'project_id, trace_session_id') {
    throw new Error('trace_sessions is not ReplacingMergeTree ORDER BY (project_id, trace_session_id) '
      + `(${sessions ? `${sessions.engine} ORDER BY (${sessions.sorting_key})` : 'missing'}): it cannot hold `
      + 'one session id in two projects, so a seed would write a state no store holds');
  }
  if (!spans || !replacing(spans.engine) || !spans.sorting_key.startsWith('project_id, ')) {
    throw new Error(`spans is not a project-keyed ReplacingMergeTree (${spans ? `${spans.engine} ORDER BY (${spans.sorting_key})` : 'missing'})`);
  }
}

interface SessionClaim { project_id: string; ours: string; other_rows: string }

async function hasSessionMarker(ch: SeededClickHouse): Promise<boolean> {
  const columns = await chRows<{ name: string; type: string }>(ch,
    "SELECT name, type FROM system.columns WHERE database = currentDatabase() AND table = 'trace_sessions' AND name = 'e2e_seed_id'");
  if (columns.length && columns[0].type !== 'String') throw new Error('trace_sessions.e2e_seed_id must be String');
  return columns.length === 1;
}

const ownershipParams = (plan: SharedSessionPlan) => ({ s: plan.traceSessionId, seed: plan.seedId,
  ext: plan.externalSessionId, first: plan.firstSeen, v: plan.version });

/** Without a persisted nonce, EVERY existing row is foreign, regardless of its version. */
async function sessionClaims(ch: SeededClickHouse, plan: SharedSessionPlan): Promise<Map<string, { ours: number; foreign: number }>> {
  const own = await hasSessionMarker(ch) ? OWN_SESSION : '0';
  const rows = await chRows<SessionClaim>(ch,
    // Inspect physical rows as well as FINAL: a foreign row must not be hidden by our seed.
    `SELECT toString(ts.project_id) AS project_id,
       countIf(${own}) AS ours, countIf(NOT (${own})) AS other_rows
     FROM trace_sessions AS ts
     WHERE ts.trace_session_id = {s:UUID} AND has({ps:Array(UUID)}, ts.project_id)
     GROUP BY ts.project_id`,
    { ...ownershipParams(plan), ps: uuidArray(plan.projects.map(p => p.projectId)) });
  return new Map(rows.map(r => [r.project_id, { ours: Number(r.ours), foreign: Number(r.other_rows) }]));
}

async function seededSpanKeys(ch: SeededClickHouse, plan: SharedSessionPlan): Promise<Set<string>> {
  const rows = await chRows<{ project_id: string; id: string }>(ch,
    `SELECT DISTINCT toString(project_id) AS project_id, id FROM spans
     WHERE trace_session_id = {s:UUID} AND attrs_string[{attr:String}] = {seed:String}`,
    { s: plan.traceSessionId, attr: SEEDED_ATTRIBUTE, seed: plan.seedId });
  return new Set(rows.map(r => `${r.project_id}/${r.id}`));
}

interface Tally { planned: number; inserted: number; alreadyPresent: number }

function receiptFor(plan: SharedSessionPlan, ch: SeededClickHouse, action: SeedReceipt['action'],
  spans: Tally, sessions: Tally): SeedReceipt {
  return {
    kind: 'seeded-clickhouse', action, seedId: plan.seedId, orgId: plan.orgId,
    traceSessionId: plan.traceSessionId, externalSessionId: plan.externalSessionId, database: ch.database,
    projects: plan.projects.map(p => ({ projectId: p.projectId, traceIds: p.traceIds, spanIds: p.spanIds })),
    tables: {
      spans: { label: `attrs_string['${SEEDED_ATTRIBUTE}'] = seedId`, ...spans },
      trace_sessions: { label: 'e2e_seed_id = seedId (fixture-only column on the loopback stack)',
        version: plan.version, ...sessions },
    },
    notWritten: ['postgres tracer_trace_session (primary key id)', 'trace_sessions_dict (PRIMARY KEY trace_session_id)',
      'trace_session_id_remap'],
  };
}

async function attach(testInfo: TestInfo, receipt: SeedReceipt): Promise<void> {
  // One file per call: a re-run's or a no-op's receipt never replaces an earlier one.
  const n = (testInfo.attachments?.length ?? 0) + 1;
  await testInfo.attach(`seeded-clickhouse-${receipt.action}-${receipt.seedId}-${n}.json`,
    { body: JSON.stringify(receipt, null, 2), contentType: 'application/json' });
}

/**
 * Writes `plan` (or a plan minted from `spec`) into spans and trace_sessions: the same
 * internal session id in every listed project of one organization. Re-running the same
 * plan writes nothing. Refuses, before any write, a non-loopback endpoint, a store that
 * cannot hold the state, and a project already holding a row for the id that this plan
 * did not write (a collector session is shadowed into OTHER projects, never overwritten).
 */
export async function seedSharedSession(testInfo: TestInfo, planOrSpec: SharedSessionPlan | SharedSessionSpec,
  ch: SeededClickHouse = stackClickHouse()): Promise<SeededSharedSession> {
  const plan = 'seedId' in planOrSpec ? planOrSpec : planSharedSession(planOrSpec);
  await assertRepresentable(ch);
  const claims = await sessionClaims(ch, plan);
  for (const project of plan.projects) {
    const claim = claims.get(project.projectId);
    if (claim && claim.foreign > 0) {
      throw new Error(`project ${project.projectId} already holds trace session ${plan.traceSessionId} not seeded by this plan; `
        + 'shadow it into another project instead of overwriting it');
    }
  }
  // No schema write until the occupancy check passed. Never mark pre-existing rows:
  // DEFAULT '' keeps collector-owned rows foreign, including ones arriving later.
  if (!await hasSessionMarker(ch)) {
    await chRequest(ch, "ALTER TABLE trace_sessions ADD COLUMN IF NOT EXISTS e2e_seed_id String DEFAULT ''");
    if (!await hasSessionMarker(ch)) throw new Error('trace_sessions seed marker column did not land');
  }
  const present = await seededSpanKeys(ch, plan);
  const missing = new Set(plan.projects.flatMap(p => p.spanIds.map(id => `${p.projectId}/${id}`))
    .filter(key => !present.has(key)));
  if (missing.size) {
    await chRequest(ch, `INSERT INTO spans (${SPAN_COLUMNS}) FORMAT JSONEachRow`,
      { data: spanRowLines(plan, missing).join('\n') });
  }
  const sessionTargets = plan.projects.map(p => p.projectId).filter(id => !(claims.get(id)?.ours));
  if (sessionTargets.length) {
    await chRequest(ch, `INSERT INTO trace_sessions (${SESSION_COLUMNS}) FORMAT JSONEachRow`,
      { data: sessionRowLines(plan, sessionTargets).join('\n') });
  }
  const landedSpans = await seededSpanKeys(ch, plan);
  const landedSessions = await sessionClaims(ch, plan);
  const lost = plan.projects.filter(p => !(landedSessions.get(p.projectId)?.ours)
    || landedSessions.get(p.projectId)?.foreign
    || p.spanIds.some(id => !landedSpans.has(`${p.projectId}/${id}`)));
  if (lost.length) throw new Error(`seed ${plan.seedId} did not land in ${lost.map(p => p.projectId).join(', ')}`);
  const plannedSpans = plan.projects.reduce((n, p) => n + p.spanIds.length, 0);
  const receipt = receiptFor(plan, ch, 'seed',
    { planned: plannedSpans, inserted: missing.size, alreadyPresent: plannedSpans - missing.size },
    { planned: plan.projects.length, inserted: sessionTargets.length,
      alreadyPresent: plan.projects.length - sessionTargets.length });
  await attach(testInfo, receipt);
  return { plan, receipt };
}

/**
 * Live trace_sessions rows (FINAL, is_deleted = 0) for one id, sorted by project. With
 * `projectId` it is the project-keyed read F54 liveness makes; without it, the
 * deliberately project-unkeyed control that sees every project holding the id.
 */
export async function liveSessionRows(traceSessionId: string,
  opts: { projectId?: string; ch?: SeededClickHouse } = {}): Promise<LiveSessionRow[]> {
  const params: Record<string, string> = { s: canonicalUuid(traceSessionId, 'traceSessionId') };
  if (opts.projectId !== undefined) params.p = canonicalUuid(opts.projectId, 'projectId');
  const rows = await chRows<LiveSessionRow>(opts.ch ?? stackClickHouse(),
    `SELECT toString(ts.project_id) AS project_id, toString(ts.trace_session_id) AS trace_session_id,
       ts.external_session_id AS external_session_id
     FROM trace_sessions AS ts FINAL
     WHERE ts.trace_session_id = {s:UUID} AND ts.is_deleted = 0${params.p ? ' AND ts.project_id = {p:UUID}' : ''}`,
    params);
  return rows.sort((x, y) => (x.project_id < y.project_id ? -1 : x.project_id > y.project_id ? 1 : 0));
}

/**
 * Deletes this plan's trace_sessions row in one project the way a session delete does
 * (is_deleted = 1 at a newer version, so FINAL drops it), leaving the id live in the
 * plan's other projects. Spans are untouched. Only a row the plan seeded; a second
 * call is a no-op ({ tombstoned: false }).
 */
export async function tombstoneSeededSession(testInfo: TestInfo, plan: SharedSessionPlan, projectId: string,
  ch: SeededClickHouse = stackClickHouse()): Promise<{ tombstoned: boolean }> {
  const project = canonicalUuid(projectId, 'projectId');
  if (!plan.projects.some(p => p.projectId === project)) {
    throw new Error(`tombstoneSeededSession deletes only rows its plan seeded; ${project} is not in seed ${plan.seedId}`);
  }
  const claim = (await sessionClaims(ch, plan)).get(project);
  if (!claim?.ours || claim.foreign) throw new Error(`project ${project} holds no row seeded by ${plan.seedId}`);
  const params = { ...ownershipParams(plan), p: project };
  const live = await liveSessionRows(plan.traceSessionId, { projectId: project, ch });
  if (live.length) {
    await chRequest(ch,
      `INSERT INTO trace_sessions (${SESSION_COLUMNS})
       SELECT ts.project_id, ts.trace_session_id, ts.external_session_id, ts.first_seen,
         addMicroseconds(ts.version, 1), 1, ts.e2e_seed_id
       FROM trace_sessions AS ts FINAL
       WHERE ts.project_id = {p:UUID} AND ts.trace_session_id = {s:UUID} AND ts.is_deleted = 0
         AND (${OWN_SESSION})`, { params });
    if ((await liveSessionRows(plan.traceSessionId, { projectId: project, ch })).length) {
      throw new Error(`tombstone of ${plan.traceSessionId} in ${project} did not win under FINAL`);
    }
  }
  await attach(testInfo, receiptFor({ ...plan, projects: plan.projects.filter(p => p.projectId === project) }, ch,
    'tombstone', { planned: 0, inserted: 0, alreadyPresent: 0 },
    { planned: 1, inserted: live.length ? 1 : 0, alreadyPresent: live.length ? 0 : 1 }));
  return { tombstoned: live.length > 0 };
}

/**
 * Which planned project the product's project-UNKEYED session resolver keeps for the
 * plan's id: /tracer/trace-session/{id}/query/ (F54's session tab) resolves a CH-only
 * session through resolve_session_fields([id]) with no project, and that resolver keeps
 * one row per id by overwrite (trace_session.py _resolve_ch_session_fields). Put a
 * record in a project this does NOT return, and dropping the project clause from a
 * keyed read (F54 liveness) reads the other project's row. Null when the served traces
 * belong to no project this plan seeded (shadow mode: the collector's project).
 */
export async function projectServedByUnkeyedSessionRead(api: ApiClient, plan: SharedSessionPlan): Promise<string | null> {
  const body = await api.post<{ result: { response: { trace_id: string }[] } }>(
    `/tracer/trace-session/${plan.traceSessionId}/query/`, { page_number: 0, page_size: 50 });
  const served = new Set((body.result?.response ?? []).map(t => String(t.trace_id)));
  const hits = plan.projects.filter(p => p.traceIds.some(id => served.has(id)));
  return hits.length === 1 ? hits[0].projectId : null;
}
