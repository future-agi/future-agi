import { expect } from '@playwright/test';

/** Detector components ONLY, not product reads, API envelopes or a runner seam.
 * Provenance: JOURNEYS-708/packets/J13/gateway/PACKET.json assertion_inventory
 * W03-02..14/W04-03..07 and control_inventory.controls. Camel-case fields below
 * are synthetic, value-free observations, NOT guesses at wire/Redis/SQL fields.
 * The pinned foundation lacks MAXCHARGE/G8B and their accepted observation hooks.
 * Every complete product row remains OWED/NOT_WALKED in AUTHOR.json. These checks
 * must be bound to real, reviewed observations before earning product coverage.
 * Money is bigint throughout; no float tolerance or missing-value defaults.
 */
const label = (id: string) => `J13/oracle/${id}`;
const nonnegative = (...values: bigint[]) => values.every(value => value >= 0n);

export interface RefusedEffects {
  upstreamCalls: number;
  holdDelta: bigint;
  accountHeldDelta: bigint;
  newSettledMarkers: number;
  newLedgerRows: number;
  earlierLedgerRowsBefore: number;
  earlierLedgerRowsAfter: number;
}

/** W03-12: zero NEW effects, not deletion of valid earlier-attempt debits. */
export function assertRefusedEffects(id: string, value: RefusedEffects): void {
  expect({
    upstreamCalls: value.upstreamCalls,
    holdDelta: value.holdDelta,
    accountHeldDelta: value.accountHeldDelta,
    newSettledMarkers: value.newSettledMarkers,
    newLedgerRows: value.newLedgerRows,
    earlierRowsPreserved: value.earlierLedgerRowsAfter === value.earlierLedgerRowsBefore,
  }, label(id)).toStrictEqual({
    upstreamCalls: 0, holdDelta: 0n, accountHeldDelta: 0n,
    newSettledMarkers: 0, newLedgerRows: 0, earlierRowsPreserved: true,
  });
}

export interface BoundObservation {
  max: bigint; admitted: bigint; attempt: bigint; required: bigint;
  reportedMax: bigint; reportedAdmitted: bigint; reportedAttempt: bigint;
}

/** W03-02: the refused attempt is charged against C+B_i, not only B_i.
 * Code/param/scope/limit_source and real transport are deliberately not inferred.
 */
export function assertRefusalBound(id: string, value: BoundObservation): void {
  expect({
    valid: nonnegative(value.max, value.admitted, value.attempt) && value.max > 0n,
    exceeds: value.admitted + value.attempt > value.max,
    required: value.required,
    reportedMax: value.reportedMax,
    reportedAdmitted: value.reportedAdmitted,
    reportedAttempt: value.reportedAttempt,
  }, label(id)).toStrictEqual({
    valid: true, exceeds: true, required: value.admitted + value.attempt,
    reportedMax: value.max, reportedAdmitted: value.admitted, reportedAttempt: value.attempt,
  });
}

/** W03-05: called on EVERY held and settled sample by the eventual live reader.
 * One synthetic sample here cannot establish concurrent or throughout coverage.
 */
export function assertScopeSample(id: string, value: {
  expectedMax: bigint; max: bigint; spent: bigint; held: bigint;
  outstanding: number; expectedOutstanding: number;
}): void {
  expect({
    positiveMax: value.max > 0n, max: value.max,
    validAmounts: nonnegative(value.spent, value.held),
    withinCap: value.spent + value.held <= value.max,
    outstanding: value.outstanding,
  }, label(id)).toStrictEqual({
    positiveMax: true, max: value.expectedMax, validAmounts: true,
    withinCap: true, outstanding: value.expectedOutstanding,
  });
}

/** W03-07: live and final settlement are separate observations; milliseconds. */
export function assertLiveTtl(id: string, outstanding: number, pttlMs: number): void {
  expect({ hasOutstanding: Number.isInteger(outstanding) && outstanding > 0, pttlMs }, label(id))
    .toStrictEqual({ hasOutstanding: true, pttlMs: -1 });
}
export function assertSettledTtl(id: string, held: bigint, outstanding: number, pttlMs: number): void {
  expect({ held, outstanding, boundedTtl: Number.isInteger(pttlMs) && pttlMs > 0 && pttlMs <= 86_400_000 }, label(id))
    .toStrictEqual({ held: 0n, outstanding: 0, boundedTtl: true });
}

/** W03-08: compare non-secret key RECORD identity for call/GET/two closes. */
export function assertSameKey(id: string, expectedKeyId: string, actualIds: readonly string[]): void {
  expect(actualIds, label(id)).toStrictEqual([expectedKeyId, expectedKeyId, expectedKeyId, expectedKeyId]);
}

export interface AttemptMoney {
  rated: bigint; bound: bigint; debit: bigint;
  waived: bigint | null; upstreamCost: bigint; recordedUpstreamCost: bigint;
}

/** W03-11: every debit, waived overrun and real upstream cost, then exact sum.
 * This does not name a private ledger/anomaly table or pretend a log is a ledger.
 */
export function assertCappedMoney(id: string, attempts: readonly AttemptMoney[], ledgerDebit: bigint): void {
  const rows = attempts.map(value => {
    const capped = value.rated < value.bound ? value.rated : value.bound;
    return {
      valid: nonnegative(value.rated, value.bound, value.debit, value.upstreamCost),
      debitMatches: value.debit === capped,
      waivedMatches: value.rated <= value.bound || value.waived === value.rated - capped,
      upstreamMatches: value.recordedUpstreamCost === value.upstreamCost,
    };
  });
  expect({
    nonempty: rows.length > 0, rows,
    ledgerMatches: ledgerDebit === attempts.reduce((sum, value) => sum + value.debit, 0n),
  }, label(id)).toStrictEqual({
    nonempty: true,
    rows: attempts.map(() => ({ valid: true, debitMatches: true, waivedMatches: true, upstreamMatches: true })),
    ledgerMatches: true,
  });
}

/** W03-10: a loss detector, not the unpinned Messages response decoder. */
export function assertMessagesPreservation(id: string, value: {
  status: number; code: string; parameterPreserved: boolean; metadataPreserved: boolean;
}): void {
  expect(value, label(id)).toStrictEqual({
    status: 402, code: 'max_charge_exceeded', parameterPreserved: true, metadataPreserved: true,
  });
}

/** W03-13: complete synthetic tenant state comparison, including extras. */
export function assertTenantIsolation(id: string, value: {
  status: number; code: string; before: readonly string[]; after: readonly string[];
}): void {
  expect({ status: value.status, code: value.code, state: [...value.after].sort() }, label(id))
    .toStrictEqual({ status: 404, code: 'max_charge_scope_not_found', state: [...value.before].sort() });
}

/** W03-14 has no pinned mixed-mode status/body on this candidate. This checks
 * only its pre-dispatch refusal predicate; the full typed contract stays owed.
 */
export function assertMixedModeRefusal(id: string, refused: boolean, dispatches: number): void {
  expect({ refused, dispatches }, label(id)).toStrictEqual({ refused: true, dispatches: 0 });
}

/** W04-05 counts only the refused request, not earlier comparison successes. */
export function assertCapNoDispatch(id: string, calls: number): void {
  expect(calls, label(id)).toBe(0);
}

/** W04-04: scope identity is a synthetic alias, NOT the unpinned run-level wire
 * representation. W04-06 compares sampled UTC midnight, not a clock rollover.
 */
export function assertDailyCap(id: string, value: {
  includesByok: boolean; dayBoundary: string; period: string;
  observedAtMs: number; periodStartMs: number; scopeId: string;
}, expectedScopeId: string): void {
  const midnight = Math.floor(value.observedAtMs / 86_400_000) * 86_400_000;
  expect({
    validTime: Number.isSafeInteger(value.observedAtMs) && Number.isSafeInteger(value.periodStartMs),
    includesByok: value.includesByok, dayBoundary: value.dayBoundary, period: value.period,
    periodStartMs: value.periodStartMs, scopeId: value.scopeId,
  }, label(id)).toStrictEqual({
    validTime: true, includesByok: true, dayBoundary: 'UTC', period: 'daily',
    periodStartMs: midnight, scopeId: expectedScopeId,
  });
}

/** W04-03: supply the previous and next authoritative policy revisions, NOT
 * AgentccOrgConfig.version or a count of versions. No table names are invented.
 */
export function assertActivationTransition(id: string, value: {
  beforeAtMs: number; afterAtMs: number; beforeRevision: bigint; afterRevision: bigint;
  gatewayCopyEqualsActiveConfig: boolean; accountingBefore: readonly bigint[]; accountingAfter: readonly bigint[];
}): void {
  expect({
    activationPreserved: value.afterAtMs === value.beforeAtMs,
    revisionIncreased: value.afterRevision > value.beforeRevision,
    gatewayCopyMatches: value.gatewayCopyEqualsActiveConfig,
    accounting: value.accountingAfter,
  }, label(id)).toStrictEqual({
    activationPreserved: true, revisionIncreased: true, gatewayCopyMatches: true,
    accounting: value.accountingBefore,
  });
}

/** W04-07: exact no-write deltas at all three layers, not just config versions. */
export function assertMemberNoWrite(id: string, value: {
  status: number; configVersionDelta: number; revisionDelta: bigint; activationMutationCount: number;
}): void {
  expect(value, label(id)).toStrictEqual({
    status: 403, configVersionDelta: 0, revisionDelta: 0n, activationMutationCount: 0,
  });
}
