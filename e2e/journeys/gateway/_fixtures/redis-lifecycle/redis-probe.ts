import { REDIS_READ_SCRIPT } from './redis-script';

export interface RedisSelection {
  organizationId: string;
  holdIds: readonly string[];
  scopeIds: readonly string[];
}
export function ensure(ok: unknown, code: string): asserts ok {
  if (!ok) throw new Error(code); // Fixed diagnostics; never echo raw values.
}
export function syntheticId(value: unknown): string {
  ensure(typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/.test(value), 'F02.id');
  return value;
}
function cohort(values: readonly string[], min: number, max: number) {
  ensure(Array.isArray(values) && values.length >= min && values.length <= max &&
    new Set(values).size === values.length, 'F02.cohort');
  return values.map(syntheticId);
}
function selection(input: RedisSelection) {
  ensure(typeof input.organizationId === 'string' &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(input.organizationId), 'F02.org');
  return { organizationId: input.organizationId,
    holdIds: cohort(input.holdIds, 1, 256), scopeIds: cohort(input.scopeIds, 0, 32) };
}

/** Pure query compiler, not a Redis executor. Key/field provenance is recorded
 * beside REDIS_READ_SCRIPT. Prefix MUST be projected from effective config by
 * runner, never defaulted or read from a secret-bearing environment/config dump.
 */
export function compileRedisRead(input: RedisSelection, effectiveAgentBudgetPrefix: string) {
  const s = selection(input);
  ensure(typeof effectiveAgentBudgetPrefix === 'string' &&
    /^(?:[A-Za-z0-9_.:-]{1,96})?$/.test(effectiveAgentBudgetPrefix), 'F02.prefix');
  const base = `mc:{${s.organizationId}}:`;
  return {
    command: 'EVAL_RO' as const, script: REDIS_READ_SCRIPT,
    keys: [...['acct', 'holds', 'holdexp', 'outbox'].map(k => base + k),
      ...s.holdIds.map(id => `${base}settled:${id}`),
      ...s.scopeIds.flatMap(scope => ['limit', 'spent', 'held', 'holds', 'holdexp', 'stopped']
        .map(k => `${effectiveAgentBudgetPrefix}abud:{${s.organizationId}}:${scope}:${k}`))],
    argv: [String(s.holdIds.length), String(s.scopeIds.length), ...s.holdIds, ...s.scopeIds],
  };
}
export type RedisReadPlan = ReturnType<typeof compileRedisRead>;

function array(raw: unknown, length?: number): unknown[] {
  ensure(Array.isArray(raw) && (length === undefined || raw.length === length), 'F02.shape');
  return raw;
}
function integer(raw: unknown, min = 0n): string {
  ensure(typeof raw === 'string' && /^-?(0|[1-9][0-9]*)$/.test(raw) && raw !== '-0', 'F02.integer');
  const value = BigInt(raw);
  ensure(value >= min && value <= 9223372036854775807n, 'F02.integer');
  return raw;
}
function optionalInteger(raw: unknown, min = 0n): string | null {
  return raw === null ? null : integer(raw, min);
}
function count(raw: unknown, min = 0): number {
  ensure(typeof raw === 'number' && Number.isSafeInteger(raw) && raw >= min, 'F02.counter');
  return raw;
}
function bit(raw: unknown): boolean {
  ensure(raw === 0 || raw === 1, 'F02.bit');
  return raw === 1;
}
function pttl(raw: unknown): number { return count(raw, -2); }
function pairs(raw: unknown): Map<string, string> {
  const values = array(raw);
  ensure(values.length <= 512 && values.length % 2 === 0, 'F02.inventory-limit');
  const result = new Map<string, string>();
  for (let i = 0; i < values.length; i += 2) {
    const id = syntheticId(values[i]);
    ensure(!result.has(id), 'F02.duplicate');
    result.set(id, integer(values[i + 1]));
  }
  return result;
}
function holds(rawHolds: unknown, rawExpiries: unknown) {
  const amounts = pairs(rawHolds);
  const expiries = pairs(rawExpiries);
  ensure(amounts.size === expiries.size && [...amounts.keys()].every(id => expiries.has(id)), 'F02.hold-expiry');
  // Return ALL holds for this synthetic org/scope, not just the selected cohort.
  // Extra holds remain visible and must fail the caller's exact-set assertion.
  return [...amounts].sort(([a], [b]) => a.localeCompare(b)).map(([holdId, micros]) =>
    ({ holdId, micros, expiresAtMs: expiries.get(holdId)! }));
}
function outbox(raw: unknown) {
  const [l, g, p, lagRaw] = array(raw, 4);
  const retained = count(l);
  const groupPresent = bit(g);
  if (!groupPresent) {
    ensure(p === null && lagRaw === null, 'F02.group');
    return { retained, groupPresent, pending: null, lag: null, backlog: retained };
  }
  const pending = count(p);
  // Redis can report unknown lag (null or -1); never report this as drained.
  const lag = lagRaw === null || lagRaw === -1 ? null : count(lagRaw);
  const backlog = lag === null ? null : count(pending + lag);
  return { retained, groupPresent, pending, lag, backlog };
}

/** RESP2 contract: bulk strings remain strings, nil is null, integers are safe
 * JS integers (adapter refuses overflow), no Buffer/object coercion. Not a PG
 * settlement ledger; an empty outbox backlog is NOT exactly-once money proof.
 */
export function decodeRedisRead(input: RedisSelection, raw: unknown) {
  const s = selection(input);
  const [acctRaw, h, e, m, o, sc] = array(raw, 6);
  const a = array(acctRaw, 4);
  ensure(a.every(v => v === null) || a.every(v => v !== null), 'F02.account');
  const account = a[0] === null ? null : {
    grantedMicros: integer(a[0]), grantedVersion: integer(a[1]),
    spentMicros: integer(a[2]), heldMicros: integer(a[3]),
  };
  const markersRaw = array(m);
  ensure(markersRaw.length === s.holdIds.length, 'F02.markers');
  const markers = markersRaw.map((rawMarker, i) => {
    const [id, value, ttl] = array(rawMarker, 3);
    ensure(id === s.holdIds[i] && (value === null || value === '1'), 'F02.marker');
    const pttlMs = pttl(ttl);
    ensure(value === null ? pttlMs === -2 : pttlMs >= 0, 'F02.marker');
    return { holdId: s.holdIds[i], value: value as '1' | null, pttlMs };
  });
  const scopesRaw = array(sc);
  ensure(scopesRaw.length === s.scopeIds.length, 'F02.scopes');
  const scopes = scopesRaw.map((rawScope, i) => {
    const [id, limit, spent, held, sh, se, stopped, ttlsRaw] = array(rawScope, 8);
    ensure(id === s.scopeIds[i], 'F02.scopes');
    const scopeHolds = holds(sh, se);
    const ttls = array(ttlsRaw, 6).map(pttl);
    const stoppedPresent = bit(stopped);
    ensure(stoppedPresent ? ttls[5] >= -1 : ttls[5] === -2, 'F02.scope-ttl');
    for (const [value, ttl] of [[limit, ttls[0]], [spent, ttls[1]], [held, ttls[2]]] as const) {
      ensure(value === null ? ttl === -2 : ttl >= -1, 'F02.scope-ttl');
    }
    return { scopeId: s.scopeIds[i], limitMicros: optionalInteger(limit, -1n),
      spentMicros: optionalInteger(spent), heldMicros: optionalInteger(held),
      holds: scopeHolds, outstandingHoldCount: scopeHolds.length,
      stopped: stoppedPresent, pttlMs: ttls,
      // 'stopped' is a guardrail marker, NOT a scope-close witness.
      closedState: 'UNSUPPORTED_AT_SOURCE_PIN' as const };
  });
  return { organizationId: s.organizationId, account, holds: holds(h, e),
    markers, outbox: outbox(o), scopes,
    credentialEpochs: 'UNSUPPORTED_AT_SOURCE_PIN' as const };
}
export type RedisState = ReturnType<typeof decodeRedisRead>;
