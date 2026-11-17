import { createAssertionMinter } from "./sessionAssertion";
import {
  OMEGA_STATE,
  OmegaError,
  describeBody,
  stateForStatus,
} from "./omegaStates";
import {
  customerCopy,
  isStaleAssertion,
  isWaitRetry,
  retryAfterSeconds,
} from "./omegaReasons";

/**
 * Data client for the Omega customer gateway, mounted same-origin at
 * /omega/api (services/omega-gateway). Each request carries a freshly minted
 * customer assertion and nothing else: no FutureAGI token, no cookies, no
 * workspace headers (the assertion carries the scope).
 */
export const DEFAULT_OMEGA_API_BASE = "/omega/api";
export const MALFORMED_RESPONSE = "malformed_response";
// Distinct from every valid JSON value, including a JSON string or null.
const MALFORMED_BODY = Symbol("malformed_body");
const BASE_RE = /^(?:\/[A-Za-z0-9_-]+)+$/;
const PATH_RE = /^\/[A-Za-z0-9._~!$&'()*+,;=:@%/?-]*$/;
// A 503 that asked for a pause (verification_unavailable, a draining gateway)
// is retried once after at most this long; a longer Retry-After is shown.
const MAX_WAIT_RETRY_MS = 3000;

export function resolveOmegaApiBase(value) {
  return typeof value === "string" && BASE_RE.test(value)
    ? value
    : DEFAULT_OMEGA_API_BASE;
}

function checkPath(path) {
  if (
    typeof path !== "string" ||
    !PATH_RE.test(path) ||
    path.startsWith("//")
  ) {
    throw new TypeError(
      `omegaFetch expects a gateway route such as "/workspace/deployments", got ${JSON.stringify(path)}`,
    );
  }
  if (
    path
      .split("?")[0]
      .split("/")
      .some((segment) => segment === "." || segment === "..")
  ) {
    throw new TypeError("omegaFetch routes must not contain . or .. segments");
  }
}

function withQuery(path, query) {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "")
      params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `${path}${path.includes("?") ? "&" : "?"}${text}` : path;
}

async function readBody(response) {
  if (response.status === 204) return null;
  const text = await response.text();
  if (!text) return null;
  if ((response.headers.get("content-type") || "").includes("json")) {
    try {
      return JSON.parse(text);
    } catch {
      return response.ok ? MALFORMED_BODY : { message: text };
    }
  }
  return response.ok ? MALFORMED_BODY : text;
}

function isRetrySafe(method, headers) {
  return (
    method === "GET" ||
    Boolean(headers["Idempotency-Key"] || headers["idempotency-key"])
  );
}

/**
 * @param {{ basePath?: string, fetchImpl?: typeof fetch, mintAssertion?: () => Promise<{token: string}> }} [deps]
 */
export function createOmegaClient({
  basePath = resolveOmegaApiBase(import.meta.env?.VITE_OMEGA_API_BASE),
  fetchImpl = (...args) => globalThis.fetch(...args),
  mintAssertion = createAssertionMinter(),
  sleep = pause,
} = {}) {
  /**
   * omegaFetch(path, init) never throws for HTTP or network failures; it
   * resolves `{ ok: true, status, data, headers, response }` or
   * `{ ok: false, state, status, code, reason, title, message, retryAfter }`.
   * Aborts reject.
   *
   * `message` and `title` are always Future AGI copy (omegaReasons.js): a 401
   * or 403 never carries the server's words, and any other server message is
   * kept only when it passes the COPY1 check. `retryAfter` is in seconds.
   *
   * A stale one-use key (expired, replayed, or minted before a role change)
   * is replaced once at once; a 503 that asks for a short pause is retried
   * once after it. Both only for GET or a request with an Idempotency-Key.
   *
   * init: fetch options plus `json` (body object), `query` (object),
   * `raw` (resolve with the unread Response for streams), `onSend` (called
   * each time the request goes out, after its key is minted: a stream times
   * its first byte from there, not from before a slow mint) and `onAnswer`
   * (called with each Response as it arrives, before any retry pause or
   * re-mint: a stream stops timing a request that has been answered).
   */
  async function omegaFetch(path, init = {}) {
    checkPath(path);
    const {
      json,
      query,
      raw,
      onSend,
      onAnswer,
      headers: extraHeaders,
      ...rest
    } = init;
    const method = String(rest.method || "GET").toUpperCase();
    const headers = {
      Accept: raw ? "text/event-stream" : "application/json",
      ...(extraHeaders || {}),
    };
    let body = rest.body;
    if (json !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(json);
    }
    const url = `${basePath}${withQuery(path, query)}`;
    let retried = false;
    for (;;) {
      let assertion;
      try {
        assertion = await mintAssertion();
      } catch (error) {
        if (error instanceof OmegaError)
          return { ok: false, ...fieldsOf(error) };
        throw error;
      }
      let response;
      onSend?.();
      try {
        response = await fetchImpl(url, {
          ...rest,
          method,
          body,
          credentials: "omit",
          headers: { ...headers, Authorization: `Bearer ${assertion.token}` },
        });
      } catch (error) {
        if (error?.name === "AbortError") throw error;
        return failure({
          status: null,
          code: "network_error",
          fallbackState: OMEGA_STATE.UNAVAILABLE,
        });
      }
      onAnswer?.(response);
      if (response.ok) {
        if (raw)
          return {
            ok: true,
            status: response.status,
            headers: response.headers,
            response,
          };
        const data = await readBody(response);
        if (data === MALFORMED_BODY)
          return failure({
            status: response.status,
            code: MALFORMED_RESPONSE,
            fallbackState: OMEGA_STATE.ERROR,
          });
        return {
          ok: true,
          status: response.status,
          data,
          headers: response.headers,
          response,
        };
      }
      const parsed = await readBody(response).catch(() => null);
      const { code, message, reason, detail } = describeBody(parsed);
      const { status } = response;
      const retryAfter = retryAfterSeconds(
        response.headers.get("retry-after"),
        parsed,
      );
      const safe = !retried && isRetrySafe(method, headers);
      if (safe && isStaleAssertion(status, code, reason, parsed)) {
        retried = true;
        continue;
      }
      if (
        safe &&
        isWaitRetry(status, code, reason) &&
        (retryAfter ?? 1) * 1000 <= MAX_WAIT_RETRY_MS
      ) {
        retried = true;
        await sleep((retryAfter ?? 1) * 1000, rest.signal);
        if (rest.signal?.aborted) throw abortError();
        continue;
      }
      return failure({
        status,
        code,
        reason,
        detail,
        serverMessage: message,
        fallbackState: stateForStatus(status),
        retryAfter,
      });
    }
  }

  /** Resolve with data or throw an OmegaError (for react-query and friends). */
  async function omegaJson(path, init) {
    const result = await omegaFetch(path, init);
    if (!result.ok) throw new OmegaError(result);
    return result.data;
  }

  return { omegaFetch, omegaJson, basePath };
}

/** A typed `{ ok: false }` result whose words are always Future AGI copy. */
export function failure({
  status = null,
  code = null,
  reason = null,
  detail = null,
  serverMessage = null,
  fallbackState,
  retryAfter = null,
}) {
  const copy = customerCopy({
    status,
    code,
    reason,
    detail,
    serverMessage,
    fallbackState,
    retryAfter,
  });
  return {
    ok: false,
    state: copy.state,
    status,
    code: code || null,
    reason: reason || null,
    title: copy.title,
    message: copy.message,
    retryAfter: retryAfter ?? null,
  };
}

/** A missing or non-array list is a failed response, not an empty result. */
export function requireList(body, key) {
  if (Array.isArray(body?.[key])) return body[key];
  throw new OmegaError(
    failure({
      code: MALFORMED_RESPONSE,
      fallbackState: OMEGA_STATE.ERROR,
    }),
  );
}

function fieldsOf(error) {
  return {
    state: error.state,
    status: error.status,
    code: error.code,
    reason: error.reason ?? null,
    title: error.title ?? null,
    message: error.message,
    retryAfter: error.retryAfter ?? null,
  };
}

function abortError() {
  return new DOMException("The operation was aborted.", "AbortError");
}

function pause(ms, signal) {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

const defaultClient = createOmegaClient();

/** omegaFetch(path, init) against /omega/api with a fresh customer assertion per request. */
export const omegaFetch = defaultClient.omegaFetch;
export const omegaJson = defaultClient.omegaJson;
