import { requireScope, type MoneyScope } from './contract.ts';
import { fail, integer, record, uuid } from './validation.ts';

/** Decimal STRING arithmetic only, NEVER a public wallet numeric token.
 * Server Decimal-to-float rendering can already have lost micros before capture;
 * this helper cannot establish provenance or repair it. Runtime exact-money
 * assertions must use exactPrivateMoney on the admitted private projection.
 * No rounding: more than six places, exponent notation and negative zero fail. */
export function usdToMicros(value: unknown): string {
  if (typeof value !== 'string' || value.length > 30 || !/^-?(0|[1-9][0-9]*)(\.[0-9]{1,6})?$/.test(value)) return fail('AMOUNT');
  const negative = value.startsWith('-');
  const [whole, fraction = ''] = (negative ? value.slice(1) : value).split('.');
  let micros = BigInt(whole) * 1000000n + BigInt(fraction.padEnd(6, '0'));
  if (negative) {
    if (micros === 0n) return fail('AMOUNT');
    micros = -micros;
  }
  return integer(micros.toString()).toString();
}

/** A non-dispatchable ledger-service seam, not a direct balance setter, issuer,
 * authorization callback or HTTP client. Actual staff authentication remains
 * in the separately reviewed non-retaining local adapter. */
export function grantPlan(scope: MoneyScope, input: unknown) {
  requireScope(scope);
  const r = record(input, ['amountMicros', 'grantId']);
  const amount = integer(r.amountMicros, true);
  // ManagedCreditGrantRequestSerializer: 0.000001 <= USD <= 1,000,000,000.
  if (amount === 0n || amount > 1000000000000000n) return fail('AMOUNT');
  const grantId = uuid(r.grantId);
  return Object.freeze({
    kind: 'f05-ledger-service-grant-plan',
    dispatchable: false,
    authority: 'isolated-staff-session-required',
    origin: scope.apiOrigin,
    method: 'POST', path: '/agentcc/managed-credits/grant/',
    body: Object.freeze({
      org_id: scope.organizationId,
      amount_usd: `${amount / 1000000n}.${(amount % 1000000n).toString().padStart(6, '0')}`,
      note: 'e2e-f05 synthetic credit; not payment fulfilment',
      idempotency_key: `e2e-f05-${scope.runId}-${grantId}`,
    }),
  });
}
