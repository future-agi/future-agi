// J12 money assertion arithmetic ONLY. No service simulator, wire decoder, ledger
// mock, fixture provisioning, acceptance gate or runner is implemented here.
// Provenance: JOURNEYS-708/packets/J12/gateway/PACKET.json
// consumer_descriptor.fixture_contract: F=1000, H=100, U=40 and
// assertion_inventory A02-A10; controls C02/C04/C05.
// These are packet units, NOT claimed candidate micros. The money owner must pin
// their conversion and the private ledger/Redis projection before live binding.

export interface MoneyAmounts {
  available: string;
  openHolds: string;
  netSettled: string;
}

export interface MoneyFixture {
  funded: string;
  reserved: string;
  knownUsage: string;
}

export const REFERENCE_MONEY: Readonly<MoneyFixture> = Object.freeze({
  funded: '1000', reserved: '100', knownUsage: '40',
});

export type MoneyBoundary = 'funded' | 'reserved' | 'missing-usage' | 'known-usage';
export type MoneyViolation =
  | 'NON_INTEGER_UNITS'
  | 'INVALID_FIXTURE'
  | 'UNKNOWN_BOUNDARY'
  | 'INVALID_FENCE_WITNESS'
  | 'CONSERVATION'
  | 'FUNDED_STATE'
  | 'RESERVATION_STATE'
  | 'UNPROTECTED_OBLIGATION'
  | 'KNOWN_USAGE_STATE';

// Canonical unsigned decimal strings avoid float rounding, exponent notation,
// partial parsing and accepting -0. BigInt keeps even >2^53 units exact.
function units(value: unknown): bigint | null {
  return typeof value === 'string' && /^(0|[1-9][0-9]*)$/.test(value)
    ? BigInt(value) : null;
}

/** A necessary arithmetic check, never sufficient monetary evidence.
 * It cannot prove authoritative provenance, fencing enforcement/recoverability,
 * no duplicate compensated entries, per-request holds or idempotency identities.
 * fenceObserved is a separately obtained witness, not inferred from a balance.
 * Full-H/fenced alternatives apply only at the missing-usage boundary; a later
 * reconciliation policy still needs the money owner's reviewed candidate contract.
 */
export function moneyViolations(
  fixture: MoneyFixture,
  amounts: MoneyAmounts,
  boundary: MoneyBoundary,
  fenceObserved: boolean,
): MoneyViolation[] {
  const f = units(fixture.funded);
  const h = units(fixture.reserved);
  const u = units(fixture.knownUsage);
  const a = units(amounts.available);
  const held = units(amounts.openHolds);
  const settled = units(amounts.netSettled);
  if (f === null || h === null || u === null || a === null || held === null || settled === null) {
    return ['NON_INTEGER_UNITS'];
  }
  if (h <= 0n || u <= 0n || u > h || h > f) return ['INVALID_FIXTURE'];
  if (typeof fenceObserved !== 'boolean') return ['INVALID_FENCE_WITNESS'];
  const problems: MoneyViolation[] = [];
  if (a + held + settled !== f) problems.push('CONSERVATION');
  switch (boundary) {
    case 'funded':
      if (a !== f || held !== 0n || settled !== 0n || fenceObserved) problems.push('FUNDED_STATE');
      break;
    case 'reserved':
      if (a !== f - h || held !== h || settled !== 0n || fenceObserved) problems.push('RESERVATION_STATE');
      break;
    case 'missing-usage': {
      const fullHoldSettled = a === f - h && held === 0n && settled === h;
      const fullHoldFenced = a === f - h && held === h && settled === 0n && fenceObserved;
      if (!fullHoldSettled && !fullHoldFenced) problems.push('UNPROTECTED_OBLIGATION');
      break;
    }
    case 'known-usage':
      if (a !== f - u || held !== 0n || settled !== u || fenceObserved) problems.push('KNOWN_USAGE_STATE');
      break;
    default:
      problems.push('UNKNOWN_BOUNDARY');
  }
  return problems;
}

/** Necessary duplicate-delivery check AFTER the reviewed terminal reconciliation.
 * Not an outbox/worker simulator, not proof that a real duplicate was delivered,
 * and not a substitute for reading all debit/settlement identities (F05).
 * Every observation must independently satisfy the complete known-usage amounts.
 * An empty observation list must fail rather than pass an Array.every check.
 */
export function knownUsageReplayViolations(
  fixture: MoneyFixture,
  observations: readonly MoneyAmounts[],
): readonly string[] {
  if (observations.length === 0) return ['NO_REPLAY_OBSERVATIONS'];
  return observations.flatMap((amounts, index) =>
    moneyViolations(fixture, amounts, 'known-usage', false).map(code => `${index}:${code}`));
}
