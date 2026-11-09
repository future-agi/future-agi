// Request bodies and the call to EE, by the manifest's body and timeout classes.
//
// Bodies: `none` forwards nothing (a stray body up to 1 KiB is read and dropped), `json` is
// capped at OMEGA_MAX_BODY_BYTES, `bundle` (deployment bundles, sandbox file writes) at
// OMEGA_MAX_BUNDLE_BYTES; a declared length over the cap is refused before a byte is read.
//
// Responses are piped, never buffered (READINESS 1.3, LD-05: gateway RSS under 150 MB during
// a 200 MB download): EE's body streams to the browser with backpressure, and a browser that
// goes away cancels the EE stream. The timeout class bounds the wait for EE's response
// headers only, so streams and long downloads stay open once they start.
import { Readable } from "node:stream";
import { SIGNED_TRIGGER_EVENT_PATH } from "./manifest.mjs";
import { publicRedirect } from "./redirect.mjs";

// The only request headers that ever reach EE besides the assertion and the trace context.
// Host selection headers go to the issuer only; X-Omega-* authority headers, cookies and
// forwarding headers are dropped.
const FORWARDED_REQUEST_HEADERS = [
  "content-type",
  "idempotency-key",
  "if-match",
  "if-none-match",
  "last-event-id",
];
// Receipt headers a client may need to reconcile a mutation. Redirect Locations are
// validated and rewritten separately; EE's own request id is kept under another name.
const FORWARDED_RESPONSE_HEADERS = [
  "etag",
  "retry-after",
  "x-correlation-id",
  "x-omega-operation-id",
  "x-omega-receipt-id",
  "x-omega-idempotency-key",
  "content-disposition",
];
const NONE_BODY_TOLERANCE = 1_024;

export class BodyRefused extends Error {}

function capFor(bodyClass, config) {
  if (bodyClass === "bundle") return config.maxBundleBytes;
  if (bodyClass === "json") return config.maxBodyBytes;
  return NONE_BODY_TOLERANCE;
}

/** Read the request body by its class; `undefined` when nothing is forwarded. */
export async function readBody(request, bodyClass, config) {
  if (request.method === "GET" || request.method === "HEAD") return undefined;
  const limit = capFor(bodyClass, config);
  const declared = Number(request.headers["content-length"]);
  if (Number.isFinite(declared) && declared > limit)
    throw new BodyRefused("request body too large");
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > limit) throw new BodyRefused("request body too large");
    chunks.push(chunk);
  }
  if (bodyClass === "none") return undefined;
  return size === 0 ? undefined : Buffer.concat(chunks);
}

/**
 * The URL EE will be sent, or `null` when a URL parser would turn the matched path into a
 * different one (a `#`, a character fetch re-encodes, a dot segment): what wall W2 matched
 * must be byte for byte what EE receives, or nothing is sent.
 */
export function upstreamUrl(remoteBaseUrl, path, search) {
  let url;
  try {
    url = new URL(`${remoteBaseUrl}${path}${search}`);
  } catch {
    return null;
  }
  const basePath = new URL(remoteBaseUrl).pathname.replace(/\/+$/, "");
  return url.pathname === `${basePath}${path}` && url.hash === ""
    ? url.href
    : null;
}

/**
 * Call EE and pipe its answer to the browser.
 *
 * @returns {Promise<{ status: number, error?: string, stream?: boolean }>}
 */
export async function forwardToEe({
  request,
  response,
  config,
  fetchImpl,
  context,
  route,
  path,
  search,
  token,
  body,
  headers,
  trackStream,
}) {
  const target = upstreamUrl(config.remoteBaseUrl, path, search);
  if (target === null) return { status: 400, error: "bad_request_target" };
  // A channel route (X-1) sends EE no assertion: its token travels in the query, alone.
  const upstreamHeaders = {
    accept: request.headers.accept || "application/json",
    ...(token ? { authorization: `Bearer ${token}` } : {}),
    ...context.outboundHeaders(),
  };
  for (const name of FORWARDED_REQUEST_HEADERS) {
    if (typeof request.headers[name] === "string")
      upstreamHeaders[name] = request.headers[name];
  }
  // Signed event metadata is data, never authority. Forward only on the exact ingress route.
  if (request.method === "POST" && route.path === SIGNED_TRIGGER_EVENT_PATH) {
    for (const name of [
      "x-omega-event-id",
      "x-omega-timestamp",
      "x-omega-trigger-signature",
    ]) {
      if (typeof request.headers[name] === "string")
        upstreamHeaders[name] = request.headers[name];
    }
  }
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, config.timeouts[route.timeout] ?? config.requestTimeoutMs);
  // A browser that leaves before EE answers cancels reads, streams and model calls. A write
  // carries on in EE, so its receipt is there for the retry with the same Idempotency-Key.
  const onEarlyClose = () => {
    if (!response.writableFinished) controller.abort();
  };
  if (route.timeout !== "write") response.once("close", onEarlyClose);
  let upstream;
  try {
    upstream = await context.time("ee", () =>
      fetchImpl(target, {
        method: request.method,
        redirect: "manual",
        headers: upstreamHeaders,
        body,
        signal: controller.signal,
      }),
    );
  } catch {
    const status = timedOut ? 504 : 502;
    return {
      status,
      error: timedOut ? "omega_remote_timeout" : "omega_remote_unreachable",
    };
  } finally {
    clearTimeout(timer);
    response.off("close", onEarlyClose);
  }
  const contentType =
    upstream.headers.get("content-type") || "application/json";
  const out = {};
  // Manual fetch exposes the real 3xx and all its headers. Only Location gets
  // special handling; the same response-header allowlist below applies to every status.
  // 304 without Location is a conditional response, not a redirect to follow.
  if (
    upstream.status >= 300 &&
    upstream.status < 400 &&
    (upstream.status !== 304 || upstream.headers.has("location"))
  ) {
    const location = publicRedirect(
      upstream.headers.get("location"),
      target,
      config,
    );
    if (location === null) {
      await upstream.body?.cancel().catch(() => {});
      return { status: 502, error: "omega_remote_bad_redirect" };
    }
    out.location = location;
  }
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) out[name] = value;
  }
  const eeRequestId = upstream.headers.get("x-request-id");
  if (eeRequestId && /^[\x21-\x7e]{1,128}$/.test(eeRequestId))
    out["x-upstream-request-id"] = eeRequestId;
  // fetch decodes compressed bodies, so a length is only true when nothing was encoded.
  const length = upstream.headers.get("content-length");
  if (length && !upstream.headers.get("content-encoding"))
    out["content-length"] = length;
  const isStream = contentType.includes("text/event-stream");
  context.status = upstream.status;
  // Tenant-scoped responses must not be retained by a browser, CDN or mesh cache.
  response.writeHead(upstream.status, {
    ...out,
    "content-type": contentType,
    "cache-control": "no-store",
    ...headers(),
  });
  if (!upstream.body || request.method === "HEAD") {
    response.end();
    return { status: upstream.status };
  }
  const stream = Readable.fromWeb(upstream.body);
  const onClose = () => stream.destroy();
  response.once("close", onClose);
  stream.once("close", () => response.off("close", onClose));
  if (isStream) trackStream?.(response, stream);
  await new Promise((resolve) => {
    stream.once("error", () => {
      response.destroy();
      resolve();
    });
    response.once("close", resolve);
    stream.pipe(response);
  });
  return { status: upstream.status, stream: isStream };
}
