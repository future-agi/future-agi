import test from 'node:test';
import assert from 'node:assert/strict';
import { decideRoute, decideIdentity } from '../src/routes.mjs';

test('host workspace emergency authority is exact-route only and does not raise the role cap', () => {
  const member = { role: 'member', auth_method: 'session', org_id: 'org-a', workspace_id: 'ws-a', emergency_control: 'workspace' };
  for (const [method, path] of [['GET', '/workspace/kill-switch/status'], ['GET', '/workspace/kill-switch/operations/stop_123'], ['POST', '/workspace/kill-switch/workspace'], ['POST', '/workspace/kill-switch/workspace/resume']]) {
    const decision = decideRoute(method, path);
    assert.equal(decision.allowed, true);
    assert.equal(decideIdentity(decision, member).allowed, true, path);
    assert.equal(decideIdentity(decision, { ...member, emergency_control: undefined }).allowed, false, path);
    assert.equal(decideIdentity(decision, { ...member, auth_method: 'api_key' }).allowed, false, path);
  }
  for (const path of ['/workspace/kill-switch/organization', '/workspace/kill-switch/organization/resume']) {
    const decision = decideRoute('POST', path);
    assert.equal(decideIdentity(decision, member).allowed, false);
    assert.equal(decideIdentity(decision, { ...member, emergency_control: 'organization' }).allowed, false);
    assert.equal(decideIdentity(decision, { ...member, role: 'admin', emergency_control: 'organization' }).allowed, true);
  }
  assert.equal(decideIdentity(decideRoute('PUT', '/workspace/observe/settings'), member).allowed, false);
});
