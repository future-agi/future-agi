import { expect, test } from '@playwright/test';
import type { APIRequestContext, BrowserContext, Page, Request } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import { authInitScript } from '../../lib/auth';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { provisionActor, type TestActor } from '../../lib/provisioning';
import { startLocalSamlIdp, type LocalSamlIdp } from '../../lib/saml-idp';
import { StateProbe } from '../../lib/state-probe';

const RESULTS = path.join(
  path.dirname(path.dirname(path.dirname(fileURLToPath(import.meta.url)))),
  'test-results',
  'saml-requests.json',
);
const SAML_PATHS = new Set(['/saml2_auth/acs/', '/saml2_auth/complete/', '/accounts/user-info/']);
const receipts: RequestReceipt[] = [];
const pendingReceipts: Promise<void>[] = [];

interface RequestReceipt {
  flow: string;
  method: string;
  path: string;
  cookieNames: string[];
  authorizationPrefix: string | null;
  hasOrganizationHeader: boolean;
  hasWorkspaceHeader: boolean;
}

interface SamlTokenRow {
  auth_origin: string;
  scoped_organization_id: string;
  state: string;
}

function recordRequests(page: Page, flow: string): void {
  page.on('request', (request: Request) => {
    const url = new URL(request.url());
    if (!SAML_PATHS.has(url.pathname)) return;
    // headers() leaves out Cookie; allHeaders() has the cookies the browser
    // actually sent, which is what the binder checks are about.
    pendingReceipts.push(request.allHeaders().then(headers => {
      const cookieNames = (headers.cookie ?? '')
        .split(';')
        .map(cookie => cookie.trim().split('=', 1)[0])
        .filter(Boolean)
        .sort();
      const authorization = headers.authorization;
      receipts.push({
        flow,
        method: request.method(),
        path: url.pathname,
        cookieNames,
        authorizationPrefix: authorization ? authorization.split(' ', 1)[0] : null,
        hasOrganizationHeader: 'x-organization-id' in headers,
        hasWorkspaceHeader: 'x-workspace-id' in headers,
      });
    }));
  });
}

async function settledReceipts(): Promise<RequestReceipt[]> {
  await Promise.allSettled(pendingReceipts);
  return receipts;
}

async function uploadIdp(
  request: APIRequestContext,
  actor: TestActor,
  metadata: string,
): Promise<void> {
  const response = await request.post(`${E2E.apiUrl}/saml2_auth/idp-uploads/`, {
    headers: {
      Authorization: `Bearer ${actor.tokens.access}`,
      'X-Organization-Id': actor.organizationId,
      'X-Workspace-Id': actor.workspaceId,
    },
    multipart: {
      file: { name: 'e2e-idp.xml', mimeType: 'application/xml', buffer: Buffer.from(metadata) },
      // sso_name_validator: a letter first, then letters, digits or hyphens.
      name: 'e2e-local-idp',
      identity_type: '2',
      is_enabled: 'true',
    },
  });
  expect(response.status(), await response.text()).toBe(200);
}

async function setupSamlActor(
  request: APIRequestContext,
  label: string,
): Promise<{ actor: TestActor; idp: LocalSamlIdp; probe: StateProbe }> {
  const idp = await startLocalSamlIdp();
  const actor = await provisionActor(request, label);
  await uploadIdp(request, actor, idp.metadata);
  const probe = new StateProbe({
    api: actor.api,
    chUrl: E2E.chUrl,
    chDatabase: E2E.chDatabase,
    chPassword: E2E.chPassword,
    pgUrl: E2E.pgUrl,
  });
  return { actor, idp, probe };
}

async function initiate(page: Page, email: string, next = '/dashboard/develop'): Promise<string> {
  return page.evaluate(async ({ apiUrl, userEmail, nextPath }) => {
    const url = new URL('/saml2_auth/idp-login/', apiUrl);
    url.searchParams.set('email', userEmail);
    url.searchParams.set('next', nextPath);
    const response = await fetch(url, { credentials: 'include' });
    const body = await response.json();
    if (!response.ok) throw new Error(`SAML initiation failed: ${response.status}`);
    return body.result.url as string;
  }, { apiUrl: E2E.apiUrl, userEmail: email, nextPath: next });
}

async function completeBrowserLogin(
  page: Page,
  idp: LocalSamlIdp,
  email: string,
  next = '/dashboard/develop',
): Promise<string> {
  await page.goto('/auth/jwt/login', { waitUntil: 'domcontentloaded' });
  await idp.setIdentity(email);
  const idpUrl = await initiate(page, email, next);
  expect(new URL(idpUrl).hostname).toBe('127.0.0.1');
  const callback = page.waitForURL(
    url => url.origin === E2E.appUrl && url.pathname.startsWith('/dashboard/'),
    { timeout: 30_000 },
  );
  await page.goto(idpUrl, { waitUntil: 'domcontentloaded' });
  await callback;
  return page.evaluate(() => localStorage.getItem('accessToken') ?? '');
}

async function dispose(idp: LocalSamlIdp, probe: StateProbe): Promise<void> {
  await Promise.all([idp.stop(), probe.dispose()]);
}

test.describe('SAML tenant isolation', () => {
  test.describe.configure({ mode: 'serial' });

  test.afterAll(async () => {
    await mkdir(path.dirname(RESULTS), { recursive: true });
    await writeFile(RESULTS, `${JSON.stringify(await settledReceipts(), null, 2)}\n`);
  });

  test('SAML-E2E-001: signed login issues one organization-scoped session', {
    tag: ['@flow', '@saml'],
    annotation: flowAnnotation({
      id: 'SAML-E2E-001', area: 'auth',
      userGoal: 'A member signs in through the tenant IdP and receives one scoped session',
      steps: ['provision an organization member and its local IdP metadata', 'initiate from the browser', 'complete the cross-site signed response', 'render the authenticated dashboard'],
      backendChecks: ['the AuthToken row has auth_origin=saml and the actor organization scope', 'the shared login attempt reaches consumed', 'redacted callback request receipts contain no secret values'],
    }),
  }, async ({ page, request }) => {
    const { actor, idp, probe } = await setupSamlActor(request, 'saml-happy');
    try {
      recordRequests(page, 'SAML-E2E-001');
      const token = await completeBrowserLogin(page, idp, actor.email);
      expect(token).not.toBe('');
      expect(new URL(page.url()).search).toBe('');
      const rows = await probe.pg<SamlTokenRow>(
        `SELECT t.auth_origin, t.scoped_organization_id, a.state
         FROM accounts_auth_token t
         JOIN accounts_user u ON u.id = t.user_id
         JOIN saml_login_attempt a ON a.user_hint_id = u.id
         WHERE u.email = $1 AND t.auth_origin = 'saml'
         ORDER BY t.created_at DESC LIMIT 1`,
        [actor.email],
      );
      expect(rows).toEqual([{
        auth_origin: 'saml', scoped_organization_id: actor.organizationId, state: 'consumed',
      }]);
    } finally {
      await dispose(idp, probe);
    }
  });

  test('SAML-E2E-002: another browser cannot consume the initiating browser candidate', {
    tag: ['@flow', '@saml'],
    annotation: flowAnnotation({
      id: 'SAML-E2E-002', area: 'auth',
      userGoal: 'The browser that initiated SSO can complete after another browser posts first',
      steps: ['start SSO in browser X', 'let browser Y post the signed response', 'observe Y fail without the binder', 'complete the same candidate in X'],
      backendChecks: ['no SAML token exists after Y fails', 'X consumes the pending attempt and receives exactly one token'],
    }),
  }, async ({ browser, request }) => {
    const { actor, idp, probe } = await setupSamlActor(request, 'saml-binding');
    let x: BrowserContext | undefined;
    let y: BrowserContext | undefined;
    try {
      x = await browser.newContext({ baseURL: E2E.appUrl });
      y = await browser.newContext({ baseURL: E2E.appUrl });
      const pageX = await x.newPage();
      const pageY = await y.newPage();
      recordRequests(pageX, 'SAML-E2E-002-X');
      recordRequests(pageY, 'SAML-E2E-002-Y');
      await pageX.goto('/auth/jwt/login', { waitUntil: 'domcontentloaded' });
      await pageY.goto('/auth/jwt/login', { waitUntil: 'domcontentloaded' });
      await idp.setIdentity(actor.email);
      const idpUrl = await initiate(pageX, actor.email);
      const acsResponse = pageY.waitForResponse(response =>
        new URL(response.url()).pathname === '/saml2_auth/acs/' && response.request().method() === 'POST',
      );
      await pageY.goto(idpUrl, { waitUntil: 'domcontentloaded' });
      const candidate = new URL((await acsResponse).headers().location, E2E.apiUrl).searchParams.get('c');
      expect(candidate).toBeTruthy();
      expect(await probe.pg<{ id: string }>(
        `SELECT t.id FROM accounts_auth_token t JOIN accounts_user u ON u.id = t.user_id
         WHERE u.email = $1 AND t.auth_origin = 'saml'`, [actor.email],
      )).toEqual([]);

      await pageX.goto(`${E2E.apiUrl}/saml2_auth/complete/?c=${candidate}`, { waitUntil: 'domcontentloaded' });
      await expect(pageX).toHaveURL(/\/dashboard\//, { timeout: 30_000 });
      expect(await probe.pg<{ id: string }>(
        `SELECT t.id FROM accounts_auth_token t JOIN accounts_user u ON u.id = t.user_id
         WHERE u.email = $1 AND t.auth_origin = 'saml'`, [actor.email],
      )).toHaveLength(1);
    } finally {
      await Promise.all([x?.close(), y?.close(), dispose(idp, probe)]);
    }
  });

  test('SAML-E2E-003: the cross-site ACS post omits the binder and completion carries it', {
    tag: ['@flow', '@saml'],
    annotation: flowAnnotation({
      id: 'SAML-E2E-003', area: 'auth',
      userGoal: 'A real cross-site IdP POST does not leak the browser binding cookie',
      steps: ['initiate SSO from localhost', 'navigate to the 127.0.0.1 IdP', 'inspect ACS and completion requests'],
      backendChecks: ['ACS has no fai_saml_b cookie', 'completion has exactly one fai_saml_b cookie'],
    }),
  }, async ({ page, request }) => {
    const { actor, idp, probe } = await setupSamlActor(request, 'saml-lax');
    try {
      recordRequests(page, 'SAML-E2E-003');
      await completeBrowserLogin(page, idp, actor.email);
      const flowReceipts = (await settledReceipts()).filter(receipt => receipt.flow === 'SAML-E2E-003');
      const acs = flowReceipts.find(receipt => receipt.path === '/saml2_auth/acs/');
      const completion = flowReceipts.find(receipt => receipt.path === '/saml2_auth/complete/');
      expect(acs?.cookieNames.filter(name => name.startsWith('fai_saml_b_'))).toEqual([]);
      expect(completion?.cookieNames.filter(name => name.startsWith('fai_saml_b_'))).toHaveLength(1);
    } finally {
      await dispose(idp, probe);
    }
  });

  test('SAML-E2E-004: safe next survives the binding while external next values do not', {
    tag: ['@flow', '@saml'],
    annotation: flowAnnotation({
      id: 'SAML-E2E-004', area: 'auth',
      userGoal: 'A SAML user returns to the approved deep page and cannot be redirected off-site',
      steps: ['complete a login with a deep relative next', 'repeat with protocol-relative next', 'repeat with an absolute external next'],
      backendChecks: ['the deep relative path is rendered after the barrier', 'unsafe next values land on the default dashboard route'],
    }),
  }, async ({ page, request }) => {
    const { actor, idp, probe } = await setupSamlActor(request, 'saml-next');
    try {
      await completeBrowserLogin(page, idp, actor.email, '/dashboard/develop/runs');
      await expect(page).toHaveURL(/\/dashboard\/develop\/runs$/);
      await completeBrowserLogin(page, idp, actor.email, '//evil.example');
      expect(new URL(page.url()).origin).toBe(E2E.appUrl);
      await completeBrowserLogin(page, idp, actor.email, 'https://evil.example');
      expect(new URL(page.url()).origin).toBe(E2E.appUrl);
    } finally {
      await dispose(idp, probe);
    }
  });

  test('SAML-E2E-005: a SAML landing resolves the authenticated organization without configuration writes', {
    tag: ['@flow', '@saml'],
    annotation: flowAnnotation({
      id: 'SAML-E2E-005', area: 'auth',
      userGoal: 'A SAML session lands in the organization that signed the assertion',
      steps: ['complete a signed login', 'read the current organization', 'inspect the account configuration row'],
      backendChecks: ['the current organization is the bound SAML organization', 'the callback did not write User.config'],
    }),
  }, async ({ page, request }) => {
    const { actor, idp, probe } = await setupSamlActor(request, 'saml-org');
    try {
      const before = await probe.pg<{ config: unknown }>(
        'SELECT config FROM accounts_user WHERE email = $1', [actor.email],
      );
      const token = await completeBrowserLogin(page, idp, actor.email);
      const current = await page.evaluate(async ({ apiUrl, accessToken }) => {
        const response = await fetch(new URL('/accounts/organizations/current/', apiUrl), {
          headers: { Authorization: `Bearer ${accessToken}` },
        });
        return { status: response.status, body: await response.json() };
      }, { apiUrl: E2E.apiUrl, accessToken: token });
      expect(current.status).toBe(200);
      expect(JSON.stringify(current.body)).toContain(actor.organizationId);
      expect(await probe.pg<{ config: unknown }>(
        'SELECT config FROM accounts_user WHERE email = $1', [actor.email],
      )).toEqual(before);
    } finally {
      await dispose(idp, probe);
    }
  });

  test('SAML-E2E-006: SAML replaces a live password session before any stale scope is sent', {
    tag: ['@flow', '@saml'],
    annotation: flowAnnotation({
      id: 'SAML-E2E-006', area: 'auth',
      userGoal: 'A SAML callback replaces a live session without using its stale organization scope',
      steps: ['render a live password session for another organization', 'open the SAML callback in the same tab', 'inspect the first protected request and browser storage', 'try an explicit old organization selector'],
      backendChecks: ['the first user-info request uses the SAML bearer and no organization or workspace header', 'refresh and stale selector state are absent after the barrier', 'a forced old organization selector is denied'],
    }),
  }, async ({ page, request }) => {
    const { actor: samlActor, idp, probe } = await setupSamlActor(request, 'saml-replace');
    const passwordActor = await provisionActor(request, 'password-replace');
    try {
      await page.goto('/auth/jwt/login', { waitUntil: 'domcontentloaded' });
      await page.evaluate(authInitScript, {
        access: passwordActor.tokens.access,
        refresh: passwordActor.tokens.refresh,
        organizationId: passwordActor.organizationId,
        workspaceId: passwordActor.workspaceId,
      });
      await page.evaluate(orgId => sessionStorage.setItem('workspaceOrgId', orgId), passwordActor.organizationId);
      await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
      recordRequests(page, 'SAML-E2E-006');
      await idp.setIdentity(samlActor.email);
      const idpUrl = await initiate(page, samlActor.email);
      const callback = page.waitForURL(
        url => url.origin === E2E.appUrl && url.pathname.startsWith('/dashboard/'),
        { timeout: 30_000 },
      );
      await page.goto(idpUrl, { waitUntil: 'domcontentloaded' });
      await callback;
      const samlToken = await page.evaluate(() => localStorage.getItem('accessToken') ?? '');
      await expect.poll(() => receipts.filter(receipt =>
        receipt.flow === 'SAML-E2E-006' && receipt.path === '/accounts/user-info/',
      ).length).toBeGreaterThan(0);
      const firstUserInfo = receipts.find(receipt =>
        receipt.flow === 'SAML-E2E-006' && receipt.path === '/accounts/user-info/',
      );
      expect(firstUserInfo).toMatchObject({
        authorizationPrefix: 'Bearer', hasOrganizationHeader: false, hasWorkspaceHeader: false,
      });
      const storage = await page.evaluate(() => ({
        refresh: localStorage.getItem('refreshToken'),
        organization: sessionStorage.getItem('organizationId'),
        workspace: sessionStorage.getItem('workspaceId'),
      }));
      // The SAML bootstrap clears all of this. The app then re-pins the
      // organization (and remember_me) from the SAML user-info response, so
      // what must hold is no refresh token and none of the password session's
      // selectors.
      expect(storage.refresh).toBeNull();
      expect([null, samlActor.organizationId]).toContain(storage.organization);
      expect(storage.workspace).not.toBe(passwordActor.workspaceId);
      const forced = await page.evaluate(async ({ apiUrl, accessToken, organizationId }) => {
        const response = await fetch(new URL('/accounts/user-info/', apiUrl), {
          headers: {
            Authorization: `Bearer ${accessToken}`,
            'X-Organization-Id': organizationId,
          },
        });
        return { status: response.status, body: await response.json() };
      }, {
        apiUrl: E2E.apiUrl, accessToken: samlToken, organizationId: passwordActor.organizationId,
      });
      expect(forced.status).toBe(403);
      expect(JSON.stringify(forced.body)).toContain('saml_scope_conflict');
    } finally {
      await dispose(idp, probe);
    }
  });
});
