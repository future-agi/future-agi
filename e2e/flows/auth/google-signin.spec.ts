import { test, expect, request as pwRequest } from '@playwright/test';
import { E2E } from '../../lib/env';
import { provisionActor } from '../../lib/provisioning';
import { StateProbe } from '../../lib/state-probe';
import { googleFixture, type GoogleCase } from '../../lib/google-fixture';
import { flowAnnotation } from '../../lib/flow-meta';

// saml2_auth/urls.py; jwt-login-view.jsx; auth/guard/auth-guard.jsx.
const LOGIN = '/auth/jwt/login';
const CALLBACK = '/saml2_auth/auth/callback/';
const USER_INFO = '/accounts/user-info/';
const UI_READY = 60_000;
// Resolve only the owned Compose service name to its published loopback port.
// No browser request is mocked or fulfilled by Playwright.
test.use({ launchOptions: { args: ['--host-resolver-rules=MAP mock-google 127.0.0.1'] } });

test('AUTH-E2E-005: Google sign-in accepts only verified identities including rotated keys', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'AUTH-E2E-005', area: 'auth',
    userGoal: 'An existing user signs in with Google only when Google supplies a valid identity, including after signing-key rotation',
    steps: ['provision one uniquely named synthetic account', 'click Continue with Google on the real login page',
      'authorize the identity at the owned mock provider', 'see the authenticated dataset dashboard for a valid identity',
      'see login refusal for invalid signature, issuer, audience, expiry and unknown key',
      'sign in successfully after the provider rotates its signing key'],
    backendChecks: [
      'Successful Google callbacks persist exactly one new active AuthToken for the minted user and its existing organization.',
      'Rejected Google callbacks leave account and token counts unchanged and return the expected login-error redirect.',
      'The owned provider witnesses token exchange and JWKS verification; an unknown cached kid refreshes once, then succeeds only when the key is published.',
    ],
  }),
}, async ({ browser }, testInfo) => {
  // Eight attempts × three UI_READY stages + 120 s provisioning/cleanup.
  test.setTimeout(1_560_000);
  const req = await pwRequest.newContext({ baseURL: E2E.apiUrl });
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const actor = await provisionActor(req, `google-${suffix}`);
  const provider = await googleFixture();
  const probe = new StateProbe({ api: actor.api, chUrl: E2E.chUrl, chDatabase: E2E.chDatabase,
    chPassword: E2E.chPassword, pgUrl: E2E.pgUrl });
  await testInfo.attach('google-identity', { contentType: 'application/json',
    body: JSON.stringify({ email: actor.email, organizationId: actor.organizationId, workspaceId: actor.workspaceId }) });
  // accounts/models/{auth_token,user}.py: synchronous callback writes, not CDC.
  const tokens = () => probe.pg<{ id: string }>(
    `SELECT t.id FROM accounts_auth_token t JOIN accounts_user u ON u.id = t.user_id
     WHERE u.email = $1 AND t.is_active ORDER BY t.id`, [actor.email]);
  try {
    const cases: GoogleCase[] = ['valid', 'signature', 'issuer', 'audience', 'expired', 'unknown', 'rotated', 'valid'];
    for (const [index, mode] of cases.entries()) {
      await test.step(`${index + 1}: ${mode}`, async () => {
        const context = await browser.newContext({ baseURL: E2E.appUrl, ignoreHTTPSErrors: true,
          userAgent: 'FutureAGI-E2E-Google-Fixture' });
        try {
          const scenario = await provider.configure(context, actor.email, mode);
          const page = await context.newPage();
          const before = await tokens();
          const beforeEvents = await provider.events();
          await page.goto(LOGIN);
          await page.getByRole('button', { name: 'Continue with Google', exact: true }).click({ timeout: UI_READY });
          await expect(page.getByRole('heading', { name: 'Synthetic Google sign-in', exact: true })).toBeVisible({ timeout: UI_READY });
          const callback = page.waitForResponse(r => new URL(r.url()).pathname === CALLBACK, { timeout: UI_READY });
          const succeeds = mode === 'valid' || mode === 'rotated';
          const userInfo = succeeds ? page.waitForResponse(r => new URL(r.url()).pathname === USER_INFO && r.status() === 200,
            { timeout: UI_READY }) : null;
          await page.getByRole('button', { name: 'Authorize synthetic identity', exact: true }).click();
          const response = await callback;
          expect(response.status()).toBe(302);
          const location = response.headers().location;
          const query = new URL(location).searchParams;
          if (succeeds) {
            expect(query.has('sso_token')).toBe(true);
            expect(query.get('is_new_user')).toBe('false');
            const identity = await (await userInfo!).json();
            expect(identity.organization.id).toBe(actor.organizationId);
            // settings.py default_next_url and the captured authenticated browser.
            await expect(page).toHaveURL(`${E2E.appUrl}/dashboard/develop`, { timeout: UI_READY });
            await expect(page.getByRole('heading', { name: 'Datasets', exact: true })).toBeVisible({ timeout: UI_READY });
            expect((await tokens()).length - before.length, 'successful callback AuthToken delta').toBe(1);
          } else {
            expect(query.get('denied')).toBe('true');
            expect(query.has('sso_token')).toBe(false);
            await expect(page.getByText('Unable to process OAuth callback', { exact: true })).toBeVisible({ timeout: UI_READY });
            expect(await page.evaluate(() => localStorage.getItem('accessToken'))).toBeNull();
            expect((await tokens()).length - before.length, 'rejected callback AuthToken delta').toBe(0);
            expect(await tokens()).toEqual(before);
          }
          const users = await probe.pg<{ organization_id: string }>('SELECT organization_id FROM accounts_user WHERE email = $1', [actor.email]);
          expect(users).toEqual([{ organization_id: actor.organizationId }]);
          const events = await provider.events();
          expect(events.filter(e => e.scenario === scenario).map(e => e.kind)).toEqual(['authorization', 'token']);
          const fetched = events.filter(e => e.kind === 'jwks').length - beforeEvents.filter(e => e.kind === 'jwks').length;
          if (mode === 'unknown' || mode === 'rotated') {
            expect(fetched, 'unknown cached kid JWKS refresh delta').toBe(1);
          } else if (index === 0) {
            expect([0, 1]).toContain(fetched); // May already have a verified warm cache from a prior flow run.
            expect(events.filter(e => e.kind === 'jwks').length).toBeGreaterThan(0);
          } else {
            expect(fetched).toBe(0);
          }
          await testInfo.attach(`google-${index}-${mode}`, { contentType: 'application/json',
            body: JSON.stringify({ scenario, mode, status: response.status(), jwksFetches: fetched, tokensBefore: before.length,
              tokensAfter: (await tokens()).length }) });
          await testInfo.attach(`google-${index}-${mode}-browser`, { contentType: 'image/png', body: await page.screenshot() });
        } finally { await context.close(); }
      });
    }
  } finally {
    await provider.dispose();
    await probe.dispose();
    await req.dispose();
  }
});
