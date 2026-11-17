import type { PaymentScope } from './contract.ts';
import { reference, requireScope } from './contract.ts';
import { cents, count, exactSet, fail, integer, list, ORACLE_ONLY, record, uuid } from './validation.ts';

// This normalized shape is a proposed capture contract, NOT private DB column names.
function snapshot(s: PaymentScope, input: unknown) {
  const r = record(input, ['grantedMicros', 'settledMicros', 'defaultCardId', 'payments', 'events', 'credits']);
  const payments = list(r.payments, 10).map(input => {
    const p = record(input, ['id', 'sessionId', 'status']);
    if (!['pending', 'cancelled', 'paid'].includes(p.status as string)) return fail('MONEY');
    return { id: reference(s, 'payment', p.id), sessionId: reference(s, 'session', p.sessionId), status: p.status };
  });
  const events = list(r.events, 10).map(input => {
    const e = record(input, ['id', 'paymentId']);
    return { id: reference(s, 'event', e.id), paymentId: reference(s, 'payment', e.paymentId) };
  });
  const credits = list(r.credits, 10).map(input => {
    const c = record(input, ['id', 'paymentId', 'sessionId', 'amountMicros']);
    return { id: uuid(c.id), paymentId: reference(s, 'payment', c.paymentId),
      sessionId: reference(s, 'session', c.sessionId), amountMicros: String(cents(c.amountMicros)) };
  });
  for (const rows of [payments, events, credits]) if (new Set(rows.map(r => r.id)).size !== rows.length) fail('COHORT');
  return { grantedMicros: String(integer(r.grantedMicros)), settledMicros: String(integer(r.settledMicros, true)),
    defaultCardId: r.defaultCardId === null ? null : reference(s, 'card', r.defaultCardId), payments, events, credits };
}

/** Exact fresh-session cohort; self-consistent synthetic input is not proof of authentic capture. */
export function assertPaymentOutcome(s: PaymentScope, expectation: unknown, input: unknown) {
  requireScope(s);
  const e = record(expectation, ['scenario', 'sessionId', 'paymentId', 'eventId', 'amountMicros', 'deliveryIds']);
  const scenarios = ['invalid-signature', 'tampered-signature', 'cancelled', 'unknown-pending', 'credited', 'duplicate-replay', 'concurrent-replay'];
  if (typeof e.scenario !== 'string' || !scenarios.includes(e.scenario)) return fail();
  const scenario = e.scenario;
  const session = reference(s, 'session', e.sessionId); const payment = reference(s, 'payment', e.paymentId);
  const event = reference(s, 'event', e.eventId); const amount = cents(e.amountMicros);
  const credited = ['credited', 'duplicate-replay', 'concurrent-replay'].includes(scenario);
  const replay = ['duplicate-replay', 'concurrent-replay'].includes(scenario);
  const expectedCount = replay ? 2 : ['cancelled', 'unknown-pending'].includes(scenario) ? 0 : 1;
  const ids = list(e.deliveryIds, 2).map(uuid);
  if (ids.length !== expectedCount) return fail('COHORT');
  const r = record(input, ['organizationId', 'before', 'after', 'deliveries', 'returnResponse']);
  if (r.organizationId !== s.organizationId) return fail('IDENTITY');
  const before = snapshot(s, r.before); const after = snapshot(s, r.after);
  const deliveries = list(r.deliveries, 2).map(input => {
    const d = record(input, ['id', 'eventId', 'signatureValid', 'entered', 'returned']);
    if (reference(s, 'event', d.eventId) !== event || d.signatureValid !== credited) return fail('SIGNATURE');
    const entered = count(d.entered); const returned = count(d.returned);
    if (entered >= returned) return fail('TIME');
    return { id: uuid(d.id), entered, returned };
  });
  exactSet(deliveries.map(d => d.id), ids);
  if (scenario === 'concurrent-replay' && Math.max(...deliveries.map(d => d.entered)) >= Math.min(...deliveries.map(d => d.returned)))
    return fail('CONCURRENCY');
  if (scenario === 'duplicate-replay') {
    const ordered = [...deliveries].sort((a, b) => a.entered - b.entered);
    if (ordered[1].entered < ordered[0].returned) return fail('CONCURRENCY');
  }
  // Full per-logical-payment scope starts with no credit/event; existing account totals may be nonzero.
  if (before.credits.length || before.events.length) return fail('MONEY');
  const expectedPayments = scenario === 'unknown-pending' ? [] : [payment];
  exactSet(before.payments.map(p => p.id), expectedPayments);
  exactSet(after.payments.map(p => p.id), expectedPayments);
  for (const p of before.payments) if (p.sessionId !== session || p.status !== (scenario === 'cancelled' ? 'cancelled' : 'pending')) fail('MONEY');
  if (credited) {
    if (after.payments[0].sessionId !== session || after.payments[0].status !== 'paid') return fail('MONEY');
    if (after.credits.length !== 1 || after.events.length !== 1) return fail('MONEY');
    const c = after.credits[0]; const ev = after.events[0];
    if (c.paymentId !== payment || c.sessionId !== session || BigInt(c.amountMicros) !== amount
      || ev.id !== event || ev.paymentId !== payment) return fail('MONEY');
  } else if (JSON.stringify(before) !== JSON.stringify(after)) return fail('MONEY');
  if (BigInt(after.grantedMicros) - BigInt(before.grantedMicros) !== (credited ? amount : 0n)
    || before.settledMicros !== after.settledMicros || before.defaultCardId !== after.defaultCardId) return fail('MONEY');
  const response = record(r.returnResponse, ['status', 'result']);
  const result = record(response.result, ['session_id', 'status', 'credited']);
  if (response.status !== true || result.credited !== credited || result.status !== (credited ? 'complete' : 'pending')
    || result.session_id !== (credited ? session : null)) return fail('RETURN');
  return Object.freeze({ kind: ORACLE_ONLY, scenario, credited, deliveryCount: deliveries.length });
}

/** Compare safe normalized projections, not raw provider objects or card numbers. */
export function assertDefaultCards(s: PaymentScope, input: unknown) {
  requireScope(s);
  const r = record(input, ['organizationId', 'expectedCardIds', 'expectedDefaultCardId', 'apiCards', 'authoritativeCards']);
  if (r.organizationId !== s.organizationId) return fail('IDENTITY');
  const expected = list(r.expectedCardIds, 100).map(id => reference(s, 'card', id));
  const selected = r.expectedDefaultCardId === null ? null : reference(s, 'card', r.expectedDefaultCardId);
  if (selected !== null && !expected.includes(selected)) return fail('CARD');
  for (const input of [r.apiCards, r.authoritativeCards]) {
    const cards = list(input, 100).map(input => {
      const c = record(input, ['id', 'is_default']);
      if (typeof c.is_default !== 'boolean') return fail('CARD');
      return { id: reference(s, 'card', c.id), isDefault: c.is_default };
    });
    exactSet(cards.map(c => c.id), expected);
    exactSet(cards.filter(c => c.isDefault).map(c => c.id), selected === null ? [] : [selected]);
  }
  return Object.freeze({ kind: ORACLE_ONLY, cardCount: expected.length, defaultCardId: selected });
}
