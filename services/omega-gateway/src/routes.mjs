// Wall W2: the route decision of the Future AGI gateway (CP P3, P4; READINESS L04).
//
// Generated from `manifest/console-route-manifest.v1.json` by `manifest.mjs`. There is no
// hand-written route list here and no fallthrough: a method and path pair with no manifest
// `routes` entry is refused (default deny). The whole `/os` namespace, every `eeOnly` family
// (`/auth`, `/billing`, `/files`, the operator projections), member and invite writes and
// the billing POSTs are therefore closed, because the manifest admits none of them to a
// browser.
//
// Paths are matched raw, exactly as they will be forwarded: the `%` rule (manifest.mjs)
// runs first, so an encoding can never become a new segment, a dot segment, a disguised
// literal or a second decode upstream, and a `#` (which fetch would cut) is refused. What the
// gateway matched is what EE receives; upstream.mjs refuses to forward anything else.
import crypto from "node:crypto";
import { loadConsoleRouteManifest, pathEncodingAllowed, ROLE_RANK } from "./manifest.mjs";

const LOADED = loadConsoleRouteManifest();

/** The compiled manifest this gateway enforces. */
export const ROUTE_MANIFEST = LOADED.manifest;
/** sha256 of the manifest bytes: the two repositories' copies must agree (CP SEC2). */
export const ROUTE_MANIFEST_SHA256 = crypto.createHash("sha256").update(LOADED.bytes).digest("hex");

/**
 * Decide one request. `reason` is a stable code for logs and the refusal body.
 *
 * @returns {{ allowed: true, route: object, scoped: Array<[string, string]> } | { allowed: false, reason: "path_refused" | "route_not_in_manifest" }}
 */
export function decideRoute(method, path, manifest = ROUTE_MANIFEST) {
  if (!pathEncodingAllowed(path)) return { allowed: false, reason: "path_refused" };
  const found = manifest.match(method, path);
  if (found === undefined) return { allowed: false, reason: "route_not_in_manifest" };
  return { allowed: true, route: found.route, scoped: found.scoped };
}

/**
 * The role floor and scope parameters, checked once the caller's claims are known. A hidden
 * feature's route (manifest `hidden`) skips the role floor: it answers 404 for every role, so a
 * 403 never says that a route the Console does not have exists for a higher role.
 */
export function decideIdentity(decision, claims) {
  const floor = decision.route.hiddenBy === undefined ? ROLE_RANK[decision.route.role] : 0;
  if ((ROLE_RANK[claims.role] ?? 0) < floor) return { allowed: false, reason: "role_below_floor" };
  if (decision.route.hiddenBy === undefined && decision.route.emergency !== undefined &&
      (claims.auth_method !== 'session' || (decision.route.emergency === 'organization'
        ? claims.emergency_control !== 'organization' || !['owner', 'admin'].includes(claims.role)
        : !['workspace', 'organization'].includes(claims.emergency_control)))) return { allowed: false, reason: 'role_below_floor' };
  const scope = { orgId: claims.org_id, workspaceId: claims.workspace_id };
  if (decision.scoped.some(([field, value]) => value !== scope[field])) return { allowed: false, reason: "scope_pivot" };
  return { allowed: true };
}

export function isAllowedRoute(method, path) {
  return decideRoute(method, path).allowed;
}

/** True when some method is admitted on this path. */
export function isAllowedPath(path) {
  return ["GET", "POST", "PUT", "PATCH", "DELETE"].some((method) => isAllowedRoute(method, path));
}

/** True for routes whose body class is `bundle` (deployment bundles, sandbox file writes). */
export function isBundleRoute(method, path) {
  const decision = decideRoute(String(method || "GET").toUpperCase(), path);
  return decision.allowed && decision.route.body === "bundle";
}
