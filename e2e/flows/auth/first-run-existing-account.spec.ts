import { test as base, expect, request as pwRequest } from '@playwright/test';
import { E2E } from '../../lib/env';
import { provisionActor } from '../../lib/provisioning';
import { ApiClient, type Tokens } from '../../lib/api-client';
import { StateProbe } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// frontend/src/routes/paths.js: ossSetup, auth.jwt.login and auth.jwt.setup_org.
const SETUP = '/setup';
const LOGIN = /\/auth\/jwt\/login$/;
const SETUP_ORG = /\/auth\/jwt\/setup-org/;
// futureagi/tfc/urls.py setup-checks; frontend/src/api/ossSetup/oss-setup.js sends
// the launch mode as `mode`, LAUNCH_MODE.EXPERIMENT (oss-first-run/constants.js).
const SETUP_CHECKS = '/api/setup-checks/';
const TEST_FLIGHT = 'experiment';
// frontend/src/utils/axios.js endpoints.auth.login; jwt-login-view.jsx posts { email, password, … }.
const TOKEN = '/accounts/token/';
// oss-first-run/constants.js accountStep: the next step once an account exists.
const SIGN_IN_STEP = 'This instance already has an account: sign in with it on the next screen.';
// tfc/views/setup_checks.py SNAPSHOT_TTL_SECONDS (3 s): a snapshot cached before the
// owner existed can still answer for that long.
const SNAPSHOT_FRESH = 15_000;
const UI_READY = 60_000; // README Writing a flow: browser action/first-paint budget.

// tfc/utils/api_serializers.py SetupChecksResponseSerializer, in success_response's envelope.
interface SetupChecks { result: { account_exists: boolean; setup: string; checks: { id: string; status: string }[] } }

// Deliberately NOT using lib/fixtures: the first-run screen and sign-in are
// signed-out pages, so this starts from a clean, unauthenticated context.
base('AUTH-E2E-003: first-run setup sends an existing owner to sign in', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'AUTH-E2E-003', area: 'auth',
    userGoal: 'An operator whose install already created the owner account runs the first-run checks at /setup and is sent to sign in with that account instead of signing up again',
    steps: ['an owner account exists before the first visit (signed up over the API; ./bin/install runs create_user)',
            'open /setup in a fresh, signed-out browser', 'choose Test flight and continue',
            'read the pre-flight checks and the next step, which says to sign in', 'press Continue',
            'land on the sign-in page and sign in with the existing account'],
    backendChecks: ['the owner is an active accounts_user row in PG, which is what account_exists reads',
                    'the GET /api/setup-checks/?mode=experiment response the screen used reports account_exists: true',
                    'Continue routes to /auth/jwt/login, not /auth/jwt/register',
                    'signing in as the owner returns a token pair from POST /accounts/token/ and moves on to organization setup'],
  }),
}, async ({ page }, testInfo) => {
  // 15 s snapshot wait + 8 × UI_READY (480 s), plus provisioning and navigation.
  base.setTimeout(540_000);
  const req = await pwRequest.newContext({ baseURL: E2E.apiUrl });
  const owner = await provisionActor(req, 'first-run');
  const probe = new StateProbe({ api: owner.api, chUrl: E2E.chUrl, chDatabase: E2E.chDatabase,
    chPassword: E2E.chPassword, pgUrl: E2E.pgUrl });
  try {
    await testInfo.attach('seeded-owner', { contentType: 'application/json', body: JSON.stringify({
      email: owner.email, organizationId: owner.organizationId, workspaceId: owner.workspaceId }) });

    expect(await probe.pg<{ is_active: boolean }>('SELECT is_active FROM accounts_user WHERE email = $1',
      [owner.email])).toEqual([{ is_active: true }]);
    // Readiness, not an assertion: the screen reads the same public endpoint below.
    const anon = new ApiClient(req, E2E.apiUrl);
    await expect.poll(async () => (await anon.get<SetupChecks>(SETUP_CHECKS, { mode: TEST_FLIGHT })).result.account_exists,
      { timeout: SNAPSHOT_FRESH }).toBe(true);

    await page.goto(SETUP);
    await expect(page.getByRole('heading', { name: 'Plan your launch' })).toBeVisible({ timeout: UI_READY });
    const testFlight = page.getByRole('button', { name: /^Test flight/ });
    await testFlight.click();
    await expect(testFlight).toHaveAttribute('aria-pressed', 'true', { timeout: UI_READY });
    const checksResponse = page.waitForResponse(r => {
      const url = new URL(r.url());
      return url.pathname === SETUP_CHECKS && url.searchParams.get('mode') === TEST_FLIGHT;
    }, { timeout: UI_READY });
    await page.getByRole('button', { name: 'Continue', exact: true }).click();
    const checks = await checksResponse;
    expect(checks.status()).toBe(200);
    const snapshot: SetupChecks = await checks.json();
    await testInfo.attach('setup-checks', { contentType: 'application/json', body: JSON.stringify(snapshot.result) });
    expect(snapshot.result.account_exists).toBe(true);

    await expect(page.getByText(SIGN_IN_STEP, { exact: true })).toBeVisible({ timeout: UI_READY });
    const next = page.getByRole('button', { name: 'Continue', exact: true });
    await expect(next).toBeEnabled({ timeout: UI_READY });
    await next.click();
    await expect(page).toHaveURL(LOGIN, { timeout: UI_READY });

    await page.getByLabel(/email/i).fill(owner.email);
    await page.getByLabel(/password/i).fill(owner.password);
    const tokenResponse = page.waitForResponse(r => new URL(r.url()).pathname === TOKEN &&
      r.request().method() === 'POST', { timeout: UI_READY });
    await page.getByRole('button', { name: 'Continue', exact: true }).click();
    const signIn = await tokenResponse;
    expect(signIn.status()).toBe(200);
    expect(signIn.request().postDataJSON().email).toBe(owner.email);
    const pair: Tokens = await signIn.json();
    expect(pair.access).toMatch(/.+/);
    expect(pair.refresh).toMatch(/.+/);
    // A freshly signed-up org has is_new = true, so sign-in moves on to organization setup.
    await expect(page).toHaveURL(SETUP_ORG, { timeout: UI_READY });
  } finally {
    await probe.dispose();
    await req.dispose();
  }
});
