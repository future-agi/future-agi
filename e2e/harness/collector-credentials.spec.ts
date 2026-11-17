import { createServer } from 'node:http';
import { randomBytes, randomUUID } from 'node:crypto';
import { test, expect, request, type APIRequestContext } from '@playwright/test';
import { sendTrace, sendTraceResult, type SendTraceConfig } from '../lib/otlp';
import { parseRetainedCredentials, requireRetainedFixtureTarget } from '../lib/retained-credentials';

const cfg: SendTraceConfig = {
  collectorUrl: 'http://localhost:1', apiKey: 'synthetic-public', secretKey: 'synthetic-private',
  projectName: 'e2e-obs61-unit', traceId: '11111111-2222-3333-4444-555555555555',
  rootSpanId: '1111111111111111', childSpanId: '2222222222222222',
};

test('offline: HTTP result preserves preassigned identities, deadline and value-free refusal', async () => {
  let reached = 0;
  const req = { post: async (url: string, options: { timeout: number; maxRedirects: number;
    data: { resourceSpans: { scopeSpans: { spans: { traceId: string; spanId: string }[] }[] }[] } }) => {
    reached++;
    expect(url).toBe(`${cfg.collectorUrl}/v1/traces`);
    expect(options.timeout).toBe(1234);
    expect(options.maxRedirects).toBe(0);
    expect(options.data.resourceSpans[0].scopeSpans[0].spans.map(row => row.spanId))
      .toEqual([cfg.rootSpanId, cfg.childSpanId]);
    return { status: () => 401, text: () => { throw new Error('must not read an error body'); } };
  } } as unknown as APIRequestContext;
  const result = await sendTraceResult(req, cfg, 1234);
  expect(reached).toBe(1); // the method actually called is the one patched
  expect(Object.keys(result).sort()).toEqual(['elapsedMs', 'projectName', 'spanIds', 'status', 'traceId']);
  expect(result).toMatchObject({ traceId: cfg.traceId, spanIds: [cfg.rootSpanId, cfg.childSpanId], status: 401 });
  expect(result.elapsedMs).toBeGreaterThanOrEqual(0);
  expect(JSON.stringify(result)).not.toContain(cfg.secretKey);
});

test('offline: transport errors with request details are not exposed', async () => {
  let reached = 0;
  const req = { post: async () => { reached++; throw new Error(cfg.secretKey); } } as unknown as APIRequestContext;
  await expect(sendTraceResult(req, cfg)).rejects.toThrow('Collector transport failed (request details withheld)');
  expect(reached).toBe(1);
  reached = 0;
  await expect(sendTraceResult(req, cfg, 0)).rejects.toThrow('timeout must be positive');
  expect(reached).toBe(0);
});

test('offline: real loopback HTTP exposes refusal and never follows a credentialed redirect', async () => {
  let requests = 0;
  const server = createServer((_req, res) => {
    requests++;
    res.writeHead(requests === 1 ? 401 : 302, { Location: 'http://unroutable.invalid/v1/traces' });
    res.end('synthetic response details');
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('missing fixture port');
  const req = await request.newContext();
  try {
    const target = { ...cfg, collectorUrl: `http://127.0.0.1:${address.port}` };
    expect((await sendTraceResult(req, target)).status).toBe(401);
    expect((await sendTraceResult(req, target)).status).toBe(302);
    await expect(sendTrace(req, target)).rejects.toThrow('collector 302');
    expect(requests).toBe(3);
  } finally {
    await req.dispose();
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  }
});

test('offline: retained fixture requires explicit disposable loopback scope', () => {
  const env = { E2E_RETAINED_CREDENTIAL_FIXTURES: '1', E2E_COMPOSE_PROJECT: 'futureagi-e2e-obs61-unit' };
  expect(() => requireRetainedFixtureTarget(env, ['http://localhost:1', 'postgresql://localhost:2/db'])).not.toThrow();
  for (const change of [{ E2E_RETAINED_CREDENTIAL_FIXTURES: '' }, { E2E_COMPOSE_PROJECT: 'default' },
    { E2E_STACK: 'standalone' }, { DOCKER_HOST: 'tcp://localhost:2375' }, { DOCKER_CONTEXT: 'remote' }]) {
    expect(() => requireRetainedFixtureTarget({ ...env, ...change }, ['http://localhost:1'])).toThrow();
  }
  for (const url of ['http://external.invalid', 'http://localhost.external.invalid', 'https://localhost',
    'http://192.0.2.1', 'http://user@external.invalid']) {
    expect(() => requireRetainedFixtureTarget(env, [url])).toThrow('loopback-only');
  }
});

test('offline: retained fixture response is exact and malformed values stay out of errors', () => {
  const row = () => ({ id: randomUUID(), apiKey: randomBytes(16).toString('hex'),
    secretKey: randomBytes(16).toString('hex') });
  const good = { system: row(), retired: row() };
  // A failed harness assertion must not print even synthetic credential values.
  expect(JSON.stringify(parseRetainedCredentials(JSON.stringify(good))) === JSON.stringify(good)).toBe(true);
  for (const value of [{ ...good, extra: true }, { system: good.system },
    { system: good.system, retired: good.system },
    { ...good, retired: { ...good.retired, secretKey: 'invalid-private-material' } }]) {
    expect(() => parseRetainedCredentials(JSON.stringify(value)))
      .toThrow('Invalid retained fixture result (values withheld)');
  }
});
