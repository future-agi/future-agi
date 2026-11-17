import { expect } from '@playwright/test';
import type { CreditAccount } from '../_shared/postgres-config';
import type { RequestRecord } from '../_shared/postgres-requests';
import { requireCohort, requireId } from '../_shared/identifiers';
import { CREDIT_KINDS, usdToMicros, type CreditEntry, type CreditStatement } from './credit-readers';

/** All functions below are PARTIAL row oracles. They neither provision fixtures
 * nor emit journey-row passes. F05's authoritative ledger, F02's Redis probe,
 * F09's personal actors, CSV/UI parity and accepted runtime binding remain owed.
 * A data-only control exercising these functions is NOT a J11 product walk.
 */
export function assertExactEntries(actual: readonly CreditEntry[], expected: readonly CreditEntry[]): void {
  requireCohort(expected.map(row => row.id));
  const order = (rows: readonly CreditEntry[]) => [...rows].sort((a, b) => a.id.localeCompare(b.id));
  // Cardinality and every projected field: no Set-deduplication hiding a debit.
  expect(order(actual), 'J11-ORACLE entries exact multiplicity').toEqual(order(expected));
}

/** Independent aggregation of the public fresh-org inventory, not a second call
 * to the statement endpoint. Pin: agentcc/views/managed_credits.py:credit_statement.
 * Only `spend` is grouped into spend_by_model; usage_debit still changes balance.
 * The local mock fixture must independently prove nonestimated usage. The public
 * entries serializer does not expose usage_estimated, so this oracle cannot
 * establish that provenance; do not apply it to estimated/unknown fixtures.
 */
export function assertUnestimatedStatement(
  entries: readonly CreditEntry[], statement: CreditStatement, expectedEntryIds: readonly string[],
): void {
  requireCohort(expectedEntryIds);
  expect(entries.map(row => row.id).sort(), 'J11-ORACLE statement exact entry inventory')
    .toEqual([...expectedEntryIds].sort());
  expect(statement.currency, 'J11-ORACLE statement currency').toBe('USD');
  expect(statement.month, 'J11-ORACLE statement month').toMatch(/^\d{4}-(0[1-9]|1[0-2])$/);
  const start = new Date(`${statement.month}-01T00:00:00.000Z`);
  const end = new Date(start);
  end.setUTCMonth(end.getUTCMonth() + 1);
  let opening = 0n;
  let closing = 0n;
  const credits = Object.fromEntries(CREDIT_KINDS.map(kind => [kind, 0n])) as
    Record<typeof CREDIT_KINDS[number], bigint>;
  const spend = new Map<string, { count: number; amount: bigint; markup: bigint }>();
  for (const row of entries) {
    const time = Date.parse(row.occurred_at);
    expect(Number.isFinite(time), 'J11-ORACLE entry time').toBe(true);
    const amount = BigInt(row.amount_micros);
    if (time < end.getTime()) closing += amount;
    if (time < start.getTime()) { opening += amount; continue; }
    if (time >= end.getTime()) continue;
    if (row.kind === 'spend') {
      expect(amount < 0n, 'J11-ORACLE spend sign').toBe(true);
      expect(row.markup_micros, 'J11-ORACLE spend markup present').not.toBeNull();
      const prior = spend.get(row.model) ?? { count: 0, amount: 0n, markup: 0n };
      spend.set(row.model, { count: prior.count + 1, amount: prior.amount - amount,
        markup: prior.markup + BigInt(row.markup_micros!) });
    } else if (row.kind !== 'usage_debit') credits[row.kind] += amount;
  }
  expect(statement.opening_micros, 'J11-ORACLE statement opening').toBe(String(opening));
  expect(statement.closing_micros, 'J11-ORACLE statement closing').toBe(String(closing));
  expect(statement.credits_by_kind, 'J11-ORACLE statement credit kinds').toEqual(
    Object.fromEntries(CREDIT_KINDS.map(kind => [kind, String(credits[kind])])));
  const expected = [...spend].map(([model, values]) => ({ model, count: values.count,
    spend_micros: String(values.amount), markup_micros: String(values.markup), estimated: false }));
  const order = (rows: CreditStatement['spend_by_model']) => [...rows].sort(
    (a, b) => a.model.localeCompare(b.model) || Number(a.estimated) - Number(b.estimated));
  expect(order(statement.spend_by_model), 'J11-ORACLE statement exact model lines').toEqual(order(expected));
}

/** Publicly pinned account totals/version/ack only (_shared/postgres-config.ts).
 * Does NOT cover W02-03's updated_at/gateway_push_state. Their authoritative
 * projection is unbound; five actual GETs plus those fields remain owed.
 */
export function assertObservedAccountUnchanged(before: CreditAccount, after: CreditAccount): void {
  expect(after, 'J11-ORACLE account unchanged').toEqual(before);
}

type ByokLog = Pick<RequestRecord, 'request_id' | 'billing_mode' | 'cost_markup'>;
/** W01-08 subset: the safe log, complete public entry set and observed account.
 * Private ledger absence, one real upstream call and in-memory credential
 * equality still need accepted fixtures. Never turn this subset into row PASS.
 */
export function assertByokObserved(
  requestId: string, log: ByokLog, entries: readonly CreditEntry[],
  before: CreditAccount, after: CreditAccount,
): void {
  requireId(requestId);
  expect(log.request_id, 'J11-ORACLE BYOK request identity').toBe(requestId);
  expect(log.billing_mode, 'J11-ORACLE BYOK mode').toBe('byok');
  expect(usdToMicros(log.cost_markup), 'J11-ORACLE BYOK markup').toBe('0');
  expect(entries.filter(row => row.request_id === requestId), 'J11-ORACLE BYOK no public debit').toEqual([]);
  assertObservedAccountUnchanged(before, after);
}

/** Literal retained W01-05 requirement, NOT an inferred header convention.
 * PACKET.json W01-05 -> canonical W01 backendAssertions says markup equals
 * x-agentcc-cost. The inspected server's managed_messages_test.go:53-74 instead
 * expects the BILLED cost. Do not weaken either assertion: this entire row is
 * owed pending main/money-owner contract resolution, recorded in AUTHOR.json.
 * The equation is useful only as the declared packet oracle's fallibility test.
 * These integer micros are observations, not a guessed catalogue price fixture.
 */
export function assertPacketCostEquality(
  upstreamMicros: bigint, billedMicros: bigint, markupMicros: bigint, headerUsd: string,
): void {
  expect(upstreamMicros >= 0n, 'J11-ORACLE nonnegative upstream').toBe(true);
  const halfUpBilled = (upstreamMicros * 105n + 50n) / 100n;
  expect(billedMicros, 'J11-ORACLE half-up billed micros').toBe(halfUpBilled);
  expect(markupMicros, 'J11-ORACLE markup difference').toBe(billedMicros - upstreamMicros);
  expect(usdToMicros(headerUsd), 'J11-ORACLE packet header equality').toBe(String(markupMicros));
}
