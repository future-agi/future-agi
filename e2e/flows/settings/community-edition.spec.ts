import { test as base, expect, request as pwRequest, type Page, type Response } from '@playwright/test';
import { ApiClient, ApiError, type Tokens } from '../../lib/api-client';
import { assertCommunityLane, setLaneLicence } from '../../lib/community-lane';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { StateProbe } from '../../lib/state-probe';

// --- Pinned facts ------------------------------------------------------------
// frontend/src/routes/paths.js: ossSetup, auth.jwt.register / setup_org,
// dashboard.getstarted, settings.eeLicenses / manageteam; the settings
// "workspace" index route is WorkSpaceManagement (routes/sections/dashboard.jsx).
const SETUP = '/setup';
const REGISTER = /\/auth\/jwt\/register/;
const SETUP_ORG = /\/auth\/jwt\/setup-org/;
const GET_STARTED_PATH = '/dashboard/get-started';
const GET_STARTED = /\/dashboard\/get-started$/;
const PLAN_LICENSE = '/dashboard/settings/ee-licenses';
const USERS = '/dashboard/settings/user-management';
const WORKSPACES = '/dashboard/settings/workspace';
// frontend/src/utils/axios.js endpoints: auth.register, auth.user_onboarding_info,
// settings.teams.inviteMember (setup-org posts { org_name, members }),
// rbac.inviteCreate, workspaces.create, organizations.create; GET /api/edition/
// is useEdition().
const SIGNUP = '/accounts/signup/';
const ONBOARDING = '/accounts/onboarding/';
const TEAM_USERS = '/accounts/team/users/';
const EDITION = '/api/edition/';
const INVITE = '/accounts/organization/invite/';
const WORKSPACE_CREATE = '/accounts/workspaces/';
const ORG_CREATE = '/accounts/organizations/new/';
// e2e/flows/settings/mcp-tool-groups.spec.ts (SET-E2E-001): { tool_name, params }.
const MCP_CONFIG = '/mcp/config/';
const TOOL_CALL = '/mcp/internal/tool-call/';
// futureagi/mcp_server/constants.py RATE_LIMITS["free"]["per_minute"]: the Cloud
// Free-tier MCP cap (A5) that no longer applies off-cloud.
const FREE_MCP_PER_MINUTE = 200;
// oss-first-run/constants.js accountStep: the next step when no account exists yet.
const CREATE_ACCOUNT_STEP = 'Create your account on the next screen. You become the owner of a new workspace.';
// UserManagementV2/constant.js orgRoleOptions; LEVELS.ADMIN / OWNER.
const ADMIN = 8;
const OWNER = 15;
// futureagi/tfc/capabilities/edition.py COMMUNITY_LIMITS, CONTACT_EMAIL, ACTIVATION_ROUTE.
const SALES_MAILTO = 'mailto:sales@futureagi.com';
const GATE_BASE = { edition: 'community', contact: 'sales@futureagi.com',
  activation_route: '/dashboard/settings/ee-licenses' };
// e2e/scripts/test-licence.mjs: the lane's test licence.
const TEST_LICENCE = { issuedTo: 'E2E Test Licence', maskedId: 'lic_****0001' };

// --- Budgets -------------------------------------------------------------------
const UI_READY = 60_000; // README Writing a flow: one browser action's first paint.
// A click, fill or default-timeout expect on a control a UI_READY wait has
// already shown (playwright.config.ts expect.timeout; page.setDefaultTimeout).
const ACTION = 10_000;
// One API or Postgres read: Playwright's APIRequestContext request default.
const API_CALL = 30_000;
// `bin/e2e licence <state>`: recreate the app container, readiness and the
// validator verdict. Measured on this stack 2026-10-06: 95.5 s (enterprise),
// 56.1 s (removed); 240 s leaves room for a loaded laptop.
const LICENCE_RESTART = 240_000;
// The Free-tier MCP cap is per minute: the burst only proves something if it
// lands inside one, so it stops at the minute (plus the batch in flight).
const MCP_WINDOW = 60_000;
const budget = (b: { ui?: number; actions?: number; api?: number; restarts?: number; ms?: number }) =>
  (b.ui ?? 0) * UI_READY + (b.actions ?? 0) * ACTION + (b.api ?? 0) * API_CALL
  + (b.restarts ?? 0) * LICENCE_RESTART + (b.ms ?? 0);
// Every awaited wait in each stage, counted from the code below (a request
// raced with its click counts both); the test ceiling is the sum, 6,490 s.
// A green run on the lane takes about 4.5 min.
const STAGE = {
  signup: budget({ ui: 17, actions: 13, api: 2 }),
  planLicense: budget({ ui: 4, actions: 7 }),
  mcpBurst: budget({ api: 2, ms: MCP_WINDOW }),
  inviteTwo: budget({ ui: 3, actions: 9, api: 1 }),
  fourthMember: budget({ ui: 7, actions: 9, api: 2 }),
  secondWorkspace: budget({ ui: 5, actions: 6, api: 1 }),
  secondOrganization: budget({ ui: 6, actions: 7, api: 2 }),
  snapshots: budget({ api: 6 }),
  activate: budget({ restarts: 1, ui: 3, actions: 1, api: 3 }),
  enterpriseCreates: budget({ ui: 12, actions: 14, api: 4 }),
  expire: budget({ restarts: 1, ui: 7, actions: 7, api: 11 }),
  remove: budget({ restarts: 1, api: 4 }),
};

// --- Wire shapes (serializers) ---------------------------------------------------
type Limit = { limit: number | null; current: number };
// tfc/capabilities/contracts.py EditionEnvelopeSerializer.
interface EditionEnvelope { status: boolean; result: {
  edition: 'community' | 'enterprise' | 'cloud';
  limits: { organizations: Limit; workspaces: Limit; members: Limit };
  over_limit: boolean;
  license?: { state: string; issued_to: string | null; license_id_masked: string | null };
} }
// accounts/authentication.py custom_exception_handler, FeatureUnavailable branch.
interface GateBody { error: { code: string }; enterprise_gate: Record<string, unknown> }
// e2e/lib/provisioning.ts.
interface UserInfo { id: string; organization: { id: string }; default_workspace_id: string }
interface KeysEnvelope { data: { api_key: string; secret_key: string } }
type Row = Record<string, unknown>;

const isPost = (path: string) => (r: Response) =>
  new URL(r.url()).pathname === path && r.request().method() === 'POST';
const isGet = (path: string) => (r: Response) =>
  new URL(r.url()).pathname === path && r.request().method() === 'GET';

async function gateDialog(page: Page, title: string, description: string) {
  // EnterpriseGateDialog.jsx passes aria-label to MUI's Dialog root, not to the
  // role="dialog" paper, so the dialog has no accessible name: match its title.
  const dialog = page.getByRole('dialog').filter({ has: page.getByText(title, { exact: true }) });
  await expect(dialog).toBeVisible({ timeout: UI_READY });
  await expect(dialog.getByText(description, { exact: true })).toBeVisible({ timeout: UI_READY });
  await expect(dialog.getByRole('link', { name: 'Contact sales' })).toHaveAttribute('href', SALES_MAILTO);
  await expect(dialog.getByRole('button', { name: 'Activate license' })).toBeVisible();
  return dialog;
}

async function expectRefused(call: Promise<unknown>, feature: string) {
  const err = await call.then(() => null, (e: unknown) => e);
  expect(err).toBeInstanceOf(ApiError);
  expect((err as ApiError).status).toBe(402);
  const body = (err as ApiError).body as GateBody;
  expect(body.error.code).toBe('ENTERPRISE_FEATURE_REQUIRED');
  expect(body.enterprise_gate.feature).toBe(feature);
}

// Deliberately NOT using lib/fixtures: the flow drives the install's first
// sign-up itself, so it starts signed out on the Community lane, where it is
// the only flow (README "The Community edition lane").
base('SET-E2E-002: a Community admin meets the Enterprise gate and activates a licence without losing data', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SET-E2E-002', area: 'settings',
    userGoal: 'An admin of a fresh self-hosted Community install sees its edition, uses its one organization, one workspace and three member seats, gets an actionable Enterprise gate at the next creation, and activates a test-signed licence without losing identities or data',
    steps: [
      'run the first-run checks at /setup and sign up the install\'s first owner, then name the organization',
      'land on Get started',
      'open Settings > Plan & License: Community · Self-hosted with 1 / 1, 1 / 1 and 1 / 3, Contact sales and Activate license',
      'invite two admins from Settings > Users',
      'invite a fourth member: the Enterprise gate dialog opens; Activate license leads to Plan & License',
      'create a second workspace from Settings > Workspaces: the Enterprise gate dialog opens',
      'create a second organization from the workspace switcher: the Enterprise gate dialog opens',
      'the operator sets a test-signed EE_LICENSE_KEY and restarts the app (bin/e2e licence enterprise)',
      'Plan & License shows Enterprise · Self-hosted; the fourth invite, the second workspace and the second organization now succeed',
      'the licence expires (restart with an expired licence): Plan & License shows Community, expired and over the limit',
      'a new workspace is refused again from Settings > Workspaces',
      'the licence is removed (restart without a key): still Community, still refused, nothing lost',
    ],
    backendChecks: [
      'the first sign-up creates the install\'s only organization (PG accounts_organization) and the owner lands on Get started, not Falcon AI',
      'GET /api/edition/ (the page\'s own request) reports community with limits 1/1, 1/1, 3/1 and licence state missing',
      'on Community 201 MCP tool calls inside one minute all succeed: the Free-tier 200/min cap does not apply',
      'the two invites are exactly two Pending admin rows in PG accounts_organization_invite',
      'the fourth invite is HTTP 402 ENTERPRISE_FEATURE_REQUIRED with enterprise_gate members 3/3, and writes no invite or user row',
      'the second workspace is HTTP 402 with enterprise_gate workspaces 1/1 and PG keeps exactly one active workspace',
      'the second organization is HTTP 402 with enterprise_gate organizations 1/1 and PG keeps exactly one organization',
      'after activation GET /api/edition/ reports enterprise with the test licence active and the owner, organization, workspace, API key and invite ids are unchanged',
      'with the licence the fourth invite, the second workspace and the second organization are written to PG',
      'after expiry GET /api/edition/ reports community, licence expired, over_limit, with 2 organizations, 3 workspaces and 4 member seats kept',
      'after expiry PG keeps every organization, workspace, membership level and invite, and a new member, workspace and organization are each HTTP 402',
      'after removal GET /api/edition/ reports community with licence missing and a new workspace is still HTTP 402',
    ],
  }),
}, async ({ page }, testInfo) => {
  base.setTimeout(Object.values(STAGE).reduce((sum, ms) => sum + ms, 0));
  assertCommunityLane();
  page.setDefaultTimeout(ACTION);
  page.setDefaultNavigationTimeout(UI_READY);

  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const minted = {
    owner: `e2e-edition-${suffix}@futureagi.com`,
    password: `E2e-edition-${suffix}`,
    org: `e2e-edition-org-${suffix}`,
    m1: `e2e-edition-${suffix}-m1@futureagi.com`,
    m2: `e2e-edition-${suffix}-m2@futureagi.com`,
    m4: `e2e-edition-${suffix}-m4@futureagi.com`,
    m5: `e2e-edition-${suffix}-m5@futureagi.com`,
    ws2: `e2e-edition-ws2-${suffix}`,
    ws3: `e2e-edition-ws3-${suffix}`,
    org2: `e2e-edition-org2-${suffix}`,
    org3: `e2e-edition-org3-${suffix}`,
  };
  const { password: _password, ...mintedNames } = minted;
  await testInfo.attach('minted', { contentType: 'application/json', body: JSON.stringify(mintedNames) });

  const req = await pwRequest.newContext();
  let probe: StateProbe | undefined;
  try {
    let api!: ApiClient;
    let ids!: { userId: string; organizationId: string; workspaceId: string };

    await base.step('first-run checks, first owner sign-up and organization name', async () => {
      await page.goto(SETUP);
      await expect(page.getByRole('heading', { name: 'Plan your launch' })).toBeVisible({ timeout: UI_READY });
      const testFlight = page.getByRole('button', { name: /^Test flight/ });
      await testFlight.click();
      await expect(testFlight).toHaveAttribute('aria-pressed', 'true', { timeout: UI_READY });
      await page.getByRole('button', { name: 'Continue', exact: true }).click();
      await expect(page.getByText(CREATE_ACCOUNT_STEP, { exact: true })).toBeVisible({ timeout: UI_READY });
      const next = page.getByRole('button', { name: 'Continue', exact: true });
      await expect(next).toBeEnabled({ timeout: UI_READY });
      await next.click();
      await expect(page).toHaveURL(REGISTER, { timeout: UI_READY });

      await page.getByLabel(/^Full Name/).fill('E2E Edition Owner');
      await page.getByLabel(/^Email ID/).fill(minted.owner);
      await page.getByLabel(/^Set Password/).fill(minted.password);
      await page.getByLabel(/^Confirm Password/).fill(minted.password);
      const signup = page.waitForResponse(isPost(SIGNUP), { timeout: UI_READY });
      // setup-org.jsx resets its role form when its own onboarding read lands.
      const onboardingRead = page.waitForResponse(isGet(ONBOARDING), { timeout: 2 * UI_READY });
      await page.getByRole('button', { name: 'Continue', exact: true }).click();
      expect((await signup).status()).toBeLessThan(300);
      await expect(page).toHaveURL(SETUP_ORG, { timeout: UI_READY });
      expect((await onboardingRead).status()).toBe(200);

      // setup-org.jsx: role (step 0), goals (step 1, skippable), then the
      // owner's organization step.
      await expect(page.getByText("What's your role", { exact: true })).toBeVisible({ timeout: UI_READY });
      await page.getByText('Backend / Platform Engineer / DevOps', { exact: true }).click();
      const roleChosen = page.getByRole('button', { name: 'Continue', exact: true });
      await expect(roleChosen).toBeEnabled({ timeout: UI_READY });
      await roleChosen.click();
      await expect(page.getByText('Tell us about your goals', { exact: true })).toBeVisible({ timeout: UI_READY });
      const onboarded = page.waitForResponse(isPost(ONBOARDING), { timeout: UI_READY });
      await page.getByText('Skip for now', { exact: true }).click();
      expect((await onboarded).status()).toBeLessThan(300);
      await expect(page.getByText('Collaborate with your team', { exact: true })).toBeVisible({ timeout: UI_READY });
      await page.getByLabel(/^Organization Name/).fill(minted.org);
      const named = page.waitForResponse(isPost(TEAM_USERS), { timeout: UI_READY });
      await page.getByRole('button', { name: 'Continue', exact: true }).click();
      const namedResponse = await named;
      expect(namedResponse.status()).toBeLessThan(300);
      expect(namedResponse.request().postDataJSON()).toEqual({ org_name: minted.org, members: [] });
      await expect(page).toHaveURL(GET_STARTED, { timeout: UI_READY });

      // The UI's own session: the same tokens the SPA stored after sign-up.
      const tokens: Tokens = await page.evaluate(() => ({
        access: localStorage.getItem('accessToken') ?? '', refresh: localStorage.getItem('refreshToken') ?? '' }));
      const info = await new ApiClient(req, E2E.apiUrl).withAuth(tokens).get<UserInfo>('/accounts/user-info/');
      ids = { userId: info.id, organizationId: info.organization.id, workspaceId: info.default_workspace_id };
      api = new ApiClient(req, E2E.apiUrl).withAuth(tokens, ids.organizationId, ids.workspaceId);
      probe = new StateProbe({ api, chUrl: E2E.chUrl, chDatabase: E2E.chDatabase,
        chPassword: E2E.chPassword, pgUrl: E2E.pgUrl });
      await testInfo.attach('seeded-ids', { contentType: 'application/json', body: JSON.stringify(ids) });

      expect(await probe.pg<Row>('SELECT id, display_name FROM accounts_organization'))
        .toEqual([{ id: ids.organizationId, display_name: minted.org }]);
    }, { timeout: STAGE.signup });

    const pg = () => probe as StateProbe;
    const invites = () => pg().pg<Row>(
      `SELECT target_email, status, level FROM accounts_organization_invite
        WHERE organization_id = $1 AND deleted = false ORDER BY target_email`, [ids.organizationId]);
    const activeWorkspaces = () => pg().pg<Row>(
      'SELECT id, name FROM accounts_workspace WHERE is_active AND deleted = false ORDER BY created_at');
    const organizations = () => pg().pg<Row>(
      'SELECT id, display_name FROM accounts_organization ORDER BY created_at');

    await base.step('Plan & License shows Community with the real counts', async () => {
      const edition = page.waitForResponse(isGet(EDITION), { timeout: UI_READY });
      await page.goto(PLAN_LICENSE);
      const body: EditionEnvelope = await (await edition).json();
      expect(body.result.edition).toBe('community');
      expect(body.result.limits).toEqual({
        organizations: { limit: 1, current: 1 },
        workspaces: { limit: 1, current: 1 },
        members: { limit: 3, current: 1 },
      });
      expect(body.result.license?.state).toBe('missing');

      await expect(page.getByText('Community · Self-hosted', { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(page.getByText('1 organization · 1 workspace · up to 3 members', { exact: true })).toBeVisible();
      // LicensePage.jsx DetailRow: the label and "current / limit" in one row.
      for (const [label, value] of [['Organizations', '1 / 1'], ['Workspaces', '1 / 1'], ['Members', '1 / 3']]) {
        await expect(page.getByText(new RegExp(`^${label}\\d+ / \\d+$`))).toHaveText(`${label}${value}`);
      }
      await expect(page.getByRole('link', { name: 'Contact sales' })).toHaveAttribute('href', SALES_MAILTO);
      await page.getByRole('button', { name: 'Activate license' }).click();
      await expect(page.getByText('Activate a license', { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(page.getByText(/^Set EE_LICENSE_KEY on every backend, worker and Temporal worker container/))
        .toBeVisible();
    }, { timeout: STAGE.planLicense });

    await base.step('included usage is uncapped: 201 MCP tool calls inside one minute', async () => {
      await api.get(MCP_CONFIG);
      const statuses: boolean[] = [];
      const started = Date.now();
      const calls = Array.from({ length: FREE_MCP_PER_MINUTE + 1 }, (_, i) => i);
      for (let i = 0; i < calls.length && Date.now() - started < MCP_WINDOW; i += 4) {
        const batch = await Promise.all(calls.slice(i, i + 4).map(() =>
          api.post<{ status: boolean }>(TOOL_CALL, { tool_name: 'whoami', params: {} })));
        statuses.push(...batch.map((r) => r.status));
      }
      const elapsed = Date.now() - started;
      await testInfo.attach('mcp-burst', { contentType: 'application/json',
        body: JSON.stringify({ calls: statuses.length, elapsedMs: elapsed }) });
      expect(elapsed).toBeLessThan(MCP_WINDOW);
      expect(statuses).toEqual(Array(FREE_MCP_PER_MINUTE + 1).fill(true));
    }, { timeout: STAGE.mcpBurst });

    const openInvite = async () => {
      await page.goto(USERS);
      await page.getByRole('button', { name: 'Invite User' }).click();
      const dialog = page.getByRole('dialog').filter({ hasText: 'Invite new users' });
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      return dialog;
    };
    const fillInvite = async (dialog: ReturnType<Page['getByRole']>, emails: string[]) => {
      const input = dialog.getByLabel(/^Emails/);
      for (const email of emails) {
        await input.fill(email);
        await input.press('Enter');
      }
      // FormSearchSelectFieldControl opens its options as a menu.
      await dialog.getByLabel(/^Organization Role/).click();
      await page.getByRole('menu').getByRole('menuitem', { name: 'Admin', exact: true }).click();
    };

    await base.step('invite two admins from Settings > Users', async () => {
      const dialog = await openInvite();
      await fillInvite(dialog, [minted.m1, minted.m2]);
      const invited = page.waitForResponse(isPost(INVITE), { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Send Invite' }).click();
      const response = await invited;
      expect(response.status()).toBeLessThan(300);
      // Wire body pinned against accounts/serializers/rbac.py InviteCreateSerializer.
      expect(response.request().postDataJSON())
        .toEqual({ emails: [minted.m1, minted.m2], org_level: ADMIN, workspace_access: [] });
      expect(await invites()).toEqual([
        { target_email: minted.m1, status: 'Pending', level: ADMIN },
        { target_email: minted.m2, status: 'Pending', level: ADMIN },
      ]);
      await page.keyboard.press('Escape');
    }, { timeout: STAGE.inviteTwo });

    await base.step('a fourth member is an Enterprise gate, not an error', async () => {
      const dialog = await openInvite();
      await fillInvite(dialog, [minted.m4]);
      const refused = page.waitForResponse(isPost(INVITE), { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Send Invite' }).click();
      const response = await refused;
      expect(response.status()).toBe(402);
      const body: GateBody = await response.json();
      expect(body.error.code).toBe('ENTERPRISE_FEATURE_REQUIRED');
      expect(body.enterprise_gate).toEqual({ ...GATE_BASE, feature: 'members', limit: 3, current: 3,
        requested: 1, license_state: 'missing' });

      const gate = await gateDialog(page, 'Add more members with Enterprise',
        'Community includes up to 3 organization members. More members are an Enterprise feature.');
      expect(await invites()).toEqual([
        { target_email: minted.m1, status: 'Pending', level: ADMIN },
        { target_email: minted.m2, status: 'Pending', level: ADMIN },
      ]);
      expect(await pg().pg<Row>('SELECT id FROM accounts_user WHERE email = $1', [minted.m4])).toEqual([]);

      await gate.getByRole('button', { name: 'Activate license' }).click();
      await expect(page).toHaveURL(new RegExp(`${PLAN_LICENSE}$`), { timeout: UI_READY });
      await expect(page.getByText('Community · Self-hosted', { exact: true })).toBeVisible({ timeout: UI_READY });
    }, { timeout: STAGE.fourthMember });

    const createWorkspace = async (name: string) => {
      await page.goto(WORKSPACES);
      await page.getByRole('button', { name: 'Create New Workspace' }).click();
      const dialog = page.getByRole('dialog').filter({ hasText: 'Create New Workspace' });
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await dialog.getByLabel(/^Workspace Name/).fill(name);
      const created = page.waitForResponse(isPost(WORKSPACE_CREATE), { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Create', exact: true }).click();
      const response = await created;
      // Wire body pinned from WorkSpaceManagement.jsx handleCreateWorkspace.
      expect(response.request().postDataJSON())
        .toEqual({ name, display_name: name, emails: [], role: 'workspace_admin' });
      return response;
    };

    await base.step('a second workspace is an Enterprise gate', async () => {
      const response = await createWorkspace(minted.ws2);
      expect(response.status()).toBe(402);
      const body: GateBody = await response.json();
      expect(body.error.code).toBe('ENTERPRISE_FEATURE_REQUIRED');
      expect(body.enterprise_gate).toEqual({ ...GATE_BASE, feature: 'workspaces', limit: 1, current: 1,
        requested: 1, license_state: 'missing' });
      const gate = await gateDialog(page, 'Create more workspaces with Enterprise',
        'Community includes one workspace. More workspaces are an Enterprise feature.');
      expect((await activeWorkspaces()).map((w) => w.id)).toEqual([ids.workspaceId]);
      await gate.getByRole('button', { name: 'Close' }).click();
    }, { timeout: STAGE.secondWorkspace });

    // WorkspaceSwitcher.jsx: the trigger shows the workspace's display name; the
    // organization row (its display name) opens SelectOrganization, whose
    // "Create Organization" opens CreateOrganizationModal.
    const workspaceLabel = async () => {
      const [ws] = await pg().pg<{ display_name: string; name: string }>(
        'SELECT display_name, name FROM accounts_workspace WHERE id = $1', [ids.workspaceId]);
      return ws.display_name || ws.name;
    };
    const createOrganization = async (name: string) => {
      // The switcher is in the dashboard's main navigation (settings pages
      // have their own sidebar).
      await page.goto(GET_STARTED_PATH);
      await page.getByText(await workspaceLabel(), { exact: true }).first().click({ timeout: UI_READY });
      await page.getByText(minted.org, { exact: true }).last().hover();
      await page.getByRole('button', { name: 'Create Organization' }).click();
      const dialog = page.getByRole('dialog').filter({ hasText: 'Create new organization' });
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await dialog.getByLabel(/^Organization name/).fill(name);
      const created = page.waitForResponse(isPost(ORG_CREATE), { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Create organization' }).click();
      return created;
    };

    await base.step('a second organization is an Enterprise gate', async () => {
      const response = await createOrganization(minted.org2);
      expect(response.status()).toBe(402);
      const body: GateBody = await response.json();
      expect(body.error.code).toBe('ENTERPRISE_FEATURE_REQUIRED');
      expect(body.enterprise_gate).toEqual({ ...GATE_BASE, feature: 'organizations', limit: 1, current: 1,
        requested: 1, license_state: 'missing' });
      const gate = await gateDialog(page, 'Create more organizations with Enterprise',
        'Community includes one organization. More organizations are an Enterprise feature.');
      expect((await organizations()).map((o) => o.id)).toEqual([ids.organizationId]);
      await gate.getByRole('button', { name: 'Close' }).click();
    }, { timeout: STAGE.secondOrganization });

    const keysBefore = await api.get<KeysEnvelope>('/accounts/keys/');
    const invitesBefore = await pg().pg<Row>(
      'SELECT id, target_email FROM accounts_organization_invite WHERE organization_id = $1 ORDER BY target_email',
      [ids.organizationId]);

    await base.step('activate a test-signed licence: Enterprise, nothing lost', async () => {
      await setLaneLicence('enterprise', LICENCE_RESTART);
      const edition = page.waitForResponse(isGet(EDITION), { timeout: UI_READY });
      await page.goto(PLAN_LICENSE);
      const body: EditionEnvelope = await (await edition).json();
      expect(body.result.edition).toBe('enterprise');
      expect(body.result.license).toMatchObject({ state: 'active', issued_to: TEST_LICENCE.issuedTo,
        license_id_masked: TEST_LICENCE.maskedId });
      await expect(page.getByText('Enterprise · Self-hosted', { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(page.getByText(TEST_LICENCE.maskedId, { exact: true })).toBeVisible();

      const info = await api.get<UserInfo>('/accounts/user-info/');
      expect({ userId: info.id, organizationId: info.organization.id, workspaceId: info.default_workspace_id })
        .toEqual(ids);
      expect(await api.get<KeysEnvelope>('/accounts/keys/')).toEqual(keysBefore);
      expect(await pg().pg<Row>(
        'SELECT id, target_email FROM accounts_organization_invite WHERE organization_id = $1 ORDER BY target_email',
        [ids.organizationId])).toEqual(invitesBefore);
    }, { timeout: STAGE.activate });

    await base.step('with the licence the fourth member, second workspace and second organization succeed', async () => {
      const dialog = await openInvite();
      await fillInvite(dialog, [minted.m4]);
      const invited = page.waitForResponse(isPost(INVITE), { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Send Invite' }).click();
      expect((await invited).status()).toBeLessThan(300);
      await page.keyboard.press('Escape');
      expect((await invites()).map((i) => i.target_email)).toEqual([minted.m1, minted.m2, minted.m4]);

      expect((await createWorkspace(minted.ws2)).status()).toBeLessThan(300);
      await expect(page.getByText('Workspace created', { exact: true })).toBeVisible({ timeout: UI_READY });
      const workspaces = await activeWorkspaces();
      expect(workspaces.map((w) => w.id)[0]).toBe(ids.workspaceId);
      expect(workspaces.slice(1).map((w) => w.name)).toEqual([minted.ws2]);

      expect((await createOrganization(minted.org2)).status()).toBeLessThan(300);
      await expect(page.getByText('Organization created successfully', { exact: true }))
        .toBeVisible({ timeout: UI_READY });
      expect((await organizations()).map((o) => o.display_name)).toEqual([minted.org, minted.org2]);
    }, { timeout: STAGE.enterpriseCreates });

    const [org2] = await pg().pg<{ id: string }>('SELECT id FROM accounts_organization WHERE display_name = $1',
      [minted.org2]);
    const memberships = () => pg().pg<Row>(
      `SELECT organization_id, level, is_active FROM accounts_organization_membership
        WHERE user_id = $1 AND deleted = false ORDER BY joined_at`, [ids.userId]);
    const membershipsLicensed = await memberships();
    expect(membershipsLicensed).toEqual([
      { organization_id: ids.organizationId, level: OWNER, is_active: true },
      { organization_id: org2.id, level: OWNER, is_active: true },
    ]);
    const workspacesLicensed = await activeWorkspaces();
    const invitesLicensed = await invites();

    await base.step('the licence expires: Community again, everything kept, new creation refused', async () => {
      await setLaneLicence('expired', LICENCE_RESTART);
      const body = await api.get<EditionEnvelope>(EDITION);
      expect(body.result.edition).toBe('community');
      expect(body.result.license?.state).toBe('expired');
      expect(body.result.over_limit).toBe(true);
      expect(body.result.limits).toEqual({
        organizations: { limit: 1, current: 2 },
        workspaces: { limit: 1, current: 3 },
        members: { limit: 3, current: 4 },
      });

      await page.goto(PLAN_LICENSE);
      await expect(page.getByText('Community · Self-hosted', { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(page.getByText(/^This install has more organizations and workspaces/)).toBeVisible();

      expect(await organizations()).toHaveLength(2);
      expect(await activeWorkspaces()).toEqual(workspacesLicensed);
      expect(await memberships()).toEqual(membershipsLicensed);
      expect(await invites()).toEqual(invitesLicensed);
      // The owner still administers the second organization.
      const asOrg2 = new ApiClient(req, E2E.apiUrl).withAuth(
        { access: (await page.evaluate(() => localStorage.getItem('accessToken'))) ?? '', refresh: '' }, org2.id);
      expect((await asOrg2.get<EditionEnvelope>(EDITION)).result.license?.state).toBe('expired');

      await expectRefused(api.post(INVITE, { emails: [minted.m5], org_level: ADMIN, workspace_access: [] }), 'members');
      await expectRefused(api.post(ORG_CREATE, { name: minted.org3, display_name: minted.org3 }), 'organizations');
      const response = await createWorkspace(minted.ws3);
      expect(response.status()).toBe(402);
      await gateDialog(page, 'Create more workspaces with Enterprise',
        'Community includes one workspace. More workspaces are an Enterprise feature.');
      expect(await activeWorkspaces()).toEqual(workspacesLicensed);
      expect(await invites()).toEqual(invitesLicensed);
      expect(await organizations()).toHaveLength(2);
    }, { timeout: STAGE.expire });

    await base.step('the licence is removed: still Community, nothing lost, still refused', async () => {
      await setLaneLicence('removed', LICENCE_RESTART);
      const body = await api.get<EditionEnvelope>(EDITION);
      expect(body.result.edition).toBe('community');
      expect(body.result.license?.state).toBe('missing');
      await expectRefused(api.post(WORKSPACE_CREATE,
        { name: minted.ws3, display_name: minted.ws3, emails: [], role: 'workspace_admin' }), 'workspaces');
      expect(await activeWorkspaces()).toEqual(workspacesLicensed);
      expect(await memberships()).toEqual(membershipsLicensed);
    }, { timeout: STAGE.remove });
  } finally {
    await probe?.dispose();
    await req.dispose();
  }
});
