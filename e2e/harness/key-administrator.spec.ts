import { test as adminTest, expect } from '../lib/key-administrator';
import { test as ordinaryTest } from '../lib/fixtures';
import { E2E } from '../lib/env';

adminTest('key administrator fixture grants only key view/change and can use admin', async ({ page, actor, probe }) => {
  const users = await probe.pg<{ is_staff: boolean; is_superuser: boolean }>(
    'SELECT is_staff, is_superuser FROM accounts_user WHERE email = $1', [actor.email]);
  expect(users).toEqual([{ is_staff: true, is_superuser: false }]);
  const permissions = await probe.pg<{ codename: string }>(
    `SELECT p.codename FROM auth_permission p JOIN accounts_user_user_permissions up
     ON p.id = up.permission_id JOIN accounts_user u ON u.id = up.user_id
     WHERE u.email = $1 ORDER BY p.codename`, [actor.email]);
  expect(permissions.map(p => p.codename)).toEqual(['change_orgapikey', 'view_orgapikey']);
  await page.goto(`${E2E.apiUrl}/admin/login/`);
  await page.locator('#id_username').fill(actor.email);
  await page.locator('#id_password').fill(actor.password);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page).toHaveURL(`${E2E.apiUrl}/admin/`, { timeout: 60_000 });
});

ordinaryTest('ordinary actor cannot log into the admin', async ({ page, actor, probe }) => {
  const users = await probe.pg<{ is_staff: boolean; is_superuser: boolean }>(
    'SELECT is_staff, is_superuser FROM accounts_user WHERE email = $1', [actor.email]);
  expect(users).toEqual([{ is_staff: false, is_superuser: false }]);
  await page.goto(`${E2E.apiUrl}/admin/login/`);
  await page.locator('#id_username').fill(actor.email);
  await page.locator('#id_password').fill(actor.password);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.locator('.errornote')).toHaveText(
    'Please enter the correct email and password for a staff account. Note that both fields may be case-sensitive.',
    { timeout: 60_000 });
  await page.goto(`${E2E.apiUrl}/admin/accounts/orgapikey/`);
  await expect(page).toHaveURL(/\/admin\/login\//, { timeout: 60_000 });
});
