import { createServer, request as forward, type IncomingMessage } from 'node:http';
import { connect, type AddressInfo, type Socket } from 'node:net';
import type { Duplex } from 'node:stream';
import { request, type APIRequestContext, type Browser, type BrowserContext, type BrowserContextOptions } from '@playwright/test';

// The walk-mock browser boundary: a browser context, and an API request
// context for the test's own calls, that may reach only the allowlisted
// loopback origins.
//
// A context route alone cannot enforce this: Playwright calls a route handler
// only for the first URL of a redirect chain, so a hop an allowlisted server
// redirects to is sent unchecked. Both contexts therefore use a forward proxy
// on 127.0.0.1 as their only way out. Playwright routes loopback through a
// context proxy too (Chromium gets <-loopback>; its Firefox build sets
// network.proxy.allow_hijacking_localhost), so the proxy sees every hop,
// redirects included, before it is sent. It refuses anything outside the
// allowlist without resolving or dialling it, and it dials nothing but
// 127.0.0.1 at an allowlisted port.
export type Boundary = {
  context: BrowserContext;
  api: APIRequestContext;
  mechanism: string;
  // Destinations (host:port) the enforcement point passed or refused.
  passed: string[];
  refused: string[];
  // Origins outside the allowlist the browser requested, failed on, or got a
  // response from, every redirect hop included.
  forbidden: string[];
  failed: string[];
  answered: string[];
  close(): Promise<void>;
};

const hostPort = (u: URL) => `${u.hostname}:${u.port || (u.protocol === 'https:' || u.protocol === 'wss:' ? '443' : '80')}`;
const HOP_BY_HOP = ['connection', 'keep-alive', 'proxy-connection', 'proxy-authorization', 'proxy-authenticate', 'te', 'trailer', 'upgrade'];

async function startProxy(origins: Set<string>, passed: string[], refused: string[]) {
  // CONNECT names only host:port, not the scheme. APIRequestContext uses it
  // for HTTP too, so permit those allowlisted destinations as well as TLS.
  // Exact host:port matching and loopback-only dialing still apply; plain
  // forward-proxy requests are checked by their full origin below.
  const tunnels = new Set([...origins].map(o => new URL(o)).filter(u => u.protocol === 'http:' || u.protocol === 'https:' || u.protocol === 'wss:').map(hostPort));
  const sockets = new Set<Socket>();
  const server = createServer((req, res) => {
    let target: URL | undefined;
    try { target = new URL(req.url ?? ''); } catch { target = undefined; }
    if (!target || target.protocol !== 'http:' || !origins.has(target.origin)) {
      refused.push(target ? hostPort(target) : `invalid:${req.url}`);
      req.socket.destroy(); // the browser reports a failed request; nothing goes onward
      return;
    }
    passed.push(hostPort(target));
    const headers = { ...req.headers };
    for (const h of HOP_BY_HOP) delete headers[h];
    const out = forward({ host: '127.0.0.1', port: Number(target.port || 80), method: req.method,
      path: target.pathname + target.search, headers, agent: false }, upstream => {
      upstream.on('error', () => res.destroy());
      const back = { ...upstream.headers };
      for (const h of HOP_BY_HOP) delete back[h];
      res.writeHead(upstream.statusCode ?? 502, back);
      upstream.pipe(res);
    });
    out.on('error', () => res.destroy());
    req.pipe(out);
  });
  server.on('connection', (s: Socket) => { sockets.add(s); s.on('close', () => sockets.delete(s)); });
  server.on('connect', (req: IncomingMessage, client: Duplex, head: Buffer) => {
    const target = req.url ?? '';
    let upstream: Socket | undefined;
    client.on('error', () => upstream?.destroy());
    if (!tunnels.has(target)) {
      refused.push(target);
      client.end('HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n');
      return;
    }
    passed.push(target);
    const tunnel = connect(Number(target.slice(target.lastIndexOf(':') + 1)), '127.0.0.1', () => {
      client.write('HTTP/1.1 200 Connection Established\r\n\r\n');
      if (head.length) tunnel.write(head);
      tunnel.pipe(client);
      client.pipe(tunnel);
    });
    upstream = tunnel;
    sockets.add(tunnel);
    tunnel.on('close', () => sockets.delete(tunnel));
    tunnel.on('error', () => client.destroy());
  });
  server.on('upgrade', (req: IncomingMessage, socket: Duplex) => {
    socket.on('error', () => undefined);
    refused.push(`upgrade:${req.url}`);
    socket.destroy();
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  return {
    server: `http://127.0.0.1:${(server.address() as AddressInfo).port}`,
    close: async () => {
      for (const s of sockets) s.destroy();
      await new Promise(resolve => server.close(resolve));
    },
  };
}

export async function openBoundary(browser: Browser, allowed: string[], options: BrowserContextOptions = {}): Promise<Boundary> {
  const origins = new Set(allowed.map(a => new URL(a).origin));
  for (const o of origins) if (new URL(o).hostname !== 'localhost') throw new Error(`walk boundary allows loopback origins only, not ${o}`);
  const outside = (raw: string) => {
    try {
      const origin = new URL(raw).origin;
      return origins.has(origin) ? undefined : origin;
    } catch {
      return raw;
    }
  };
  const passed: string[] = [], refused: string[] = [], forbidden: string[] = [], failed: string[] = [], answered: string[] = [];
  const proxy = await startProxy(origins, passed, refused);
  const context = await browser.newContext({ ...options, proxy: { server: proxy.server } });
  context.on('request', r => { const o = outside(r.url()); if (o) forbidden.push(o); });
  context.on('requestfailed', r => { const o = outside(r.url()); if (o) failed.push(o); });
  context.on('response', r => { const o = outside(r.url()); if (o) answered.push(o); });
  // First hops outside the allowlist are aborted before they reach the proxy.
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (outside(url.href)) { refused.push(hostPort(url)); await route.abort(); } else await route.continue();
  });
  const api = await request.newContext({ ignoreHTTPSErrors: options.ignoreHTTPSErrors, proxy: { server: proxy.server } });
  return {
    context, api, passed, refused, forbidden, failed, answered,
    mechanism: 'loopback forward proxy for every browser and API hop, plus a first-hop context route',
    close: async () => {
      await context.close();
      await api.dispose();
      await proxy.close();
    },
  };
}
