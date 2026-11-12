// What the trace scrubber treats as credential-bearing, by ROUTE.
// SENSITIVE_ROUTES: the request and response BODIES of these endpoints never
// stay in a trace. Each entry names the product routes it covers;
// e2e/scripts/trace-hygiene.test.mjs pins every entry to a real route of the
// product (futureagi/**/urls.py, agentcc-gateway's admin router, the frontend
// auth routes) and fails when a credential-looking product route is neither
// covered here nor listed in EXEMPT_ROUTES with its reason.

const at = (path) => new RegExp(`^(?:/api)?${path}`);

export const SENSITIVE_ROUTES = [
  {
    re: at("/accounts/token/"),
    routes: ["/accounts/token/", "/accounts/token/refresh/"],
  },
  {
    // clean-signup is exposed only by the E2E test-image URLconf.
    re: at("/accounts/(?:signup|logout|e2e/clean-signup)/$"),
    routes: ["/accounts/signup/", "/accounts/logout/", "/accounts/e2e/clean-signup/"],
  },
  {
    re: at("/accounts/activate/"),
    routes: ["/accounts/activate/<uidb64>/<token>/"],
  },
  {
    re: at("/accounts/password-reset-"),
    routes: [
      "/accounts/password-reset-initiate/",
      "/accounts/password-reset-confirm/<uidb64>/<token>/",
    ],
  },
  {
    re: at("/accounts/accept-invitation/"),
    routes: ["/accounts/accept-invitation/<uidb64>/<token>/"],
  },
  {
    re: at("/accounts/(?:organization|workspace)/invite/"),
    routes: [
      "/accounts/organization/invite/",
      "/accounts/organization/invite/resend/",
      "/accounts/organization/invite/cancel/",
      "/accounts/workspace/invite/",
    ],
  },
  {
    re: at("/accounts/(?:user/resend-invite|resend-invitation-emails)/$"),
    routes: [
      "/accounts/user/resend-invite/",
      "/accounts/resend-invitation-emails/",
    ],
  },
  {
    re: at("/accounts/(?:keys|key|redis-key)/"),
    routes: ["/accounts/keys/", "/accounts/key/", "/accounts/redis-key/"],
  },
  {
    re: at("/accounts/2fa/"),
    routes: [
      "/accounts/2fa/totp/setup/",
      "/accounts/2fa/verify/totp/",
      "/accounts/2fa/recovery-codes/",
    ],
  },
  {
    re: at("/accounts/passkeys?/"),
    routes: ["/accounts/passkey/register/verify/", "/accounts/passkeys/"],
  },
  {
    re: at("/accounts/(?:aws|gcp)-marketplace/"),
    routes: [
      "/accounts/aws-marketplace/verify-token/",
      "/accounts/gcp-marketplace/signup/",
    ],
  },
  {
    re: at("/accounts/appsmith/users/login"),
    routes: ["/accounts/appsmith/users/login"],
  },
  {
    re: at("/saml2_auth/"),
    routes: [
      "/saml2_auth/acs/",
      "/saml2_auth/complete/",
      "/saml2_auth/github/callback/",
    ],
  },
  {
    re: at("/mcp/oauth/"),
    routes: ["/mcp/oauth/token/", "/mcp/oauth/authorize/"],
  },
  {
    re: at("/integrations/(?:slack|connections)/"),
    routes: ["/integrations/slack/callback/", "/integrations/connections/"],
  },
  {
    re: at("/agentcc/(?:api-keys|provider-credentials|webhooks)/"),
    routes: [
      "/agentcc/api-keys/",
      "/agentcc/api-keys/bulk/",
      "/agentcc/provider-credentials/",
      "/agentcc/webhooks/",
    ],
  },
  // R3-LENS-02: classify each assembled route; withhold both bodies when unsure.
  {
    re: at("/agentcc/connectors/apps/[^/]+/oauth/(?:discovery/)?$"),
    routes: [
      "/agentcc/connectors/apps/<str:app_id>/oauth/", // SENSITIVE: OAuth app configuration may carry client credentials.
      "/agentcc/connectors/apps/<str:app_id>/oauth/discovery/", // SENSITIVE: discovery metadata is withheld conservatively; credential-free bodies are not established.
    ],
  },
  {
    re: at("/agentcc/connectors/oauth-clients/(?:[^/]+/(?:rotate/)?)?$"),
    routes: [
      "/agentcc/connectors/oauth-clients/", // SENSITIVE: OAuth client creation may submit client credentials.
      "/agentcc/connectors/oauth-clients/<uuid:credential_id>/", // SENSITIVE: client credential detail and mutation bodies must not enter traces.
      "/agentcc/connectors/oauth-clients/<uuid:credential_id>/rotate/", // SENSITIVE: client credential rotation may submit or return replacement secrets.
    ],
  },
  {
    re: at("/agentcc/internal/mcp/upstream-token/$"),
    routes: [
      "/agentcc/internal/mcp/upstream-token/", // SENSITIVE: the internal upstream-token exchange carries authentication material.
    ],
  },
  {
    re: at(
      "/agentcc/mcp-oauth/(?:\\.well-known/openid-configuration|authorize|consent/[^/]+/|consents/(?:[^/]+/)?|jwks\\.json|register|revoke|token)$",
    ),
    routes: [
      "/agentcc/mcp-oauth/.well-known/openid-configuration", // SENSITIVE: public discovery metadata is conservatively withheld rather than assumed credential-free.
      "/agentcc/mcp-oauth/authorize", // SENSITIVE: authorization exchanges may carry codes, state, and credential-bearing redirects.
      "/agentcc/mcp-oauth/consent/<str:request_id>/", // SENSITIVE: consent-request bodies may carry authorization request or grant material.
      "/agentcc/mcp-oauth/consents/", // SENSITIVE: consent listings are withheld conservatively; credential-free grant metadata is not established.
      "/agentcc/mcp-oauth/consents/<int:consent_id>/", // SENSITIVE: consent detail and revocation bodies may carry grant material.
      "/agentcc/mcp-oauth/jwks.json", // SENSITIVE: key-set metadata is conservatively withheld without assuming only public key material.
      "/agentcc/mcp-oauth/register", // SENSITIVE: OAuth client registration may return client credentials.
      "/agentcc/mcp-oauth/revoke", // SENSITIVE: revocation requests carry the token being revoked.
      "/agentcc/mcp-oauth/token", // SENSITIVE: OAuth token requests and responses carry credentials and tokens.
    ],
  },
  {
    re: at("/\\.well-known/(?:oauth-authorization-server|openid-configuration)/agentcc/mcp-oauth$"),
    routes: [
      "/.well-known/oauth-authorization-server/agentcc/mcp-oauth", // SENSITIVE: authorization-server discovery metadata is conservatively withheld without a credential-free guarantee.
      "/.well-known/openid-configuration/agentcc/mcp-oauth", // SENSITIVE: the OpenID discovery alias is conservatively withheld under the same body policy.
    ],
  },
  {
    re: at("/agentcc/mcp/connect/callback/$"),
    routes: [
      "/agentcc/mcp/connect/callback/", // SENSITIVE: upstream OAuth callbacks may carry authorization codes or state.
    ],
  },
  {
    re: at("/agentcc/mcp/keys/[^/]+/personal/$"),
    routes: [
      "/agentcc/mcp/keys/<uuid:key_id>/personal/", // SENSITIVE: personal-key credential operations must not retain request or response bodies.
    ],
  },
  {
    re: at("/agentcc/mcp/vendors/internal/credential/$"),
    routes: [
      "/agentcc/mcp/vendors/internal/credential/", // SENSITIVE: internal vendor credential exchange carries secrets.
    ],
  },
  {
    re: at("/agentcc/siem/destinations/[^/]+/rotate-secret/$"),
    routes: [
      "/agentcc/siem/destinations/<uuid:pk>/rotate-secret/", // SENSITIVE: destination-secret rotation may submit or return replacement secrets.
    ],
  },
  // Routes inherited from the newer composed product base. Bodies are withheld
  // rather than exempted, including policy/refusal endpoints beside credentials.
  {
    re: at(
      "/agentcc/mcp/(?:credentials/|service/(?:agent-keys/|credential/|workspaces/[^/]+/(?:agent-key-policy|agent-keys)/|agent-key-refusals/))",
    ),
    routes: [
      "/agentcc/mcp/credentials/",
      "/agentcc/mcp/credentials/<uuid:credential_id>/",
      "/agentcc/mcp/credentials/<uuid:credential_id>/rotate/",
      "/agentcc/mcp/credentials/<str:server_id>/use-managed/",
      "/agentcc/mcp/service/agent-keys/",
      "/agentcc/mcp/service/agent-keys/<str:agent_key_id>/revoke/",
      "/agentcc/mcp/service/credential/",
      "/agentcc/mcp/service/credential/rotate/",
      "/agentcc/mcp/service/credential/revoke/",
      "/agentcc/mcp/service/workspaces/<uuid:workspace_id>/agent-key-policy/",
      "/agentcc/mcp/service/workspaces/<uuid:workspace_id>/agent-keys/",
      "/agentcc/mcp/service/agent-key-refusals/",
    ],
  },
  {
    re: at(
      "/omega-hub/v1/(?:me/(?:tokens|email-verification)(?:/|$)|publishers/[^/]+/verification-request(?:/|$))",
    ),
    routes: [
      "/omega-hub/v1/me/tokens",
      "/omega-hub/v1/me/tokens/<uuid:token_id>",
      "/omega-hub/v1/me/email-verification",
      "/omega-hub/v1/me/email-verification/confirm",
      "/omega-hub/v1/publishers/<str:handle>/verification-request",
    ],
  },
  {
    re: at(
      "/webhooks/endpoints/[^/]+/(?:rotate-secret|revoke-previous-secret)/",
    ),
    routes: [
      "/webhooks/endpoints/<str:endpoint_id>/rotate-secret/",
      "/webhooks/endpoints/<str:endpoint_id>/revoke-previous-secret/",
    ],
  },
  {
    re: at("/model-hub/(?:api-keys|secrets)/"),
    routes: ["/model-hub/api-keys/", "/model-hub/secrets/"],
  },
  {
    re: at("/tracer/(?:shared|shared-links)/"),
    routes: ["/tracer/shared/<str:token>/", "/tracer/shared-links/"],
  },
  {
    re: at(
      "/simulate/api/(?:harness/ingress/|livekit/(?:listener-token|validate-credentials)/)",
    ),
    routes: [
      "/simulate/api/harness/ingress/<str:token>/",
      "/simulate/api/livekit/validate-credentials/",
    ],
  },
  {
    re: at("/falcon-ai/mcp-connectors/[^/]+/(?:authenticate|oauth)/"),
    routes: ["/falcon-ai/mcp-connectors/<uuid:connector_id>/authenticate/"],
  },
  // agentcc-gateway admin API (internal/server/server.go): keys and configs carry provider keys.
  {
    re: /^\/-\/(?:keys|config|orgs\/[^/]+\/config|orgs\/configs|admin\/providers\/[^/]+\/rotat|admin\/edge\/config)/,
    gateway: [
      "/-/keys",
      "/-/keys/{key_id}",
      "/-/config",
      "/-/orgs/{org_id}/config",
      "/-/orgs/configs",
      "/-/admin/providers/{id}/rotate",
      "/-/admin/edge/config",
    ],
  },
  // Frontend pages whose URL carries the token (frontend/src/routes/sections/{auth,index}.jsx).
  {
    re: /^\/auth\/jwt\/(?:invitation\/(?:accept|set-password)|verify)\//,
    frontend: [
      "invitation/accept/:uuid/:token",
      "invitation/set-password/:uuid/:token",
      "verify/:uuid/:token",
    ],
  },
  { re: /^\/shared\//, frontend: ["/shared/:token"] },
];

/** Product routes whose name looks credential-related but that carry no credential. */
export const EXEMPT_ROUTES = {
  "/accounts/organization/2fa-policy/":
    "the org's 2FA requirement flag; no secret",
  "/accounts/organization/members/reactivate/": "membership state",
  "/accounts/user/deactivate/": "membership state",
  "/agent-playground/graphs/<uuid:pk>/versions/<uuid:version_id>/activate/":
    "graph version activation",
  "/simulate/agent-definitions/<uuid:agent_id>/versions/<uuid:version_id>/activate/":
    "version activation",
  "/api/traces/span-attribute-keys/": "span attribute names",
};

function pathnameOf(url) {
  if (typeof url !== "string" || !url) return "";
  let p = url;
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(url)) {
    try {
      p = new URL(url).pathname;
    } catch {
      return "";
    }
  } else if (url.startsWith("None/")) p = url.slice(4);
  return p.split(/[?#]/)[0];
}

/** True when a URL or path is one of the SENSITIVE_ROUTES. */
export function sensitiveUrl(url) {
  const p = pathnameOf(url);
  return Boolean(p) && SENSITIVE_ROUTES.some((r) => r.re.test(p));
}

const LOCAL_HOST =
  /^(?:localhost|127(?:\.\d{1,3}){3}|\[::1\]|::1|0\.0\.0\.0)$/i;

/**
 * True for an absolute http(s)/ws(s) URL to anything but this machine: a
 * third-party request (analytics beacons, fonts, CDNs). Its query and bodies
 * are never evidence, and they carry identifiers and payloads.
 */
export function thirdPartyUrl(url) {
  if (typeof url !== "string" || !/^(?:https?|wss?):\/\//i.test(url))
    return false;
  try {
    return !LOCAL_HOST.test(new URL(url).hostname);
  } catch {
    return true;
  }
}

export const REDACTED = "[REDACTED]";
const SEG = "[^/?#\\s\"'\\\\<>]+";
const PATH_TOKENS = [
  // uidb64 + token pairs: accounts/urls.py and the frontend links built from them.
  new RegExp(
    `(/(?:accounts/(?:accept-invitation|password-reset-confirm|activate)|auth/jwt/(?:invitation/(?:accept|set-password)|verify))/)${SEG}/${SEG}`,
    "g",
  ),
  // single-token links: tracer shared links, the frontend /shared page, harness ingress.
  new RegExp(
    `(/(?:tracer/)?shared/|/simulate/api/harness/ingress/)${SEG}`,
    "g",
  ),
];
const QUERY_TOKEN =
  /([?&#;](?:token|access_token|refresh_token|id_token|auth|code|state|key|api_key|apikey|secret|password|pwd|otp|signature|sig|uid|uidb64|samlresponse|samlrequest|relaystate|ticket|sessionid|session_token|invite|invitation)=)[^&#\s"'\\<>]+/gi;

const ABSOLUTE_URL_QUERY =
  /\b((?:https?|wss?):\/\/[^/\s"'?#\\<>]+[^\s"'?#\\<>]*)\?[^\s"'#\\<>]*/gi;

/** Invite, reset, verify and shared-link tokens in URLs (path and query forms). */
export function scrubUrlTokens(s) {
  let out = s.replace(ABSOLUTE_URL_QUERY, (m, base) =>
    thirdPartyUrl(base) ? `${base}?${REDACTED}` : m,
  );
  out = out.replace(PATH_TOKENS[0], `$1${REDACTED}/${REDACTED}`);
  out = out.replace(PATH_TOKENS[1], `$1${REDACTED}`);
  return out.replace(QUERY_TOKEN, `$1${REDACTED}`);
}
