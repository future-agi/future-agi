// The four packet additions stay in AUTH-E2E-005's existing scenario loop.
import { writeFileSync } from 'node:fs';
import type { BrowserContext } from '@playwright/test';
import { LANDING_V2_CASES, seedLandingFixture, type LandingCase, type LandingFixture } from '../../lib/landing-fixtures';
import { WalkBoundary } from '../../lib/walk/boundary';
import { E2E } from '../../lib/env';
import { readGrants, checkGrants, foreignRows } from './workspace-landing.assertions';
import { checker, wrongOracle, sameSet, digest, passwordLogin, assertLanding, LANDING, WORKSPACES, UI_READY,
  type Consumer, type CaseRow, type WorkspaceList, type Members } from './workspace-landing.browser';

export async function runV2LandingCase(scenario: LandingCase, c: Consumer): Promise<CaseRow> {
  const row: CaseRow = { scenario, browser: c.browserName, control: wrongOracle(), checks: [], phase: 'seed', status: 'not-run' };
  const check = checker(row), boundary = new WalkBoundary(`AUTH004-${scenario}-${c.browserName}`);
  let fixture: LandingFixture | undefined, context: BrowserContext | undefined, victim: string | undefined;
  try {
    check('known_v2_case', (LANDING_V2_CASES as readonly string[]).includes(scenario));
    fixture = await seedLandingFixture(c.scopes.ownerA, c.scopes.ownerB, c.scopes.member, scenario);
    const f = fixture, actor = c.scopes.member;
    const expected = row.control ? f.wrongExpectedWorkspaceId : f.expected;
    check('unequal_foreign_wrong_oracle', f.wrongExpectedWorkspaceId === f.foreignWorkspaceId && f.expected !== f.wrongExpectedWorkspaceId);
    victim = await foreignRows(c.probe, f.foreignOrg);
    row.phase = 'setup-readback';
    const configs = await c.probe.pg<{ config: Record<string, any> }>('SELECT config FROM accounts_user WHERE id=$1', [actor.userId]);
    check('persisted_user_exists', configs.length === 1);
    const config = configs[0].config;
    check('selector_tuple', (config.orgWorkspaceMap?.[f.org] || null) === f.selectors.saved &&
      (config.currentWorkspaceId || null) === f.selectors.current && (config.defaultWorkspaceId || null) === f.selectors.configured);
    const workspaces = await c.probe.pg<{ id: string; organization_id: string; is_active: boolean; deleted: boolean; is_default: boolean }>(
      'SELECT id, organization_id, is_active, deleted, is_default FROM accounts_workspace WHERE id=ANY($1::uuid[]) ORDER BY id',
      [[...f.localWorkspaceIds, f.foreignWorkspaceId]]);
    check('named_workspace_set', sameSet(workspaces.map(w => w.id), [...f.localWorkspaceIds, f.foreignWorkspaceId]));
    check('workspace_flags', workspaces.every(w => {
      const planned = f.workspaces.find(p => p.id === w.id)!;
      return w.organization_id === planned.org && !w.deleted && w.is_active === (planned.is_active !== false) && w.is_default === planned.is_default;
    }));
    const grants = await readGrants(c.probe, actor.userId);
    const eligible = checkGrants(f, grants, f.expectedActiveMembershipIdsBeforeAuth, check, 'before_auth');
    check('no_extra_membership_rows', grants.length === f.expectedActiveMembershipIdsBeforeAuth.length);
    if (f.shape === 'precedence-ladder') {
      const { saved, current, configured, isDefault, membershipOrder } = f.candidates;
      const roles = [saved!, current!, configured!, isDefault!, membershipOrder!];
      check('five_distinct_priority_positions', new Set(roles).size === 5 && sameSet(roles, f.localWorkspaceIds));
      check('five_active_authorized_positions', sameSet(eligible, roles));
      const independentWinner = scenario === 'saved-preference' ? saved : scenario === 'legacy-current' ? current : configured;
      check('fixed_priority_winner', f.expected === independentWinner && independentWinner !== isDefault && independentWinner !== membershipOrder);
      check('one_distinct_default', sameSet(workspaces.filter(w => w.organization_id === f.org && w.is_default).map(w => w.id), [isDefault!]));
      check('full_precision_membership_times', grants.every(g => g.created_at === f.membershipCreatedAt[g.workspace_id]));
      const earliest = grants.find(g => g.workspace_id === membershipOrder)!;
      check('earliest_is_distinct_not_lowest_uuid', Boolean(earliest) && membershipOrder !== [...f.localWorkspaceIds].sort()[0] &&
        grants.every(g => g.workspace_id === membershipOrder || earliest.created_at < g.created_at));
    } else {
      check('inactive_preference_exists_with_active_membership', workspaces.some(w => w.id === f.selectors.saved && !w.is_active) &&
        grants.some(g => g.workspace_id === f.selectors.saved && g.is_active && !g.deleted));
      check('inactive_excluded_from_access', !eligible.includes(f.selectors.saved!) && sameSet(eligible, [f.expected!]));
    }
    const ownerRoster = await c.scopes.send<WorkspaceList>(c.scopes.ownerA, 'GET', WORKSPACES);
    check('owner_roster_200', ownerRoster.status === 200);
    const local = ownerRoster.body.result.workspaces;
    check('seeded_local_active_set', f.localWorkspaceIds.filter(id => workspaces.find(w => w.id === id)!.is_active).every(id => local.some(w => w.id === id)) &&
      !local.some(w => w.id === f.foreignWorkspaceId));
    const publicGrants: string[] = [];
    for (const workspace of local) {
      const members = await c.scopes.send<Members>(c.scopes.withWorkspace(c.scopes.ownerA, workspace.id), 'GET', `${WORKSPACES}${workspace.id}/members/`);
      check('owner_members_200', members.status === 200 && members.body.result.workspace.id === workspace.id &&
        members.body.result.members.length === members.body.result.total);
      if (members.body.result.members.some(m => m.user_id === actor.userId)) publicGrants.push(workspace.id);
    }
    check('independent_public_exact_access', sameSet(publicGrants, eligible));
    context = await c.browser.newContext({ baseURL: E2E.appUrl, serviceWorkers: 'block' });
    await boundary.install(context, scenario);
    const page = await context.newPage(); page.setDefaultTimeout(UI_READY); page.setDefaultNavigationTimeout(UI_READY);
    row.phase = 'sign-in';
    const initial = await passwordLogin(page, actor.email, `H1-${c.scopes.prefix}-member!`, check);
    const wanted = { expected, org: f.org, userId: actor.userId, foreign: f.foreignWorkspaceId, eligible, local };
    const after = async (phase: string) => {
      const actual = await readGrants(c.probe, actor.userId);
      const access = checkGrants(f, actual, f.expectedActiveMembershipIdsAfterAuth, check, phase);
      check(`${phase}_all_grants_unchanged`, digest(actual) === digest(grants));
      check(`${phase}_access_unchanged`, sameSet(access, eligible));
      check(`${phase}_foreign_victim_unchanged`, await foreignRows(c.probe, f.foreignOrg) === victim);
    };
    row.phase = 'landing';
    await after('after_auth');
    await page.goto(new URL(LANDING, E2E.appUrl).href, { waitUntil: 'domcontentloaded' });
    await assertLanding(page, initial, wanted, check);
    row.phase = 'reload';
    await page.reload({ waitUntil: 'domcontentloaded' });
    await after('after_reload');
    await assertLanding(page, initial, wanted, check, 'reload');
    row.status = 'passed';
  } catch { row.status = 'failed'; }
  finally {
    try {
      if (fixture) {
        await fixture.snapshotAllRows();
        if (victim) check('finally_foreign_victim_unchanged', await foreignRows(c.probe, fixture.foreignOrg) === victim);
      }
    } catch { row.checks.push({ name: 'final_observation', passed: false }); row.status = 'failed'; }
    try { await context?.close(); } catch { row.status = 'failed'; }
    try { await fixture?.dispose(); } catch { row.checks.push({ name: 'dispose', passed: false }); row.status = 'failed'; }
    const audit = boundary.receipt();
    row.boundary = { localRequests: audit.continuedLocal, refused: audit.attempts.length,
      unrouted: audit.attempts.filter(a => a.phase === 'observed-unrouted').length };
    try { check('zero_external_attempts', audit.attempts.length === 0); } catch { row.status = 'failed'; }
    const body = JSON.stringify(row);
    writeFileSync(c.testInfo.outputPath(`landing-case-${scenario}.json`), body);
    await c.testInfo.attach(`landing-case-${scenario}`, { contentType: 'application/json', body });
  }
  return row;
}
