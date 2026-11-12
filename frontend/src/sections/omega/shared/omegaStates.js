/**
 * Shared vocabulary for every Omega tab (Deployments, Sandboxes, Console).
 *
 * A tab renders exactly one of these states; it never invents data for a
 * state that is not READY.
 */
export const OMEGA_STATE = Object.freeze({
  /** The session can mint assertions and the gateway answered. */
  READY: "ready",
  /** The FutureAGI session is gone; the host's login flow takes over. */
  SIGNED_OUT: "signed-out",
  /** Signed in, but this role/workspace may not use Omega (viewers, foreign scope, unmapped identity, operator route). */
  REFUSED: "refused",
  /** Omega is not reachable or not configured here (delegation off, gateway or EE down). */
  UNAVAILABLE: "unavailable",
  /** The addressed Omega resource does not exist (or is not visible to this tenant). */
  NOT_FOUND: "not-found",
  /** Any other refusal with a code and message from Omega (validation, conflict, ...). */
  ERROR: "error",
});

/**
 * A typed failure. `omegaJson` and the stream helper throw it; `omegaFetch`
 * returns the same fields as a plain `{ ok: false, ... }` result instead.
 */
export class OmegaError extends Error {
  constructor({
    state,
    status = null,
    code = null,
    reason = null,
    title = null,
    message = null,
    retryAfter = null,
  }) {
    super(message || defaultMessage(state));
    this.name = "OmegaError";
    this.state = state;
    this.status = status;
    this.code = code;
    /** The server's sub-reason (a route refusal's `reason`), when it sent one. */
    this.reason = reason;
    /** Future AGI's short heading for this failure (omegaReasons.js), or null. */
    this.title = title;
    /** Seconds the server asked the caller to wait before retrying, or null. */
    this.retryAfter = retryAfter;
  }
}

export function defaultMessage(state) {
  switch (state) {
    case OMEGA_STATE.SIGNED_OUT:
      return "Your Future AGI session has ended. Sign in again to continue.";
    case OMEGA_STATE.REFUSED:
      return "Your access in this workspace doesn't include this. Ask a workspace owner or admin if you need access.";
    case OMEGA_STATE.UNAVAILABLE:
      return "Omega isn't responding right now. Try again in a moment.";
    case OMEGA_STATE.NOT_FOUND:
      return "This Omega resource was not found.";
    default:
      return "Omega could not complete the request.";
  }
}

/**
 * A 401 the client could not recover from (it already tried a fresh key): the
 * sign-in was not confirmed. That is never the user's role, so a page says so
 * and offers a reload, which signs in again if the Future AGI session ended.
 */
export function isSignInUnconfirmed(error) {
  return error?.state === OMEGA_STATE.REFUSED && error?.status === 401;
}

/** Map a gateway/EE HTTP status to a state. Mint failures are mapped in sessionAssertion.js. */
export function stateForStatus(status) {
  if (status === 401 || status === 403) return OMEGA_STATE.REFUSED;
  if (status === 404) return OMEGA_STATE.NOT_FOUND;
  if (status === 502 || status === 503 || status === 504)
    return OMEGA_STATE.UNAVAILABLE;
  return OMEGA_STATE.ERROR;
}

const plainObject = (value) =>
  value && typeof value === "object" && !Array.isArray(value) ? value : null;
const firstString = (...values) =>
  values.find((value) => typeof value === "string" && value) || null;

/**
 * Pull `{ code, message, reason, detail }` out of a failure body:
 * - the gateway and EE route refusals: flat `{error: <code>, reason?}`;
 * - EE host-assertion refusals: `{error: <code>, message, retryable}`;
 * - EE workspace routes: `{schemaVersion, error: {code, message, detail?}}`,
 *   where `detail.reason` narrows the code (sandbox creator-only access sends
 *   `{reason: "not_creator", verb}`, role-table refusals send `{role, action}`);
 * - DRF: `{code, detail: <message>}`.
 * `detail` keeps only the two strings the copy table reads (`verb`, `role`).
 */
export function describeBody(body) {
  const record = plainObject(body) || {};
  const nested = plainObject(record.error);
  const detailBag = plainObject(nested?.detail) || plainObject(record.detail);
  const code = firstString(
    record.code,
    typeof record.error === "string" ? record.error : null,
    nested?.code,
  );
  const message = firstString(
    record.message,
    typeof record.detail === "string" ? record.detail : null,
    nested?.message,
  );
  const reason = firstString(record.reason, nested?.reason, detailBag?.reason);
  return {
    code,
    message,
    reason,
    detail: {
      verb: firstString(detailBag?.verb),
      role: firstString(detailBag?.role),
    },
  };
}
