// Additive AUTH assertions; all mutations/restore and all-row readers are imported.
import type { Page } from '@playwright/test';
import type { ScopeActor } from '../../lib/scope-actors';
import type { StateProbe } from '../../lib/state-probe';
import type { LandingFixture, InactiveMembershipFault } from '../../lib/landing-fixtures';
import { digest, sameSet, readBrowserLanding, type Check, type Info, type WorkspaceList } from './workspace-landing.browser';

export interface Grant {
  workspace_id: string; org: string; is_active: boolean; deleted: boolean; level: number; role: string;
  workspace_active: boolean; workspace_deleted: boolean; created_at: string;
}
export async function readGrants(probe: StateProbe, userId: string) {
  // No active-only manager: independently distinguish an inactive workspace from
  // an inactive/deleted membership, retaining PostgreSQL microsecond precision.
  return probe.pg<Grant>(`SELECT m.workspace_id, w.organization_id AS org, m.is_active, m.deleted,
    m.level, m.role, w.is_active AS workspace_active, w.deleted AS workspace_deleted,
    to_char(m.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') || '+00:00' AS created_at
    FROM accounts_workspacemembership m JOIN accounts_workspace w ON w.id=m.workspace_id
    WHERE m.user_id=$1 ORDER BY m.workspace_id`, [userId]);
}
export async function foreignRows(probe: StateProbe, org: string) {
  // Hash full row JSON in the consumer; PG serializes dates without JS Date loss.
  const rows = await probe.pg<{ kind: string; rows: string }>(`SELECT 'workspaces' AS kind,
    coalesce(jsonb_agg(to_jsonb(w) ORDER BY w.id)::text,'[]') AS rows FROM accounts_workspace w WHERE w.organization_id=$1
    UNION ALL SELECT 'memberships', coalesce(jsonb_agg(to_jsonb(m) ORDER BY m.id)::text,'[]')
    FROM accounts_workspacemembership m JOIN accounts_workspace w ON w.id=m.workspace_id WHERE w.organization_id=$1
    UNION ALL SELECT 'organization', coalesce(jsonb_agg(to_jsonb(o) ORDER BY o.id)::text,'[]')
    FROM accounts_organization o WHERE o.id=$1`, [org]);
  return digest(rows);
}
export function checkGrants(fixture: LandingFixture, grants: Grant[], wanted: string[], check: Check, phase: string) {
  const active = grants.filter(g => g.org === fixture.org && g.is_active && !g.deleted);
  check(`${phase}_named_active_memberships`, sameSet(active.map(g => g.workspace_id), wanted));
  check(`${phase}_membership_roles`, active.every(g => g.level === 3 && g.role === 'workspace_member'));
  const foreign = grants.filter(g => g.org === fixture.foreignOrg && g.is_active && !g.deleted);
  check(`${phase}_foreign_memberships`, sameSet(foreign.map(g => g.workspace_id),
    fixture.foreignMembershipExpected ? [fixture.foreignWorkspaceId] : []));
  return active.filter(g => g.workspace_active && !g.workspace_deleted).map(g => g.workspace_id);
}
export async function prepareLandingChecks(fixture: LandingFixture, actor: ScopeActor, probe: StateProbe, check: Check) {
  check('unequal_foreign_wrong_oracle', fixture.wrongExpectedWorkspaceId === fixture.foreignWorkspaceId &&
    fixture.wrongExpectedWorkspaceId !== fixture.expected);
  const beforeGrants = await readGrants(probe, actor.userId);
  checkGrants(fixture, beforeGrants, fixture.expectedActiveMembershipIdsBeforeAuth, check, 'before_auth');
  if (['reversed-uuid', 'membership-order', 'is-default'].includes(fixture.scenario)) {
    const rows = await probe.pg<{ config: Record<string, any> }>('SELECT config FROM accounts_user WHERE id=$1', [actor.userId]);
    const config = rows[0]?.config;
    check('no_higher_priority_selectors', Boolean(config) && !config.currentWorkspaceId &&
      !config.defaultWorkspaceId && !config.orgWorkspaceMap?.[fixture.org]);
    const defaults = await probe.pg<{ id: string }>(`SELECT id FROM accounts_workspace WHERE organization_id=$1
      AND is_active AND NOT deleted AND is_default ORDER BY id`, [fixture.org]);
    check('independent_default_position', sameSet(defaults.map(w => w.id), fixture.scenario === 'is-default' ? [fixture.expected!] : []));
  }
  if (fixture.scenario === 'membership-order') {
    const local = beforeGrants.filter(g => g.org === fixture.org);
    const first = local.find(g => g.workspace_id === fixture.candidates.membershipOrder);
    check('unequal_full_precision_membership_times', local.length === 2 && Boolean(first) &&
      local.every(g => g.workspace_id === first!.workspace_id || first!.created_at < g.created_at) &&
      local.every(g => g.created_at === fixture.membershipCreatedAt[g.workspace_id]));
    check('earliest_opposes_uuid_order', first!.workspace_id !== [...fixture.localWorkspaceIds].sort()[0]);
  }
  const foreign = await foreignRows(probe, fixture.foreignOrg);
  const baseline = await fixture.snapshotAllRows();
  const noMembership = fixture.scenario === 'member-without-membership';
  const faultAt = process.env.E2E_LANDING_INACTIVE_WRITE;
  if (faultAt !== undefined && !['after-login', 'after-reload'].includes(faultAt)) throw new Error('AUTH004_UNKNOWN_INACTIVE_CONTROL');
  let fault: InactiveMembershipFault | undefined;
  const noWrites = async (phase: string) => {
    const snapshot = await fixture.snapshotAllRows();
    // Authentication legitimately changes user/token rows; A11 compares ALL
    // membership rows, including inactive/deleted, not those auth categories.
    check(`no_membership_writes_${phase}`, snapshot.categories.memberships === baseline.categories.memberships &&
      snapshot.categories.orgMemberships === baseline.categories.orgMemberships);
  };
  const postGrants = async (phase: string) => {
    const grants = await readGrants(probe, actor.userId);
    const eligible = checkGrants(fixture, grants, fixture.expectedActiveMembershipIdsAfterAuth, check, phase);
    check(`${phase}_foreign_victim_unchanged`, await foreignRows(probe, fixture.foreignOrg) === foreign);
    return eligible;
  };
  const noAccess = async (page: Page, phase: string) => {
    const readback = await readBrowserLanding(page), info = readback.me.body as Info;
    const roster = (readback.roster.body as WorkspaceList).result;
    check(`${phase}_null_and_empty_despite_inactive_fault`, readback.me.status === 200 && readback.roster.status === 200 &&
      info.id === actor.userId && info.organization.id === fixture.org && info.default_workspace_id === null &&
      roster.total === 0 && roster.workspaces.length === 0);
  };
  return {
    async afterLogin(page: Page) {
      if (noMembership && faultAt === 'after-login') fault = await fixture.injectInactiveMembership();
      if (noMembership) { await noAccess(page, 'after_login'); await noWrites('after_login'); }
      await postGrants('after_auth');
    },
    async afterReload(page: Page) {
      if (noMembership && faultAt === 'after-reload') fault = await fixture.injectInactiveMembership();
      if (noMembership) { await noAccess(page, 'after_reload'); await noWrites('after_reload'); }
      const eligible = await postGrants('after_reload');
      const readback = await readBrowserLanding(page), info = readback.me.body as Info;
      const roster = (readback.roster.body as WorkspaceList).result;
      check('reload_independent_info_200', readback.me.status === 200 && readback.roster.status === 200);
      check('reload_independent_landing', info.id === actor.userId && info.organization.id === fixture.org &&
        info.default_workspace_id === fixture.expected && info.default_workspace_id !== fixture.foreignWorkspaceId);
      if (fixture.scenario !== 'owner-without-membership') check('reload_independent_access',
        sameSet(roster.workspaces.map(w => w.id), eligible) && roster.total === eligible.length);
    },
    async finish() {
      try {
        // Observe before removing the planted fault or disposing fixture rows,
        // including when login, reload or a named assertion failed.
        if (noMembership) await noWrites('finally');
        check('finally_foreign_victim_unchanged', await foreignRows(probe, fixture.foreignOrg) === foreign);
      } finally {
        if (fault) { await fault.remove(); await noWrites('fault_restored'); }
      }
    },
  };
}
