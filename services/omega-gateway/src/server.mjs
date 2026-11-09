// Omega customer gateway: Future AGI browser session -> customer assertion -> Omega EE.
//
// Adapted from Squad's services/omega-squad-gateway/src/server.mjs (7debd6482). The request
// path, in order (each step is its own module):
//   1. trace context and request id (trace.mjs), probes, drain refusal (lifecycle.mjs);
//   2. the route decision, generated from the route manifest, default deny (routes.mjs);
//   3. the credential: a presented assertion is checked, a browser token or a personal API
//      key (SB W2-KEYS) exchanged (credential.mjs); then the product wall: the verified key
//      must name the route's product in its `products` claim (product-wall.mjs, READINESS
//      3.13, lane 1.10b); the human-session-only verbs refuse every method but a browser
//      session (session-only.mjs, critique C2-3);
//   4. the role floor and scope parameters, then the rate limit for the route's class
//      (routes.mjs, rate-class.mjs, ratelimit.mjs), all before the body is read; a key the
//      gateway exchanged and a slow upload aged is minted again before it is spent (for the
//      same identity, or refused), and a presented assertion the upload outlived is refused,
//      never forwarded expired (upstream.mjs reads the body); a route of a feature the manifest
//      hides answers 404 `not_found`, in EE's envelope, and goes no further (HIDE-API);
//   5. EE, with the timeout class, and the answer piped back (upstream.mjs).
// Two listeners share this path: the app listener on the Future AGI origin (`/omega/api`,
// Sandboxes and Deployments tabs, browser tokens or assertions) and, when configured, the
// Console-origin listener (static Console build, assertions only; console-origin.mjs).
// Every answer the gateway gives itself instead of EE's also writes one refusal line with its
// reason (refusal-log.mjs, READINESS 3.13 RC-03), beside the access record of every request.
import http from "node:http";
import { pathToFileURL } from "node:url";
import { loadConfig } from "./config.mjs";
import {
  apiKeyCredential,
  checkPresentedAssertion,
  mintFromCredential,
  presentedCredential,
  sameIdentity,
} from "./credential.mjs";
import {
  CSP_REPORT_PATH,
  readCspReport,
  securityHeaders,
  serveStatic,
} from "./console-origin.mjs";
import { createChannelBindings } from "./channel-token/bindings.mjs";
import { CHANNEL_TOKEN_QUERY } from "./channel-token/manifest-credential.mjs";
import { redactSecrets } from "./channel-token/redact.mjs";
import {
  isChannelTarget,
  mintAwareFetch,
  serveChannelRequest,
} from "./channel-token/serve.mjs";
import { createLifecycle } from "./lifecycle.mjs";
import { createSpanExporter } from "./otlp.mjs";
import { createMemoryStore, createRateLimiter } from "./ratelimit.mjs";
import { createRedisStore } from "./ratelimit-redis.mjs";
import { decideIdentity, decideRoute, ROUTE_MANIFEST } from "./routes.mjs";
import { rateClassOf } from "./rate-class.mjs";
import { TRIGGER_BUDGETS } from "./trigger-budget-contract.mjs";
import { decideSessionOnly } from "./session-only.mjs";
import { accessRecord, serverTiming, startRequest } from "./trace.mjs";
import { BodyRefused, forwardToEe, readBody } from "./upstream.mjs";
import { decideProduct, loadRouteProducts } from "./product-wall.mjs";
import { createRefusalLog } from "./refusal-log.mjs";

// Loaded once at start: a malformed table stops the gateway instead of widening the wall.
const ROUTE_PRODUCTS = loadRouteProducts();

// Matched lower-cased. The API-key spellings (SB W2-KEYS) are here so a key put in a URL is
// refused before it can reach EE's request line or any log, never forwarded.
const CREDENTIAL_QUERY_KEYS = new Set([
  "access_token",
  "api_key",
  "api-key",
  "apikey",
  "authorization",
  "credential",
  "credentials",
  "key",
  "secret",
  "secret_key",
  "secret-key",
  "secretkey",
  "token",
  "x-api-key",
  "x-secret-key",
]);
// A key with less than this left after its body was read is minted again before it goes to EE.
const REMINT_BELOW_SECONDS = 20;
// EE's workspace envelope version: a hidden feature's 404 is the body EE gives an app that is not offered.
const WORKSPACE_SCHEMA_VERSION = "omega-workspace/v1";
const EXPOSED_HEADERS =
  "retry-after, server-timing, x-request-id, x-upstream-request-id, etag, x-omega-operation-id, x-omega-receipt-id, x-omega-idempotency-key, content-disposition";

function corsHeaders(request, config) {
  const origin = request.headers.origin;
  if (!origin || !config.allowedOrigins.includes(origin)) return {};
  return {
    "access-control-allow-origin": origin,
    "access-control-allow-credentials": "true",
    "access-control-allow-headers":
      "authorization, content-type, idempotency-key, if-match, if-none-match, last-event-id, x-api-key, x-secret-key, x-organization-id, x-workspace-id, traceparent, tracestate, x-omega-event-id, x-omega-timestamp, x-omega-trigger-signature",
    "access-control-allow-methods": "GET,POST,PATCH,PUT,DELETE,OPTIONS",
    "access-control-expose-headers": EXPOSED_HEADERS,
    "timing-allow-origin": origin,
    vary: "Origin",
  };
}

/**
 * The raw request target, split. Only origin-form (`/path?query`) is accepted, and never a
 * `#`: a browser never sends a fragment, and fetch would cut the target there, so EE would
 * receive a different path from the one the wall matched.
 */
function rawTarget(request) {
  const target = request.url || "";
  if (!target.startsWith("/") || target.includes("#")) return null;
  const mark = target.indexOf("?");
  return mark === -1
    ? { path: target, search: "" }
    : { path: target.slice(0, mark), search: target.slice(mark) };
}

function routePathOf(path, basePath) {
  if (!basePath) return path;
  if (path === basePath) return "/";
  return path.startsWith(`${basePath}/`) ? path.slice(basePath.length) : null;
}

function clientIp(request, hops) {
  if (hops > 0) {
    const chain = String(request.headers["x-forwarded-for"] || "")
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean);
    if (chain.length >= hops) return chain[chain.length - hops];
  }
  return request.socket.remoteAddress || "unknown";
}

/** The limiter a gateway gets unless a test injects one: Redis when configured, with OD-07. */
export function defaultRateLimiter(config, log) {
  const store =
    config.rateLimitStore?.kind === "redis"
      ? createRedisStore(config.rateLimitStore)
      : createMemoryStore();
  return createRateLimiter({ store, fallback: createMemoryStore(), log });
}

// No channel token survives a log line (X-1); the record holds none by design (trace.mjs).
const defaultLog = (record) =>
  process.stdout.write(`${redactSecrets(JSON.stringify(record))}\n`);

/**
 * Build both listeners and their shared state.
 *
 * @param {{ config?: object, fetchImpl?: typeof fetch, rateLimiter?: object, exporter?: object,
 *           log?: (record: object) => void, manifest?: object, exportFetch?: typeof fetch,
 *           routeProducts?: object, refusalLog?: (entry: object) => void }} [options]
 *   `log` takes the access record of every request; `refusalLog` one line per answer the
 *   gateway gives itself (RC-03).
 */
export function createGateway({
  config = loadConfig(),
  fetchImpl = globalThis.fetch,
  rateLimiter,
  exporter,
  log = defaultLog,
  manifest = ROUTE_MANIFEST,
  exportFetch,
  routeProducts = ROUTE_PRODUCTS,
  refusalLog = createRefusalLog(),
} = {}) {
  const limiter = rateLimiter ?? defaultRateLimiter(config, log);
  const spans =
    exporter ??
    createSpanExporter(config.otlp, {
      fetchImpl: exportFetch ?? globalThis.fetch,
      log,
    });
  const lifecycle = createLifecycle(config, { fetchImpl });
  // CX-WALL: the channel tokens this gateway saw EE mint, by digest (channel-token/bindings.mjs).
  const channels = createChannelBindings();
  const deps = {
    config,
    fetchImpl,
    limiter,
    lifecycle,
    manifest,
    routeProducts,
    refusalLog,
    channels,
  };

  function dispatch(listener) {
    return (request, response) => {
      const context = startRequest(request, { listener });
      lifecycle.track(response);
      response.once("close", () => {
        if (context.status === undefined) context.status = response.statusCode;
        try {
          log(accessRecord(context));
          spans.record(context);
        } catch {}
      });
      handle(request, response, context, deps).catch(() => {
        context.reason = context.reason ?? "gateway_failure";
        if (!response.headersSent) {
          context.status = 502;
          response.writeHead(502, {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
            "x-request-id": context.traceId,
          });
          response.end(
            JSON.stringify({
              error: "omega_gateway_failure",
              requestId: context.traceId,
            }),
          );
          refusalLog({
            method: request.method,
            path: String(request.url || "/").split("?")[0],
            status: 502,
            error: "omega_gateway_failure",
          });
        } else {
          response.destroy();
        }
      });
    };
  }

  const app = http.createServer(dispatch("app"));
  const consoleServer = config.consoleOrigin
    ? http.createServer(dispatch("console"))
    : null;
  const servers = [app, consoleServer].filter(Boolean);
  const gateway = {
    config,
    app,
    console: consoleServer,
    lifecycle,
    rateLimiter: limiter,
    exporter: spans,
    /** Stop taking work, end streams, wait for the rest (bounded), flush spans, release Redis. */
    async drain(drainMs = config.drainMs) {
      const outcome = await lifecycle.drain(servers, drainMs);
      await spans.shutdown().catch(() => {});
      limiter.close();
      return outcome;
    },
  };
  app.omega = gateway;
  return gateway;
}

/** The app listener alone (the shape the local stack and older callers use). */
export function createGatewayServer(options = {}) {
  return createGateway(options).app;
}

async function handle(
  request,
  response,
  context,
  {
    config,
    fetchImpl,
    limiter,
    lifecycle,
    manifest,
    routeProducts,
    refusalLog,
    channels,
  },
) {
  const isConsole = context.listener === "console";
  // The refusal line names org and workspace only from a key whose signature this gateway checked.
  const verified = Boolean(config.assertionPublicKeys);
  const logPath = String(request.url || "/").split("?")[0];
  let knownClaims;
  const cors = isConsole ? {} : corsHeaders(request, config);
  const base = () => ({
    ...cors,
    ...(isConsole ? securityHeaders(config.mode) : {}),
    "x-request-id": context.traceId,
    "server-timing": serverTiming(context),
  });
  const respond = (status, body, extra = {}) => {
    context.status = status;
    if (body.reason) context.reason = body.reason;
    else if (status >= 400) context.reason = body.error;
    // `reason` is a stable code (never token or request material); `requestId` is the trace id.
    response.writeHead(status, {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      ...base(),
      ...extra,
    });
    response.end(
      JSON.stringify({
        ...body,
        ...(status >= 400 ? { requestId: context.traceId } : {}),
      }),
    );
    if (status >= 400 && body.error) {
      refusalLog({
        method: request.method,
        path: logPath,
        status,
        error: body.error,
        reason: body.reason,
        claims: knownClaims,
        verified,
      });
    }
  };
  // A feature the Console does not show (manifest `hidden`): the answer EE gives an Integrations app that
  // is not offered (404, `schemaVersion`, `error: { code, message }`, `code`, `message`, `request_id`): the
  // same status, code and five keys at W2 and W3 for every tenant. Not indistinguishable: this body is compact
  // JSON with no x-upstream-request-id, and its request_id is the gateway's. The refusal line names it.
  const respondHidden = () => {
    context.status = 404;
    context.reason = "feature_hidden";
    const message = "Not found.";
    response.writeHead(404, {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      ...base(),
    });
    response.end(
      JSON.stringify({
        schemaVersion: WORKSPACE_SCHEMA_VERSION,
        error: { code: "not_found", message },
        code: "not_found",
        message,
        request_id: context.traceId,
      }),
    );
    refusalLog({
      method: request.method,
      path: logPath,
      status: 404,
      error: "not_found",
      reason: "feature_hidden",
      claims: knownClaims,
      verified,
    });
  };
  const refuse = (refusal) => {
    if (refusal.claims) knownClaims = refusal.claims;
    const extra = refusal.retryAfterSeconds
      ? { "retry-after": String(refusal.retryAfterSeconds) }
      : {};
    const body = { error: refusal.error, reason: refusal.reason };
    if (refusal.rateClass)
      Object.assign(body, {
        rateClass: refusal.rateClass,
        retryAfterSeconds: refusal.retryAfterSeconds,
      });
    if (refusal.rateClass === "trigger-ingress") body.budgets = TRIGGER_BUDGETS;
    respond(refusal.status, body, extra);
  };

  if (request.method === "OPTIONS") {
    context.status = 204;
    response.writeHead(204, {
      ...cors,
      ...(isConsole ? securityHeaders(config.mode) : {}),
    });
    response.end();
    return;
  }
  const target = rawTarget(request);
  if (!target) return respond(400, { error: "bad_request_target" });
  // Credentials belong in the exchanged browser session, never in a URL; a channel route's own
  // token (X-1) is the one exception, and only on that route (channel-token/serve.mjs).
  const channelTarget = isChannelTarget(
    request.method,
    routePathOf(target.path, config.basePath),
    manifest,
  );
  for (const key of new URLSearchParams(target.search).keys()) {
    if (
      CREDENTIAL_QUERY_KEYS.has(key.toLowerCase()) &&
      !(channelTarget && key === CHANNEL_TOKEN_QUERY)
    )
      return respond(400, { error: "credential_in_query" });
  }
  if (
    isConsole &&
    target.path === CSP_REPORT_PATH &&
    request.method === "POST"
  ) {
    const report = await readCspReport(request);
    context.route = CSP_REPORT_PATH;
    if (report)
      context.reason = `csp:${report.directive}:${report.blocked}:${report.disposition}`;
    context.status = 204;
    response.writeHead(204, securityHeaders(config.mode));
    response.end();
    return;
  }
  const routePath = routePathOf(target.path, config.basePath);
  // Liveness and readiness are the gateway's own; they need no session and name no upstream.
  for (const probe of ["/healthz", "/readyz"]) {
    if (target.path !== probe && routePath !== probe) continue;
    context.route = probe;
    if (request.method !== "GET" && request.method !== "HEAD")
      return respond(405, { error: "method_not_allowed" });
    if (probe === "/healthz") return respond(200, { live: true });
    const state = await lifecycle.readiness();
    return respond(state.ready ? 200 : 503, state);
  }
  if (lifecycle.draining)
    return respond(
      503,
      { error: "omega_gateway_draining" },
      { connection: "close", "retry-after": "1" },
    );
  if (routePath === null) {
    if (
      isConsole &&
      serveStatic(request, response, target.path, config.consoleOrigin, {
        mode: config.mode,
        extraHeaders: { "x-request-id": context.traceId },
      })
    ) {
      context.route = "static";
      context.status = 200;
      return;
    }
    return respond(404, { error: "not_found" });
  }
  const decision = decideRoute(request.method, routePath, manifest);
  if (!decision.allowed)
    return respond(403, {
      error: "route_not_allowed",
      reason: decision.reason,
    });
  context.route = decision.route.path;
  // The manifest's class, or the gateway class that refines it (L03: sandbox creates).
  const rateClass = rateClassOf(decision.route);
  context.rateClass = rateClass;
  if (decision.route.rate === "public-share" && !config.publicShare)
    return respond(404, { error: "not_found", reason: "public_share_closed" });
  if (
    isConsole &&
    request.headers.origin &&
    request.headers.origin !== config.consoleOrigin.origin
  ) {
    return respond(403, { error: "origin_not_allowed" });
  }
  if (decision.route.credential === "channel") {
    return serveChannelRequest({
      request,
      response,
      context,
      config,
      fetchImpl,
      decision,
      routePath,
      search: target.search,
      manifest,
      bindings: channels,
      routeProducts,
      verified,
      refuse,
      limiter,
      ip: clientIp(request, config.trustedProxyHops),
      headers: base,
      trackStream: lifecycle.trackStream,
    });
  }
  if (decision.route.credential === "trigger-signature") {
    if (decision.route.hiddenBy !== undefined) return respondHidden();
    // No session exchange, bearer parsing or caller-claimed tenant. EE verifies the signature
    // and re-joins the stored owner's live membership, role and Console admission on every event.
    // Pre-verification traffic pays only its source-IP bucket. EE charges the trigger's
    // per-trigger and per-workspace budgets after signature verification.
    const ingressRateClass = "trigger-ingress";
    context.rateClass = ingressRateClass;
    const limit = await limiter.take(ingressRateClass, {
      ip: clientIp(request, config.trustedProxyHops),
    });
    context.rateStore = limit.store;
    if (limit.unavailable)
      return refuse({
        status: 503,
        error: "omega_rate_limit_unavailable",
        reason: "rate_store_unavailable",
        rateClass: ingressRateClass,
        retryAfterSeconds: limit.retryAfterSeconds,
      });
    if (!limit.allowed)
      return refuse({
        status: 429,
        error: "omega_rate_limited",
        reason: "rate_limited",
        rateClass: ingressRateClass,
        retryAfterSeconds: limit.retryAfterSeconds,
      });
    let body;
    try {
      body = await readBody(request, decision.route.body, config);
    } catch (error) {
      return refuse({
        status: error instanceof BodyRefused ? 413 : 400,
        error: "request_body_refused",
      });
    }
    const result = await forwardToEe({
      request,
      response,
      config,
      context,
      route: decision.route,
      path: routePath,
      search: target.search,
      fetchImpl,
      body,
      headers: base,
      trackStream: lifecycle.trackStream,
    });
    if (result.error && !response.headersSent)
      respond(result.status, { error: result.error });
    return;
  }
  const keyPresented = apiKeyCredential(request);
  const presented = keyPresented ?? presentedCredential(request);
  if (presented.status && !presented.kind) return refuse(presented);
  if (!presented.kind)
    return refuse({
      status: 401,
      error: "omega_session_unavailable",
      reason: "no_credential",
    });
  // The Console origin never handles a Future AGI browser token or API key (CP P6).
  if (isConsole && presented.kind !== "assertion")
    return respond(401, { error: "omega_assertion_required" });

  const admit = async (claims) => {
    const identity = decideIdentity(decision, claims);
    if (!identity.allowed)
      return {
        status: 403,
        error:
          identity.reason === "role_below_floor"
            ? "omega_role_not_permitted"
            : "omega_scope_mismatch",
        reason: identity.reason,
      };
    context.tenant = { org: claims.org_id, workspace: claims.workspace_id };
    const limit = await limiter.take(rateClass, {
      sub: claims.sub,
      orgId: claims.org_id,
      workspaceId: claims.workspace_id,
      ip: clientIp(request, config.trustedProxyHops),
    });
    context.rateStore = limit.store;
    // OD-07 (CH-08): the shared limit store is down and this class may not fall back.
    if (limit.unavailable)
      return {
        status: 503,
        error: "omega_rate_limit_unavailable",
        reason: "rate_store_unavailable",
        rateClass,
        retryAfterSeconds: limit.retryAfterSeconds,
      };
    if (!limit.allowed)
      return {
        status: 429,
        error: "omega_rate_limited",
        reason: "rate_limited",
        rateClass,
        retryAfterSeconds: limit.retryAfterSeconds,
      };
    return null;
  };
  const read = async () => {
    try {
      return { body: await readBody(request, decision.route.body, config) };
    } catch (error) {
      if (error instanceof BodyRefused)
        return { refusal: { status: 413, error: "request_body_too_large" } };
      return { refusal: { status: 400, error: "request_body_unreadable" } };
    }
  };

  // Either way the caller is proven, admitted and limited before a byte of the body is read,
  // so a stranger can never make the gateway buffer an upload.
  // Django limits key attempts per key and client source (accounts/omega_throttles.py), so the
  // exchange names the client this gateway saw: one address (`clientIp`), never the caller's own
  // X-Forwarded-For chain. Django believes it only from a proxy it is configured to trust.
  const exchangeHeaders = () => ({
    ...context.outboundHeaders(),
    "x-forwarded-for": clientIp(request, config.trustedProxyHops),
  });
  const mint = () =>
    context.time("mint", () =>
      mintFromCredential(
        presented,
        request,
        config,
        fetchImpl,
        exchangeHeaders(),
      ),
    );
  // The product wall (READINESS 3.13): only claims this gateway verified count, so an edited
  // `products` claim never passes; a dark product answers 404, a paused one 503 for new work.
  const productWall = (claims) => {
    const product = decideProduct(
      routeProducts,
      request.method,
      routePath,
      claims,
      { verified },
    );
    return product.ok ? null : product;
  };
  let credential =
    presented.kind === "assertion"
      ? checkPresentedAssertion(presented.token, request, config)
      : await mint();
  if (!credential.token) return refuse(credential);
  knownClaims = credential.claims;
  const closed = productWall(credential.claims);
  if (closed) return refuse(closed);
  // Approvals, share links, sharing and settings take a person, never a key (session-only.mjs);
  // refused before the limiter and the body, so a refused verb spends nothing.
  const humanOnly = decideSessionOnly(decision.route, credential.claims);
  if (humanOnly) return refuse(humanOnly);
  const refusal = await admit(credential.claims);
  if (refusal) return refuse(refusal);
  // Proven, admitted to the product and limited like any request; never read or forwarded.
  if (decision.route.hiddenBy !== undefined) return respondHidden();
  const got = await read();
  if (got.refusal) return refuse(got.refusal);
  // A key this gateway exchanged (from a browser token or an API key) that a slow upload has
  // aged is replaced before it is spent, so EE never sees a key about to expire (READINESS 1.3;
  // an SDK's 1 GiB upload by API key, SB W2-FILES). A presented assertion cannot be re-minted here:
  // if the upload outlived it, it is refused with the code the Future AGI client already
  // retries with a fresh key (omegaClient.js STALE_ASSERTION_CODES), instead of being sent to
  // EE expired. Streaming the body to EE is the full fix (owed).
  if (
    presented.kind === "assertion" &&
    Math.floor(Date.now() / 1000) >= credential.claims.exp
  ) {
    return respond(401, {
      error: "omega_assertion_rejected",
      reason: "expired_assertion",
      message:
        "The key expired while the upload was read. Retry with a fresh key.",
    });
  }
  if (
    presented.kind !== "assertion" &&
    credential.claims.exp - Math.floor(Date.now() / 1000) < REMINT_BELOW_SECONDS
  ) {
    const admitted = credential.claims;
    credential = await mint();
    if (!credential.token) return refuse(credential);
    // The new key must speak for the person, tenant and credential the request was admitted
    // with: an issuer answer for another workspace (a re-pinned key, a changed default) is
    // refused, never forwarded (the Future AGI tab's copy for it says to reload). The refusal
    // line names the tenant the request was admitted for.
    if (!sameIdentity(admitted, credential.claims))
      return refuse({
        status: 403,
        error: "omega_scope_mismatch",
        reason: "scope_mismatch",
        claims: admitted,
      });
    knownClaims = credential.claims;
    const reclosed = productWall(credential.claims);
    if (reclosed) return refuse(reclosed);
    const identity = decideIdentity(decision, credential.claims);
    if (!identity.allowed)
      return refuse({
        status: 403,
        error:
          identity.reason === "role_below_floor"
            ? "omega_role_not_permitted"
            : "omega_scope_mismatch",
        reason: identity.reason,
      });
  }
  const body = got.body;
  const result = await forwardToEe({
    request,
    response,
    config,
    context,
    route: decision.route,
    path: routePath,
    search: target.search,
    fetchImpl: mintAwareFetch(fetchImpl, {
      decision,
      routePath,
      manifest,
      bindings: channels,
      claims: credential.claims,
    }),
    token: credential.token,
    body,
    headers: base,
    trackStream: lifecycle.trackStream,
  });
  if (result.error && !response.headersSent)
    respond(result.status, { error: result.error });
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  const config = loadConfig();
  const gateway = createGateway({ config });
  const listen = (server, port, host, name) =>
    new Promise((resolve, reject) => {
      server.once("error", reject);
      server.listen(port, host, () => {
        defaultLog({
          event: "omega-gateway.listening",
          listener: name,
          url: `http://${host}:${server.address().port}`,
          mode: config.mode,
          rateStore: gateway.rateLimiter.storeKind,
          otlp: gateway.exporter.enabled,
        });
        resolve();
      });
    });
  await listen(gateway.app, config.port, config.host, "app");
  if (gateway.console)
    await listen(
      gateway.console,
      config.consoleOrigin.port,
      config.consoleOrigin.host,
      "console",
    );
  let stopping = false;
  const stop = async (signal) => {
    if (stopping) return;
    stopping = true;
    defaultLog({
      event: "omega-gateway.draining",
      signal,
      inFlight: gateway.lifecycle.inFlight(),
    });
    const outcome = await gateway.drain();
    defaultLog({ event: "omega-gateway.stopped", ...outcome });
    process.exit(0);
  };
  process.once("SIGTERM", () => stop("SIGTERM"));
  process.once("SIGINT", () => stop("SIGINT"));
}
