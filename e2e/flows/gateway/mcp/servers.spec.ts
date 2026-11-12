import { request } from '@playwright/test';
import type { APIRequestContext } from '@playwright/test';
import { test, expect } from '../../../lib/scope-actors';
import type { ScopeActor } from '../../../lib/scope-actors';
import { E2E } from '../../../lib/env';
import { POLL } from '../../../lib/state-probe';
import { flowAnnotation } from '../../../lib/flow-meta';
import { GatewayAdmin } from '../../../lib/gateway-admin';
import { GatewayGridReader, GRID_VIEWPORT } from '../../../lib/gateway-grid';
import { profileTag } from '../../../lib/profile';
import { MCP_MOCK } from '../../../lib/public-network';
import { inspectMcpCatalog } from '../../../lib/mcp-catalog';
import { observeDisabledMcpReads, assertDisabledMcpNavigation, assertStoredMcpGuardrailEdits } from './visible-tabs';

// An org saves its own MCP server in the console and lists its tools
// (MCP-APPS-PLAN stage 2, lane S2-E; M8-M17 ported in P1 81021429df).
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
// - MCP_STATUS: views/gateway.py mcp_status, mcp_admin.org_status (reason
//   gateway_mcp_disabled, frontend mcp/utils.js MCP_OFF_CODE).
// - MCP_TOOLS: views/gateway.py mcp_tools (success_response, result list).
// - UPDATE_SERVER / UPDATE_CONFIG: the wire bodies of AddMCPServerDialog.jsx
//   handleSave (transport http, auth {type bearer, token}) and
//   hooks/useMCPConfigPatch.js ({mcp: patch}); org B is seeded with them.
// - GATEWAY_CONFIG: the Servers tab's config read (Django's copy).
// - VAULT: views/mcp_credentials.py MCPCredentialListView, describe() fields.
// - The page: MCP_PATH ?tab=servers (mcp/tabs.js), the empty state's
//   "Add server", the dialog "Add MCP server" with Server ID, URL,
//   Authentication "Bearer token", Token, "Test connection" (probeMessage:
//   "Connected to <name>: <n> tools, ..."), "Add server"; the banner
//   "MCP is off for this organization" / "Turn on MCP" (MCPStatusBanner.jsx);
//   the card's aria-label "<id> Healthy" (ServerCatalog.jsx, utils.js); the
//   tools grid's "Tool" column (ToolsGrid.jsx, <server>__<tool>).
// - The gateway's copy masks a set secret as "****" (admin_orgs.go
//   mcpSecretMask) and names tools <server>__<tool> (vserver.Separator).
// Written on a6bee8f551 (lane S2-E); first run live on the mcp profile with H-MCP.
const MCP_PATH = '/dashboard/gateway/mcp';
const MCP_STATUS = '/agentcc/gateways/default/mcp-status/';
const MCP_TOOLS = '/agentcc/gateways/default/mcp-tools/';
const UPDATE_SERVER = '/agentcc/gateways/default/update-mcp-server/';
const UPDATE_CONFIG = '/agentcc/gateways/default/update-config/';
const GATEWAY_CONFIG = '/agentcc/gateways/default/config/';
const VAULT = '/agentcc/mcp/credentials/';
const UPSTREAM_URL = MCP_MOCK.url;
// The fixed test bearer the mock requires; it guards nothing outside the stack.
const UPSTREAM_TOKEN = MCP_MOCK.bearer;
// fixtures/mcp/common/tools.json, sorted.
const FIXTURE_TOOLS = ['delete_file', 'echo', 'get_weather'];
const SECRET_MASK = '****';
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// scopeActors provisioning 120 + seeding 30 + GATEWAY_SYNC 75 (tools listed)
// + GATEWAY_SYNC 75 (explicit read-only default) + four UI steps of
// STEP_BUDGET 75 (300) + API checks 30 + headroom 45.
// 798/850/917: ten tab/edit steps plus the Overview reset, 11 x 75s,
// plus the existing 675s = 1500s.
const FLOW_TIMEOUT = 1_500_000;

interface Envelope<T> { result: T }
interface Tool { name: string; server_id: string; upstream_name?: string; quarantined?: boolean }
interface Credential { id: string; server_id: string; scope: string; kind: string; status: string; has_value: boolean }
interface Page<T> { count: number; results: T[] }

// One JSON-RPC message to the gateway's MCP endpoint, as an agent sends it
// (Streamable HTTP: a JSON or an SSE answer, lib_mcp.sh scn_mcp_post).
async function rpc(req: APIRequestContext, key: string, session: string | undefined, message: object) {
  const res = await req.post(`${E2E.gatewayUrl}/mcp`, {
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

// initialize, notifications/initialized, tools/list: the names an agent sees.
async function agentToolNames(req: APIRequestContext, key: string): Promise<string[]> {
  const init = await rpc(req, key, undefined, {
    jsonrpc: '2.0', id: 1, method: 'initialize',
    params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'e2e-gw23', version: '1' } },
  });
  expect(init.status, JSON.stringify(init.body)).toBe(200);
  await rpc(req, key, init.session, { jsonrpc: '2.0', method: 'notifications/initialized' });
  const list = await rpc(req, key, init.session, { jsonrpc: '2.0', id: 2, method: 'tools/list' });
  expect(list.status, JSON.stringify(list.body)).toBe(200);
  return ((list.body?.result?.tools ?? []) as Array<{ name: string }>).map((tool) => tool.name).sort();
}

// The MCP tools API's rows for one server, by name.
const toolsOf = async (actor: ScopeActor, serverId: string) =>
  (await actor.api.get<Envelope<Tool[]>>(MCP_TOOLS)).result
    .filter((tool) => tool.server_id === serverId).sort((a, b) => a.name.localeCompare(b.name));

test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-023: an org admin connects and configures an MCP server, with write-only credentials and storage-only guardrails',
  {
    tag: ['@flow', profileTag('mcp')],
    annotation: flowAnnotation({
      id: 'GW-E2E-023',
      area: 'gateway',
      userGoal:
        'An organization admin connects and configures the organization\'s own MCP server: credentials remain write-only, tools stay org-scoped, Resources/Prompts remain visibly disabled without fetching on their pages, and stored MCP guardrails can be added and edited without claiming runtime enforcement',
      steps: [
        'open MCP tools, Servers, and click Add server',
        'type a server id, the server\'s URL, pick Bearer token and type the token',
        'click Test connection and read the result, then click Add server',
        'click Turn on MCP on the banner that says MCP is off for this organization',
        'read the server card and the Tools grid',
        'verify the agent has no default access, then grant a read-only default virtual server through the org-config API',
        'leave Servers for a fresh selected Overview, then observe disabled Resources/Prompts query/path links, reloads and Guardrails edits; no list fetch occurs within that window',
        'open Guardrails with missing and API-seeded empty stored settings, add disabled stored settings, then reopen by path and edit them without implying enforcement',
      ],
      backendChecks: [
        'before the save, the console MCP status names no reason: the gateway serves MCP on this profile',
        'Django\'s copy of the org config holds the server\'s URL and a shared vault credential reference, and no response the console reads carries the token',
        'the vault lists exactly one active shared bearer credential for the server, with a value and without it',
        'PG agentcc_mcp_credential holds the credential for the org and server, and the token appears in none of its columns',
        'the gateway\'s copy of the org config carries the server with its shared credential assembled at push and shown only as the mask',
        'the MCP tools API lists exactly the upstream\'s three tools; an org key initially gets none on /mcp, then exactly echo and get_weather after a read-only default virtual server is bound',
        'a second organization that saves the same server id and URL without the token lists none of those tools, through the API or on /mcp',
        'zero attempted mcp-resources or mcp-prompts requests after the post-Servers Overview selection through disabled deep links, reloads and stored-settings edits; server setup/drawer reads are outside this window',
        'each real update-mcp-guardrails body contains only config with enabled=false and the minted blocked-tool marker, and the config API reads back exactly those stored settings',
      ],
      browserChecks: [
        'Test connection reports Connected with the upstream\'s three tools',
        'the card reads the server as Healthy and the Tools grid shows exactly the three tool names',
        'the token appears nowhere on the page after the save',
        'Resources/Prompts remain visible but disabled with the exact accessible notice; Guardrails remains enabled for missing, empty and disabled stored settings',
        'Guardrails explains storage-only behavior, and its add/edit dialog rehydrates the saved disabled flag and marker',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    await testInfo.attach('mcp-profile', { body: JSON.stringify(inspectMcpCatalog()), contentType: 'application/json' });
    const owner = scopes.ownerA;
    const other = scopes.ownerB;

    const status = await owner.api.get<Envelope<{ enabled: boolean; reason?: string }>>(MCP_STATUS);
    expect(status.result.reason, 'the mcp profile\'s gateway serves MCP: a fresh org\'s status names no reason')
      .toBeUndefined();

    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const serverId = `e2e-gw23-${suffix}`;
    const expectedTools = FIXTURE_TOOLS.map((tool) => `${serverId}__${tool}`);
    const key = await owner.api.post<Envelope<{ key: string; gateway_key_id: string }>>(
      '/agentcc/api-keys/', { name: `e2e-gw23-${suffix}` });
    const otherKey = await other.api.post<Envelope<{ key: string; gateway_key_id: string }>>(
      '/agentcc/api-keys/', { name: `e2e-gw23-${suffix}-b` });
    // Org B: the same id and URL, no token (the mock refuses it), MCP on.
    await other.api.post(UPDATE_SERVER, { server_id: serverId, config: { transport: 'http', url: UPSTREAM_URL } });
    await other.api.post(UPDATE_CONFIG, { mcp: { enabled: true } });
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({
        suffix, serverId, organizationId: owner.organizationId, otherOrganizationId: other.organizationId,
        gatewayKeyId: key.result.gateway_key_id, otherGatewayKeyId: otherKey.result.gateway_key_id,
      }, null, 2),
    });

    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    await page.setViewportSize(GRID_VIEWPORT);
    const dialog = page.getByRole('dialog', { name: 'Add MCP server' });
    let disabledReads: ReturnType<typeof observeDisabledMcpReads> | undefined;
    try {
      await test.step('UI: add the server with its token and test the connection', async () => {
        await page.goto(`${MCP_PATH}?tab=servers`, { waitUntil: 'domcontentloaded' });
        await page.getByRole('button', { name: 'Add server' }).click({ timeout: UI_READY });
        await expect(dialog).toBeVisible({ timeout: UI_READY });
        await dialog.getByRole('textbox', { name: 'Server ID' }).fill(serverId);
        await dialog.getByRole('textbox', { name: 'URL' }).fill(UPSTREAM_URL);
        await dialog.getByRole('combobox', { name: 'Authentication' }).click();
        await page.getByRole('option', { name: 'Bearer token', exact: true }).click();
        await dialog.getByLabel('Token', { exact: true }).fill(UPSTREAM_TOKEN);
        await dialog.getByRole('button', { name: 'Test connection' }).click();
        await expect(dialog.getByText('Test passed', { exact: true })).toBeVisible({ timeout: UI_READY });
        await expect(dialog.getByText(/^Test passed( for .*)?: 3 tools\b/)).toBeVisible({ timeout: UI_READY });
        await dialog.getByRole('button', { name: 'Save server' }).click();
        await expect(dialog).toBeHidden({ timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      await test.step('UI: turn MCP on for the organization', async () => {
        await expect(page.getByText('MCP is off for this organization')).toBeVisible({ timeout: UI_READY });
        await page.getByRole('button', { name: 'Turn on MCP' }).click();
        await expect(page.getByText('MCP is off for this organization')).toBeHidden({ timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      await test.step('API: the server is saved with a vault reference, the token never read back', async () => {
        const config = await owner.api.get<Envelope<{ mcp?: { servers?: Record<string, any> } }>>(GATEWAY_CONFIG);
        const entry = config.result.mcp?.servers?.[serverId];
        expect(entry?.url).toBe(UPSTREAM_URL);
        expect(entry?.credential?.mode).toBe('shared');
        expect(entry?.credential?.state).toBe('active');
        expect(entry?.auth, 'no inline auth is stored').toBeUndefined();
        expect(JSON.stringify(config)).not.toContain(UPSTREAM_TOKEN);

        const vault = await owner.api.get<Page<Credential>>(VAULT, { server_id: serverId });
        expect(vault.results.map(({ server_id, scope, kind, status: state, has_value }) =>
          ({ server_id, scope, kind, status: state, has_value }))).toEqual([
          { server_id: serverId, scope: 'shared', kind: 'bearer', status: 'active', has_value: true },
        ]);
        expect(vault.results[0].id).toBe(entry?.credential?.credential_id);
        expect(JSON.stringify(vault)).not.toContain(UPSTREAM_TOKEN);
      });

      await test.step('storage: the credential row holds no clear token', async () => {
        const rows = await probe.pg<{ n: string; clear: string }>(
          `SELECT count(*) AS n,
                  count(*) FILTER (WHERE row_to_json(c)::text LIKE '%' || $3 || '%') AS clear
             FROM agentcc_mcp_credential c
            WHERE organization_id = $1 AND server_id = $2`,
          [owner.organizationId, serverId, UPSTREAM_TOKEN],
        );
        expect(rows.map((row) => ({ n: Number(row.n), clear: Number(row.clear) }))).toEqual([{ n: 1, clear: 0 }]);
      });

      const admin = await GatewayAdmin.open();
      const req = await request.newContext();
      try {
        await test.step('gateway: its copy carries the assembled credential, masked on read', async () => {
          await expect.poll(async () => {
            const server = (await admin.orgConfig(owner.organizationId))?.mcp?.servers?.[serverId];
            return server ? { url: server.url, mode: server.credential?.mode, secret: server.credential?.secret?.value } : null;
          }, POLL.GATEWAY_SYNC).toEqual({ url: UPSTREAM_URL, mode: 'shared', secret: SECRET_MASK });
          const otherServer = (await admin.orgConfig(other.organizationId))?.mcp?.servers?.[serverId];
          expect(otherServer?.credential?.secret, 'org B saved no token').toBeUndefined();
        });

        await test.step('API: the org lists exactly the upstream\'s tools, the other org none', async () => {
          await expect.poll(async () => (await toolsOf(owner, serverId)).map((tool) => tool.name),
            POLL.GATEWAY_SYNC).toEqual(expectedTools);
          // New orgs deny the compatibility view until explicitly bound (CN30a
          // section 2). Keep that protection and grant only the read-only tools.
          expect(await agentToolNames(req, key.result.key)).toEqual([]);
          await owner.api.post(UPDATE_CONFIG, { mcp: { virtual_servers: { [`read-${suffix}`]: {
            members: [serverId], naming: 'prefixed',
            tools: { include: [`${serverId}/echo`, `${serverId}/get_weather`] },
            access: { default_for_org: true },
          } } } });
          await expect.poll(() => agentToolNames(req, key.result.key), POLL.GATEWAY_SYNC)
            .toEqual([`${serverId}__echo`, `${serverId}__get_weather`]);
          expect(await toolsOf(other, serverId)).toEqual([]);
          expect((await agentToolNames(req, otherKey.result.key)).filter((name) => name.startsWith(`${serverId}__`)))
            .toEqual([]);
        });
      } finally {
        await req.dispose();
        await admin.dispose();
      }

      await test.step('UI: the card is Healthy and the Tools grid lists exactly the three tools', async () => {
        await page.reload({ waitUntil: 'domcontentloaded' });
        await expect(page.getByRole('button', { name: `${serverId} Healthy` })).toBeVisible({ timeout: UI_READY });
        const grid = GatewayGridReader.in(page);
        await expect.poll(async () => (await grid.columnHeaded('Tool')).sort(), { timeout: UI_READY })
          .toEqual(expectedTools);
        await expect(page.locator('body')).not.toContainText(UPSTREAM_TOKEN, { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });
      await test.step('UI: leave the Servers drawer before the disabled-tab observation window', async () => {
        // ServersTab always mounts ServerDrawer, which legitimately fetches both
        // lists (servers/ServerDrawer.jsx:133-134). A fresh Overview unmounts it.
        await page.goto(MCP_PATH, { waitUntil: 'domcontentloaded' });
        await expect(page.getByRole('tab', { name: 'Overview', exact: true }))
          .toHaveAttribute('aria-selected', 'true', { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });
      disabledReads = observeDisabledMcpReads(page);
      await assertDisabledMcpNavigation(page, owner, disabledReads.requests);
      await assertStoredMcpGuardrailEdits(page, owner, `e2e-gw23-stored-${suffix}`);
      expect(disabledReads.requests).toEqual([]);
    } finally {
      disabledReads?.stop();
      await context.close();
    }
  },
);
