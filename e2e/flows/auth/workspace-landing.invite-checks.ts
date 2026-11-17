// Read-only consumer predicates, using the shared pending fixture's bounded
// owner readers (plain owner GETs would illegally rebaseline token timestamps).
import type { seedPendingLandingInvite, OwnedInviteSnapshot } from '../../lib/pending-landing-invite';
import { readGrants } from './workspace-landing.assertions';
import { sameSet, digest, type Consumer, type Check, type WorkspaceList, type Members } from './workspace-landing.browser';
export type Pending = Awaited<ReturnType<typeof seedPendingLandingInvite>>;

export function noInviteWrites(before: OwnedInviteSnapshot, after: OwnedInviteSnapshot, check: Check, phase: string) {
  check(`${phase}_all_rows_and_cache_unchanged`, before.protectedDigest === after.protectedDigest &&
    digest(before.categories) === digest(after.categories));
  check(`${phase}_cache_reader_present`, typeof before.categories.cache === 'string' && before.categories.cache.length > 0);
}
export async function readInviteState(f: Pending, c: Consumer, check: Check, accepted: boolean) {
  const snapshot = await f.snapshotAllRows();
  const planned = [f.alternateWorkspaceId, f.preferredWorkspaceId];
  check('fixed_default_opposes_first_grant_and_uuid', f.expectedWorkspaceId === f.preferredWorkspaceId &&
    f.preferredWorkspaceId > f.alternateWorkspaceId && !planned.includes(f.foreignWorkspaceId));
  check('invite_state', snapshot.userActive === accepted && snapshot.status === (accepted ? 'Accepted' : 'Pending') &&
    snapshot.inviteCount === 1 && !snapshot.isExpired && snapshot.grantsValid);
  check('exact_invite_grants', sameSet(snapshot.workspaceIds, planned) &&
    sameSet(snapshot.activeWorkspaceIds, accepted ? planned : []) && snapshot.inviteGrants.length === 2 &&
    snapshot.inviteGrants.every((g, i) => g.workspace_id === planned[i] && g.level === 3));
  check('zero_automatic_keys', snapshot.systemKeyCount === 0 && snapshot.keyCount === 0);
  if (!accepted) check('fresh_pending_identity', snapshot.tokenValid && snapshot.orgPending && snapshot.selectorsAbsent && snapshot.tokenCount === 0);
  const users = await c.probe.pg<{ is_active: boolean; config: Record<string, any> }>(
    'SELECT is_active, config FROM accounts_user WHERE id=$1', [f.invitee.userId]);
  check('independent_user_activation', users.length === 1 && users[0].is_active === accepted);
  if (accepted) {
    const config = users[0].config;
    check('accepted_organization_selectors', config.currentOrganizationId === f.org && config.selected_organization_id === f.org);
    check('accepted_workspace_selectors_authorized', [config.currentWorkspaceId, config.defaultWorkspaceId, config.orgWorkspaceMap?.[f.org]]
      .every(id => !id || planned.includes(id)));
  }
  const orgs = await c.probe.pg<{ organization_id: string; is_active: boolean; deleted: boolean; level: number }>(
    'SELECT organization_id, is_active, deleted, level FROM accounts_organization_membership WHERE user_id=$1', [f.invitee.userId]);
  check('independent_exact_org_grant', orgs.length === 1 && orgs[0].organization_id === f.org &&
    orgs[0].level === 3 && !orgs[0].deleted && orgs[0].is_active === accepted);
  const grants = await readGrants(c.probe, f.invitee.userId);
  check('independent_exact_two_grants', sameSet(grants.map(g => g.workspace_id), planned) && grants.every(g => g.org === f.org &&
    g.is_active === accepted && !g.deleted && g.role === 'workspace_member' && g.level === 3 && g.workspace_active && !g.workspace_deleted));
  const flags = await c.probe.pg<{ id: string; is_default: boolean }>(
    'SELECT id, is_default FROM accounts_workspace WHERE id=ANY($1::uuid[]) ORDER BY id', [planned]);
  check('independent_fixed_default', flags.length === 2 && flags.every(w => w.is_default === (w.id === f.preferredWorkspaceId)));
  const roster = await f.readOwnerRoster<WorkspaceList>();
  check('bounded_owner_roster_200', roster.status === 200 && roster.body.result.total === roster.body.result.workspaces.length);
  const local = roster.body.result.workspaces;
  check('owner_roster_contains_planned_not_foreign', planned.every(id => local.some(w => w.id === id)) && !local.some(w => w.id === f.foreignWorkspaceId));
  const publicGrants: string[] = [];
  for (const workspace of local) {
    const response = await f.readOwnerMembers<Members>(workspace.id);
    check('bounded_owner_members_200', response.status === 200 && response.body.result.workspace.id === workspace.id &&
      response.body.result.total === response.body.result.members.length);
    if (response.body.result.members.some(m => m.user_id === f.invitee.userId)) publicGrants.push(workspace.id);
  }
  check('public_exact_invitee_grants', sameSet(publicGrants, accepted ? planned : []));
  // Seal all legitimate bounded owner GETs before any no-write bracket begins.
  await f.snapshotAllRows();
  return local;
}
