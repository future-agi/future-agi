import { isCustomerSafe } from "./customerSafe";
import { OMEGA_STATE, defaultMessage } from "./omegaStates";

export { isCustomerSafe };

/**
 * Future AGI copy for every refusal on the Omega path (READINESS L07, PLT-02).
 *
 * The gateway, EE and the session exchange each answer with a stable code.
 * A page shows the copy below, never the server's own words for a 401 or a
 * 403: those name keys, servers and operators a customer has never heard of.
 * Other answers (a validation error, a conflict, "sandboxes are off") keep the
 * server's message only when it passes the COPY1 check, so no operator text
 * reaches a page by that route either.
 *
 * Codes come from four places, pinned in __tests__/fixtures/omega-refusal-codes.v1.json:
 * EE `managed-auth/host-assertion-codes.ts`, EE's sandbox and deployment
 * workspace routes (their 401 and 403 codes, `detail.reason` and verbs), the
 * gateway (`services/omega-gateway`) and Django's session exchange
 * (`accounts/views/omega_session.py`).
 */

const ACCESS = "Ask a workspace owner or admin if you need access.";

const SIGN_IN_UNCONFIRMED = {
  state: OMEGA_STATE.REFUSED,
  title: "Omega couldn't confirm your sign-in",
  message:
    "Future AGI couldn't confirm your sign-in with Omega. Reload the page and try again.",
};
const ROLE_FLOOR = {
  state: OMEGA_STATE.REFUSED,
  title: "Your role doesn't allow this",
  message: `Your role in this workspace doesn't allow this action. ${ACCESS}`,
};
const OUT_OF_REACH = {
  state: OMEGA_STATE.REFUSED,
  title: "Not available from Future AGI",
  message:
    "This part of Omega can't be opened from Future AGI. Nothing was created or changed.",
};
const OTHER_WORKSPACE = {
  state: OMEGA_STATE.REFUSED,
  title: "This belongs to another workspace",
  message:
    "This item isn't part of the workspace you have selected, so it can't be opened here.",
};
const SIGN_IN_THROTTLED = {
  state: OMEGA_STATE.ERROR,
  title: "Too many sign-ins",
  message:
    "Your account opened Omega too many times in the last minute. Try again in {seconds}.",
  retry: true,
};
const NOT_RESPONDING = {
  state: OMEGA_STATE.UNAVAILABLE,
  title: "Omega isn't responding",
  message: "Omega isn't responding right now. Try again in a moment.",
  retry: true,
};

const NOT_ALLOWED = {
  state: OMEGA_STATE.REFUSED,
  title: "Not allowed",
  message: `Your access in this workspace doesn't include this. ${ACCESS}`,
};
const ADMINS_ONLY = {
  state: OMEGA_STATE.REFUSED,
  title: "Only workspace owners and admins can do this",
  message: `Only workspace owners and admins can do this. ${ACCESS}`,
};

/**
 * EE's creator-only sandbox access (`workspace/sandboxes/access/creator-only.ts`,
 * owner decision D11): 403 `role_forbidden` with `detail {reason: "not_creator",
 * verb}`. Owners and admins may view and delete any sandbox but never operate
 * one they did not create, so the reader may well be an admin: this copy says
 * who can, never "only owners and admins".
 */
const CREATOR_ONLY = {
  state: OMEGA_STATE.REFUSED,
  title: "Only the sandbox's creator can do this",
  message: "Only the member who created this sandbox can do this.",
  byVerb: {
    view: {
      state: OMEGA_STATE.REFUSED,
      title: "This sandbox is private to its creator",
      message:
        "Only the member who created this sandbox, and workspace owners and admins, can open it.",
    },
    operate: {
      state: OMEGA_STATE.REFUSED,
      title: "Only the sandbox's creator can do this",
      message:
        "Only the member who created this sandbox can run commands in it, change its files or change its state. Workspace owners and admins can still open it and delete it.",
    },
    destroy: {
      state: OMEGA_STATE.REFUSED,
      title: "Only its creator or an admin can delete this sandbox",
      message:
        "Only the member who created this sandbox, or a workspace owner or admin, can delete it.",
    },
  },
};

/** Route refusals carry a `reason` (EE `HOST_ROUTE_REFUSAL_REASONS`, gateway `decideRoute`). */
const ROUTE_REASONS = {
  role_below_floor: ROLE_FLOOR,
  scope_pivot: OTHER_WORKSPACE,
  path_refused: OUT_OF_REACH,
  route_not_in_manifest: OUT_OF_REACH,
};

/**
 * One entry per code. `stale`: a fresh one-use key can succeed, so the client
 * retries once at once. `wait`: the server asked for a short pause first.
 * `{seconds}` in a message is filled from Retry-After.
 */
export const REFUSAL_COPY = Object.freeze({
  // EE host-assertion refusals.
  assertion_invalid: SIGN_IN_UNCONFIRMED,
  assertion_expired: { ...SIGN_IN_UNCONFIRMED, stale: true },
  assertion_replayed: { ...SIGN_IN_UNCONFIRMED, stale: true },
  identity_not_connected: {
    state: OMEGA_STATE.REFUSED,
    title: "Omega isn't connected for your account",
    message: `Your Future AGI account isn't connected to Omega in this workspace. ${ACCESS}`,
  },
  role_out_of_date: {
    state: OMEGA_STATE.REFUSED,
    title: "Your role just changed",
    message:
      "Your role in this workspace changed a moment ago. Reload the page to continue with your current role.",
    stale: true,
  },
  verification_unavailable: {
    state: OMEGA_STATE.UNAVAILABLE,
    title: "Sign-in checks are briefly unavailable",
    message:
      "Omega couldn't check your sign-in just now. Try again in a few seconds.",
    retry: true,
    wait: true,
  },
  host_delegation_scope_denied: { ...OUT_OF_REACH, byReason: ROUTE_REASONS },
  host_delegation_scope_unavailable: OUT_OF_REACH,
  // EE before reason codes: a reasonless 401 for every failed check.
  unauthorized: {
    state: OMEGA_STATE.REFUSED,
    title: "Omega couldn't confirm your access",
    message:
      "Omega couldn't confirm your Future AGI access to this workspace. Reload the page to try again. If it keeps happening, ask a workspace owner or admin for access.",
  },
  // EE workspace routes the tabs reach (sandboxes, deployments). The copy
  // claims only what the body proves: role-table refusals name the caller's
  // role in `detail.role` (owners and admins pass them), creator-only access
  // names `detail.reason` and `detail.verb`, anything else is plain "not allowed".
  role_forbidden: {
    ...NOT_ALLOWED,
    byReason: { not_creator: CREATOR_ONLY },
    byRole: { member: ADMINS_ONLY, viewer: ADMINS_ONLY },
  },
  // EE workspace routes: no current signed-in workspace behind the request.
  unauthenticated: SIGN_IN_UNCONFIRMED,
  // EE's credential list (deployments): a guest, unproven workspace authority,
  // or a change that needs an admin. The body does not say which.
  forbidden: NOT_ALLOWED,
  // Gateway refusals.
  omega_assertion_rejected: {
    ...SIGN_IN_UNCONFIRMED,
    byReason: {
      expired_assertion: { ...SIGN_IN_UNCONFIRMED, stale: true },
      replayed_assertion: { ...SIGN_IN_UNCONFIRMED, stale: true },
    },
  },
  omega_assertion_required: SIGN_IN_UNCONFIRMED,
  omega_session_unavailable: SIGN_IN_UNCONFIRMED,
  omega_scope_mismatch: {
    ...OTHER_WORKSPACE,
    message:
      "The workspace selected in Future AGI changed. Reload the page to continue in the selected workspace.",
    byReason: { role_below_floor: ROLE_FLOOR, scope_pivot: OTHER_WORKSPACE },
  },
  omega_role_not_permitted: ROLE_FLOOR,
  omega_viewer_not_admitted: {
    state: OMEGA_STATE.REFUSED,
    title: "Viewers can't use Omega",
    message:
      "Viewers can't use Omega in this workspace. Ask a workspace owner or admin for member access.",
  },
  route_not_allowed: { ...OUT_OF_REACH, byReason: ROUTE_REASONS },
  origin_not_allowed: {
    state: OMEGA_STATE.REFUSED,
    title: "Omega can't be reached from this address",
    message:
      "This page can't reach Omega from its current address. Open Future AGI from its usual address and try again.",
  },
  omega_rate_limited: {
    state: OMEGA_STATE.ERROR,
    title: "Too many requests",
    message:
      "This workspace is sending requests to Omega faster than its limit. Try again in {seconds}.",
    retry: true,
    byReason: { exchange_throttled: SIGN_IN_THROTTLED },
  },
  omega_rate_limit_unavailable: {
    state: OMEGA_STATE.UNAVAILABLE,
    title: "Omega is pausing changes",
    message:
      "Omega is pausing new changes for a moment. Reading still works. Try again in a few seconds.",
    retry: true,
    wait: true,
  },
  omega_issuer_unavailable: {
    ...NOT_RESPONDING,
    title: "Future AGI couldn't open Omega",
    message:
      "Future AGI couldn't start an Omega session just now. Try again in a moment.",
  },
  omega_gateway_draining: {
    ...NOT_RESPONDING,
    title: "Omega is restarting",
    message: "Omega is restarting. Try again in a moment.",
    wait: true,
  },
  omega_remote_unreachable: NOT_RESPONDING,
  omega_remote_timeout: {
    ...NOT_RESPONDING,
    message: "Omega took too long to answer. Try again in a moment.",
  },
  omega_gateway_failure: NOT_RESPONDING,
  network_error: {
    ...NOT_RESPONDING,
    title: "Omega can't be reached",
    message:
      "Your browser couldn't reach Omega. Check your connection and try again.",
  },
  request_body_too_large: {
    state: OMEGA_STATE.ERROR,
    title: "Too large to send",
    message: "That is larger than Omega accepts in one request.",
  },
  // Session exchange (Django) answers. A throttled exchange stays UNAVAILABLE,
  // the state it had before reason codes, so a tab keeps its "Omega isn't
  // responding" heading above the wait; the gateway's own 429 stays ERROR.
  session_exchange_throttled: {
    ...SIGN_IN_THROTTLED,
    state: OMEGA_STATE.UNAVAILABLE,
  },
  session_exchange_unavailable: {
    state: OMEGA_STATE.UNAVAILABLE,
    title: "Omega isn't available here",
    message: "Omega isn't available in this workspace right now.",
    retry: true,
  },
  session_exchange_refused: {
    state: OMEGA_STATE.REFUSED,
    title: "Omega isn't available to you here",
    message: `Future AGI couldn't confirm your access to this workspace. Reload the page. ${ACCESS}`,
  },
  // Live streams (omegaStream.js).
  stream_interrupted: {
    state: OMEGA_STATE.UNAVAILABLE,
    title: "The live view stopped",
    message:
      "The live connection to Omega dropped and couldn't reopen. Anything already started may still be running.",
    retry: true,
  },
  stream_cut: {
    state: OMEGA_STATE.UNAVAILABLE,
    title: "The live view stopped",
    message:
      "The live connection to Omega closed before this finished. Anything already started may still be running.",
    retry: true,
  },
  // Domain codes whose server message may be replaced by the COPY1 check.
  sandboxes_unavailable: {
    state: OMEGA_STATE.UNAVAILABLE,
    title: "Sandboxes aren't turned on",
    message: "Sandboxes aren't available right now.",
  },
  deployments_unavailable: {
    state: OMEGA_STATE.UNAVAILABLE,
    title: "Deployments aren't turned on",
    message: "Deployments aren't turned on for this workspace.",
  },
  // EE `project-deployments/secrets.ts`: a secret reference names no active
  // vault credential, or more than one. Its message names the customer's own
  // reference (DATABASE_URL), so it is kept when the rest of it passes COPY1
  // (`keepsNames`); otherwise this copy, which still says what to do.
  secret_unavailable: {
    state: OMEGA_STATE.ERROR,
    title: "A secret this deployment needs isn't ready",
    message:
      "A secret this deployment references isn't available in this workspace's vault. Check it under Credentials, then deploy again.",
    keepsNames: true,
  },
  // EE `project-deployments`: the bundle's package.json depends on a package
  // the runtime does not provide. EE's own words describe its roadmap ("cannot
  // install packages yet ... today"), so this copy always replaces them
  // (`ownCopy`): it says what deploys and what to change.
  dependencies_unsupported: {
    state: OMEGA_STATE.ERROR,
    title: "This project has package dependencies",
    message:
      "Deployments run projects whose only package dependencies are @omega/* packages, which the runtime provides. Remove the other dependencies from package.json, then deploy again.",
    ownCopy: true,
  },
});

/** Copy for a status with no known code. 401 and 403 never fall back to server text. */
const BY_STATUS = { 401: SIGN_IN_UNCONFIRMED, 403: NOT_ALLOWED };

/** "12 seconds", "1 minute", or "a few seconds" when the server gave no time. */
export function formatWait(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return "a few seconds";
  const whole = Math.ceil(value);
  if (whole < 60) return whole === 1 ? "1 second" : `${whole} seconds`;
  const minutes = Math.ceil(whole / 60);
  return minutes === 1 ? "1 minute" : `${minutes} minutes`;
}

const pick = (table, key) =>
  table && typeof key === "string" && Object.hasOwn(table, key)
    ? table[key]
    : null;

/**
 * The table entry for a code, or null. It narrows by the body's `reason`, then
 * by `detail.verb`, then by `detail.role` (describeBody in omegaStates.js).
 */
export function refusalEntry(code, reason, detail = null) {
  const entry = pick(REFUSAL_COPY, code);
  if (!entry) return null;
  const byReason = pick(entry.byReason, reason) || entry;
  const byVerb = pick(byReason.byVerb, detail?.verb) || byReason;
  return pick(byVerb.byRole, detail?.role) || byVerb;
}

/**
 * Seconds to wait before a retry: the `Retry-After` header (delta seconds),
 * else the gateway's `retryAfterSeconds`, else null.
 */
export function retryAfterSeconds(headerValue, body) {
  const header = Number.parseInt(String(headerValue ?? ""), 10);
  if (Number.isFinite(header) && header >= 0) return header;
  const fromBody = Number(body?.retryAfterSeconds);
  return Number.isFinite(fromBody) && fromBody >= 0 ? fromBody : null;
}

/**
 * The customer-facing fields of a failure: `{ state, title, message, retry }`.
 * `fallbackState` is the state the HTTP status maps to; a known code wins.
 */
export function customerCopy({
  status = null,
  code = null,
  reason = null,
  detail = null,
  serverMessage = null,
  fallbackState,
  retryAfter = null,
}) {
  const entry = refusalEntry(code, reason, detail);
  const authStatus = status === 401 || status === 403;
  const base = entry ||
    (authStatus ? BY_STATUS[status] : null) || {
      state: fallbackState,
      title: null,
      message: null,
    };
  const state = base.state || fallbackState;
  let message;
  if (
    authStatus ||
    base.ownCopy ||
    !isCustomerSafe(serverMessage, { names: Boolean(base.keepsNames) })
  ) {
    message = base.message || defaultMessage(state);
  } else {
    message = serverMessage;
  }
  if (message.includes("{seconds}"))
    message = message.replace("{seconds}", formatWait(retryAfter));
  return {
    state,
    title: base.title || null,
    message,
    retry: Boolean(base.retry),
  };
}

/** True when a fresh one-use key could succeed where this answer failed. */
export function isStaleAssertion(status, code, reason, body) {
  if (status !== 401 && status !== 403) return false;
  const entry = refusalEntry(code, reason);
  if (entry?.stale) return true;
  const reasonEntry = refusalEntry(reason, null);
  if (reasonEntry?.stale) return true;
  return status === 401 && body?.retryable === true && code !== "unauthorized";
}

/** True when the server asked for a short pause and a retry can then succeed. */
export function isWaitRetry(status, code, reason) {
  return status === 503 && Boolean(refusalEntry(code, reason)?.wait);
}
