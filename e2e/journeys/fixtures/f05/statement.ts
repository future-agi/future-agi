import { requireScope, type MoneyScope } from './contract.ts';
import { fail, integer, list, model, record, timestamp, uuid } from './validation.ts';

// Candidate serializers/managed_credits.py:63-73,487-519. Not DB column guesses.
export const CREDIT_KINDS = Object.freeze([
  'topup', 'grant', 'refund', 'adjustment', 'expiry', 'topup_reversal',
  'dispute_hold', 'dispute_release',
] as const);
const ENTRY_FIELDS = ['id', 'organization_id', 'occurred_at', 'kind', 'amount_micros',
  'model', 'markup_micros', 'usage_estimated'] as const;
type Group = { model: string; estimated: boolean; count: number; spend: bigint; markup: bigint };

function monthWindow(value: unknown): { month: string; start: string; end: string } {
  if (typeof value !== 'string' || !/^[0-9]{4}-(0[1-9]|1[0-2])$/.test(value)) return fail('MONTH');
  const year = Number(value.slice(0, 4)), month = Number(value.slice(5));
  if (year < 1 || year >= 9999) return fail('MONTH');
  const next = month === 12 ? `${year + 1}`.padStart(4, '0') + '-01'
    : value.slice(0, 5) + `${month + 1}`.padStart(2, '0');
  return { month: value, start: `${value}-01T00:00:00.000000Z`, end: `${next}-01T00:00:00.000000Z` };
}

/** Strict DATA-ONLY oracle over a proposed normalized capture. Does not collect
 * private rows or certify their completeness/authenticity. Expected IDs must
 * come from an independently obtained full-cohort witness, never the same page.
 * Legacy synthetic arithmetic controls only. Public Decimal JSON can already be
 * rounded on the SERVER: even original tokens are not exact ledger evidence.
 * Runtime uses privateStatement over private capture.entries; public statement
 * comparisons use assertStatementDisplay, which returns DISPLAY_ONLY. */
export function reconcileStatement(scope: MoneyScope, input: unknown) {
  requireScope(scope);
  const r = record(input, ['organizationId', 'month', 'currency', 'expectedEntryIds', 'entries', 'statement']);
  if (uuid(r.organizationId) !== scope.organizationId || r.currency !== 'USD') return fail('STATEMENT-SCOPE');
  const window = monthWindow(r.month);
  const expected = list(r.expectedEntryIds).map(uuid);
  if (new Set(expected).size !== expected.length) return fail('COHORT');
  const entries = list(r.entries);
  if (entries.length !== expected.length) return fail('COHORT');
  const wanted = new Set(expected), seen = new Set<string>();
  const credits = new Map<string, bigint>(CREDIT_KINDS.map(kind => [kind, 0n]));
  const groups = new Map<string, Group>();
  let opening = 0n, change = 0n;
  for (const raw of entries) {
    const e = record(raw, ENTRY_FIELDS);
    const id = uuid(e.id);
    if (!wanted.has(id) || seen.has(id)) return fail('COHORT');
    seen.add(id);
    if (uuid(e.organization_id) !== scope.organizationId) return fail('STATEMENT-TENANT');
    const at = timestamp(e.occurred_at);
    if (at >= window.end) return fail('STATEMENT-WINDOW');
    const kind = e.kind;
    if (typeof kind !== 'string' || (!credits.has(kind) && kind !== 'spend' && kind !== 'usage_debit')) return fail('ENTRY-KIND');
    const amount = integer(e.amount_micros);
    if (typeof e.usage_estimated !== 'boolean') return fail();
    const entryModel = e.model === '' && kind !== 'spend' ? '' : model(e.model);
    const markup = e.markup_micros === null ? 0n : integer(e.markup_micros, true);
    if ((['spend', 'usage_debit', 'expiry', 'topup_reversal', 'dispute_hold'].includes(kind) && amount > 0n)
      || (['topup', 'grant', 'refund', 'dispute_release'].includes(kind) && amount < 0n)) return fail('ENTRY-SIGN');
    if (at < window.start) { opening += amount; continue; }
    change += amount; // Includes usage_debit, which has no spend_by_model line.
    if (credits.has(kind)) credits.set(kind, credits.get(kind)! + amount);
    if (kind === 'spend') {
      const key = JSON.stringify([entryModel, e.usage_estimated]);
      const group = groups.get(key) ?? { model: entryModel, estimated: e.usage_estimated,
        count: 0, spend: 0n, markup: 0n };
      group.count++; group.spend -= amount; group.markup += markup;
      groups.set(key, group);
    }
  }
  const s = record(r.statement, ['opening_balance_micros', 'closing_balance_micros', 'credits_by_kind', 'spend_by_model']);
  if (integer(s.opening_balance_micros) !== opening || integer(s.closing_balance_micros) !== opening + change) return fail('STATEMENT-BALANCE');
  const actualCredits = record(s.credits_by_kind, CREDIT_KINDS);
  for (const kind of CREDIT_KINDS) {
    if (integer(actualCredits[kind]) !== credits.get(kind)) return fail('STATEMENT-CREDITS');
  }
  const actualGroups = list(s.spend_by_model);
  if (actualGroups.length !== groups.size) return fail('STATEMENT-GROUPS');
  const groupSeen = new Set<string>();
  for (const raw of actualGroups) {
    const g = record(raw, ['model', 'estimated', 'count', 'spend_micros', 'markup_micros']);
    const name = model(g.model);
    if (typeof g.estimated !== 'boolean' || typeof g.count !== 'number' || !Number.isSafeInteger(g.count) || g.count < 1) return fail('STATEMENT-GROUPS');
    const key = JSON.stringify([name, g.estimated]);
    const expectedGroup = groups.get(key);
    if (!expectedGroup || groupSeen.has(key) || g.count !== expectedGroup.count
      || integer(g.spend_micros, true) !== expectedGroup.spend
      || integer(g.markup_micros, true) !== expectedGroup.markup) return fail('STATEMENT-GROUPS');
    groupSeen.add(key);
  }
  return Object.freeze({
    kind: 'oracle-only-not-financial-acceptance', organizationId: scope.organizationId,
    month: window.month, entryCount: entries.length,
    closingBalanceMicros: (opening + change).toString(),
  });
}
