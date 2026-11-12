// Public managed-credit projections at 18519f182cee7c61ef70b959d920bd710e0a6841.
// serializers/managed_credits.py columns; views/managed_credits.py statement grouping.
import type { ScopeActor, ScopeActors } from '../scope-actors';
import type { StateProbe } from '../state-probe';
import { E2E } from '../env';
import { usdToMicros, type EntryRow } from './money';
export const ENTRY_CSV_COLUMNS = [
  'occurred_at',
  'kind',
  'amount_usd',
  'request_id',
  'model',
  'provider',
  'markup_usd',
  'markup_bps',
  'cost_source',
  'stripe_ref',
] as const;
export const STATEMENT_CSV_COLUMNS = ['line', 'key', 'count', 'amount_usd', 'markup_usd', 'estimated'] as const;
/** serializers/managed_credits.py CREDIT_KINDS, the statement's credit lines in order. */
export const CREDIT_KINDS = [
  'topup',
  'grant',
  'refund',
  'adjustment',
  'expiry',
  'topup_reversal',
  'dispute_hold',
  'dispute_release',
] as const;

/** RFC 4180 rows (quoted cells, doubled quotes), header first. */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ',') {
      row.push(cell);
      cell = '';
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++;
      row.push(cell);
      rows.push(row);
      row = [];
      cell = '';
    } else cell += ch;
  }
  if (cell !== '' || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows;
}

export function usd6(value: bigint | string | number): string {
  const micros = BigInt(value);
  const abs = micros < 0n ? -micros : micros;
  return `${micros < 0n ? '-' : ''}${abs / 1_000_000n}.${String(abs % 1_000_000n).padStart(6, '0')}`;
}
export async function csvAs(scopes: ScopeActors, actor: ScopeActor, path: string, query: string) {
  const url = new URL(`${path}?${query}`, E2E.apiUrl);
  if (!['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)) throw new Error('CSV reads require the local API');
  const res = await fetch(url, { headers: scopes.headers(actor), redirect: 'error' });
  return { status: res.status, rows: parseCsv(await res.text()) };
}
export function exactFields(value: object, fields: readonly string[]): void {
  if (JSON.stringify(Object.keys(value).sort()) !== JSON.stringify([...fields].sort())) {
    throw new Error('money walk: public projection field set changed');
  }
}
/** The four additive fields in entry_values at the pinned serializer. */
const ENTRY_IDENTITY_FIELDS = ['org_id', 'api_key_id', 'agent_run_id', 'agent_id'] as const;
export interface EntryIdentity {
  id: string;
  org_id: string;
  api_key_id: string | null;
  agent_run_id: string | null;
  /** The pinned serializer always emits null; G08 mapping is not implemented. */
  agent_id: null;
}
export interface EntryInventory {
  orgId: string;
  entries: readonly EntryIdentity[];
}
/** Independent, org-scoped storage identity oracle; never inferred from API rows.
 * JSON extraction matches the serializer's getattr fallback on older schemas;
 * only these identifiers are returned, never key material or internal columns. */
export const entryIdentities = (probe: StateProbe, org: string) =>
  probe.pg<EntryIdentity>(
    `SELECT id::text, organization_id::text AS org_id,
    NULLIF(to_jsonb(e)->>'api_key_id', '') AS api_key_id,
    NULLIF(to_jsonb(e)->>'agent_run_id', '') AS agent_run_id, NULL::text AS agent_id
    FROM usage_managedcreditentry e WHERE organization_id = $1 ORDER BY id`,
    [org],
  );
export interface ApiEntry extends EntryIdentity {
  occurred_at: string;
  kind: string;
  amount_usd: number;
  request_id: string;
  model: string;
  provider: string;
  markup_usd: number | null;
  markup_bps: number | null;
  cost_source: string;
  stripe_ref: string;
}
export function entriesFromApiShape(
  value: { entries: ApiEntry[]; next_cursor: string | null },
  expected?: EntryInventory,
): ApiEntry[] {
  exactFields(value, ['entries', 'next_cursor']);
  if (!Array.isArray(value.entries) || value.next_cursor !== null || value.entries.length > 200) {
    throw new Error('money walk: fresh-org inventory exceeded one complete page');
  }
  // Optional in the signature for existing negative-shape callers, never at runtime.
  if (!expected || typeof expected.orgId !== 'string' || !expected.orgId || !Array.isArray(expected.entries)) {
    throw new Error('money walk: independent entry identity inventory is required');
  }
  const identities = new Map(expected.entries.map((entry) => [entry.id, entry]));
  if (identities.size !== expected.entries.length || value.entries.length !== identities.size) {
    throw new Error('money walk: entry identity inventory changed');
  }
  const seen = new Set<string>();
  for (const row of value.entries) {
    exactFields(row, ['id', ...ENTRY_CSV_COLUMNS, ...ENTRY_IDENTITY_FIELDS]);
    // ManagedCreditEntrySerializer :485-514; UsdField :160-165 emits numbers,
    // not numeric strings. Check every wire type before money/CSV normalization
    // or identity comparison can conceal an equivalent-value type regression.
    if (
      typeof row.id !== 'string' ||
      typeof row.org_id !== 'string' ||
      (row.api_key_id !== null && typeof row.api_key_id !== 'string') ||
      (row.agent_run_id !== null && typeof row.agent_run_id !== 'string') ||
      row.agent_id !== null ||
      typeof row.occurred_at !== 'string' ||
      typeof row.kind !== 'string' ||
      typeof row.amount_usd !== 'number' || !Number.isFinite(row.amount_usd) ||
      typeof row.request_id !== 'string' ||
      typeof row.model !== 'string' ||
      typeof row.provider !== 'string' ||
      (row.markup_usd !== null && (typeof row.markup_usd !== 'number' || !Number.isFinite(row.markup_usd))) ||
      (row.markup_bps !== null && (typeof row.markup_bps !== 'number' || !Number.isInteger(row.markup_bps))) ||
      typeof row.cost_source !== 'string' ||
      typeof row.stripe_ref !== 'string'
    ) {
      throw new Error('money walk: entry wire type changed');
    }
    usdToMicros(row.amount_usd);
    if (row.markup_usd !== null) usdToMicros(row.markup_usd);
    const identity = identities.get(row.id);
    if (
      !identity || seen.has(row.id) ||
      typeof row.org_id !== 'string' || row.org_id !== expected.orgId || identity.org_id !== expected.orgId ||
      (row.api_key_id !== null && (typeof row.api_key_id !== 'string' || !row.api_key_id)) ||
      (row.agent_run_id !== null && (typeof row.agent_run_id !== 'string' || !row.agent_run_id)) ||
      row.agent_id !== null ||
      ENTRY_IDENTITY_FIELDS.some((field) => row[field] !== identity[field])
    ) {
      throw new Error('money walk: entry attribution changed');
    }
    seen.add(row.id);
  }
  return value.entries;
}
export interface Statement {
  month: string;
  currency: string;
  opening_balance: number;
  closing_balance: number;
  credits_by_kind: Record<string, number>;
  spend_by_model: { model: string; count: number; spend_usd: number; markup_usd: number; estimated: boolean }[];
}
export function statementFromApiShape(value: Statement) {
  exactFields(value, ['month', 'currency', 'opening_balance', 'closing_balance', 'credits_by_kind', 'spend_by_model']);
  exactFields(value.credits_by_kind, CREDIT_KINDS);
  if (value.currency !== 'USD') throw new Error('money walk: currency changed');
  return {
    month: value.month,
    currency: value.currency,
    opening_balance: usdToMicros(value.opening_balance),
    closing_balance: usdToMicros(value.closing_balance),
    credits_by_kind: Object.fromEntries(CREDIT_KINDS.map((kind) => [kind, usdToMicros(value.credits_by_kind[kind])])),
    spend_by_model: value.spend_by_model.map((line) => {
      exactFields(line, ['model', 'count', 'spend_usd', 'markup_usd', 'estimated']);
      if (!Number.isSafeInteger(line.count) || line.count <= 0 || typeof line.estimated !== 'boolean')
        throw new Error('money walk: invalid statement group');
      return { ...line, spend_usd: usdToMicros(line.spend_usd), markup_usd: usdToMicros(line.markup_usd) };
    }),
  };
}
/** Exact micros projection, grouped by (model, usage_estimated), UTC month bounded. */
export function statementFrom(entries: EntryRow[], month: string) {
  const credits = Object.fromEntries(CREDIT_KINDS.map((kind) => [kind, 0n]));
  const spend = new Map<string, { model: string; count: number; spend: bigint; markup: bigint; estimated: boolean }>();
  let opening = 0n;
  let closing = 0n;
  for (const entry of entries) {
    const at = new Date(entry.occurred_at).toISOString().slice(0, 7);
    if (at > month) continue;
    const amount = BigInt(entry.amount_micros);
    closing += amount;
    if (at < month) {
      opening += amount;
      continue;
    }
    if (Object.hasOwn(credits, entry.kind)) credits[entry.kind] += amount;
    if (entry.kind === 'spend') {
      const key = JSON.stringify([entry.model, entry.usage_estimated]);
      const line = spend.get(key) ?? {
        model: entry.model,
        count: 0,
        spend: 0n,
        markup: 0n,
        estimated: entry.usage_estimated,
      };
      line.count++;
      line.spend -= amount;
      line.markup += BigInt(entry.markup_micros ?? '0');
      spend.set(key, line);
    }
  }
  const lines = [...spend.values()].sort((a, b) =>
    a.spend === b.spend
      ? a.model.localeCompare(b.model) || Number(a.estimated) - Number(b.estimated)
      : a.spend > b.spend
        ? -1
        : 1,
  );
  return {
    month,
    currency: 'USD',
    opening_balance: String(opening),
    closing_balance: String(closing),
    credits_by_kind: Object.fromEntries(Object.entries(credits).map(([kind, amount]) => [kind, String(amount)])),
    spend_by_model: lines.map((line) => ({
      model: line.model,
      count: line.count,
      spend_usd: String(line.spend),
      markup_usd: String(line.markup),
      estimated: line.estimated,
    })),
  };
}
