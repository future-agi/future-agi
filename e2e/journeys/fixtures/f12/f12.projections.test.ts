// Proposed capture/oracle controls, NOT authoritative product evidence.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  F12_CANDIDATE, paymentScope, testReference, assertPaymentOutcome,
  assertDefaultCards, consentQuery, assertConsentTransition, assertClaimRace,
} from './index.ts';

const id = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const scope = () => paymentScope({ candidateHead: F12_CANDIDATE.head, runId: id(1),
  organizationId: id(2), personId: id(3), authority: 'person-session',
  fixtureKind: 'test-owned-loopback', apiOrigin: 'http://127.0.0.1:8000', appOrigin: 'http://localhost:3000' });
const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v));
function payment(scenario: string) {
  const s = scope(); const ref = (kind: string, n: number) => testReference(s, kind, id(n));
  const success = ['credited', 'duplicate-replay', 'concurrent-replay'].includes(scenario);
  const count = ['duplicate-replay', 'concurrent-replay'].includes(scenario) ? 2
    : ['cancelled', 'unknown-pending'].includes(scenario) ? 0 : 1;
  const expected = { scenario, sessionId: ref('session', 4), paymentId: ref('payment', 5),
    eventId: ref('event', 6), amountMicros: '25000000', deliveryIds: Array.from({ length: count }, (_, i) => id(20 + i)) };
  const before = { grantedMicros: '1000000', settledMicros: '0', defaultCardId: null,
    payments: scenario === 'unknown-pending' ? [] : [{ id: expected.paymentId, sessionId: expected.sessionId,
      status: scenario === 'cancelled' ? 'cancelled' : 'pending' }],
    events: [] as object[], credits: [] as object[] };
  const after = clone(before);
  if (success) {
    after.grantedMicros = '26000000'; after.payments[0].status = 'paid';
    after.events = [{ id: expected.eventId, paymentId: expected.paymentId }];
    after.credits = [{ id: id(30), paymentId: expected.paymentId, sessionId: expected.sessionId, amountMicros: expected.amountMicros }];
  }
  const capture = { organizationId: s.organizationId, before, after,
    deliveries: expected.deliveryIds.map((delivery, i) => ({ id: delivery, eventId: expected.eventId,
      signatureValid: success, entered: scenario === 'concurrent-replay' ? 1 + i : 1 + i * 10,
      returned: scenario === 'concurrent-replay' ? 10 + i : 5 + i * 10 })),
    returnResponse: { status: true, result: { session_id: success ? expected.sessionId : null,
      status: success ? 'complete' : 'pending', credited: success } } };
  return { s, expected, capture };
}

for (const scenario of ['invalid-signature', 'tampered-signature', 'cancelled', 'unknown-pending', 'credited', 'duplicate-replay', 'concurrent-replay']) {
  test(`F12-10: ${scenario} has exact signed-delivery, payment, event, ledger and return checks`, () => {
    const { s, expected, capture } = payment(scenario);
    const result = assertPaymentOutcome(s, expected, capture);
    assert.equal(result.kind, 'oracle-only-not-financial-acceptance');
    assert.equal(result.scenario, scenario);
    const corrupt = clone(capture); corrupt.after.grantedMicros = '999000000';
    assert.throws(() => assertPaymentOutcome(s, expected, corrupt), /F12-MONEY/);
  });
}

test('F12-11: duplicate/concurrent replay never accepts a second payment credit or duplicate event record', () => {
  for (const scenario of ['duplicate-replay', 'concurrent-replay']) {
    const { s, expected, capture } = payment(scenario);
    for (const field of ['credits', 'events', 'payments'] as const) {
      const bad = clone(capture); bad.after[field].push(clone(bad.after[field][0]) as never);
      assert.throws(() => assertPaymentOutcome(s, expected, bad), /F12-(COHORT|MONEY)/);
    }
    const missing = clone(capture); missing.deliveries.pop();
    assert.throws(() => assertPaymentOutcome(s, expected, missing), /F12-COHORT/);
    const same = clone(capture); same.deliveries[1].id = same.deliveries[0].id;
    assert.throws(() => assertPaymentOutcome(s, expected, same), /F12-COHORT/);
  }
});

test('F12-12: concurrent means actual overlapping observations, not Promise.all or a label', () => {
  const { s, expected, capture } = payment('concurrent-replay');
  capture.deliveries[1].entered = 12; capture.deliveries[1].returned = 15;
  assert.throws(() => assertPaymentOutcome(s, expected, capture), /F12-CONCURRENCY/);
  capture.deliveries[1].entered = 2; capture.deliveries[1].returned = 2;
  assert.throws(() => assertPaymentOutcome(s, expected, capture), /F12-TIME/);
});

test('F12-13: pending/cancelled returns never echo unverified IDs or claim credited', () => {
  for (const scenario of ['cancelled', 'unknown-pending', 'invalid-signature', 'tampered-signature']) {
    const { s, expected, capture } = payment(scenario);
    const bad = clone(capture); bad.returnResponse.result.session_id = expected.sessionId;
    assert.throws(() => assertPaymentOutcome(s, expected, bad), /F12-RETURN/);
    const mutation = clone(capture); mutation.after.defaultCardId = testReference(s, 'card', id(42)) as never;
    assert.throws(() => assertPaymentOutcome(s, expected, mutation), /F12-MONEY/);
    if (capture.deliveries.length) {
      capture.deliveries[0].signatureValid = true;
      assert.throws(() => assertPaymentOutcome(s, expected, capture), /F12-SIGNATURE/);
    }
  }
});

test('F12-14: positive return requires all money witnesses, never a success query or 503', () => {
  const { s, expected, capture } = payment('credited');
  for (const alter of [
    (c: typeof capture) => { c.after.credits = []; },
    (c: typeof capture) => { c.after.events = []; },
    (c: typeof capture) => { c.after.settledMicros = '1'; },
    (c: typeof capture) => { c.organizationId = id(99); },
    (c: typeof capture) => { c.returnResponse.result.credited = false; },
  ]) {
    const bad = clone(capture); alter(bad);
    assert.throws(() => assertPaymentOutcome(s, expected, bad), /F12-/);
  }
  assert.throws(() => assertPaymentOutcome(s, expected, { ...capture, returnResponse: { status: false, code: 'billing_unavailable' } }), /F12-/);
  assert.throws(() => assertPaymentOutcome(s, expected, { ...capture, secret: 'synthetic' }), /F12-INPUT/);
  assert.throws(() => assertPaymentOutcome(s, { ...expected, amountMicros: '9007199254740993' }, capture), /F12-AMOUNT/);
});

test('F12-15: default-card exact set and authoritative/API agreement; no PAN capture', () => {
  const s = scope(); const a = testReference(s, 'card', id(40)); const b = testReference(s, 'card', id(41));
  const rows = [{ id: a, is_default: false }, { id: b, is_default: true }];
  const data = { organizationId: s.organizationId, expectedCardIds: [a, b], expectedDefaultCardId: b,
    apiCards: clone(rows), authoritativeCards: clone(rows) };
  assert.equal(assertDefaultCards(s, data).defaultCardId, b);
  for (const alter of [
    (d: typeof data) => { d.apiCards[0].is_default = true; },
    (d: typeof data) => { d.authoritativeCards[1].is_default = false; },
    (d: typeof data) => { d.authoritativeCards.pop(); },
    (d: typeof data) => { d.organizationId = id(90); },
    (d: typeof data) => { d.expectedDefaultCardId = testReference(s, 'card', id(99)); },
  ]) {
    const bad = clone(data); alter(bad); assert.throws(() => assertDefaultCards(s, bad), /F12-/);
  }
  assert.throws(() => assertDefaultCards(s, { ...data, apiCards: rows.map(r => ({ ...r, cardNumber: 'not-a-card' })) }), /F12-INPUT/);
  assert.equal(assertDefaultCards(s, { organizationId: s.organizationId, expectedCardIds: [], expectedDefaultCardId: null,
    apiCards: [], authoritativeCards: [] }).defaultCardId, null);
});

const time = '2026-10-30T12:00:00.000001Z';
function consent() {
  const s = scope();
  const receipt = { id: id(50), organization_id: s.organizationId, actor_id: s.personId,
    actor_identifier: s.personId, consent_version: 'auto-reload-v1', accepted_at: time,
    threshold_micros: '5000000', amount_micros: '25000000' };
  const off = { enabled: false, version: '', acceptedAt: null, thresholdMicros: '5000000', amountMicros: '25000000' };
  const on = { ...off, enabled: true, version: 'auto-reload-v1', acceptedAt: time };
  const data = { action: 'enable', expectedReceiptId: receipt.id, before: off, after: on,
    beforeReceipts: [] as typeof receipt[], afterReceipts: [receipt] };
  return { s, receipt, off, on, data };
}

test('F12-16: public consent query is schema-qualified, tenant-scoped and read-only', () => {
  const q = consentQuery(scope(), 'public');
  assert.match(q.text, /FROM "public"\."agentcc_auto_reload_consent"/);
  assert.match(q.text, /WHERE organization_id = \$1::uuid/);
  assert.deepEqual(q.values, [id(2)]); assert.ok(Object.isFrozen(q));
  assert.doesNotMatch(q.text, /SELECT \*|UPDATE |INSERT |DELETE |secret|token/i);
  assert.throws(() => consentQuery(scope(), 'public; UPDATE x'), /F12-SCHEMA/);
});

test('F12-17: enabling appends one matching actor/version/time/amount receipt', () => {
  const { s, data } = consent();
  assert.equal(assertConsentTransition(s, data).active, true);
  for (const patch of [{ actor_id: null }, { actor_identifier: id(99) },
    { consent_version: 'auto-reload-v0' }, { amount_micros: '26000000' },
    { organization_id: id(99) }, { accepted_at: '2026-02-30T00:00:00.000000Z' }]) {
    const bad = clone(data); Object.assign(bad.afterReceipts[0], patch);
    assert.throws(() => assertConsentTransition(s, bad), /F12-/);
  }
  assert.throws(() => assertConsentTransition(s, { ...data, afterReceipts: [] }), /F12-CONSENT/);
  assert.throws(() => assertConsentTransition(s, { ...data, beforeReceipts: data.afterReceipts }), /F12-CONSENT/);
});

test('F12-18: revoke clears active consent without rewriting or deleting receipt history', () => {
  const { s, receipt, off, on } = consent();
  const data = { action: 'revoke', expectedReceiptId: null, before: on, after: off,
    beforeReceipts: [receipt], afterReceipts: [clone(receipt)] };
  assert.equal(assertConsentTransition(s, data).active, false);
  assert.throws(() => assertConsentTransition(s, { ...data, after: on }), /F12-CONSENT/);
  assert.throws(() => assertConsentTransition(s, { ...data, afterReceipts: [] }), /F12-CONSENT/);
  data.afterReceipts[0].amount_micros = '26000000';
  assert.throws(() => assertConsentTransition(s, data), /F12-CONSENT/);
});

test('F12-19: rejected consent update is no-write; already-enabled saves still append anew', () => {
  const { s, receipt, on } = consent();
  const rejected = { action: 'reject', expectedReceiptId: null, before: on, after: clone(on),
    beforeReceipts: [receipt], afterReceipts: [clone(receipt)] };
  assert.equal(assertConsentTransition(s, rejected).active, true);
  rejected.after.amountMicros = '26000000';
  assert.throws(() => assertConsentTransition(s, rejected), /F12-CONSENT/);
  const fresh = { ...receipt, id: id(51), accepted_at: '2026-10-30T12:00:01.000001Z', amount_micros: '26000000' };
  const updated = { action: 'enable', expectedReceiptId: fresh.id, before: on,
    after: { ...on, acceptedAt: fresh.accepted_at, amountMicros: fresh.amount_micros },
    beforeReceipts: [receipt], afterReceipts: [receipt, fresh] };
  assert.equal(assertConsentTransition(s, updated).active, true);
});

test('F12-20: claim lock ordering, consent-under-lock and policy-OFF are distinct', () => {
  const s = scope();
  const disabled = { scenario: 'disable-before-claim', policyEnabled: true, disableCommitted: 1,
    claimLockAcquired: 2, receiptCurrentUnderLock: false, claimCount: 0, paymentStartedCount: 0 };
  assert.equal(assertClaimRace(s, disabled).positiveClaimCovered, false);
  assert.throws(() => assertClaimRace(s, { ...disabled, claimCount: 1 }), /F12-CLAIM/);
  assert.throws(() => assertClaimRace(s, { ...disabled, claimLockAcquired: 0 }), /F12-CLAIM/);
  const claimed = { ...disabled, scenario: 'claim-before-disable', disableCommitted: 2, claimLockAcquired: 1,
    receiptCurrentUnderLock: true, claimCount: 1, paymentStartedCount: 1 };
  assert.equal(assertClaimRace(s, claimed).positiveClaimCovered, true);
  assert.throws(() => assertClaimRace(s, { ...claimed, receiptCurrentUnderLock: false }), /F12-CLAIM/);
  const off = { scenario: 'policy-off', policyEnabled: false, disableCommitted: null, claimLockAcquired: null,
    receiptCurrentUnderLock: false, claimCount: 0, paymentStartedCount: 0 };
  const result = assertClaimRace(s, off);
  assert.equal(result.kind, 'oracle-only-not-financial-acceptance');
  assert.equal(result.positiveClaimCovered, false);
  assert.throws(() => assertClaimRace(s, { ...off, policyEnabled: true }), /F12-CLAIM/);
  assert.throws(() => assertClaimRace(s, { ...off, paymentStartedCount: 1 }), /F12-CLAIM/);
});
