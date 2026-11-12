import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { provisionActor } from '../../lib/provisioning';
import { authInitScript } from '../../lib/auth';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// accounts/urls.py and accounts/serializers/org_api_key.py (personal issuer).
const CREATE = '/accounts/key/generate_secret_key/';
const LIST = '/accounts/key/get_secret_keys/';
const BOOTSTRAP = '/accounts/keys/';
const USER_INFO = '/accounts/user-info/';
// frontend/src/routes/paths.js; both pages share CreateApiKey and KeysEmptyState.
const ROUTES = ['/dashboard/keys', '/dashboard/settings/api_keys'];
const EMPTY = 'No API keys yet. Create a personal key to use the SDK, CLI or OTLP.';
const UI_READY = 60_000;
type Pair = { key_id: string; key_name: string; api_key: string; secret_key: string; masked_api_key: string; masked_secret_key: string };
type List = { result: { table: { id: string; key_name: string; api_key: string; secret_key: string; type: string }[]; metadata: { total_rows: number } } };

// Show-once credentials must never enter screenshots, trace snapshots or videos.
test.use({ trace: 'off', screenshot: 'off', video: 'off' });

test('SET-E2E-009: a person explicitly creates and saves workspace-scoped and unrestricted keys', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SET-E2E-009', area: 'settings',
    userGoal: 'A person starting without keys explicitly creates and saves a personal key with the workspace access they selected.',
    steps: [
      'open both Keys routes with zero keys and open each prefilled form without creating a credential',
      'name a key, select the current workspace, submit once and save the one-time pair and export snippet',
      'create a second named key with no workspace restriction from the other Keys route',
      'reload both routes and reopen creation; read only masked metadata without another mint',
      'authenticate a read with each saved pair as the same signed-in person',
    ],
    backendChecks: [
      'Both page/form mounts leave the fresh organization key set empty and make no bootstrap or issuance request.',
      'Each explicit submit creates exactly one personal row for the session person and organization with respectively the selected workspace UUID and NULL.',
      'The key-list API and both UI routes expose only masked values after dismissal, reopen and reload without additional rows.',
      'Each newly issued pair authenticates as its stored person.',
    ],
  }),
}, async ({ browser, probe }, testInfo) => {
  test.setTimeout(540_000); // 8 * UI_READY + 60 s provisioning/API/storage headroom.
  const req = await request.newContext({ baseURL: E2E.apiUrl, timeout: UI_READY });
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  try {
    // The provisioning callback runs before its explicit fixture key mint.
    // The entire no-key goal runs inside the callback, not on a shared worker org.
    await provisionActor(req, `set9-${suffix}`, async fresh => {
      const info = await fresh.api.get<{ id: string }>(USER_INFO);
      const rows = () => probe.pg<{ id: string; name: string; user_id: string; organization_id: string; workspace_id: string | null; type: string; enabled: boolean; deleted: boolean }>(
        'SELECT id, name, user_id, organization_id, workspace_id, type, enabled, deleted FROM accounts_orgapikey WHERE organization_id=$1 ORDER BY name', [fresh.organizationId]);
      await testInfo.attach('identity', { contentType: 'application/json', body: JSON.stringify({ organizationId: fresh.organizationId, workspaceId: fresh.workspaceId, userId: info.id }) });
      const context = await browser.newContext({ baseURL: E2E.appUrl });
      await context.addInitScript(authInitScript, { access: fresh.tokens.access, refresh: fresh.tokens.refresh, organizationId: fresh.organizationId, workspaceId: fresh.workspaceId });
      // No production or third-party browser traffic, even if a bundle requests it.
      await context.route('**/*', route => new URL(route.request().url()).hostname === 'localhost' ? route.continue() : route.abort('blockedbyclient'));
      const page = await context.newPage();
      const traffic: string[] = [];
      page.on('request', r => {
        const path = new URL(r.url()).pathname;
        if (path === BOOTSTRAP || (path === CREATE && r.method() === 'POST')) traffic.push(path);
      });
      const pairs: Pair[] = [];
      const names = [`e2e-set9-${suffix}-selected`, `e2e-set9-${suffix}-unrestricted`];
      try {
        for (const route of ROUTES) {
          await test.step(`mount without issuance: ${route}`, async () => {
            await page.goto(route, { waitUntil: 'domcontentloaded' });
            await expect(page.getByText(EMPTY, { exact: true })).toBeVisible({ timeout: UI_READY });
            await page.getByRole('button', { name: 'Create a key', exact: true }).click();
            const dialog = page.getByRole('dialog', { name: 'Create a personal key' });
            await expect(dialog.getByLabel('Key name')).toHaveValue(/^Personal key, /, { timeout: UI_READY });
            expect(await rows()).toEqual([]);
            expect(traffic).toEqual([]);
            await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
          }, { timeout: UI_READY });
        }
        for (const [index, route] of ROUTES.entries()) {
          await test.step(`explicit creation: ${route}`, async () => {
            await page.goto(route, { waitUntil: 'domcontentloaded' });
            await page.getByRole('button', { name: index === 0 ? 'Create a key' : 'Add API Key', exact: true }).click({ timeout: UI_READY });
            const dialog = page.getByRole('dialog');
            await dialog.getByLabel('Key name').fill(names[index]);
            if (index === 0) {
              await dialog.getByRole('combobox', { name: 'Workspace (optional)' }).click();
              const current = page.getByRole('option').filter({ hasNotText: 'No workspace restriction' });
              await expect(current).toHaveCount(1, { timeout: UI_READY });
              await current.click();
            }
            const pending = page.waitForResponse(r => new URL(r.url()).pathname === CREATE && r.request().method() === 'POST', { timeout: UI_READY });
            await dialog.getByRole('button', { name: 'Create a key', exact: true }).click();
            const response = await pending;
            expect(response.status()).toBe(200);
            expect(response.request().postDataJSON()).toEqual({ key_name: names[index], workspace_id: index === 0 ? fresh.workspaceId : null });
            const { result: pair } = await response.json() as { result: Pair };
            // Booleans deliberately keep raw credentials out of assertion output.
            expect(/^[0-9a-f]{32}$/i.test(pair.api_key)).toBe(true);
            expect(/^[0-9a-f]{32}$/i.test(pair.secret_key)).toBe(true);
            pairs.push(pair);
            await testInfo.attach(`issued-${index}`, { contentType: 'application/json', body: JSON.stringify({ keyId: pair.key_id, name: names[index] }) });
            await expect(page.getByRole('dialog', { name: 'Save your personal key' })).toBeVisible({ timeout: UI_READY });
            expect(await dialog.getByLabel('API key', { exact: true }).inputValue() === pair.api_key).toBe(true);
            expect(await dialog.getByLabel('Secret key', { exact: true }).inputValue() === pair.secret_key).toBe(true);
            expect(await dialog.getByLabel('Shell environment').inputValue() === `export FI_API_KEY='${pair.api_key}' FI_SECRET_KEY='${pair.secret_key}'`).toBe(true);
            for (const label of ['Copy API key', 'Copy Secret key', 'Copy export snippet']) await expect(dialog.getByRole('button', { name: label, exact: true })).toBeVisible({ timeout: UI_READY });
            await dialog.getByRole('button', { name: 'I saved it', exact: true }).click();
            await expect(dialog).toHaveCount(0, { timeout: UI_READY });
            expect(traffic).toEqual(Array(index + 1).fill(CREATE));
            expect(await rows()).toEqual(pairs.map((p, i) => ({ id: p.key_id, name: names[i], user_id: info.id, organization_id: fresh.organizationId, workspace_id: i === 0 ? fresh.workspaceId : null, type: 'user', enabled: true, deleted: false })));
          }, { timeout: UI_READY });
        }
        for (const route of ROUTES) {
          await test.step(`masked after reload: ${route}`, async () => {
            await page.goto(route, { waitUntil: 'domcontentloaded' });
            await page.reload({ waitUntil: 'domcontentloaded' });
            const listed = await fresh.api.get<List>(LIST);
            expect(listed.result.table.map(r => r.id).sort()).toEqual(pairs.map(p => p.key_id).sort());
            expect(listed.result.metadata.total_rows).toBe(2);
            // DevKeysView uses MUI DataTable; ApiKeysLandingPage uses AG Grid.
            // Each supplies the backend UUID as its row identity.
            const rowAttribute = route === ROUTES[0] ? 'data-id' : 'row-id';
            const columnAttribute = route === ROUTES[0] ? 'data-field' : 'col-id';
            await expect(page.locator(`[role="row"][${rowAttribute}]`)).toHaveCount(2, { timeout: UI_READY });
            for (const pair of pairs) {
              const row = listed.result.table.find(r => r.id === pair.key_id)!;
              expect(row.type).toBe('user');
              expect(row.key_name).toBe(pair.key_name);
              expect(row.api_key === pair.masked_api_key && row.secret_key === pair.masked_secret_key).toBe(true);
              expect(/^.{4}\*{10}.{4}$/.test(row.api_key) && /^.{4}\*{10}.{4}$/.test(row.secret_key)).toBe(true);
              const ui = page.locator(`[role="row"][${rowAttribute}="${pair.key_id}"]`);
              await expect(ui.locator(`[${columnAttribute}="key_name"]`)).toHaveText(pair.key_name, { timeout: UI_READY });
              // Do not echo a raw list value into the HTML report if masking regresses.
              expect(await ui.locator(`[${columnAttribute}="api_key"]`).innerText() === pair.masked_api_key).toBe(true);
              expect(await ui.locator(`[${columnAttribute}="secret_key"]`).innerText() === pair.masked_secret_key).toBe(true);
            }
            await page.getByRole('button', { name: 'Add API Key', exact: true }).click();
            await expect(page.getByRole('dialog', { name: 'Create a personal key' })).toBeVisible({ timeout: UI_READY });
            expect(await page.evaluate(ps => {
              const text = document.body.innerText + Array.from(document.querySelectorAll('input,textarea')).map(n => (n as HTMLInputElement).value).join('\n') + JSON.stringify(localStorage) + JSON.stringify(sessionStorage);
              return ps.every(p => !text.includes(p.api_key) && !text.includes(p.secret_key));
            }, pairs)).toBe(true);
            await page.getByRole('dialog').getByRole('button', { name: 'Cancel', exact: true }).click();
            expect(traffic).toEqual([CREATE, CREATE]);
            expect((await rows()).map(r => r.id).sort()).toEqual(pairs.map(p => p.key_id).sort());
          }, { timeout: UI_READY });
        }
        await test.step('saved pairs authenticate as the same person', async () => {
          for (const pair of pairs) {
            const response = await req.get(USER_INFO, { headers: { 'X-Api-Key': pair.api_key, 'X-Secret-Key': pair.secret_key, 'X-Organization-Id': fresh.organizationId, 'X-Workspace-Id': fresh.workspaceId } });
            expect(response.status()).toBe(200);
            expect((await response.json()).id).toBe(info.id);
          }
        }, { timeout: UI_READY });
      } finally { await page.close(); await context.close(); }
    });
  } finally { await req.dispose(); }
});
