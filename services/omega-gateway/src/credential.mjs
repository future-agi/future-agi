// The one credential a request may present upstream (adapted from Squad's server.mjs,
// 7debd6482, and split out of server.mjs).
//
// A caller presents a pre-minted customer assertion (checked here, then used once, as is),
// its Future AGI browser token, or a personal API key as `X-Api-Key` and `X-Secret-Key`
// (SB W2-KEYS; an SDK or CI). The gateway exchanges a browser token or a key with Django for
// a fresh assertion that must pass the same check and name the matching `auth_method`. The
// two steps are separate so the server can check a presented assertion before it reads a
// body, and mint again after the body when a slow upload aged the first one (READINESS 1.3:
// a 45 s key is never spent waiting for a slow upload).
import { checkCustomerAssertion, looksLikeAssertion } from "./assertion.mjs";
import { CHANNEL_TOKEN } from "./channel-token/manifest-credential.mjs";

const BEARER = /^Bearer ([^\s]{1,8192})$/;

export function selectionHeaders(request) {
  const out = {};
  for (const name of ["x-organization-id", "x-workspace-id"]) {
    if (typeof request.headers[name] === "string")
      out[name] = request.headers[name];
  }
  return out;
}

/** `{ kind: "assertion" | "browser", token }`, or a refusal. */
export function presentedCredential(request) {
  const match = BEARER.exec(request.headers.authorization || "");
  if (!match)
    return {
      status: 401,
      error: "omega_session_unavailable",
      reason: "no_bearer",
    };
  // A channel token (X-1) is never a key: refused here, never sent to the session exchange.
  if (CHANNEL_TOKEN.test(match[1]))
    return {
      status: 401,
      error: "channel_token_invalid",
      reason: "channel_token_as_bearer",
    };
  return {
    kind: looksLikeAssertion(match[1]) ? "assertion" : "browser",
    token: match[1],
  };
}

const MAX_API_KEY_HEADER_LENGTH = 255;

/** `{ kind: "api-key", apiKey, secretKey }`, or a refusal. */
export function apiKeyCredential(request) {
  const apiKey = request.headers["x-api-key"];
  const secretKey = request.headers["x-secret-key"];
  if (apiKey === undefined && secretKey === undefined) return null;
  if (request.headers.authorization !== undefined) {
    return {
      status: 401,
      error: "omega_session_unavailable",
      reason: "mixed_credentials",
    };
  }
  if (
    typeof apiKey !== "string" ||
    typeof secretKey !== "string" ||
    apiKey.length === 0 ||
    secretKey.length === 0 ||
    apiKey.length > MAX_API_KEY_HEADER_LENGTH ||
    secretKey.length > MAX_API_KEY_HEADER_LENGTH
  ) {
    return {
      status: 401,
      error: "omega_session_unavailable",
      reason: "invalid_api_key_headers",
    };
  }
  return { kind: "api-key", apiKey, secretKey };
}

const checkOptions = (config) => ({
  issuer: config.assertionIssuer,
  publicKeys: config.assertionPublicKeys,
});

/** A pre-minted assertion: the claim check, plus the host selection headers must agree with it. */
export function checkPresentedAssertion(token, request, config) {
  const checked = checkCustomerAssertion(token, checkOptions(config));
  if (!checked.ok)
    return {
      status: 401,
      error: "omega_assertion_rejected",
      reason: checked.reason,
    };
  const selected = selectionHeaders(request);
  if (
    (selected["x-organization-id"] !== undefined &&
      selected["x-organization-id"] !== checked.claims.org_id) ||
    (selected["x-workspace-id"] !== undefined &&
      selected["x-workspace-id"] !== checked.claims.workspace_id)
  ) {
    // The claims ride along for the refusal line only (never the response body), and only when
    // this gateway verified their signature does that line print the ids (refusal-log.mjs).
    return {
      status: 403,
      error: "omega_scope_mismatch",
      reason: "scope_mismatch",
      claims: checked.claims,
    };
  }
  return { token, claims: checked.claims };
}

// Who an assertion speaks for and how it was proven. A key minted again after a slow upload
// (server.mjs) must name the same of each as the key the request was admitted with; only its
// lifetime, replay ids, role and products may differ, and the role and products are checked again.
const IDENTITY_CLAIMS = Object.freeze([
  "sub",
  "org_id",
  "workspace_id",
  "auth_method",
  "sid",
  "key_id",
]);

/** True when two verified claim sets name the same person, tenant and credential. */
export function sameIdentity(first, second) {
  return IDENTITY_CLAIMS.every((claim) => first?.[claim] === second?.[claim]);
}

// What the issuer must sign for each credential the gateway exchanges (SB W2-KEYS): an
// assertion minted for an API key that claims a browser session, or the reverse, is refused,
// so EE's human-session-only verbs (W2-ACCESS) never see a key dressed as a person.
const EXPECTED_AUTH_METHOD = Object.freeze({
  browser: "session",
  "api-key": "api_key",
});

function exchangeHeaders(presented) {
  if (presented.kind === "api-key") {
    // These headers stop at the issuer, never at Omega EE.
    return {
      "x-api-key": presented.apiKey,
      "x-secret-key": presented.secretKey,
    };
  }
  return { authorization: `Bearer ${presented.token}` };
}

/**
 * Exchange a Future AGI browser token or a personal API key (`presented`, from
 * `presentedCredential` or `apiKeyCredential`) for a fresh assertion. Django's refusals keep
 * their meaning for the caller: a viewer is told why (CP P10), an organization system or MCP
 * key is `omega_key_not_admitted`, a throttled caller gets 429 with Django's `Retry-After`, a
 * workspace admitted to no hosted product is 404 and an unreadable rollout store 503
 * (READINESS 3.13; reason `exchange`), and an unreachable or failing issuer is 503 (Future AGI
 * is unavailable, CP REL5), never a sign-in prompt.
 */
export async function mintFromCredential(
  presented,
  request,
  config,
  fetchImpl,
  traceHeaders = {},
) {
  const expectedMethod = EXPECTED_AUTH_METHOD[presented.kind];
  if (expectedMethod === undefined)
    throw new Error(`Cannot exchange a ${presented.kind} credential.`);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.requestTimeoutMs);
  let response;
  try {
    response = await fetchImpl(config.exchangeUrl, {
      method: "POST",
      redirect: "manual",
      headers: {
        "content-type": "application/json",
        ...exchangeHeaders(presented),
        ...selectionHeaders(request),
        ...traceHeaders,
      },
      body: JSON.stringify({ audience: "omega-squad" }),
      signal: controller.signal,
    });
  } catch {
    return {
      status: 503,
      error: "omega_issuer_unavailable",
      reason: "issuer_unreachable",
    };
  } finally {
    clearTimeout(timer);
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 403 && body?.code === "omega_viewer_not_admitted") {
      return {
        status: 403,
        error: "omega_viewer_not_admitted",
        reason: "viewer",
      };
    }
    if (response.status === 403 && body?.code === "omega_key_not_admitted") {
      return {
        status: 403,
        error: "omega_key_not_admitted",
        reason: "key_not_admitted",
      };
    }
    if (response.status === 404 && body?.code === "product_not_admitted") {
      return { status: 404, error: "product_not_admitted", reason: "exchange" };
    }
    if (response.status === 503 && body?.code === "verification_unavailable") {
      return {
        status: 503,
        error: "verification_unavailable",
        reason: "exchange",
      };
    }
    if (response.status === 429) {
      const retryAfter = Number.parseInt(
        response.headers.get("retry-after") || "",
        10,
      );
      return {
        status: 429,
        error: "omega_rate_limited",
        reason: "exchange_throttled",
        rateClass: "session-exchange",
        retryAfterSeconds:
          Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : 60,
      };
    }
    if (response.status >= 500)
      return {
        status: 503,
        error: "omega_issuer_unavailable",
        reason: "issuer_failed",
      };
    return {
      status: 401,
      error: "omega_session_unavailable",
      reason: "exchange_refused",
    };
  }
  const accessToken = body?.accessToken || body?.access_token;
  if (typeof accessToken !== "string" || accessToken.length === 0) {
    return {
      status: 503,
      error: "omega_issuer_unavailable",
      reason: "issuer_malformed",
    };
  }
  const checked = checkCustomerAssertion(accessToken, checkOptions(config));
  if (!checked.ok)
    return {
      status: 401,
      error: "omega_session_unavailable",
      reason: `minted_${checked.reason}`,
    };
  if (checked.claims.auth_method !== expectedMethod) {
    return {
      status: 401,
      error: "omega_session_unavailable",
      reason: "minted_auth_method_mismatch",
    };
  }
  return { token: accessToken, claims: checked.claims };
}
