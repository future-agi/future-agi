import { test, expect } from '../../lib/backend-request';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { provisionActor } from '../../lib/provisioning';

const UI_READY = 60_000;

test('AUTH-E2E-020: an invited member sets a password and signs in', {
  tag: '@flow',
  annotation: flowAnnotation({
    id: 'AUTH-E2E-020', area: 'auth',
    userGoal: 'Join the inviting organization using a password that meets the displayed requirements.',
    steps: ['open a new member invitation signed out', 'enter and confirm a password with a slash but no digit',
      'submit the form and reach organization setup'],
    backendChecks: ['the browser signs in as the invited email in the inviting organization'],
  }),
}, async ({ page, request }) => {
  test.setTimeout(240_000);
  const owner = await provisionActor(request, 'invite-owner');
  const email = `member-${owner.email}`;
  const { result } = await owner.api.post<{ result: { invites: { email: string; invite_link: string }[] } }>(
    '/accounts/organization/invite/', { emails: [email], org_level: 3,
      workspace_access: [{ workspace_id: owner.workspaceId, level: 3 }] });
  const invite = result.invites.find(item => item.email === email);
  if (!invite) throw new Error('Expected a link for the invited member');

  await page.goto(new URL(invite.invite_link, E2E.appUrl).pathname);
  const password = 'Cedar/Meadow';
  await page.getByLabel('Password', { exact: true }).fill(password, { timeout: UI_READY });
  await page.getByLabel('Confirm Password', { exact: true }).fill(password);
  const [me] = await Promise.all([
    page.waitForResponse(r => new URL(r.url()).pathname === '/accounts/user-info/' && r.request().method() === 'GET',
      { timeout: UI_READY }),
    page.getByRole('button', { name: 'Set Password & Continue' }).click(),
  ]);
  expect(me.status()).toBe(200);
  const account = await me.json();
  expect(account.email).toBe(email);
  expect(account.organization.id).toBe(owner.organizationId);
  await expect(page).toHaveURL(/\/auth\/jwt\/setup-org\b/, { timeout: UI_READY });
});
