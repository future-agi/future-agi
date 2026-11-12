// CX-WALL: a request on a `channel` route of the manifest, end to end.
//
// The route was already decided by W2 (routes.mjs). In order, before a byte of the body is read:
//   1. no other credential: an `Authorization` header of any kind is refused (a host key is never
//      admitted here, and is never spent: nothing is exchanged or forwarded);
//   2. exactly one well-formed token in `token`, and no other credential in the query;
//   3. a token this gateway saw EE mint (bindings.mjs), still live, for the same bound path values
//      (the conversation): unknown or expired is 401 `channel_token_invalid`, another conversation 404;
//   4. the role floor, scope parameters, product wall and rate limit, judged by the verified claims of
//      the key that minted the token, exactly as the mint itself was judged.
// Then EE gets the request with the token in its query and NO assertion (EE re-checks the token, the
// person's access and the computer generation on every use), and the answer is piped back with
// `followChannel` keeping the binding in step with EE's renewals and refusals.
import { decideProduct } from "../product-wall.mjs";
import { decideIdentity, decideRoute } from "../routes.mjs";
import { BodyRefused, forwardToEe, readBody } from "../upstream.mjs";
import { CHANNEL_TOKEN, CHANNEL_TOKEN_QUERY, bindingPrefixFor, boundValues } from "./manifest-credential.mjs";
import { followChannel, learnMint } from "./taps.mjs";

const QUERY_CREDENTIALS = new Set([
  "access_token", "api_key", "apikey", "authorization", "credential", "credentials", "key", "secret",
]);

const invalid = (reason) => ({ status: 401, error: "channel_token_invalid", reason });

/** True when this method and route path resolve to a `channel` route (the query exemption needs it first). */
export function isChannelTarget(method, routePath, manifest) {
  if (routePath === null) return false;
  const decision = decideRoute(method, routePath, manifest);
  return decision.allowed && decision.route.credential === "channel";
}

/** Steps 1 to 3: the binding this request may use, or the refusal. */
export function admitChannelRequest({ request, route, routePath, search, manifest, bindings }) {
  if (request.headers.authorization !== undefined) return { refusal: invalid("credential_on_channel_route") };
  const query = new URLSearchParams(search);
  for (const key of query.keys()) {
    if (QUERY_CREDENTIALS.has(key.toLowerCase()) || (key !== CHANNEL_TOKEN_QUERY && key.toLowerCase() === CHANNEL_TOKEN_QUERY)) {
      return { refusal: { status: 400, error: "credential_in_query" } };
    }
  }
  const tokens = query.getAll(CHANNEL_TOKEN_QUERY);
  if (tokens.length !== 1 || !CHANNEL_TOKEN.test(tokens[0])) return { refusal: invalid("malformed_channel_token") };
  const token = tokens[0];
  const binding = bindings.find(token);
  if (binding === undefined) return { refusal: invalid("unknown_channel_token") };
  if (binding.expired) return { refusal: invalid("expired_channel_token") };
  const prefix = bindingPrefixFor(manifest, route);
  const values = boundValues(binding.prefix, routePath);
  const sameScope = prefix === binding.prefix && Object.entries(binding.bound).every(([name, value]) => values[name] === value);
  if (!sameScope) return { refusal: { status: 404, error: "not_found", reason: "channel_token_other_scope" } };
  return { token, binding };
}

/**
 * Serve one channel request. `refuse` is the server's own refusal helper (one refusal line each),
 * `limiter` its rate limiter, `ip` the client address and `headers` the base response headers.
 */
export async function serveChannelRequest({
  request, response, context, config, fetchImpl, decision, routePath, search, manifest, bindings, routeProducts,
  verified, refuse, limiter, ip, headers, trackStream,
}) {
  const admitted = admitChannelRequest({ request, route: decision.route, routePath, search, manifest, bindings });
  if (admitted.refusal) return refuse(admitted.refusal);
  const { token, binding } = admitted;
  const claims = binding.claims;
  const identity = decideIdentity(decision, claims);
  if (!identity.allowed) {
    return refuse({ status: 403, error: identity.reason === "role_below_floor" ? "omega_role_not_permitted" : "omega_scope_mismatch", reason: identity.reason });
  }
  const product = decideProduct(routeProducts, request.method, routePath, claims, { verified });
  if (!product.ok) return refuse(product);
  context.tenant = { org: claims.org_id, workspace: claims.workspace_id };
  const rate = decision.route.rate;
  const limit = await limiter.take(rate, { sub: claims.sub, orgId: claims.org_id, workspaceId: claims.workspace_id, ip });
  context.rateStore = limit.store;
  if (limit.unavailable) return refuse({ status: 503, error: "omega_rate_limit_unavailable", reason: "rate_store_unavailable", rateClass: rate, retryAfterSeconds: limit.retryAfterSeconds });
  if (!limit.allowed) return refuse({ status: 429, error: "omega_rate_limited", reason: "rate_limited", rateClass: rate, retryAfterSeconds: limit.retryAfterSeconds });
  let body;
  try {
    body = await readBody(request, decision.route.body, config);
  } catch (error) {
    return refuse(error instanceof BodyRefused ? { status: 413, error: "request_body_too_large" } : { status: 400, error: "request_body_unreadable" });
  }
  const result = await forwardToEe({
    request, response, config, fetchImpl: followChannel(fetchImpl, { bindings, token }), context, route: decision.route,
    path: routePath, search, token: undefined, body, headers, trackStream,
  });
  if (result.error && !response.headersSent) refuse({ status: result.status, error: result.error });
}

/** The fetch for a key request: on the `channel-mint` route it learns the token EE answers. */
export function mintAwareFetch(fetchImpl, { decision, routePath, manifest, bindings, claims }) {
  if (decision.route.credential !== "channel-mint") return fetchImpl;
  const prefix = bindingPrefixFor(manifest, decision.route);
  return learnMint(fetchImpl, { bindings, prefix, bound: boundValues(prefix, routePath), claims });
}
