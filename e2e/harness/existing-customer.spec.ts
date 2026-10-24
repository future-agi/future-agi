import { test, expect, EXISTING_CUSTOMER_CREATED_AT } from '../lib/existing-customer';
import type { UserInfo } from '../lib/provisioning';

// One browser action's first-paint budget (see README "UI_READY").
const UI_READY = 60_000;

test('the existing-customer actor is an org dated before the Simulate cutoff, and the signed-in page is that org', async ({ page, actor, probe }, testInfo) => {
  await testInfo.attach('existing-customer', { contentType: 'application/json', body: JSON.stringify({ email: actor.email, organizationId: actor.organizationId, workspaceId: actor.workspaceId }) });
  const rows = await probe.pg<{ created_at: Date }>(
    'SELECT created_at FROM accounts_organization WHERE id = $1', [actor.organizationId]);
  expect(rows).toHaveLength(1);
  expect(rows[0].created_at.toISOString()).toBe(EXISTING_CUSTOMER_CREATED_AT);

  // The request the app makes at boot, with the page's own org header.
  const userInfo = page.waitForResponse(r => r.url().includes('/accounts/user-info/')
    && r.request().method() === 'GET' && r.status() === 200, { timeout: UI_READY });
  await page.goto('/dashboard/observe');
  const info = await (await userInfo).json() as UserInfo;
  expect(info.organization.id).toBe(actor.organizationId);

  expect(info.restrict_simulate_to_environments).toBe(false);
});
