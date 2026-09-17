import { test as base, expect, request as pwRequest } from '@playwright/test';
import { E2E } from '../../lib/env';
import { provisionActor } from '../../lib/provisioning';
import { Tokens } from '../../lib/api-client';
import { StateProbe } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// Deliberately NOT using lib/fixtures: this flow tests the login UI itself,
// so it starts from a clean, unauthenticated context.
base('AUTH-E2E-001: user signs in with email and password', {
  tag: ['@flow', '@smoke'],
  annotation: flowAnnotation({
    id: 'AUTH-E2E-001', area: 'auth',
    userGoal: 'A new user signs in with email and password and is routed to organization setup',
    steps: ['open login page', 'fill email + password', 'submit',
            'land on the organization setup step'],
    backendChecks: ['POST /accounts/token/ returns 200 with a token pair',
                    'the UI login persists a new active AuthToken row in PG for the user'],
  }),
}, async ({ page }) => {
  const req = await pwRequest.newContext({ baseURL: E2E.apiUrl });
  const actor = await provisionActor(req, 'login');
  const probe = new StateProbe({ api: actor.api, chUrl: E2E.chUrl, chDatabase: E2E.chDatabase, pgUrl: E2E.pgUrl });
  const activeTokens = () => probe.pg<{ id: string }>(
    `SELECT t.id FROM accounts_auth_token t JOIN accounts_user u ON t.user_id = u.id
     WHERE u.email = $1 AND t.is_active`, [actor.email]);

  await page.goto('/auth/jwt/login');
  await page.getByLabel(/email/i).fill(actor.email);
  await page.getByLabel(/password/i).fill(actor.password);
  // provisionActor already logged in over the API, so active rows exist before the UI submit;
  // only their growth proves the browser login persisted its own.
  const before = (await activeTokens()).length;
  const tokenResp = page.waitForResponse(r => r.url().includes('/accounts/token/') && r.status() === 200);
  await page.getByRole('button', { name: 'Continue' }).click();
  const pair: Tokens = await (await tokenResp).json();
  expect(pair.access).toMatch(/.+/);
  expect(pair.refresh).toMatch(/.+/);
  // A freshly signed-up org has is_new = true, so login routes to organization setup.
  await expect(page).toHaveURL(/\/auth\/jwt\/setup-org/, { timeout: 15_000 });

  expect((await activeTokens()).length).toBeGreaterThan(before);
  await probe.dispose();
  await req.dispose();
});
