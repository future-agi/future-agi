import { randomUUID } from 'node:crypto';
import { test, expect } from '@playwright/test';
import { startMcpOAuthFixture } from '../_fixtures/mcp-approvals/server.ts';
import type { Counter } from '../_fixtures/mcp-approvals/counters.ts';
import { attachSeededIds } from '../_shared/contract';

/** Source da4c81cc62: internal/mcp/types.go ProtocolVersion/InitializeParams,
 * client.go gatewayClientInfo/newID/discover/initialize; probe.go probeClientInfo,
 * post/send/streamable; upstream/wire/{era,meta}.go RequestMeta/SetMeta.
 * These are the exact default discovery + initialize payloads, including the
 * numeric IDs and differing clientInfo names. Fixture response semantics are
 * mcp-http.ts, negotiating like mcpmock/{era,rpc}.go for the 2025-03-26 era.
 * This does NOT execute the Go clients; pinned native-client execution is OWED.
 */
const details = { tag: ['@j14-fixture-only'], annotation: [{
  type: 'journey-subassertion', description: JSON.stringify({ packet: 'J14',
    scope: 'LOCAL_FIXTURE_SELF_TEST', satisfiesPacketRow: false, productExecution: false }),
}] };
const order = (rows: Counter[]) => [...rows].sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));

for (const name of ['agentcc-gateway', 'agentcc-gateway-probe']) {
  test(`J14-FIXTURE ${name} default revision negotiates a usable legacy session`, details, async ({}, info) => {
    const principal = randomUUID();
    await attachSeededIds(info, 'J14', { userIds: [principal] });
    const fixture = await startMcpOAuthFixture({ principalIds: [principal],
      redirectUris: ['http://127.0.0.1:12345/callback'] });
    try {
      const token = fixture.issueBearer(principal);
      const clientInfo = { name, version: '1.0.0' };
      const post = (message: object, headers: Record<string, string> = {}) => fetch(fixture.resource, {
        method: 'POST', redirect: 'manual', headers: { 'Content-Type': 'application/json',
          Accept: 'application/json, text/event-stream', Authorization: `Bearer ${token}`, ...headers },
        body: JSON.stringify(message),
      });
      const discovery = await post({ jsonrpc: '2.0', id: 1, method: 'server/discover', params: { _meta: {
        'io.modelcontextprotocol/protocolVersion': '2026-07-28',
        'io.modelcontextprotocol/clientCapabilities': {},
        'io.modelcontextprotocol/clientInfo': clientInfo,
      } } }, { 'MCP-Protocol-Version': '2026-07-28', 'Mcp-Method': 'server/discover' });
      expect(discovery.status).toBe(404);
      expect((await discovery.json()).error.code).toBe(-32001);
      const initialize = await post({ jsonrpc: '2.0', id: 2, method: 'initialize', params: {
        protocolVersion: '2025-11-25', capabilities: {}, clientInfo,
      } });
      expect(initialize.status).toBe(200);
      expect(await initialize.json()).toEqual({ jsonrpc: '2.0', id: 2, result: {
        protocolVersion: '2025-03-26', capabilities: { tools: { listChanged: false } },
        serverInfo: { name: 'j14-local-fixture', version: '1.0.0' },
      } });
      const sid = initialize.headers.get('mcp-session-id');
      expect(typeof sid === 'string' && sid.length > 0).toBe(true);
      const session = { 'Mcp-Session-Id': sid!, 'MCP-Protocol-Version': '2025-03-26' };
      const notified = await post({ jsonrpc: '2.0', method: 'notifications/initialized' }, session);
      expect(notified.status).toBe(202);
      const mismatch = await post({ jsonrpc: '2.0', id: 3, method: 'tools/list' },
        { ...session, 'MCP-Protocol-Version': '2025-11-25' });
      expect(mismatch.status).toBe(400);
      expect(await mismatch.json()).toEqual({ jsonrpc: '2.0', id: 3,
        error: { code: -32600, message: 'Protocol mismatch' } });
      const list = await post({ jsonrpc: '2.0', id: 4, method: 'tools/list' }, session);
      expect(list.status).toBe(200);
      expect((await list.json()).result.tools.map((tool: { name: string }) => tool.name))
        .toEqual(['echo', 'get_weather', 'delete_file']);
      const args = { text: `e2e-negotiation-${randomUUID()}` };
      const echo = await post({ jsonrpc: '2.0', id: 5, method: 'tools/call',
        params: { name: 'echo', arguments: args } }, session);
      expect(echo.status).toBe(200);
      expect((await echo.json()).result.content).toEqual([
        { type: 'text', text: `echo ok: ${JSON.stringify(args)}` },
      ]);
      // Negotiating unsupported NONEMPTY revision strings must not accept a
      // malformed/missing version or mint sessions for these invalid requests.
      for (const params of [{}, { protocolVersion: '' }, { protocolVersion: null },
        { protocolVersion: 20251125 }, { protocolVersion: [] }]) {
        const bad = await post({ jsonrpc: '2.0', id: 6, method: 'initialize', params });
        expect(bad.status).toBe(400);
        expect(bad.headers.get('mcp-session-id') === null).toBe(true);
        expect(await bad.json()).toEqual({ jsonrpc: '2.0', id: 6,
          error: { code: -32602, message: 'Initialize needs a nonempty protocolVersion' } });
      }
      const ended = await fetch(fixture.resource, { method: 'DELETE', redirect: 'manual',
        headers: { Authorization: `Bearer ${token}`, ...session } });
      expect(ended.status).toBe(204);
      const expired = await post({ jsonrpc: '2.0', id: 7, method: 'tools/list' }, session);
      expect(expired.status).toBe(404);
      expect((await expired.json()).error.code).toBe(-32001);
      expect(order(fixture.counters())).toEqual(order([
        { principalId: principal, operation: 'arrival', tool: '', outcome: 'received', count: 13 },
        { principalId: principal, operation: 'initialize', tool: '', outcome: 'accepted', count: 1 },
        { principalId: principal, operation: 'initialize', tool: '', outcome: 'refused', count: 5 },
        { principalId: principal, operation: 'list', tool: '', outcome: 'accepted', count: 1 },
        { principalId: principal, operation: 'tool', tool: 'echo', outcome: 'accepted', count: 1 },
        { principalId: principal, operation: 'transport', tool: '', outcome: 'accepted', count: 2 },
        { principalId: principal, operation: 'transport', tool: '', outcome: 'refused', count: 3 },
      ]));
    } finally { await fixture.close(); }
  });
}
