// curl text for one request a Sandboxes or Deployments page can make through
// the Omega gateway (parity P11, gap 1(c) and 10(c)).
//
// A request is a plain descriptor (catalog.js builds them from the same route
// helpers and body builders the tabs' clients use), so a snippet cannot drift
// from what the page itself sends. The caller's personal API key travels in
// the X-Api-Key / X-Secret-Key headers only (the gateway refuses a key in a
// URL with 400 credential_in_query, SB-W2-KEYS CLAIMS section 3), in one of
// three forms:
// - MASKED: what the page shows before the person reveals the key;
// - ENV: "$FI_API_KEY" / "$FI_SECRET_KEY", what Copy gives before a reveal
//   (the variable names the platform SDK reads, sdk-split PLAN section 2.6);
// - FULL: the key itself, only after an explicit reveal.

export const KEY_MODE = Object.freeze({
  MASKED: "masked",
  ENV: "env",
  FULL: "full",
});

export const KEY_ENV = Object.freeze({
  API: "FI_API_KEY",
  SECRET: "FI_SECRET_KEY",
});

const KEY_VALUE = /^[A-Za-z0-9_-]{8,256}$/;
const SCOPE_ID = /^[A-Za-z0-9-]{1,64}$/;
const BASE_URL = /^https?:\/\/[^\s/?#'"\\]+(?:\/[A-Za-z0-9_-]+)+$/;
const ROUTE_PATH = /^\/workspace\/[A-Za-z0-9._~!$&()*+,;=:@%/?-]*$/;
const MASK = "\u2022".repeat(8);

/** One argument for a POSIX shell: single-quoted, a ' inside closed and reopened. */
export function shellQuote(value) {
  return `'${String(value).replace(/'/g, "'\\''")}'`;
}

/** `••••••••a1b2`: enough to tell two keys apart, never enough to use one. */
export function maskValue(value) {
  const text = String(value ?? "");
  return `${MASK}${text.length > 12 ? text.slice(-4) : ""}`;
}

/** True for a key pair this module will put in a snippet. */
export function isUsableKey(key) {
  return Boolean(
    key &&
      KEY_VALUE.test(key.apiKey ?? "") &&
      KEY_VALUE.test(key.secretKey ?? ""),
  );
}

function keyHeaders(key, mode) {
  if (mode === KEY_MODE.ENV || !isUsableKey(key)) {
    return [
      `-H "X-Api-Key: $${KEY_ENV.API}"`,
      `-H "X-Secret-Key: $${KEY_ENV.SECRET}"`,
    ];
  }
  if (mode === KEY_MODE.FULL) {
    return [
      `-H ${shellQuote(`X-Api-Key: ${key.apiKey}`)}`,
      `-H ${shellQuote(`X-Secret-Key: ${key.secretKey}`)}`,
    ];
  }
  // The API key's last four tell two keys apart; the secret shows none of itself.
  return [
    `-H ${shellQuote(`X-Api-Key: ${maskValue(key.apiKey)}`)}`,
    `-H ${shellQuote(`X-Secret-Key: ${MASK}`)}`,
  ];
}

/** The route with its query, as the tab's client writes it (omegaClient withQuery). */
export function routeWithQuery(path, query) {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [name, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "")
      params.set(name, String(value));
  }
  const text = params.toString();
  return text ? `${path}${path.includes("?") ? "&" : "?"}${text}` : path;
}

/** The exact body bytes the request sends, or null when it sends none. */
export function bodyText(request) {
  return request.json === undefined ? null : JSON.stringify(request.json);
}

/**
 * What makes two requests one request to the stack: the method, the route with
 * its query, and the body. An Idempotency-Key belongs to exactly one: EE
 * replays a key sent again with the same request and refuses it with another
 * (409 idempotency_conflict, project-deployments record-ops.ts isReplay).
 */
export function requestFingerprint(request) {
  return JSON.stringify([
    request.method.toUpperCase(),
    routeWithQuery(request.path, request.query),
    bodyText(request),
  ]);
}

/**
 * The curl command for `request` against `baseUrl` (the stack's own origin and
 * gateway mount, target.js). `scope` adds the page's organization and
 * workspace selection; `idempotencyKey` is required for a request that takes
 * one. A request with `bodyFile` reads its body from that file (a deployment
 * bundle is too large to inline), which the page offers as a download of
 * exactly bodyText(request).
 */
export function toCurl(
  request,
  { baseUrl, key = null, mode = KEY_MODE.ENV, scope = {}, idempotencyKey } = {},
) {
  if (!BASE_URL.test(baseUrl ?? "")) {
    throw new TypeError(`Not a gateway base URL: ${JSON.stringify(baseUrl)}`);
  }
  const target = routeWithQuery(request.path, request.query);
  if (!ROUTE_PATH.test(target) || target.split("?")[0].includes("//")) {
    throw new TypeError(`Not a gateway route: ${JSON.stringify(target)}`);
  }
  if (request.idempotent && !idempotencyKey) {
    throw new TypeError(`${request.id} needs an Idempotency-Key.`);
  }
  const method = request.method.toUpperCase();
  const first = ["curl", "-sS"];
  if (request.stream) first.push("-N");
  if (method !== "GET") first.push("-X", method);
  first.push(shellQuote(`${baseUrl}${target}`));

  const lines = [first.join(" "), ...keyHeaders(key, mode)];
  if (scope.organizationId && SCOPE_ID.test(scope.organizationId)) {
    lines.push(
      `-H ${shellQuote(`X-Organization-Id: ${scope.organizationId}`)}`,
    );
  }
  if (scope.workspaceId && SCOPE_ID.test(scope.workspaceId)) {
    lines.push(`-H ${shellQuote(`X-Workspace-Id: ${scope.workspaceId}`)}`);
  }
  lines.push(
    `-H ${shellQuote(`Accept: ${request.stream ? "text/event-stream" : "application/json"}`)}`,
  );
  const body = bodyText(request);
  if (body !== null)
    lines.push(`-H ${shellQuote("Content-Type: application/json")}`);
  if (request.idempotent) {
    lines.push(`-H ${shellQuote(`Idempotency-Key: ${idempotencyKey}`)}`);
  }
  if (body !== null) {
    lines.push(
      request.bodyFile
        ? `--data-binary ${shellQuote(`@${request.bodyFile}`)}`
        : `--data-binary ${shellQuote(body)}`,
    );
  }
  return lines.join(" \\\n  ");
}
