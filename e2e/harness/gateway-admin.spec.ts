import { test, expect } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { createServer, type IncomingMessage, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { GatewayAdmin, GatewayAdminError } from '../lib/gateway-admin';

// GatewayAdmin's own tests. The offline half answers on a loopback stand-in (no stack); the
// live half reads the running stack's gateway.

type Seen = { method: string; url: string; authorization: string | undefined; body: string };

async function standIn(token: string): Promise<{ server: Server; base: string; seen: Seen[] }> {
  const seen: Seen[] = [];
  const server = createServer((req: IncomingMessage, res) => {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      seen.push({ method: req.method!, url: req.url!, authorization: req.headers.authorization, body });
      const reply = (status: number, value: unknown) => {
        res.writeHead(status, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(value));
      };
      if (req.headers.authorization !== `Bearer ${token}`) return reply(401, { error: 'unauthorized' });
      const path = req.url!.split('?')[0];
      if (path === '/-/config') return reply(200, { server: { port: 8080 }, logging: { level: 'info' }, batch: { storage: 'memory' } });
      if (path === '/-/orgs/known/config') return reply(200, req.method === 'PUT' ? { status: 'ok' } : { cache: { enabled: false } });
      if (path === '/-/orgs/missing/config') return reply(404, { error: 'org config not found' });
      if (path.startsWith('/-/batches/')) return reply(req.url!.includes('org_id=org-a') ? 200 : 404, { batch_id: 'b-1' });
      if (path === '/-/keys/k-1') return reply(404, { error: { code: 'key_not_found' } });
      return reply(500, { error: 'unexpected stand-in path' });
    });
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  return { server, base: `http://127.0.0.1:${(server.address() as AddressInfo).port}`, seen };
}

test('gateway admin probe offline: reads, 404 as null, org scoping and the token on every call', async () => {
  const { server, base, seen } = await standIn('t-offline');
  const admin = await GatewayAdmin.open('t-offline', base);
  try {
    expect(Object.keys(await admin.config()).sort()).toEqual(['batch', 'logging', 'server']);
    expect(await admin.orgConfig('known')).toEqual({ cache: { enabled: false } });
    expect(await admin.orgConfig('missing')).toBeNull();
    await expect(admin.orgConfig('other')).rejects.toMatchObject({ status: 500, path: '/-/orgs/other/config' });
    await admin.putOrgConfig('known', { cache: { enabled: true } });
    expect(await admin.batch('b-1', 'org-a')).toEqual({ status: 200, body: { batch_id: 'b-1' } });
    expect((await admin.batch('b-1', 'org-b')).status).toBe(404);
    expect((await admin.cancelBatch('b-1', 'org-a')).status).toBe(200);
    expect((await admin.key('k-1')).status).toBe(404);
    expect(seen.map((s) => `${s.method} ${s.url}`)).toEqual([
      'GET /-/config', 'GET /-/orgs/known/config', 'GET /-/orgs/missing/config', 'GET /-/orgs/other/config',
      'PUT /-/orgs/known/config', 'GET /-/batches/b-1?org_id=org-a', 'GET /-/batches/b-1?org_id=org-b',
      'POST /-/batches/b-1/cancel?org_id=org-a', 'GET /-/keys/k-1',
    ]);
    expect(JSON.parse(seen[4].body)).toEqual({ cache: { enabled: true } });
    expect(new Set(seen.map((s) => s.authorization))).toEqual(new Set(['Bearer t-offline']));
  } finally {
    await admin.dispose();
    server.close();
  }
});

test('gateway admin probe offline: a wrong token is a 401 error that never carries the token', async () => {
  const { server, base } = await standIn('t-right');
  const admin = await GatewayAdmin.open('t-wrong-secret', base);
  try {
    const error = await admin.config().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(GatewayAdminError);
    expect((error as GatewayAdminError).status).toBe(401);
    expect((error as Error).message).not.toContain('t-wrong-secret');
  } finally {
    await admin.dispose();
    server.close();
  }
});

test('gateway admin probe offline: refuses any gateway that is not local', async () => {
  await expect(GatewayAdmin.open('t', 'https://gateway.invalid')).rejects.toThrow('local gateway only');
});

test('gateway admin probe live: the running gateway answers config, an unknown org and a wrong token', async () => {
  const admin = await GatewayAdmin.open();
  const wrong = await GatewayAdmin.open('e2e-not-the-admin-token');
  try {
    const config = await admin.config();
    expect(Object.keys(config)).toEqual(expect.arrayContaining(['server', 'logging', 'batch']));
    expect(await admin.orgConfig(randomUUID())).toBeNull();
    await expect(wrong.config()).rejects.toMatchObject({ status: 401 });
  } finally {
    await admin.dispose();
    await wrong.dispose();
  }
});
