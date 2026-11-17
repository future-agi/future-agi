import { randomBytes, randomUUID, createHash } from 'node:crypto';
import { test, expect } from '@playwright/test';
import { startMcpOAuthFixture } from '../_fixtures/mcp-approvals/server.ts';
import { startNativeCallback } from '../_fixtures/mcp-approvals/native-callback.ts';
import { readApprovalAuditPages, readApprovalAuditDetail, requireApprovalWireContract } from '../_fixtures/mcp-approvals/approval-reader.ts';
import { attachSeededIds } from '../_shared/contract';
import { counterDelta, createCounters } from '../_fixtures/mcp-approvals/counters.ts';
import { createOAuthState } from '../_fixtures/mcp-approvals/oauth-state.ts';

/** Tests authored BEFORE the fixtures. NOT RUN in the authoring lane.
 * These are real loopback HTTP fixture self-tests, NOT product journey rows.
 * No ordinary actor is imported (it auto-reads org keys). No journey-row passed
 * event is emitted. Runner must inventory these separately from W07-W12.
 * Secrets stay in memory: assertions compare booleans, never print token bodies.
 */
const details = { tag: ['@j14-fixture-only'], annotation: [{
  type: 'journey-subassertion', description: JSON.stringify({ packet: 'J14',
    scope: 'LOCAL_FIXTURE_SELF_TEST', satisfiesPacketRow: false, productExecution: false }),
}] };
const verifier = () => randomBytes(32).toString('base64url');
const challenge = (value: string) => createHash('sha256').update(value).digest('base64url');
const form = (body: Record<string, string>) => ({ method: 'POST', redirect: 'manual' as const,
  headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams(body) });

for (const scope of ['A1', 'A2'] as const) {
  test(`J14-FIXTURE native OAuth ${scope}: DCR, consent, callback, rotation and revoke`, details,
    async ({ page }, info) => {
      test.setTimeout(180_000); // Two 60s native-callback UI waits plus setup/exchanges.
      const principal = randomUUID();
      await attachSeededIds(info, 'J14', { userIds: [principal] });
      const callback = await startNativeCallback();
      let fixture: Awaited<ReturnType<typeof startMcpOAuthFixture>> | undefined;
      try {
        fixture = await startMcpOAuthFixture({ principalIds: [principal], redirectUris: [callback.redirectUri] });
        const registration = await fetch(`${fixture.origin}/register`, { method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ redirect_uris: [callback.redirectUri], application_type: 'native' }) });
        expect(registration.status).toBe(201);
        const client = await registration.json();
        const v = verifier();
        const url = new URL('/authorize', fixture.origin);
        for (const [key, value] of Object.entries({ response_type: 'code', client_id: client.client_id,
          redirect_uri: callback.redirectUri, resource: fixture.resource, scope: 'mcp',
          code_challenge: challenge(v), code_challenge_method: 'S256', login_hint: principal })) {
          url.searchParams.set(key, value);
        }
        const attempt = callback.arm(url.href);
        const consent = await page.goto(attempt.startUrl);
        // Fixture-owned oauth-http.ts consent document: allow exactly this
        // registered callback origin, not every loopback port or external host.
        const callbackOrigin = new URL(callback.redirectUri).origin;
        expect(callbackOrigin !== fixture.origin).toBe(true);
        expect(consent?.headers()['content-security-policy']).toBe(
          `default-src 'none'; form-action 'self' ${callbackOrigin}; frame-ancestors 'none'`);
        await page.getByRole('button', { name: 'Approve', exact: true }).click();
        await expect(page.getByRole('heading', { name: 'Fixture callback received', exact: true }))
          .toBeVisible({ timeout: 60_000 }); // One UI first-paint budget, e2e/README.md.
        expect(callback.receipt(attempt.id)).toEqual({ status: 'received', cookieMatched: true,
          stateMatched: true, issuerMatched: true, deliveries: 1 });
        const code = callback.takeCode(attempt.id);
        const tokenForm = { grant_type: 'authorization_code', client_id: client.client_id,
          redirect_uri: callback.redirectUri, code_verifier: v, resource: fixture.resource, code };
        const exchange = await fetch(`${fixture.origin}/token`, form(tokenForm));
        expect(exchange.status).toBe(200);
        const issued = await exchange.json();
        const reusedCode = await fetch(`${fixture.origin}/token`, form(tokenForm));
        expect(reusedCode.status).toBe(400);
        expect((await reusedCode.json()).error).toBe('invalid_grant');
        const rpc = (token: string, method: string, params: object, sid?: string) => fetch(fixture!.resource, {
          method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`,
            ...(sid ? { 'Mcp-Session-Id': sid, 'MCP-Protocol-Version': '2025-03-26' } : {}) },
          body: JSON.stringify({ jsonrpc: '2.0', id: randomUUID(), method, params }),
        });
        const init = await rpc(issued.access_token, 'initialize', { protocolVersion: '2025-03-26', capabilities: {} });
        expect(init.status).toBe(200);
        const sid = init.headers.get('mcp-session-id');
        expect(typeof sid === 'string' && sid.length > 0).toBe(true);
        const marker = `e2e-${randomUUID()}`;
        const call = await rpc(issued.access_token, 'tools/call', { name: 'echo', arguments: { text: marker } }, sid!);
        expect(call.status).toBe(200);
        expect((await call.json()).result.content).toEqual([{ type: 'text', text: `echo ok: ${JSON.stringify({ text: marker })}` }]);
        expect(fixture.counters().filter(row => row.operation === 'tool')).toEqual([
          { principalId: principal, operation: 'tool', tool: 'echo', outcome: 'accepted', count: 1 },
        ]);
        const refreshForm = { grant_type: 'refresh_token', client_id: client.client_id,
          resource: fixture.resource, refresh_token: issued.refresh_token };
        const refreshed = await fetch(`${fixture.origin}/token`, form(refreshForm));
        expect(refreshed.status).toBe(200);
        const next = await refreshed.json();
        expect(next.refresh_token !== issued.refresh_token).toBe(true);
        const replay = await fetch(`${fixture.origin}/token`, form(refreshForm));
        expect(replay.status).toBe(400);
        expect((await replay.json()).error).toBe('invalid_grant');
        const descendant = await fetch(`${fixture.origin}/token`, form({ ...refreshForm, refresh_token: next.refresh_token }));
        expect(descendant.status).toBe(400);
        // Keep this already-invalid-family/idempotent-revoke check, but it is
        // NOT the revocation-effect proof. That uses a distinct live family below.
        const revoke = await fetch(`${fixture.origin}/revoke`, form({ client_id: client.client_id, token: next.refresh_token }));
        expect(revoke.status).toBe(200);
        expect(fixture.counters().filter(row => row.operation === 'revoke')).toEqual([
          { principalId: principal, operation: 'revoke', tool: '', outcome: 'accepted', count: 1 },
        ]);
        const denied = await rpc(next.access_token, 'tools/call', { name: 'echo', arguments: { text: marker } }, sid!);
        expect(denied.status).toBe(401);
        expect(fixture.counters().filter(row => row.operation === 'tool' && row.outcome === 'accepted'))
          .toEqual([{ principalId: principal, operation: 'tool', tool: 'echo', outcome: 'accepted', count: 1 }]);

        // SOL-03: a second native authorization mints an unrelated live family.
        // Refresh it ONCE, prove the resulting access works, then revoke the
        // unused descendant refresh token. No replay or clock change can mask
        // a no-op revoker; both effects are checked independently afterwards.
        const liveAttempt = callback.arm(url.href);
        const liveConsent = await page.goto(liveAttempt.startUrl);
        expect(liveConsent?.headers()['content-security-policy']).toBe(
          `default-src 'none'; form-action 'self' ${callbackOrigin}; frame-ancestors 'none'`);
        await page.getByRole('button', { name: 'Approve', exact: true }).click();
        await expect(page.getByRole('heading', { name: 'Fixture callback received', exact: true }))
          .toBeVisible({ timeout: 60_000 });
        expect(callback.receipt(liveAttempt.id)).toEqual({ status: 'received', cookieMatched: true,
          stateMatched: true, issuerMatched: true, deliveries: 1 });
        const freshExchange = await fetch(`${fixture.origin}/token`, form({ ...tokenForm,
          code: callback.takeCode(liveAttempt.id) }));
        expect(freshExchange.status).toBe(200);
        const fresh = await freshExchange.json();
        const liveRefresh = await fetch(`${fixture.origin}/token`, form({ ...refreshForm,
          refresh_token: fresh.refresh_token }));
        expect(liveRefresh.status).toBe(200);
        const live = await liveRefresh.json();
        expect(live.refresh_token !== fresh.refresh_token && live.refresh_token !== next.refresh_token).toBe(true);
        const liveArgs = { name: 'echo', arguments: { text: `e2e-live-revoke-${randomUUID()}` } };
        const liveCall = await rpc(live.access_token, 'tools/call', liveArgs, sid!);
        expect(liveCall.status).toBe(200);
        expect((await liveCall.json()).result.content).toEqual([
          { type: 'text', text: `echo ok: ${JSON.stringify(liveArgs.arguments)}` },
        ]);
        const beforeLiveRevoke = fixture.counters();
        const liveRevoke = await fetch(`${fixture.origin}/revoke`, form({ client_id: client.client_id,
          token: live.refresh_token }));
        expect(liveRevoke.status).toBe(200);
        expect(await liveRevoke.json()).toEqual({});
        expect(counterDelta(beforeLiveRevoke, fixture.counters())).toEqual([
          { principalId: principal, operation: 'revoke', tool: '', outcome: 'accepted', count: 1 },
          { principalId: null, operation: 'arrival', tool: '', outcome: 'received', count: 1 },
        ]);
        expect((await rpc(live.access_token, 'tools/call', liveArgs, sid!)).status).toBe(401);
        const revokedRefresh = await fetch(`${fixture.origin}/token`, form({ ...refreshForm,
          refresh_token: live.refresh_token }));
        expect(revokedRefresh.status).toBe(400);
        expect((await revokedRefresh.json()).error).toBe('invalid_grant');
        expect(fixture.counters().filter(row => row.operation === 'tool' && row.outcome === 'accepted'))
          .toEqual([{ principalId: principal, operation: 'tool', tool: 'echo', outcome: 'accepted', count: 2 }]);
        expect(fixture.counters().filter(row => row.operation === 'revoke')).toEqual([
          { principalId: principal, operation: 'revoke', tool: '', outcome: 'accepted', count: 2 },
        ]);
      } finally {
        await fixture?.close();
        await callback.close();
      }
    });
}

test('J14-FIXTURE rejects nonlocal DCR redirect before any redirect or grant', details, async () => {
  const fixture = await startMcpOAuthFixture({ principalIds: [randomUUID()], redirectUris: ['http://127.0.0.1:12345/callback'] });
  try {
    const response = await fetch(`${fixture.origin}/register`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // Data-only invalid target: NEVER navigated, fetched or resolved.
      body: JSON.stringify({ redirect_uris: ['https://nonlocal.invalid/callback'], application_type: 'native' }) });
    expect(response.status).toBe(400);
    expect((await response.json()).error).toBe('invalid_redirect_uri');
    expect(fixture.counters()).toEqual([
      { principalId: null, operation: 'arrival', tool: '', outcome: 'received', count: 1 },
      { principalId: null, operation: 'register', tool: '', outcome: 'refused', count: 1 },
    ]);
  } finally { await fixture.close(); }
});

test('J14-FIXTURE native receiver refuses wrong cookie, wrong state and replay', details, async () => {
  const callback = await startNativeCallback();
  try {
    const auth = new URL('http://127.0.0.1:12345/authorize');
    const attempt = callback.arm(auth.href);
    const started = await fetch(attempt.startUrl, { redirect: 'manual' });
    expect(started.status).toBe(302);
    const location = new URL(started.headers.get('location')!);
    const cookie = started.headers.get('set-cookie')!.split(';')[0];
    const target = new URL(callback.redirectUri);
    target.search = new URLSearchParams({ state: location.searchParams.get('state')!, iss: auth.origin,
      code: verifier() }).toString();
    expect((await fetch(target, { redirect: 'manual' })).status).toBe(400);
    const wrong = new URL(target); wrong.searchParams.set('state', verifier());
    expect((await fetch(wrong, { headers: { Cookie: cookie }, redirect: 'manual' })).status).toBe(400);
    const wrongIssuer = new URL(target); wrongIssuer.searchParams.set('iss', 'http://127.0.0.1:12346');
    expect((await fetch(wrongIssuer, { headers: { Cookie: cookie }, redirect: 'manual' })).status).toBe(400);
    expect((await fetch(target, { headers: { Cookie: cookie }, redirect: 'manual' })).status).toBe(200);
    expect((await fetch(target, { headers: { Cookie: cookie }, redirect: 'manual' })).status).toBe(400);
    expect(callback.receipt(attempt.id)).toEqual({ status: 'received', cookieMatched: true,
      stateMatched: true, issuerMatched: true, deliveries: 1 });
  } finally { await callback.close(); }
});

test('J14-FIXTURE principal isolation, revoke and duplicate calls stay visible', details, async ({}, info) => {
  const a1 = randomUUID(), a2 = randomUUID();
  await attachSeededIds(info, 'J14', { userIds: [a1, a2] });
  const fixture = await startMcpOAuthFixture({ principalIds: [a1, a2], redirectUris: ['http://127.0.0.1:12345/callback'] });
  try {
    const t1 = fixture.issueBearer(a1), t2 = fixture.issueBearer(a2);
    const rpc = (token: string, method: string, params: object, sid?: string) => fetch(fixture.resource, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`,
        ...(sid ? { 'Mcp-Session-Id': sid } : {}) },
      body: JSON.stringify({ jsonrpc: '2.0', id: 'same-fixture-rpc-id', method, params }),
    });
    const init1 = await rpc(t1, 'initialize', { protocolVersion: '2025-03-26' });
    const init2 = await rpc(t2, 'initialize', { protocolVersion: '2025-03-26' });
    expect([init1.status, init2.status]).toEqual([200, 200]);
    const s1 = init1.headers.get('mcp-session-id')!, s2 = init2.headers.get('mcp-session-id')!;
    const listed = await rpc(t1, 'tools/list', {}, s1);
    expect((await listed.json()).result.tools.map((tool: { name: string }) => tool.name))
      .toEqual(['echo', 'get_weather', 'delete_file']);
    const before = fixture.counters();
    const args = { name: 'delete_file', arguments: { path: `synthetic-${randomUUID()}` } };
    // Same RPC ID twice must count TWICE. This is target instrumentation, not a
    // pretend implementation of the product's absent one-use approvals.
    const copies = await Promise.all([rpc(t1, 'tools/call', args, s1), rpc(t1, 'tools/call', args, s1)]);
    expect(copies.map(response => response.status)).toEqual([200, 200]);
    expect((await rpc(t2, 'tools/call', args, s1)).status).toBe(404);
    fixture.revokeBearer(t1);
    expect((await rpc(t1, 'tools/call', args, s1)).status).toBe(401);
    expect((await rpc(t2, 'tools/call', { name: 'echo', arguments: { text: `e2e-${randomUUID()}` } }, s2)).status).toBe(200);
    const order = (rows: ReturnType<typeof fixture.counters>) => [...rows].sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
    expect(order(counterDelta(before, fixture.counters()))).toEqual(order([
      { principalId: a1, operation: 'arrival', tool: '', outcome: 'received', count: 3 },
      { principalId: a2, operation: 'arrival', tool: '', outcome: 'received', count: 2 },
      { principalId: a1, operation: 'tool', tool: 'delete_file', outcome: 'accepted', count: 2 },
      { principalId: a1, operation: 'transport', tool: '', outcome: 'refused', count: 1 },
      { principalId: a2, operation: 'transport', tool: '', outcome: 'refused', count: 1 },
      { principalId: a2, operation: 'tool', tool: 'echo', outcome: 'accepted', count: 1 },
    ]));
    expect(() => counterDelta(fixture.counters(), before)).toThrow('Counter census regressed');
    const malformedBefore = fixture.counters();
    const malformed = await fetch(fixture.resource, { method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${t2}` }, body: '{' });
    expect(malformed.status).toBe(400);
    expect(counterDelta(malformedBefore, fixture.counters())).toEqual([
      { principalId: a2, operation: 'arrival', tool: '', outcome: 'received', count: 1 },
    ]);
  } finally { await fixture.close(); }
});

test('J14-FIXTURE OAuth clock, PKCE, foreign client and concurrent redemption controls', details, async () => {
  const principal = randomUUID(), redirect = 'http://127.0.0.1:12345/callback';
  let now = 0;
  const counters = createCounters([principal]);
  const state = createOAuthState(counters, [redirect], () => now);
  const resource = 'http://127.0.0.1:12346/mcp'; state.bindResource(resource);
  const register = () => String(state.register({ redirect_uris: [redirect], application_type: 'native' }).body.client_id);
  const client = register(), other = register(), v = verifier();
  const authorize = () => {
    const grant = state.validateAuthorization({ client_id: client, redirect_uri: redirect, response_type: 'code',
      code_challenge_method: 'S256', code_challenge: challenge(v), state: verifier(), resource,
      scope: 'mcp', login_hint: principal });
    expect(grant !== null).toBe(true);
    return state.authorize(grant!, true)!;
  };
  const exchange = (code: string, cv = v) => state.token({ grant_type: 'authorization_code',
    client_id: client, redirect_uri: redirect, resource, code_verifier: cv, code });
  const expired = authorize(); now = 600_000;
  expect(exchange(expired).body.error).toBe('invalid_grant');
  const badPkce = authorize();
  expect(exchange(badPkce, verifier()).body.error).toBe('invalid_grant');
  expect(exchange(badPkce).body.error).toBe('invalid_grant');
  const issued = exchange(authorize()); expect(issued.status).toBe(200);
  const refresh = { grant_type: 'refresh_token', client_id: client, resource, refresh_token: String(issued.body.refresh_token) };
  expect(state.token({ ...refresh, client_id: other }).body.error).toBe('invalid_grant');
  expect(state.token({ ...refresh, resource: 'http://127.0.0.1:12347/mcp' }).body.error).toBe('invalid_target');
  // Dispatches in the same event-loop turn; the synchronous consume transition
  // must admit exactly one and then revoke the family on replay.
  const pair = await Promise.all([Promise.resolve().then(() => state.token(refresh)), Promise.resolve().then(() => state.token(refresh))]);
  expect(pair.map(value => value.status)).toEqual([200, 400]);
  expect(state.token({ ...refresh, refresh_token: String(pair[0].body.refresh_token) }).body.error).toBe('invalid_grant');
  const fresh = exchange(authorize()); expect(fresh.status).toBe(200);
  expect(state.principal(String(fresh.body.access_token)).valid).toBe(true);
  now += 900_000;
  expect(state.principal(String(fresh.body.access_token)).valid).toBe(false);
  state.dispose();
});

test('J14-FIXTURE approval API contract is explicitly unavailable at prep pin', details, async () => {
  for (const operation of ['decision', 'list', 'detail', 'expiry', 'one-use', 'args-binding', 'rate-limit']) {
    expect(() => requireApprovalWireContract(operation)).toThrow('OWED_F11_PRODUCT_CONTRACT');
  }
});

test('J14-FIXTURE audit pagination retains every row and tenant/run bindings', details, async () => {
  const org = randomUUID(), run = randomUUID();
  const rows = ['pending', 'approved', 'denied', 'expired', 'used'].map((status, index) => ({
    approval_id: `approval-${index}`, virtual_server_id: 'fixture-vs', tool: 'delete_file',
    args_hash: 'a'.repeat(64), agent_run_id: run, api_key_id: 'fixture-personal-key', status,
    requested_at: '2026-01-01 00:00:00+00', decided_at: null, decided_by_id: null, expires_at: null,
  }));
  const seen: unknown[][] = [];
  // SQL reader unit fixture only: this is not native pagination/API evidence.
  const probe = { async pg<T>(sql: string, values: unknown[]) {
    expect(sql.startsWith('SELECT ')).toBe(true); seen.push(values);
    expect(values.slice(0, 2)).toEqual([org, run]);
    return rows.filter(row => values[2] === null || row.approval_id > String(values[2])).slice(0, Number(values[3])) as T[];
  } };
  expect(await readApprovalAuditPages(probe, org, run, { pageSize: 2, maxPages: 4 })).toEqual(rows);
  expect(seen).toHaveLength(3);
  await expect(readApprovalAuditPages(probe, org, run, { pageSize: 1, maxPages: 2 }))
    .rejects.toThrow('Approval audit page budget exhausted');
  const duplicate = { async pg<T>() { return [rows[0], rows[0]] as T[]; } };
  await expect(readApprovalAuditPages(duplicate, org, run, { pageSize: 2, maxPages: 2 }))
    .rejects.toThrow('Approval audit page repeated or regressed');
  const malformed = { async pg<T>() { return [{ ...rows[0], status: 'unknown-status' }] as T[]; } };
  await expect(readApprovalAuditPages(malformed, org, run, { pageSize: 2, maxPages: 2 }))
    .rejects.toThrow('Invalid approval audit projection');
  const detail = { async pg<T>(_sql: string, values: unknown[]) {
    expect(values).toEqual([org, run, rows[0].approval_id]); return [rows[0]] as T[];
  } };
  expect(await readApprovalAuditDetail(detail, org, run, rows[0].approval_id)).toEqual(rows[0]);
  const foreign = { async pg<T>() { return [] as T[]; } };
  expect(await readApprovalAuditDetail(foreign, randomUUID(), run, rows[0].approval_id)).toBeNull();
  // Data-only query construction checks. Not product list/role/404 coverage.
});
