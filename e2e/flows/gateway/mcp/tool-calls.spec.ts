import { request } from '@playwright/test';
import type { APIRequestContext } from '@playwright/test';
import { test, expect } from '../../../lib/scope-actors';
import { E2E } from '../../../lib/env';
import { POLL } from '../../../lib/state-probe';
import { flowAnnotation } from '../../../lib/flow-meta';
import { profileTag } from '../../../lib/profile';
import { MCP_MOCK } from '../../../lib/public-network';
import { inspectMcpCatalog } from '../../../lib/mcp-catalog';
import { GatewayGridReader, GRID_VIEWPORT } from '../../../lib/gateway-grid';

// Each tool call an agent makes through the organization's MCP endpoints leaves
// exactly one record (M15 meter, M16 ingestion, ported in P1 81021429df): the
// gateway emits an mcp-tool-call-v1 record on the log webhook's event sink
// (cmd/agentcc/wiring_mcp_meter.go), Django stores it in agentcc_tool_call
// (services/mcp_tool_call_ingestion.py), and the console's Tool calls tab
// lists it. This flow drives two real calls, a read-only one that the gateway
// serves and a destructive one that it denies, and delivers no record itself,
// so it proves the gateway produces both records as well.
//
// Run stack commands AND this flow through with-mcp-subfeatures.sh W14.
// W14 enables the explorer/download switches; other MCP switches stay OFF.
// Runs on the mcp gateway profile only (harness extension H-MCP, tag
// @gw-profile-mcp; E2E_STACK=standalone E2E_GATEWAY_PROFILE=mcp): the stack's
// gateway serves MCP (e2e/stack/gateway.mcp.yaml) and its one MCP upstream is
// mcp-mock, agentcc-mcpmock on e2e-public only at mcp.e2e-public.test, with the
// scenarios' common fixtures (lib/public-network.ts MCP_MOCK), refusing any call
// without its fixed test bearer. On this profile a status that says MCP is off
// fails the flow; it is never a skip.
//
// Pinned off the code it drives:
// - The seed is GW-E2E-023's save, through the same wire bodies
//   (AddMCPServerDialog.jsx handleSave; useMCPConfigPatch.js "Turn on MCP").
// - The read-only call: initialize, notifications/initialized, tools/call on
//   /mcp with the org key (lib_mcp.sh scn_mcp_post, W3b_isolation_live.sh part
//   2). The mock's echo tool (readOnlyHint) returns its arguments as text
//   (fixtures/mcp/common).
// - The denied write: a virtual server over the server whose access rule denies
//   the destructive delete_file (the rule shape a catalogue add writes for a
//   denied tool; write tools run after an approval, connector_catalog._virtual_server:
//   access.rules [{match: {}, deny: [<server>/<tool>]}]), seeded through the
//   console's update-config patch (virtualServers.js virtualServerPatch: members,
//   naming, tools.include). The console's drawer edits no access rule, so the
//   seed writes it. At /mcp/v/<slug> the agent is offered the echo tool only
//   (hook_list_filter.go), and its call to delete_file by name is refused in band
//   (hook_acl.go authorizeTool: tool_denied, metadata.rule access.rules[0];
//   denial.go: an isError result whose _meta io.futureagi/error carries it),
//   after the tool is bound to its server, so the record names the server.
// - TOOL_CALLS: views/mcp_tool_calls.py (cursor pages of results, filters
//   server_id and tool; the detail at <id>/), serializers/mcp_tool_calls.py.
// - The record: outcome denied with error_code tool_denied for a refusal
//   (meter/outcome.go Classify), virtual_server_id the virtual server's id.
// - The page: MCP_PATH ?tab=calls (mcp/tabs.js); the grid's Tool (toolLabel:
//   the upstream tool's name), Server and Outcome ("Success", "Denied", OUTCOMES
//   in calls/toolCalls.js) columns (ToolCallsTab.jsx); a row opens
//   ToolCallDrawer.jsx: its status chip, a Call section that lists Tool and
//   Server, and for a refusal an Error section whose Code is the error code.
// Written on a6bee8f551 (lane S2-E); first run live on the mcp profile with H-MCP.
const MCP_PATH = '/dashboard/gateway/mcp';
const MCP_STATUS = '/agentcc/gateways/default/mcp-status/';
const UPDATE_SERVER = '/agentcc/gateways/default/update-mcp-server/';
const UPDATE_CONFIG = '/agentcc/gateways/default/update-config/';
const GATEWAY_CONFIG = '/agentcc/gateways/default/config/';
const TOOL_CALLS = '/agentcc/mcp/tool-calls/';
// The in-band refusal's _meta key (agentcc-gateway internal/mcp/denial.go MetaKeyError).
const ERROR_META = 'io.futureagi/error';
const UPSTREAM_URL = MCP_MOCK.url;
// The fixed test bearer the mock requires; it guards nothing outside the stack.
const UPSTREAM_TOKEN = MCP_MOCK.bearer;
// The gateway flushes its log webhook batch, then Django stores the record.
const RECORD_VISIBLE = POLL.ASYNC_JOB;
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// scopeActors provisioning 120 + seeding 30 + GATEWAY_SYNC 75 (the tool is
// served on /mcp) + GATEWAY_SYNC 75 (the virtual server is served) +
// RECORD_VISIBLE 60 + three UI steps of STEP_BUDGET 75 (225) + API checks 30 +
// headroom 85.
const FLOW_TIMEOUT = 700_000;

interface Envelope<T> { result: T }
interface ToolCall {
  id: number; server_id: string; tool: string; upstream_tool: string; virtual_server_id: string;
  outcome: string; is_error: boolean; api_key_id: string; principal_kind: string; error_code: string;
}
interface CursorPage<T> { next: string | null; results: T[] }

// One JSON-RPC message to a gateway MCP endpoint (/mcp or /mcp/v/<slug>), as an
// agent sends it (Streamable HTTP: a JSON or an SSE answer, lib_mcp.sh scn_mcp_post).
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

async function agentSession(req: APIRequestContext, path: string, key: string): Promise<string | undefined> {
  const init = await rpc(req, path, key, undefined, {
    jsonrpc: '2.0', id: 1, method: 'initialize',
    params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'e2e-gw25', version: '1' } },
  });
  expect(init.status, JSON.stringify(init.body)).toBe(200);
  await rpc(req, path, key, init.session, { jsonrpc: '2.0', method: 'notifications/initialized' });
  return init.session;
}

// A new session on `path` and the tool names tools/list offers there, sorted.
async function offered(req: APIRequestContext, path: string, key: string) {
  const session = await agentSession(req, path, key);
  const list = await rpc(req, path, key, session, { jsonrpc: '2.0', id: 2, method: 'tools/list' });
  return { session, names: ((list.body?.result?.tools ?? []) as Array<{ name: string }>).map((t) => t.name).sort() };
}

test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-025: each tool call an agent makes through the organization\'s MCP endpoints leaves exactly one record, the served read-only call and the denied write alike, listed in Tool calls',
  {
    tag: ['@flow', profileTag('mcp'), '@mcp-subfeature-W14'],
    annotation: flowAnnotation({
      id: 'GW-E2E-025',
      area: 'gateway',
      userGoal:
        'A platform engineer finds the record of each tool call an agent made through the organization\'s MCP endpoints: one record per call, with the tool, the server, the outcome and the key that made it, a write the gateway refused shown as denied',
      steps: [
        'seed the organization\'s MCP server with its bearer token and MCP on, as GW-E2E-023 saves them, a virtual server over it whose access rule denies the destructive delete_file tool, as a catalogue add denies write tools, and mint a gateway key',
        'as the agent, open an MCP session on /mcp with the key and call the server\'s read-only echo tool with a minted text',
        'as the agent, open an MCP session on the virtual server\'s /mcp/v/<slug> and call delete_file by name',
        'open MCP tools, Tool calls, and read the grid',
        'open the denied call and read its Call and Error sections, then the echo call and its Call section',
      ],
      backendChecks: [
        'before the seed, the console MCP status names no reason: the gateway serves MCP on this profile',
        'status explicitly reports the explorer and download capabilities ON',
        'the echo call answers the upstream\'s echo of the minted text',
        'the virtual server offers the agent exactly the echo tool, and the delete_file call is refused in band with tool_denied by access.rules[0]',
        'PG agentcc_tool_call holds exactly two rows for the organization and server, both made by the minted key: echo with outcome success, and delete_file with outcome denied, error code tool_denied, under the virtual server\'s id',
        'the tool-call list API returns exactly those two records for the server, and the denied record\'s detail names the same tool, server, outcome and error code',
        'both CSV and NDJSON exports return 200 with their expected content types and include the two calls',
        'another organization\'s tool-call list has no record of the server',
      ],
      browserChecks: [
        'the Tool calls grid shows exactly two rows for the server: delete_file Denied and echo Success',
        'the denied call\'s drawer shows Denied, names delete_file and the server, and its Error section the code tool_denied',
        'the echo call\'s drawer names the echo tool and the server',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    expect(process.env.E2E_MCP_SUBFEATURE_SELECTION, 'start and run this flow with the W14 wrapper').toBe('W14');
    await testInfo.attach('mcp-profile', { body: JSON.stringify(inspectMcpCatalog()), contentType: 'application/json' });
    const owner = scopes.ownerA;
    const other = scopes.ownerB;

    const status = await owner.api.get<Envelope<{ enabled: boolean; reason?: string; features: Record<string, boolean> }>>(MCP_STATUS);
    expect(status.result.reason, 'the mcp profile\'s gateway serves MCP: a fresh org\'s status names no reason')
      .toBeUndefined();
    expect(status.result.features).toMatchObject({ tool_call_explorer: true, tool_call_download: true });

    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const serverId = `e2e-gw25-${suffix}`;
    const slug = `e2e-gw25-vs-${suffix}`;
    const tool = `${serverId}__echo`;
    const writeTool = `${serverId}__delete_file`;
    const marker = `e2e-gw25-echo-${suffix}`;
    await owner.api.post(UPDATE_SERVER, {
      server_id: serverId,
      config: { transport: 'http', url: UPSTREAM_URL, auth: { type: 'bearer', token: UPSTREAM_TOKEN } },
    });
    await owner.api.post(UPDATE_CONFIG, { mcp: { enabled: true } });
    // virtualServerPatch's members, naming and tools. The seed denies delete_file with the
    // access rule a catalogue add writes for a denied tool (connector_catalog._virtual_server).
    await owner.api.post(UPDATE_CONFIG, { mcp: { virtual_servers: { [slug]: {
      title: `Writes denied ${suffix}`,
      members: [serverId],
      naming: 'prefixed',
      tools: { include: [`${serverId}/echo`, `${serverId}/delete_file`], exclude: null },
      // The same read-only default is used by /mcp and the named endpoint.
      access: { default_for_org: true, rules: [{ match: {}, deny: [`${serverId}/delete_file`] }] },
    } } } });
    const saved = await owner.api.get<Envelope<{ mcp?: { virtual_servers?: Record<string, { id?: string }> } }>>(GATEWAY_CONFIG);
    const virtualServerId = saved.result.mcp?.virtual_servers?.[slug]?.id ?? '';
    expect(virtualServerId, 'the API gives the new virtual server an id').not.toBe('');
    const key = await owner.api.post<Envelope<{ key: string; gateway_key_id: string }>>(
      '/agentcc/api-keys/', { name: `e2e-gw25-${suffix}` });
    const gatewayKeyId = key.result.gateway_key_id;
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({
        suffix, serverId, slug, virtualServerId, tool, writeTool, marker, organizationId: owner.organizationId,
        otherOrganizationId: other.organizationId, gatewayKeyId,
      }, null, 2),
    });

    const req = await request.newContext();
    try {
      await test.step('agent: call the read-only echo tool once through /mcp', async () => {
        let session: string | undefined;
        await expect.poll(async () => {
          const seen = await offered(req, '/mcp', key.result.key);
          session = seen.session;
          return seen.names.includes(tool);
        }, POLL.GATEWAY_SYNC).toBe(true);
        const call = await rpc(req, '/mcp', key.result.key, session, {
          jsonrpc: '2.0', id: 3, method: 'tools/call', params: { name: tool, arguments: { text: marker } },
        });
        expect(call.status, JSON.stringify(call.body)).toBe(200);
        expect(call.body?.result?.isError ?? false).toBe(false);
        const texts = ((call.body?.result?.content ?? []) as Array<{ type: string; text?: string }>)
          .filter((part) => part.type === 'text').map((part) => part.text ?? '');
        expect(texts).toEqual([`echo ok: ${JSON.stringify({ text: marker })}`]);
      });

      await test.step('agent: the virtual server offers only echo, and refuses delete_file', async () => {
        const path = `/mcp/v/${slug}`;
        let session: string | undefined;
        await expect.poll(async () => {
          const seen = await offered(req, path, key.result.key);
          session = seen.session;
          return seen.names;
        }, POLL.GATEWAY_SYNC).toEqual([tool]);
        const call = await rpc(req, path, key.result.key, session, {
          jsonrpc: '2.0', id: 3, method: 'tools/call', params: { name: writeTool, arguments: { path: `/tmp/${marker}` } },
        });
        expect(call.status, JSON.stringify(call.body)).toBe(200);
        expect(call.body?.error, JSON.stringify(call.body)).toBeUndefined();
        expect(call.body?.result?.isError, JSON.stringify(call.body)).toBe(true);
        const refusal = call.body?.result?._meta?.[ERROR_META];
        expect({ code: refusal?.code, rule: refusal?.metadata?.rule }, JSON.stringify(call.body))
          .toEqual({ code: 'tool_denied', rule: 'access.rules[0]' });
      });
    } finally {
      await req.dispose();
    }

    let echoId = 0;
    let deniedId = 0;
    await test.step('storage: exactly one record per call, under the org, the server and the key', async () => {
      const rows = () => probe.pg<{ tool: string; upstream_tool: string; outcome: string; error_code: string;
        api_key_id: string; virtual_server_id: string }>(
        `SELECT tool, upstream_tool, outcome, error_code, api_key_id, virtual_server_id FROM agentcc_tool_call
          WHERE organization_id = $1 AND server_id = $2 ORDER BY tool`,
        [owner.organizationId, serverId],
      );
      await expect.poll(async () => (await rows()).length, RECORD_VISIBLE).toBeGreaterThanOrEqual(2);
      const found = await rows();
      expect(found.map(({ tool: name, upstream_tool, outcome, error_code, api_key_id }) =>
        ({ tool: name, upstream_tool, outcome, error_code, api_key_id }))).toEqual([
        { tool: writeTool, upstream_tool: 'delete_file', outcome: 'denied', error_code: 'tool_denied', api_key_id: gatewayKeyId },
        { tool, upstream_tool: 'echo', outcome: 'success', error_code: '', api_key_id: gatewayKeyId },
      ]);
      expect(found[0].virtual_server_id, 'the refusal is recorded under the virtual server').toBe(virtualServerId);
    });

    await test.step('API: the list returns those two records, the denied one\'s detail, another org none', async () => {
      const listed = await owner.api.get<CursorPage<ToolCall>>(TOOL_CALLS, { server_id: serverId });
      expect(listed.results.map(({ server_id, tool: name, upstream_tool, outcome, error_code }) =>
        ({ server_id, tool: name, upstream_tool, outcome, error_code }))
        .sort((a, b) => a.tool.localeCompare(b.tool))).toEqual([
        { server_id: serverId, tool: writeTool, upstream_tool: 'delete_file', outcome: 'denied', error_code: 'tool_denied' },
        { server_id: serverId, tool, upstream_tool: 'echo', outcome: 'success', error_code: '' },
      ]);
      echoId = listed.results.find((call) => call.tool === tool)!.id;
      deniedId = listed.results.find((call) => call.tool === writeTool)!.id;
      const detail = await owner.api.get<Envelope<ToolCall>>(`${TOOL_CALLS}${deniedId}/`);
      expect({ server_id: detail.result.server_id, tool: detail.result.tool, outcome: detail.result.outcome,
        error_code: detail.result.error_code })
        .toEqual({ server_id: serverId, tool: writeTool, outcome: 'denied', error_code: 'tool_denied' });
      const foreign = await other.api.get<CursorPage<ToolCall>>(TOOL_CALLS, { server_id: serverId });
      expect(foreign.results).toEqual([]);
    });
    await test.step('API: W14 enables CSV and NDJSON downloads for these calls', async () => {
      const downloads = await request.newContext();
      try {
        for (const [format, contentType] of [['csv', 'text/csv'], ['json', 'application/x-ndjson']]) {
          const exported = await downloads.get(`${E2E.apiUrl}${TOOL_CALLS}export/`, {
            headers: scopes.headers(owner), params: { server_id: serverId, export_format: format },
          });
          expect(exported.status()).toBe(200);
          expect(exported.headers()['content-type']).toContain(contentType);
          const body = await exported.text();
          expect(body).toContain(serverId);
          expect(body).toContain('delete_file');
          expect(body).toContain('echo');
        }
      } finally {
        await downloads.dispose();
      }
    });
    await testInfo.attach('tool-calls', { contentType: 'application/json', body: JSON.stringify({ echoId, deniedId }) });

    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    await page.setViewportSize(GRID_VIEWPORT);
    const grid = GatewayGridReader.in(page);
    const drawer = page.getByRole('dialog');
    // The grid's rows as the user reads them, paired by display row, sorted by tool.
    const gridRows = async () => {
      const [tools, servers, outcomes] = [await grid.columnHeaded('Tool'), await grid.columnHeaded('Server'),
        await grid.columnHeaded('Outcome')];
      return tools.map((name, i) => ({ tool: name, server: servers[i], outcome: outcomes[i] }))
        .sort((a, b) => a.tool.localeCompare(b.tool));
    };

    await test.step('UI: Tool calls lists exactly the two calls', async () => {
      await page.goto(`${MCP_PATH}?tab=calls`, { waitUntil: 'domcontentloaded' });
      await expect.poll(gridRows, { timeout: UI_READY }).toEqual([
        { tool: 'delete_file', server: serverId, outcome: 'Denied' },
        { tool: 'echo', server: serverId, outcome: 'Success' },
      ]);
    }, { timeout: STEP_BUDGET });

    await test.step('UI: the denied call\'s drawer shows the refusal', async () => {
      await grid.row((await grid.columnHeaded('Tool')).indexOf('delete_file')).click();
      await expect(drawer.getByRole('heading', { name: 'Call' })).toBeVisible({ timeout: UI_READY });
      await expect(drawer.getByText('Denied', { exact: true }).first()).toBeVisible({ timeout: UI_READY });
      await expect(drawer.getByText('delete_file', { exact: true }).first()).toBeVisible({ timeout: UI_READY });
      await expect(drawer.getByText(serverId, { exact: true }).first()).toBeVisible({ timeout: UI_READY });
      await expect(drawer.getByRole('heading', { name: 'Error' })).toBeVisible({ timeout: UI_READY });
      await expect(drawer.getByText('tool_denied', { exact: true }).first()).toBeVisible({ timeout: UI_READY });
      await drawer.getByRole('button', { name: 'Close' }).click();
      await expect(drawer).toBeHidden({ timeout: UI_READY });
    }, { timeout: STEP_BUDGET });

    await test.step('UI: the echo call\'s drawer names the tool and the server', async () => {
      await grid.row((await grid.columnHeaded('Tool')).indexOf('echo')).click();
      await expect(drawer.getByRole('heading', { name: 'Call' })).toBeVisible({ timeout: UI_READY });
      await expect(drawer.getByText('echo', { exact: true }).first()).toBeVisible({ timeout: UI_READY });
      await expect(drawer.getByText(serverId, { exact: true }).first()).toBeVisible({ timeout: UI_READY });
      await expect(drawer.getByRole('heading', { name: 'Error' })).toHaveCount(0);
    }, { timeout: STEP_BUDGET });
  },
);


// The OFF phase is a separate W07 stack; never toggle a running deployment in a flow.
test(
  'GW-E2E-037: tool-call switches OFF refuse reads and downloads while legacy MCP stays visible',
  {
    tag: ['@flow', profileTag('mcp'), '@mcp-subfeature-W07'],
    annotation: flowAnnotation({
      id: 'GW-E2E-037', area: 'gateway',
      userGoal: 'Keep static MCP and the playground available while the new call explorer and downloads are off',
      steps: ['open the Tool calls deep link on the W07 OFF stack'],
      backendChecks: ['status reports both capabilities false; list, detail and both download formats return the typed explorer-disabled 404'],
      browserChecks: ['the deep link opens the disabled Tool calls tab, which says Not available in this deployment, without tool-call requests; Servers and Try a tool remain enabled'],
    }),
  },
  async ({ browser, scopeActors: scopes }, testInfo) => {
    expect(process.env.E2E_MCP_SUBFEATURE_SELECTION).toBe('W07');
    await testInfo.attach('mcp-profile', { body: JSON.stringify(inspectMcpCatalog()), contentType: 'application/json' });
    const owner = scopes.ownerA;
    await owner.api.post(UPDATE_CONFIG, { mcp: { enabled: true } });
    await expect.poll(async () => {
      const status = await owner.api.get<Envelope<{ enabled: boolean; features: Record<string, boolean> }>>(MCP_STATUS);
      expect(status.result.features).toMatchObject({ tool_call_explorer: false, tool_call_download: false });
      return status.result.enabled;
    }, POLL.GATEWAY_SYNC).toBe(true);
    for (const path of [TOOL_CALLS, `${TOOL_CALLS}1/`, `${TOOL_CALLS}export/?export_format=csv`, `${TOOL_CALLS}export/?export_format=json`]) {
      const refused = await scopes.send<{ code: string }>(owner, 'GET', path);
      expect(refused.status).toBe(404);
      expect(refused.body.code).toBe('mcp_tool_call_explorer_disabled');
    }
    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    const toolCallRequests: string[] = [];
    page.on('request', (req) => {
      if (new URL(req.url()).pathname.startsWith(TOOL_CALLS)) toolCallRequests.push(req.method());
    });
    for (const path of [`${MCP_PATH}?tab=calls`, `${MCP_PATH}/calls`]) {
      await page.goto(path, { waitUntil: 'domcontentloaded' });
      // The link lands on the disabled Tool calls tab
      // with the deployment's line, not Overview (was: no Tool calls tab, Overview selected).
      const calls = page.getByRole('tab', { name: 'Tool calls', exact: true });
      await expect(calls).toHaveAttribute('aria-selected', 'true', { timeout: UI_READY });
      await expect(calls).toHaveAttribute('aria-disabled', 'true');
      await expect(page.getByTestId('mcp-tab-off-notice')).toHaveText('Not available in this deployment', { timeout: UI_READY });
      await expect(page.getByRole('tab', { name: 'Overview', exact: true })).toHaveAttribute('aria-selected', 'false');
      await expect(page.getByRole('tab', { name: 'Servers', exact: true })).toBeEnabled();
      await expect(page.getByRole('tab', { name: 'Try a tool', exact: true })).toBeEnabled();
    }
    expect(toolCallRequests).toEqual([]);
  },
);
