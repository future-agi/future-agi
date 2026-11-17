// Source controls only. Oracle rows below are synthetic, NOT product receipts.
// Run only in the admitted guarded test venue; this lane performs syntax checks.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  F09_CANDIDATE, requireF09Runtime, identityPlan, bindIdentityReadback,
  signupCensusQuery, assertZeroKeySignup, personalOwnershipQuery, assertPersonalOwnership,
} from './index.ts';

const id = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const input = () => ({ runId: id(1), browser: 'chromium', profile: 'stock',
  apiOrigin: 'http://127.0.0.1:8000', appOrigin: 'http://localhost:3000' });
function oracle() {
  const plan = identityPlan(input());
  const rows = plan.actors.map((actor, i) => ({
    slot: actor.slot, isolationId: plan.isolationId,
    userId: id(100 + i), organizationId: id(actor.organization === 'a' ? 2 : 3),
    workspaceId: id(actor.organization === 'a' ? 4 : 5), contextId: id(200 + i),
    orgLevel: actor.orgLevel, wsLevel: actor.wsLevel,
  }));
  return { plan, rows };
}
function cohort() {
  const { plan, rows } = oracle();
  return bindIdentityReadback(plan, rows);
}
function zeros() {
  return [2, 3].map(n => ({ organization_id: id(n), total_rows: 0,
    system_rows: 0, user_rows: 0, other_rows: 0 }));
}
function personal() {
  return [{ id: id(300), user_id: id(100), organization_id: id(2),
    type: 'user', enabled: true, deleted: false }];
}

test('F09-01: candidate refusal precedes any signup or issuance', () => {
  assert.equal(F09_CANDIDATE.head, 'da4c81cc623f909ce38f53e01b557ee8374d5adb');
  assert.equal(F09_CANDIDATE.runtimeReady, false);
  assert.throws(() => requireF09Runtime(F09_CANDIDATE.head), /F09-CANDIDATE-733-ABSENT/);
  assert.throws(() => requireF09Runtime('0'.repeat(40)), /F09-CANDIDATE-UNBOUND/);
  assert.throws(() => requireF09Runtime({ runtimeReady: true }), /F09-CANDIDATE-UNBOUND/);
  assert.ok(Object.isFrozen(F09_CANDIDATE));
});

test('F09-02: two orgs each have distinct Owner, org Admin, Member and Viewer slots', () => {
  const plan = identityPlan(input());
  assert.deepEqual(plan.actors.map(a => a.slot), [
    'a.owner', 'a.admin', 'a.member', 'a.viewer', 'b.owner', 'b.admin', 'b.member', 'b.viewer',
  ]);
  assert.deepEqual(plan.actors.map(a => a.orgLevel), [15, 8, 3, 1, 15, 8, 3, 1]);
  // user-info returns effective workspace levels, not the stored membership level.
  assert.deepEqual(plan.actors.map(a => a.wsLevel), [15, 8, 3, 1, 15, 8, 3, 1]);
  assert.equal(new Set(plan.actors.map(a => a.label)).size, 8);
  assert.ok(Object.isFrozen(plan) && Object.isFrozen(plan.actors));
  assert.ok(plan.actors.every(Object.isFrozen));
  assert.ok(!JSON.stringify(plan).includes('secret'));
});

test('F09-03: browser, profile and fresh run are separate isolation dimensions', () => {
  const plans = [identityPlan(input()), identityPlan({ ...input(), browser: 'firefox' }),
    identityPlan({ ...input(), profile: 'opt-in' }), identityPlan({ ...input(), runId: id(9) })];
  assert.equal(new Set(plans.map(p => p.isolationId)).size, 4);
  assert.equal(new Set(plans.flatMap(p => p.actors.map(a => a.label))).size, 32);
  const { rows } = oracle();
  assert.throws(() => bindIdentityReadback(plans[1], rows), /F09-IDENTITY/);
});

for (const origin of ['https://example.invalid', 'http://127.0.0.1.evil.invalid',
  'http://2130706433', 'http://localhost@evil.invalid', 'http://localhost:8000/path',
  'http://localhost:8000?token=synthetic', 'http://localhost:8000#part', 'file:///tmp/local',
  'http://127.0.0.1:99999', 'http://user:synthetic@127.0.0.1:8000']) {
  test(`F09-04: refuses noncanonical origin ${origin}`, () => {
    assert.throws(() => identityPlan({ ...input(), apiOrigin: origin }), /F09-INPUT/);
    assert.throws(() => identityPlan({ ...input(), appOrigin: origin }), /F09-INPUT/);
  });
}

for (const extra of [
  { apiKey: 'synthetic-not-a-real-key' }, { secretKey: 'synthetic-not-a-real-secret' },
  { tokens: { access: 'synthetic' } }, { storageState: '/tmp/existing-profile' },
  { borrowOrgKey: true }, { autoMint: true }, { balance: 100 },
  { sql: 'UPDATE balances SET amount = 100' },
  { signature: 'tampered-synthetic-envelope' },
]) {
  test(`F09-05: rejects unowned credential, money or signed-envelope input ${Object.keys(extra)[0]}`, () => {
    assert.throws(() => identityPlan({ ...input(), ...extra }), error => {
      assert.equal((error as Error).message, 'F09-INPUT');
      return true;
    });
  });
}

test('F09-06: refuses malformed run/browser/profile instead of weakening freshness', () => {
  for (const bad of [{ runId: '' }, { browser: 'shared' }, { profile: '../saved' }, { profile: '' }]) {
    assert.throws(() => identityPlan({ ...input(), ...bad }), /F09-INPUT/);
  }
});

test('F09-07: exact eight-person readback, not an alias for workspace Admin', () => {
  const { plan, rows } = oracle();
  const bound = bindIdentityReadback(plan, rows);
  assert.equal(bound.actors.length, 8);
  assert.ok(Object.isFrozen(bound) && Object.isFrozen(bound.actors));
  rows[1].orgLevel = 3; // workspace level remains Admin: this is NOT an org Admin.
  assert.throws(() => bindIdentityReadback(plan, rows), /F09-IDENTITY/);
  assert.equal(bound.actors[1].orgLevel, 8); // Detached from the mutable readback.
});

for (const field of ['userId', 'contextId'] as const) {
  test(`F09-08: refuses reused ${field}`, () => {
    const { plan, rows } = oracle(); rows[1][field] = rows[0][field];
    assert.throws(() => bindIdentityReadback(plan, rows), /F09-IDENTITY/);
  });
}

test('F09-09: refuses missing/extra actors and foreign organization/workspace', () => {
  const { plan, rows } = oracle();
  assert.throws(() => bindIdentityReadback(plan, rows.slice(1)), /F09-IDENTITY/);
  assert.throws(() => bindIdentityReadback(plan, [...rows, rows[0]]), /F09-IDENTITY/);
  assert.throws(() => bindIdentityReadback(plan, rows.map((r, i) =>
    i === 1 ? { ...r, organizationId: id(3) } : r)), /F09-IDENTITY/);
  assert.throws(() => bindIdentityReadback(plan, rows.map((r, i) =>
    i === 1 ? { ...r, workspaceId: id(5) } : r)), /F09-IDENTITY/);
  assert.throws(() => bindIdentityReadback(plan, rows.map(r => ({ ...r, organizationId: id(2) }))), /F09-IDENTITY/);
});

test('F09-10: rejects credential-bearing readbacks and forged serialized handles', () => {
  const { plan, rows } = oracle();
  assert.throws(() => bindIdentityReadback(plan, rows.map(r => ({ ...r, secret_key: 'synthetic' }))), /F09-IDENTITY/);
  assert.throws(() => bindIdentityReadback(JSON.parse(JSON.stringify(plan)), rows), /F09-PLAN/);
  assert.throws(() => signupCensusQuery(JSON.parse(JSON.stringify(cohort()))), /F09-ROSTER/);
});

test('F09-11: census uses only bound org IDs and counts all keys, including disabled/deleted', () => {
  const query = signupCensusQuery(cohort());
  assert.deepEqual(query.values, [id(2), id(3)]);
  assert.match(query.text, /LEFT JOIN accounts_orgapikey/);
  assert.match(query.text, /COUNT\(k\.id\)/);
  assert.doesNotMatch(query.text, /\b(INSERT|UPDATE|DELETE|secret_key|api_key|ciphertext|enabled|deleted)\b/i);
  assert.doesNotMatch(query.text, /00000000/);
  assert.equal(assertZeroKeySignup(cohort(), zeros()).totalKeys, 0);
});

test('F09-12: census refuses any automatic key, omitted org, string count or secret field', () => {
  const roster = cohort();
  for (const field of ['total_rows', 'system_rows', 'user_rows', 'other_rows']) {
    const rows = zeros().map((row, i) => i === 0 ? { ...row, [field]: 1 } : row);
    assert.throws(() => assertZeroKeySignup(roster, rows), /F09-ZERO-KEY/);
  }
  for (const rows of [[], zeros().slice(1), [...zeros(), zeros()[0]],
    zeros().map(r => ({ ...r, total_rows: '0' })),
    zeros().map(r => ({ ...r, api_key: 'synthetic' })),
    zeros().map(r => ({ ...r, organization_id: id(99) }))]) {
    assert.throws(() => assertZeroKeySignup(roster, rows), /F09-ZERO-KEY/);
  }
});

test('F09-13: personal ownership projection selects metadata, never the key pair', () => {
  const roster = cohort(); const query = personalOwnershipQuery(roster, 'a.owner', id(300));
  assert.deepEqual(query.values, [id(2), id(300)]);
  assert.doesNotMatch(query.text, /\b(secret_key|api_key|ciphertext|INSERT|UPDATE|DELETE)\b/i);
  assert.equal(assertPersonalOwnership(roster, 'a.owner', id(300), personal()).keyId, id(300));
  assert.throws(() => personalOwnershipQuery(roster, 'a.workspaceAdmin', id(300)), /F09-KEY/);
  assert.throws(() => personalOwnershipQuery(roster, 'a.owner', "'; UPDATE balances"), /F09-KEY/);
});

for (const bad of [{ type: 'system' }, { type: 'mcp' }, { type: 'ingest' },
  { user_id: null }, { user_id: id(101) }, { organization_id: id(3) },
  { id: id(301) }, { enabled: false }, { deleted: true },
  { secret_key: 'synthetic-not-a-real-secret' }, { signature: 'tampered-synthetic' }]) {
  test(`F09-14: refuses borrowed/retired/unowned or credential-bearing key ${Object.keys(bad)[0]}`, () => {
    assert.throws(() => assertPersonalOwnership(cohort(), 'a.owner', id(300),
      [{ ...personal()[0], ...bad }]), /F09-KEY/);
  });
}

test('F09-15: absence or duplicate key rows is never successful ownership proof', () => {
  for (const rows of [[], [...personal(), ...personal()]]) {
    assert.throws(() => assertPersonalOwnership(cohort(), 'a.owner', id(300), rows), /F09-KEY/);
  }
});

test('F09-16: Owner effective workspace readback is 15, not stored membership 8', () => {
  const { plan, rows } = oracle();
  rows[0].wsLevel = 8;
  assert.throws(() => bindIdentityReadback(plan, rows), /F09-IDENTITY/);
});

test('F09-17: strict data input never invokes a getter or coerces an arbitrary object', () => {
  let calls = 0;
  const data = input();
  Object.defineProperty(data, 'browser', { get() { calls++; return 'chromium'; } });
  assert.throws(() => identityPlan(data), /F09-INPUT/);
  assert.throws(() => identityPlan({ ...input(), browser: {
    toString() { calls++; return 'chromium'; },
  } }), /F09-INPUT/);
  assert.equal(calls, 0);
  assert.throws(() => identityPlan({ ...input(), profile: 'synthetic-not-a-real-secret' }), /F09-INPUT/);
});
