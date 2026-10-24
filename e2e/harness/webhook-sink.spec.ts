import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { test, expect } from '../lib/fixtures';
import { ApiError } from '../lib/api-client';
import { PUBLIC_NETWORK } from '../lib/public-network';
import { mintScope, WebhookSink } from '../lib/sinks';

// WebhookSink's live test (its offline half is scripts/webhook-sink.test.mjs). The product
// refuses every private webhook address at save and at delivery
// (agentcc/serializers/webhook.py validate_url, views/webhook_outbound.py test);
// the sink is reachable because e2e-public is public-classified, not because any
// guard is relaxed: (c) is the positive control that the guard is intact.

const root = fileURLToPath(new URL('../../', import.meta.url));
const sink = new WebhookSink();
// agentcc/services/url_safety.py WEBHOOK_PRIVATE_URL_ERROR.
const WEBHOOK_PRIVATE_URL_ERROR = 'Webhook URL cannot point to internal or private addresses';
const { ip, alias } = PUBLIC_NETWORK.pinned['webhook-sink'];

type Created = { result: { id: string } };
type TestResult = { result: { status_code: number | null; success: boolean } };
type EventRow = { event_type: string; status: string; attempts: number; last_response_code: number | null };

test('webhook sink live: the product\'s Python resolves the sink to a global address only', () => {
  // bin/e2e uses DOCKER_HOST (or the default daemon), never a context.
  const env = { ...process.env };
  delete env.DOCKER_CONTEXT;
  const service = process.env.E2E_STACK === 'standalone' ? 'app' : 'backend';
  const out = execFileSync(`${root}bin/e2e`, ['compose', 'exec', '-T', service, 'python', '-I', '-c',
    'import ipaddress, socket, sys; ips = sorted({a[4][0] for a in socket.getaddrinfo(sys.argv[1], 8080)}); '
    + 'print(ips, [ipaddress.ip_address(i).is_global for i in ips])', alias], { encoding: 'utf8', env, timeout: 60_000 });
  expect(out.trim()).toBe(`['${ip}'] [True]`);
});

test('webhook sink live: a saved webhook on the sink receives the product\'s test delivery, once', async ({ actor }, testInfo) => {
  const channel = mintScope('hook');
  const created = await actor.api.post<Created>('/agentcc/webhooks/', {
    name: `e2e-hook-${channel}`, url: sink.url(channel), secret: `e2e-hook-secret-${channel}`,
    events: ['request.completed'], is_active: true,
  });
  await testInfo.attach('seeded', { contentType: 'application/json',
    body: JSON.stringify({ channel, webhookId: created.result.id, organizationId: actor.organizationId }) });
  const sent = await actor.api.post<TestResult>(`/agentcc/webhooks/${created.result.id}/test/`);
  expect(sent.result).toEqual({ status_code: 200, success: true });
  const received = await sink.received(channel);
  expect(received.map((r) => ({ method: r.method, status: r.status, body: r.body }))).toEqual([{ method: 'POST', status: 200,
    body: { event: 'test', webhook_id: created.result.id, message: 'This is a test event from Agentcc.' } }]);
  expect(received[0].headers['x-agentcc-signature']).toMatch(/^sha256=[0-9a-f]{64}$/);
});

test('webhook sink live: POSITIVE CONTROL, a webhook on the private compose network is refused', async ({ actor }) => {
  // The control addresses mock-llm by its private compose-network address, as the
  // product's Python resolves it. Not by name: the model field is a URLField
  // (agentcc/models/webhook.py url), and DRF refuses a host with no TLD ("Enter a
  // valid URL.") before validate_url runs; an IPv4 literal passes it. Not by a name
  // nobody resolves either: ensure_public_http_url raises the same
  // WEBHOOK_PRIVATE_URL_ERROR for a host that does not resolve (url_safety.py
  // _raise_if_unsafe_url, allow_unresolvable False), which would pass for the wrong reason.
  const env = { ...process.env };
  delete env.DOCKER_CONTEXT;
  const service = process.env.E2E_STACK === 'standalone' ? 'app' : 'backend';
  const out = execFileSync(`${root}bin/e2e`, ['compose', 'exec', '-T', service, 'python', '-I', '-c',
    'import ipaddress, json, socket, sys; ips = sorted({a[4][0] for a in socket.getaddrinfo(sys.argv[1], 8080)}); '
    + 'print(json.dumps([i for i in ips if ipaddress.ip_address(i).is_private]))', 'mock-llm'],
  { encoding: 'utf8', env, timeout: 60_000 });
  const privateIps = JSON.parse(out.trim().split('\n').pop() ?? '[]') as string[];
  expect(privateIps).toHaveLength(1);
  const error = await actor.api.post('/agentcc/webhooks/', { name: `e2e-hook-private-${mintScope('pc')}`,
    url: `http://${privateIps[0]}:8080/hook`, events: ['request.completed'], is_active: true }).catch((e: unknown) => e);
  expect(error).toBeInstanceOf(ApiError);
  expect((error as ApiError).status).toBe(400);
  const body = JSON.stringify((error as ApiError).body);
  expect(body).not.toContain('Enter a valid URL');
  expect(body).toContain(WEBHOOK_PRIVATE_URL_ERROR);
});

test('webhook sink live: failFirst 1 answers the first delivery 500, and the product records it failed', async ({ actor }, testInfo) => {
  const channel = mintScope('hook-fail');
  const created = await actor.api.post<Created>('/agentcc/webhooks/', {
    name: `e2e-hook-${channel}`, url: sink.url(channel, { failFirst: 1 }), events: ['request.completed'], is_active: true,
  });
  await testInfo.attach('seeded', { contentType: 'application/json',
    body: JSON.stringify({ channel, webhookId: created.result.id }) });
  const first = await actor.api.post<TestResult>(`/agentcc/webhooks/${created.result.id}/test/`);
  expect(first.result).toEqual({ status_code: 500, success: false });
  const events = await actor.api.get<{ result: EventRow[] }>('/agentcc/webhook-events/', { webhook_id: created.result.id });
  expect(events.result.map(({ event_type, status, attempts, last_response_code }) =>
    ({ event_type, status, attempts, last_response_code }))).toEqual([
    { event_type: 'test', status: 'failed', attempts: 1, last_response_code: 500 }]);
  const second = await actor.api.post<TestResult>(`/agentcc/webhooks/${created.result.id}/test/`);
  expect(second.result).toEqual({ status_code: 200, success: true });
  expect((await sink.received(channel)).map((r) => r.status)).toEqual([500, 200]);
});
