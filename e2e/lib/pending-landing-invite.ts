// J02 shared fixture, not browser assertions. Binding: r2 a0372df842d3
// issuer rbac_views.py:191-249,252-369; mail utils.py:486-559;
// UI invite-accepted.jsx:40-111. Expiry 0f047cc94a signup.py:718-808,
// organization_invite.py:78-108. Never reuse ScopeActors.invite (autoaccepts).
import { randomUUID } from 'node:crypto';
import type { ScopeActor } from './scope-actors';
import type { StateProbe } from './state-probe';
import { E2E } from './env';
import { landingPlan, holdLandingScopes, runLandingFixture } from './landing-fixtures';
import { localAppOrigin, mailCaptureBinding, type LocalMailCapture } from './local-mail-capture';

export interface OwnedInviteSnapshot {
  protectedDigest: string;
  categories: Record<string, string>;
  userId: string; userActive: boolean; status: string | null;
  isExpired: boolean; effectiveStatus: string | null; tokenValid: boolean;
  workspaceIds: string[]; activeWorkspaceIds: string[];
  grantsValid: boolean; orgPending: boolean; selectorsAbsent: boolean;
  inviteGrants: { workspace_id: string; level: number }[];
  inviteCount: number; systemKeyCount: number; keyCount: number; tokenCount: number;
}
function requireFixture(ok: unknown, reason: string): asserts ok {
  if (!ok) throw new Error(`pending landing invite refused: ${reason}`);
}
export function pendingInviteIdentity(nonce: string = randomUUID()) {
  if (!/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(nonce))
    throw new Error('pending landing invite requires UUID nonce');
  return { email: `e2e-${nonce}-invite@example.invalid`, password: `J02-${nonce}-Local!` };
}
export function assertPendingInviteSnapshot(s: OwnedInviteSnapshot, alternate: string, preferred: string) {
  requireFixture(!s.userActive && s.status === 'Pending' && s.inviteCount === 1 && !s.isExpired && s.tokenValid,
    'not genuinely pending');
  requireFixture(s.systemKeyCount === 0 && s.keyCount === 0 && s.tokenCount === 0, 'unexpected key or token mint');
  requireFixture(s.grantsValid && s.orgPending && s.selectorsAbsent && s.activeWorkspaceIds.length === 0 &&
    s.workspaceIds.length === 2 && s.workspaceIds.includes(alternate) && s.workspaceIds.includes(preferred), 'pending grants or selectors');
  requireFixture(s.inviteGrants.length === 2 && s.inviteGrants.every((g, i) =>
    g.workspace_id === [alternate, preferred][i] && g.level === 3), 'invitation grant order');
}

export async function seedPendingLandingInvite({ ownerA, ownerB, probe, capture, nonce }: {
  ownerA: ScopeActor; ownerB: ScopeActor; probe: StateProbe; capture: LocalMailCapture; nonce: string;
}) {
  const appOrigin = localAppOrigin(E2E.appUrl);
  localAppOrigin(E2E.apiUrl);
  requireFixture([ownerA, ownerB].every(a => a.email.startsWith('e2e-')) && ownerA.organizationId !== ownerB.organizationId,
    'owned distinct scopes required');
  const identity = pendingInviteIdentity(nonce), binding = mailCaptureBinding(capture);
  requireFixture(binding.nonce === nonce && binding.appOrigin === appOrigin && binding.recipients.includes(identity.email), 'mail binding');
  capture.cursor(); // Refuse a poisoned/closed capture BEFORE mutation.
  const plan = landingPlan(ownerA.organizationId, ownerB.organizationId, 'pending-invite', nonce);
  const preferredWorkspaceId = plan.expected!;
  const alternateWorkspaceId = plan.localWorkspaceIds.find(id => id !== preferredWorkspaceId)!;
  requireFixture(alternateWorkspaceId < preferredWorkspaceId, 'default must oppose UUID ordering');
  const input = { ...plan, owner: ownerA.userId, foreignOwner: ownerB.userId, email: identity.email,
    socketPath: binding.socketPath, appOrigin };
  // An independent PG freshness read, not a seed receipt or active-only manager.
  const fresh = await probe.pg<{ n: string }>('SELECT count(*)::text AS n FROM accounts_user WHERE lower(email)=lower($1)', [identity.email]);
  requireFixture(fresh[0]?.n === '0', 'identity already exists');
  // The synthetic bearer is stdin-only to the read-only identity binder, never
  // part of the undo receipt, command argv, inspector or cleanup input.
  let { undo } = runLandingFixture({ ...input, action: 'prepare-invite', issuerAccess: ownerA.tokens.access });
  const held = holdLandingScopes(plan);
  let disposed = false, readingOwner = false, deliveredHref = '', token = '';
  const snapshotAllRows = async (): Promise<OwnedInviteSnapshot> => {
    requireFixture(!disposed && !readingOwner && token, 'snapshot needs idle delivered pending identity');
    // Raw password/config/token/cache values are hashed inside Django; ONLY
    // digests, row counts and safety booleans leave its process, never mail.
    const snapshot = runLandingFixture<OwnedInviteSnapshot>({ ...input, action: 'inspect-invite', token, undo });
    requireFixture(snapshot.systemKeyCount === 0 && snapshot.keyCount === 0, 'unexpected key mint');
    return snapshot;
  };
  // Use these fixed public GETs for owner roster/member readbacks while this
  // fixture lives. Raw ScopeActors.send(ownerA, ...) would advance last_used_at
  // outside its receipt and must still fail cleanup, rather than be laundered.
  // No callback/arbitrary path/method, personal-key mint, auth bypass or DB repair.
  const ownerReadback = async <T>(workspaceId?: string): Promise<{ status: number; body: T }> => {
    requireFixture(!disposed && !readingOwner && token, 'owner readback needs idle delivered identity');
    held.assertTop();
    const { readback } = runLandingFixture<{ readback: string }>({ ...input, action: 'begin-owner-readback',
      undo, readWorkspace: workspaceId });
    readingOwner = true;
    let response: Response | undefined;
    try {
      try {
        const route = workspaceId === undefined ? '/accounts/workspaces/' : `/accounts/workspaces/${workspaceId}/members/`;
        response = await fetch(`${E2E.apiUrl}${route}`, { method: 'GET', redirect: 'error', signal: AbortSignal.timeout(30_000),
          headers: { Authorization: `Bearer ${ownerA.tokens.access}`, 'X-Organization-Id': ownerA.organizationId,
            'X-Workspace-Id': workspaceId ?? ownerA.workspaceId } });
        return { status: response.status, body: await response.json() as T };
      } catch {
        throw new Error('pending landing invite owner readback transport failed');
      } finally {
        ({ undo } = runLandingFixture({ ...input, action: 'seal-owner-readback', undo, readback,
          readSucceeded: response?.status === 200 }));
      }
    } finally { readingOwner = false; }
  };
  const dispose = async () => {
    if (disposed) return;
    requireFixture(!readingOwner, 'owner readback in progress');
    held.assertTop();
    // Retain a last no-write observation BEFORE deleting the owned identity.
    let observationFailed = false;
    if (token) { try { await snapshotAllRows(); } catch { observationFailed = true; } }
    runLandingFixture({ ...input, action: 'dispose-invite', undo });
    disposed = true; held.release();
    deliveredHref = ''; token = '';
    await capture.clear(identity.email);
    if (observationFailed) throw new Error('pending landing invite cleanup observed unsafe state');
  };
  try {
    const after = capture.cursor();
    // Normal owner's person JWT and explicit A scope; no API key provisioning.
    // Native fetch avoids Playwright API tracing of the rendered response link.
    let response: Response | undefined;
    try {
      response = await fetch(`${E2E.apiUrl}/accounts/organization/invite/`, {
        method: 'POST', redirect: 'error', signal: AbortSignal.timeout(30_000),
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${ownerA.tokens.access}`,
          'X-Organization-Id': ownerA.organizationId, 'X-Workspace-Id': ownerA.workspaceId },
        body: JSON.stringify({ emails: [identity.email], org_level: 3, workspace_access: [
          { workspace_id: alternateWorkspaceId, level: 3 }, { workspace_id: preferredWorkspaceId, level: 3 }] }),
      });
    } finally {
      // Validate ONLY the exact issuer last_used_at advance; all other token
      // rows/fields retain the pre-request baseline. No-op is allowed only for
      // a failed request. Freeze the full post-issuer token state before the
      // consumer can act, even when mail/result validation will subsequently fail.
      ({ undo } = runLandingFixture({ ...input, action: 'seal-issuer-auth', undo, issuerSucceeded: response?.status === 200 }));
    }
    requireFixture(response?.status === 200, 'issuer status');
    const body = await response.json() as { result?: { invited?: string[]; invites?: { email: string; invite_link: string }[] } };
    requireFixture(JSON.stringify(body.result?.invited) === JSON.stringify([identity.email]), 'issuer recipient result');
    const delivered = await capture.takeInvite({ recipient: identity.email, after, timeoutMs: 5000 });
    deliveredHref = delivered.href;
    const url = new URL(deliveredHref);
    requireFixture(url.origin === appOrigin, 'delivered origin');
    token = url.pathname.split('/').at(-1)!;
    const publicLinks = body.result?.invites?.filter(i => i.email === identity.email);
    if (publicLinks) requireFixture(publicLinks.length === 1 && publicLinks[0].invite_link === deliveredHref, 'public/delivered link equality');
    const pending = await snapshotAllRows();
    assertPendingInviteSnapshot(pending, alternateWorkspaceId, preferredWorkspaceId);
    const grants = await probe.pg<{ workspace_id: string; is_active: boolean; deleted: boolean; level: number }>(
      'SELECT workspace_id, is_active, deleted, level FROM accounts_workspacemembership WHERE user_id=$1 ORDER BY workspace_id', [pending.userId]);
    requireFixture(grants.length === 2 && grants.every(g => !g.is_active && !g.deleted && g.level === 3 &&
      [alternateWorkspaceId, preferredWorkspaceId].includes(g.workspace_id)), 'independent pending memberships');
    const expirePendingInvite = async () => {
      requireFixture(!disposed && !readingOwner, 'disposed or owner readback in progress');
      const before = await snapshotAllRows();
      runLandingFixture({ ...input, action: 'expire-invite', token, undo });
      const afterExpiry = await snapshotAllRows();
      requireFixture(afterExpiry.status === 'Pending' && afterExpiry.isExpired && afterExpiry.effectiveStatus === 'Expired' && afterExpiry.tokenValid,
        'deadline-only expiry did not preserve valid token');
      requireFixture(Object.keys(before.categories).filter(k => k !== 'invites').every(k => before.categories[k] === afterExpiry.categories[k]),
        'deadline control changed protected state');
    };
    return { invitee: { userId: pending.userId, ...identity }, org: plan.org, foreignOrg: plan.foreignOrg,
      preferredWorkspaceId, alternateWorkspaceId, foreignWorkspaceId: plan.foreignWorkspaceId,
      expectedWorkspaceId: preferredWorkspaceId,
      get deliveredHref() { requireFixture(!disposed && !readingOwner, 'disposed or owner readback in progress'); return deliveredHref; },
      snapshotAllRows, expirePendingInvite, backdatePendingInvitePastDeadline: expirePendingInvite,
      readOwnerRoster: <T = unknown>() => ownerReadback<T>(),
      readOwnerMembers: <T = unknown>(workspaceId: string) => ownerReadback<T>(workspaceId),
      async setPendingInviteStatus(status: 'Expired' | 'Cancelled') {
        requireFixture(!disposed && !readingOwner, 'disposed or owner readback in progress'); runLandingFixture({ ...input, action: 'status-invite', token, status, undo });
      },
      async removePendingInviteAuthority() {
        requireFixture(!disposed && !readingOwner, 'disposed or owner readback in progress'); runLandingFixture({ ...input, action: 'remove-invite', token, undo });
      },
      dispose,
    };
  } catch {
    await dispose();
    // Never propagate a fetch error, response body, URL or token in an exception.
    throw new Error('pending landing invite setup refused');
  }
}
