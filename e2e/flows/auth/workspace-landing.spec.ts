import { createHash } from 'node:crypto';
import { writeFileSync } from 'node:fs';
import type { BrowserContext, Page } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { LANDING_CASES, LANDING_V2_CASES, seedLandingFixture, type LandingFixture } from '../../lib/landing-fixtures';
import { WalkBoundary } from '../../lib/walk/boundary';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { prepareLandingChecks } from './workspace-landing.assertions';
import { runV2LandingCase } from './workspace-landing.v2';
import { runInvitationCell } from './workspace-landing.invites';
import { recordLandingInventory } from './workspace-landing.inventory';

// accounts/views/user.py:get_user_info, accounts/views/workspace.py:WorkspaceManagementView.get
// and WorkspaceMembershipView.get. The latter is read as the owning administrator,
// not as the member (who cannot enumerate other members).
const ME = '/accounts/user-info/';
const WORKSPACES = '/accounts/workspaces/';
const TOKEN = '/accounts/token/';
// accounts/urls.py; SwitchWorkspaceSerializer.new_workspace_id and
// OrganizationSwitchRequestSerializer.organization_id. Both persist synchronously.
const SWITCH_WORKSPACE = '/accounts/workspace/switch/';
const SWITCH_ORGANIZATION = '/accounts/organizations/switch/';
// frontend/src/sections/auth/jwt/jwt-login-view.jsx: returnTo and the native form;
// flows/auth/saml-tenant-isolation.spec.ts: real browser-bound SAML round trip.
const LOGIN = '/auth/jwt/login';
const LANDING = '/dashboard/develop';
const INITIATE = '/saml2_auth/idp-login/';
const COMPLETE = '/saml2_auth/complete/';
// README Writing a flow: first-paint/action budget, not a CDC wait.
const UI_READY = 60_000;
const CASE_BUDGET = 8 * UI_READY;
const sha = (value: unknown) => `sha256:${createHash('sha256').update(JSON.stringify(value)).digest('hex')}`;

interface Workspace { id: string; name: string; display_name: string | null; is_default: boolean }
interface WorkspaceList { result: { workspaces: Workspace[]; total: number } }
interface Members { result: { workspace: { id: string }; members: { user_id: string; role: string }[]; total: number } }
interface Info {
  id: string; organization: { id: string }; default_workspace_id: string | null;
  default_workspace_display_name: string | null; default_workspace_role: string | null;
  org_level: number; ws_level: number | null;
}
interface StoredConfig {
  currentOrganizationId?: string; selected_organization_id?: string;
  currentWorkspaceId?: string; defaultWorkspaceId?: string;
  orgWorkspaceMap?: Record<string, string>;
}
interface CaseReceipt {
  scenario: string; browser: string; control: boolean; seedDigest?: string; setupDigest?: string;
  expectedDigest?: string; observedDigest?: string;
  checks: { name: string; passed: boolean }[];
  phase: string; status: 'not-run' | 'passed' | 'failed';
  boundary?: { localRequests: number; refused: number; unrouted: number };
}

// No screenshots, traces, videos, raw seeded IDs, emails, SAML assertions or tokens.
// The existing fixture's two attachments also use its digest-only mode.
test.use({ scopeEvidence: 'digest', trace: 'off', screenshot: 'off', video: 'off' });

test.describe('AUTH-E2E-005 / J02.L1', () => {
// Sessions share the fixed socket, never overlap; retain independent test failures.
test.describe.configure({ mode: 'default' });
test('AUTH-E2E-005: workspace landing stays within the current organization and authorized workspaces', {
  tag: ['@flow', '@workspace-landing'],
  annotation: flowAnnotation({
    id: 'AUTH-E2E-005', area: 'auth',
    userGoal: 'Sign in as a multi-workspace SAML member, self-serve owner or invited member and land only in the workspace selected by the current organization access policy',
    steps: [
      'mint two synthetic tenants and seed each deterministic landing case with the existing landing fixture',
      'switch to then revoke an existing local preference; persist the other organization before SAML; read config, rosters and memberships back',
      'require equal membership timestamps for the UUID tiebreak; unequal timestamps are a fixture blocker, not a product failure',
      'sign in through the password form or complete the local signed SAML round trip in a fresh browser context',
      'read the first unscoped user-info response and the exact visible workspace label',
      'read the resolved workspace and accessible set again without a workspace selector, then reload the landing page',
      'restore each owned fixture and attach only digests, status codes and named assertion outcomes',
      'exercise saved, legacy, configured and inactive preferences with named V2 roles and independent exact membership readbacks',
      'bracket real no-membership login and reload with unfiltered all-row membership snapshots',
      'run invite-link, invite-deadline-expired-pending, invite-cancelled and invite-missing-authority as separate fresh-session tests on the same fixed local mail socket',
    ],
    backendChecks: [
      ...LANDING_CASES.map(scenario => `${scenario}: browser and independent user-info resolve the expected workspace (or null), product workspace/member APIs confirm its access, and the foreign workspace is never the current-org landing`),
      'SAML resolves the signing organization while both persisted organization selectors still point to the other accessible tenant',
      'a persisted preference for an existing local workspace with revoked membership loses to a separate authorized default',
      ...LANDING_V2_CASES.map(scenario => `${scenario}: first unscoped SPA response, independent APIs, exact active grants, visible label and reload agree with the fixed named-role oracle`),
      'member-without-membership: all-row membership equality after login, reload and before cleanup; separate inactive-row login/reload fault controls',
      'invite-link: real delivered-link UI acceptance, exact two grants, fixed default versus first grant/UUID, reload, read-only preview and no-write accepted-link replay; explicit Expired uses another fresh session',
      'invite-deadline-expired-pending, invite-cancelled, invite-missing-authority: real preview GET and crafted browser POST each refuse with separate exact DB/cache no-write brackets',
      'all five test identities and seventeen scenario receipts per browser must pass the Chromium/Firefox aggregate; source only until the shared runtime gates clear',
    ],
  }),
}, async ({ browser, browserName, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  test.setTimeout(120_000 + LANDING_CASES.length * CASE_BUDGET);
  expect(new URL(E2E.pgUrl).hostname === 'localhost', 'AUTH004_LOCAL_STORAGE_READS').toBe(true);
  // 120 s ScopeActors + 7 * (seed 60 + setup/readbacks 60 + login page 60 +
  // login exchange 60 + landing UI 60 + API 60 + reload 60 + disposal 60) = 3480 s.
  // No retries and no enlarged per-action wait. These are synchronous account APIs;
  // they must be correct on the first response, not eventually converge under a poll.
  const control = process.env.E2E_LANDING_EXPECT_WRONG;
  if (control !== undefined && control !== 'all') throw new Error('AUTH004_UNKNOWN_CONTROL');
  const rows: CaseReceipt[] = [];
  const apiOrigin = new URL(E2E.apiUrl).origin;
  const app = (path: string) => new URL(path, E2E.appUrl).href;
  test.setTimeout(120_000 + (LANDING_CASES.length + LANDING_V2_CASES.length) * CASE_BUDGET);
  for (const scenario of [...LANDING_CASES, ...LANDING_V2_CASES]) {
    if ((LANDING_V2_CASES as readonly string[]).includes(scenario)) {
      rows.push(await runV2LandingCase(scenario, { browser, browserName, scopes, probe, testInfo }));
      continue;
    }
    const row: CaseReceipt = { scenario, browser: browserName, control: control === 'all',
      checks: [], phase: 'seed', status: 'not-run' };
    rows.push(row);
    const check = (name: string, passed: boolean) => {
      row.checks.push({ name, passed });
      // Boolean assertion prevents a failed report from printing seeded values.
      expect(passed, `AUTH004_${scenario}_${name}`).toBe(true);
    };
    const actor = scenario === 'owner-without-membership' ? scopes.ownerA : scopes.member;
    let fixture: LandingFixture | undefined;
    let context: BrowserContext | undefined;
    let added: Awaited<ReturnType<typeof prepareLandingChecks>> | undefined;
    const boundary = new WalkBoundary(`AUTH004-${scenario}-${browserName}`);
    try {
      await test.step(scenario, async () => {
        fixture = await seedLandingFixture(scopes.ownerA, scopes.ownerB, actor, scenario);
        const seeded = fixture;
        // This switch changes ONLY the assertion side. Never send it to the seed,
        // login, browser storage, API request headers or fixture's expected field.
        const expected = control === 'all' ? seeded.workspaces[2].id : seeded.expected;
        row.seedDigest = sha({ user: actor.userId, orgs: [seeded.org, seeded.foreignOrg],
          workspaces: seeded.workspaces, preference: seeded.preference, nonce: seeded.nonce });
        row.expectedDigest = sha(expected);
        await testInfo.attach(`landing-seed-${scenario}`, { contentType: 'application/json',
          body: JSON.stringify({ scenario, seedDigest: row.seedDigest, expectedDigest: row.expectedDigest }) });
        check('distinct_tenants', seeded.org !== seeded.foreignOrg);
        check('reversed_uuid_fixture', seeded.workspaces[0].id > seeded.workspaces[1].id);

        let local: Workspace[] = [];
        const memberships = new Map<string, Members['result']['members']>();
        const denied = seeded.workspaces[1].id;
        const readConfig = async () => {
          const rows = await probe.pg<{ config: StoredConfig }>('SELECT config FROM accounts_user WHERE id=$1', [actor.userId]);
          check('persisted_user_exists', rows.length === 1);
          return rows[0].config;
        };
        row.phase = 'setup-readback';
        await test.step('establish and read discriminating product state', async () => {
          // These are ordinary product actions on the fixture-owned rows. No SQL
          // writes or alternate seeder; fixture.dispose restores the original state.
          const authorizedActor = scopes.withWorkspace(actor, seeded.workspaces[0].id);
          if (scenario === 'stale-preference') {
            const selected = await scopes.send(authorizedActor, 'POST', SWITCH_WORKSPACE, { new_workspace_id: denied });
            check('select_future_revoked_preference_200', selected.status === 200);
            const removed = await scopes.send(scopes.withWorkspace(scopes.ownerA, denied), 'DELETE',
              `${WORKSPACES}${denied}/members/${actor.userId}/`);
            check('revoke_preferred_membership_200', removed.status === 200);
          }
          if (seeded.samlIdp) {
            const switched = await scopes.send(authorizedActor, 'POST', SWITCH_ORGANIZATION,
              { organization_id: seeded.foreignOrg });
            check('persist_other_organization_200', switched.status === 200);
            const foreign = await scopes.send<Members>(scopes.withWorkspace(scopes.ownerB, seeded.workspaces[2].id),
              'GET', `${WORKSPACES}${seeded.workspaces[2].id}/members/`);
            check('saml_other_tenant_accessible', foreign.status === 200 &&
              foreign.body.result.workspace.id === seeded.workspaces[2].id &&
              foreign.body.result.members.some(m => m.user_id === actor.userId));
          }
          const config = await readConfig();
          row.setupDigest = sha(config);
          if (seeded.samlIdp) check('saml_persisted_other_organization',
            config.currentOrganizationId === seeded.foreignOrg && config.selected_organization_id === seeded.foreignOrg);
          if (scenario === 'stale-preference') check('stale_persisted_revoked_workspace',
            config.currentWorkspaceId === denied && config.defaultWorkspaceId === denied && config.orgWorkspaceMap?.[seeded.org] === denied);
          const roster = await scopes.send<WorkspaceList>(scopes.ownerA, 'GET', WORKSPACES);
          check('owner_roster_200', roster.status === 200);
          local = roster.body.result.workspaces;
          check('multi_workspace_roster', local.length >= 2 && roster.body.result.total === local.length);
          check('seeded_local_pair', seeded.workspaces.slice(0, 2).every(w => local.some(r => r.id === w.id)));
          check('foreign_not_in_roster', !local.some(w => w.id === seeded.workspaces[2].id));
          for (const workspace of local) {
            const response = await scopes.send<Members>(scopes.withWorkspace(scopes.ownerA, workspace.id),
              'GET', `${WORKSPACES}${workspace.id}/members/`);
            check('members_200', response.status === 200);
            check('member_workspace_binding', response.body.result.workspace.id === workspace.id);
            check('member_total', response.body.result.members.length === response.body.result.total);
            memberships.set(workspace.id, response.body.result.members);
          }
          const granted = [...memberships].filter(([, members]) => members.some(m => m.user_id === actor.userId))
            .map(([id]) => id).sort();
          const wanted = seeded.noMembership ? [] : seeded.workspaces.slice(0, scenario === 'stale-preference' ? 1 : 2).map(w => w.id).sort();
          check('exact_memberships', sha(granted) === sha(wanted));
          if (scenario === 'stale-preference') {
            check('stale_existing_current_org', local.some(w => w.id === denied));
            check('stale_membership_revoked', !memberships.get(denied)!.some(m => m.user_id === actor.userId));
            check('stale_authorized_fallback', seeded.expected !== denied &&
              Boolean(memberships.get(seeded.expected!)?.some(m => m.user_id === actor.userId)));
          }
          if (scenario === 'reversed-uuid') {
            // The public members API exposes only the calendar date. Read exact
            // timestamps as text: JS Date would erase the microsecond difference.
            // Until the shared fixture owner supplies a tied pair AND a separate
            // membership-order witness, this fails as a setup blocker, not product red.
            const grants = await probe.pg<{ created_at: string }>(`SELECT created_at::text AS created_at
              FROM accounts_workspacemembership WHERE user_id=$1 AND workspace_id=ANY($2::uuid[])
              AND is_active AND NOT deleted`, [actor.userId, seeded.workspaces.slice(0, 2).map(w => w.id)]);
            check('uuid_tiebreak_requires_equal_membership_times', grants.length === 2 && grants[0].created_at === grants[1].created_at);
            check('uuid_tiebreak_has_no_default', local.every(w => !w.is_default));
          }
        }, { timeout: UI_READY });

        added = await prepareLandingChecks(seeded, actor, probe, check);
        context = await browser.newContext({ baseURL: E2E.appUrl, serviceWorkers: 'block' });
        await boundary.install(context, scenario); // before the first page, including login
        const page = await context.newPage();
        page.setDefaultTimeout(UI_READY);
        page.setDefaultNavigationTimeout(UI_READY);
        row.phase = 'login-page';
        await test.step('open a fresh signed-out login page', async () => {
          await page.goto(app(`${LOGIN}?returnTo=${encodeURIComponent(LANDING)}`), { waitUntil: 'domcontentloaded' });
          check('fresh_scope', await page.evaluate(() => !localStorage.getItem('accessToken') &&
            !sessionStorage.getItem('workspaceId') && !sessionStorage.getItem('organizationId')));
          await expect(page.getByLabel(/email/i)).toBeVisible({ timeout: UI_READY });
        }, { timeout: UI_READY });

        row.phase = 'sign-in';
        let initial: Info | undefined;
        await test.step('authenticate without selecting a workspace', async () => {
          // Register before submitting. This is the actual SPA request, not an API
          // replay whose explicit selector could hide a broken login resolver.
          const infoResponse = page.waitForResponse(r => new URL(r.url()).origin === apiOrigin &&
            new URL(r.url()).pathname === ME && r.request().method() === 'GET', { timeout: UI_READY });
          // Attach a rejection handler immediately; a failed UI action must not
          // leave an unhandled waiter while the case is being disposed.
          void infoResponse.catch(() => undefined);
          if (seeded.samlIdp) {
            await seeded.samlIdp.setIdentity(actor.email);
            const idpUrl = await page.evaluate(async ({ api, email, next, path, timeout }) => {
              const url = new URL(path, api); url.searchParams.set('email', email); url.searchParams.set('next', next);
              const response = await fetch(url, { credentials: 'include', redirect: 'error', signal: AbortSignal.timeout(timeout) });
              if (response.status !== 200) throw new Error('AUTH004_SAML_INITIATE');
              return (await response.json()).result.url as string;
            }, { api: E2E.apiUrl, email: actor.email, next: LANDING, path: INITIATE, timeout: UI_READY });
            check('local_idp_origin', new URL(idpUrl).origin === new URL(seeded.samlIdp.baseUrl).origin);
            const completion = page.waitForResponse(r => new URL(r.url()).origin === apiOrigin &&
              new URL(r.url()).pathname === COMPLETE && r.status() === 302, { timeout: UI_READY });
            void completion.catch(() => undefined);
            await page.goto(idpUrl, { waitUntil: 'domcontentloaded' });
            await completion;
          } else {
            // Password is pinned from ScopeActors.invite and provisionActor, not
            // a new identity/provisioning implementation or a token injection.
            const password = actor === scopes.ownerA ? scopes.owners[0].password : `H1-${scopes.prefix}-member!`;
            await page.getByLabel(/email/i).fill(actor.email);
            await page.getByLabel('Password', { exact: true }).fill(password);
            const [response] = await Promise.all([
              page.waitForResponse(r => new URL(r.url()).origin === apiOrigin && new URL(r.url()).pathname === TOKEN &&
                r.request().method() === 'POST', { timeout: UI_READY }),
              page.getByRole('button', { name: 'Continue', exact: true }).click(),
            ]);
            check('password_login_200', response.status() === 200);
          }
          const response = await infoResponse;
          check('initial_info_200', response.status() === 200);
          const headers = await response.request().allHeaders();
          check('initial_no_workspace_selector', !headers['x-workspace-id'] &&
            !new URL(response.url()).searchParams.has('workspace_id'));
          if (seeded.samlIdp) check('saml_no_org_override', !headers['x-organization-id']);
          initial = await response.json() as Info;
        }, { timeout: UI_READY });

        await added.afterLogin(page);
        // Observe and assert the backend even when the UI lands incorrectly.
        // The assertion-only control is evaluated here, after the real login.
        row.phase = 'resolved-api';
        await test.step('resolved workspace and access via product APIs', async () => {
          const readback = await page.evaluate(async ({ api, me, workspaces, timeout }) => {
            const token = localStorage.getItem('accessToken');
            if (!token) throw new Error('AUTH004_NO_BROWSER_SESSION');
            const get = async (path: string) => {
              const response = await fetch(new URL(path, api), { headers: { Authorization: `Bearer ${token}` },
                credentials: 'omit', redirect: 'error', signal: AbortSignal.timeout(timeout) });
              return { status: response.status, body: await response.json() };
            };
            return { me: await get(me), roster: await get(workspaces) };
          }, { api: E2E.apiUrl, me: ME, workspaces: WORKSPACES, timeout: UI_READY });
          check('readback_200', readback.me.status === 200 && readback.roster.status === 200);
          const info = readback.me.body as Info;
          const available = (readback.roster.body as WorkspaceList).result;
          row.observedDigest = sha({ initial: initial!.default_workspace_id, resolved: info.default_workspace_id,
            available: available.workspaces.map(w => w.id).sort() });
          check('authenticated_actor', initial!.id === actor.userId && info.id === actor.userId);
          check('current_organization', initial!.organization.id === seeded.org && info.organization.id === seeded.org);
          if (seeded.samlIdp) {
            const config = await readConfig();
            check('saml_persisted_scope_unchanged', config.currentOrganizationId === seeded.foreignOrg &&
              config.selected_organization_id === seeded.foreignOrg);
          }
          if (scenario === 'stale-preference') check('revoked_workspace_never_lands',
            initial!.default_workspace_id !== denied && info.default_workspace_id !== denied);
          const eligible = actor === scopes.ownerA ? local.map(w => w.id) : [...memberships]
            .filter(([, members]) => members.some(m => m.user_id === actor.userId)).map(([id]) => id);
          check('exact_accessible_set', sha(available.workspaces.map(w => w.id).sort()) === sha(eligible.sort()) &&
            available.total === eligible.length);
          check('never_outside_access', info.default_workspace_id === null ? eligible.length === 0 :
            eligible.includes(info.default_workspace_id));
          check('never_foreign_landing', info.default_workspace_id !== seeded.workspaces[2].id);
          check('role_policy', info.org_level === (actor === scopes.ownerA ? 15 : 3) &&
            (seeded.expected === null ? info.ws_level === null && info.default_workspace_role === null :
              info.ws_level === (actor === scopes.ownerA ? 15 : 3)));
          check('expected_landing', initial!.default_workspace_id === expected && info.default_workspace_id === expected);
        }, { timeout: UI_READY });

        const expectedRow = local.find(w => w.id === expected);
        const label = expectedRow ? expectedRow.display_name || expectedRow.name : 'Workspace';
        const ui = async (page: Page) => {
          // WorkspaceSwitcher.jsx:wsDisplayLabel; exact visible singleton, not
          // contains-text or a stored selector alone. Boolean polling is value-free.
          await expect.poll(async () => {
            const labels = await page.getByText(label, { exact: true }).filter({ visible: true }).allTextContents();
            return labels.length === 1 && labels[0] === label;
          }, { timeout: UI_READY, message: `AUTH004_${scenario}_visible_workspace` }).toBe(true);
          const state = await page.evaluate(() => ({ org: sessionStorage.getItem('organizationId'),
            workspace: sessionStorage.getItem('workspaceId'), workspaceOrg: sessionStorage.getItem('workspaceOrgId') }));
          check('ui_scope', state.org === seeded.org && state.workspace === expected &&
            (expected === null ? state.workspaceOrg === null : state.workspaceOrg === seeded.org));
        };
        row.phase = 'landing-ui';
        await test.step('view the resolved workspace in the application', async () => {
          // A new self-serve owner can first see organization setup. Ordinary
          // navigation is allowed; no storage, scope or response is overwritten.
          await page.goto(app(LANDING), { waitUntil: 'domcontentloaded' });
          await expect(page).toHaveURL(app(LANDING), { timeout: UI_READY });
          await ui(page);
        }, { timeout: UI_READY });
        row.phase = 'reload';
        await test.step('reload keeps the same resolved workspace', async () => {
          await page.reload({ waitUntil: 'domcontentloaded' });
          await added!.afterReload(page);
          await ui(page);
        }, { timeout: UI_READY });
        row.status = 'passed';
      }, { timeout: CASE_BUDGET - UI_READY });
    } catch {
      // Preserve the failing named checkpoint in the value-free receipt and
      // continue all cases. No catch turns a failed subcase into a green flow.
      row.status = 'failed';
    } finally {
      try { await added?.finish(); }
      catch { row.checks.push({ name: 'final_observation', passed: false }); row.status = 'failed'; }
      try {
        await context?.close();
        await fixture?.dispose();
      } catch { row.checks.push({ name: 'dispose', passed: false }); row.status = 'failed'; }
      const audit = boundary.receipt();
      row.boundary = { localRequests: audit.continuedLocal, refused: audit.attempts.length,
        unrouted: audit.attempts.filter(a => a.phase === 'observed-unrouted').length };
      // No asset exception for this flow: any attempted external traffic blocks
      // qualification, even when the browser aborted it before dispatch.
      if (audit.attempts.length) { row.checks.push({ name: 'zero_external_attempts', passed: false }); row.status = 'failed'; }
      const body = JSON.stringify(row);
      writeFileSync(testInfo.outputPath(`landing-case-${scenario}.json`), body);
      await testInfo.attach(`landing-case-${scenario}`, { contentType: 'application/json', body });
    }
  }
  await testInfo.attach('workspace-landing-results', { contentType: 'application/json', body: JSON.stringify({
    cases: rows, complete: rows.length === LANDING_CASES.length + LANDING_V2_CASES.length,
    provisioningDebt: 'Existing ScopeActors bootstrap dependency: rerun on the first personal-key composition before activation',
  }) });
  expect(rows.map(r => r.scenario), 'J02.L1.A13 exact non-mail inventory').toEqual([
    'reversed-uuid', 'membership-order', 'is-default', 'stale-preference', 'cross-org-preference',
    'saml-scoped-default', 'owner-without-membership', 'member-without-membership',
    'saved-preference', 'legacy-current', 'configured-default', 'inactive-preference',
  ]);
  recordLandingInventory(testInfo, browserName, 'landing', scopes, rows);
  expect(rows.filter(r => r.status !== 'passed').map(r => ({ scenario: r.scenario, phase: r.phase })),
    'Every landing case must pass; failed or unrun cases are not coverage').toEqual([]);
});
// J02-ADOPT-01(b): four independent test identities, not an environment selector.
for (const scenario of ['invite-link', 'invite-deadline-expired-pending', 'invite-cancelled', 'invite-missing-authority'] as const) {
  // The single @flow catalog entry above owns these subtests, not four new flows.
  test(`AUTH-E2E-005: ${scenario}`, { tag: ['@workspace-landing'] },
    async ({ browser, browserName, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
      test.setTimeout(120_000 + (scenario === 'invite-link' ? 2 : 1) * CASE_BUDGET);
      const rows = await runInvitationCell(scenario, { browser, browserName, scopes, probe, testInfo });
      expect(rows.map(r => r.scenario), 'J02.L1.A13 exact mail inventory').toEqual(
        scenario === 'invite-link' ? ['invite-link', 'invite-explicit-expired'] : [scenario]);
      recordLandingInventory(testInfo, browserName, scenario, scopes, rows);
      expect(rows.every(r => r.status === 'passed'), `AUTH004_${scenario}_complete`).toBe(true);
    });
}
});
