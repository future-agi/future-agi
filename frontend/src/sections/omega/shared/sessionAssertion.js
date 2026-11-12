import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";

import { OMEGA_STATE, OmegaError, describeBody } from "./omegaStates";
import { customerCopy, retryAfterSeconds } from "./omegaReasons";

/**
 * Customer assertions come from FutureAGI's own backend:
 * POST /accounts/omega/session-exchange/ with {"audience":"omega-squad"}.
 * The host axios instance supplies the browser session (Authorization) and
 * the selected X-Organization-Id / X-Workspace-Id, and runs the host's normal
 * token-refresh flow on 401.
 *
 * Every assertion is one-use and lives 45 seconds, so nothing here caches or
 * reuses one: each Omega request mints its own. The token is held only in the
 * calling closure; it is never stored, logged or put in a URL.
 */
export const OMEGA_AUDIENCE = "omega-squad";
export const SESSION_EXCHANGE_PATH = apiPath(
  "/accounts/omega/session-exchange/",
);

/**
 * Status, body and headers of a failed exchange, whichever way it was thrown.
 * The host axios instance (src/utils/axios.js) rejects with the response body
 * spread flat plus `statusCode` and `transportCode`, and drops the headers: a
 * Django refusal arrives as `{status: false, code, detail, message, ...,
 * statusCode: 403}`, so `status` is the envelope's flag, never the HTTP status.
 * A plain axios error (an injected `post`) keeps `response`.
 */
export function exchangeAnswer(error) {
  const response =
    error?.response && typeof error.response === "object"
      ? error.response
      : null;
  if (response && Number.isInteger(response.status)) {
    return {
      status: response.status,
      body: response.data ?? null,
      headers: response.headers ?? null,
    };
  }
  const status = Number.isInteger(error?.statusCode) ? error.statusCode : null;
  return {
    status,
    body: status === null ? null : error,
    headers: null,
  };
}

/**
 * The exchange's refusals in Future AGI copy. 401: the browser session ended
 * (the host's login flow takes over). 403: a viewer, no compatible role, or a
 * stale or foreign workspace selection. 429: too many sign-ins this minute.
 * 503 or no answer: Omega is not available on this backend right now.
 */
function mintFailure({ status, body, headers }) {
  const { code } = describeBody(body);
  // Through the host instance the Retry-After header is gone (and a
  // cross-origin API does not expose it), so the wait comes from the body's
  // `retryAfterSeconds` when the exchange sends one, else "a few seconds".
  const header =
    typeof headers?.get === "function"
      ? headers.get("retry-after")
      : headers?.["retry-after"];
  const retryAfter = retryAfterSeconds(header, body);
  if (status === 401) {
    return new OmegaError({
      state: OMEGA_STATE.SIGNED_OUT,
      status,
      code: code || "session_ended",
    });
  }
  let mapped;
  if (status === 403)
    mapped =
      code === "omega_viewer_not_admitted" ? code : "session_exchange_refused";
  else if (status === 429) mapped = "session_exchange_throttled";
  else mapped = "session_exchange_unavailable";
  const copy = customerCopy({
    status,
    code: mapped,
    fallbackState: OMEGA_STATE.UNAVAILABLE,
    retryAfter,
  });
  return new OmegaError({
    state: copy.state,
    status: status ?? null,
    code: mapped,
    title: copy.title,
    message: copy.message,
    retryAfter,
  });
}

/**
 * @param {{ post?: (url: string, body: object) => Promise<{ data: object }>, now?: () => number }} [deps]
 * @returns {() => Promise<{ token: string, expiresAt: number }>}
 */
export function createAssertionMinter({
  post = (url, body) => axios.post(url, body),
  now = () => Date.now(),
} = {}) {
  return async function mintAssertion() {
    let response;
    try {
      response = await post(SESSION_EXCHANGE_PATH, {
        audience: OMEGA_AUDIENCE,
      });
    } catch (error) {
      throw mintFailure(exchangeAnswer(error));
    }
    const data = response?.data || {};
    if (
      data.tokenType !== "Bearer" ||
      typeof data.accessToken !== "string" ||
      !data.accessToken.startsWith("omega_delegate_")
    ) {
      throw new OmegaError({
        state: OMEGA_STATE.UNAVAILABLE,
        code: "invalid_session_exchange_response",
        title: "Future AGI couldn't open Omega",
        message:
          "Future AGI couldn't start an Omega session just now. Try again in a moment.",
      });
    }
    const lifetime = Number.isInteger(data.expiresIn) ? data.expiresIn : 0;
    return { token: data.accessToken, expiresAt: now() + lifetime * 1000 };
  };
}
