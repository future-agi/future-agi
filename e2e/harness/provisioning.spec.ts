import { request } from '@playwright/test';
import { test, expect } from '../lib/fixtures';
import { E2E } from '../lib/env';
import { provisionActor, type UserInfo } from '../lib/provisioning';

test('provisionActor creates an isolated, API-usable tenant', async () => {
  const req = await request.newContext({ baseURL: E2E.apiUrl });
  const actor = await provisionActor(req, 'selftest');
  expect(actor.organizationId).toMatch(/^[0-9a-f-]{36}$/);
  expect(actor.workspaceId).toBeTruthy();
  expect(actor.apiKey).toBeTruthy();
  expect(actor.secretKey).toBeTruthy();
  // The authed client works and is scoped to the fresh org.
  const info = await actor.api.get<{ organization: { id: string } }>('/accounts/user-info/');
  expect(info.organization.id).toBe(actor.organizationId);
  // Two actors never share a tenant.
  const actor2 = await provisionActor(req, 'selftest2');
  expect(actor2.organizationId).not.toBe(actor.organizationId);
  await req.dispose();
});

test('authenticated page fixture lands on the dashboard', async ({ page, actor }) => {
  await page.goto('/dashboard/observe');
  await expect(page).toHaveURL(/\/dashboard\/observe/);
  // user-info fired with the actor's org header and succeeded
  const userInfo = page.waitForResponse(r => r.url().includes('/accounts/user-info/') && r.status() === 200);
  await page.reload();
  await userInfo;
});

test('the ordinary actor is a new org: created in this run, restricted to Environments', async ({ actor, probe }, testInfo) => {
  // The database's own clock: Colima's VM clock can drift from the host's.
  const rows = await probe.pg<{ fresh: boolean }>(
    "SELECT created_at > now() - interval '6 hours' AS fresh FROM accounts_organization WHERE id = $1",
    [actor.organizationId]);
  expect(rows).toEqual([{ fresh: true }]);

  const info = await actor.api.get<UserInfo>('/accounts/user-info/');
  expect(info.organization.id).toBe(actor.organizationId);
  // Both stacks set the cutoff to 2026-10-01T00:00:00Z, before any run's signup.
  expect(info.restrict_simulate_to_environments).toBe(true);
});
