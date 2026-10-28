import { request } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// routes/paths.js and sections/keys/view/dev-keys-view.jsx.
const KEYS_PAGE = '/dashboard/keys';
// accounts/urls.py and views/keys.py: GetKeysView, SecretKeyAPIViewSet.
const BOOTSTRAP = '/accounts/keys/';
const KEY_LIST = '/accounts/key/get_secret_keys/';
const KEY_CREATE = '/accounts/key/generate_secret_key/';
const KEY_DELETE = '/accounts/key/delete_secret_key/';
// accounts/urls.py; MemberRemoveAPIView + MemberRemoveSerializer (user_id).
const REMOVE_MEMBER = '/accounts/organization/members/remove/';
// accounts/views/user.py user-info response identifies the authenticated user.
const USER_INFO = '/accounts/user-info/';
// auth/guard/auth-guard.jsx: revoked sessions return to sign-in.
const LOGIN_PAGE = /\/auth\/jwt\/login(?:\?|$)/;
// README: browser first-paint budget. All storage writes here are synchronous PG.
const UI_READY = 60_000;
// Only the assertion side changes for the cloud fallibility runs.
const wrong = process.env.E2E_KEYS_EXPECT_WRONG;

type Pair = { id: string; api_key: string; secret_key: string };
type Bootstrap = { status: string; data: Pair };
type KeyRow = { id: string; key_name: string; api_key: string; secret_key: string; type: string };
type KeyList = { result: { table: KeyRow[]; metadata: { total_rows: number } } };

test('SET-E2E-007: key access follows the current organization role and removal', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SET-E2E-007', area: 'settings',
    userGoal: 'Use only the keys allowed by the current organization role and lose access after removal',
    steps: [
      'bootstrap an Owner and Viewer using their own bearer sessions',
      'open each Keys page and compare exact allowed IDs, names and masked fields',
      'use each returned key for a permitted read and refuse a Viewer owner-only deletion',
      'remove the Viewer through the public membership endpoint',
      'refuse the existing bearer and personal key, then open a fresh browser at sign-in',
    ],
    backendChecks: [
      'A: bootstrap IDs map to an organization system key for the Owner and a user-bound personal key for the Viewer in Postgres',
      'B: each masked Keys UI row set equals the independently scoped management API and Postgres identities',
      'C: each key authenticates the intended user for reads; Viewer deletion is refused and the target remains unchanged',
      'D: public removal revokes bearer and key access, sends a fresh browser to sign-in and leaves no active or regenerated membership',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  test.setTimeout(360_000); // 5 × UI_READY (300s) + synchronous API/PG work (60s).
  const { ownerA: owner, viewer } = scopes;
  const name = `e2e-keys-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const ownerResult = await scopes.send<Bootstrap>(owner, 'GET', BOOTSTRAP);
  const viewerResult = await scopes.send<Bootstrap>(viewer, 'GET', BOOTSTRAP);
  expect(ownerResult.status).toBe(200);
  expect(viewerResult.status).toBe(200);
  const pairs = [ownerResult.body.data, viewerResult.body.data];
  const target = await owner.api.post<{ result: { key_id: string } }>(KEY_CREATE, { key_name: name });
  const targetId = target.result.key_id;
  await testInfo.attach('key-role-identities', {
    contentType: 'application/json',
    body: JSON.stringify({ organizationId: owner.organizationId, ownerId: owner.userId,
      viewerId: viewer.userId, systemKeyId: pairs[0].id, personalKeyId: pairs[1].id, targetId }),
  });

  await test.step('A: bootstrap ownership persists in Postgres', async () => {
    // accounts/models/user.py: OrgApiKey default Django table, no credential columns read.
    const rows = await probe.pg<{ id: string; type: string; user_id: string | null; organization_id: string }>(
      'SELECT id, type, user_id, organization_id FROM accounts_orgapikey WHERE id = ANY($1::uuid[]) AND NOT deleted',
      [pairs.map(pair => pair.id)],
    );
    const byId = Object.fromEntries(rows.map(row => [row.id, row]));
    expect(rows).toHaveLength(2);
    expect(byId[pairs[0].id]).toEqual({ id: pairs[0].id, type: 'system', user_id: null,
      organization_id: owner.organizationId });
    expect(byId[pairs[1].id], 'A: personal bootstrap ownership').toEqual({ id: pairs[1].id,
      type: wrong === 'bootstrap' ? 'system' : 'user', user_id: viewer.userId,
      organization_id: owner.organizationId });
  });

  for (const [index, actor] of [owner, viewer].entries()) {
    await test.step(`B: ${index === 0 ? 'Owner' : 'Viewer'} sees exact masked Keys rows`, async () => {
      const listed = await actor.api.get<KeyList>(KEY_LIST);
      const expectedIds = (index === 0 ? [pairs[0].id, pairs[1].id, targetId] : [pairs[1].id]).sort();
      expect(listed.result.table.map(row => row.id).sort()).toEqual(expectedIds);
      expect(listed.result.metadata.total_rows).toBe(expectedIds.length);
      if (index === 0) expect(listed.result.table.find(row => row.id === targetId)!.key_name).toBe(name);
      const context = await scopes.openContext(browser, actor);
      // Abort external assets/telemetry rather than contacting any production host.
      await context.route('**/*', route => new URL(route.request().url()).hostname === 'localhost'
        ? route.continue() : route.abort('blockedbyclient'));
      const page = await context.newPage();
      const loaded = page.waitForResponse(response => new URL(response.url()).pathname === KEY_LIST
        && response.request().method() === 'GET', { timeout: UI_READY });
      await page.goto(KEYS_PAGE, { waitUntil: 'domcontentloaded' });
      const response = await loaded;
      expect(response.status()).toBe(200);
      const uiRows = (await response.json() as KeyList).result.table;
      expect(uiRows.map(row => row.id).sort()).toEqual(expectedIds);
      // DataTable.jsx uses MUI DataGrid getRowId(row.id); dev-keys-view.jsx pins fields.
      const rendered = page.locator('[role="row"][data-id]');
      await expect(rendered).toHaveCount(expectedIds.length, { timeout: UI_READY });
      expect((await rendered.evaluateAll(nodes => nodes.map(node => node.getAttribute('data-id')))).sort())
        .toEqual(expectedIds);
      for (const row of uiRows) {
        const apiRow = listed.result.table.find(item => item.id === row.id)!;
        expect(row).toEqual(apiRow);
        expect(row.api_key).toMatch(/^.{4}\*{10}.{4}$/);
        expect(row.secret_key).toMatch(/^.{4}\*{10}.{4}$/);
        const uiRow = page.locator(`[role="row"][data-id="${row.id}"]`);
        await expect(uiRow.locator('[data-field="key_name"]'))
          .toHaveText(row.key_name, { timeout: UI_READY });
        expect(await uiRow.locator('[data-field="key_name"]').innerText(), 'B: exact allowed key name')
          .toBe(wrong === 'ui-rows' ? `${row.key_name}-wrong` : row.key_name);
        await expect(uiRow.locator('[data-field="api_key"]')).toHaveText(row.api_key, { timeout: UI_READY });
        await expect(uiRow.locator('[data-field="secret_key"]')).toHaveText(row.secret_key, { timeout: UI_READY });
      }
      await context.close();
    }, { timeout: UI_READY });
  }

  // A separate request context has NO bearer: success must come from this key pair.
  const keyClients = await Promise.all(pairs.map(pair => request.newContext({ baseURL: E2E.apiUrl,
    extraHTTPHeaders: { 'X-Api-Key': pair.api_key, 'X-Secret-Key': pair.secret_key } })));
  try {
    await test.step('C: credentials retain their principal and cannot elevate Viewer writes', async () => {
      for (const [index, actor] of [owner, viewer].entries()) {
        const response = await keyClients[index].get(USER_INFO);
        expect(response.status()).toBe(200);
        const info = await response.json();
        expect(info.id).toBe(actor.userId);
        expect(info.organization.id).toBe(actor.organizationId);
        expect(info.org_level).toBe(index === 0 ? 15 : 1);
        const reused = await keyClients[index].get(BOOTSTRAP);
        expect(reused.status()).toBe(200);
        expect((await reused.json() as Bootstrap).data.id).toBe(pairs[index].id);
      }
      const denied = await keyClients[1].delete(KEY_DELETE, { data: { key_id: targetId } });
      expect(denied.status(), 'C: Viewer cannot delete keys').toBe(wrong === 'key-action' ? 200 : 403);
      const rows = await probe.pg<{ id: string; deleted: boolean; enabled: boolean }>(
        'SELECT id, deleted, enabled FROM accounts_orgapikey WHERE id = $1', [targetId]);
      expect(rows).toEqual([{ id: targetId, deleted: false, enabled: true }]);
    });

    await test.step('D: removal denies fresh reads and cannot regenerate membership', async () => {
      const before = await probe.pg<{ id: string }>(
        'SELECT id FROM accounts_organization_membership WHERE user_id = $1 AND organization_id = $2',
        [viewer.userId, viewer.organizationId]);
      expect(before).toHaveLength(1);
      const removed = await scopes.send(owner, 'DELETE', REMOVE_MEMBER, { user_id: viewer.userId });
      expect(removed.status).toBe(200);
      for (const path of [BOOTSTRAP, KEY_LIST, USER_INFO]) {
        const bearer = await scopes.send(viewer, 'GET', path);
        expect(bearer.status).toBe(401);
        const key = await keyClients[1].get(path);
        expect(key.status()).toBe(403);
      }
      const after = await probe.pg<{ id: string; is_active: boolean }>(
        'SELECT id, is_active FROM accounts_organization_membership WHERE user_id = $1 AND organization_id = $2',
        [viewer.userId, viewer.organizationId]);
      expect(after, 'D: membership stays revoked').toEqual([{ id: before[0].id,
        is_active: wrong === 'removed-membership' }]);
      const workspaceRows = await probe.pg<{ id: string }>(
        'SELECT id FROM accounts_workspacemembership WHERE user_id = $1 AND is_active AND NOT deleted',
        [viewer.userId]);
      expect(workspaceRows).toEqual([]);
      const context = await scopes.openContext(browser, viewer);
      await context.route('**/*', route => new URL(route.request().url()).hostname === 'localhost'
        ? route.continue() : route.abort('blockedbyclient'));
      const page = await context.newPage();
      await page.goto(KEYS_PAGE, { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(LOGIN_PAGE, { timeout: UI_READY });
      await expect(page.locator('[role="row"][data-id]')).toHaveCount(0, { timeout: UI_READY });
      await context.close();
    }, { timeout: UI_READY });
  } finally {
    for (const client of keyClients) await client.dispose();
  }
});
