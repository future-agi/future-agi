import { randomUUID } from 'node:crypto';
import type { Page } from '@playwright/test';
import { test, expect, type ScopeActor, type ScopeActors } from '../lib/scope-actors';
import { E2E } from '../lib/env';
import { AttachmentMediaDelivery, MEDIA_EXCHANGE, MEDIA_SESSION_HEADER, mediaResolve,
  exerciseAppRefresh, type MediaOperation } from '../lib/attachment-media-delivery';

// These self-tests need the real Distributed HTTPS frontend/backend. They must
// remain NOT_RUN until the managed-stack owner hands execution over. No HTTP
// server, fabricated cookie or hand-built token stands in for this qualification.
test.skip(process.env.E2E_STACK === 'standalone', 'Actual attachment media HTTPS issuance is Distributed-only');
test.use({ trace: 'off', video: 'off' }); // Tokens/cookies are inspected only in memory.
const UI_READY = 60_000;
const MUTATE_DELIVERY = process.env.E2E_H3_DELIVER_A_FIRST === '1';

async function openApp(page: Page) {
  const user = page.waitForResponse(response => new URL(response.url()).pathname === '/accounts/user-info/',
    { timeout: UI_READY });
  await page.goto('/dashboard/observe', { timeout: UI_READY });
  expect((await user).status()).toBe(200);
  await expect(page.getByText('Welcome to Observe', { exact: true })).toBeVisible({ timeout: UI_READY });
}

const identity = (actor: ScopeActor, mediaSessionId?: string) => ({
  authorization: `Bearer ${actor.tokens.access}`, organizationId: actor.organizationId,
  workspaceId: actor.workspaceId, ...(mediaSessionId ? { mediaSessionId } : {}),
});

/** Browser HTTP probe of the delivery harness, NOT a second app auth client.
 * It only reads the existing signed-in app state; no refresh/install logic.
 * Media requests are real, with the browser supplying Origin and cookies.
 */
function browserPost(page: Page, path: string, body: unknown, sessionId?: string) {
  const pending = page.evaluate(async ({ path, body, sessionId, sessionHeader }) => {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${localStorage.getItem('accessToken')}`,
      'X-Organization-Id': sessionStorage.getItem('organizationId')!,
      'X-Workspace-Id': sessionStorage.getItem('workspaceId')!,
    };
    if (sessionId) headers[sessionHeader] = sessionId;
    const response = await fetch(path, { method: 'POST', headers, body: JSON.stringify(body), credentials: 'same-origin' });
    const text = await response.text();
    return { status: response.status, sessionId: response.headers.get(sessionHeader),
      body: text.length === 0 ? null : JSON.parse(text) };
  }, { path, body, sessionId, sessionHeader: MEDIA_SESSION_HEADER });
  void pending.catch(() => {}); // Preserve the primary barrier failure during cleanup.
  return pending;
}

async function resolveCase(scopes: ScopeActors) {
  // Source pins: ProjectSerializer; e2e_attachment_scope.py; F01 ResolveRequest.
  // Real not_found response qualifies response forwarding, NOT ready-grant or
  // media playback. Those checks belong to H0/H2 and dependent catalog flows.
  const created = await scopes.send<{ result: { project_id: string } }>(scopes.ownerA, 'POST', '/tracer/project/',
    { name: `${scopes.prefix}-delivery`, trace_type: 'observe', model_type: 'GenerativeLLM' });
  expect(created.status).toBe(200);
  const projectId = created.body.result.project_id;
  const optIn = await scopes.send(scopes.ownerA, 'POST', '/tracer/e2e/attachment-scope/', { project_id: projectId });
  expect(optIn.status).toBe(200);
  const occurrenceId = randomUUID();
  return { projectId, occurrenceId, body: { items: [{ occurrence_id: occurrenceId,
    parent: { trace_id: randomUUID().replaceAll('-', ''), span_id: randomUUID().replaceAll('-', '').slice(0, 16) },
    representation: 'original' }] } };
}

test('H3 real media: backend A before B, browser B before A, cookie installation only at delivery',
  async ({ browser, scopeActors: scopes }, testInfo) => {
    test.setTimeout(300_000); // 2*DELIVERY_READY + 2*UI_READY + 60s setup/headroom.
    const context = await scopes.openContext(browser, scopes.ownerA,
      { appUrl: E2E.attachmentsAppUrl, ignoreHTTPSErrors: true });
    const barrier = new AttachmentMediaDelivery(context, E2E.attachmentsAppUrl, true);
    try {
      const pageA = await context.newPage(), pageB = await context.newPage();
      await test.step('Open A app', () => openApp(pageA), { timeout: UI_READY });
      await test.step('Open B app', () => openApp(pageB), { timeout: UI_READY });
      const a = await barrier.holdNext(pageA, 'A', { kind: 'exchange' }, identity(scopes.ownerA));
      const aRequest = browserPost(pageA, MEDIA_EXCHANGE, {});
      await a.ready();
      await a.assertHeldCookieAbsent();
      const b = await barrier.holdNext(pageB, 'B', { kind: 'exchange' }, identity(scopes.ownerA), a);
      const bRequest = browserPost(pageB, MEDIA_EXCHANGE, {});
      await b.ready();
      expect(a.sessionId).not.toBe(b.sessionId);
      await a.assertHeldCookieAbsent(); await b.assertHeldCookieAbsent();
      if (MUTATE_DELIVERY) {
        // One-sided fallibility: unchanged order assertion below must fail.
        await a.release(); await b.release();
      } else {
        await b.release();
        await a.assertHeldCookieAbsent();
        const bPath = `/tracer/v1/attachment-media/${b.sessionId}/`;
        const bValue = (await context.cookies()).find(cookie => cookie.path === bPath)?.value;
        expect(Boolean(bValue)).toBe(true);
        await a.release();
        expect((await context.cookies()).find(cookie => cookie.path === bPath)?.value === bValue).toBe(true);
      }
      barrier.assertReversed(a, b);
      const [ar, br] = await Promise.all([aRequest, bRequest]);
      expect(ar).toEqual({ status: 204, sessionId: a.sessionId, body: null });
      expect(br).toEqual({ status: 204, sessionId: b.sessionId, body: null });
      const cookies = (await context.cookies()).filter(cookie => cookie.name === 'fi_media_session_v2');
      expect(cookies.map(cookie => ({ domain: cookie.domain, path: cookie.path,
        secure: cookie.secure, httpOnly: cookie.httpOnly, sameSite: cookie.sameSite }))
        .sort((x, y) => x.path.localeCompare(y.path))).toEqual([a, b].map(op => ({
          domain: 'localhost', path: `/tracer/v1/attachment-media/${op.sessionId}/`,
          secure: true, httpOnly: true, sameSite: 'Strict',
        })).sort((x, y) => x.path.localeCompare(y.path)));
      await testInfo.attach('h3-real-delivery-order', { contentType: 'application/json', body: JSON.stringify(barrier.evidence()) });
    } finally { await barrier.dispose(); await context.close(); }
  });

test('H3 real resolve: operation-affine handles and unchanged real denial bodies in reverse delivery',
  async ({ browser, scopeActors: scopes }, testInfo) => {
    test.setTimeout(300_000);
    const context = await scopes.openContext(browser, scopes.ownerA,
      { appUrl: E2E.attachmentsAppUrl, ignoreHTTPSErrors: true });
    const barrier = new AttachmentMediaDelivery(context, E2E.attachmentsAppUrl, true);
    try {
      const pageA = await context.newPage(), pageB = await context.newPage();
      await test.step('Open A app', () => openApp(pageA), { timeout: UI_READY });
      await test.step('Open B app', () => openApp(pageB), { timeout: UI_READY });
      const sessions = [];
      for (const page of [pageA, pageB]) {
        const response = await browserPost(page, MEDIA_EXCHANGE, {});
        expect(response.status).toBe(204); expect(response.sessionId).toBeTruthy();
        sessions.push(response.sessionId!);
      }
      const fixture = await resolveCase(scopes);
      const operation: MediaOperation = { kind: 'resolve', projectId: fixture.projectId };
      const a = await barrier.holdNext(pageA, 'A', operation, identity(scopes.ownerA, sessions[0]));
      const ar = browserPost(pageA, mediaResolve(fixture.projectId), fixture.body, sessions[0]);
      await a.ready();
      const b = await barrier.holdNext(pageB, 'B', operation, identity(scopes.ownerA, sessions[1]), a);
      const br = browserPost(pageB, mediaResolve(fixture.projectId), fixture.body, sessions[1]);
      await b.ready();
      if (MUTATE_DELIVERY) { await a.release(); await b.release(); }
      else { await b.release(); await a.release(); }
      barrier.assertReversed(a, b);
      for (const response of await Promise.all([ar, br])) {
        expect(response.status).toBe(200);
        const items = response.body.items as { occurrence_id: string; status: string }[];
        expect(items.map(({ occurrence_id, status }) => ({ occurrence_id, status })))
          .toEqual([{ occurrence_id: fixture.occurrenceId, status: 'not_found' }]);
        expect(Object.keys(items[0]).sort()).toEqual(['occurrence_id', 'status']);
      }
      await testInfo.attach('h3-real-resolve-delivery', { contentType: 'application/json', body: JSON.stringify({
        ...barrier.evidence(), projectId: fixture.projectId, occurrenceId: fixture.occurrenceId,
        coverage: 'real not_found forwarding; no ready-grant or playback claim',
      }) });
    } finally { await barrier.dispose(); await context.close(); }
  });

for (const kind of ['exchange', 'resolve'] as const) {
  test(`H3 real app refresh: single ordinary-read 401 while ${kind} response is held`,
    async ({ browser, scopeActors: scopes }, testInfo) => {
      test.setTimeout(300_000);
      const context = await scopes.openContext(browser, scopes.ownerA,
        { appUrl: E2E.attachmentsAppUrl, ignoreHTTPSErrors: true });
      const barrier = new AttachmentMediaDelivery(context, E2E.attachmentsAppUrl, true);
      try {
        const mediaPage = await context.newPage(), appPage = await context.newPage();
        await test.step('Open media app', () => openApp(mediaPage), { timeout: UI_READY });
        await test.step('Open refresh app', () => openApp(appPage), { timeout: UI_READY });
        const fixture = kind === 'resolve' ? await resolveCase(scopes) : undefined;
        let sessionId: string | undefined;
        if (fixture) {
          const exchange = await browserPost(mediaPage, MEDIA_EXCHANGE, {});
          expect(exchange.status).toBe(204); expect(exchange.sessionId).toBeTruthy();
          sessionId = exchange.sessionId!;
        }
        const operation: MediaOperation = fixture ? { kind: 'resolve', projectId: fixture.projectId } : { kind: 'exchange' };
        const held = await barrier.holdNext(mediaPage, 'BeforeRefresh', operation, identity(scopes.ownerA, sessionId));
        const pending = browserPost(mediaPage, fixture ? mediaResolve(fixture.projectId) : MEDIA_EXCHANGE,
          fixture?.body ?? {}, sessionId);
        await held.ready();
        const refresh = await exerciseAppRefresh(appPage, () => appPage.reload({ timeout: UI_READY }));
        await held.release();
        expect((await pending).status).toBe(kind === 'exchange' ? 204 : 200);
        // This proves real app refresh and old-response transport, not stale-result
        // rejection in a player; OBS-E2E-074 owns that separate product assertion.
        await testInfo.attach(`h3-real-refresh-${kind}`, { contentType: 'application/json', body: JSON.stringify({
          refresh, delivery: barrier.evidence(), playerStaleResultGuardQualified: false,
        }) });
      } finally { await barrier.dispose(); await context.close(); }
    });
}
