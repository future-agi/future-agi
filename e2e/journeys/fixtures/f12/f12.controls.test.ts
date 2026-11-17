// Tests first; synthetic inputs only. Execution belongs to the admitted guarded venue.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  F12_CANDIDATE, paymentScope, requireF12Runtime, testReference,
  checkoutPlan, cardPlan, settingsPlan, createLocalSigner,
  privatePaymentQuery, autoReloadBarrier,
} from './index.ts';

const id = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const input = () => ({ candidateHead: F12_CANDIDATE.head, runId: id(1),
  organizationId: id(2), personId: id(3), authority: 'person-session',
  fixtureKind: 'test-owned-loopback', apiOrigin: 'http://127.0.0.1:8000',
  appOrigin: 'http://localhost:3000' });
const scope = () => paymentScope(input());
const checkout = () => ({ amountMicros: '25000000', successPath: '/wallet/return', cancelPath: '/wallet/cancel' });
function event(s = scope()) {
  return { eventId: testReference(s, 'event', id(4)), sessionId: testReference(s, 'session', id(5)),
    paymentId: testReference(s, 'payment', id(6)), amountMicros: '25000000', outcome: 'paid' };
}

test('F12-01: runtime, private projection and claim barrier refuse before side effects', () => {
  assert.equal(F12_CANDIDATE.runtimeReady, false);
  assert.equal(F12_CANDIDATE.projectionBinding, 'UNBOUND');
  assert.throws(() => requireF12Runtime(F12_CANDIDATE.head), /F12-PRIVATE-BINDING-UNBOUND/);
  assert.throws(() => requireF12Runtime('0'.repeat(40)), /F12-CANDIDATE-UNBOUND/);
  assert.throws(() => privatePaymentQuery(scope()), /F12-PRIVATE-BINDING-UNBOUND/);
  assert.throws(() => autoReloadBarrier(scope()), /F12-CLAIM-BARRIER-UNBOUND/);
  assert.ok(Object.isFrozen(F12_CANDIDATE));
});

test('F12-02: session-only, exact, detached scopes never borrow or mint keys', () => {
  const original = input(); const s = paymentScope(original); original.organizationId = id(99);
  assert.equal(s.organizationId, id(2)); assert.ok(Object.isFrozen(s));
  for (const extra of [{ orgKey: 'synthetic' }, { apiKey: 'synthetic' }, { autoMint: true },
    { signingSecret: 'synthetic-not-real' }, { cardNumber: 'not-a-card' }, { balance: 100 },
    { sql: 'UPDATE balances SET amount = 1' }, { runtimeReady: true }]) {
    assert.throws(() => paymentScope({ ...input(), ...extra }), /^Error: F12-INPUT$/);
  }
  for (const authority of ['system', 'org-key', 'user-key', 'anonymous'])
    assert.throws(() => paymentScope({ ...input(), authority }), /F12-IDENTITY/);
  assert.throws(() => checkoutPlan({ ...s }, checkout()), /F12-SCOPE/);
  assert.throws(() => paymentScope({ ...input(), candidateHead: '0'.repeat(40) }), /F12-CANDIDATE-UNBOUND/);
});

test('F12-03: literal loopback only; no credentials, paths or numeric host aliases', () => {
  for (const origin of ['https://example.invalid', 'http://2130706433', 'http://127.1',
    'http://localhost@elsewhere.invalid', 'http://user:synthetic@127.0.0.1',
    'http://localhost:99999', 'http://localhost/path', 'http://localhost?key=synthetic']) {
    assert.throws(() => paymentScope({ ...input(), apiOrigin: origin }), /F12-ORIGIN/);
    assert.throws(() => paymentScope({ ...input(), appOrigin: origin }), /F12-ORIGIN/);
  }
  let invoked = false;
  const bad = { ...input() }; Object.defineProperty(bad, 'personId', { get() { invoked = true; return id(3); } });
  assert.throws(() => paymentScope(bad), /F12-INPUT/); assert.equal(invoked, false);
});

test('F12-04: source-bound checkout body preserves cents and never dispatches', () => {
  const p = checkoutPlan(scope(), checkout());
  assert.equal(p.dispatchable, false); assert.equal(p.method, 'POST');
  assert.equal(p.path, '/agentcc/managed-credits/checkout/');
  assert.equal(p.bodyJson, '{"amount_usd":25.00,"success_url":"http://localhost:3000/wallet/return","cancel_url":"http://localhost:3000/wallet/cancel"}');
  assert.ok(Object.isFrozen(p));
  for (const amountMicros of ['0', '-1', '10001', '1e6', '01', 25000000, '100000000000000'])
    assert.throws(() => checkoutPlan(scope(), { ...checkout(), amountMicros }), /F12-AMOUNT/);
  for (const successPath of ['//elsewhere.invalid', '/path?secret=x', '/a/../b', '/%2f%2felsewhere', '/a\\b'])
    assert.throws(() => checkoutPlan(scope(), { ...checkout(), successPath }), /F12-PATH/);
  assert.throws(() => checkoutPlan(scope(), { ...checkout(), balance: 25 }), /F12-INPUT/);
});

test('F12-05: card plans contain references only, never PAN or setup secrets', () => {
  const s = scope(); const pm = testReference(s, 'card', id(7));
  assert.equal(cardPlan(s, { operation: 'collect' }).path, '/usage/v2/payment-methods/');
  assert.equal(cardPlan(s, { operation: 'collect' }).bodyJson, '{}');
  assert.equal(cardPlan(s, { operation: 'default', cardId: pm }).path, `/usage/v2/payment-methods/${pm}/default/`);
  assert.equal(cardPlan(s, { operation: 'remove', cardId: pm }).method, 'DELETE');
  const cs = testReference(s, 'session', id(8));
  assert.equal(cardPlan(s, { operation: 'confirm', sessionId: cs }).bodyJson, JSON.stringify({ session_id: cs }));
  assert.throws(() => cardPlan(s, { operation: 'collect', signingSecret: 'synthetic' }), /F12-INPUT/);
  assert.throws(() => cardPlan(s, { operation: 'default', cardId: '../live' }), /F12-REFERENCE/);
  assert.throws(() => cardPlan(s, { operation: 'collect', cardNumber: 'not-a-card' }), /F12-INPUT/);
});

test('F12-06: every enabled save requires explicit current consent; disabling carries none', () => {
  const s = scope();
  const enable = { enabled: true, consentVersion: 'auto-reload-v1', thresholdMicros: '5000000', amountMicros: '25000000' };
  const p = settingsPlan(s, { lowBalanceMicros: null, autoReload: enable });
  assert.equal(p.path, '/agentcc/managed-credits/settings/'); assert.equal(p.method, 'PUT');
  assert.equal(JSON.parse(p.bodyJson).auto_reload.consent_version, 'auto-reload-v1');
  for (const consentVersion of [null, '', 'auto-reload-v0', ' auto-reload-v1'])
    assert.throws(() => settingsPlan(s, { lowBalanceMicros: null, autoReload: { ...enable, consentVersion } }), /F12-CONSENT/);
  const disabled = settingsPlan(s, { lowBalanceMicros: null, autoReload: { enabled: false } });
  assert.deepEqual(JSON.parse(disabled.bodyJson).auto_reload, { enabled: false });
  assert.throws(() => settingsPlan(s, { lowBalanceMicros: null, autoReload: { enabled: false, consentVersion: 'auto-reload-v1' } }), /F12-INPUT/);
});

test('F12-07: signer accepts no supplied secret; fresh per-instance keys never serialize', () => {
  const s = scope();
  assert.throws(() => createLocalSigner(s, { signingSecret: 'synthetic-not-real' }), /F12-INPUT/);
  const a = createLocalSigner(s); const b = createLocalSigner(s);
  try {
    const e = event(s); const one = a.sign(e, 1000); const two = b.sign(e, 1000);
    assert.notEqual(one.signature, two.signature);
    assert.equal(JSON.stringify(a), '{}');
    assert.deepEqual(a.verify(one, 1001), e);
    assert.throws(() => b.verify(one, 1001), /F12-SIGNATURE/);
  } finally { a.dispose(); b.dispose(); }
});

test('F12-08: invalid, tampered, stale, future and foreign-run envelopes are refused', () => {
  const s = scope(); const signer = createLocalSigner(s);
  try {
    const signed = signer.sign(event(s), 1000);
    for (const bad of [{ ...signed, signature: '0'.repeat(64) },
      { ...signed, body: signed.body.replace('25000000', '26000000') },
      { ...signed, issuedAt: 1001 }, { ...signed, protocol: 'provider-signature' },
      { ...signed, secret: 'synthetic' }])
      assert.throws(() => signer.verify(bad, 1001), /F12-(SIGNATURE|INPUT)/);
    assert.throws(() => signer.verify(signed, 1301), /F12-TIME/);
    assert.throws(() => signer.verify(signed, 999), /F12-TIME/);
    assert.throws(() => signer.sign({ ...event(s), amountMicros: '0' }, 1000), /F12-AMOUNT/);
    const other = paymentScope({ ...input(), runId: id(99) });
    assert.throws(() => signer.sign(event(other), 1000), /F12-REFERENCE/);
    assert.throws(() => signer.sign({ ...event(s), liveMode: true }, 1000), /F12-INPUT/);
  } finally { signer.dispose(); }
  assert.throws(() => signer.sign(event(s), 1000), /F12-DISPOSED/);
});

test('F12-09: replay remains cryptographically valid, never mistaken for money deduplication', () => {
  const s = scope(); const signer = createLocalSigner(s);
  try {
    const e = event(s); const signed = signer.sign(e, 1000);
    assert.deepEqual(signer.verify(signed, 1000), e);
    assert.deepEqual(signer.verify(signed, 1000), e);
    assert.equal(signed.protocol, 'f12-local-oracle/v1');
    assert.throws(() => requireF12Runtime(s.candidateHead), /F12-PRIVATE-BINDING-UNBOUND/);
  } finally { signer.dispose(); }
});
