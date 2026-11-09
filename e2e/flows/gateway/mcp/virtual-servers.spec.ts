import { request } from '@playwright/test';
import type { APIRequestContext } from '@playwright/test';
import { test, expect } from '../../../lib/scope-actors';
import { E2E } from '../../../lib/env';
import { POLL } from '../../../lib/state-probe';
import { flowAnnotation } from '../../../lib/flow-meta';
import { profileTag } from '../../../lib/profile';
import { MCP_MOCK } from '../../../lib/public-network';
import { inspectMcpCatalog } from '../../../lib/mcp-catalog';
import { GatewayAdmin } from '../../../lib/gateway-admin';
import { GatewayGridReader, GRID_VIEWPORT } from '../../../lib/gateway-grid';

// A virtual MCP server (M10, ported in P1 81021429df): one named endpoint,
// /mcp/v/<slug>, that offers an agent only the tools the admin picked.
//
// Runs on the mcp gateway profile only (harness extension H-MCP, tag
// @gw-profile-mcp; E2E_STACK=standalone E2E_GATEWAY_PROFILE=mcp): the stack's
// gateway serves MCP (e2e/stack/gateway.mcp.yaml) and its one MCP upstream is
// mcp-mock, agentcc-mcpmock on e2e-public only at mcp.e2e-public.test, with the
// scenarios' common fixtures (lib/public-network.ts MCP_MOCK), refusing any call
// without its fixed test bearer. On this profile a status that says MCP is off
// fails the flow; it is never a skip.
//
// Pinned off the code it drives:
// - The seed is GW-E2E-023's save, through the same wire bodies:
//   update-mcp-server as AddMCPServerDialog.jsx handleSave sends it, and
//   update-config {mcp: {enabled: true}} as useMCPConfigPatch.js sends "Turn
//   on MCP". GW-E2E-023 drives those two in the browser.
// - The page: MCP_PATH ?tab=virtual (mcp/tabs.js); the empty state's "New
//   virtual server"; the drawer, a dialog named by its title
//   (GatewayDetailDrawer.jsx), with Title, Slug, the Servers checkboxes named
//   by server id, "Only these tools" (patterns <server>/<tool>, the drawer's
//   toolPatterns), the "Exposes <n> tool(s)" preview and "Create"
//   (VirtualServerDrawer.jsx); the grid's Slug and Tools columns
//   (VirtualServersTab.jsx).
// - What is written: virtualServerPatch (virtualServers.js): members, naming
//   "prefixed" (the form's default), tools {include, exclude: null}.
// - Exposed names: <member>__<tool> under prefixed naming (vserver/names.go,
//   Separator "__").
// Written on a6bee8f551 (lane S2-E); first run live on the mcp profile with H-MCP.
const MCP_PATH = '/dashboard/gateway/mcp';
const MCP_STATUS = '/agentcc/gateways/default/mcp-status/';
const UPDATE_SERVER = '/agentcc/gateways/default/update-mcp-server/';
const UPDATE_CONFIG = '/agentcc/gateways/default/update-config/';
const GATEWAY_CONFIG = '/agentcc/gateways/default/config/';
const UPSTREAM_URL = MCP_MOCK.url;
// The fixed test bearer the mock requires; it guards nothing outside the stack.
const UPSTREAM_TOKEN = MCP_MOCK.bearer;
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// scopeActors provisioning 120 + seeding 30 + GATEWAY_SYNC 75 (the org's
// tools listed) + three UI steps of STEP_BUDGET 75 (225) + GATEWAY_SYNC 75
// (the virtual server reaches the gateway) + headroom 75.
const FLOW_TIMEOUT = 600_000;

interface Envelope<T> { result: T }

// One JSON-RPC message to a gateway MCP endpoint, as an agent sends it
// (Streamable HTTP: a JSON or an SSE answer, lib_mcp.sh scn_mcp_post).
async function rpc(req: APIRequestContext, path: string, key: string, session: string | undefined, message: object) {
  const res = await req.post(`${E2E.gatewayUrl}${path}`, {
    headers: {
      Authorization: `Bearer ${key}`,
      'Content-Type': 'application/json',
      Accept: 'application/json, text/event-stream',
      ...(session ? { 'Mcp-Session-Id': session } : {}),
    },
    data: message,
  });
  const text = await res.text();
  const sse = (res.headers()['content-type'] ?? '').includes('text/event-stream');
  const payload = sse
    ? text.split('\n').filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim()).pop() ?? ''
    : text;
  return { status: res.status(), session: res.headers()['mcp-session-id'], body: payload ? JSON.parse(payload) : null };
}

// initialize, notifications/initialized, tools/list on `path`: the HTTP
// status of the session's start, and the names an agent is offered.
async function agentTools(req: APIRequestContext, path: string, key: string, expectedListStatus = 200) {
  const init = await rpc(req, path, key, undefined, {
    jsonrpc: '2.0', id: 1, method: 'initialize',
    params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'e2e-gw24', version: '1' } },
  });
  expect(init.status, JSON.stringify(init.body)).toBe(200);
  await rpc(req, path, key, init.session, { jsonrpc: '2.0', method: 'notifications/initialized' });
  const list = await rpc(req, path, key, init.session, { jsonrpc: '2.0', id: 2, method: 'tools/list' });
  expect(list.status, JSON.stringify(list.body)).toBe(expectedListStatus);
  return {
    status: list.status,
    error: list.body?.error,
    names: ((list.body?.result?.tools ?? []) as Array<{ name: string }>).map((tool) => tool.name).sort(),
  };
}

test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-024: an org admin publishes a virtual MCP server, and an agent there gets exactly the tools it picked',
  {
    tag: ['@flow', profileTag('mcp')],
    annotation: flowAnnotation({
      id: 'GW-E2E-024',
      area: 'gateway',
      userGoal:
        'An organization admin gives an agent one MCP endpoint that offers only the tools picked for it, out of everything the organization\'s servers serve',
      steps: [
        'seed the organization\'s MCP server with its bearer token and MCP on, and explicitly allow its compatibility view as the wider baseline',
        'open MCP tools, Virtual servers, and click New virtual server',
        'type a title and a slug, tick the server, and keep only its echo tool',
        'read the preview, click Create, and read the virtual servers grid',
      ],
      backendChecks: [
        'before the seed, the console MCP status names no reason: the gateway serves MCP on this profile',
        'Django\'s copy of the org config holds the virtual server under its slug with the server as its one member, prefixed naming and the echo pattern',
        'the gateway\'s copy of the org config carries the same virtual server',
        'an agent key of the org gets exactly `<server>__echo` on /mcp/v/<slug>, where the org\'s /mcp offers more of the server\'s tools',
        'an agent key of another organization gets none of the server\'s tools at that endpoint',
      ],
      browserChecks: [
        'the preview says the virtual server exposes 1 tool, `<server>__echo`',
        'the grid lists exactly the new slug with 1 tool',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    await testInfo.attach('mcp-profile', { body: JSON.stringify(inspectMcpCatalog()), contentType: 'application/json' });
    const owner = scopes.ownerA;
    const other = scopes.ownerB;

    const status = await owner.api.get<Envelope<{ enabled: boolean; reason?: string }>>(MCP_STATUS);
    expect(status.result.reason, 'the mcp profile\'s gateway serves MCP: a fresh org\'s status names no reason')
      .toBeUndefined();

    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const serverId = `e2e-gw24-${suffix}`;
    const slug = `e2e-gw24-${suffix}`;
    const title = `Support agent ${suffix}`;
    const pattern = `${serverId}/echo`;
    const exposed = `${serverId}__echo`;
    await owner.api.post(UPDATE_SERVER, {
      server_id: serverId,
      config: { transport: 'http', url: UPSTREAM_URL, auth: { type: 'bearer', token: UPSTREAM_TOKEN } },
    });
    // Explicit compatibility-view permission (hook_acl_test.go's access.default
    // allow case): this is the wider baseline the UI-created endpoint narrows.
    await owner.api.post(UPDATE_CONFIG, { mcp: { enabled: true, access: { default: 'allow' } } });
    const key = await owner.api.post<Envelope<{ key: string; gateway_key_id: string }>>(
      '/agentcc/api-keys/', { name: `e2e-gw24-${suffix}` });
    const otherKey = await other.api.post<Envelope<{ key: string; gateway_key_id: string }>>(
      '/agentcc/api-keys/', { name: `e2e-gw24-${suffix}-b` });
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({
        suffix, serverId, slug, organizationId: owner.organizationId, otherOrganizationId: other.organizationId,
        gatewayKeyId: key.result.gateway_key_id, otherGatewayKeyId: otherKey.result.gateway_key_id,
      }, null, 2),
    });

    const req = await request.newContext();
    const admin = await GatewayAdmin.open();
    try {
      // The org's own endpoint first: the server's tools are served there, so
      // the narrowing below is the virtual server's doing.
      await test.step('API: the org\'s /mcp offers the server\'s tools before any virtual server', async () => {
        await expect.poll(async () => (await agentTools(req, '/mcp', key.result.key)).names,
          POLL.GATEWAY_SYNC).toEqual(['delete_file', 'echo', 'get_weather'].map(name => `${serverId}__${name}`));
      });

      const context = await scopes.openContext(browser, owner);
      const page = await context.newPage();
      await page.setViewportSize(GRID_VIEWPORT);
      const drawer = page.getByRole('dialog', { name: 'New virtual server' });

      await test.step('UI: create the virtual server with only the echo tool', async () => {
        await page.goto(`${MCP_PATH}?tab=virtual`, { waitUntil: 'domcontentloaded' });
        await page.getByRole('button', { name: 'New virtual server' }).click({ timeout: UI_READY });
        await expect(drawer).toBeVisible({ timeout: UI_READY });
        await drawer.getByRole('textbox', { name: 'Title' }).fill(title);
        await drawer.getByRole('textbox', { name: 'Slug' }).fill(slug);
        await drawer.getByRole('checkbox', { name: serverId, exact: true }).check();
        const only = drawer.getByRole('combobox', { name: 'Only these tools' });
        await only.fill(pattern);
        await only.press('Enter');
      }, { timeout: STEP_BUDGET });

      await test.step('UI: the preview exposes exactly the echo tool; create it', async () => {
        await expect(drawer.getByRole('heading', { name: 'Exposes 1 tool' })).toBeVisible({ timeout: UI_READY });
        await expect(drawer.getByText(exposed, { exact: true })).toBeVisible({ timeout: UI_READY });
        await drawer.getByRole('button', { name: 'Create' }).click();
        await expect(drawer).toBeHidden({ timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      await test.step('UI: the grid lists exactly the new virtual server with one tool', async () => {
        const grid = GatewayGridReader.in(page);
        await expect.poll(() => grid.columnHeaded('Slug'), { timeout: UI_READY }).toEqual([slug]);
        await expect.poll(() => grid.columnHeaded('Tools'), { timeout: UI_READY }).toEqual(['1']);
      }, { timeout: STEP_BUDGET });

      await test.step('API: Django and the gateway hold the virtual server as created', async () => {
        const config = await owner.api.get<Envelope<{ mcp?: { virtual_servers?: Record<string, any> } }>>(GATEWAY_CONFIG);
        const saved = config.result.mcp?.virtual_servers?.[slug];
        expect({ members: saved?.members, naming: saved?.naming, include: saved?.tools?.include })
          .toEqual({ members: [serverId], naming: 'prefixed', include: [pattern] });
        await expect.poll(async () => {
          const held = (await admin.orgConfig(owner.organizationId))?.mcp?.virtual_servers?.[slug];
          return held ? { members: held.members, include: held.tools?.include } : null;
        }, POLL.GATEWAY_SYNC).toEqual({ members: [serverId], include: [pattern] });
      });

      await test.step('API: an agent at /mcp/v/<slug> gets exactly the echo tool, another org none', async () => {
        await expect.poll(async () => (await agentTools(req, `/mcp/v/${slug}`, key.result.key)).names,
          POLL.GATEWAY_SYNC).toEqual([exposed]);
        const foreign = await agentTools(req, `/mcp/v/${slug}`, otherKey.result.key, 404);
        expect(foreign.error).toEqual({ code: -32600, message: 'virtual server not found' });
        expect(foreign.names).toEqual([]);
      });
    } finally {
      await req.dispose();
      await admin.dispose();
    }
  },
);
