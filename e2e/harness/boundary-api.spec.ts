import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer as httpServer, type RequestListener, type Server } from 'node:http';
import { createServer as httpsServer } from 'node:https';
import type { AddressInfo } from 'node:net';
import { openBoundary } from '../lib/browser-boundary';

const listen = (server: Server) =>
  new Promise<number>(resolve => server.listen(0, '127.0.0.1', () => resolve((server.address() as AddressInfo).port)));
const close = (server: Server) => {
  server.closeAllConnections();
  return new Promise(resolve => server.close(resolve));
};

for (const scheme of ['http', 'https']) {
  test(`boundary allows ${scheme} API calls and refuses every unlisted destination`, async ({ browser }, info) => {
    let tls: { key: Buffer; cert: Buffer } | undefined;
    if (scheme === 'https') {
      // Ephemeral synthetic TLS material, never a product credential or CA-trust proof. A test
      // input: under the OS temp directory, not the audited output directory (708 add.78).
      const dir = mkdtempSync(join(tmpdir(), 'e2e-boundary-tls-'));
      const key = join(dir, 'fixture-key.pem'), cert = join(dir, 'fixture-cert.pem');
      execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
        '-keyout', key, '-out', cert, '-subj', '/CN=walk boundary fixture',
        '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1'], { stdio: 'ignore' });
      tls = { key: readFileSync(key), cert: readFileSync(cert) };
    }
    const create = (handler: RequestListener): Server => tls ? httpsServer(tls, handler) : httpServer(handler);
    let outsideConnections = 0, outsideRequests = 0;
    const outsider = create((_, res) => { outsideRequests++; res.end('outside'); });
    outsider.on('connection', () => outsideConnections++);
    const outsidePort = await listen(outsider);
    let origin = '';
    const destinations: Record<string, string> = {};
    const inside = create((req, res) => {
      if (req.url === '/ok') { res.end('inside'); return; }
      const target = destinations[req.url ?? ''];
      res.writeHead(target ? 302 : 404, target ? { Location: target } : {});
      res.end();
    });
    const insidePort = await listen(inside);
    origin = `${scheme}://localhost:${insidePort}`;
    Object.assign(destinations, {
      '/alias': `${scheme}://127.0.0.1:${outsidePort}/outside`,
      '/port': `${scheme}://localhost:${outsidePort}/outside`,
      '/chain': `${origin}/alias`,
      '/reserved': `${scheme}://walk-boundary.invalid/outside`,
    });
    const boundary = await openBoundary(browser, [origin], { ignoreHTTPSErrors: true });
    try {
      const page = await boundary.context.newPage();
      await page.goto(`${origin}/ok`);
      expect(await page.textContent('body')).toBe('inside');
      // APIRequestContext uses CONNECT for HTTP as well as HTTPS.
      const allowed = await boundary.api.get(`${origin}/ok`);
      expect(allowed.status()).toBe(200);
      expect(await allowed.text()).toBe('inside');
      expect(boundary.refused).toEqual([]);
      expect(boundary.passed).toContain(`localhost:${insidePort}`);
      for (const [path, target] of Object.entries(destinations)) {
        const blocked = new URL(path === '/chain' ? destinations['/alias'] : target);
        const host = `${blocked.hostname}:${blocked.port || (scheme === 'https' ? 443 : 80)}`;
        for (const client of ['browser', 'api']) {
          const before = boundary.refused.length;
          if (client === 'browser') {
            // Isolate failed navigations: Firefox may still be loading the
            // previous page's proxy-error document when goto rejects.
            const redirectPage = await boundary.context.newPage();
            try { await redirectPage.goto(`${origin}${path}`).catch(() => undefined); }
            finally { await redirectPage.close(); }
          } else await boundary.api.get(`${origin}${path}`, { timeout: 5000 }).catch(() => undefined);
          await expect.poll(() => boundary.refused.slice(before), { message: `${client} redirect ${path}` }).toContain(host);
          expect({ outsideConnections, outsideRequests }).toEqual({ outsideConnections: 0, outsideRequests: 0 });
        }
      }
      // Unlike a browser's first-hop route, API calls must be refused by the proxy itself.
      for (const target of [...Object.values(destinations).filter(u => !u.startsWith(origin)),
        `${scheme}://127.0.0.1:${insidePort}/ok`]) {
        const before = boundary.refused.length, url = new URL(target);
        await boundary.api.get(target, { timeout: 5000 }).catch(() => undefined);
        expect(boundary.refused.slice(before)).toContain(`${url.hostname}:${url.port || (scheme === 'https' ? 443 : 80)}`);
      }
      expect({ outsideConnections, outsideRequests, answered: boundary.answered })
        .toEqual({ outsideConnections: 0, outsideRequests: 0, answered: [] });
      expect(boundary.passed.every(host => host === `localhost:${insidePort}`)).toBe(true);
      await info.attach('boundary-api', { contentType: 'application/json', body: JSON.stringify({
        scheme, allowedAPIStatus: allowed.status(), outsideConnections, outsideRequests,
        passed: [...new Set(boundary.passed)], refused: [...new Set(boundary.refused)], productAccepted: false,
      }) });
    } finally {
      await boundary.close();
      await Promise.all([close(inside), close(outsider)]);
    }
  });
}
