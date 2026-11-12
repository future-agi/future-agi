// AUTH-E2E-005's data seam. Owned synthetic actors only; no replacement auth
// or fabricated assertion. SAML configuration uses the product's public upload.
import { createHash, randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { request } from '@playwright/test';
import type { ScopeActor } from './scope-actors';
import { E2E } from './env';
import { djangoFixtureCommand } from './django-fixture';
import { startLocalSamlIdp, type LocalSamlIdp } from './saml-idp';

export const LANDING_CASES = ['reversed-uuid', 'membership-order', 'is-default', 'stale-preference', 'cross-org-preference',
  'saml-scoped-default', 'owner-without-membership', 'member-without-membership'] as const;
export const LANDING_V2_CASES = ['saved-preference', 'legacy-current', 'configured-default', 'inactive-preference'] as const;
export type LandingCase = typeof LANDING_CASES[number] | typeof LANDING_V2_CASES[number] | 'pending-invite';
type WorkspaceSeed = { id: string; org: string; is_default: boolean; is_active?: boolean };
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const root = fileURLToPath(new URL('../../', import.meta.url));
const active = new Map<string, string[]>();

function legacyPlan(org: string, foreignOrg: string, scenario: typeof LANDING_CASES[number], nonce: string) {
  if (!uuid.test(org) || !uuid.test(foreignOrg) || org === foreignOrg || !LANDING_CASES.includes(scenario) || !uuid.test(nonce))
    throw new Error('landing fixture needs two distinct owned orgs, a known case and a UUID nonce');
  const id = (scope: string, prefix: string) => {
    const hex = createHash('sha256').update(`auth-e2e-004:${scope}:${scenario}:${nonce}`).digest('hex');
    return `${prefix}${hex.slice(1, 8)}-${hex.slice(8, 12)}-4${hex.slice(13, 16)}-8${hex.slice(17, 20)}-${hex.slice(20, 32)}`;
  };
  const high = id(org, 'f'), low = id(org, '1'), foreign = id(foreignOrg, 'f'), stale = id(org, 'e');
  // Insertion order is intentionally the opposite of lexical UUID order.
  const workspaces = [{ id: high, org, is_default: !['reversed-uuid', 'membership-order'].includes(scenario) },
    { id: low, org, is_default: false }, { id: foreign, org: foreignOrg, is_default: true }];
  const preference = scenario === 'stale-preference' ? stale :
    ['cross-org-preference', 'saml-scoped-default'].includes(scenario) ? foreign : null;
  const noMembership = scenario.endsWith('without-membership');
  const expected = scenario === 'member-without-membership' ? null : scenario === 'reversed-uuid' ? low : high;
  return { scenario, nonce, org, foreignOrg, workspaces, preference, noMembership, expected,
    samlOrgId: scenario === 'saml-scoped-default' ? org : null };
}

export function landingPlan(org: string, foreignOrg: string, scenario: LandingCase, nonce: string = randomUUID()) {
  if (!uuid.test(org) || !uuid.test(foreignOrg) || org === foreignOrg || !uuid.test(nonce) ||
      ![...LANDING_CASES, ...LANDING_V2_CASES, 'pending-invite'].includes(scenario))
    throw new Error('landing fixture requires distinct UUID scopes, UUID nonce and known case');
  const legacy = (LANDING_CASES as readonly string[]).includes(scenario);
  const ladder = ['saved-preference', 'legacy-current', 'configured-default'].includes(scenario);
  const id = (role: string, prefix: string) => {
    const h = createHash('sha256').update(`j02-v2:${org}:${foreignOrg}:${scenario}:${nonce}:${role}`).digest('hex');
    return `${prefix}${h.slice(1, 8)}-${h.slice(8, 12)}-4${h.slice(13, 16)}-8${h.slice(17, 20)}-${h.slice(20, 32)}`;
  };
  const saved = id('saved', '1'), current = id('current', '2'), configured = id('configured', '3');
  const isDefault = id('default', '4'), membershipOrder = id('order', '5'), foreign = id('foreign', 'e');
  const preferred = id('preferred', 'f'), alternate = id('alternate', '1');
  const base = legacy ? legacyPlan(org, foreignOrg, scenario as typeof LANDING_CASES[number], nonce) : {
    scenario, nonce, org, foreignOrg,
    workspaces: ladder ? [saved, current, configured, isDefault, membershipOrder].map(w =>
      ({ id: w, org, is_default: w === isDefault })).concat([{ id: foreign, org: foreignOrg, is_default: false }]) :
      [{ id: preferred, org, is_default: true },
        { id: alternate, org, is_default: false, ...(scenario === 'inactive-preference' ? { is_active: false } : {}) },
        { id: foreign, org: foreignOrg, is_default: false }],
    preference: scenario === 'inactive-preference' ? alternate : null,
    noMembership: scenario === 'pending-invite',
    expected: ladder ? (scenario === 'saved-preference' ? saved : scenario === 'legacy-current' ? current : configured) : preferred,
    samlOrgId: null,
  };
  const localWorkspaceIds = base.workspaces.filter(w => w.org === org).map(w => w.id);
  const foreignWorkspaceId = base.workspaces.find(w => w.org === foreignOrg)!.id;
  const selectors = ladder ? { saved: scenario === 'saved-preference' ? saved : null,
    current: scenario !== 'configured-default' ? current : null, configured } :
    { saved: base.preference, current: base.preference, configured: base.preference };
  const candidates = ladder ? { saved, current, configured, isDefault, membershipOrder } :
    { saved: base.preference, current: base.preference, configured: base.preference,
      isDefault: base.workspaces.find(w => w.org === org && w.is_default)?.id ?? null,
      membershipOrder: scenario === 'reversed-uuid' ? localWorkspaceIds[1] : localWorkspaceIds[0] };
  const membershipCreatedAt: Record<string, string> = {};
  if (ladder) localWorkspaceIds.forEach((w, i) => {
    membershipCreatedAt[w] = `2020-01-01T00:00:00.12345${w === membershipOrder ? 0 : i + 1}+00:00`;
  });
  if (scenario === 'reversed-uuid' || scenario === 'membership-order') localWorkspaceIds.forEach((w, i) => {
    membershipCreatedAt[w] = `2020-01-01T00:00:00.12345${6 + (scenario === 'membership-order' ? i : 0)}+00:00`;
  });
  // AUTH's retained stale-preference setup selects then revokes the low grant
  // before its pre-auth readback. These sets describe that boundary, not seeding:
  // legacyPlan and Python still seed BOTH memberships byte-semantically as before.
  const beforeAuth = base.noMembership ? [] : scenario === 'stale-preference' ? [localWorkspaceIds[0]] : [...localWorkspaceIds];
  return { ...base, workspaces: base.workspaces as WorkspaceSeed[],
    shape: (ladder ? 'precedence-ladder' : scenario === 'pending-invite' ? 'pending-invite' : 'legacy-pair') as
      'legacy-pair' | 'precedence-ladder' | 'pending-invite',
    localWorkspaceIds, foreignWorkspaceId, selectors, candidates, membershipCreatedAt,
    expectedActiveMembershipIdsBeforeAuth: beforeAuth,
    expectedActiveMembershipIdsAfterAuth: scenario === 'pending-invite' ? [...localWorkspaceIds] : [...beforeAuth],
    foreignMembershipExpected: scenario === 'saml-scoped-default', wrongExpectedWorkspaceId: foreignWorkspaceId };
}

export function holdLandingScopes(plan: { org: string; foreignOrg: string; nonce: string }) {
  const scopes = [plan.org, plan.foreignOrg];
  for (const org of scopes) active.set(org, [...(active.get(org) ?? []), plan.nonce]);
  const assertTop = () => {
    if (scopes.some(org => active.get(org)?.at(-1) !== plan.nonce))
      throw new Error('dispose overlapping landing fixtures in reverse seed order');
  };
  return { assertTop, release() {
    assertTop();
    for (const org of scopes) {
      const stack = active.get(org)!; stack.pop(); if (!stack.length) active.delete(org);
    }
  } };
}

export type LandingPlan = ReturnType<typeof landingPlan>;
export type LandingPlanV2 = LandingPlan & { dispose(): Promise<void> };
export type InactiveMembershipFault = { remove(): Promise<void> };
export type LandingFixture = LandingPlan & {
  /** Digests cover unfiltered full-precision rows, never resolver output. */
  snapshotAllRows(): Promise<{ protectedDigest: string; categories: Record<string, string> }>;
  /** A11 only: insert ONE inactive owned row; caller chooses login/reload timing.
   * No active access, auth, config or expected-landing override. Remove verifies
   * the exact inserted row and restores the all-row baseline; disposal removes it. */
  injectInactiveMembership(): Promise<InactiveMembershipFault>;
  /** The local signer whose metadata was uploaded; use it for the real login. */
  samlIdp?: LocalSamlIdp;
  /** Idempotent; overlapping fixtures on the same org must be disposed LIFO. */
  dispose(): Promise<void>;
};

export function runLandingFixture<T = { undo: string }>(input: object): T {
  const env = { ...process.env }; delete env.DOCKER_CONTEXT;
  let out: string;
  try {
    out = execFileSync(root + 'bin/e2e', ['compose', 'exec', '-T', process.env.E2E_STACK === 'standalone' ? 'app' : 'backend',
      'python', '-O', 'manage.py', 'shell', '-c',
      djangoFixtureCommand(readFileSync(root + 'e2e/lib/landing_fixture.py', 'utf8'))],
    { input: JSON.stringify(input), env, encoding: 'utf8', timeout: 60_000, stdio: ['pipe', 'pipe', 'pipe'] });
  } catch (error) {
    // Child errors embed stdin/stdout and synthetic IDs. Keep them out of reports;
    // retain only the Python exception class, never its message or arguments.
    const stderr = String((error as { stderr?: Buffer }).stderr ?? '');
    const kind = [...stderr.matchAll(/^([A-Za-z]+(?:Error|Exception)):/gm)].at(-1)?.[1] ?? 'process';
    throw new Error(`landing fixture command failed (${kind}); no database receipt`);
  }
  const result = out.split('\n').find(line => line.startsWith('E2E-LANDING '));
  if (!result) throw new Error('landing fixture produced no receipt');
  return JSON.parse(result.slice('E2E-LANDING '.length)) as T;
}

/** Fresh ScopeActors only, never a shared flow org. Always dispose in finally.
 * Repeated cases get independent IDs/names. SAML needs an unconfigured test org
 * (dispose its previous SAML fixture first); existing IdPs are never replaced. */
export async function seedLandingFixture(owner: ScopeActor, foreignOwner: ScopeActor, actor: ScopeActor,
  scenario: LandingCase): Promise<LandingFixture> {
  if (![E2E.apiUrl, E2E.appUrl].every(url => ['localhost', '127.0.0.1'].includes(new URL(url).hostname)))
    throw new Error('landing fixtures are loopback-only');
  if (actor.organizationId !== owner.organizationId || ![owner, foreignOwner, actor].every(a => a.email.startsWith('e2e-')))
    throw new Error('landing fixture requires owned synthetic actors');
  if ((scenario === 'owner-without-membership') !== (actor.userId === owner.userId))
    throw new Error('only owner-without-membership uses the Owner actor');
  if (scenario === 'pending-invite') throw new Error('pending-invite requires the fresh public issuer fixture');
  const plan = landingPlan(owner.organizationId, foreignOwner.organizationId, scenario);
  const input = { ...plan, owner: owner.userId, foreignOwner: foreignOwner.userId, actor: actor.userId };
  const { undo } = runLandingFixture({ ...input, action: 'seed' });
  const held = holdLandingScopes(plan);
  let restored = false, stopped = false, inactiveFault: InactiveMembershipFault | undefined;
  const assertLive = () => {
    if (restored) throw new Error('landing fixture is disposed');
    held.assertTop();
  };
  const fixture: LandingFixture = { ...plan,
    async snapshotAllRows() {
      assertLive();
      return runLandingFixture<{ protectedDigest: string; categories: Record<string, string> }>({ ...input, action: 'snapshot' });
    },
    async injectInactiveMembership() {
      assertLive();
      if (scenario !== 'member-without-membership') throw new Error('inactive membership fault requires member-without-membership');
      if (inactiveFault) throw new Error('inactive membership fault already held');
      const fault = runLandingFixture<{ rowDigest: string; baseline: string }>({ ...input, action: 'inject-inactive' });
      let removed = false;
      const handle: InactiveMembershipFault = { async remove() {
        if (removed) return;
        assertLive();
        runLandingFixture({ ...input, action: 'remove-inactive', fault });
        removed = true; inactiveFault = undefined;
      } };
      inactiveFault = handle;
      return handle;
    },
    async dispose(): Promise<void> {
      if (!restored) {
        held.assertTop();
        await inactiveFault?.remove();
        runLandingFixture({ ...input, action: 'dispose', undo });
        restored = true;
        held.release();
      }
      if (!stopped) { await fixture.samlIdp?.stop(); stopped = true; }
    },
  };
  try {
    if (plan.samlOrgId) {
      fixture.samlIdp = await startLocalSamlIdp();
      const api = await request.newContext({ baseURL: E2E.apiUrl });
      try {
        const response = await api.post('/saml2_auth/idp-uploads/', {
          maxRedirects: 0,
          headers: { Authorization: `Bearer ${owner.tokens.access}`,
            'X-Organization-Id': owner.organizationId, 'X-Workspace-Id': owner.workspaceId },
          multipart: { file: { name: 'e2e-idp.xml', mimeType: 'application/xml', buffer: Buffer.from(fixture.samlIdp.metadata) },
            name: `e2e-landing-${plan.nonce}`, identity_type: '2', is_enabled: 'true' },
        });
        if (response.status() !== 200) throw new Error(`landing SAML upload failed: HTTP ${response.status()}`);
      } finally { await api.dispose(); }
    }
    return fixture;
  } catch (error) {
    await fixture.dispose();
    throw error;
  }
}
