import { request as pwRequest } from '@playwright/test';
import type { BrowserContext, Page, Request, Response, Route } from '@playwright/test';
import { ownedScopeOrigin, scopeId } from './scope-actors';

// Pins at foundation 69567785f: tracer/urls.py; views/attachments.py
// AttachmentMediaSessionView/AttachmentResolveView; views/attachment_media.py
// exchange_response; services/attachments/playback.py cookie/header constants.
export const DELIVERY_READY = 60_000;
export const MEDIA_EXCHANGE = '/tracer/v1/attachment-media-sessions';
export const MEDIA_SESSION_HEADER = 'x-attachment-media-session';
const EXPIRY_HEADER = 'x-attachment-media-expires-at';
const MEDIA_COOKIE = 'fi_media_session_v2';
export const mediaResolve = (projectId: string) => `/tracer/v1/projects/${scopeId(projectId)}/attachments/resolve`;
const cookiePath = (sessionId: string) => `/tracer/v1/attachment-media/${scopeId(sessionId)}/`;

function requireThat(condition: unknown, message: string): asserts condition {
  // Do not put actual headers, cookies, bodies or transport call logs in errors.
  if (!condition) throw new Error(`H3 ${message}`);
}

function signal<T>() {
  let resolve!: (value: T) => void, reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  void promise.catch(() => {}); // A cancelled route may finish before its waiter.
  return { promise, resolve, reject };
}

async function bounded<T>(promise: Promise<T>, phase: string): Promise<T> {
  // Deadline only, never a sleep, retry, or delayed release.
  const deadline = AbortSignal.timeout(DELIVERY_READY);
  let abort!: () => void;
  const expired = new Promise<never>((_, reject) => {
    abort = () => reject(new Error(`H3 ${phase} exceeded DELIVERY_READY`));
    deadline.addEventListener('abort', abort, { once: true });
  });
  try { return await Promise.race([promise, expired]); }
  finally { deadline.removeEventListener('abort', abort); }
}

export interface DeliveryEvent {
  operation: string;
  phase: 'request' | 'backend-response' | 'browser-delivery';
  sequence: number;
}

/** Independent order oracle: a release-order mutation must fail here. */
export function assertReverseDelivery(events: readonly DeliveryEvent[], a: string, b: string): void {
  const sequence = (operation: string, phase: DeliveryEvent['phase']) => {
    const matches = events.filter(event => event.operation === operation && event.phase === phase);
    requireThat(matches.length === 1, 'order requires exactly one event per operation/phase');
    return matches[0].sequence;
  };
  const ar = sequence(a, 'request'), ab = sequence(a, 'backend-response');
  const br = sequence(b, 'request'), bb = sequence(b, 'backend-response');
  const ad = sequence(a, 'browser-delivery'), bd = sequence(b, 'browser-delivery');
  requireThat(ar < ab && ab < br && br < bb, 'must complete A backend response before B issuance');
  requireThat(bb < bd && bd < ad, 'must deliver B before A');
}

export interface MediaOperationIdentity {
  /** Actual app bearer captured for THIS operation; retained only in memory. */
  authorization: string;
  organizationId: string;
  workspaceId: string;
  mediaSessionId?: string;
}
export type MediaOperation = { kind: 'exchange' } | { kind: 'resolve'; projectId: string };

function headerMap(headers: { name: string; value: string }[]): Record<string, string> {
  const map: Record<string, string> = {};
  for (const { name, value } of headers) {
    const key = name.toLowerCase();
    map[key] = key in map ? `${map[key]}${key === 'set-cookie' ? '\n' : ', '}${value}` : value;
  }
  return map;
}

/** Holds only real responses. No synthetic session, grant, token, or cookie.
 * IMPORTANT: never use route.fetch(), page.request or context.request here:
 * those share the browser cookie jar and can install Set-Cookie before fulfill.
 * A fresh isolated APIRequestContext forwards the original request's exact
 * headers/body once, with no redirects or retries. Only fulfill delivers it.
 * Credentials are memory-only; dependent specs must disable trace recording.
 */
export class AttachmentMediaDelivery {
  readonly origin: string;
  private readonly events: DeliveryEvent[] = [];
  private readonly operations: HeldMediaResponse[] = [];
  constructor(readonly context: BrowserContext, origin: string,
    readonly ignoreHTTPSErrors = false) {
    this.origin = ownedScopeOrigin(origin);
    requireThat(new URL(this.origin).protocol === 'https:', 'media delivery requires HTTPS');
  }

  async holdNext(page: Page, label: string, operation: MediaOperation,
    expected: MediaOperationIdentity, after?: HeldMediaResponse): Promise<HeldMediaResponse> {
    requireThat(page.context() === this.context, 'page must belong to the owned context');
    requireThat(/^[A-Za-z][A-Za-z0-9_-]{0,31}$/.test(label), 'operation label must be non-secret');
    requireThat(!this.operations.some(op => op.label === label), 'operation labels must be unique');
    requireThat(!after || this.operations.includes(after), 'predecessor must belong to this barrier');
    scopeId(expected.organizationId); scopeId(expected.workspaceId);
    requireThat(/^Bearer \S+$/.test(expected.authorization), 'requires an actual application bearer');
    if (operation.kind === 'resolve') scopeId(expected.mediaSessionId ?? '');
    const held = new HeldMediaResponse(this, page, label, operation, { ...expected }, after);
    this.operations.push(held);
    await held.install();
    return held;
  }

  record(operation: string, phase: DeliveryEvent['phase']) {
    this.events.push({ operation, phase, sequence: this.events.length + 1 });
  }
  assertReversed(a: HeldMediaResponse, b: HeldMediaResponse) {
    assertReverseDelivery(this.events, a.label, b.label);
  }
  evidence() {
    return { transport: 'isolated-api-request-context', events: this.events.map(event => ({ ...event })),
      operations: this.operations.map(operation => operation.evidence()) };
  }
  async dispose() { for (const operation of this.operations) await operation.dispose(); }
}

export class HeldMediaResponse {
  private readonly readySignal = signal<void>();
  private readonly releaseSignal = signal<void>();
  private readonly deliveredSignal = signal<void>();
  private claimed = false;
  private disposed = false;
  private task?: Promise<void>;
  private backendComplete = false;
  private readyForSuccessor = false;
  private delivered = false;
  private cookieValue?: string;
  private _sessionId?: string;
  private expiresAt?: string;
  private status?: number;
  private cookieAbsentWhileHeld = false;
  private cookieInstalledOnDelivery = false;
  private unchangedResponse = false;
  private requestIdentityMatched = false;
  private readonly url: string;
  private readonly handler: (route: Route) => Promise<void>;

  constructor(private readonly barrier: AttachmentMediaDelivery, private readonly page: Page,
    readonly label: string, private readonly operation: MediaOperation,
    private readonly expected: MediaOperationIdentity, private readonly after?: HeldMediaResponse) {
    this.url = barrier.origin + (operation.kind === 'exchange' ? MEDIA_EXCHANGE : mediaResolve(operation.projectId));
    this.handler = async route => {
      if (route.request().method() !== 'POST' || this.claimed) return route.fallback();
      this.claimed = true;
      this.task = this.capture(route);
      await this.task;
    };
  }
  async install() { await this.page.route(this.url, this.handler); }
  async ready() { await bounded(this.readySignal.promise, 'backend response'); }
  get sessionId(): string {
    requireThat(this.backendComplete && this._sessionId, 'session handle is unavailable before backend completion');
    return this._sessionId;
  }

  private async cookies() {
    // context.cookies(origin) would OMIT cookies scoped to a deeper media path.
    return (await this.barrier.context.cookies()).filter(cookie =>
      cookie.name === MEDIA_COOKIE && cookie.domain === new URL(this.barrier.origin).hostname
      && cookie.path === cookiePath(this.sessionId));
  }
  async assertHeldCookieAbsent() {
    requireThat(this.operation.kind === 'exchange' && !this.delivered, 'cookie hold check requires an undelivered exchange');
    requireThat((await this.cookies()).length === 0, 'held Set-Cookie reached browser before release');
    this.cookieAbsentWhileHeld = true;
  }

  private async capture(route: Route) {
    let upstream: Awaited<ReturnType<typeof pwRequest.newContext>> | undefined;
    let browserReply: ReturnType<Page['waitForResponse']> | undefined;
    try {
      requireThat(!this.disposed, 'barrier is disposed');
      // Enforce causality at request interception, BEFORE sending anything to backend.
      requireThat(!this.after || this.after.readyForSuccessor, 'B requested before A backend completed');
      const request = route.request(), headers = await request.allHeaders();
      requireThat(headers.authorization === this.expected.authorization
        && headers['x-organization-id'] === this.expected.organizationId
        && headers['x-workspace-id'] === this.expected.workspaceId
        && headers.origin === this.barrier.origin, 'operation-affine authorization/scope/origin mismatch');
      if (this.operation.kind === 'resolve') {
        requireThat(headers[MEDIA_SESSION_HEADER] === this.expected.mediaSessionId, 'operation-affine handle mismatch');
      }
      this.requestIdentityMatched = true;
      this.barrier.record(this.label, 'request');
      upstream = await pwRequest.newContext({ ignoreHTTPSErrors: this.barrier.ignoreHTTPSErrors,
        storageState: { cookies: [], origins: [] } });
      const response = await upstream.fetch(request.url(), { method: request.method(), headers,
        data: request.postDataBuffer() ?? undefined, maxRedirects: 0, maxRetries: 0, timeout: DELIVERY_READY });
      const body = await response.body(); // Body completion, not request-start order.
      const originalHeaders = response.headersArray();
      const map = headerMap(originalHeaders);
      this.status = response.status();
      requireThat(this.status === (this.operation.kind === 'exchange' ? 204 : 200), 'actual media response was not successful');
      if (this.operation.kind === 'exchange') {
        this._sessionId = scopeId(map[MEDIA_SESSION_HEADER] ?? '');
        requireThat(Number.isFinite(Date.parse(map[EXPIRY_HEADER])), 'exchange expiry header missing');
        this.expiresAt = map[EXPIRY_HEADER];
        const cookieHeaders = originalHeaders.filter(header => header.name.toLowerCase() === 'set-cookie');
        const media = cookieHeaders.filter(header => header.value.startsWith(`${MEDIA_COOKIE}=`));
        requireThat(media.length === 1, 'exchange must return one real media Set-Cookie');
        const parts = media[0].value.split(';').map(part => part.trim());
        this.cookieValue = parts[0].slice(MEDIA_COOKIE.length + 1);
        const attributes = parts.slice(1).map(part => part.toLowerCase());
        requireThat(this.cookieValue && attributes.includes('secure') && attributes.includes('httponly')
          && attributes.includes('samesite=strict') && attributes.includes(`path=${cookiePath(this._sessionId)}`)
          && !attributes.some(part => part.startsWith('domain=')), 'media cookie security/path contract mismatch');
      } else {
        this._sessionId = scopeId(this.expected.mediaSessionId!);
      }
      this.backendComplete = true;
      this.barrier.record(this.label, 'backend-response');
      if (this.operation.kind === 'exchange') await this.assertHeldCookieAbsent();
      this.readyForSuccessor = true;
      this.readySignal.resolve();
      await bounded(this.releaseSignal.promise, 'held response release');
      requireThat(!this.disposed, 'barrier was disposed before delivery');
      if (this.operation.kind === 'exchange') await this.assertHeldCookieAbsent();
      browserReply = this.page.waitForResponse(reply => reply.request() === request, { timeout: DELIVERY_READY });
      void browserReply.catch(() => {});
      // APIResponse forwarding retains real body/status/headers including Set-Cookie.
      // Assert what Chromium received below; do not infer delivery from fulfill alone.
      await route.fulfill({ response });
      const received = await browserReply;
      requireThat(await bounded(received.finished(), 'browser body') === null, 'browser response did not finish');
      const receivedHeaders = headerMap(await received.headersArray());
      // Chromium need not retain a Network.getResponseBody resource for 204.
      // The HTTP status forbids a body; the browser self-test also reads the
      // native fetch Response.text() and requires empty, rather than inventing it.
      const sameBody = response.status() === 204 ? body.length === 0 : (await received.body()).equals(body);
      requireThat(received.status() === response.status() && sameBody, 'browser status/body changed during delivery');
      requireThat(Object.entries(map).every(([name, value]) => receivedHeaders[name] === value),
        'browser response headers changed during delivery');
      this.unchangedResponse = true;
      if (this.operation.kind === 'exchange') {
        const cookies = await this.cookies();
        requireThat(cookies.length === 1 && cookies[0].value === this.cookieValue
          && cookies[0].secure && cookies[0].httpOnly && cookies[0].sameSite === 'Strict',
        'real cookie not installed at browser delivery');
        this.cookieInstalledOnDelivery = true;
      }
      this.delivered = true;
      this.barrier.record(this.label, 'browser-delivery');
      this.deliveredSignal.resolve();
    } catch (error) {
      const safe = error instanceof Error && /^H[13] /.test(error.message)
        ? error : new Error('H3 isolated response transport failed (raw headers/body omitted)');
      this.readySignal.reject(safe);
      this.deliveredSignal.reject(safe);
      await route.abort('failed').catch(() => {});
    } finally {
      await upstream?.dispose();
      this.cookieValue = undefined;
    }
  }

  async release() {
    await this.ready();
    requireThat(!this.delivered, 'response already delivered');
    this.releaseSignal.resolve();
    await bounded(this.deliveredSignal.promise, 'browser delivery');
  }
  evidence() {
    return { operation: this.label, kind: this.operation.kind,
      organizationId: this.expected.organizationId, workspaceId: this.expected.workspaceId,
      sessionId: this._sessionId ?? null, expiresAt: this.expiresAt ?? null, status: this.status ?? null,
      requestIdentityMatched: this.requestIdentityMatched, backendComplete: this.backendComplete,
      delivered: this.delivered, unchangedResponse: this.unchangedResponse,
      cookieAbsentWhileHeld: this.cookieAbsentWhileHeld, cookieInstalledOnDelivery: this.cookieInstalledOnDelivery,
      cookiePath: this._sessionId ? cookiePath(this._sessionId) : null };
  }
  async dispose() {
    this.disposed = true;
    const error = new Error('H3 barrier disposed');
    this.releaseSignal.reject(error); this.readySignal.reject(error); this.deliveredSignal.reject(error);
    await this.page.unroute(this.url, this.handler);
    await this.task;
  }
}

// Pins: frontend/src/auth/context/jwt/utils.js:87-105 refreshTokenRequest,
// frontend/src/utils/axios.js:167-239 interceptor, and the ordinary Observe GET
// in e2e/harness/scope-actors.spec.ts / tracer/views/project.py list_projects.
export const REFRESH_TRIGGER_READ = '/tracer/project/list_projects/';
const TOKEN_REFRESH = '/accounts/token/refresh/';

/** ONE explicit 401 fault injection, not natural expiry. Trigger must drive the
 * actual app's ordinary Observe read (navigation/reload/UI), not a parallel auth
 * client. Refresh is never intercepted, fetched, fulfilled or installed here.
 * The application sends {refresh,recaptcha_response}, empty Authorization and
 * JSON, consumes the real response and replays its read through its interceptor.
 */
export async function exerciseAppRefresh(page: Page, trigger: () => Promise<unknown>) {
  ownedScopeOrigin(new URL(page.url()).origin);
  const before = await page.evaluate(() => ({ access: localStorage.getItem('accessToken'),
    refresh: localStorage.getItem('refreshToken'), organizationId: sessionStorage.getItem('organizationId'),
    workspaceId: sessionStorage.getItem('workspaceId') }));
  requireThat(before.access && before.refresh, 'refresh requires a signed-in real application');
  const organizationId = scopeId(before.organizationId ?? ''), workspaceId = scopeId(before.workspaceId ?? '');
  let injections = 0, refreshCount = 0;
  let refreshRequest: Request | undefined;
  let injectedRequest: Request | undefined;
  // Separate event signals keep all errors consumed even when app navigation fails.
  const refreshResponse = signal<Response>();
  const replayResponse = signal<Response>();
  const onRequest = (request: Request) => {
    const url = new URL(request.url());
    if (url.hostname === 'localhost' && url.pathname === TOKEN_REFRESH && request.method() === 'POST') {
      if (injections !== 1) {
        refreshResponse.reject(new Error('H3 refresh preceded the explicit fault injection'));
        return;
      }
      refreshCount += 1; refreshRequest = request;
    }
  };
  const onResponse = (response: Response) => {
    if (response.request() === refreshRequest) refreshResponse.resolve(response);
    if (injectedRequest && response.request() !== injectedRequest
      && response.url() === injectedRequest.url() && response.request().method() === 'GET') {
      // Request.headers() omits security headers on some engines; never let an
      // absent header masquerade as the refreshed bearer on an unrelated read.
      void response.request().allHeaders().then(headers => {
        if (headers.authorization && headers.authorization !== `Bearer ${before.access}`) replayResponse.resolve(response);
      }).catch(() => replayResponse.reject(new Error('H3 could not observe app replay headers')));
    }
  };
  const matchesRead = (url: URL) => url.hostname === 'localhost' && url.pathname === REFRESH_TRIGGER_READ;
  const handler = async (route: Route) => {
    try {
      if (route.request().method() !== 'GET' || injections !== 0) return await route.fallback();
      const headers = await route.request().allHeaders();
      requireThat(headers.authorization === `Bearer ${before.access}`
        && headers['x-organization-id'] === organizationId && headers['x-workspace-id'] === workspaceId,
      'fault trigger must be the signed-in app ordinary read');
      injections += 1; injectedRequest = route.request();
      const message = 'H3 ordinary-read 401 fault injection; not natural token expiry.';
      // tfc/utils/api_errors.py build_error_envelope, status 401.
      await route.fulfill({ status: 401, contentType: 'application/json',
        headers: { 'Cache-Control': 'no-store', 'X-E2E-Fault-Injection': 'ordinary-read-401' },
        body: JSON.stringify({ status: false, type: 'authentication_error', code: 'not_authenticated',
          detail: message, message, error: message, result: message }) });
    } catch {
      // Never propagate transport errors: they may contain credentials/call logs.
      // Settle BOTH observations before abort, even if the page has terminated.
      const safe = new Error('H3 ordinary-read fault injection failed (request details omitted)');
      refreshResponse.reject(safe); replayResponse.reject(safe);
      try { await route.abort('failed'); } catch { /* Already terminated; keep the sanitized failure. */ }
    }
  };
  page.on('request', onRequest); page.on('response', onResponse);
  await page.route(matchesRead, handler);
  try {
    const observations = Promise.all([bounded(refreshResponse.promise, 'real refresh response'),
      bounded(replayResponse.promise, 'application replay')]);
    void observations.catch(() => {});
    // A failed route must surface even if navigation is still waiting on it.
    const [, [refresh, replay]] = await Promise.all([
      bounded(trigger(), 'ordinary app read'), observations,
    ]);
    requireThat(refreshCount === 1 && injections === 1 && refreshRequest, 'expected exactly one injected 401 and real refresh');
    requireThat(refresh.status() === 200 && replay.status() === 200, 'refresh/read replay was not successful');
    requireThat((await bounded(Promise.all([refresh.finished(), replay.finished()]), 'refresh/replay completion'))
      .every(error => error === null), 'refresh/read replay did not complete');
    const headers = await refreshRequest.allHeaders();
    const data = refreshRequest.postDataJSON() as Record<string, unknown>;
    requireThat(Object.keys(data).sort().join(',') === 'recaptcha_response,refresh'
      && data.refresh === before.refresh && (headers.authorization ?? '') === ''
      && headers['content-type']?.split(';')[0] === 'application/json', 'actual refresh request contract changed');
    const body = await refresh.json() as { access: string };
    requireThat(typeof body.access === 'string' && body.access.length > 0 && body.access !== before.access,
      'real refresh must return a distinct access token');
    const replayHeaders = await replay.request().allHeaders();
    requireThat(replayHeaders.authorization === `Bearer ${body.access}`
      && replayHeaders['x-organization-id'] === organizationId && replayHeaders['x-workspace-id'] === workspaceId,
    'app interceptor did not replay using refreshed bearer and original scope');
    requireThat(await page.evaluate(() => localStorage.getItem('accessToken')) === body.access,
      'app did not install the real refreshed access token');
    return { faultInjection: 'single-ordinary-read-401', naturalExpiry: false, injections, refreshCount,
      triggerPath: REFRESH_TRIGGER_READ, organizationId, workspaceId,
      actualRefreshContractMatched: true, appInstalledRealToken: true, replayUsedRealToken: true };
  } finally {
    page.off('request', onRequest); page.off('response', onResponse);
    await page.unroute(matchesRead, handler);
  }
}
