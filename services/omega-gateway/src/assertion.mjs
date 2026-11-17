// Customer assertion checks.
//
// The only credential this gateway ever sends to Omega EE is a customer host
// assertion minted by FutureAGI's POST /accounts/omega/session-exchange/
// (futureagi/accounts/omega_session.py): `omega_delegate_<compact RS256 JWS>`,
// audience `omega-squad`, role owner/admin/member, lifetime <= 45 s, with fresh
// `jti` and `nonce` replay identifiers that EE consumes once.
//
// EE remains the authority (signature, replay, identity binding). The gateway
// checks the claims so that anything else (a FutureAGI browser token, an
// operator key, a viewer or operator role, an expired or foreign-audience
// assertion) never reaches EE, and verifies the signature too when a public
// JWKS is configured.
import crypto from "node:crypto";

export const ASSERTION_PREFIX = "omega_delegate_";
export const ASSERTION_AUDIENCE = "omega-squad";
export const ASSERTION_MAX_LIFETIME_SECONDS = 45;
export const CUSTOMER_ROLES = new Set(["owner", "admin", "member"]);

const SEGMENT = /^[A-Za-z0-9_-]+$/;
const ID = /^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}$/;
const REPLAY_ID = /^[A-Za-z0-9_-]{32,128}$/;
const AUTH_METHODS = Object.freeze({ session: "sid", api_key: "key_id" });
const MAX_TOKEN_LENGTH = 8192;
// Clocks are expected to be synchronized (EE allows no skew); tolerate only a
// small forward skew on `iat` so a fresh assertion is not refused here.
const FUTURE_IAT_TOLERANCE_SECONDS = 5;

function decodeJson(segment) {
  return JSON.parse(Buffer.from(segment, "base64url").toString("utf8"));
}

export function looksLikeAssertion(token) {
  return typeof token === "string" && token.startsWith(ASSERTION_PREFIX);
}

/**
 * Parse a public JWKS ({keys:[{kty:"RSA", kid, n, e, alg?, use?}]}) into a map
 * of kid -> KeyObject. Private members are refused so a private key is never
 * accidentally deployed to the gateway.
 */
export function parsePublicKeys(jwks) {
  const keys = new Map();
  for (const jwk of Array.isArray(jwks?.keys) ? jwks.keys : []) {
    if (jwk?.kty !== "RSA" || typeof jwk.kid !== "string" || !jwk.kid) throw new Error("OMEGA_ASSERTION_JWKS_FILE must contain RSA keys with a kid");
    if (jwk.d || jwk.p || jwk.q) throw new Error("OMEGA_ASSERTION_JWKS_FILE must contain public keys only");
    if ((jwk.alg && jwk.alg !== "RS256") || (jwk.use && jwk.use !== "sig")) throw new Error("OMEGA_ASSERTION_JWKS_FILE keys must be RS256 signing keys");
    keys.set(jwk.kid, crypto.createPublicKey({ key: { kty: "RSA", n: jwk.n, e: jwk.e }, format: "jwk" }));
  }
  if (keys.size === 0) throw new Error("OMEGA_ASSERTION_JWKS_FILE contains no keys");
  return keys;
}

/**
 * Validate a customer assertion. Returns `{ ok: true, claims }` or
 * `{ ok: false, reason }`; `reason` is a stable code, never token material.
 *
 * @param {string} token
 * @param {{ now?: number, issuer?: string, publicKeys?: Map<string, crypto.KeyObject> | null }} [options]
 */
export function checkCustomerAssertion(token, { now = Math.floor(Date.now() / 1000), issuer = "", publicKeys = null } = {}) {
  if (!looksLikeAssertion(token) || token.length > MAX_TOKEN_LENGTH) return { ok: false, reason: "not_a_customer_assertion" };
  const parts = token.slice(ASSERTION_PREFIX.length).split(".");
  if (parts.length !== 3 || !parts.every((part) => SEGMENT.test(part))) return { ok: false, reason: "malformed_assertion" };
  let header;
  let claims;
  try {
    header = decodeJson(parts[0]);
    claims = decodeJson(parts[1]);
  } catch {
    return { ok: false, reason: "malformed_assertion" };
  }
  if (!header || header.alg !== "RS256" || header.typ !== "JWT" || typeof header.kid !== "string" || !header.kid) {
    return { ok: false, reason: "malformed_assertion" };
  }
  if (!claims || typeof claims !== "object" || Array.isArray(claims)) return { ok: false, reason: "malformed_assertion" };
  if (claims.aud !== ASSERTION_AUDIENCE) return { ok: false, reason: "wrong_audience" };
  if (!CUSTOMER_ROLES.has(claims.role)) return { ok: false, reason: "not_a_customer_role" };
  if (![claims.sub, claims.org_id, claims.workspace_id].every((value) => typeof value === "string" && ID.test(value))) {
    return { ok: false, reason: "malformed_assertion" };
  }
  if (![claims.jti, claims.nonce].every((value) => typeof value === "string" && REPLAY_ID.test(value))) {
    return { ok: false, reason: "malformed_assertion" };
  }
  const bindingClaim = AUTH_METHODS[claims.auth_method];
  if (!bindingClaim || typeof claims[bindingClaim] !== "string" || !ID.test(claims[bindingClaim])) {
    return { ok: false, reason: "malformed_authentication_claim" };
  }
  const otherBindingClaim = bindingClaim === "sid" ? "key_id" : "sid";
  if (Object.prototype.hasOwnProperty.call(claims, otherBindingClaim)) {
    return { ok: false, reason: "malformed_authentication_claim" };
  }
  const { iat, exp } = claims;
  if (!Number.isInteger(iat) || !Number.isInteger(exp) || exp <= iat || exp - iat > ASSERTION_MAX_LIFETIME_SECONDS) {
    return { ok: false, reason: "invalid_lifetime" };
  }
  if (now >= exp) return { ok: false, reason: "expired_assertion" };
  if (iat > now + FUTURE_IAT_TOLERANCE_SECONDS) return { ok: false, reason: "invalid_lifetime" };
  if (issuer && claims.iss !== issuer) return { ok: false, reason: "wrong_issuer" };
  if (publicKeys) {
    const key = publicKeys.get(header.kid);
    if (!key) return { ok: false, reason: "unknown_key" };
    const valid = crypto.verify(
      "RSA-SHA256",
      Buffer.from(`${parts[0]}.${parts[1]}`, "ascii"),
      key,
      Buffer.from(parts[2], "base64url"),
    );
    if (!valid) return { ok: false, reason: "bad_signature" };
  }
  return { ok: true, claims };
}
