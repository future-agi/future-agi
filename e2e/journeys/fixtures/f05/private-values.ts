import { requireScope, type MoneyScope } from './contract.ts';
import { ENTRY_COLUMNS, ACCOUNT_COLUMNS } from './private-plan.ts';
import { CREDIT_KINDS } from './statement.ts';
import { fail, integer, list, record, timestamp, uuid } from './validation.ts';

type Entry = Readonly<Record<keyof typeof ENTRY_COLUMNS, string | boolean | null>>;
const entries = new WeakSet<object>();
const MONEY = ['amount_micros', 'upstream_cost_micros', 'markup_micros'] as const;
const nullable = new Set(['upstream_cost_micros', 'markup_micros', 'markup_bps', 'workspace_id', 'related_entry_id']);
const lengths: Record<string, number> = { idempotency_key: 200, served_upstream_provider: 64,
  cost_source: 32, catalog_snapshot_id: 32, policy_version: 64, agent_run_id: 128,
  agent_id: 128, source_kind: 32, price_catalog_version: 32 };
function text(value: unknown, max = 255): string {
  // Synthetic test-owned identifiers only; no arbitrary bodies, notes or JSON.
  if (typeof value !== 'string' || value.length > max || !/^[a-zA-Z0-9_.:/-]*$/.test(value)) fail('PRIVATE-TEXT');
  return value;
}

/** Normalization is a schema oracle, NOT proof rows came from a database.
 * Only capturePrivateLedger attaches actual transaction metadata. Never feed
 * wallet JSON here or relabel a public observation as an integer private row. */
export function decodePrivateEntry(scope: MoneyScope, raw: unknown): Entry {
  requireScope(scope);
  const r = record(raw, Object.keys(ENTRY_COLUMNS));
  const out: Record<string, string | boolean | null> = {};
  for (const [name, type] of Object.entries(ENTRY_COLUMNS)) {
    const v = r[name];
    if (v === null && nullable.has(name)) { out[name] = null; continue; }
    if (type === 'uuid') out[name] = uuid(v);
    else if (type === 'int8' || type === 'int4') {
      const n = integer(v, name !== 'amount_micros');
      if (type === 'int4' && n > 2147483647n) fail('AMOUNT');
      out[name] = n.toString();
    } else if (type === 'timestamptz') out[name] = timestamp(v);
    else if (type === 'bool') {
      if (typeof v !== 'boolean') fail('PRIVATE-TYPE');
      out[name] = v;
    } else out[name] = text(v, lengths[name] ?? 255);
  }
  if (out.organization_id !== scope.organizationId) fail('PRIVATE-TENANT');
  if (!out.idempotency_key) fail('PRIVATE-IDENTITY');
  if (![...CREDIT_KINDS, 'spend', 'usage_debit'].includes(out.kind as string)) fail('ENTRY-KIND');
  const amount = integer(out.amount_micros);
  if ((['spend', 'usage_debit', 'topup_reversal', 'dispute_hold'].includes(out.kind as string) && amount > 0n)
    || (['topup', 'grant', 'refund', 'dispute_release'].includes(out.kind as string) && amount < 0n)) fail('ENTRY-SIGN');
  // Stored IDs are historical facts. Blank stays blank, never resolved through
  // today's mutable OrgApiKey binding; person_id does not exist on this row.
  const result = Object.freeze(out) as Entry;
  entries.add(result);
  return result;
}
function entry(value: unknown): Entry {
  if (!value || typeof value !== 'object' || !entries.has(value)) fail('PRIVATE-EXACT-SOURCE');
  return value as Entry;
}
export function exactPrivateMoney(value: unknown, field: unknown): bigint | null {
  const r = entry(value);
  if (typeof field !== 'string' || !(MONEY as readonly string[]).includes(field)) fail('PRIVATE-FIELD-UNMAPPED');
  const amount = r[field as typeof MONEY[number]];
  return amount === null ? null : integer(amount);
}
export function decodePrivateAccount(scope: MoneyScope, rows: unknown) {
  requireScope(scope);
  const all = list(rows, 2); if (all.length !== 1) fail('ACCOUNT-CARDINALITY');
  const r = record(all[0], Object.keys(ACCOUNT_COLUMNS));
  if (uuid(r.organization_id) !== scope.organizationId) fail('ACCOUNT-TENANT');
  const out: Record<string, string | null> = { organization_id: scope.organizationId };
  for (const [name, type] of Object.entries(ACCOUNT_COLUMNS)) {
    if (type === 'int8') out[name] = integer(r[name], name !== 'granted_total_micros').toString();
    else if (type === 'timestamptz') out[name] = r[name] === null && name === 'gateway_push_state_at' ? null : timestamp(r[name]);
  }
  if (integer(out.gateway_acked_version) > integer(out.version)) fail('ACCOUNT-VERSION');
  if (!['', 'ok', 'na_managed_off'].includes(r.gateway_push_state as string)) fail('ACCOUNT-PUSH');
  out.gateway_push_state = r.gateway_push_state as string;
  return Object.freeze(out);
}

const REPLAY = Object.freeze({ billed_micros: 'amount_micros', upstream_micros: 'upstream_cost_micros',
  markup_micros: 'markup_micros', markup_bps: 'markup_bps', request_id: 'request_id',
  idempotency_key: 'idempotency_key', provider: 'provider', served_upstream_provider: 'served_upstream_provider',
  cost_source: 'cost_source', catalog_snapshot_id: 'catalog_snapshot_id', policy_version: 'policy_version',
  api_key_id: 'api_key_id', agent_run_id: 'agent_run_id', agent_id: 'agent_id', model: 'model',
  usage_estimated: 'usage_estimated' } as const);

/** The product _write replay checks org/kind, not all payload fields. This
 * oracle MUST flag differing replay payloads even if the worker ACKs them. */
export function assertReplayPayload(value: unknown, payload: unknown): void {
  const r = entry(value), p = record(payload, Object.keys(REPLAY));
  if (r.kind !== 'spend') fail('REPLAY-MISMATCH');
  for (const [wire, column] of Object.entries(REPLAY)) {
    const expected = wire === 'billed_micros' ? (-integer(r.amount_micros)).toString() : r[column];
    if (p[wire] !== expected) fail('REPLAY-MISMATCH');
  }
}
function cohort(raw: unknown): Entry[] {
  const all = list(raw).map(entry), ids = new Set<string>(), keys = new Set<string>();
  for (const r of all) {
    if (ids.has(r.id as string) || keys.has(r.idempotency_key as string)) fail('PRIVATE-DUPLICATE');
    ids.add(r.id as string); keys.add(r.idempotency_key as string);
  }
  return all;
}
export function assertIdempotentReplay(before: unknown, after: unknown): void {
  const a = cohort(before), b = cohort(after);
  if (!a.length || a.length !== b.length) fail('REPLAY-MISMATCH');
  const rows = new Map(b.map(r => [r.id, r]));
  for (const r of a) {
    const other = rows.get(r.id);
    if (!other || Object.keys(ENTRY_COLUMNS).some(k => r[k as keyof Entry] !== other[k as keyof Entry])) fail('REPLAY-MISMATCH');
  }
}

function displayMicros(value: bigint, places: number): string {
  const divisor = 10n ** BigInt(6 - places), negative = value < 0n;
  const magnitude = negative ? -value : value;
  const rounded = (magnitude + divisor / 2n) / divisor;
  const digits = rounded.toString().padStart(places + 1, '0');
  const unsigned = places ? `${digits.slice(0, -places)}.${digits.slice(-places)}` : digits;
  return negative && rounded !== 0n ? `-${unsigned}` : unsigned;
}
function displayFloat(value: unknown, places: number): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || Math.abs(value) >= 1e21) fail('PUBLIC-DISPLAY');
  const formatter = new Intl.NumberFormat('en-US', { useGrouping: false,
    minimumFractionDigits: places, maximumFractionDigits: places });
  const result = formatter.format(value);
  return /^-0(?:\.0+)?$/.test(result) ? result.slice(1) : result;
}
/** Display equality ONLY. This never turns a public float into exact money. */
export function assertMoneyDisplay(exact: bigint, rendered: unknown, places: number): void {
  if (!Number.isInteger(places) || places < 0 || places > 6) fail('DISPLAY-PRECISION');
  if (displayFloat(rendered, places) !== displayMicros(exact, places)) fail('PUBLIC-DISPLAY');
}
export function assertWalletDisplay(privateRows: unknown, publicRows: unknown, places: number) {
  if (!Number.isInteger(places) || places < 0 || places > 6) fail('DISPLAY-PRECISION');
  const rows = cohort(privateRows), pub = list(publicRows);
  if (rows.length !== pub.length) fail('DISPLAY-COHORT');
  const byId = new Map(rows.map(r => [r.id, r])), seen = new Set<string>();
  for (const raw of pub) {
    // Safe wallet subset only; caller must discard unsafe fields at the emitter.
    const p = record(raw, ['id', 'request_id', 'kind', 'amount_usd', 'markup_usd']);
    const id = uuid(p.id), r = byId.get(id);
    if (!r || seen.has(id) || p.request_id !== r.request_id || p.kind !== r.kind) fail('DISPLAY-COHORT');
    seen.add(id);
    for (const [publicField, privateField] of [['amount_usd', 'amount_micros'], ['markup_usd', 'markup_micros']] as const) {
      const exact = exactPrivateMoney(r, privateField), rendered = p[publicField];
      if (exact === null || rendered === null) {
        if (exact !== null || rendered !== null) fail('PUBLIC-DISPLAY');
      } else assertMoneyDisplay(exact, rendered, places);
    }
  }
  return Object.freeze({ authority: 'DISPLAY_ONLY', moneyReconciled: false, places, entryCount: rows.length });
}
