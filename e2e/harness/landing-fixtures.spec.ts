import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test, expect } from '@playwright/test';
import { test as scopeTest, type ScopeActor, type ScopeActors } from '../lib/scope-actors';
import { LANDING_CASES, LANDING_V2_CASES, landingPlan, seedLandingFixture, type LandingFixture } from '../lib/landing-fixtures';
import { djangoFixtureCommand } from '../lib/django-fixture';
import type { StateProbe } from '../lib/state-probe';
const A = '11111111-1111-4111-8111-111111111111';
const B = '22222222-2222-4222-8222-222222222222';
const NONCE = '33333333-3333-4333-8333-333333333333';
const root = fileURLToPath(new URL('../../', import.meta.url));
const digest = (value: unknown) => createHash('sha256').update(JSON.stringify(value)).digest('hex');

// Independent PG reads, including inactive/deleted rows and every grant field.
// Neither the seed receipt nor its undo data is an assertion oracle. Hash full
// snapshots in assertions so a failing report does not print IDs or user config.
async function snapshot(probe: StateProbe, actors: ScopeActors, actor: ScopeActor) {
  const orgs = [actors.ownerA.organizationId, actors.ownerB.organizationId];
  // JSON keeps PG's full timestamp precision; the pg driver's Date conversion
  // would hide lost microseconds during restore.
  const read = async <T = Record<string, any>>(sql: string): Promise<T[]> =>
    (await probe.pg<{ row: T }>(sql, [orgs])).map(result => result.row);
  const workspaces = await read<{ id: string; organization_id: string; is_default: boolean; name: string }>(
    'SELECT to_jsonb(w) AS row FROM accounts_workspace w WHERE organization_id = ANY($1::uuid[]) ORDER BY id');
  const memberships = await read('SELECT to_jsonb(m) AS row FROM accounts_workspacemembership m JOIN accounts_workspace w ON w.id=m.workspace_id WHERE w.organization_id=ANY($1::uuid[]) ORDER BY m.id');
  const orgMemberships = await read('SELECT to_jsonb(m) AS row FROM accounts_organization_membership m WHERE organization_id=ANY($1::uuid[]) ORDER BY id');
  const saml = await read('SELECT to_jsonb(s) AS row FROM saml_meta s WHERE organization_id=ANY($1::uuid[]) ORDER BY id');
  const config = await probe.pg('SELECT config FROM accounts_user WHERE id=$1', [actor.userId]);
  const audit = await read('SELECT to_jsonb(a) AS row FROM accounts_audit_log a WHERE organization_id=ANY($1::uuid[]) ORDER BY id');
  return { workspaces, memberships, orgMemberships, saml, config, audit };
}

for (const scenario of LANDING_CASES) {
  test(`offline landing fixture: ${scenario} has a nonce-scoped deterministic oracle`, () => {
    const p = landingPlan(A, B, scenario, NONCE);
    expect(p).toEqual(landingPlan(A, B, scenario, NONCE));
    expect(p.workspaces[0].id > p.workspaces[1].id).toBe(true);
    expect(new Set(p.workspaces.map(w => w.id)).size).toBe(3);
    expect(p.workspaces[0].is_default).toBe(!['reversed-uuid', 'membership-order'].includes(scenario));
    if (scenario === 'stale-preference') expect(p.workspaces.some(w => w.id === p.preference)).toBe(false);
    if (scenario === 'cross-org-preference' || scenario === 'saml-scoped-default')
      expect(p.preference).toBe(p.workspaces[2].id);
    expect(p.samlOrgId).toBe(scenario === 'saml-scoped-default' ? A : null);
    expect(p.noMembership).toBe(scenario.endsWith('without-membership'));
    if (scenario === 'member-without-membership') expect(p.expected).toBeNull();
    else expect(p.expected).toBe(p.workspaces[scenario === 'reversed-uuid' ? 1 : 0].id);
    const again = landingPlan(A, B, scenario);
    expect(again.workspaces.some(w => p.workspaces.some(old => old.id === w.id))).toBe(false);
  });

  scopeTest(`landing fixture live: ${scenario} seeds twice and restores every changed row`, async ({ scopeActors, scopeProbe }) => {
    scopeTest.setTimeout(240_000);
    const actor = scenario === 'owner-without-membership' ? scopeActors.ownerA : scopeActors.member;
    const before = await snapshot(scopeProbe, scopeActors, actor);
    const oldIds = new Set(before.workspaces.map(w => w.id));
    const oldMemberships = before.memberships.filter(m => m.user_id === actor.userId &&
      before.workspaces.some(w => w.id === m.workspace_id && w.organization_id === actor.organizationId));
    expect(oldMemberships.length).toBeGreaterThan(0); // Non-vacuous restoration proof.
    expect(before.workspaces.filter(w => w.is_default)).toHaveLength(2);
    const previous = new Set<string>();
    for (let attempt = 0; attempt < 2; attempt++) {
      const fixture = await seedLandingFixture(scopeActors.ownerA, scopeActors.ownerB, actor, scenario);
      try {
        const during = await snapshot(scopeProbe, scopeActors, actor);
        const created = during.workspaces.filter(w => !oldIds.has(w.id));
        expect(created).toHaveLength(3);
        expect(created.some(w => previous.has(w.id))).toBe(false);
        created.forEach(w => previous.add(w.id));
        const local = created.filter(w => w.organization_id === actor.organizationId).sort((a, b) => a.id.localeCompare(b.id));
        const foreign = created.filter(w => w.organization_id === scopeActors.ownerB.organizationId);
        expect(local).toHaveLength(2); expect(foreign).toHaveLength(1);
        expect(local.map(w => w.is_default)).toEqual([false, !['reversed-uuid', 'membership-order'].includes(scenario)]);
        expect(foreign[0].is_default).toBe(true);
        expect(during.workspaces.filter(w => oldIds.has(w.id)).every(w => !w.is_default)).toBe(true);
        expect(during.memberships.some(m => oldMemberships.some(old => old.id === m.id))).toBe(false);
        const localGrants = during.memberships.filter(m => m.user_id === actor.userId && local.some(w => w.id === m.workspace_id));
        expect(localGrants).toHaveLength(scenario.endsWith('without-membership') ? 0 : 2);
        expect(localGrants.every(m => m.role === 'workspace_member' && m.level === 3 && m.is_active)).toBe(true);
        if (scenario === 'reversed-uuid' || scenario === 'membership-order') {
          // Compare PG timestamp text, never JS Date (which drops microseconds).
          const ordered = await scopeProbe.pg<{ workspace_id: string; created_at: string }>(
            `SELECT workspace_id, created_at::text AS created_at FROM accounts_workspacemembership
             WHERE user_id=$1 AND workspace_id=ANY($2::uuid[]) AND is_active AND NOT deleted
             ORDER BY created_at, workspace_id`, [actor.userId, local.map(w => w.id)]);
          expect(ordered).toHaveLength(2);
          expect(ordered[0].created_at === ordered[1].created_at).toBe(scenario === 'reversed-uuid');
          expect(ordered[0].workspace_id === fixture.expected).toBe(true);
          expect(local.every(w => !w.is_default)).toBe(true);
        }
        const config = during.config[0].config;
        if (['saml-scoped-default', 'cross-org-preference'].includes(scenario)) {
          expect(config.defaultWorkspaceId === foreign[0].id).toBe(true);
        } else if (scenario === 'stale-preference') {
          expect(typeof config.defaultWorkspaceId).toBe('string');
          expect(during.workspaces.some(w => w.id === config.defaultWorkspaceId)).toBe(false);
        } else expect(config.defaultWorkspaceId).toBeUndefined();
        expect(config.currentWorkspaceId === config.defaultWorkspaceId).toBe(true);
        expect(digest(config.orgWorkspaceMap)).toBe(digest(config.defaultWorkspaceId ? { [actor.organizationId]: config.defaultWorkspaceId } : {}));
        const expected = scenario === 'member-without-membership' ? null : local[scenario === 'reversed-uuid' ? 0 : 1].id;
        expect(fixture.expected === expected).toBe(true);
        if (scenario === 'saml-scoped-default') {
          expect(during.saml).toHaveLength(1);
          const idp = during.saml[0];
          expect(idp.organization_id === actor.organizationId && idp.is_enabled && !idp.deleted).toBe(true);
          expect(idp.identity_type).toBe(2);
          expect(idp.meta === fixture.samlIdp?.metadata).toBe(true);
          // The default belongs to the configured SAML org, not the preferred
          // foreign org, where this actor really has access (not a fake pointer).
          expect(local.some(w => w.organization_id === idp.organization_id && w.is_default)).toBe(true);
          expect(during.memberships.filter(m => m.user_id === actor.userId && m.workspace_id === foreign[0].id)).toHaveLength(1);
          expect(during.orgMemberships.filter(m => m.user_id === actor.userId && m.organization_id === foreign[0].organization_id)).toHaveLength(1);
        } else expect(during.saml).toHaveLength(0);
      } finally { await fixture.dispose(); }
      expect(digest(await snapshot(scopeProbe, scopeActors, actor))).toBe(digest(before));
      await fixture.dispose(); // Idempotent, including IdP process shutdown.
      expect(digest(await snapshot(scopeProbe, scopeActors, actor))).toBe(digest(before));
    }
  });
}

scopeTest('landing fixture live: overlapping seeds have disjoint IDs and names, with LIFO rollback', async ({ scopeActors, scopeProbe }) => {
  const actor = scopeActors.member;
  const before = await snapshot(scopeProbe, scopeActors, actor);
  const first = await seedLandingFixture(scopeActors.ownerA, scopeActors.ownerB, actor, 'is-default');
  let second: LandingFixture | undefined;
  try {
    const middle = await snapshot(scopeProbe, scopeActors, actor);
    second = await seedLandingFixture(scopeActors.ownerA, scopeActors.ownerB, actor, 'is-default');
    const during = await snapshot(scopeProbe, scopeActors, actor);
    const created = during.workspaces.filter(w => !before.workspaces.some(old => old.id === w.id));
    expect(created).toHaveLength(6);
    expect(new Set(created.map(w => w.id)).size).toBe(6);
    expect(new Set(created.map(w => w.name)).size).toBe(6);
    await expect(first.dispose()).rejects.toThrow('reverse seed order');
    expect(digest(await snapshot(scopeProbe, scopeActors, actor))).toBe(digest(during));
    await second.dispose();
    expect(digest(await snapshot(scopeProbe, scopeActors, actor))).toBe(digest(middle));
  } finally { await second?.dispose(); await first.dispose(); }
  expect(digest(await snapshot(scopeProbe, scopeActors, actor))).toBe(digest(before));
});

scopeTest('landing fixture live: failed public SAML configuration rolls back the data seed', async ({ scopeActors, scopeProbe }) => {
  const before = await snapshot(scopeProbe, scopeActors, scopeActors.member);
  const owner = { ...scopeActors.ownerA, tokens: { access: 'invalid-fixture-session', refresh: '' } };
  await expect(seedLandingFixture(owner, scopeActors.ownerB, scopeActors.member, 'saml-scoped-default'))
    .rejects.toThrow('landing SAML upload failed: HTTP 401');
  expect(digest(await snapshot(scopeProbe, scopeActors, scopeActors.member))).toBe(digest(before));
});

scopeTest('landing fixture live: Python -O preserves owner, role and synthetic-user safety checks', async ({ scopeActors, scopeProbe }) => {
  const before = await snapshot(scopeProbe, scopeActors, scopeActors.member);
  const input = { ...landingPlan(scopeActors.ownerA.organizationId, scopeActors.ownerB.organizationId, 'is-default'),
    action: 'seed', owner: scopeActors.ownerA.userId, foreignOwner: scopeActors.ownerB.userId, actor: scopeActors.member.userId };
  const source = readFileSync(root + 'e2e/lib/landing_fixture.py', 'utf8');
  // Same real Django/DB implementation, no mock safety predicate. A temporary
  // non-synthetic member is rolled back even if a guard incorrectly succeeds.
  const checks = `
args = json.load(sys.stdin)
for guard in ('owner', 'role', 'synthetic'):
    with transaction.atomic():
        candidate = dict(args)
        if guard == 'owner':
            candidate['owner'] = args['actor']
        if guard == 'role':
            candidate['actor'] = args['owner']
        if guard == 'synthetic':
            # Keep the owner/actor lookups valid; change the separate Viewer.
            outsider = OrganizationMembership.no_workspace_objects.filter(organization_id=args['org']).exclude(user_id__in=[args['owner'], args['actor']]).first()
            if outsider is None:
                raise RuntimeError('safety test needs the Viewer member')
            User.objects.filter(pk=outsider.user_id).update(email='non-synthetic@fixture.invalid')
            candidate['foreignOwner'] = args['foreignOwner']
        rejected = False
        try:
            apply(candidate)
        except ValueError as error:
            expected = {'owner': 'not the owned test org', 'role': 'actor role does not match case', 'synthetic': 'landing org contains a non-synthetic user'}[guard]
            if str(error) != expected:
                raise RuntimeError('wrong guard rejected the case') from None
            rejected = True
        transaction.set_rollback(True)
    if not rejected:
        raise RuntimeError('optimized interpreter disabled a safety guard')
print('E2E-SAFETY 3')
`;
  const body = source.replace("print('E2E-LANDING ' + json.dumps(apply(json.load(sys.stdin))))", checks);
  const env = { ...process.env }; delete env.DOCKER_CONTEXT;
  let passed = false;
  try {
    const out = execFileSync(root + 'bin/e2e', ['compose', 'exec', '-T', process.env.E2E_STACK === 'standalone' ? 'app' : 'backend',
      'python', '-O', 'manage.py', 'shell', '-c', djangoFixtureCommand(body)],
    { input: JSON.stringify(input), env, encoding: 'utf8', timeout: 60_000, stdio: ['pipe', 'pipe', 'pipe'] });
    passed = out.includes('E2E-SAFETY 3');
  } catch { /* no child output or raw IDs in the report */ }
  expect(passed).toBe(true);
  expect(digest(await snapshot(scopeProbe, scopeActors, scopeActors.member))).toBe(digest(before));
});

test('offline landing fixture: membership order is independent of UUID and defaults', () => {
  const p = landingPlan(A, B, 'membership-order', NONCE);
  expect(p.workspaces.slice(0, 2).map(w => w.is_default)).toEqual([false, false]);
  expect(p.workspaces[0].id > p.workspaces[1].id).toBe(true);
  expect(p.expected).toBe(p.workspaces[0].id);
  expect(p.preference).toBeNull();
  expect(p.noMembership).toBe(false);
});

test('landing fixture live: expanded shapes preserve all eight legacy obligations', () => {
  // Golden legacy projection at d844a026da; adding fields may not change a seed.
  // This is an offline compatibility oracle; the eight live loops above stay intact.
  const seeds = [
    'e4940f421af9fde80743828b198e93892e5f3624dcbd76a9fca6b105ac868ae2',
    'a25b5c4f72c4e8b3af902fcaf6696c8c16bd7b515b0fbac4b683425fbda4957c',
    'b98fa03ffa6452af0214558fc7e9d3c34cb7f69e89059aa2e7f5a7442f3caf96',
    'b55b84a52658d55b433ba2f259ca7388141ea6d311f7a826bf160621bee5b148',
    '281e19d98582b2cc06653ccb95bcbb64136182579c416e7f6be1dc50c1f320da',
    'f35f93564c51dc8fece7d2a3c42f485ec2e689d9476aae7a5ee4521b779becd6',
    'b8138c8d0da8176c5fa0fa3258f635939a84f8a937448ffc18954a8f5bda88c9',
    '9e673aff31ab99c90bdb515fa3c2ccbef7a4e9fa0ff9f95ffdf3dc83542c4059',
  ];
  LANDING_CASES.forEach((scenario, i) => {
    const p = landingPlan(A, B, scenario, NONCE);
    const { nonce, org, foreignOrg, workspaces, preference, noMembership, expected, samlOrgId } = p;
    const projection = { scenario, nonce, org, foreignOrg, workspaces, preference, noMembership, expected, samlOrgId };
    expect(digest(projection)).toBe(seeds[i]);
    expect(digest({ ...projection, expected: p.foreignWorkspaceId })).not.toBe(seeds[i]);
  });
});

test('offline landing fixture: precedence-ladder has five independent priority candidates', () => {
  const winners = { 'saved-preference': 'saved', 'legacy-current': 'current', 'configured-default': 'configured' } as const;
  for (const [scenario, role] of Object.entries(winners)) {
    const p = landingPlan(A, B, scenario as keyof typeof winners, NONCE);
    expect(p.shape).toBe('precedence-ladder');
    expect(p.workspaces).toHaveLength(6);
    expect(new Set(p.workspaces.map(w => w.id)).size).toBe(6);
    expect(p.localWorkspaceIds).toHaveLength(5);
    expect(new Set(Object.values(p.candidates)).size).toBe(5);
    expect(p.expected === p.candidates[role]).toBe(true);
    expect(p.workspaces.filter(w => w.org === A && w.is_default).map(w => w.id)).toEqual([p.candidates.isDefault]);
    expect(p.candidates.membershipOrder === [...p.localWorkspaceIds].sort()[0]).toBe(false);
    expect([...p.localWorkspaceIds].sort((a, b) => p.membershipCreatedAt[a].localeCompare(p.membershipCreatedAt[b]))[0]).toBe(p.candidates.membershipOrder);
    expect(p.expectedActiveMembershipIdsBeforeAuth).toEqual(p.localWorkspaceIds);
    expect(p.expectedActiveMembershipIdsAfterAuth).toEqual(p.localWorkspaceIds);
    expect(p.foreignMembershipExpected).toBe(false);
    expect(p.selectors).toEqual({
      saved: scenario === 'saved-preference' ? p.candidates.saved : null,
      current: scenario !== 'configured-default' ? p.candidates.current : null,
      configured: p.candidates.configured,
    });
    expect(landingPlan(A, B, scenario as keyof typeof winners).workspaces.some(w => p.workspaces.some(v => v.id === w.id))).toBe(false);
  }
});

test('offline landing fixture: each scenario has an unequal foreign wrong-expectation oracle', () => {
  for (const scenario of [...LANDING_CASES, ...LANDING_V2_CASES, 'pending-invite'] as const) {
    const p = landingPlan(A, B, scenario, NONCE);
    expect(p.wrongExpectedWorkspaceId === p.foreignWorkspaceId).toBe(true);
    expect(p.expected === p.wrongExpectedWorkspaceId).toBe(false);
    expect(p.localWorkspaceIds.includes(p.foreignWorkspaceId)).toBe(false);
    expect(p.expected === null || p.localWorkspaceIds.includes(p.expected)).toBe(true);
  }
});

test('offline landing fixture: revoked preference sets describe the pre-auth transition, not the seed', () => {
  const p = landingPlan(A, B, 'stale-preference', NONCE);
  // The legacy seed still creates both grants; the consumer selects then revokes
  // the low one BEFORE its pre-auth readback. Never repair this by changing seed.
  expect(p.localWorkspaceIds).toEqual(p.workspaces.slice(0, 2).map(w => w.id));
  expect(p.noMembership).toBe(false);
  expect(p.expectedActiveMembershipIdsBeforeAuth).toEqual([p.workspaces[0].id]);
  expect(p.expectedActiveMembershipIdsAfterAuth).toEqual([p.workspaces[0].id]);
  expect(p.expected).toBe(p.workspaces[0].id);
  expect(p.expectedActiveMembershipIdsBeforeAuth.includes(p.workspaces[1].id)).toBe(false);
});

scopeTest('landing fixture live: revoked preference public transition matches exact V2 sets', async ({ scopeActors, scopeProbe }) => {
  const actor = scopeActors.member;
  const before = await snapshot(scopeProbe, scopeActors, actor);
  const f = await seedLandingFixture(scopeActors.ownerA, scopeActors.ownerB, actor, 'stale-preference');
  const [remaining, revoked] = f.localWorkspaceIds;
  const active = async () => (await scopeProbe.pg<{ workspace_id: string }>(
    `SELECT m.workspace_id FROM accounts_workspacemembership m JOIN accounts_workspace w ON w.id=m.workspace_id
     WHERE m.user_id=$1 AND w.organization_id=$2 AND m.is_active AND NOT m.deleted ORDER BY m.workspace_id`,
    [actor.userId, f.org])).map(m => m.workspace_id);
  try {
    expect(await active()).toEqual([...f.localWorkspaceIds].sort());
    const selected = await scopeActors.send(scopeActors.withWorkspace(actor, remaining), 'POST',
      '/accounts/workspace/switch/', { new_workspace_id: revoked });
    expect(selected.status).toBe(200);
    const removed = await scopeActors.send(scopeActors.withWorkspace(scopeActors.ownerA, revoked), 'DELETE',
      `/accounts/workspaces/${revoked}/members/${actor.userId}/`);
    expect(removed.status).toBe(200);
    expect(await active()).toEqual([remaining]);
    expect(await active()).toEqual([...f.expectedActiveMembershipIdsBeforeAuth].sort());
    const during = await snapshot(scopeProbe, scopeActors, actor);
    expect(during.config[0].config.orgWorkspaceMap[f.org]).toBe(revoked);
    expect(during.config[0].config.currentWorkspaceId).toBe(revoked);
    expect(during.config[0].config.defaultWorkspaceId).toBe(revoked);
    for (const workspaceId of f.localWorkspaceIds) {
      const read = await scopeActors.send<{ result: { members: { user_id: string }[] } }>(
        scopeActors.withWorkspace(scopeActors.ownerA, workspaceId), 'GET', `/accounts/workspaces/${workspaceId}/members/`);
      expect(read.status).toBe(200);
      expect(read.body.result.members.some(m => m.user_id === actor.userId)).toBe(workspaceId === remaining);
    }
    expect(f.expectedActiveMembershipIdsAfterAuth).toEqual([remaining]); // Actual browser login remains WS-LANDING's gate.
  } finally { await f.dispose(); }
  const restored = await snapshot(scopeProbe, scopeActors, actor);
  for (const category of ['workspaces', 'memberships', 'orgMemberships', 'saml', 'config'] as const)
    expect(digest(restored[category])).toBe(digest(before[category]));
  // A public revoke may append its legitimate audit; never erase that evidence.
  expect(digest(restored.audit.filter(row => before.audit.some(old => old.id === row.id)))).toBe(digest(before.audit));
});

test('offline landing fixture: inactive preference is not a missing grant', () => {
  const p = landingPlan(A, B, 'inactive-preference', NONCE);
  expect(p.localWorkspaceIds).toHaveLength(2);
  expect(p.workspaces.find(w => w.id === p.selectors.saved)?.is_active).toBe(false);
  expect(p.expectedActiveMembershipIdsBeforeAuth).toEqual(p.localWorkspaceIds);
  expect(p.expected === p.workspaces.find(w => w.org === A && w.is_active !== false)?.id).toBe(true);
});

scopeTest('landing fixture live: precedence-ladder selectors and exact five grants are restored', async ({ scopeActors, scopeProbe }) => {
  scopeTest.setTimeout(300_000);
  const before = await snapshot(scopeProbe, scopeActors, scopeActors.member);
  for (const scenario of LANDING_V2_CASES) {
    for (let repeat = 0; repeat < 2; repeat++) {
      const p = await seedLandingFixture(scopeActors.ownerA, scopeActors.ownerB, scopeActors.member, scenario);
      try {
        const during = await snapshot(scopeProbe, scopeActors, scopeActors.member);
        const created = during.workspaces.filter(w => !before.workspaces.some(old => old.id === w.id));
        expect(created.length).toBe(p.shape === 'precedence-ladder' ? 6 : 3);
        expect(created.map(w => w.id).sort()).toEqual(p.workspaces.map(w => w.id).sort());
        const foreignIds = before.workspaces.filter(w => w.organization_id === p.foreignOrg).map(w => w.id);
        expect(digest(during.workspaces.filter(w => foreignIds.includes(w.id)))).toBe(digest(before.workspaces.filter(w => foreignIds.includes(w.id))));
        expect(digest(during.memberships.filter(m => foreignIds.includes(m.workspace_id)))).toBe(digest(before.memberships.filter(m => foreignIds.includes(m.workspace_id))));
        const grants = during.memberships.filter(m => m.user_id === scopeActors.member.userId &&
          during.workspaces.some(w => w.id === m.workspace_id && w.organization_id === p.org));
        expect(grants.map(m => m.workspace_id).sort()).toEqual([...p.expectedActiveMembershipIdsBeforeAuth].sort());
        expect(grants.every(m => m.is_active && !m.deleted && m.level === 3 && m.role === 'workspace_member')).toBe(true);
        const config = during.config[0].config;
        expect(config.orgWorkspaceMap?.[p.org] ?? null).toBe(p.selectors.saved);
        expect(config.currentWorkspaceId ?? null).toBe(p.selectors.current);
        expect(config.defaultWorkspaceId ?? null).toBe(p.selectors.configured);
        if (p.shape === 'precedence-ladder') {
          const times = await scopeProbe.pg<{ workspace_id: string; stamp: string }>(
            `SELECT workspace_id, to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"') AS stamp
             FROM accounts_workspacemembership WHERE user_id=$1 AND workspace_id=ANY($2::uuid[])`,
            [scopeActors.member.userId, p.localWorkspaceIds]);
          expect(times.length).toBe(5);
          expect(times.every(t => t.stamp === p.membershipCreatedAt[t.workspace_id])).toBe(true);
        }
      } finally { await p.dispose(); }
      expect(digest(await snapshot(scopeProbe, scopeActors, scopeActors.member))).toBe(digest(before));
      await p.dispose();
    }
  }
});

// Shared source primitive only. WS-LANDING invokes it at the actual after-login
// and before-reload-snapshot boundaries, keeping those browser assertions owned
// by AUTH. These two arms prove independent fresh insert/remove lifecycles.
for (const boundary of ['after-login', 'before-reload-snapshot'] as const) {
  scopeTest(`landing fixture live: A11 inactive fault ${boundary} changes all rows but not access`, async ({ scopeActors, scopeProbe }) => {
    const actor = scopeActors.member;
    const before = await snapshot(scopeProbe, scopeActors, actor);
    const f = await seedLandingFixture(scopeActors.ownerA, scopeActors.ownerB, actor, 'member-without-membership');
    const read = async () => scopeProbe.pg<{ row: { is_active: boolean; deleted: boolean; workspace_id: string; level: number; role: string } }>(
      'SELECT to_jsonb(m) AS row FROM accounts_workspacemembership m WHERE user_id=$1 ORDER BY id', [actor.userId]);
    try {
      const baseline = await f.snapshotAllRows(), rows = await read();
      expect(rows.length).toBe(0); expect(f.expected).toBeNull();
      const fault = await f.injectInactiveMembership();
      const injected = await read();
      expect(injected.length).toBe(1);
      expect(injected.every(({ row }) => !row.is_active && !row.deleted && row.level === 3 &&
        row.role === 'workspace_member' && f.localWorkspaceIds.includes(row.workspace_id))).toBe(true);
      expect((await f.snapshotAllRows()).protectedDigest === baseline.protectedDigest).toBe(false);
      expect(injected.filter(({ row }) => row.is_active && !row.deleted).length).toBe(0);
      await expect(f.injectInactiveMembership()).rejects.toThrow('inactive membership fault already held');
      await fault.remove(); await fault.remove();
      expect(digest(await read())).toBe(digest(rows));
      expect((await f.snapshotAllRows()).protectedDigest === baseline.protectedDigest).toBe(true);
      // Disposal also removes a still-held owned fault before restoring old rows.
      await f.injectInactiveMembership();
    } finally { await f.dispose(); }
    expect(digest(await snapshot(scopeProbe, scopeActors, actor))).toBe(digest(before));
    await expect(f.injectInactiveMembership()).rejects.toThrow('landing fixture is disposed');
  });
}
scopeTest('landing fixture live: A11 fault refuses other cases and non-top fixtures', async ({ scopeActors, scopeProbe }) => {
  const actor = scopeActors.member;
  const first = await seedLandingFixture(scopeActors.ownerA, scopeActors.ownerB, actor, 'member-without-membership');
  let second: LandingFixture | undefined;
  try {
    second = await seedLandingFixture(scopeActors.ownerA, scopeActors.ownerB, actor, 'is-default');
    const baseline = await snapshot(scopeProbe, scopeActors, actor);
    await expect(first.injectInactiveMembership()).rejects.toThrow('reverse seed order');
    await expect(second.injectInactiveMembership()).rejects.toThrow('inactive membership fault requires member-without-membership');
    expect(digest(await snapshot(scopeProbe, scopeActors, actor))).toBe(digest(baseline));
  } finally { await second?.dispose(); await first.dispose(); }
});

scopeTest('landing fixture live: A11 removal refuses a modified fault without erasing evidence', async ({ scopeActors, scopeProbe }) => {
  const actor = scopeActors.member;
  const f = await seedLandingFixture(scopeActors.ownerA, scopeActors.ownerB, actor, 'member-without-membership');
  const baseline = await f.snapshotAllRows();
  const fault = await f.injectInactiveMembership();
  try {
    await scopeProbe.pg('UPDATE accounts_workspacemembership SET level=1 WHERE user_id=$1 AND workspace_id=$2',
      [actor.userId, f.localWorkspaceIds[0]]);
    await expect(fault.remove()).rejects.toThrow('landing fixture command failed (ValueError)');
    const rows = await scopeProbe.pg<{ level: number; is_active: boolean }>(
      'SELECT level, is_active FROM accounts_workspacemembership WHERE user_id=$1 AND workspace_id=$2',
      [actor.userId, f.localWorkspaceIds[0]]);
    expect(rows.length === 1 && rows[0].level === 1 && !rows[0].is_active).toBe(true);
  } finally {
    await scopeProbe.pg('UPDATE accounts_workspacemembership SET level=3 WHERE user_id=$1 AND workspace_id=$2',
      [actor.userId, f.localWorkspaceIds[0]]);
    try {
      await fault.remove();
      expect((await f.snapshotAllRows()).protectedDigest === baseline.protectedDigest).toBe(true);
    } finally { await f.dispose(); }
  }
});

test('offline landing fixture: rejects unknown cases, non-distinct scopes and invalid nonces', () => {
  expect(() => landingPlan(A, A, 'is-default')).toThrow();
  expect(() => landingPlan('bad', B, 'is-default')).toThrow();
  expect(() => landingPlan(A, B, 'unknown' as any)).toThrow();
  expect(() => landingPlan(A, B, 'is-default', 'bad')).toThrow();
  expect(landingPlan(A, B, 'is-default', NONCE).workspaces[0].id)
    .not.toBe(landingPlan(A, B, 'stale-preference', NONCE).workspaces[0].id);
});
