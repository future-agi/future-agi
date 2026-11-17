import { isUuid, loopbackOrigin, record } from './validation.ts';

export interface PlannedActor {
  readonly slot: string;
  readonly organization: 'a' | 'b';
  readonly role: 'owner' | 'admin' | 'member' | 'viewer';
  readonly orgLevel: number;
  readonly wsLevel: number;
  readonly label: string;
}
export interface IdentityPlan {
  readonly kind: 'f09-identity-plan';
  readonly runId: string;
  readonly browser: 'chromium' | 'firefox' | 'webkit';
  readonly profile: 'stock' | 'opt-in';
  readonly apiOrigin: string;
  readonly appOrigin: string;
  readonly isolationId: string;
  readonly actors: readonly PlannedActor[];
}
export interface IdentityReadback {
  readonly slot: string;
  readonly isolationId: string;
  readonly userId: string;
  readonly organizationId: string;
  readonly workspaceId: string;
  readonly contextId: string;
  readonly orgLevel: number;
  readonly wsLevel: number;
}
export interface IdentityRoster {
  readonly isolationId: string;
  readonly actors: readonly IdentityReadback[];
}
const plans = new WeakSet<object>();
const rosters = new WeakSet<object>();
const roles = [
  // user-info exposes the effective level (max(org, workspace)), not the stored role.
  { role: 'owner', orgLevel: 15, wsLevel: 15 },
  { role: 'admin', orgLevel: 8, wsLevel: 8 },
  { role: 'member', orgLevel: 3, wsLevel: 3 },
  { role: 'viewer', orgLevel: 1, wsLevel: 1 },
] as const;

/** Allocates NO users, contexts, sessions or keys. A fresh run UUID is mandatory.
 * Profile is an isolation label, not an assertion that a runtime admits it. */
export function identityPlan(input: unknown): IdentityPlan {
  const data = record(input, ['runId', 'browser', 'profile', 'apiOrigin', 'appOrigin'], 'F09-INPUT');
  if (!isUuid(data.runId) || typeof data.browser !== 'string'
    || !['chromium', 'firefox', 'webkit'].includes(data.browser)
    || (data.profile !== 'stock' && data.profile !== 'opt-in')) throw new Error('F09-INPUT');
  const apiOrigin = loopbackOrigin(data.apiOrigin), appOrigin = loopbackOrigin(data.appOrigin);
  const browser = data.browser as IdentityPlan['browser'];
  const isolationId = `f09:${data.runId}:${browser}:${data.profile}`;
  const actors = (['a', 'b'] as const).flatMap(organization => roles.map(({ role, orgLevel, wsLevel }) =>
    Object.freeze({ slot: `${organization}.${role}`, organization, role, orgLevel, wsLevel,
      label: `e2e-f09-${data.runId}-${browser}-${data.profile}-${organization}-${role}` })));
  const plan: IdentityPlan = Object.freeze({ kind: 'f09-identity-plan', runId: data.runId,
    browser, profile: data.profile, apiOrigin, appOrigin, isolationId, actors: Object.freeze(actors) });
  plans.add(plan);
  return plan;
}

/** Shape/identity oracle, NOT proof of signup, membership persistence or browser
 * isolation. A future admitted adapter must supply fresh real readbacks. */
export function bindIdentityReadback(plan: IdentityPlan, input: unknown): IdentityRoster {
  if (!plan || !plans.has(plan)) throw new Error('F09-PLAN');
  if (!Array.isArray(input) || input.length !== plan.actors.length) throw new Error('F09-IDENTITY');
  const bySlot = new Map<string, Record<string, unknown>>();
  for (const item of input) {
    const row = record(item, ['slot', 'isolationId', 'userId', 'organizationId', 'workspaceId',
      'contextId', 'orgLevel', 'wsLevel'], 'F09-IDENTITY');
    if (typeof row.slot !== 'string' || bySlot.has(row.slot)) throw new Error('F09-IDENTITY');
    bySlot.set(row.slot, row);
  }
  const actors = plan.actors.map(expected => {
    const row = bySlot.get(expected.slot);
    if (!row || row.isolationId !== plan.isolationId || row.orgLevel !== expected.orgLevel
      || row.wsLevel !== expected.wsLevel || !isUuid(row.userId) || !isUuid(row.organizationId)
      || !isUuid(row.workspaceId) || !isUuid(row.contextId)) throw new Error('F09-IDENTITY');
    return Object.freeze({ slot: expected.slot, isolationId: plan.isolationId,
      userId: row.userId, organizationId: row.organizationId, workspaceId: row.workspaceId,
      contextId: row.contextId, orgLevel: expected.orgLevel, wsLevel: expected.wsLevel });
  });
  if (new Set(actors.map(a => a.userId)).size !== 8 || new Set(actors.map(a => a.contextId)).size !== 8)
    throw new Error('F09-IDENTITY');
  const [a, b] = [actors[0], actors[4]];
  if (a.organizationId === b.organizationId || a.workspaceId === b.workspaceId) throw new Error('F09-IDENTITY');
  for (const [i, actor] of actors.entries()) {
    const owner = i < 4 ? a : b;
    if (actor.organizationId !== owner.organizationId || actor.workspaceId !== owner.workspaceId)
      throw new Error('F09-IDENTITY');
  }
  const roster = Object.freeze({ isolationId: plan.isolationId, actors: Object.freeze(actors) });
  rosters.add(roster);
  return roster;
}

// Internal branding prevents accidentally substituting a deserialized receipt.
// It is NOT authentication: callers can build synthetic oracle inputs in tests.
export function assertRoster(roster: IdentityRoster): void {
  if (!roster || !rosters.has(roster)) throw new Error('F09-ROSTER');
}
