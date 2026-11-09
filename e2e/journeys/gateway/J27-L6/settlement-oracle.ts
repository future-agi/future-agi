import { requireId } from '../_shared/identifiers';

/** DATA-ONLY ORACLE, not a wire body, SQL schema, Redis client or runtime adapter.
 * Obligations: J27/L6/PACKET.json A05/A06 and C04. The private debit projection
 * and fresh authoritative reads remain owed (FOUNDATION F02/F05/F08).
 * Public provenance at da4c81cc623f909ce38f53e01b557ee8374d5adb:
 * - agentcc-gateway/internal/billing/settlement.go:Fields: org_id/request_id,
 *   billed_micros are decimal strings; asVoid zeroes billed amounts.
 * - internal/redisstate/managedcredits.go:managedKeys/settleScript: markers are
 *   keyed by HOLD ID, value '1'; NOT by an assumed request_id alone.
 * The future reviewed projection must join hold -> outbox -> org/request and
 * return ALL scoped rows, not filter to expected IDs or collapse duplicates.
 * No field below asserts an uninspected usage_managedcreditentry column exists.
 */
export interface ExpectedRequest {
  organizationId: string;
  requestId: string;
  holdId: string | null;
  outboxId: string | null;
  outcome: 'charged' | 'void' | 'refused';
  debitMicros: string;
}
export interface MarkerFact {
  organizationId: string;
  requestId: string;
  holdId: string;
  value: string;
}
export interface OutboxFact {
  organizationId: string;
  requestId: string;
  holdId: string;
  outboxId: string;
}
export interface DebitFact {
  organizationId: string;
  requestId: string;
  outboxId: string;
  entryId: string;
  debitMicros: string;
}
export interface SettlementFacts {
  markersAfterRecovery: MarkerFact[];
  markersReread: MarkerFact[];
  outboxes: OutboxFact[];
  debitsAfterRecovery: DebitFact[];
  debitsAfterReplay: DebitFact[];
}

function micros(value: string): bigint {
  if (typeof value !== 'string' || !/^(0|[1-9][0-9]*)$/.test(value)) {
    throw new Error('Oracle input requires exact nonnegative decimal micros');
  }
  return BigInt(value);
}
const join = (...ids: string[]) => JSON.stringify(ids);
const sameBag = (left: string[], right: string[]) =>
  JSON.stringify([...left].sort()) === JSON.stringify([...right].sort());

function validateExpected(expected: readonly ExpectedRequest[]): void {
  if (!expected.length || expected.length > 256 || !expected.some(r => r.outcome === 'charged')) {
    throw new Error('Oracle fixture needs a finite nonempty chargeable cohort');
  }
  const requestKeys: string[] = [];
  const holdKeys: string[] = [];
  const outboxKeys: string[] = [];
  for (const row of expected) {
    requestKeys.push(join(requireId(row.organizationId), requireId(row.requestId)));
    const amount = micros(row.debitMicros);
    if (row.outcome === 'charged') {
      if (amount <= 0n) throw new Error('Chargeable fixture must have a positive expected debit');
    } else if (!['void', 'refused'].includes(row.outcome) || amount !== 0n) {
      throw new Error('Void/refused fixture must declare zero debit');
    }
    if (row.outcome === 'refused') {
      if (row.holdId !== null || row.outboxId !== null) throw new Error('Refused fixture has settlement state');
    } else {
      if (row.holdId === null || row.outboxId === null) throw new Error('Settled fixture lacks its join');
      holdKeys.push(join(row.organizationId, requireId(row.holdId)));
      outboxKeys.push(join(row.organizationId, requireId(row.outboxId)));
    }
  }
  for (const keys of [requestKeys, holdKeys, outboxKeys]) {
    if (new Set(keys).size !== keys.length) throw new Error('Duplicate expected settlement identity');
  }
}

/** A05's exact-set/value detector. Two arrays are deliberately mandatory; the
 * oracle does NOT pretend they prove fresh connections, elapsed time or storage
 * persistence. Those live provenance checks remain owed, not boolean fixtures.
 */
export function markerIssues(expected: readonly ExpectedRequest[], facts: SettlementFacts): string[] {
  validateExpected(expected);
  const wanted = expected.filter(r => r.outcome !== 'refused').map(r =>
    join(r.organizationId, r.requestId, r.holdId!));
  const issues: string[] = [];
  for (const [phase, rows] of [
    ['after-recovery', facts.markersAfterRecovery], ['reread', facts.markersReread],
  ] as const) {
    const actual = rows.map(row => join(requireId(row.organizationId),
      requireId(row.requestId), requireId(row.holdId)));
    if (!sameBag(actual, wanted)) issues.push(`A05:marker-set:${phase}`);
    if (rows.some(row => row.value !== '1')) issues.push(`A05:marker-value:${phase}`);
  }
  return issues;
}

/** A06's per-request detector, including zero-debit controls, multiplicity and
 * exact BigInt totals before/after replay. OUTBOX HISTORY is not drainage: these
 * rows identify the initial cohort, not the stream's post-ack length. A04's
 * lag/PEL/retry/inflight reader and commit-before-ack hook are separately owed.
 */
export function debitIssues(expected: readonly ExpectedRequest[], facts: SettlementFacts): string[] {
  validateExpected(expected);
  const issues: string[] = [];
  const expectedOutboxes = expected.filter(r => r.outcome !== 'refused').map(r =>
    join(r.organizationId, r.requestId, r.holdId!, r.outboxId!));
  const actualOutboxes = facts.outboxes.map(r => join(requireId(r.organizationId),
    requireId(r.requestId), requireId(r.holdId), requireId(r.outboxId)));
  if (!sameBag(actualOutboxes, expectedOutboxes)) issues.push('A06:outbox-identity-set');
  const chargeable = expected.filter(r => r.outcome === 'charged');
  const wanted = chargeable.map(r => join(r.organizationId, r.requestId, r.outboxId!));
  const expectedTotal = chargeable.reduce((sum, row) => sum + micros(row.debitMicros), 0n);
  for (const [phase, rows] of [
    ['after-recovery', facts.debitsAfterRecovery], ['after-replay', facts.debitsAfterReplay],
  ] as const) {
    const actual = rows.map(r => join(requireId(r.organizationId),
      requireId(r.requestId), requireId(r.outboxId)));
    if (!sameBag(actual, wanted)) issues.push(`A06:debit-identity-multiplicity:${phase}`);
    const entryIds = rows.map(r => requireId(r.entryId));
    if (new Set(entryIds).size !== entryIds.length) issues.push(`A06:duplicate-entry-id:${phase}`);
    for (const row of rows) {
      const amount = micros(row.debitMicros);
      const target = chargeable.find(r => r.organizationId === row.organizationId &&
        r.requestId === row.requestId && r.outboxId === row.outboxId);
      if (target && amount !== micros(target.debitMicros)) issues.push(`A06:debit-amount:${phase}`);
    }
    if (rows.reduce((sum, row) => sum + micros(row.debitMicros), 0n) !== expectedTotal) {
      issues.push(`A06:debit-total:${phase}`);
    }
  }
  // A second debit with the same amount but a replacement row identity is not
  // proof the first persisted. Require the same exact rows after recovery/replay.
  const debitBag = (rows: DebitFact[]) => rows.map(r => join(
    r.organizationId, r.requestId, r.outboxId, r.entryId, r.debitMicros));
  if (!sameBag(debitBag(facts.debitsAfterRecovery), debitBag(facts.debitsAfterReplay))) {
    issues.push('A06:debit-identity-changed-after-replay');
  }
  return issues;
}
