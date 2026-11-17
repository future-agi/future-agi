import { request } from '@playwright/test';
import { test, expect } from '../../../lib/scope-actors';
import { offered, rpc } from '../../../lib/mcp-agent';
import { POLL } from '../../../lib/state-probe';
import { flowAnnotation } from '../../../lib/flow-meta';
import { profileTag } from '../../../lib/profile';
import { MCP_MOCK } from '../../../lib/public-network';
import { inspectMcpCatalog } from '../../../lib/mcp-catalog';
import { GatewayAdmin } from '../../../lib/gateway-admin';

// Tool pinning (M20, M21): once an organization accepted its MCP server's tools,
// a tool whose definition the upstream changes, or a tool it adds, is held: not
// offered, and a call to it refused, until an Owner or Admin reviews what changed
// and accepts it. The other tools keep working throughout.
//
// Runs on the mcp gateway profile only (harness extension H-MCP, tag
// @gw-profile-mcp; E2E_STACK=standalone E2E_GATEWAY_PROFILE=mcp). The one MCP
// upstream is mcp-mock (lib/public-network.ts MCP_MOCK); its /mcp-next path is
// the same server's next release (e2e/stack/mcp-mock/main.go, fixtures-next):
// echo's description changed and lookup_order is new, behind the same bearer.
// The organization points its server at it, as it would when the vendor ships
// a release at the URL it already uses.
//
// Pinned off the code it drives:
// - The seed is GW-E2E-023's save (AddMCPServerDialog.jsx handleSave;
//   useMCPConfigPatch.js "Turn on MCP"); the server's next URL is saved the same
//   way, its bearer sent again (the token is write-only: GW-E2E-023).
// - Pinning defaults (tenant config_mcp.go MCPPinning): mode quarantine,
//   new_tools quarantine, auto_baseline on, so a server with no pins serves its
//   tools as its baseline and an admin's revision list writes those pins
//   (views/mcp_revisions.py MCPRevisionListView: revisions.pull with baseline
//   for an admin; services/mcp_revisions.py).
// - Held tools: not listed (hook_list_pin.go pinFilter, OrgPins.FilterList) and
//   a call refused in band with tool_quarantined, metadata.reason changed or new
//   (hook_pin.go pinQuarantined).
// - REVISIONS: GET (pending by default; {revisions: [{id, server_id, tool,
//   reason, status, diff: [{field, before, after}]}]}), POST <id>/approve/ (an
//   Owner or Admin; serializers/mcp_revisions.py, urls_mcp_revisions.py).
const MCP_STATUS = '/agentcc/gateways/default/mcp-status/';
const UPDATE_SERVER = '/agentcc/gateways/default/update-mcp-server/';
const UPDATE_CONFIG = '/agentcc/gateways/default/update-config/';
const GATEWAY_CONFIG = '/agentcc/gateways/default/config/';
const REVISIONS = '/agentcc/mcp/revisions/';
const ERROR_META = 'io.futureagi/error';
// The next release of the mock's server (e2e/stack/mcp-mock/main.go: /mcp-next).
const NEXT_URL = MCP_MOCK.url.replace(/\/mcp$/, '/mcp-next');
// echo's description in each release (scenarios fixtures/mcp/common and
// e2e/stack/mcp-mock/fixtures-next).
const ECHO_BEFORE = 'Returns its arguments as text.';
const ECHO_AFTER = 'Returns its arguments as text, and keeps a copy of everything it is sent.';
// scopeActors provisioning 120 + seeding 30 + GATEWAY_SYNC 75 (tools served) +
// GATEWAY_SYNC 75 (pins reach the gateway) + GATEWAY_SYNC 75 (the next release
// is listed) + GATEWAY_SYNC 75 (the accepted pin is served) + API checks 40 +
// headroom 110.
const FLOW_TIMEOUT = 600_000;

interface Envelope<T> { result: T }
interface Revision { id: number; server_id: string; tool: string; reason: string; status: string;
  diff: Array<{ field: string; before?: unknown; after?: unknown }> }

test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-029: a tool the organization\'s MCP server changes or adds is held for review, and an Owner\'s acceptance serves it again',
  {
    tag: ['@flow', profileTag('mcp')],
    annotation: flowAnnotation({
      id: 'GW-E2E-029',
      area: 'gateway',
      userGoal:
        'An Owner trusts that when an MCP server the organization uses changes a tool or adds one, agents cannot use it until an admin has seen what changed and accepted it, while the server\'s other tools keep working',
      steps: [
        'seed the organization\'s MCP server with its bearer token and MCP on, as GW-E2E-023 saves them, a virtual server over it, and mint a gateway key',
        'as the agent, list the virtual server\'s tools',
        'as the Owner, open the tool revisions, which accepts the server\'s tools as its baseline',
        'as the Owner, point the server at its next release, where echo changed and lookup_order is new',
        'as the agent, list the tools again and call echo',
        'as the Owner, review the held revisions; as a Member, try to accept one',
        'as the Owner, accept echo\'s revision; as the agent, list and call echo again',
      ],
      backendChecks: [
        'before review the agent is offered the server\'s three tools, and the Owner\'s revision list writes exactly those three as the organization\'s pins, which reach the gateway',
        'after the release the agent is offered get_weather and delete_file only: changed echo and new lookup_order are held, and a call to echo is refused in band with tool_quarantined, reason changed',
        'the pending revisions are exactly echo (changed, its description before and after) and lookup_order (new), and another organization sees none of them',
        'a Member cannot accept a revision',
        'once the Owner accepts echo\'s revision, the agent is offered echo with its new description and its call is answered, while lookup_order stays held',
      ],
    }),
  },
  async ({ scopeActors: scopes }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    await testInfo.attach('mcp-profile', { body: JSON.stringify(inspectMcpCatalog()), contentType: 'application/json' });
    const owner = scopes.ownerA;
    const other = scopes.ownerB;
    const member = scopes.member;

    const status = await owner.api.get<Envelope<{ enabled: boolean; reason?: string }>>(MCP_STATUS);
    expect(status.result.reason, 'the mcp profile\'s gateway serves MCP: a fresh org\'s status names no reason')
      .toBeUndefined();

    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const serverId = `e2e-gw29-${suffix}`;
    const slug = `e2e-gw29-${suffix}`;
    const path = `/mcp/v/${slug}`;
    const name = (tool: string) => `${serverId}__${tool}`;
    const marker = `e2e-gw29-echo-${suffix}`;
    const saveServer = (url: string) => owner.api.post(UPDATE_SERVER, {
      server_id: serverId, config: { transport: 'http', url, auth: { type: 'bearer', token: MCP_MOCK.bearer } },
    });
    await saveServer(MCP_MOCK.url);
    await owner.api.post(UPDATE_CONFIG, { mcp: { enabled: true } });
    await owner.api.post(UPDATE_CONFIG, { mcp: { virtual_servers: { [slug]: {
      title: `Tool review ${suffix}`, members: [serverId], naming: 'prefixed',
    } } } });
    const key = await owner.api.post<Envelope<{ key: string; gateway_key_id: string }>>(
      '/agentcc/api-keys/', { name: `e2e-gw29-${suffix}` });
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ suffix, serverId, slug, nextUrl: NEXT_URL, organizationId: owner.organizationId,
        otherOrganizationId: other.organizationId, gatewayKeyId: key.result.gateway_key_id }, null, 2),
    });

    const req = await request.newContext();
    const admin = await GatewayAdmin.open();
    const pending = async (actor = owner) => (await actor.api.get<Envelope<{ revisions: Revision[] }>>(REVISIONS))
      .result.revisions.filter((r) => r.server_id === serverId);
    try {
      await test.step('agent: the server\'s three tools are offered', async () => {
        await expect.poll(async () => (await offered(req, path, key.result.key)).names, POLL.GATEWAY_SYNC)
          .toEqual(['delete_file', 'echo', 'get_weather'].map(name));
      });

      await test.step('Owner: the revision list accepts the three tools as the server\'s baseline', async () => {
        const pins = async () => Object.keys((await owner.api.get<Envelope<{ mcp?: { pinning?: { pins?: Record<string, string> } } }>>(
          GATEWAY_CONFIG)).result.mcp?.pinning?.pins ?? {}).filter((k) => k.startsWith(`${serverId}/`)).sort();
        await expect.poll(async () => { await pending(); return pins(); }, POLL.GATEWAY_SYNC)
          .toEqual(['delete_file', 'echo', 'get_weather'].map((tool) => `${serverId}/${tool}`));
        await expect.poll(async () => Object.keys(
          (await admin.orgConfig(owner.organizationId))?.mcp?.pinning?.pins ?? {}).filter((k) => k.startsWith(`${serverId}/`)).length,
        POLL.GATEWAY_SYNC).toBe(3);
      });

      await test.step('Owner: the server moves to its next release; changed echo and new lookup_order are held', async () => {
        await saveServer(NEXT_URL);
        let session: string | undefined;
        await expect.poll(async () => {
          const seen = await offered(req, path, key.result.key);
          session = seen.session;
          return seen.names;
        }, POLL.GATEWAY_SYNC).toEqual(['delete_file', 'get_weather'].map(name));
        const call = await rpc(req, path, key.result.key, session, {
          jsonrpc: '2.0', id: 3, method: 'tools/call', params: { name: name('echo'), arguments: { text: marker } },
        });
        expect(call.status, JSON.stringify(call.body)).toBe(200);
        expect(call.body?.result?.isError, JSON.stringify(call.body)).toBe(true);
        const refusal = call.body?.result?._meta?.[ERROR_META];
        expect({ code: refusal?.code, reason: refusal?.metadata?.reason }, JSON.stringify(call.body))
          .toEqual({ code: 'tool_quarantined', reason: 'changed' });
      });

      let echoRevision = 0;
      await test.step('Owner: the held revisions say what changed; another org sees none', async () => {
        await expect.poll(async () => (await pending()).map((r) => `${r.tool}:${r.reason}:${r.status}`).sort(),
          POLL.GATEWAY_SYNC).toEqual(['echo:changed:pending', 'lookup_order:new:pending']);
        const echo = (await pending()).find((r) => r.tool === 'echo')!;
        expect(echo.diff).toEqual([{ field: 'description', before: ECHO_BEFORE, after: ECHO_AFTER }]);
        echoRevision = echo.id;
        expect(await pending(other)).toEqual([]);
      });

      await test.step('Member: cannot accept a revision', async () => {
        const refused = await scopes.send(member, 'POST', `${REVISIONS}${echoRevision}/approve/`, {});
        expect(refused.status).toBe(403);
      });

      await test.step('Owner: accepting echo serves it again, with its new description; lookup_order stays held', async () => {
        const accepted = await scopes.send<Envelope<{ status: string; tool: string }>>(
          owner, 'POST', `${REVISIONS}${echoRevision}/approve/`, {});
        expect(accepted.status).toBe(200);
        expect({ tool: accepted.body.result.tool, status: accepted.body.result.status })
          .toEqual({ tool: 'echo', status: 'approved' });
        let seen = { session: undefined as string | undefined, names: [] as string[], descriptions: {} as Record<string, string> };
        await expect.poll(async () => { seen = await offered(req, path, key.result.key); return seen.names; },
          POLL.GATEWAY_SYNC).toEqual(['delete_file', 'echo', 'get_weather'].map(name));
        expect(seen.descriptions[name('echo')]).toBe(ECHO_AFTER);
        const call = await rpc(req, path, key.result.key, seen.session, {
          jsonrpc: '2.0', id: 4, method: 'tools/call', params: { name: name('echo'), arguments: { text: marker } },
        });
        expect(call.body?.result?.isError ?? false, JSON.stringify(call.body)).toBe(false);
        expect((await pending()).map((r) => `${r.tool}:${r.reason}`)).toEqual(['lookup_order:new']);
      });
    } finally {
      await req.dispose();
      await admin.dispose();
    }
  },
);
