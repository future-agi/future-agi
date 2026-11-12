import { requireScope, type MoneyScope } from './contract.ts';
import { CREDIT_KINDS } from './statement.ts';
import { exactPrivateMoney, assertMoneyDisplay, type decodePrivateEntry } from './private-values.ts';
import { fail, list, record } from './validation.ts';

type Entry = ReturnType<typeof decodePrivateEntry>;
type Group = { model: string; estimated: boolean; count: number; spendMicros: string; markupMicros: string };
const statements = new WeakSet<object>();
function window(month: unknown) {
  if (typeof month !== 'string' || !/^[0-9]{4}-(0[1-9]|1[0-2])$/.test(month)) fail('MONTH');
  const y = Number(month.slice(0, 4)), m = Number(month.slice(5));
  if (y < 1 || y >= 9999) fail('MONTH');
  const next = m === 12 ? `${y + 1}`.padStart(4, '0') + '-01' : month.slice(0, 5) + `${m + 1}`.padStart(2, '0');
  return { month, start: `${month}-01T00:00:00.000000Z`, end: `${next}-01T00:00:00.000000Z` };
}

/** Exact calculation exclusively from private-schema-normalized rows. Caller
 * must use capture.entries (full org history in ONE snapshot), not a wallet page
 * or requestEntries subset. This function alone does not certify completeness.
 * Service semantics: agentcc/views/managed_credits.py:391-457 at the r2 head. */
export function privateStatement(scope: MoneyScope, privateRows: unknown, month: unknown) {
  requireScope(scope);
  const w = window(month), rows = list(privateRows), ids = new Set(), keys = new Set();
  const credits = new Map<string, bigint>(CREDIT_KINDS.map(k => [k, 0n]));
  const groups = new Map<string, Group>();
  let opening = 0n, change = 0n, count = 0;
  for (const value of rows) {
    // Brand validation BEFORE reading any properties. No public amount cast.
    const amount = exactPrivateMoney(value, 'amount_micros');
    if (amount === null) fail('AMOUNT');
    const r = value as Entry;
    if (r.organization_id !== scope.organizationId) fail('PRIVATE-TENANT');
    if (ids.has(r.id) || keys.has(r.idempotency_key)) fail('PRIVATE-DUPLICATE');
    ids.add(r.id); keys.add(r.idempotency_key);
    const at = r.occurred_at as string;
    if (at >= w.end) continue;
    count++;
    if (at < w.start) { opening += amount; continue; }
    change += amount; // Includes platform usage_debit and every credit kind.
    const kind = r.kind as string;
    if (credits.has(kind)) credits.set(kind, credits.get(kind)! + amount);
    if (kind === 'spend') {
      const key = JSON.stringify([r.model, r.usage_estimated]);
      const g = groups.get(key) ?? { model: r.model as string, estimated: r.usage_estimated as boolean,
        count: 0, spendMicros: '0', markupMicros: '0' };
      g.count++; g.spendMicros = (BigInt(g.spendMicros) - amount).toString();
      // Source SQL Sum excludes NULL; COALESCE aggregate to zero only for a
      // statement subtotal. NULL is NOT zero for exact per-entry assertions.
      g.markupMicros = (BigInt(g.markupMicros) + (exactPrivateMoney(r, 'markup_micros') ?? 0n)).toString();
      groups.set(key, g);
    }
  }
  const result = Object.freeze({ authority: 'PRIVATE_SCHEMA_ORACLE', organizationId: scope.organizationId,
    month: w.month, currency: 'USD', entryCount: count, openingMicros: opening.toString(),
    closingMicros: (opening + change).toString(),
    credits: Object.freeze(Object.fromEntries([...credits].map(([k, v]) => [k, v.toString()]))),
    groups: Object.freeze([...groups.values()].map(g => Object.freeze(g))),
    moneyReconciled: false,
  });
  statements.add(result);
  return result;
}

/** Wire fields from serializers/managed_credits.py:487-519. Public statement
 * numbers only support display equality. This cannot certify a one-micro
 * financial invariant, regardless of requested display places. */
export function assertStatementDisplay(privateValue: unknown, publicValue: unknown, places: number) {
  if (!privateValue || typeof privateValue !== 'object' || !statements.has(privateValue)) fail('PRIVATE-EXACT-SOURCE');
  const p = privateValue as ReturnType<typeof privateStatement>;
  const s = record(publicValue, ['month', 'currency', 'opening_balance', 'closing_balance', 'credits_by_kind', 'spend_by_model']);
  if (s.month !== p.month || s.currency !== p.currency) fail('DISPLAY-COHORT');
  assertMoneyDisplay(BigInt(p.openingMicros), s.opening_balance, places);
  assertMoneyDisplay(BigInt(p.closingMicros), s.closing_balance, places);
  const credits = record(s.credits_by_kind, CREDIT_KINDS);
  for (const kind of CREDIT_KINDS) assertMoneyDisplay(BigInt(p.credits[kind]), credits[kind], places);
  const groups = list(s.spend_by_model), seen = new Set();
  if (groups.length !== p.groups.length) fail('DISPLAY-GROUP');
  for (const value of groups) {
    const g = record(value, ['model', 'estimated', 'count', 'spend_usd', 'markup_usd']);
    const expected = p.groups.find(x => x.model === g.model && x.estimated === g.estimated);
    if (!expected || seen.has(expected) || g.count !== expected.count) fail('DISPLAY-GROUP');
    seen.add(expected);
    assertMoneyDisplay(BigInt(expected.spendMicros), g.spend_usd, places);
    assertMoneyDisplay(BigInt(expected.markupMicros), g.markup_usd, places);
  }
  return Object.freeze({ authority: 'DISPLAY_ONLY', moneyReconciled: false, places, entryCount: p.entryCount });
}
