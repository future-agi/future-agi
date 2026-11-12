import { fail, loopback, record, uuid } from './validation.ts';

export const F12_CANDIDATE = Object.freeze({
  head: 'da4c81cc623f909ce38f53e01b557ee8374d5adb', runtimeReady: false,
  projectionBinding: 'UNBOUND', signingProtocolBinding: 'UNBOUND', claimBarrierBinding: 'UNBOUND',
  financialReviewer: 'UNAPPOINTED: main WALKS-D2 independent PORT-708J-W02 G10/G12 role',
});
export const F12_PROJECTION_REQUIREMENTS = Object.freeze({
  version: 'f12-proposed-projection/v1', status: 'PROPOSED-NOT-ADMITTED',
  payment: Object.freeze(['org/person/run attribution', 'checkout/payment/event identities',
    'payment state and exactly-once ledger credit in integer micros', 'complete delivery cohort and overlap witnesses']),
  card: Object.freeze(['complete test-owned card ID set', 'authoritative default versus API projection']),
  consent: Object.freeze(['append-only actor/version/time/amount/threshold receipts',
    'active authorization invalidated on revoke', 'real claim-under-account-lock and disable ordering']),
});
export interface PaymentScope {
  readonly candidateHead: string;
  readonly runId: string;
  readonly organizationId: string;
  readonly personId: string;
  readonly authority: 'person-session';
  readonly fixtureKind: 'test-owned-loopback';
  readonly apiOrigin: string;
  readonly appOrigin: string;
}
const scopes = new WeakSet<object>();
/** A planning scope, not proof that the caller owns the IDs or has an authenticated session. */
export function paymentScope(input: unknown): PaymentScope {
  const r = record(input, ['candidateHead', 'runId', 'organizationId', 'personId', 'authority', 'fixtureKind', 'apiOrigin', 'appOrigin']);
  if (r.candidateHead !== F12_CANDIDATE.head) return fail('CANDIDATE-UNBOUND');
  if (r.authority !== 'person-session' || r.fixtureKind !== 'test-owned-loopback') return fail('IDENTITY');
  const s: PaymentScope = Object.freeze({ candidateHead: F12_CANDIDATE.head, runId: uuid(r.runId),
    organizationId: uuid(r.organizationId), personId: uuid(r.personId), authority: 'person-session',
    fixtureKind: 'test-owned-loopback', apiOrigin: loopback(r.apiOrigin), appOrigin: loopback(r.appOrigin) });
  scopes.add(s); return s;
}
export function requireScope(s: PaymentScope): void {
  if (!s || typeof s !== 'object' || !scopes.has(s)) fail('SCOPE');
}
export function testReference(s: PaymentScope, kind: string, id: unknown): string {
  requireScope(s);
  if (!['session', 'payment', 'event', 'card'].includes(kind)) return fail('REFERENCE');
  return `f12_${kind}_${s.runId}_${uuid(id)}`;
}
export function reference(s: PaymentScope, kind: string, value: unknown): string {
  requireScope(s); const prefix = `f12_${kind}_${s.runId}_`;
  if (typeof value !== 'string' || !value.startsWith(prefix)) return fail('REFERENCE');
  try { uuid(value.slice(prefix.length)); } catch { return fail('REFERENCE'); }
  return value;
}
export function requireF12Runtime(head: unknown): never {
  if (head !== F12_CANDIDATE.head) return fail('CANDIDATE-UNBOUND');
  return fail('PRIVATE-BINDING-UNBOUND');
}
/** No private table/column guess and no executor callback. */
export function privatePaymentQuery(s: PaymentScope): never {
  requireScope(s); return requireF12Runtime(s.candidateHead);
}
/** A local mutex would not establish the real product's account-row lock. */
export function autoReloadBarrier(s: PaymentScope): never {
  requireScope(s); return fail('CLAIM-BARRIER-UNBOUND');
}
