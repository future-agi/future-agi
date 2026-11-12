import { test, expect } from '../../../lib/fixtures';
import { ApiError } from '../../../lib/api-client';
import { flowAnnotation } from '../../../lib/flow-meta';
import { walkGatewayProfile, PROFILE_SERVES_MCP } from '../../../lib/profile';
import { request, type APIRequestContext } from '@playwright/test';
import { E2E } from '../../../lib/env';
import { POLL } from '../../../lib/state-probe';
import { MCP_MOCK } from '../../../lib/public-network';
import { inspectMcpCatalog } from '../../../lib/mcp-catalog';

// The Integrations page (CONNECTORS-PLAN 2.10, lane CN21) against the real
// connector catalog API (CN9). Pinned off the code it drives:
// - CATALOG_PATH: futureagi/agentcc/urls_connector_catalog.py, mounted under
//   /agentcc/ by agentcc/urls.py since CN-ADD (MCP-APPS-PLAN 2.3, lane S2-T)
//   made the API-key providers addable; a 404 there fails this flow.
// - PAGE_PATH: paths.dashboard.gateway.integrations (lane S2-F), routed with it.
// - connectable(): what CN-ADD adds on this build, the rule the catalog API
//   lists by (connector_catalog.plan_for): no credential, or one shared bearer
//   token or API key sent in a header. The page's own filter
//   (frontend/src/sections/gateway/connectors/catalog-model.js) is looser; the
//   API decides, so every provider it lists must pass this rule.
// - MCP_STATUS: the console's MCP status (mcp_admin.org_status), checked against
//   the declared gateway profile (lib/profile.ts PROFILE_SERVES_MCP), never read
//   to pick a branch. On the stock and r7alt profiles the gateway runs with MCP
//   off: the status says gateway_mcp_disabled, no app can be added, and the page
//   shows the MCP page's off state (IntegrationsSection.jsx, MCPOffState.jsx),
//   which this flow checks instead of the browse steps. On the mcp profile
//   (harness extension H-MCP) the gateway serves MCP: a fresh org's status names
//   no reason (org_status: no server yet), and the flow browses the grid.
// - H-MCP's catalogue fixture reuses CN30a's key-provider shape with the URL
//   fixed to the isolated mock. inspectMcpCatalog attests both readers before
//   any add. The UI's connect-request.js sends provider and credential.value;
//   CN-ADD lists tools, creates the vault reference and the virtual server,
//   allowing read-only tools and running write tools after an approval.
// First written 2026-10-21 in lane CN21 as GW-E2E-004; 022 on the release line,
// where 004 is the request-attempts flow. The search step waits once for each
// listed app (23 in the catalogue this build bundles), so the test's ceiling
// grows with the list (set after the API step).
const CATALOG_PATH = '/agentcc/connectors/catalog/';
const PAGE_PATH = '/dashboard/gateway/integrations';
const MCP_STATUS = '/agentcc/gateways/default/mcp-status/';
const MCP_OFF = 'gateway_mcp_disabled';
const MCP_OFF_TEXT = 'MCP is turned off for this deployment';
const PAGE_SIZE = 200;
const UI_READY = 60_000;
const CATEGORIES = ['Productivity', 'Developer', 'Communication', 'Data', 'Research'];
// Nothing on the page may promise what does not work yet (AM-C23).
const FORBIDDEN_TEXT = /coming soon|after (per-user )?sign-in|sign in with|connect with oauth/i;
// Fields that would carry a secret; the catalog API never sends one.
const SECRET_FIELDS = new Set(['value', 'secret', 'client_secret', 'token', 'api_key', 'encrypted_secret']);

interface Credential { mode: string; kind?: string; in?: string; label?: string }
interface Provider { id: string; catalog_ref: string; tier: string; credential: Credential }
interface Server { server_id: string; catalog_ref: string | null; connected: boolean }
interface CatalogApp {
  id: string;
  name: string;
  description: string;
  vendor?: { name?: string };
  ee_category: string;
  categories: string[];
  providers: Provider[];
  servers: Server[];
}
interface CatalogPage { results: CatalogApp[]; next: string | null; total_pages: number }
interface Envelope<T> { result: T }

function connectable(provider: Provider): boolean {
  const { mode, kind, in: placement } = provider.credential;
  return mode === 'none' || (mode === 'shared' && (kind === 'bearer' || kind === 'api_key') && placement === 'header');
}

function secretFieldsIn(value: unknown, path = '$'): string[] {
  if (Array.isArray(value)) return value.flatMap((item, i) => secretFieldsIn(item, `${path}[${i}]`));
  if (value && typeof value === 'object') {
    return Object.entries(value).flatMap(([key, item]) => [
      ...(SECRET_FIELDS.has(key) ? [`${path}.${key}`] : []),
      ...secretFieldsIn(item, `${path}.${key}`),
    ]);
  }
  return [];
}

// Streamable HTTP as in lib_mcp.sh: the endpoint CN-ADD creates for this app.
async function rpc(req: APIRequestContext, key: string, session: string | undefined, message: object) {
  const res = await req.post(`${E2E.gatewayUrl}/mcp/v/${MCP_MOCK.appId}`, {
    headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json',
      Accept: 'application/json, text/event-stream', ...(session ? { 'Mcp-Session-Id': session } : {}) },
    data: message,
  });
  const text = await res.text();
  const payload = (res.headers()['content-type'] ?? '').includes('text/event-stream')
    ? text.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trim()).pop() ?? '' : text;
  return { status: res.status(), session: res.headers()['mcp-session-id'], body: payload ? JSON.parse(payload) : null };
}

test('GW-E2E-022: an admin browses and connects an API-key app, whose read-only tools work and writes wait for an approval', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'GW-E2E-022',
    area: 'gateway',
    userGoal: 'An organization admin browses and connects an API-key app, uses its read-only tools and cannot run its write tools without an approval',
    steps: [
      'open Gateway Integrations',
      'on a gateway profile whose gateway runs with MCP off (stock, r7alt), read that no app can be connected, and stop',
      'on the mcp gateway profile, search for each listed app by name, then for a word no app has, and clear the filters',
      'pick the first app\'s category and see exactly the apps in it',
      'switch to Connected and back to All',
      'open the app and read its access control',
      'connect the local fixture app by entering its API key in the drawer',
      'call its read-only echo tool with a minted marker, then try its destructive tool',
    ],
    backendChecks: [
      'the console MCP status agrees with the declared gateway profile: reason gateway_mcp_disabled where the gateway runs with MCP off, no reason where it serves MCP (the mcp profile)',
      'the connector catalog API lists at least one app for the organization, and every provider it lists can be added on this build: no key, or one shared key sent in a header',
      'no catalog API response carries a secret field',
      'the browser add lists exactly the three fixture tools: echo and get_weather allowed, delete_file allowed after an approval (approval_required)',
      'the active org config and encrypted vault row retain exactly the added app, its include of all three tools and its approval policy for delete_file (listed, once), with no deny rule',
      'an org key sees all three tools at the app endpoint, echo returns the minted marker, and delete_file is refused in band before the upstream: approval_required with a pending approval id where the gateway keeps approvals (E2E_GATEWAY_REPLICAS=2), else policy_unavailable',
    ],
    browserChecks: [
      'with MCP off, the page shows the MCP off state and no grid, search box or Custom MCP server action',
      'the page shows the five categories, the search box and the Custom MCP server action',
      'the grid holds exactly as many apps as the API lists, finds each of them, and the Connected count is the API\'s',
      'no text offers a sign-in or coming-soon app',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // The API step is fast; the ceiling for the UI steps is set once the list
  // is known (below).
  test.setTimeout(180_000);

  const profile = walkGatewayProfile();
  // The mcp-catalog profile serves the real catalogue and no MCP mock, so there
  // is no fixture app to connect: GW-E2E-026 browses that catalogue instead.
  test.skip(profile === 'mcp-catalog', 'GW-E2E-026 browses the real catalogue on this profile');
  const servesMcp = PROFILE_SERVES_MCP[profile];
  await test.step('API: the console\'s MCP status agrees with the declared gateway profile', async () => {
    const status = await actor.api.get<Envelope<{ enabled: boolean; reason?: string }>>(MCP_STATUS);
    await testInfo.attach('mcp-status', { body: JSON.stringify({ profile, ...status.result }), contentType: 'application/json' });
    if (servesMcp) {
      expect(status.result.reason, `profile ${profile}: the gateway serves MCP, so a fresh org's status names no reason`)
        .toBeUndefined();
    } else {
      expect(status.result.reason, `profile ${profile}: the gateway runs with MCP off`).toBe(MCP_OFF);
    }
  });
  if (!servesMcp) {
    await test.step('UI: with MCP off the page shows the off state and offers no app', async () => {
      await page.goto(PAGE_PATH, { waitUntil: 'domcontentloaded' });
      await expect(page.getByRole('heading', { level: 1, name: 'Integrations' })).toBeVisible({ timeout: UI_READY });
      await expect(page.getByText(MCP_OFF_TEXT)).toBeVisible({ timeout: UI_READY });
      await expect(page.getByTestId('integrations-grid')).toHaveCount(0);
      await expect(page.getByRole('textbox', { name: 'Search apps' })).toHaveCount(0);
      await expect(page.getByRole('link', { name: 'Custom MCP server' })).toHaveCount(0);
      await expect(page.getByTestId('integrations-page')).not.toContainText(FORBIDDEN_TEXT);
    });
    return;
  }

  await testInfo.attach('mcp-profile', { body: JSON.stringify(inspectMcpCatalog()), contentType: 'application/json' });
  const apps: CatalogApp[] = [];
  await test.step('API: the catalog lists only apps the org can add on this build', async () => {
    for (let pageNo = 1; pageNo <= 50; pageNo += 1) {
      let body: CatalogPage;
      try {
        body = await actor.api.get<CatalogPage>(CATALOG_PATH, { limit: PAGE_SIZE, page: pageNo });
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          throw new Error(
            `${CATALOG_PATH} answered 404: this build mounts the connector catalog routes (CN-ADD, `
            + 'agentcc/urls.py), so the backend under test is not this build',
          );
        }
        throw err;
      }
      expect(secretFieldsIn(body), 'catalog API response carries a secret field').toEqual([]);
      apps.push(...body.results);
      if (!body.next || pageNo >= body.total_pages) break;
    }
  });

  const listed = apps.filter(
    (app) => app.providers.some(connectable) || app.servers.length > 0,
  );
  expect(listed.length, 'the catalog routes are mounted only once a real provider is addable').toBeGreaterThan(0);
  // The first page load can use most of one UI_READY (60 s) while the app's
  // chunks load; every later wait is an in-page update, one per listed app in
  // the search loop. 120 s plus 5 s for each listed app (235 s for 23), and
  // never less than 180 s, raised for this test only.
  // Browse 180 + connect UI 60 + gateway sync 75 + agent/API/storage 60 + headroom.
  test.setTimeout(Math.max(480_000, 420_000 + 5_000 * listed.length));
  for (const app of apps) {
    for (const provider of app.providers) {
      expect(connectable(provider), `${app.id}/${provider.id} is listed but cannot be connected today`).toBe(true);
    }
  }
  const first = listed[0];
  const isConnected = (app: CatalogApp) =>
    app.servers.some((server) => Boolean(server.catalog_ref) && server.connected);
  const connectedCount = listed.filter(isConnected).length;
  const grid = page.getByTestId('integrations-grid');
  // AG Grid's aria-rowcount is the header row plus every row the grid holds,
  // rendered or not, so it counts the whole list where row virtualisation
  // would hide some rows from a DOM count.
  const gridRoot = grid.locator('[aria-rowcount]').first();
  const rowOf = (name: string) => grid.getByRole('row').filter({ hasText: name });
  const search = page.getByRole('textbox', { name: 'Search apps' });
  await testInfo.attach('listed-apps', {
    body: JSON.stringify(listed.map((app) => app.id)),
    contentType: 'application/json',
  });

  await test.step('UI: the page header, filter bar and grid', async () => {
    await page.goto(PAGE_PATH, { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { level: 1, name: 'Integrations' })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByRole('link', { name: 'Custom MCP server' })).toBeVisible({ timeout: UI_READY });
    await expect(search).toBeVisible({ timeout: UI_READY });
    const categories = page.getByRole('group', { name: 'Category' });
    for (const name of CATEGORIES) {
      await expect(categories.getByRole('button', { name, exact: true })).toBeVisible({ timeout: UI_READY });
    }
    await expect(rowOf(first.name).first()).toBeVisible({ timeout: UI_READY });
    await expect(gridRoot).toHaveAttribute('aria-rowcount', String(listed.length + 1), { timeout: UI_READY });
    await expect(page.getByRole('button', { name: `Connected (${connectedCount})` })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByTestId('integrations-page')).not.toContainText(FORBIDDEN_TEXT, { timeout: UI_READY });
  });

  await test.step('UI: search finds every listed app, a miss shows the one empty state', async () => {
    for (const app of listed) {
      await search.fill(app.name);
      await expect(rowOf(app.name).first()).toBeVisible({ timeout: UI_READY });
    }
    await search.fill('zz-no-app-has-this-word');
    await expect(page.getByText('No apps match')).toBeVisible({ timeout: UI_READY });
    await expect(grid).toHaveCount(0, { timeout: UI_READY });
    await page.getByRole('button', { name: 'Clear filters' }).click();
    await expect(search).toHaveValue('', { timeout: UI_READY });
    await expect(gridRoot).toHaveAttribute('aria-rowcount', String(listed.length + 1), { timeout: UI_READY });
  });

  await test.step('UI: a category keeps exactly its apps', async () => {
    const label = CATEGORIES.find((name) => name.toLowerCase() === first.ee_category);
    expect(label, `${first.id} has a category the page knows`).toBeTruthy();
    const inCategory = listed.filter(
      (app) => app.ee_category === first.ee_category || app.categories.includes(first.ee_category),
    );
    const chip = page.getByRole('group', { name: 'Category' }).getByRole('button', { name: label, exact: true });
    await chip.click();
    await expect(chip).toHaveAttribute('aria-pressed', 'true', { timeout: UI_READY });
    await expect(gridRoot).toHaveAttribute('aria-rowcount', String(inCategory.length + 1), { timeout: UI_READY });
    await expect(rowOf(first.name).first()).toBeVisible({ timeout: UI_READY });
    await chip.click();
    await expect(chip).toHaveAttribute('aria-pressed', 'false', { timeout: UI_READY });
  });

  await test.step('UI: Connected shows the org\'s connected apps, then All again', async () => {
    await page.getByRole('button', { name: `Connected (${connectedCount})` }).click();
    if (connectedCount === 0) {
      await expect(page.getByText('No connected apps yet')).toBeVisible({ timeout: UI_READY });
      await page.getByRole('button', { name: 'Show all apps' }).click();
    } else {
      await expect(gridRoot).toHaveAttribute('aria-rowcount', String(connectedCount + 1), { timeout: UI_READY });
      await expect(rowOf(listed.find(isConnected)!.name).first()).toBeVisible({ timeout: UI_READY });
      // AG Grid animates leaving rows out, so this waits for them to go. rowOf
      // matches a substring of the row's text, so the absence check takes an
      // unconnected app whose name is in no connected row's text.
      const connectedText = listed.filter(isConnected).map((app) => [
        app.name, app.description, app.vendor?.name,
        ...app.providers.map((provider) => provider.credential.label),
      ].join(' ').toLowerCase());
      const unconnected = listed.find((app) => !isConnected(app)
        && !connectedText.some((text) => text.includes(app.name.toLowerCase())));
      if (unconnected) await expect(rowOf(unconnected.name)).toHaveCount(0, { timeout: UI_READY });
      await page.getByRole('button', { name: 'All', exact: true }).click();
    }
    await expect(gridRoot).toHaveAttribute('aria-rowcount', String(listed.length + 1), { timeout: UI_READY });
  });

  await test.step('UI: the app\'s detail shows its access control', async () => {
    await rowOf(first.name).first().click();
    const drawer = page.getByRole('dialog', { name: first.name });
    await expect(drawer).toBeVisible({ timeout: UI_READY });
    await expect(drawer.getByRole('heading', { name: 'Access control' })).toBeVisible({ timeout: UI_READY });
    await expect(drawer.getByText('Who can connect')).toBeVisible({ timeout: UI_READY });
    const keyField = drawer.locator('input[type="password"]');
    if (await keyField.count()) {
      await expect(keyField).toHaveValue('', { timeout: UI_READY });
      await expect(keyField).toHaveAttribute('autocomplete', 'new-password', { timeout: UI_READY });
    }
    await expect(drawer).not.toContainText(FORBIDDEN_TEXT, { timeout: UI_READY });
    await drawer.getByRole('button', { name: 'Close' }).click();
    await expect(drawer).toBeHidden({ timeout: UI_READY });
  });

  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const appId = MCP_MOCK.appId;
  const marker = `e2e-gw22-${suffix}`;
  await testInfo.attach('seeded', { contentType: 'application/json',
    body: JSON.stringify({ appId, marker, organizationId: actor.organizationId }) });
  await test.step('UI and API: connect the mock app with a key and read its exact tool postures', async () => {
    await rowOf(MCP_MOCK.appName).first().click();
    const drawer = page.getByRole('dialog', { name: MCP_MOCK.appName });
    await drawer.getByLabel('API key', { exact: true }).fill(MCP_MOCK.bearer);
    const added = page.waitForResponse(r => r.request().method() === 'POST' &&
      new URL(r.url()).pathname === `/agentcc/connectors/apps/${appId}/add/`, { timeout: UI_READY });
    await drawer.getByRole('button', { name: 'Connect', exact: true }).click();
    const response = await added;
    expect(response.status()).toBe(201);
    const { result } = await response.json();
    expect(result.server_id).toBe(appId);
    // The provider declares no tools and its defaultPosture is ask, so the
    // listing's annotations decide: the destructive delete_file runs only after
    // an approval (connector_catalog.listed_tool_policy).
    expect(result.tools.map((t: any) => ({ name: t.name, posture: t.gateway_posture, reason: t.reason ?? null }))
      .sort((a: any, b: any) => a.name.localeCompare(b.name))).toEqual([
      { name: 'delete_file', posture: 'allow', reason: 'approval_required' },
      { name: 'echo', posture: 'allow', reason: null }, { name: 'get_weather', posture: 'allow', reason: null },
    ]);
    await expect(drawer.getByRole('button', { name: 'Connect', exact: true })).toHaveCount(0, { timeout: UI_READY });
    await drawer.getByRole('button', { name: 'Close' }).click();
    await page.getByRole('button', { name: `Connected (${connectedCount + 1})` }).click();
    await expect(rowOf(MCP_MOCK.appName)).toHaveCount(1, { timeout: UI_READY });
  });

  await test.step('API and storage: the app keeps its exact tools and approval policy with a write-only key', async () => {
    const { result } = await actor.api.get<Envelope<any>>('/agentcc/gateways/default/config/');
    const server = result.mcp.servers[appId];
    const virtual = result.mcp.virtual_servers[appId];
    expect(server.url).toBe(MCP_MOCK.catalogUrl);
    expect(virtual.members).toEqual([appId]);
    expect(virtual.tools.include).toEqual([`${appId}/delete_file`, `${appId}/echo`, `${appId}/get_weather`]);
    // No ttl_seconds: the gateway's default (900 s) applies. No tool is denied,
    // so the add writes no access block (connector_catalog._virtual_server).
    expect(virtual.approval).toEqual({ require: 'listed', tools: [`${appId}/delete_file`], validity: 'once' });
    expect(virtual.access).toBeUndefined();
    expect(JSON.stringify(result)).not.toContain(MCP_MOCK.bearer);
    const rows = await probe.pg<{ server_id: string; scope: string; clear: boolean }>(
      `SELECT server_id, scope, position($3 in row_to_json(c)::text) > 0 AS clear
         FROM agentcc_mcp_credential c WHERE organization_id = $1 AND server_id = $2`,
      [actor.organizationId, appId, MCP_MOCK.bearer]);
    expect(rows).toEqual([{ server_id: appId, scope: 'shared', clear: false }]);
    const catalog = await actor.api.get<CatalogPage>(CATALOG_PATH, { limit: PAGE_SIZE });
    expect(catalog.results.filter(a => a.id === appId).flatMap(a => a.servers.filter(s => s.connected).map(s => s.server_id)))
      .toEqual([appId]);
  });

  const key = await actor.api.post<Envelope<{ key: string }>>('/agentcc/api-keys/', { name: marker });
  const req = await request.newContext();
  try {
    await test.step('agent: all three tools are offered, echo works and delete_file waits for an approval', async () => {
      let session: string | undefined;
      await expect.poll(async () => {
        const init = await rpc(req, key.result.key, undefined, { jsonrpc: '2.0', id: 1, method: 'initialize',
          params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'e2e-gw22', version: '1' } } });
        expect(init.status).toBe(200); session = init.session;
        await rpc(req, key.result.key, session, { jsonrpc: '2.0', method: 'notifications/initialized' });
        const list = await rpc(req, key.result.key, session, { jsonrpc: '2.0', id: 2, method: 'tools/list' });
        return list.body.result.tools.map((t: any) => t.name).sort();
      }, POLL.GATEWAY_SYNC).toEqual([`${appId}__delete_file`, `${appId}__echo`, `${appId}__get_weather`]);
      const echo = await rpc(req, key.result.key, session, { jsonrpc: '2.0', id: 3, method: 'tools/call',
        params: { name: `${appId}__echo`, arguments: { text: marker } } });
      expect(echo.status).toBe(200);
      expect(echo.body.result.content).toEqual([{ type: 'text', text: `echo ok: ${JSON.stringify({ text: marker })}` }]);
      // The approval check is step 10 of the tools/call chain and the upstream
      // call step 17 (agentcc-gateway internal/mcp/chain.go), so a call it refuses
      // never reaches the mock. On this profile the mock is on e2e-public only
      // (lib/public-network.ts publicOnly), so this runner cannot read its
      // /__received journal; the in-band refusal below is the gateway's own.
      // The gateway keeps approvals only in Redis (redisstate.MCPApprovalStore, no
      // fallback). The app's gateway has Redis only when the stack declares the H8
      // replica (E2E_GATEWAY_REPLICAS=2: bin/e2e, lib/managed-mock.ts
      // replicaDeclared); without it the approval step refuses the call as
      // policy_unavailable (approval.go approvalUnavailable) and still runs nothing.
      const approvalsKept = process.env.E2E_GATEWAY_REPLICAS === '2';
      const gated = await rpc(req, key.result.key, session, { jsonrpc: '2.0', id: 4, method: 'tools/call',
        params: { name: `${appId}__delete_file`, arguments: { path: marker } } });
      expect(gated.status).toBe(200);
      expect(gated.body.error, JSON.stringify(gated.body)).toBeUndefined();
      expect(gated.body.result.isError, JSON.stringify(gated.body)).toBe(true);
      const refusal = gated.body.result._meta?.['io.futureagi/error'];
      if (approvalsKept) {
        expect({ code: refusal?.code, status: refusal?.metadata?.status }, JSON.stringify(gated.body))
          .toEqual({ code: 'approval_required', status: 'pending' });
        expect(refusal?.metadata?.approval_id, 'the refusal names the pending approval').toMatch(/^\S+$/);
      } else {
        expect(refusal?.code, JSON.stringify(gated.body)).toBe('policy_unavailable');
      }
      // TODO(GW-E2E-022): approve the pending call in MCP tools > Approvals, retry
      // the identical call with params._meta io.futureagi/approval_id, and see
      // delete_file run once. No flow helper drives the call approvals API yet.
    });
  } finally { await req.dispose(); }
});
