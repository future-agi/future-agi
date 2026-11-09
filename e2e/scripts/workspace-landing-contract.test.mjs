// Source-contract tests, NOT browser/product evidence. Evaluate the exact Boolean
// guards in AUTH-E2E-004 against the review's counterexamples. Missing guards fail
// too: a correct fixture alone must not silently replace the flow's readbacks.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const path = new URL('../flows/auth/workspace-landing.spec.ts', import.meta.url);
const text = readFileSync(path, 'utf8');
const source = ts.createSourceFile(path.pathname, text, ts.ScriptTarget.Latest, true);
const guards = new Map();
function visit(node) {
  if (ts.isCallExpression(node) && node.expression.getText(source) === 'check' &&
      ts.isStringLiteral(node.arguments[0]) && node.arguments.length === 2) {
    const name = node.arguments[0].text;
    assert.ok(!guards.has(name), `duplicate landing checkpoint: ${name}`);
    guards.set(name, node.arguments[1].getText(source));
  }
  ts.forEachChild(node, visit);
}
visit(source);
function evaluate(name, scope) {
  assert.ok(guards.has(name), `missing discriminator: ${name}`);
  const code = ts.transpileModule(`globalThis.result = (${guards.get(name)});`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const context = vm.createContext({ ...scope });
  vm.runInContext(code, context, { timeout: 1000 });
  assert.equal(typeof context.result, 'boolean');
  return context.result;
}
const seeded = { org: 'org-a', foreignOrg: 'org-b', expected: 'allowed',
  workspaces: [{ id: 'allowed' }, { id: 'denied' }, { id: 'foreign' }] };
const actor = { userId: 'actor' };

test('SAML setup rejects matching persisted and signing organizations', () => {
  const name = 'saml_persisted_other_organization';
  const config = { currentOrganizationId: 'org-b', selected_organization_id: 'org-b' };
  assert.equal(evaluate(name, { config, seeded }), true);
  for (const key of ['currentOrganizationId', 'selected_organization_id']) {
    assert.equal(evaluate(name, { config: { ...config, [key]: 'org-a' }, seeded }), false);
  }
  assert.equal(evaluate(name, { config: {}, seeded }), false);
});

test('SAML readback cannot pass by rewriting persisted scope to the signing org', () => {
  const name = 'saml_persisted_scope_unchanged';
  assert.equal(evaluate(name, { config: { currentOrganizationId: 'org-b', selected_organization_id: 'org-b' }, seeded }), true);
  assert.equal(evaluate(name, { config: { currentOrganizationId: 'org-a', selected_organization_id: 'org-a' }, seeded }), false);
});

test('revoked preference must persist on all three preference surfaces', () => {
  const name = 'stale_persisted_revoked_workspace', denied = 'denied';
  const config = { currentWorkspaceId: denied, defaultWorkspaceId: denied, orgWorkspaceMap: { 'org-a': denied } };
  assert.equal(evaluate(name, { config, denied, seeded }), true);
  for (const key of ['currentWorkspaceId', 'defaultWorkspaceId', 'orgWorkspaceMap']) {
    assert.equal(evaluate(name, { config: { ...config, [key]: undefined }, denied, seeded }), false);
  }
});

test('nonexistent and foreign preferences cannot qualify the denied-local case', () => {
  const name = 'stale_existing_current_org', denied = 'denied';
  assert.equal(evaluate(name, { local: [{ id: denied }, { id: 'allowed' }], denied }), true);
  assert.equal(evaluate(name, { local: [{ id: 'allowed' }], denied }), false);
});

test('an active denied-workspace membership and a missing fallback are rejected', () => {
  const denied = 'denied';
  const memberships = new Map([[denied, []], ['allowed', [actor].map(a => ({ user_id: a.userId }))]]);
  assert.equal(evaluate('stale_membership_revoked', { memberships, denied, actor }), true);
  assert.equal(evaluate('stale_authorized_fallback', { memberships, denied, actor, seeded }), true);
  memberships.set(denied, [{ user_id: actor.userId }]);
  assert.equal(evaluate('stale_membership_revoked', { memberships, denied, actor }), false);
  memberships.set('allowed', []);
  assert.equal(evaluate('stale_authorized_fallback', { memberships, denied, actor, seeded }), false);
});

test('UUID tiebreak prerequisite rejects unequal microsecond membership times', () => {
  const name = 'uuid_tiebreak_requires_equal_membership_times';
  const first = { created_at: '2026-01-01 00:00:00.000001+00' };
  assert.equal(evaluate(name, { grants: [first, { ...first }] }), true);
  assert.equal(evaluate(name, { grants: [first, { created_at: '2026-01-01 00:00:00.000002+00' }] }), false);
  assert.equal(evaluate(name, { grants: [first] }), false);
});
