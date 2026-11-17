import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { inspectMcpCatalog } from '../lib/mcp-catalog';
import { profileTag } from '../lib/profile';

// The reused scenario mock is a real Streamable HTTP server with verified TLS.
// Run from the managed app's network namespace, never via a hosted endpoint.
const runner = fileURLToPath(new URL('../../bin/e2e', import.meta.url));
const script = `
import http.client, ssl, json, sys
context = ssl.create_default_context(cafile='/opt/e2e/mcp/ca.pem')
session = None
seq = 0
def rpc(method, params=None, authenticated=True):
    global session, seq
    seq += 1
    headers = {'Content-Type':'application/json', 'Accept':'application/json, text/event-stream'}
    if authenticated: headers['Authorization'] = 'Bearer e2e-mcp-upstream-bearer-not-a-secret'
    if session: headers['Mcp-Session-Id'] = session
    message = {'jsonrpc':'2.0', 'method':method, 'params':params or {}}
    if not method.startswith('notifications/'): message['id'] = seq
    connection = http.client.HTTPSConnection('mcp.e2e-public.test', 8443, context=context, timeout=10)
    connection.request('POST', '/mcp', json.dumps(message, separators=(',', ':')), headers)
    response = connection.getresponse()
    text = response.read().decode()
    session = response.getheader('Mcp-Session-Id') or session
    if response.getheader('Content-Type', '').startswith('text/event-stream'):
        text = [line[5:].strip() for line in text.splitlines() if line.startswith('data:')][-1]
    answer = {'status':response.status, 'body':json.loads(text) if text else None}
    connection.close()
    return answer
params = {'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'hmcp-harness','version':'1'}}
result = {'unauthenticated':rpc('initialize', params, False)['status'], 'initialize':rpc('initialize', params)['status']}
rpc('notifications/initialized')
for method in ['tools/list','resources/list','prompts/list']:
    result[method] = rpc(method)
result['echo'] = rpc('tools/call', {'name':'echo','arguments':{'text':sys.argv[1]}})
print(json.dumps(result))
`;

test('MCP upstream live: verified TLS, bearer gate, exact tools, resource, prompt and echo', {
  tag: [profileTag('mcp')],
}, async ({}, testInfo) => {
  test.setTimeout(90_000);
  inspectMcpCatalog();
  const marker = `e2e-upstream-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const result = JSON.parse(execFileSync(runner,
    ['compose', 'exec', '-T', 'app', 'python', '-I', '-c', script, marker],
    { encoding: 'utf8', timeout: 60_000 }));
  expect(result.unauthenticated).toBe(401);
  expect(result.initialize).toBe(200);
  expect(result['tools/list'].body.result.tools.map((tool: any) => ({
    name: tool.name, readOnly: tool.annotations?.readOnlyHint === true,
    destructive: tool.annotations?.destructiveHint === true,
  })).sort((a: any, b: any) => a.name.localeCompare(b.name))).toEqual([
    { name: 'delete_file', readOnly: false, destructive: true },
    { name: 'echo', readOnly: true, destructive: false },
    { name: 'get_weather', readOnly: true, destructive: false },
  ]);
  expect(result['resources/list'].body.result.resources.map((resource: any) => resource.uri)).toEqual(['mock://docs/readme']);
  expect(result['prompts/list'].body.result.prompts.map((prompt: any) => prompt.name)).toEqual(['greet']);
  expect(result.echo.body.result.content).toEqual([{ type: 'text', text: `echo ok: ${JSON.stringify({ text: marker })}` }]);
});
