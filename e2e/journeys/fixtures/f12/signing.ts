import { createHmac, randomBytes, timingSafeEqual } from 'node:crypto';
import type { PaymentScope } from './contract.ts';
import { reference, requireScope } from './contract.ts';
import { cents, count, fail, record } from './validation.ts';

const PROTOCOL = 'f12-local-oracle/v1';
function event(s: PaymentScope, input: unknown) {
  const r = record(input, ['eventId', 'sessionId', 'paymentId', 'amountMicros', 'outcome']);
  if (r.outcome !== 'paid' && r.outcome !== 'cancelled') return fail();
  return Object.freeze({ eventId: reference(s, 'event', r.eventId), sessionId: reference(s, 'session', r.sessionId),
    paymentId: reference(s, 'payment', r.paymentId), amountMicros: String(cents(r.amountMicros)), outcome: r.outcome });
}
/**
 * Synthetic envelope oracle ONLY. Not a Stripe/payment-provider signature protocol,
 * receiver, webhook route, deployment secret or attestation of private fulfilment.
 * No provided key is accepted; each instance owns a fresh memory-only random key.
 */
export function createLocalSigner(s: PaymentScope, options: unknown = {}) {
  requireScope(s); record(options, []);
  const key = randomBytes(32); let disposed = false;
  function live() { if (disposed) fail('DISPOSED'); }
  function mac(body: string, issuedAt: number) {
    return createHmac('sha256', key).update(`${PROTOCOL}\n${issuedAt}\n${body}`).digest();
  }
  function sign(input: unknown, issuedAt: unknown) {
    live(); const timestamp = count(issuedAt); const e = event(s, input);
    const body = JSON.stringify({ runId: s.runId, organizationId: s.organizationId, personId: s.personId, event: e });
    return Object.freeze({ protocol: PROTOCOL, body, issuedAt: timestamp, signature: mac(body, timestamp).toString('hex') });
  }
  function verify(input: unknown, now: unknown) {
    live(); const r = record(input, ['protocol', 'body', 'issuedAt', 'signature']);
    if (r.protocol !== PROTOCOL || typeof r.body !== 'string' || r.body.length > 8192
      || typeof r.signature !== 'string' || !/^[a-f0-9]{64}$/.test(r.signature)) return fail('SIGNATURE');
    const issued = count(r.issuedAt); const current = count(now);
    if (issued > current || current - issued > 300) return fail('TIME');
    if (!timingSafeEqual(mac(r.body, issued), Buffer.from(r.signature, 'hex'))) return fail('SIGNATURE');
    let decoded: unknown;
    try { decoded = JSON.parse(r.body); } catch { return fail('SIGNATURE'); }
    const b = record(decoded, ['runId', 'organizationId', 'personId', 'event']);
    if (b.runId !== s.runId || b.organizationId !== s.organizationId || b.personId !== s.personId) return fail('SIGNATURE');
    return event(s, b.event);
  }
  function dispose() { key.fill(0); disposed = true; }
  // Functions serialize to no key material. The key is never returned or logged.
  return Object.freeze({ sign, verify, dispose });
}
