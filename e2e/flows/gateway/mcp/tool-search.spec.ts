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

// Tool search on a virtual MCP server (M30): in search mode the server offers an
// agent three meta-tools in place of its tools, search_tools finds the tools the
// agent may use, and call_tool runs one of them exactly as a direct call would:
// judged by the same access rules, sent to the same upstream and recorded once
// as that tool, made through call_tool.
//
// Run stack commands AND this flow through with-mcp-subfeatures.sh W14.
// W14 enables the explorer/download switches; other MCP switches stay OFF.
// Runs on the mcp gateway profile only (harness extension H-MCP, tag
// @gw-profile-mcp; E2E_STACK=standalone E2E_GATEWAY_PROFILE=mcp): the stack's
// gateway serves MCP (e2e/stack/gateway.mcp.yaml) and its one MCP upstream is
// mcp-mock (lib/public-network.ts MCP_MOCK), with the scenarios' common fixtures:
// echo and get_weather read-only, delete_file destructive.
//
// Pinned off the code it drives:
// - The seed is GW-E2E-023's save (AddMCPServerDialog.jsx handleSave;
//   useMCPConfigPatch.js "Turn on MCP"), and a virtual server through the
//   console's update-config patch (virtualServers.js virtualServerPatch: members,
//   naming "prefixed", tools.include) plus mode "search", which the console's
//   drawer keeps but does not edit (virtualServerPatch leaves it to the merge);
//   Django's contract: contracts/gateway_admin.py mode Literal["direct", "search"].
// - The meta-tools: vserver/names.go MetaSearchTools, MetaDescribeTool,
//   MetaCallTool; search mode lists exactly them (hook_list_toolset.go toolset)
//   with _meta["io.futureagi/tool_list"] {mode: "search", tools_full, tools_sent}.
// - search_tools {query, limit}: the gateway answers it itself from what a
//   direct list would offer, structuredContent {tools: [{name, title,
//   description}]} (hook_metatool.go searchTools).
// - call_tool {name, arguments}: rewritten to a call of the named tool before it
//   is resolved (enterCallTool); a tool the agent is not offered answers as a
//   missing one, -32602 Unknown tool (hook_acl.go authorizeTool).
// - The record: agentcc_tool_call via "call_tool" (record.go Via,
//   models/mcp_tool_call.py via); the list API views/mcp_tool_calls.py.
// - The page: MCP_PATH ?tab=virtual (mcp/tabs.js); the grid's Slug and Not
//   enforced columns (VirtualServersTab.jsx; virtualServers.js notEnforced).
const MCP_PATH = '/dashboard/gateway/mcp';
const MCP_STATUS = '/agentcc/gateways/default/mcp-status/';
const UPDATE_SERVER = '/agentcc/gateways/default/update-mcp-server/';
const UPDATE_CONFIG = '/agentcc/gateways/default/update-config/';
const TOOL_CALLS = '/agentcc/mcp/tool-calls/';
const TOOL_LIST_META = 'io.futureagi/tool_list';
const META_TOOLS = ['call_tool', 'describe_tool', 'search_tools'];
// get_weather's canned answer (agentcc-gateway scripts/scenarios/fixtures/mcp/common/tools.json).
const WEATHER = 'Sunny, 21 C';
const RECORD_VISIBLE = POLL.ASYNC_JOB;
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// scopeActors provisioning 120 + seeding 30 + GATEWAY_SYNC 75 (search mode
// served) + agent calls 30 + RECORD_VISIBLE 60 + one UI step of STEP_BUDGET 75
// + API checks 30 + headroom 80.
const FLOW_TIMEOUT = 500_000;

interface Envelope<T> { result: T }
interface ToolCall { id: number; server_id: string; tool: string; upstream_tool: string; outcome: string; via: string;
  api_key_id: string }
interface CursorPage<T> { next: string | null; results: T[] }

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

async function agentSession(req: APIRequestContext, path: string, key: string): Promise<string | undefined> {
  const init = await rpc(req, path, key, undefined, {
    jsonrpc: '2.0', id: 1, method: 'initialize',
    params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'e2e-gw28', version: '1' } },
  });
  expect(init.status, JSON.stringify(init.body)).toBe(200);
  await rpc(req, path, key, init.session, { jsonrpc: '2.0', method: 'notifications/initialized' });
  return init.session;
}

const texts = (result: { content?: Array<{ type: string; text?: string }> } | undefined) =>
  (result?.content ?? []).filter((part) => part.type === 'text').map((part) => part.text ?? '');

test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-031: an agent on a virtual MCP server in search mode finds a tool with search_tools and runs it with call_tool, recorded once as that tool',
  {
    tag: ['@flow', profileTag('mcp'), '@mcp-subfeature-W14'],
    annotation: flowAnnotation({
      id: 'GW-E2E-031',
      area: 'gateway',
      userGoal:
        'A platform engineer puts a virtual MCP server in search mode so an agent is offered three meta-tools instead of every tool, and the agent finds the tool it needs and runs it, with the same rules and record as a direct call',
      steps: [
        'seed the organization\'s MCP server with its bearer token and MCP on, as GW-E2E-023 saves them, a virtual server in search mode over its echo and get_weather tools, and mint a gateway key',
        'open MCP tools, Virtual servers, and read the virtual server\'s row',
        'as the agent, open an MCP session on the virtual server and list its tools',
        'as the agent, search for a weather tool, then run the tool search returns with call_tool',
        'as the agent, search for the delete tool the virtual server leaves out, and try to run it with call_tool',
      ],
      backendChecks: [
        'the virtual server lists exactly search_tools, describe_tool and call_tool, and says it offers them in place of its two tools',
        'search_tools for "weather" returns the virtual server\'s get_weather tool first, and for "delete" no tool',
        'call_tool on get_weather answers the upstream\'s canned weather',
        'call_tool on delete_file, which the virtual server leaves out, is refused as an unknown tool and never reaches the upstream',
        'PG agentcc_tool_call holds exactly one row for the organization and server: get_weather, success, made via call_tool by the minted key, and the list API returns the same one',
      ],
      browserChecks: [
        'the Virtual servers grid lists the virtual server with nothing in Not enforced: search mode is applied',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    expect(process.env.E2E_MCP_SUBFEATURE_SELECTION, 'start and run this flow with the W14 wrapper').toBe('W14');
    await testInfo.attach('mcp-profile', { body: JSON.stringify(inspectMcpCatalog()), contentType: 'application/json' });
    const owner = scopes.ownerA;

    const status = await owner.api.get<Envelope<{ enabled: boolean; reason?: string; features: Record<string, boolean> }>>(MCP_STATUS);
    expect(status.result.reason, 'the mcp profile\'s gateway serves MCP: a fresh org\'s status names no reason')
      .toBeUndefined();
    expect(status.result.features).toMatchObject({ tool_call_explorer: true, tool_call_download: true });

    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const serverId = `e2e-gw28-${suffix}`;
    const slug = `e2e-gw28-${suffix}`;
    const path = `/mcp/v/${slug}`;
    const weather = `${serverId}__get_weather`;
    const city = `e2e-gw28-city-${suffix}`;
    await owner.api.post(UPDATE_SERVER, {
      server_id: serverId,
      config: { transport: 'http', url: MCP_MOCK.url, auth: { type: 'bearer', token: MCP_MOCK.bearer } },
    });
    await owner.api.post(UPDATE_CONFIG, { mcp: { enabled: true } });
    await owner.api.post(UPDATE_CONFIG, { mcp: { virtual_servers: { [slug]: {
      title: `Tool search ${suffix}`,
      members: [serverId],
      naming: 'prefixed',
      tools: { include: [`${serverId}/echo`, `${serverId}/get_weather`], exclude: null },
      mode: 'search',
    } } } });
    const key = await owner.api.post<Envelope<{ key: string; gateway_key_id: string }>>(
      '/agentcc/api-keys/', { name: `e2e-gw28-${suffix}` });
    const gatewayKeyId = key.result.gateway_key_id;
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ suffix, serverId, slug, city, organizationId: owner.organizationId, gatewayKeyId }, null, 2),
    });

    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    await page.setViewportSize(GRID_VIEWPORT);
    await test.step('UI: the virtual server is listed with nothing in Not enforced', async () => {
      await page.goto(`${MCP_PATH}?tab=virtual`, { waitUntil: 'domcontentloaded' });
      const grid = GatewayGridReader.in(page);
      await expect.poll(() => grid.columnHeaded('Slug'), { timeout: UI_READY }).toEqual([slug]);
      await expect.poll(() => grid.columnHeaded('Not enforced'), { timeout: UI_READY }).toEqual(['—']);
    }, { timeout: STEP_BUDGET });

    const req = await request.newContext();
    try {
      let session: string | undefined;
      await test.step('agent: the virtual server lists exactly the three meta-tools', async () => {
        let listed: { tools?: Array<{ name: string }>; _meta?: Record<string, any> } | undefined;
        await expect.poll(async () => {
          session = await agentSession(req, path, key.result.key);
          const list = await rpc(req, path, key.result.key, session, { jsonrpc: '2.0', id: 2, method: 'tools/list' });
          listed = list.body?.result;
          return (listed?.tools ?? []).map((t) => t.name).sort();
        }, POLL.GATEWAY_SYNC).toEqual(META_TOOLS);
        expect(listed?._meta?.[TOOL_LIST_META], JSON.stringify(listed?._meta))
          .toMatchObject({ mode: 'search', tools_full: 2, tools_sent: 3 });
      });

      await test.step('agent: search_tools finds get_weather; call_tool runs it', async () => {
        const found = await rpc(req, path, key.result.key, session, {
          jsonrpc: '2.0', id: 3, method: 'tools/call', params: { name: 'search_tools', arguments: { query: 'weather', limit: 5 } },
        });
        expect(found.status, JSON.stringify(found.body)).toBe(200);
        const hits = (found.body?.result?.structuredContent?.tools ?? []) as Array<{ name: string }>;
        expect(hits[0]?.name, JSON.stringify(found.body)).toBe(weather);
        const ran = await rpc(req, path, key.result.key, session, {
          jsonrpc: '2.0', id: 4, method: 'tools/call', params: { name: 'call_tool', arguments: { name: hits[0].name, arguments: { city } } },
        });
        expect(ran.status, JSON.stringify(ran.body)).toBe(200);
        expect(ran.body?.result?.isError ?? false, JSON.stringify(ran.body)).toBe(false);
        expect(texts(ran.body?.result)).toEqual([WEATHER]);
      });

      await test.step('agent: the left-out delete tool is neither found nor run', async () => {
        const found = await rpc(req, path, key.result.key, session, {
          jsonrpc: '2.0', id: 5, method: 'tools/call', params: { name: 'search_tools', arguments: { query: 'delete file', limit: 5 } },
        });
        expect(found.body?.result?.structuredContent?.tools, JSON.stringify(found.body)).toEqual([]);
        const ran = await rpc(req, path, key.result.key, session, {
          jsonrpc: '2.0', id: 6, method: 'tools/call',
          params: { name: 'call_tool', arguments: { name: `${serverId}__delete_file`, arguments: { path: `/tmp/${suffix}` } } },
        });
        expect(ran.body?.error, JSON.stringify(ran.body)).toMatchObject({ code: -32602 });
      });
    } finally {
      await req.dispose();
    }

    await test.step('storage: exactly one record, get_weather via call_tool, by the key', async () => {
      const rows = () => probe.pg<{ upstream_tool: string; outcome: string; via: string; api_key_id: string }>(
        `SELECT upstream_tool, outcome, via, api_key_id FROM agentcc_tool_call
          WHERE organization_id = $1 AND server_id = $2 ORDER BY upstream_tool`,
        [owner.organizationId, serverId],
      );
      await expect.poll(async () => (await rows()).length, RECORD_VISIBLE).toBeGreaterThanOrEqual(1);
      expect(await rows()).toEqual([
        { upstream_tool: 'get_weather', outcome: 'success', via: 'call_tool', api_key_id: gatewayKeyId },
      ]);
    });

    await test.step('API: the tool-call list returns the same one record', async () => {
      const listed = await owner.api.get<CursorPage<ToolCall>>(TOOL_CALLS, { server_id: serverId });
      expect(listed.results.map(({ server_id, upstream_tool, outcome, via }) => ({ server_id, upstream_tool, outcome, via })))
        .toEqual([{ server_id: serverId, upstream_tool: 'get_weather', outcome: 'success', via: 'call_tool' }]);
    });
  },
);
