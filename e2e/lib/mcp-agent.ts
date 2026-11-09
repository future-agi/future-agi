import { expect } from '@playwright/test';
import type { APIRequestContext } from '@playwright/test';
import { E2E } from './env';

// Shared with GW-E2E-029: real agent JSON-RPC over Streamable HTTP, not a
// gateway-config proxy for tools/list or tools/call. No credentials are logged.
export async function rpc(req: APIRequestContext, path: string, key: string, session: string | undefined, message: object) {
  const res = await req.post(`${E2E.gatewayUrl}${path}`, {
    headers: {
      Authorization: `Bearer ${key}`,
      'Content-Type': 'application/json',
      Accept: 'application/json, text/event-stream',
      ...(session ? { 'Mcp-Session-Id': session } : {}),
    },
    data: message,
    maxRedirects: 0,
  });
  const text = await res.text();
  const sse = (res.headers()['content-type'] ?? '').includes('text/event-stream');
  const payload = sse
    ? text.split('\n').filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim()).pop() ?? ''
    : text;
  return { status: res.status(), session: res.headers()['mcp-session-id'], body: payload ? JSON.parse(payload) : null };
}

export async function agentSession(req: APIRequestContext, path: string, key: string): Promise<string | undefined> {
  const init = await rpc(req, path, key, undefined, {
    jsonrpc: '2.0', id: 1, method: 'initialize',
    params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'e2e-tool-review', version: '1' } },
  });
  expect(init.status, JSON.stringify(init.body)).toBe(200);
  await rpc(req, path, key, init.session, { jsonrpc: '2.0', method: 'notifications/initialized' });
  return init.session;
}

// A fresh session: pin invalidation must be reflected in the tools offered to
// a new agent, independent of a previously cached tools/list response.
export async function offered(req: APIRequestContext, path: string, key: string) {
  const session = await agentSession(req, path, key);
  const list = await rpc(req, path, key, session, { jsonrpc: '2.0', id: 2, method: 'tools/list' });
  expect(list.status, JSON.stringify(list.body)).toBe(200);
  expect(list.body?.error).toBeUndefined();
  const tools = (list.body?.result?.tools ?? []) as Array<{ name: string; description?: string }>;
  return { session, names: tools.map((t) => t.name).sort(),
    descriptions: Object.fromEntries(tools.map((t) => [t.name, t.description ?? ''])) };
}
