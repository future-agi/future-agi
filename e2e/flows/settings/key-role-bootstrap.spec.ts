import { request } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { provisionActor } from '../../lib/provisioning';
import { createPersonalKey } from '../../lib/personal-key';
import { seedRetiredKey } from '../../lib/retired-key-fixture';

// accounts/urls.py, accounts/views/keys.py and serializers/org_api_key.py.
const BOOTSTRAP = '/accounts/keys/';
const CREATE = '/accounts/key/generate_secret_key/';
const LIST = '/accounts/key/get_secret_keys/';
const REVOKE = '/accounts/key/delete_secret_key/';
const REMOVE = '/accounts/organization/members/remove/';
const USER_INFO = '/accounts/user-info/';
// accounts/serializers/rbac.py MemberRoleUpdateSerializer; Level: Admin=8, Member=3, Viewer=1.
const ROLE = '/accounts/organization/members/role/';
const DISABLE = '/accounts/key/disable_key/';
const ENABLE = '/accounts/key/enable_key/';
// routes/paths.js, dev-keys-view.jsx and DataTable.jsx: MUI rows carry data-id.
const KEYS_PAGE = '/dashboard/keys';
const UI_READY = 60_000;
type KeyRow = { id: string; key_name: string; api_key: string; secret_key: string; type: string };
type KeyList = { result: { table: KeyRow[]; metadata: { total_rows: number } } };

// Draft until the composed frontend/backend managed run and per-check
// fallibility passes. No retries or skips conceal a missing product contract.
test('SET-E2E-007: Personal keys only', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SET-E2E-007', area: 'settings',
    userGoal: 'Create and use only my personal keys, with access ending when my membership ends',
    steps: [
      'sign up a fresh account, verify zero keys and the retired bootstrap, then explicitly create a personal key',
      'create personal keys and compare Owner, Admin, Member and workspace Member masked Keys pages with stored identities',
      'refuse retired legacy credentials over header, Basic and both MCP transports',
      'refuse API-key issuance and a Member administrative action; let the Owner revoke a member key',
      'remove the Member and verify the personal key stays refused without recreating membership',
    ],
    backendChecks: [
      'fresh signup and repeated bootstrap 410 leave zero API-key rows before explicit session issuance',
      'Member and workspace Member list only own keys; Owner and Admin list masked organization personal metadata; all four Keys pages match exact stored IDs without issuing on mount',
      'a seeded retired key returns 401 api_key_retired only with the correct secret on header, Basic and MCP',
      'API-key issuance and Member admin writes return 403; non-admins cannot modify others keys; own disable and enable work; Owner and Admin revocation disable the exact selected personal keys',
      'removed-member key calls return 401 and neither organization nor workspace membership heals',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  test.setTimeout(360_000); // Four UI_READY waits plus provisioning/API/storage headroom.
  const name = `e2e-personal-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const anonymous = await request.newContext({ baseURL: E2E.apiUrl });
  const { ownerA: owner, member } = scopes;
  try {
    await provisionActor(anonymous, 'personal-fresh', async fresh => {
      const count = () => probe.pg<{ n: number }>('SELECT count(*)::int AS n FROM accounts_orgapikey WHERE organization_id=$1', [fresh.organizationId]);
      expect(await count()).toEqual([{ n: 0 }]);
      for (let attempt = 0; attempt < 2; attempt++) {
        const response = await anonymous.get(BOOTSTRAP, { headers: { Authorization: `Bearer ${fresh.tokens.access}` } });
        expect(response.status()).toBe(410);
        expect(await response.json()).toEqual({ code: 'org_keys_retired', create_url: KEYS_PAGE });
        expect(await count()).toEqual([{ n: 0 }]);
      }
    });
    const personal = await createPersonalKey(member.api, `${name}-member`);
    const revocable = await createPersonalKey(member.api, `${name}-revoke`);
    const adminRevocable = await createPersonalKey(member.api, `${name}-admin-revoke`);
    const workspaceMember = scopes.viewer;
    expect((await scopes.send(owner, 'POST', ROLE, {
      user_id: workspaceMember.userId, ws_level: 3, workspace_id: workspaceMember.workspaceId,
    })).status).toBe(200);
    const workspacePersonal = await createPersonalKey(workspaceMember.api, `${name}-workspace-member`);
    const retired = await seedRetiredKey(owner, `${name}-legacy`);
    await testInfo.attach('personal-key-identities', { contentType: 'application/json', body: JSON.stringify({
      organizationId: owner.organizationId, ownerId: owner.userId, memberId: member.userId,
      personalKeyId: personal.key_id, revocableKeyId: revocable.key_id, retiredKeyId: retired.key_id,
      workspaceMemberId: workspaceMember.userId, workspacePersonalKeyId: workspacePersonal.key_id,
      adminRevocableKeyId: adminRevocable.key_id,
    }) });

    // Reuse the public-invited actor, first as a workspace Member and then as
    // an org Admin. No SQL role changes, additional identities or fixture bypass.
    for (const { actor, role, allKeys, orgLevel, wsLevel } of [
      { actor: member, role: 'Member', allKeys: false, orgLevel: 3, wsLevel: 3 },
      { actor: workspaceMember, role: 'workspace Member', allKeys: false, orgLevel: 1, wsLevel: 3 },
      { actor: owner, role: 'Owner', allKeys: true, orgLevel: 15, wsLevel: 15 },
      { actor: workspaceMember, role: 'Admin', allKeys: true, orgLevel: 8, wsLevel: 8 },
    ]) {
      if (role === 'Admin') expect((await scopes.send(owner, 'POST', ROLE, {
        user_id: actor.userId, org_level: 8, ws_level: 8, workspace_id: actor.workspaceId,
      })).status).toBe(200);
      const info = await actor.api.get<{ org_level: number; ws_level: number }>(USER_INFO);
      expect(info).toMatchObject({ org_level: orgLevel, ws_level: wsLevel });
      const listed = await actor.api.get<KeyList>(LIST);
      const rows = await probe.pg<{ id: string }>(
        `SELECT id FROM accounts_orgapikey WHERE organization_id=$1 AND NOT deleted AND type='user'
         AND ($2::boolean OR user_id=$3) ORDER BY id`, [actor.organizationId, allKeys, actor.userId]);
      const ids = rows.map(row => row.id).sort();
      expect(listed.result.table.map(row => row.id).sort()).toEqual(ids);
      expect(listed.result.metadata.total_rows).toBe(ids.length);
      if (actor === member) expect(ids).toEqual([personal.key_id, revocable.key_id, adminRevocable.key_id].sort());
      if (role === 'workspace Member') expect(ids).toEqual([workspacePersonal.key_id]);
      if (!allKeys) {
        const other = actor === member ? workspacePersonal.key_id : personal.key_id;
        for (const [method, path] of [['POST', DISABLE], ['POST', ENABLE], ['DELETE', REVOKE]]) {
          expect((await scopes.send(actor, method, path, { key_id: other })).status).toBe(400);
          expect(await probe.pg('SELECT enabled, deleted FROM accounts_orgapikey WHERE id=$1', [other]))
            .toEqual([{ enabled: true, deleted: false }]);
        }
      }
      for (const row of listed.result.table) {
        expect(row.type).toBe('user');
        // Keep credentials out of failure reports if a masking regression occurs.
        expect(/^.{4}\*{10}.{4}$/.test(row.api_key)).toBe(true);
        expect(/^.{4}\*{10}.{4}$/.test(row.secret_key)).toBe(true);
      }
      const context = await scopes.openContext(browser, actor);
      const network = { local: 0, nonLoopbackAttempts: 0 };
      try {
        await context.route('**/*', route => {
          if (new URL(route.request().url()).hostname === 'localhost') {
            network.local++;
            return route.continue();
          }
          network.nonLoopbackAttempts++;
          return route.abort('blockedbyclient');
        });
        const page = await context.newPage();
        const issuance: string[] = [];
        page.on('request', request => {
          const path = new URL(request.url()).pathname;
          if (path === BOOTSTRAP || (path === CREATE && request.method() === 'POST')) issuance.push(path);
        });
        await page.goto(KEYS_PAGE, { waitUntil: 'domcontentloaded' });
        await expect(page).toHaveURL(new RegExp(`${KEYS_PAGE}$`), { timeout: UI_READY });
        await expect(page.getByRole('link', { name: 'Keys', exact: true }).first()).toBeVisible({ timeout: UI_READY });
        const rendered = page.locator('[role="row"][data-id]');
        await expect(rendered).toHaveCount(ids.length, { timeout: UI_READY });
        expect((await rendered.evaluateAll(nodes => nodes.map(node => node.getAttribute('data-id')))).sort()).toEqual(ids);
        for (const row of listed.result.table) {
          const ui = page.locator(`[role="row"][data-id="${row.id}"]`);
          await expect(ui.locator('[data-field="key_name"]')).toHaveText(row.key_name, { timeout: UI_READY });
          await expect(ui.locator('[data-field="api_key"]')).toHaveText(row.api_key, { timeout: UI_READY });
          await expect(ui.locator('[data-field="secret_key"]')).toHaveText(row.secret_key, { timeout: UI_READY });
        }
        await page.getByRole('button', { name: 'Add API Key', exact: true }).click({ timeout: UI_READY });
        const dialog = page.getByRole('dialog', { name: 'Create a personal key' });
        await expect(dialog.getByLabel('Key name')).toHaveValue(/^Personal key /, { timeout: UI_READY });
        await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
        expect(issuance).toEqual([]);
        expect((await actor.api.get<KeyList>(LIST)).result.table.map(row => row.id).sort()).toEqual(ids);
        await testInfo.attach(`keys-role-${role}`, { contentType: 'application/json', body: JSON.stringify({ role, userId: actor.userId, keyIds: ids }) });
      } finally {
        await context.close();
        await testInfo.attach(`network-${role}`, { contentType: 'application/json', body: JSON.stringify(network) });
        expect(network.nonLoopbackAttempts).toBe(0);
      }
    }
    for (const [path, enabled] of [[DISABLE, false], [ENABLE, true]] as const) {
      expect((await scopes.send(member, 'POST', path, { key_id: personal.key_id })).status).toBe(200);
      expect(await probe.pg('SELECT enabled, deleted FROM accounts_orgapikey WHERE id=$1', [personal.key_id]))
        .toEqual([{ enabled, deleted: false }]);
    }
    expect((await scopes.send(workspaceMember, 'DELETE', REVOKE, { key_id: adminRevocable.key_id })).status).toBe(200);
    expect(await probe.pg('SELECT enabled, deleted FROM accounts_orgapikey WHERE id=$1', [adminRevocable.key_id]))
      .toEqual([{ enabled: false, deleted: true }]);

    const retiredHeaders = { 'X-Api-Key': retired.api_key, 'X-Secret-Key': retired.secret_key };
    // tfc/urls.py AuthenticatedHealthView accepts Langfuse Basic. MCP routes
    // come from mcp_server/urls.py and the ASGI Streamable HTTP mount /mcp.
    for (const [path, headers] of [
      [USER_INFO, retiredHeaders],
      ['/api/public/health', { Authorization: `Basic ${Buffer.from(`${retired.api_key}:${retired.secret_key}`).toString('base64')}` }],
      ['/mcp/internal/tools/', retiredHeaders], ['/mcp', retiredHeaders],
    ] as [string, Record<string, string>][]) {
      const response = await anonymous.get(path, { headers, maxRedirects: 0 });
      expect(response.status()).toBe(401);
      expect(await response.json()).toMatchObject({ code: 'api_key_retired', create_url: KEYS_PAGE });
    }
    const wrong = await anonymous.get(USER_INFO, { headers: { ...retiredHeaders, 'X-Secret-Key': 'synthetic-incorrect-secret' } });
    expect(wrong.status()).toBe(401);
    const wrongBody = await wrong.json();
    expect(wrongBody.code).not.toBe('api_key_retired');
    expect(wrongBody.create_url).toBeUndefined();

    const memberHeaders = { 'X-Api-Key': personal.api_key, 'X-Secret-Key': personal.secret_key };
    const read = await anonymous.get(USER_INFO, { headers: memberHeaders });
    expect(read.status()).toBe(200);
    expect((await read.json()).id).toBe(member.userId);
    const deniedMint = await anonymous.post(CREATE, { headers: memberHeaders, data: { key_name: `${name}-forbidden` } });
    expect(deniedMint.status()).toBe(403);
    // agentcc/views/gateway.py mounts update-config on gateways, not org-configs.
    // GatewayAdminActionsMixin checks authority before body validation or work.
    const deniedAdmin = await anonymous.post('/agentcc/gateways/default/update-config/', { headers: memberHeaders, data: { config: {} } });
    expect(deniedAdmin.status()).toBe(403);
    const revoked = await scopes.send(owner, 'DELETE', REVOKE, { key_id: revocable.key_id });
    expect(revoked.status).toBe(200);
    expect(await probe.pg('SELECT enabled, deleted FROM accounts_orgapikey WHERE id=$1', [revocable.key_id]))
      .toEqual([{ enabled: false, deleted: true }]);

    const removed = await scopes.send(owner, 'DELETE', REMOVE, { user_id: member.userId });
    expect(removed.status).toBe(200);
    for (let attempt = 0; attempt < 2; attempt++) {
      expect((await anonymous.get(USER_INFO, { headers: memberHeaders })).status()).toBe(401);
    }
    expect(await probe.pg('SELECT id FROM accounts_organization_membership WHERE user_id=$1 AND organization_id=$2 AND is_active AND NOT deleted', [member.userId, member.organizationId])).toEqual([]);
    expect(await probe.pg('SELECT id FROM accounts_workspacemembership WHERE user_id=$1 AND is_active AND NOT deleted', [member.userId])).toEqual([]);
    expect(await probe.pg('SELECT enabled FROM accounts_orgapikey WHERE id=$1', [personal.key_id])).toEqual([{ enabled: false }]);
  } finally { await anonymous.dispose(); }
});
