// HR-JRN-01 child (child.config.ts; spawned by harness/value-free-disjoint.spec.ts, never run on its own): the
// J03 two-tenant actor set (the default Owner A, Member and Viewer, Owner B, and a B Member), provisioned the normal way by
// the ScopeActors fixture under the run switch. The fixture registers every value it mints, and everything Postgres then
// holds for those orgs, into the registry the parent gave the run (as the walk job gives one); the parent reads it back.
import { test, expect } from '../../lib/scope-actors';

test.use({ scopeRoster: { orgB: [3] }, trace: 'off', screenshot: 'off', video: 'off' });

test('value-free child: the J03 actor set, minted and registered', async ({ scopeActors: scopes }) => {
  expect(scopes.valueFree, 'value-free exactly when the run switch is set').not.toBeNull();
  expect(scopes.orgBMembers, 'the B Member').toHaveLength(1);
});
