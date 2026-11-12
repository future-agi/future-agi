import { test, expect } from '@playwright/test';
import { createHash } from 'node:crypto';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { openBoundary } from '../lib/browser-boundary';
// This proves clickable synthetic consent only. Browser alias mapping and CA
// trust for product walks are H7b, not claimed by this loopback smoke.
test.skip(process.env.E2E_WALK_MOCKS !== '1', 'cloud mock stack must be declared');
const MOCK_ORIGINS = ['https://localhost:28443', 'https://localhost:28445'];
const listen = (server: Server) =>
  new Promise<number>(resolve => server.listen(0, '127.0.0.1', () => resolve((server.address() as AddressInfo).port)));
const closeServer = (server: Server) => {
  server.closeAllConnections();
  return new Promise(resolve => server.close(resolve));
};
const hosts = (list: string[]) => [...new Set(list.map(h => (h.includes('://') ? new URL(h).hostname : h.replace(/:\d+$/, ''))))].sort();

// Two loopback servers: the allowlisted one redirects, once or after an
// allowlisted hop, to the other under a name outside the allowlist, and to a
// reserved non-local name. Nothing outside the allowlist may be sent.
test('boundary refuses every redirect hop outside the allowlist', async ({ browser }, info) => {
  let delivered = 0;
  const outsider = createServer((_, res) => { delivered++; res.end('outside the walk boundary'); });
  const outsidePort = await listen(outsider);
  let insidePort = 0;
  const inside = createServer((req, res) => {
    const to: Record<string, string> = {
      '/one': `http://127.0.0.1:${outsidePort}/one`,
      '/two': `http://localhost:${insidePort}/hop`,
      '/hop': `http://127.0.0.1:${outsidePort}/two`,
      '/far': 'http://walk-boundary.invalid/far',
    };
    const location = to[req.url ?? ''];
    res.writeHead(location ? 302 : 404, location ? { Location: location } : {});
    res.end();
  });
  insidePort = await listen(inside);
  const boundary = await openBoundary(browser, [`http://localhost:${insidePort}`]);
  try {
    const page = await boundary.context.newPage();
    for (const path of ['/one', '/two', '/far']) await page.goto(`http://localhost:${insidePort}${path}`).catch(() => undefined);
    await expect.poll(() => ({
      delivered, refused: hosts(boundary.refused), failed: hosts(boundary.failed), answered: boundary.answered,
      passedInside: boundary.passed.includes(`localhost:${insidePort}`),
    }), { timeout: 5000 }).toEqual({
      delivered: 0, refused: ['127.0.0.1', 'walk-boundary.invalid'], failed: ['127.0.0.1', 'walk-boundary.invalid'], answered: [],
      passedInside: true,
    });
    await info.attach('walk-boundary-redirects', { contentType: 'application/json', body: JSON.stringify({
      mechanism: boundary.mechanism, delivered, refused: hosts(boundary.refused), forbidden: hosts(boundary.forbidden), productAccepted: false,
    }) });
  } finally {
    await boundary.close();
    await Promise.all([closeServer(outsider), closeServer(inside)]);
  }
});

for (const provider of ['mcp', 'google', 'microsoft']) {
  test(`${provider} consent is clickable, preserves subject and state`, async ({ browser }, info) => {
    const origin = provider === 'mcp' ? 'https://localhost:28443' : 'https://localhost:28445';
    // Every request, redirect hops and the test's own API calls included, goes
    // through the boundary, which refuses anything outside the mock origins.
    const boundary = await openBoundary(browser, MOCK_ORIGINS, { ignoreHTTPSErrors: true });
    try {
      const request = boundary.api;
      const page = await boundary.context.newPage();
      const verifier = 'v'.repeat(48), challenge = createHash('sha256').update(verifier).digest('base64url');
      const redirect = `${origin}/fixture-callback`;
      let client: string, authorize: string;
      if (provider === 'mcp') {
        const registered = await request.post(`${origin}/register`, { data: { redirect_uris: [redirect], token_endpoint_auth_method: 'none' } });
        expect(registered.status()).toBe(201); client = (await registered.json()).client_id; authorize = '/authorize';
      } else {
        client = `e2e-${provider}-client`;
        authorize = provider === 'google' ? '/google/o/oauth2/v2/auth' : '/microsoft/common/oauth2/v2.0/authorize';
      }
      const subject = `browser-${info.project.name}`, state = `state-${provider}-${info.project.name}`;
      const q = new URLSearchParams({ client_id: client, response_type: 'code', redirect_uri: redirect,
        code_challenge: challenge, code_challenge_method: 'S256', login_hint: subject, state, scope: 'mcp' });
      if (provider === 'mcp') q.set('resource', 'https://mcp.e2e-public.test:8443/mcp');
      await page.goto(`${origin}${authorize}?${q}`);
      await expect(page.getByRole('button', { name: 'Approve', exact: true })).toBeVisible();
      await page.getByRole('button', { name: 'Approve', exact: true }).click();
      // The browser follows the 302 to the fixture redirect_uri on the mock's own
      // origin; the landing URL is the authorization response the client gets.
      await page.waitForURL(u => u.origin === origin && u.pathname === '/fixture-callback');
      const callback = new URL(page.url());
      expect(callback.searchParams.get('error')).toBeNull();
      expect(callback.searchParams.get('state')).toBe(state);
      const code = callback.searchParams.get('code');
      expect(code).toBeTruthy();
      const tokenPath = provider === 'mcp' ? '/token' : provider === 'google' ? '/google/token' : '/microsoft/common/oauth2/v2.0/token';
      const token = await request.post(`${origin}${tokenPath}`, { form: { client_id: client, grant_type: 'authorization_code',
        code: code!, code_verifier: verifier, redirect_uri: redirect,
        ...(provider !== 'mcp' ? { client_secret: `e2e-${provider}-secret-not-real` } : {}) } });
      expect(token.status()).toBe(200);
      const access: string = (await token.json()).access_token;
      expect(access).toBeTruthy();
      // The grant carries the login_hint subject chosen on the consent leg.
      if (provider === 'mcp') {
        const revoked = await request.post(`${origin}/revoke`, { form: { client_id: client, token: access, token_type_hint: 'access_token' } });
        expect(revoked.status()).toBe(200);
        const receipts: { client_id: string; subject?: string }[] = (await (await request.get(`${origin}/__control/oauth/received`)).json()).revocations;
        expect(receipts.filter(r => r.client_id === client).map(r => r.subject)).toEqual([subject]);
      } else {
        const userinfo = provider === 'google' ? '/google/userinfo' : '/microsoft/oidc/userinfo';
        const who = await request.get(`${origin}${userinfo}`, { headers: { Authorization: `Bearer ${access}` } });
        expect(who.status()).toBe(200); expect((await who.json()).sub).toBe(subject);
      }
      expect({ refused: boundary.refused, forbidden: boundary.forbidden, failed: boundary.failed, answered: boundary.answered })
        .toEqual({ refused: [], forbidden: [], failed: [], answered: [] });
      expect(boundary.passed).toContain(new URL(origin).host);
      await info.attach('mock-browser-scope', { contentType: 'application/json', body: JSON.stringify({
        origin, provider, subject, loopbackOnly: true, boundary: boundary.mechanism, passed: [...new Set(boundary.passed)].sort(),
        browserTLS: 'ignoreHTTPSErrors for functional smoke only; node smoke verifies CA',
        launchOptions: info.project.use.launchOptions, productAccepted: false,
      }) });
    } finally {
      await boundary.close();
    }
  });
}
