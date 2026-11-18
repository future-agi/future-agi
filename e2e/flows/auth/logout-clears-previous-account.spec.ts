import { test, expect, request as pwRequest } from '@playwright/test';
import type { Page, Request, Route } from '@playwright/test';
import { E2E } from '../../lib/env';
import { provisionActor, TestActor } from '../../lib/provisioning';
import { ApiClient } from '../../lib/api-client';
import { StateProbe } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// Deliberately NOT using lib/fixtures: its init script would re-seed the first
// account's tokens on every document, and this flow signs two accounts in and
// out through the real login form in one document.
const LOGIN = '/auth/jwt/login'; // flows/auth/login.spec.ts.
const TOKEN = '/accounts/token/';
const LOGOUT = '/accounts/logout/';
const ME = '/accounts/user-info/';
// accounts/views/workspace_management.py:ManageTeamView.post, the organization
// setup step: sets the display name and clears Organization.is_new, so sign-in
// lands in the dashboard instead of /auth/jwt/setup-org.
const ORG_SETUP = '/accounts/team/users/';
// frontend/src/utils/axios.js endpoints.errorFeed.list; its `search` param comes
// from useErrorFeedStore.searchQuery (pages/dashboard/error-feed/store.js).
const FEED = '/tracer/feed/issues/';
const OSS_VALIDATION_DONE = 'oss_validation_done';
const UI_READY = 60_000; // README Writing a flow: browser action/first-paint budget.
const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1']);

interface Identity { label: 'A' | 'B'; actor: TestActor; orgName: string; workspaceLabel: string }
interface WireRow {
  phase: string; method: string; path: string; search: string | null;
  organization: string | null; workspace: string | null; url: string; body: string;
}

test('AUTH-E2E-016: after a same-tab logout the next account sees none of the previous account\'s state', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'AUTH-E2E-016', area: 'auth',
    userGoal: 'After account A logs out in a tab, account B signing in on the same tab sees none of A\'s state',
    steps: [
      'sign in as account A through the login form',
      'open Error Feed from the sidebar and search for a value only A typed',
      'log out through the workspace menu and confirm',
      'sign in as account B through the login form in the same tab, without a page reload',
      'open Error Feed from the sidebar as B',
    ],
    backendChecks: [
      'both seeded organizations have completed setup in PG with their minted names',
      'A\'s Error Feed list request carries A\'s organization and A\'s search value',
      'no request B sends carries another organization, every request B sends from the app after signing in carries exactly B\'s organization, and no Error Feed list request of B carries a search value',
      'the access token left in the tab after B signs in resolves to B\'s user and organization',
      'no A-identifying value appears in B\'s page text, API requests, localStorage or sessionStorage',
    ],
  }),
}, async ({ page }, testInfo) => {
  test.setTimeout(480_000); // provisioning 60 + 6*UI_READY 360 + checks 60.
  page.setDefaultTimeout(UI_READY);
  page.setDefaultNavigationTimeout(UI_READY);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const attach = (name: string, value: unknown) => testInfo.attach(name, {
    contentType: 'application/json', body: JSON.stringify(value, null, 2),
  });
  const apiOrigin = new URL(E2E.apiUrl).origin;

  // ---- seed two accounts in two organizations -------------------------------
  const req = await pwRequest.newContext({ baseURL: E2E.apiUrl });
  const a = await provisionActor(req, 'auth5a');
  const b = await provisionActor(req, 'auth5b');
  const probe = new StateProbe({ api: a.api, chUrl: E2E.chUrl, chDatabase: E2E.chDatabase,
    chPassword: E2E.chPassword, pgUrl: E2E.pgUrl });
  const identities: Identity[] = [];
  for (const [label, actor] of [['A', a], ['B', b]] as const) {
    const orgName = `e2e-auth5-${label.toLowerCase()}-org-${suffix}`;
    await actor.api.post(ORG_SETUP, { org_name: orgName });
    const [ws] = await probe.pg<{ label: string }>(`SELECT coalesce(nullif(display_name, ''), name) AS label
      FROM accounts_workspace WHERE id = $1 AND organization_id = $2`, [actor.workspaceId, actor.organizationId]);
    identities.push({ label, actor, orgName, workspaceLabel: ws.label });
  }
  const [idA, idB] = identities;
  expect(a.organizationId).not.toBe(b.organizationId);
  expect(await probe.pg(`SELECT id::text, display_name, is_new FROM accounts_organization
    WHERE id = ANY($1::uuid[]) ORDER BY display_name`, [[a.organizationId, b.organizationId]]))
    .toEqual([
      { id: a.organizationId, display_name: idA.orgName, is_new: false },
      { id: b.organizationId, display_name: idB.orgName, is_new: false },
    ]);
  // Values that identify A and exist nowhere in B's account.
  const aSearch = `e2e-auth5-a-search-${suffix}`;
  const aValues = [aSearch, a.email, a.organizationId, a.workspaceId, idA.orgName];
  const tabMarker = `e2e-auth5-tab-${suffix}`;
  await attach('seeded-accounts', {
    aSearch, tabMarker,
    accounts: identities.map(({ label, actor, orgName, workspaceLabel }) => ({
      label, email: actor.email, organizationId: actor.organizationId, workspaceId: actor.workspaceId,
      orgName, workspaceLabel,
    })),
  });

  // ---- network: abort non-local hosts, record API requests by phase -----------
  const aborted: string[] = [];
  await page.context().route('**/*', async (route: Route) => {
    const url = new URL(route.request().url());
    if (url.protocol.startsWith('http') && !LOCAL_HOSTS.has(url.hostname)) {
      aborted.push(`${url.protocol}//${url.host}`);
      await route.abort();
      return;
    }
    await route.fallback();
  });
  let phase = 'setup';
  const wire: WireRow[] = [];
  const documents: { phase: string; url: string }[] = [];
  page.on('request', (request: Request) => {
    if (request.resourceType() === 'document') documents.push({ phase, url: request.url() });
    const url = new URL(request.url());
    if (url.origin !== apiOrigin) return;
    const headers = request.headers();
    wire.push({ phase, method: request.method(), path: url.pathname, search: url.searchParams.get('search'),
      organization: headers['x-organization-id'] ?? null, workspace: headers['x-workspace-id'] ?? null,
      url: request.url(), body: request.postData() ?? '' });
  });
  const apiResponse = (path: string, method: string, test: (url: URL) => boolean = () => true) =>
    page.waitForResponse(r => {
      const url = new URL(r.url());
      return url.origin === apiOrigin && url.pathname === path && r.request().method() === method && test(url);
    }, { timeout: UI_READY });

  const signIn = async (who: Identity) => {
    await expect(page.getByLabel(/email/i)).toBeVisible({ timeout: UI_READY });
    await page.getByLabel(/email/i).fill(who.actor.email);
    await page.getByLabel(/password/i).fill(who.actor.password);
    const token = apiResponse(TOKEN, 'POST');
    await page.getByRole('button', { name: 'Continue' }).click();
    expect((await token).status()).toBe(200);
    // Organization setup is complete, so sign-in lands inside the dashboard.
    await expect(page).toHaveURL(/\/dashboard\//, { timeout: UI_READY });
    await expect(page.getByText(who.workspaceLabel, { exact: true }).filter({ visible: true }))
      .toBeVisible({ timeout: UI_READY });
  };
  const openErrorFeed = async (who: Identity) => {
    const list = apiResponse(FEED, 'GET');
    await page.getByRole('link', { name: 'Error Feed', exact: true }).click();
    await expect(page).toHaveURL(/\/dashboard\/error-feed$/, { timeout: UI_READY });
    const response = await list;
    expect(response.status()).toBe(200);
    expect(response.request().headers()['x-organization-id']).toBe(who.actor.organizationId);
    return page.getByPlaceholder('Search errors');
  };
  const storage = () => page.evaluate(() => ({
    localStorage: Object.fromEntries(Object.keys(localStorage).map(k => [k, localStorage.getItem(k)])),
    sessionStorage: Object.fromEntries(Object.keys(sessionStorage).map(k => [k, sessionStorage.getItem(k)])),
  }));
  const aValuesIn = (text: string) => aValues.filter(value => text.includes(value));

  await test.step('A signs in and searches the Error Feed', async () => {
    phase = 'A';
    await page.goto(LOGIN, { waitUntil: 'domcontentloaded' });
    // This browser already ran the install's first-run checks
    // (sections/oss-first-run/ossFlowState.js); without that an OSS logout lands on /setup.
    await page.evaluate(key => localStorage.setItem(key, 'true'), OSS_VALIDATION_DONE);
    await signIn(idA);
    // The last full page load of the flow: from here on the tab keeps one document.
    await page.evaluate(marker => { (window as unknown as { e2eTab: string }).e2eTab = marker; }, tabMarker);
    phase = 'A in-app';
    const search = await openErrorFeed(idA);
    const searched = apiResponse(FEED, 'GET', url => url.searchParams.get('search') === aSearch);
    await search.fill(aSearch);
    const response = await searched;
    expect(response.status()).toBe(200);
    expect(response.request().headers()['x-organization-id']).toBe(a.organizationId);
    await expect(search).toHaveValue(aSearch);
  });

  await test.step('A logs out through the workspace menu', async () => {
    phase = 'logout';
    await page.getByText(idA.workspaceLabel, { exact: true }).filter({ visible: true }).click();
    await page.getByText('Log out', { exact: true }).click();
    const logout = apiResponse(LOGOUT, 'POST');
    await page.getByRole('dialog').getByRole('button', { name: 'Logout', exact: true }).click();
    expect((await logout).status()).toBe(200);
    await expect(page).toHaveURL(/\/auth\/jwt\/login/, { timeout: UI_READY });
    expect(await page.evaluate(() => localStorage.getItem('accessToken'))).toBeNull();
  });

  let bAccess = '';
  await test.step('B signs in on the same tab and opens the Error Feed', async () => {
    phase = 'B sign-in';
    await signIn(idB);
    phase = 'B';
    const search = await openErrorFeed(idB);
    // Same document as A's session: no reload dropped the in-memory stores.
    expect(await page.evaluate(() => (window as unknown as { e2eTab?: string }).e2eTab)).toBe(tabMarker);
    expect(documents.filter(d => d.phase !== 'setup' && d.phase !== 'A')).toEqual([]);

    // UI lane: the search box and the page show nothing of A.
    await expect.soft(search, 'Error Feed search box after B signs in').toHaveValue('');
    expect.soft(aValuesIn(await page.locator('body').innerText()), 'A values in B\'s page text').toEqual([]);

    // Storage lane: browser storage holds B's session and nothing of A.
    const stored = await storage();
    await attach('storage-after-b', stored);
    expect.soft(aValuesIn(JSON.stringify(stored)), 'A values in browser storage').toEqual([]);
    expect(stored.sessionStorage.organizationId).toBe(b.organizationId);
    bAccess = stored.localStorage.accessToken ?? '';
  });

  await test.step('backend: B\'s session is B\'s, and B\'s requests carry B\'s organization', async () => {
    await attach('wire', wire);
    await attach('aborted-non-local-requests', [...new Set(aborted)].sort());
    const orgsOn = (rows: WireRow[]) => [...new Set(rows.map(row => row.organization).filter(Boolean))];
    // Positive control: A's own session sent A's organization.
    expect(orgsOn(wire.filter(row => row.phase.startsWith('A')))).toEqual([a.organizationId]);
    // B's side starts at B's sign-in form. Sign-in requests (deployment-info, token, the first
    // user-info) carry no organization header, so the strict check covers B's in-app requests.
    const bSide = wire.filter(row => row.phase.startsWith('B'));
    const bInApp = wire.filter(row => row.phase === 'B');
    expect(bInApp.filter(row => row.path === FEED).length).toBeGreaterThan(0);
    expect.soft(orgsOn(bSide), 'organizations on B\'s API requests').toEqual([b.organizationId]);
    expect.soft([...new Set(bInApp.map(row => row.organization))], 'organization on every in-app request of B')
      .toEqual([b.organizationId]);
    expect.soft([...new Set(bInApp.filter(row => row.path === FEED).map(row => row.search))],
      'search values on B\'s Error Feed list requests').toEqual([null]);
    expect.soft(aValuesIn(JSON.stringify(bSide)), 'A values in B\'s API requests').toEqual([]);

    // The token the tab holds now belongs to B, in B's organization.
    expect(bAccess).not.toBe('');
    const tabSession = new ApiClient(req, E2E.apiUrl).withAuth({ access: bAccess, refresh: '' });
    const me = await tabSession.get<{ email: string; organization: { id: string } }>(ME);
    expect({ email: me.email, organization: me.organization.id })
      .toEqual({ email: b.email, organization: b.organizationId });
  });

  await probe.dispose();
  await req.dispose();
});
