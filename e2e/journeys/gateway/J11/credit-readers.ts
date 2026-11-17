import type { ApiClient } from '../../../lib/api-client';
import { requireId, requireUuid } from '../_shared/identifiers';

// Source pin: foundation 7543e2d9 / product base da4c81cc.
// Endpoints: futureagi/agentcc/urls_billing.py; frontend/src/utils/axios.js.
// Bodies: agentcc/serializers/managed_credits.py:{ManagedCreditEntrySerializer,
// ManagedCreditEntriesPageSerializer,ManagedCreditStatementSerializer}.
// This is the public API projection, NOT an authoritative private-ledger probe.
const ENTRIES = '/agentcc/managed-credits/entries/';
const STATEMENT = '/agentcc/managed-credits/statement/';
type Reader = Pick<ApiClient, 'get' | 'baseURL'>;
export const CREDIT_KINDS = [
  'topup', 'grant', 'refund', 'adjustment', 'expiry', 'topup_reversal',
  'dispute_hold', 'dispute_release',
] as const;
export type CreditKind = typeof CREDIT_KINDS[number];
export type EntryKind = CreditKind | 'spend' | 'usage_debit';
export interface CreditEntry {
  id: string; occurred_at: string; kind: EntryKind; amount_micros: string;
  request_id: string; model: string; provider: string; markup_micros: string | null;
  markup_bps: number | null; cost_source: string; stripe_ref_present: boolean;
}
export interface CreditStatement {
  month: string; currency: 'USD'; opening_micros: string; closing_micros: string;
  credits_by_kind: Record<CreditKind, string>;
  spend_by_model: { model: string; count: number; spend_micros: string;
    markup_micros: string; estimated: boolean }[];
}

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('J11: missing credit object');
  }
  return value as Record<string, unknown>;
}
function text(value: unknown): string {
  if (typeof value !== 'string') throw new Error('J11: missing credit string');
  return value;
}
function flag(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new Error('J11: missing credit boolean');
  return value;
}
function integer(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value)) {
    throw new Error('J11: unsafe credit integer');
  }
  return value;
}
function exactFields(value: Record<string, unknown>, fields: readonly string[]): void {
  const actual = Object.keys(value).sort();
  if (JSON.stringify(actual) !== JSON.stringify([...fields].sort())) {
    throw new Error('J11: credit projection contract changed');
  }
}
function local(api: Reader): void {
  const url = new URL(api.baseURL);
  if (!['http:', 'https:'].includes(url.protocol) ||
      !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname) || url.username || url.password) {
    throw new Error('J11 credit reads require the admitted local API');
  }
  // This is not an egress guard or authorization. The accepted runner must
  // cover redirects, sockets, app endpoints and every browser context (F17).
}
function result(body: unknown): Record<string, unknown> {
  const envelope = object(body);
  if (envelope.status !== true) throw new Error('J11: credit read did not succeed');
  return object(envelope.result);
}

/** Exact decimal conversion, never a tolerance or Math.round on money.
 * Public UsdField emits JSON numbers with <=6 decimal places. Restrict numeric
 * inputs to <=1 million USD: well below the point where a double loses micros.
 * PG decimal strings remain lossless. A sub-micro amount is a defect, not zero.
 */
export function usdToMicros(value: unknown): string {
  if (typeof value !== 'number' && typeof value !== 'string') {
    throw new Error('J11: missing USD amount');
  }
  if (typeof value === 'number' && (!Number.isFinite(value) || Math.abs(value) > 1_000_000)) {
    throw new Error('J11: amount requires a lossless wire reader');
  }
  const match = /^(-?)(\d{1,24})(?:\.(\d{1,24}))?(?:[eE]([+-]?\d{1,2}))?$/.exec(String(value));
  if (!match) throw new Error('J11: malformed USD amount');
  const fraction = match[3] ?? '';
  const shift = 6 + Number(match[4] ?? 0) - fraction.length;
  const coefficient = BigInt(match[2] + fraction);
  let amount: bigint;
  if (shift >= 0) amount = coefficient * 10n ** BigInt(shift);
  else {
    const divisor = 10n ** BigInt(-shift);
    if (coefficient % divisor !== 0n) throw new Error('J11: fractional micro is not accepted');
    amount = coefficient / divisor;
  }
  return String(match[1] ? -amount : amount);
}
function apiUsd(value: unknown): string {
  if (typeof value !== 'number') throw new Error('J11: UsdField must emit a JSON number');
  return usdToMicros(value);
}

function entry(value: unknown): CreditEntry {
  const row = object(value);
  exactFields(row, ['id', 'occurred_at', 'kind', 'amount_usd', 'request_id', 'model',
    'provider', 'markup_usd', 'markup_bps', 'cost_source', 'stripe_ref']);
  const kind = text(row.kind);
  if (![...CREDIT_KINDS, 'spend', 'usage_debit'].includes(kind)) {
    throw new Error('J11: unknown ledger entry kind');
  }
  const occurred = text(row.occurred_at);
  if (!Number.isFinite(Date.parse(occurred))) throw new Error('J11: malformed entry time');
  const requestId = text(row.request_id);
  if (requestId) requireId(requestId);
  return {
    id: requireUuid(text(row.id)), occurred_at: occurred, kind: kind as EntryKind,
    amount_micros: apiUsd(row.amount_usd), request_id: requestId,
    model: text(row.model), provider: text(row.provider),
    markup_micros: row.markup_usd === null ? null : apiUsd(row.markup_usd),
    markup_bps: row.markup_bps === null ? null : integer(row.markup_bps),
    cost_source: text(row.cost_source), stripe_ref_present: text(row.stripe_ref) !== '',
  };
}

/** Whole fresh-org inventory, with multiplicity retained; never fetch only
 * expected IDs. Public view ManagedCreditEntriesView pins limit=200 maximum
 * and next_cursor=null termination. Repeated cursors/oversize inventories fail.
 * No raw response, stripe_ref, cursor or session identifier is returned/logged.
 */
export async function readCreditEntries(api: Reader): Promise<CreditEntry[]> {
  local(api);
  const rows: CreditEntry[] = [];
  const cursors = new Set<string>();
  let cursor: string | null = null;
  for (let page = 0; page < 20; page += 1) {
    const params: Record<string, string | number> = { limit: 200 };
    if (cursor !== null) params.cursor = cursor;
    const body = result(await api.get<unknown>(ENTRIES, params));
    exactFields(body, ['entries', 'next_cursor']);
    if (!Array.isArray(body.entries) || body.entries.length > 200) {
      throw new Error('J11: invalid credit page');
    }
    rows.push(...body.entries.map(entry));
    if (body.next_cursor === null) return rows;
    cursor = text(body.next_cursor);
    if (!cursor || cursors.has(cursor) || body.entries.length === 0) {
      throw new Error('J11: non-progressing credit pagination');
    }
    cursors.add(cursor);
  }
  throw new Error('J11: credit inventory exceeded its explicit fixture bound');
}

export async function readCreditStatement(api: Reader, month: string): Promise<CreditStatement> {
  local(api);
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month) ||
      Number(month.slice(0, 4)) < 1 || Number(month.slice(0, 4)) > 9998) {
    throw new Error('J11: explicit UTC statement month required');
  }
  const body = result(await api.get<unknown>(STATEMENT, { month }));
  exactFields(body, ['month', 'currency', 'opening_balance', 'credits_by_kind',
    'spend_by_model', 'closing_balance']);
  if (body.month !== month || body.currency !== 'USD') throw new Error('J11: wrong statement identity');
  const credits = object(body.credits_by_kind);
  exactFields(credits, CREDIT_KINDS);
  if (!Array.isArray(body.spend_by_model)) throw new Error('J11: missing statement spend lines');
  return {
    month, currency: 'USD', opening_micros: apiUsd(body.opening_balance),
    closing_micros: apiUsd(body.closing_balance),
    credits_by_kind: Object.fromEntries(CREDIT_KINDS.map(kind => [kind, apiUsd(credits[kind])])) as
      Record<CreditKind, string>,
    spend_by_model: body.spend_by_model.map(value => {
      const line = object(value);
      exactFields(line, ['model', 'count', 'spend_usd', 'markup_usd', 'estimated']);
      const count = integer(line.count);
      if (count <= 0) throw new Error('J11: empty statement spend line');
      return { model: text(line.model), count, spend_micros: apiUsd(line.spend_usd),
        markup_micros: apiUsd(line.markup_usd), estimated: flag(line.estimated) };
    }),
  };
}
