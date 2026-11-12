// The trace-hygiene fixture flows, shared by trace-hygiene.chromium.spec.ts,
// trace-hygiene.firefox.spec.ts, trace-hygiene-offline.spec.ts and trace-hygiene-controls.spec.ts.
// Playwright 1.62 refuses worker options (browserName, trace) inside a describe group,
// so each spec file sets them at its top level and declares its tests through here.
//
// What put credential values into lane traces, with tracing ON: ScopeActors' public
// invitation acceptance (GET + POST accept-invitation with new_password/repeat_password),
// a UI login that fills a password field, a signed-in browser whose app requests carry
// the bearer, and key-bearing API calls (GET /accounts/keys/, then an X-Api-Key and
// X-Secret-Key request). The paired test runs the INDEPENDENT auditor
// (e2e/scripts/evidence-audit/) over the trace the first one produced: it must carry
// zero findings. Failure output is value-free (kind, entry, offset, layer, field name).
import { randomBytes } from 'node:crypto';
import { existsSync } from 'node:fs';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import path from 'node:path';
import type { APIRequestContext, BrowserContext, Route } from '@playwright/test';
import { test, expect } from '../lib/scope-actors';
import { E2E } from '../lib/env';
import { auditPaths } from '../scripts/evidence-audit/audit.mjs';

const LOGIN = '/auth/jwt/login'; // flows/auth/login.spec.ts
const UI_READY = 60_000;

export const b64url = (o: unknown) => Buffer.from(JSON.stringify(o)).toString('base64url');
export const syntheticJwt = () =>
  `${b64url({ alg: 'HS256', typ: 'JWT' })}.${b64url({ user_id: randomBytes(8).toString('hex') })}.` +
  randomBytes(32).toString('base64url');
export const findings = (report: ReturnType<typeof auditPaths>) =>
  report.hits.map((h) => `${h.kind} ${h.entry} @${h.offset} ${h.layer} name=${h.name} x${h.count}`);

// The recipe's browser boundary: every non-local request is aborted and its origin recorded.
const nonLocal = (url: URL) => !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
const boundary = (aborted: Set<string>) => async (context: BrowserContext) => {
  await context.route(nonLocal, (route: Route) => {
    aborted.add(new URL(route.request().url()).origin);
    return route.abort();
  });
};

/**
 * The same leak classes without the stack, on a traced request context against a
 * loopback stand-in (127.0.0.1 only): an invitation password POSTed to an
 * accept-invitation path carrying its token, a JWT pair and a session cookie in the
 * response, then a bearer and X-Api-Key/X-Secret-Key. `minted` receives every value
 * the test itself generates.
 */
export async function loopbackCredentialCalls(api: APIRequestContext,
  minted: (...values: string[]) => void = () => undefined): Promise<void> {
  const server = createServer((req, res) => {
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('Set-Cookie', `sessionid=${randomBytes(24).toString('base64url')}; HttpOnly`);
    res.end(JSON.stringify(req.url?.includes('accept-invitation')
      ? { access: syntheticJwt(), refresh: syntheticJwt() } : { status: true, result: [] }));
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  try {
    const password = `Pw-${randomBytes(6).toString('hex')}!`;
    const invite = `${randomBytes(3).toString('hex')}-${randomBytes(16).toString('hex')}`;
    const keys = [randomBytes(16).toString('hex'), randomBytes(16).toString('hex')];
    minted(password, invite, ...keys);
    const accepted = await api.post(`${base}/accounts/accept-invitation/${b64url('user')}/${invite}/`, {
      data: { new_password: password, repeat_password: password } });
    expect(accepted.status()).toBe(200);
    const { access, refresh } = await accepted.json();
    minted(access, refresh);
    const keyed = await api.get(`${base}/sdk/api/v1/get-evals/`, { headers: { Authorization: `Bearer ${access}`,
      'X-Api-Key': keys[0], 'X-Secret-Key': keys[1] } });
    expect(keyed.status()).toBe(200);
  } finally {
    server.close();
  }
}

/** The audit half of a pair: the trace at `tracePath` passes the independent auditor. */
export function expectCleanTrace(tracePath: string, minEntries: number): void {
  expect(existsSync(tracePath), 'the traced test wrote trace.zip').toBe(true);
  const report = auditPaths([tracePath], { root: path.dirname(tracePath) });
  expect(report.scanned.archiveEntries).toBeGreaterThan(minEntries);
  expect(report.problems).toEqual([]);
  expect(findings(report)).toEqual([]);
}

/** The live pair for one browser (the spec file sets browserName and trace: 'on'). */
export function liveTraceHygiene(browserName: 'chromium' | 'firefox'): void {
  test.describe.configure({ mode: 'serial' });
  let tracePath = '';

  test(`live: invitation acceptance, UI login and key-bearing calls are traced (${browserName})`,
    async ({ browser, request, scopeActors }, testInfo) => {
      test.setTimeout(360_000);
      tracePath = testInfo.outputPath('trace.zip');
      // 1. Provisioning accepted the member and viewer invitations over the public API.
      expect(scopeActors.receipts.filter((r) => r.method === 'POST' && r.status === 200 &&
        r.path.startsWith('/accounts/accept-invitation/')).length).toBeGreaterThanOrEqual(2);

      // 2. A UI login: the password goes into a password field and a token request.
      const owner = scopeActors.owners[0];
      const aborted = new Set<string>();
      const anon = await browser.newContext({ baseURL: E2E.appUrl, serviceWorkers: 'block' });
      await boundary(aborted)(anon);
      const page = await anon.newPage();
      page.setDefaultTimeout(UI_READY);
      await page.goto(LOGIN);
      await page.getByLabel(/email/i).fill(owner.email);
      await page.getByLabel(/password/i).fill(owner.password);
      const token = page.waitForResponse((r) => new URL(r.url()).pathname === '/accounts/token/' &&
        r.request().method() === 'POST');
      await page.getByRole('button', { name: 'Continue' }).click();
      expect((await token).status()).toBe(200);
      await expect(page).not.toHaveURL(/\/auth\/jwt\/login/, { timeout: UI_READY });
      await anon.close();

      // 3. A signed-in invited member: the app's own API requests carry the bearer.
      const member = await scopeActors.openContext(browser, scopeActors.member,
        { options: { serviceWorkers: 'block' }, guard: boundary(aborted) });
      const memberPage = await member.newPage();
      const authed = memberPage.waitForRequest((r) => r.url().startsWith(E2E.apiUrl) &&
        Boolean(r.headers()['authorization']), { timeout: UI_READY });
      await memberPage.goto('/dashboard/develop');
      await authed;
      await member.close();

      // 4. Key-bearing API calls: the org keys, then an API-key-authenticated read
      // (sdk/views/evaluations.py GetEvalsView, APIKeyAuthentication).
      const keys = await scopeActors.send<{ data: { api_key: string; secret_key: string } }>(
        scopeActors.ownerA, 'GET', '/accounts/keys/');
      expect(keys.status).toBe(200);
      const evals = await request.get(`${E2E.apiUrl}/sdk/api/v1/get-evals/`, {
        headers: { 'X-Api-Key': keys.body.data.api_key, 'X-Secret-Key': keys.body.data.secret_key } });
      expect(evals.status()).toBe(200);
      await testInfo.attach('aborted-origins', { contentType: 'application/json', body: JSON.stringify([...aborted].sort()) });
    });

  test(`live: the trace it produced carries zero detector findings (${browserName})`, async () => {
    expectCleanTrace(tracePath, 3);
  });
}
