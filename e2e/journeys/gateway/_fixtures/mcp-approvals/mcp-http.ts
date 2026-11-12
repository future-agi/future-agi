import { randomBytes } from 'node:crypto';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { body, json, object, parseJson } from './local-http.ts';
import type { Counters, Tool } from './counters.ts';

/** Exact definitions (excluding mock-only price_usd/result) at da4c81cc62:
 * agentcc-gateway/scripts/scenarios/fixtures/mcp/common/tools.json.
 * JSON-RPC/initialize/session/notification wires: cmd/agentcc-mcpmock/mcpmock/
 * {http,rpc,era,tools}.go. Only 2025-03-26 JSON Streamable HTTP is implemented;
 * SSE, 2026 discovery and native install protocol coverage remain OWED.
 * Tool dispatch is deliberately UNGOVERNED: this counts what reaches the target,
 * never simulates the missing gateway approval/rate-limit enforcement.
 */
const TOOLS = [
  { name: 'echo', title: 'Echo', description: 'Returns its arguments as text.',
    inputSchema: { type: 'object', properties: { text: { type: 'string' } }, required: ['text'] },
    annotations: { readOnlyHint: true } },
  { name: 'get_weather', title: 'Weather', description: 'Canned weather for a city.',
    inputSchema: { type: 'object', properties: { city: { type: 'string' } }, required: ['city'] },
    annotations: { readOnlyHint: true, openWorldHint: true } },
  { name: 'delete_file', description: 'Pretends to delete a file.',
    inputSchema: { type: 'object', properties: { path: { type: 'string' } }, required: ['path'] },
    annotations: { destructiveHint: true } },
];
export function createMcpHandler(counters: Counters,
  identify: (token: string) => { principalId: string | null; valid: boolean },
) {
  const sessions = new Map<string, string>();
  return async (request: IncomingMessage, response: ServerResponse, url: URL): Promise<boolean> => {
    if (url.pathname !== '/mcp') return false;
    if (!['POST', 'DELETE'].includes(request.method ?? '')) {
      counters.record(null, 'transport', 'refused');
      response.setHeader('Allow', 'POST, DELETE'); json(response, 405, { error: 'method_not_allowed' }); return true;
    }
    const authorization = request.headers.authorization ?? '';
    const identity = identify(/^Bearer ([^\s]+)$/i.exec(authorization)?.[1] ?? '');
    if (!identity.valid || !identity.principalId) {
      counters.record(identity.principalId, 'transport', 'refused');
      response.setHeader('WWW-Authenticate', `Bearer resource_metadata="${url.origin}/.well-known/oauth-protected-resource/mcp"`);
      json(response, 401, { error: 'invalid_token' }); return true;
    }
    const principalId = identity.principalId;
    const sessionId = request.headers['mcp-session-id'];
    if (request.method === 'DELETE') {
      const valid = typeof sessionId === 'string' && sessions.get(sessionId) === principalId;
      counters.record(principalId, 'transport', valid ? 'accepted' : 'refused');
      if (valid) { sessions.delete(sessionId); response.writeHead(204); response.end(); }
      else json(response, 404, { error: 'session_not_found' });
      return true;
    }
    if (request.headers['content-type']?.split(';')[0] !== 'application/json') {
      counters.record(principalId, 'transport', 'refused'); json(response, 415, { error: 'invalid_request' }); return true;
    }
    const message = parseJson(await body(request));
    const id = message.id;
    const rpcError = (status: number, code: number, text: string) => json(response, status,
      { jsonrpc: '2.0', id: id ?? null, error: { code, message: text } });
    if (message.jsonrpc !== '2.0' || typeof message.method !== 'string' ||
        (id !== undefined && typeof id !== 'string' && !Number.isSafeInteger(id))) {
      counters.record(principalId, 'transport', 'refused'); rpcError(400, -32600, 'Invalid request'); return true;
    }
    const params = object(message.params ?? {});
    const respond = (result: unknown) => json(response, 200, { jsonrpc: '2.0', id, result });
    if (message.method === 'initialize' && sessionId === undefined && id !== undefined) {
      if (typeof params.protocolVersion !== 'string' || params.protocolVersion.length === 0) {
        counters.record(principalId, 'initialize', 'refused'); rpcError(400, -32602, 'Initialize needs a nonempty protocolVersion'); return true;
      }
      // da4c81cc62 mcpmock/rpc.go:initializeResult validates a nonempty string;
      // era.go:Era.negotiate returns Legacy[0] if the requested era is unsupported.
      // With Legacy=[2025-03-26], EVERY valid initialize negotiates 2025-03-26,
      // including internal/mcp/{client,probe}.go's default 2025-11-25 request.
      // Later session requests still must use the negotiated protocol below.
      const sid = randomBytes(32).toString('base64url'); sessions.set(sid, principalId);
      counters.record(principalId, 'initialize', 'accepted'); response.setHeader('Mcp-Session-Id', sid);
      respond({ protocolVersion: '2025-03-26', capabilities: { tools: { listChanged: false } },
        serverInfo: { name: 'j14-local-fixture', version: '1.0.0' } }); return true;
    }
    if (typeof sessionId !== 'string' || sessions.get(sessionId) !== principalId) {
      counters.record(principalId, 'transport', 'refused'); rpcError(404, -32001, 'Session not found'); return true;
    }
    if (request.headers['mcp-protocol-version'] !== undefined && request.headers['mcp-protocol-version'] !== '2025-03-26') {
      counters.record(principalId, 'transport', 'refused'); rpcError(400, -32600, 'Protocol mismatch'); return true;
    }
    if (id === undefined) {
      const accepted = message.method === 'notifications/initialized';
      counters.record(principalId, 'transport', accepted ? 'accepted' : 'refused');
      if (accepted) { response.writeHead(202); response.end(); }
      else rpcError(400, -32600, 'Unsupported notification');
      return true;
    }
    if (message.method === 'tools/list') {
      // This fixture has one complete page. Never silently ignore an unknown cursor.
      if (params.cursor !== undefined) {
        counters.record(principalId, 'list', 'refused'); rpcError(200, -32602, 'Invalid cursor'); return true;
      }
      counters.record(principalId, 'list', 'accepted'); respond({ tools: TOOLS }); return true;
    }
    if (message.method === 'ping') { respond({}); return true; }
    if (message.method !== 'tools/call') {
      counters.record(principalId, 'transport', 'refused'); rpcError(200, -32601, 'Method not found'); return true;
    }
    const tool: Tool = TOOLS.some(row => row.name === params.name) ? params.name as Tool : '';
    const args = object(params.arguments);
    const field = tool === 'echo' ? 'text' : tool === 'get_weather' ? 'city' : 'path';
    if (!tool || typeof args[field] !== 'string') {
      counters.record(principalId, 'tool', 'refused', tool); rpcError(200, -32602, 'Invalid tool arguments'); return true;
    }
    // Count every dispatch, INCLUDING a duplicate RPC ID. Dedupe here would hide
    // a gateway one-use bug. delete_file is inert and never touches a filesystem.
    counters.record(principalId, 'tool', 'accepted', tool);
    respond(tool === 'get_weather' ? {
      content: [{ type: 'text', text: 'Sunny, 21 C' }], structuredContent: { sky: 'sunny', celsius: 21 },
    } : { content: [{ type: 'text', text: `${tool} ok: ${JSON.stringify(args)}` }] });
    return true;
  };
}
