// J04/PACKET.json sibling_state_contract, j04-active-sibling-projection/v1.
// Pure consumer validation, not a DB reader. readJ04SiblingState and its exact
// authority mappings remain gateway-owned. No W08 empty-B snapshot is reused.
import { check, ConsoleCoreFailure, sha } from '../journeys/console-core-seams';

type Fields = Record<string, string>;
const groups: Record<string, { fields: Fields; sort: readonly string[] }> = {
  users: { fields: { userId: 'id', isActive: 'boolean', deleted: 'boolean' }, sort: ['userId'] },
  orgMemberships: { fields: { id: 'id', orgId: 'id', userId: 'id', role: 'string', level: 'integer|null', isActive: 'boolean', deleted: 'boolean' }, sort: ['orgId', 'userId', 'id'] },
  workspaces: { fields: { id: 'id', orgId: 'id', isActive: 'boolean', isDefault: 'boolean', deleted: 'boolean' }, sort: ['orgId', 'id'] },
  workspaceMemberships: { fields: { id: 'id', workspaceId: 'id', userId: 'id', orgMembershipId: 'id|null', role: 'string', level: 'integer|null', isActive: 'boolean', deleted: 'boolean' }, sort: ['workspaceId', 'userId', 'id'] },
  sessions: { fields: { sessionRowId: 'id', userId: 'id', isActive: 'boolean', expiresAt: 'date|null', revoked: 'boolean' }, sort: ['userId', 'sessionRowId'] },
  personalKeys: { fields: { id: 'id', gatewayKeyId: 'id', orgId: 'id', userId: 'id', workspaceId: 'id|null', isActive: 'boolean', deleted: 'boolean', expiresAt: 'date|null', allowedScopes: 'set', authorityDigest: 'sha256' }, sort: ['orgId', 'userId', 'id'] },
  admissions: { fields: { orgId: 'id', workspaceId: 'id', product: 'string', served: 'boolean', admitted: 'boolean', phase: 'string', releasedParts: 'set', configDigest: 'sha256' }, sort: ['orgId', 'workspaceId', 'product'] },
  gatewayConfigs: { fields: { id: 'id', orgId: 'id', workspaceId: 'id|null', version: 'decimal', isActive: 'boolean', deleted: 'boolean', createdBy: 'id|null', payloadDigest: 'sha256' }, sort: ['orgId', 'version', 'id'] },
  oauthGrants: { fields: { id: 'id', orgId: 'id', serverId: 'id', scope: 'string', userId: 'id|null', kind: 'string', status: 'string', oauthIssuer: 'origin', version: 'decimal', grantRevision: 'decimal', sealed: 'boolean', hasRefresh: 'boolean', tokenExpiresAt: 'date|null', rotated: 'boolean', oauthRevokePending: 'boolean', oauthRevokeOutcome: 'string', gatewayPurgePending: 'boolean' }, sort: ['orgId', 'serverId', 'userId', 'id'] },
  credentialEpochs: { fields: { orgId: 'id', authorityId: 'id', replicaId: 'id', epoch: 'decimal' }, sort: ['orgId', 'authorityId', 'replicaId'] },
  channelAuthority: { fields: { channelId: 'id', kind: 'sse|terminal', orgId: 'id', workspaceId: 'id', userId: 'id', conversationId: 'id', runId: 'id', authorityId: 'id', issuedAt: 'date', expiresAt: 'date', revoked: 'boolean', closed: 'boolean' }, sort: ['orgId', 'kind', 'channelId'] },
  runAuthority: { fields: { runId: 'id', surface: 'string', orgId: 'id', workspaceId: 'id', userId: 'id', conversationId: 'id|null', authorityId: 'id', cancelRequested: 'boolean' }, sort: ['orgId', 'surface', 'runId'] },
};
const scopeFields: Fields = { orgId: 'id', userIds: 'id-set', workspaceIds: 'id-set', sessionRowIds: 'id-set', personalKeyIds: 'id-set', runIds: 'id-set', channelIds: 'id-set', grantIds: 'id-set', lmDigest: 'sha256', candidateHead: 'git-sha', unionDigest: 'sha256', phase: 'string', leg: 'L1|L2|L3|L4', armId: 'string', browser: 'chromium|firefox|webkit' };
export type Row = Record<string, any>; // Runtime exact-shape/type validator below closes this boundary.
export interface SiblingScope {
  orgId: string; userIds: string[]; workspaceIds: string[]; sessionRowIds: string[];
  personalKeyIds: string[]; runIds: string[]; channelIds: string[]; grantIds: string[];
  lmDigest: string; candidateHead: string; unionDigest: string; phase: string;
  leg: 'L1' | 'L2' | 'L3' | 'L4'; armId: string; browser: 'chromium' | 'firefox' | 'webkit';
}
export type Checkpoint = 'before' | 'during' | 'after';
export interface SiblingPort {
  scope: SiblingScope;
  // Independently reviewed LM predicates and complete source-mapped cardinality
  // inventory. An omitted group never means OFF. Existing durable rows remain
  // included even when a selected capability is absent.
  selected: Record<string, { required: boolean; absentReceiptDigest?: string }>;
  expectedProducts: readonly string[];
  expectedReplicaIds: readonly string[];
  read(checkpoint: Checkpoint): Promise<unknown>;
}
interface Snapshot {
  scope: SiblingScope; checkpoint: Checkpoint; sampledAt: string;
  protected: Record<string, Row[]>; activity: Row; bytes: string;
}
const cmp = (a: string, b: string) => Buffer.compare(Buffer.from(a), Buffer.from(b));
export function canonical(value: any): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value !== null && typeof value === 'object') return `{${Object.keys(value).sort(cmp).map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}`;
  return JSON.stringify(value);
}
const exact = (v: any, fields: readonly string[], id: string) => {
  check(!!v && typeof v === 'object' && !Array.isArray(v) &&
    Object.keys(v).length === fields.length && fields.every(k => Object.hasOwn(v, k)), id);
};
function normalize(value: any, kind: string, id: string): any {
  if (kind.endsWith('|null')) return value === null ? null : normalize(value, kind.slice(0, -5), id);
  if (kind === 'boolean') { check(typeof value === 'boolean', id); return value; }
  if (kind === 'integer') { check(Number.isSafeInteger(value), id); return value; }
  if (kind === 'set' || kind === 'id-set') {
    check(Array.isArray(value), id);
    const set = value.map(v => normalize(v, kind === 'id-set' ? 'id' : 'string', id)).sort(cmp);
    check(new Set(set).size === set.length, id); return set;
  }
  check(typeof value === 'string', id);
  if (kind === 'id') {
    check(/^[A-Za-z0-9_.:-]+$/.test(value), id);
    return /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(value) ? value.toLowerCase() : value;
  }
  if (kind === 'date') {
    const m = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?Z$/.exec(value);
    check(!!m && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 19) === m[1], id);
    return `${m[1]}.${(m[2] ?? '').padEnd(6, '0')}Z`;
  }
  if (kind === 'sha256') check(/^[a-f0-9]{64}$/.test(value), id);
  else if (kind === 'git-sha') check(/^[a-f0-9]{40}$/.test(value), id);
  else if (kind === 'decimal') check(/^(0|[1-9][0-9]*)$/.test(value), id);
  else if (kind === 'origin') {
    let u: URL | undefined; try { u = new URL(value); } catch { /* fixed failure */ }
    check(!!u && ['http:', 'https:'].includes(u.protocol) &&
      (['localhost', '127.0.0.1', '[::1]'].includes(u.hostname) || u.hostname.endsWith('.e2e-public.test')) &&
      !u.username && !u.password && u.origin === value, id);
  } else if (kind.includes('|')) check(kind.split('|').includes(value), id);
  else check(kind === 'string' && value.length > 0, id);
  return value;
}
function record(raw: any, fields: Fields, id: string): Row {
  exact(raw, Object.keys(fields), id);
  return Object.fromEntries(Object.entries(fields).map(([k, type]) => [k, normalize(raw[k], type, id)]));
}
function rows(raw: any, fields: Fields, sort: readonly string[], id: string): Row[] {
  check(Array.isArray(raw), id);
  const parsed = raw.map(v => record(v, fields, id));
  const key = (r: Row) => canonical(sort.map(k => r[k]));
  check(new Set(parsed.map(key)).size === parsed.length, id);
  return parsed.sort((a, b) => {
    for (const k of sort) {
      if (a[k] === b[k]) continue;
      if (a[k] === null) return -1;
      if (b[k] === null) return 1;
      if (fields[k] === 'decimal') return BigInt(a[k]) < BigInt(b[k]) ? -1 : 1;
      return cmp(a[k], b[k]);
    }
    return 0;
  });
}
const activityRows: Record<string, { fields: Fields; sort: string[] }> = {
  sessionChecks: { fields: { sessionRowId: 'id', ok: 'boolean', lastUsedAt: 'date|null' }, sort: ['sessionRowId'] },
  keyChecks: { fields: { keyId: 'id', ok: 'boolean', lastUsedAt: 'date|null' }, sort: ['keyId'] },
  runs: { fields: { runId: 'id', status: 'string', eventSeq: 'decimal', lastEventAt: 'date' }, sort: ['runId'] },
  terminals: { fields: { channelId: 'id', outputCursor: 'decimal', lastOutputAt: 'date' }, sort: ['channelId'] },
  oauth: { fields: { grantId: 'id', useSeq: 'decimal', successfulCalls: 'decimal', auditRows: 'decimal' }, sort: ['grantId'] },
};
const sameIds = (rs: Row[], field: string, ids: readonly string[]) =>
  canonical(rs.map(r => r[field]).sort(cmp)) === canonical([...ids].sort(cmp));
function snapshot(raw: any, port: SiblingPort, checkpoint: Checkpoint): Snapshot {
  exact(raw, ['schema', 'scope', 'checkpoint', 'sampledAt', 'authorityBindings', 'protected', 'activity'], 'L5-05.ENVELOPE');
  check(raw.schema === 'j04-active-sibling-projection/v1' && raw.checkpoint === checkpoint, 'L5-05.SCHEMA');
  const scope = record(raw.scope, scopeFields, 'L5-05.SCOPE') as SiblingScope;
  check(canonical(scope) === canonical(record(port.scope, scopeFields, 'L5-05.SCOPE')), 'L5-05.SCOPE');
  check(scope.userIds.length > 0 && scope.workspaceIds.length > 0 && scope.sessionRowIds.length > 0 &&
    scope.personalKeyIds.length > 0 && scope.runIds.length > 0, 'L5-05.ACTIVE_SCOPE');
  // Authority mapping/clock schema is a producer G5 gate, NOT fabricated here.
  check(!!raw.authorityBindings && Object.keys(raw.authorityBindings).length > 0, 'L5-05.AUTHORITY_BINDING');
  exact(raw.protected, Object.keys(groups), 'L5-05.GROUPS');
  exact(port.selected, Object.keys(groups), 'L5-05.SELECTION');
  const protectedRows: Record<string, Row[]> = {};
  for (const [name, definition] of Object.entries(groups)) {
    const id = `L5-05.${name}`;
    const selected = port.selected[name];
    check(typeof selected.required === 'boolean', id);
    if (!selected.required) normalize(selected.absentReceiptDigest, 'sha256', id);
    const group = rows(raw.protected[name], definition.fields, definition.sort, id);
    const always = ['users', 'orgMemberships', 'workspaces', 'workspaceMemberships', 'sessions', 'personalKeys', 'admissions', 'runAuthority'].includes(name);
    check(!always || selected.required, id);
    check(!(always || selected.required) || group.length > 0, id);
    for (const r of group) {
      if ('orgId' in r) check(r.orgId === scope.orgId, id);
      if ('workspaceId' in r && r.workspaceId !== null) check(scope.workspaceIds.includes(r.workspaceId), id);
      if ('userId' in r && r.userId !== null) check(scope.userIds.includes(r.userId), id);
      if (name === 'personalKeys') {
        const { authorityDigest, ...authority } = r;
        check(sha(canonical(authority)) === authorityDigest, id);
      }
    }
    protectedRows[name] = group;
  }
  for (const [group, field, ids] of [
    ['users', 'userId', scope.userIds], ['workspaces', 'id', scope.workspaceIds],
  ] as const) check(sameIds(protectedRows[group], field, ids), `L5-05.${group}`);
  // scope holds retained ACTIVE IDs; protected includes historical/inactive rows
  // too. Preserve the entire baseline ID set through canonical byte comparison.
  for (const [group, field, ids] of [
    ['sessions', 'sessionRowId', scope.sessionRowIds], ['personalKeys', 'id', scope.personalKeyIds],
    ['runAuthority', 'runId', scope.runIds], ['channelAuthority', 'channelId', scope.channelIds],
    ['oauthGrants', 'id', scope.grantIds],
  ] as const) check(ids.every(id => protectedRows[group].filter(r => r[field] === id).length === 1), `L5-05.${group}`);
  check(port.expectedProducts.length > 0 && protectedRows.admissions.length === scope.workspaceIds.length * port.expectedProducts.length &&
    scope.workspaceIds.every(w => port.expectedProducts.every(p => protectedRows.admissions.some(r => r.workspaceId === w && r.product === p))), 'L5-05.admissions');
  if (port.selected.credentialEpochs.required) {
    check(port.expectedReplicaIds.length >= 2 && new Set(port.expectedReplicaIds).size === port.expectedReplicaIds.length, 'L5-05.credentialEpochs');
    for (const authority of new Set(protectedRows.credentialEpochs.map(r => r.authorityId)))
      check(sameIds(protectedRows.credentialEpochs.filter(r => r.authorityId === authority), 'replicaId', port.expectedReplicaIds), 'L5-05.credentialEpochs');
  }
  exact(raw.activity, ['sampledAt', ...Object.keys(activityRows), 'pendingConnectCount'], 'L5-04.ACTIVITY_SHAPE');
  const activity: Row = { sampledAt: normalize(raw.activity.sampledAt, 'date', 'L5-04.CLOCK'),
    pendingConnectCount: normalize(raw.activity.pendingConnectCount, 'decimal', 'L5-04.CONNECTS') };
  for (const [name, d] of Object.entries(activityRows)) activity[name] = rows(raw.activity[name], d.fields, d.sort, 'L5-04.ACTIVITY_SHAPE');
  check(sameIds(activity.sessionChecks, 'sessionRowId', scope.sessionRowIds) && activity.sessionChecks.every((r: Row) => r.ok), 'L5-02.SESSION');
  check(sameIds(activity.keyChecks, 'keyId', scope.personalKeyIds) && activity.keyChecks.every((r: Row) => r.ok), 'L5-03.KEY');
  check(sameIds(activity.runs, 'runId', scope.runIds) && activity.runs.every((r: Row) =>
    // HR-CJ console-stop-fixture.ts StopRun.state: running | cancelled. Other
    // surfaces must map their reviewed source enum, not pass arbitrary strings.
    r.status === 'running'), 'L5-04.RUN');
  check(sameIds(activity.terminals, 'channelId', protectedRows.channelAuthority.filter(r =>
    r.kind === 'terminal' && scope.channelIds.includes(r.channelId)).map(r => r.channelId)), 'L5-04.TERMINAL');
  check(sameIds(activity.oauth, 'grantId', scope.grantIds) && activity.oauth.every((r: Row) =>
    BigInt(r.successfulCalls) > 0n && BigInt(r.auditRows) > 0n), 'L5-04.OAUTH');
  return { scope, checkpoint, sampledAt: normalize(raw.sampledAt, 'date', 'L5-05.CLOCK'),
    protected: protectedRows, activity, bytes: canonical(protectedRows) };
}
export async function beforeRevocation(port: SiblingPort): Promise<Snapshot> {
  return snapshot(structuredClone(await port.read('before')), port, 'before');
}
export async function observeDuringRevocation(port: SiblingPort, before: Snapshot): Promise<Snapshot> {
  const during = snapshot(structuredClone(await port.read('during')), port, 'during');
  assertProtectedStateUnchanged(before, during); return during;
}
export function assertProtectedStateUnchanged(before: Snapshot, next: Snapshot): void {
  check(canonical(before.scope) === canonical(next.scope), 'L5-06.BINDING');
  for (const group of Object.keys(groups)) check(canonical(before.protected[group]) === canonical(next.protected[group]), `L5-05.${group}`);
  check(before.bytes === next.bytes, 'L5-05.ALL_BYTES');
}
export async function afterRevocation(port: SiblingPort, before: Snapshot): Promise<Snapshot> {
  const after = snapshot(structuredClone(await port.read('after')), port, 'after');
  assertProtectedStateUnchanged(before, after);
  return after;
}
export interface ActionWindow {
  startedAt: number; completedAt: number;
  // Native mutation/fault checkpoint, distinct from later expiry/recovery reads.
  checkpointWindow?: { startedAt: number; completedAt: number };
}
type ActivitySnapshot = Pick<Snapshot, 'sampledAt' | 'activity'>;
// Shared with withSibling and the two-callback control. Never overwrite a sample
// with a later recovered observation or silently drop an action callback.
export async function collectDuringSamples<T>(read: () => Promise<T>,
  action: (sample: () => Promise<void>) => Promise<ActionWindow>): Promise<{ during: T[]; window: ActionWindow }> {
  const during: T[] = [];
  const window = await action(async () => { during.push(await read()); });
  check(during.length > 0, 'L5-04.MISSING_ACTION_WINDOW');
  return { during, window };
}
function assertConcurrentActivity(before: ActivitySnapshot, during: readonly ActivitySnapshot[], after: ActivitySnapshot, action: ActionWindow) {
  check(Number.isFinite(action.startedAt) && Number.isFinite(action.completedAt) &&
    action.startedAt > 0 && action.completedAt >= action.startedAt, 'L5-04.CONCURRENT_WINDOW');
  check(during.length > 0, 'L5-04.DURING_REQUIRED');
  const checkpoint = action.checkpointWindow ?? action;
  check(Number.isFinite(checkpoint.startedAt) && Number.isFinite(checkpoint.completedAt) &&
    checkpoint.startedAt >= action.startedAt && checkpoint.completedAt >= checkpoint.startedAt &&
    checkpoint.completedAt <= action.completedAt, 'L5-04.CHECKPOINT_WINDOW');
  // Both authority envelope AND independent activity clocks bracket the real
  // operation fence. First mutation evidence must fit its native operation,
  // not the much wider expiry/recovery window. Every later read is retained.
  for (const clock of [(s: ActivitySnapshot) => s.sampledAt, (s: ActivitySnapshot) => s.activity.sampledAt]) {
    check(Date.parse(clock(before)) <= action.startedAt && during.every(s =>
      Date.parse(clock(s)) >= action.startedAt && Date.parse(clock(s)) <= action.completedAt) &&
      Date.parse(clock(after)) >= action.completedAt, 'L5-04.CONCURRENT_WINDOW');
    check(Date.parse(clock(during[0])) >= checkpoint.startedAt &&
      Date.parse(clock(during[0])) <= checkpoint.completedAt, 'L5-04.CHECKPOINT_WINDOW');
  }
  const sequence = [before, ...during, after];
  for (const s of sequence) {
    check(Date.parse(s.activity.sampledAt) <= Date.parse(s.sampledAt), 'L5-04.ACTIVITY_CLOCK');
    for (const [name, time] of [['runs', 'lastEventAt'], ['terminals', 'lastOutputAt'],
      ['sessionChecks', 'lastUsedAt'], ['keyChecks', 'lastUsedAt']]) for (const r of s.activity[name]) {
      if (r[time] !== null) check(Date.parse(r[time]) <= Date.parse(s.activity.sampledAt), 'L5-04.ACTIVITY_CLOCK');
    }
  }
  for (let index = 1; index < sequence.length; index++) {
    const a = sequence[index - 1], b = sequence[index];
    check(Date.parse(a.sampledAt) <= Date.parse(b.sampledAt) &&
      Date.parse(a.activity.sampledAt) <= Date.parse(b.activity.sampledAt) &&
      a.activity.pendingConnectCount === b.activity.pendingConnectCount, 'L5-04.ACTIVITY_CLOCK');
    for (const name of ['sessionChecks', 'keyChecks']) for (const [i, r] of a.activity[name].entries()) {
      const s = b.activity[name][i];
      check(r.lastUsedAt === null || (s.lastUsedAt !== null && Date.parse(s.lastUsedAt) >= Date.parse(r.lastUsedAt)), 'L5-02.RETAINED_USE');
    }
    for (const [name, counter] of [['runs', 'eventSeq'], ['terminals', 'outputCursor'], ['oauth', 'useSeq']]) {
      // Strict progress by DURING is indispensable: a run paused throughout A's
      // operation and resumed before AFTER must turn this very oracle red.
      for (const [i, r] of a.activity[name].entries()) check(BigInt(b.activity[name][i][counter]) > BigInt(r[counter]), 'L5-04.PROGRESS');
    }
    for (const [i, r] of a.activity.oauth.entries()) check(
      BigInt(b.activity.oauth[i].successfulCalls) > BigInt(r.successfulCalls) &&
      BigInt(b.activity.oauth[i].auditRows) >= BigInt(r.auditRows), 'L5-04.OAUTH');
  }
  for (const [i, s] of during.entries()) {
    const bound = i === 0 ? checkpoint : action;
    for (const [name, time] of [['runs', 'lastEventAt'], ['terminals', 'lastOutputAt'],
      ['sessionChecks', 'lastUsedAt'], ['keyChecks', 'lastUsedAt']]) for (const r of s.activity[name]) {
      if (r[time] !== null) check(Date.parse(r[time]) >= bound.startedAt &&
        Date.parse(r[time]) <= bound.completedAt, 'L5-04.IN_WINDOW_ACTIVITY');
    }
  }
}
export interface SiblingWitness {
  schema: 'j04-sibling-consumer/v3'; leg: SiblingScope['leg']; armDigest: string;
  lmDigest: string; candidateHead: string; unionDigest: string; browser: string; phaseDigest: string;
  beforeDigest: string; duringDigest: string; afterDigest: string; groupCounts: Record<string, number>;
  beforeAt: number; duringAt: number; afterAt: number; actionStartedAt: number; actionCompletedAt: number;
  checkpointStartedAt: number; checkpointCompletedAt: number;
  observations: readonly { protectedDigest: string; activityDigest: string; at: number; activityAt: number }[];
  protectedEqual: boolean; retainedSession: boolean; retainedPersonalKey: boolean; concurrentRun: boolean;
}
// The exact on-disk child emitted by coreReceipt, not adapter-invented booleans.
export interface ChildOriginal extends SiblingWitness {
  samples: number; executed: boolean; sourceOnly: boolean; skipped: boolean; passed: boolean; assertionCount: number;
  browserRunDigest: string; browserContextDigest: string; originalDigest: string; nativeTestDigest: string;
  bodyPassed: boolean; fixturesPassed: boolean; nativeCompletionPending: boolean;
}
export async function finishRevocation(port: SiblingPort, before: Snapshot, during: readonly Snapshot[],
  action: ActionWindow): Promise<SiblingWitness> {
  const after = await afterRevocation(port, before);
  assertConcurrentActivity(before, during, after, action);
  const checkpoint = action.checkpointWindow ?? action;
  return { schema: 'j04-sibling-consumer/v3', lmDigest: before.scope.lmDigest,
    candidateHead: before.scope.candidateHead, unionDigest: before.scope.unionDigest,
    browser: before.scope.browser, phaseDigest: sha(before.scope.phase), armDigest: sha(before.scope.armId),
    leg: before.scope.leg, beforeDigest: sha(before.bytes), duringDigest: sha(during[0].bytes), afterDigest: sha(after.bytes),
    groupCounts: Object.fromEntries(Object.entries(before.protected).map(([g, rs]) => [g, rs.length])),
    beforeAt: Date.parse(before.sampledAt), duringAt: Date.parse(during[0].sampledAt), afterAt: Date.parse(after.sampledAt),
    actionStartedAt: action.startedAt, actionCompletedAt: action.completedAt,
    checkpointStartedAt: checkpoint.startedAt, checkpointCompletedAt: checkpoint.completedAt,
    observations: during.map(s => ({ protectedDigest: sha(s.bytes), activityDigest: sha(canonical(s.activity)),
      at: Date.parse(s.sampledAt), activityAt: Date.parse(s.activity.sampledAt) })),
    protectedEqual: [...during, after].every(s => before.bytes === s.bytes),
    retainedSession: [...during, after].every(s => canonical(s.scope.sessionRowIds) === canonical(before.scope.sessionRowIds)),
    retainedPersonalKey: [...during, after].every(s => canonical(s.scope.personalKeyIds) === canonical(before.scope.personalKeyIds)),
    // assertConcurrentActivity validated strict progress on EVERY consecutive pair.
    concurrentRun: before.activity.runs.length > 0,
  };
}

// Executable local controls use the SAME temporal oracle, never live receipts.
export async function siblingActivityControls(): Promise<void> {
  const time = (n: number) => new Date(n).toISOString();
  const sample = (at: number, sequence: number): ActivitySnapshot => ({ sampledAt: time(at), activity: {
    sampledAt: time(at), pendingConnectCount: '0',
    sessionChecks: [{ sessionRowId: 's', ok: true, lastUsedAt: time(at) }],
    keyChecks: [{ keyId: 'k', ok: true, lastUsedAt: time(at) }],
    runs: [{ runId: 'r', status: 'running', eventSeq: String(sequence), lastEventAt: time(at) }], terminals: [], oauth: [],
  } });
  const before = sample(1_000, 1), during = sample(2_000, 2), after = sample(4_000, 3);
  const window = { startedAt: 1_500, completedAt: 3_000 };
  const green = () => assertConcurrentActivity(before, [during], after, window);
  green();
  for (const [red, expected] of [
    [{ ...during, activity: { ...during.activity, sampledAt: time(1_000) } }, 'L5-04.CONCURRENT_WINDOW'],
    [{ ...during, activity: { ...during.activity, runs: [{ ...during.activity.runs[0], lastEventAt: time(1_000) }] } }, 'L5-04.IN_WINDOW_ACTIVITY'],
    [{ ...during, activity: { ...during.activity, runs: [{ ...during.activity.runs[0], eventSeq: '1' }] } }, 'L5-04.PROGRESS'],
  ] as const) {
    let killed = false;
    try { assertConcurrentActivity(before, [red], after, window); }
    catch (e) { killed = e instanceof ConsoleCoreFailure && e.assertion === expected; }
    check(killed, 'L5-04.CONTROL_MUST_KILL'); green();
  }
  // Generic two-callback mutation/expiry path used by withSibling, NOT evidence
  // that any real reconnect/restore producer has implemented its barrier.
  const sequenceControl = async (stallFirst: boolean, lateFirst = false) => {
    const first = sample(lateFirst ? 3_200 : 2_000, stallFirst ? 1 : 2);
    if (stallFirst) first.activity.runs[0].lastEventAt = before.activity.runs[0].lastEventAt;
    const recovery = sample(3_500, 3), final = sample(5_000, 4);
    const reads = [first, recovery];
    const collected = await collectDuringSamples(async () => reads.shift()!, async sampleDuring => {
      await sampleDuring(); // In the native mutation barrier.
      await sampleDuring(); // After a separate expiry/recovery wait.
      return { startedAt: 1_500, completedAt: 4_000,
        checkpointWindow: { startedAt: 1_500, completedAt: 2_500 } };
    });
    check(collected.during.length === 2 && reads.length === 0, 'L5-04.ALL_CALLBACKS_RETAINED');
    assertConcurrentActivity(before, collected.during, final, collected.window);
  };
  await sequenceControl(false);
  for (const [stallFirst, lateFirst, expected] of [
    [true, false, 'L5-04.PROGRESS'], [false, true, 'L5-04.CHECKPOINT_WINDOW'],
  ] as const) {
    let killed = false;
    try { await sequenceControl(stallFirst, lateFirst); }
    catch (e) { killed = e instanceof ConsoleCoreFailure && e.assertion === expected; }
    check(killed, 'L5-04.TWO_CALLBACK_CONTROL_MUST_KILL'); await sequenceControl(false);
  }
}

// Red-first fixture recipes: for EVERY declared group delete one required field,
// add an unknown field, duplicate its composite key, insert/delete a row, then
// independently mutate one protected value. Each must fail L5-05.<group> (shape
// or bytes), not setup. Restore the original sample AND run the unchanged oracle.
// Source mutants required separately: revoke retained session; rebind key owner;
// change membership/role; stop B run; bump one replica epoch; revoke channel while
// socket remains open. No post-hoc projection exclusion can hide the mutation.
export const SIBLING_GROUP_RED_CONTROLS = Object.freeze(Object.keys(groups).map(group => ({
  assertion: `L5-05.${group}`, fixture: 'omit-field/add-field/duplicate-key/delete-row/insert-row/change-protected-value',
})));
