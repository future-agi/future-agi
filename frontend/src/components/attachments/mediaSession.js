import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { registerUserStoreReset } from "src/utils/auth/userStoreResets";
import { mediaSessionHandleSchema, timestampSchema } from "./contract";

/**
 * Media-session handles for attachment playback (F01, traceAI #675).
 *
 * An exchange answers 204 with a non-secret session UUID and its fixed expiry.
 * The credential itself is only ever an HttpOnly cookie scoped to
 * /tracer/v1/attachment-media/{session}/, so sessions never overwrite each
 * other's cookies and a later exchange cannot orphan an earlier grant.
 *
 * Every resolve captures the tab's bearer and organization once and sends
 * BOTH requests of the pair under exactly that context, naming exactly the
 * handle its own exchange returned. A replayed request (the 401 refresh path
 * in src/utils/axios rewrites Authorization and re-dispatches) is refused
 * before it leaves the browser, so a handle is never sent under any bearer
 * other than the one it was issued to.
 *
 * Handles are reused, in memory only, within one bearer + organization and
 * only while they can still back a full grant. Nothing is persisted, an
 * expired record is never extended, and a context change drops everything.
 * Sharing one in-flight exchange is an optimization for this tab; separate
 * tabs exchange independently and still work.
 */

export const MEDIA_SESSION_HEADER = "X-Attachment-Media-Session";
// Axios exposes same-origin response headers with lowercase names.
const HANDLE_RESPONSE_HEADER = "x-attachment-media-session";
const EXPIRY_RESPONSE_HEADER = "x-attachment-media-expires-at";
// Documented fixed session lifetime; a reply never buys a longer reuse.
const SESSION_MAX_MS = 300000;
// A grant lasts at most 60 s and never outlives its session. Reuse a handle
// only while it can back a full grant plus that grant's own renewal margin.
export const HANDLE_MIN_REMAINING_MS = 90000;
// Per-tab issuance bound, well under the backend's 60/min per access row and
// organization (which every tab of that login shares).
export const MAX_EXCHANGES_PER_MINUTE = 10;
const MINUTE_MS = 60000;

class MediaSessionUnavailable extends Error {
  constructor(reason) {
    super(`Attachment media session unavailable: ${reason}`);
    this.name = "MediaSessionUnavailable";
  }
}

let context = null; // { authorization, organization, generation }
let generation = 0;
let reusable = null; // { handle, usableUntil, generation }
let pending = null; // { generation, controller, promise }
let issued = []; // exchange start times within the last minute
const listeners = new Set();

function readContext() {
  const common = axios?.defaults?.headers?.common || {};
  const bearer = common.Authorization;
  const organization = common["X-Organization-Id"];
  return {
    authorization:
      typeof bearer === "string" && /^Bearer \S+$/.test(bearer) ? bearer : null,
    organization:
      typeof organization === "string" && organization ? organization : null,
  };
}

function drop() {
  reusable = null;
  pending?.controller.abort();
  pending = null;
}

function notify() {
  queueMicrotask(() => listeners.forEach((listener) => listener()));
}

// The app's own source of truth is the bearer and organization every request
// carries. Any change, including a token refresh, starts a new generation.
function observe() {
  const { authorization, organization } = readContext();
  if (
    !context ||
    context.authorization !== authorization ||
    context.organization !== organization
  ) {
    const replaced = context !== null;
    generation += 1;
    context = Object.freeze({ authorization, organization, generation });
    drop();
    if (replaced) notify();
  }
  return context;
}

/** The tab's current bearer + organization, or null without a bearer. */
export function captureMediaContext() {
  const current = observe();
  return current.authorization ? current : null;
}

/** Opaque counter for hook keys; it never contains the bearer itself. */
export function mediaContextGeneration() {
  return observe().generation;
}

export function subscribeMediaContext(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function isCurrentMediaContext(captured) {
  return observe().generation === captured.generation;
}

/** Logout: forget every handle and settle no in-flight result. */
export function resetMediaSessions() {
  generation += 1;
  context = null;
  drop();
  issued = [];
  notify();
}
registerUserStoreReset("attachment-media", resetMediaSessions);

const headerValue = (headers, name) =>
  (typeof headers?.get === "function" ? headers.get(name) : headers?.[name]) ||
  null;

function pinned(captured) {
  // Runs on every dispatch, including a replay by a response interceptor.
  return function refuseReplacedContext(data, headers) {
    if (
      headerValue(headers, "Authorization") !== captured.authorization ||
      headerValue(headers, "X-Organization-Id") !== captured.organization ||
      !isCurrentMediaContext(captured)
    )
      throw new MediaSessionUnavailable("context replaced");
    return data;
  };
}

/**
 * Same-origin request config for one captured context. Explicit headers keep
 * the global defaults from changing mid-pair; the guard refuses any dispatch
 * whose bearer or organization differs from the captured one.
 */
export function pinnedRequestConfig(captured, signal, headers = {}) {
  const defaults = axios?.defaults?.transformRequest;
  return {
    // The media-session cookie must be established on the app's own origin.
    // Deployments must proxy these paths; never switch to provider/object URLs.
    baseURL: window.location.origin,
    signal,
    withCredentials: true,
    xsrfCookieName: "csrftoken",
    xsrfHeaderName: "X-CSRFToken",
    headers: {
      "Cache-Control": "no-store",
      Authorization: captured.authorization,
      ...(captured.organization
        ? { "X-Organization-Id": captured.organization }
        : {}),
      ...headers,
    },
    transformRequest: [
      pinned(captured),
      ...(Array.isArray(defaults) ? defaults : defaults ? [defaults] : []),
    ],
  };
}

function parseExchange(response, captured) {
  if (response?.status !== 204)
    throw new MediaSessionUnavailable("unexpected exchange");
  const handle = mediaSessionHandleSchema.safeParse(
    headerValue(response.headers, HANDLE_RESPONSE_HEADER),
  );
  const expiry = timestampSchema.safeParse(
    headerValue(response.headers, EXPIRY_RESPONSE_HEADER),
  );
  if (!handle.success || !expiry.success)
    throw new MediaSessionUnavailable("missing or malformed handle");
  const now = Date.now();
  const usableUntil = Math.min(Date.parse(expiry.data), now + SESSION_MAX_MS);
  if (usableUntil - now < HANDLE_MIN_REMAINING_MS)
    throw new MediaSessionUnavailable("near-expired handle");
  return Object.freeze({
    handle: handle.data,
    usableUntil,
    generation: captured.generation,
  });
}

function admitExchange() {
  const now = Date.now();
  issued = issued.filter((at) => now - at < MINUTE_MS);
  if (issued.length >= MAX_EXCHANGES_PER_MINUTE) return false;
  issued.push(now);
  return true;
}

async function exchange(captured, signal) {
  // The contract body is AttachmentEmptyRequest: `{}`, not an absent body,
  // which the development request-contract check rejects before sending.
  const response = await axios.post(
    apiPath("/tracer/v1/attachment-media-sessions"),
    {},
    pinnedRequestConfig(captured, signal),
  );
  const record = parseExchange(response, captured);
  // A reply that lands after a context change is never kept or used.
  if (!isCurrentMediaContext(captured))
    throw new MediaSessionUnavailable("context replaced");
  reusable = record;
  return record;
}

function startExchange(captured) {
  const controller = new AbortController();
  const entry = { generation: captured.generation, controller, promise: null };
  pending = entry;
  entry.promise = exchange(captured, controller.signal).finally(() => {
    if (pending === entry) pending = null;
  });
  // Waiters may all abort; the shared exchange still settles quietly.
  entry.promise.catch(() => {});
  return entry;
}

function whenSettled(promise, signal) {
  if (!signal) return promise;
  if (signal.aborted)
    return Promise.reject(new MediaSessionUnavailable("aborted"));
  return new Promise((resolve, reject) => {
    const abort = () => reject(new MediaSessionUnavailable("aborted"));
    signal.addEventListener("abort", abort, { once: true });
    promise.then(
      (value) => {
        signal.removeEventListener("abort", abort);
        resolve(value);
      },
      (error) => {
        signal.removeEventListener("abort", abort);
        reject(error);
      },
    );
  });
}

/**
 * A usable handle for exactly this captured context. `fresh` skips the
 * reusable record, e.g. after the gateway refused media on its session
 * (cookie evicted or session revoked). One caller's abort never cancels the
 * shared exchange; a failed exchange is never cached.
 */
export function acquireMediaSession(captured, signal, { fresh = false } = {}) {
  if (!isCurrentMediaContext(captured))
    return Promise.reject(new MediaSessionUnavailable("context replaced"));
  if (
    !fresh &&
    reusable &&
    reusable.generation === captured.generation &&
    reusable.usableUntil - Date.now() >= HANDLE_MIN_REMAINING_MS
  )
    return Promise.resolve(reusable);
  if (!pending) {
    // Refused issuance leaves the tab's working handle in place.
    if (!admitExchange())
      return Promise.reject(new MediaSessionUnavailable("issuance bound"));
    startExchange(captured);
  }
  // A refused session is not handed to anyone else meanwhile.
  if (fresh) reusable = null;
  return whenSettled(pending.promise, signal);
}

/** Stop reusing this exact record (its resolve failed or was foreign). */
export function discardMediaSession(record) {
  if (reusable === record) reusable = null;
}
