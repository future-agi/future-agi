import { request } from '@playwright/test';
import { test, expect } from '../lib/fixtures';
import { E2E } from '../lib/env';
import { provisionActor } from '../lib/provisioning';

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
