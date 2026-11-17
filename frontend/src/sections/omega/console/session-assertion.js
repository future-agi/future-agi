import axios from "src/utils/axios";

// The keys the Console frame receives: customer Omega assertions minted by
// Future AGI's own backend (POST /accounts/omega/session-exchange/ with
// {"audience":"omega-squad"}, plus "count" for a batch). The host axios
// instance adds the browser session and the selected organization and
// workspace, and runs the host's own refresh-or-sign-in flow on 401.
//
// The Tabs' shared minter (src/sections/omega/shared/sessionAssertion.js)
// mints one key per call, which is what Sandboxes and Deployments need. The
// Console asks for several at once (CP P7, decision D2: `embed.hello` with
// `want`), so this file keeps its own minter, with the same checks, the same
// path and the same failure states, plus the batch.
//
// Every assertion is one-use and short-lived, so nothing here caches one.
export const OMEGA_AUDIENCE = "omega-squad";
export const SESSION_EXCHANGE_PATH = "/accounts/omega/session-exchange/";
// The most keys one exchange call may mint (MAX_BATCH in
// futureagi/accounts/omega_session.py; `want` is at most 8 in the contract).
export const MAX_KEYS_PER_MINT = 8;
const ASSERTION_PREFIX = "omega_delegate_";
const MAX_TOKEN_LENGTH = 8192;

// Same values as lane platform's OMEGA_STATE, plus the viewer's refusal.
export const MINT_STATE = Object.freeze({
  SIGNED_OUT: "signed-out",
  REFUSED: "refused",
  // The exchange refused a viewer (403 omega_viewer_not_admitted, CP P10).
  VIEWER: "viewer",
  UNAVAILABLE: "unavailable",
});

export const VIEWER_REFUSAL_CODE = "omega_viewer_not_admitted";

export class AssertionMintError extends Error {
  constructor({ state, status = null, code = null, retryAfter = null }) {
    super(`Omega session exchange failed (${state})`);
    this.name = "AssertionMintError";
    this.state = state;
    this.status = status;
    this.code = code;
    // Seconds, when a 429 said how long to wait.
    this.retryAfter = retryAfter;
  }
}

// The host axios instance rejects with {...responseBody, statusCode}; a plain
// axios error carries {response: {status, data}}. Read both.
function describeFailure(error) {
  const status = error?.statusCode ?? error?.response?.status ?? null;
  const body = error?.response?.data ?? error ?? {};
  const code =
    (typeof body.code === "string" && body.code) ||
    (typeof body.error === "string" && body.error) ||
    null;
  const wait = Number(body.retryAfterSeconds ?? body.retry_after);
  const retryAfter =
    Number.isFinite(wait) && wait > 0 ? Math.min(Math.ceil(wait), 300) : null;
  return { status, code, retryAfter };
}

function stateFor(status, code) {
  if (status === 401) return MINT_STATE.SIGNED_OUT;
  if (status === 403 && code === VIEWER_REFUSAL_CODE) return MINT_STATE.VIEWER;
  // No compatible role, or a stale workspace selection.
  if (status === 403) return MINT_STATE.REFUSED;
  // 429, 503 (delegation is not configured here), other 5xx or no response.
  return MINT_STATE.UNAVAILABLE;
}

const isAssertion = (value) =>
  typeof value === "string" &&
  value.startsWith(ASSERTION_PREFIX) &&
  value.length <= MAX_TOKEN_LENGTH;

const clampCount = (count) =>
  Number.isInteger(count) && count > 1 ? Math.min(count, MAX_KEYS_PER_MINT) : 1;

// The keys in an answer. A backend that predates batches ignores `count` and
// answers with one `accessToken`; that one key is used. Any entry that is not
// an Omega assertion makes the whole answer invalid.
function keysOf(data, wanted) {
  if (data.tokenType !== "Bearer" || !isAssertion(data.accessToken))
    return null;
  if (wanted === 1 || data.accessTokens === undefined)
    return [data.accessToken];
  if (!Array.isArray(data.accessTokens) || data.accessTokens.length === 0) {
    return null;
  }
  if (!data.accessTokens.every(isAssertion)) return null;
  return data.accessTokens.slice(0, wanted);
}

/**
 * @param {{ post?: (url: string, body: object) => Promise<{ data: object }>, now?: () => number }} [deps]
 * @returns {(options?: { count?: number }) => Promise<{ token: string, tokens: string[], expiresAt: number }>}
 */
export function createAssertionMinter({
  post = (url, body) => axios.post(url, body),
  now = () => Date.now(),
} = {}) {
  return async function mintAssertion({ count } = {}) {
    const wanted = clampCount(count);
    const body =
      wanted > 1
        ? { audience: OMEGA_AUDIENCE, count: wanted }
        : { audience: OMEGA_AUDIENCE };
    let response;
    try {
      response = await post(SESSION_EXCHANGE_PATH, body);
    } catch (error) {
      const { status, code, retryAfter } = describeFailure(error);
      throw new AssertionMintError({
        state: stateFor(status, code),
        status,
        code,
        retryAfter,
      });
    }
    const tokens = keysOf(response?.data || {}, wanted);
    if (!tokens) {
      throw new AssertionMintError({
        state: MINT_STATE.UNAVAILABLE,
        code: "invalid_session_exchange_response",
      });
    }
    const lifetime = Number.isInteger(response.data.expiresIn)
      ? response.data.expiresIn
      : 0;
    return { token: tokens[0], tokens, expiresAt: now() + lifetime * 1000 };
  };
}
