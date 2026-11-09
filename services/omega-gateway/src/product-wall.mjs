// The product wall (READINESS 3.13, lane 1.10b): a route reaches Omega EE only when the verified key
// names the route's product in its `products` claim.
//
// Django's session exchange mints the claim (futureagi/accounts/omega_session.py): each hosted
// product the workspace is admitted to, as "active" or "paused", plus `rollout_epoch`. This module
// reads `manifest/route-products.v1.json` and decides, after the route allow-list and the claim
// check, before any body is read:
//   - claims whose signature this gateway did not verify (no JWKS configured) are never trusted:
//     503 verification_unavailable (fail closed; an edited `products` claim cannot pass);
//   - a key with no `products` claim, or one that does not name the route's product: 404
//     product_not_admitted, so a dark product does not reveal itself;
//   - a route whose products are all paused for this workspace: reads still answer, new work is 503
//     product_paused (never 404, so the product does not seem to vanish for its users);
//   - a route no table entry covers: 403 route_not_allowed (default deny).
import fs from "node:fs";
import { fileURLToPath } from "node:url";

export const ROUTE_PRODUCTS_SCHEMA = "omega-route-products/v1";
export const ROUTE_PRODUCTS_PATH = fileURLToPath(new URL("../manifest/route-products.v1.json", import.meta.url));
export const PLATFORM = "platform";
const STATES = new Set(["active", "paused"]);
const READS = new Set(["GET", "HEAD"]);
const METHODS = new Set(["GET", "POST", "PUT", "PATCH", "DELETE"]);
const PREFIX = /^(?:\/[a-z0-9][a-z0-9-]*)+$/;
const PARAM_PATH = /^(?:\/(?:[a-z0-9][a-z0-9-]*|\{[a-z]+\}))+$/;

function exactKeys(value, keys, name) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error(`${name} must be an object`);
  const actual = Object.keys(value);
  if (actual.length !== keys.length || !keys.every((key) => Object.hasOwn(value, key))) {
    throw new Error(`${name} has missing or unknown fields`);
  }
}

/**
 * Strict: an unknown field or product, a duplicate prefix or a malformed path fails the load, so a
 * typo can never widen the wall.
 */
export function compileRouteProducts(input) {
  exactKeys(input, ["schemaVersion", "about", "products", "routes", "whilePaused"], "route-products");
  if (input.schemaVersion !== ROUTE_PRODUCTS_SCHEMA) throw new Error("Unsupported route-products schema");
  if (!Array.isArray(input.products) || input.products.length === 0) throw new Error("route-products needs products");
  const products = new Set(input.products);
  if (products.has(PLATFORM) || products.size !== input.products.length) throw new Error("Invalid product list");
  const routes = [];
  const seen = new Set();
  for (const raw of input.routes) {
    exactKeys(raw, ["prefix", "product"], "route");
    if (typeof raw.prefix !== "string" || !PREFIX.test(raw.prefix)) throw new Error(`Invalid prefix ${String(raw.prefix)}`);
    if (seen.has(raw.prefix)) throw new Error(`Duplicate prefix ${raw.prefix}`);
    seen.add(raw.prefix);
    const list = Array.isArray(raw.product) ? raw.product : [raw.product];
    const valid = raw.product === PLATFORM
      || (list.length > 0 && new Set(list).size === list.length && list.every((product) => products.has(product)));
    if (!valid) throw new Error(`Route ${raw.prefix} names an unknown product`);
    routes.push(Object.freeze({ prefix: raw.prefix, products: Object.freeze(list) }));
  }
  if (routes.length === 0) throw new Error("route-products needs routes");
  const whilePaused = input.whilePaused.map((raw) => {
    exactKeys(raw, ["method", "path"], "whilePaused entry");
    if (!METHODS.has(raw.method) || READS.has(raw.method) || !PARAM_PATH.test(raw.path)) {
      throw new Error(`Invalid whilePaused entry ${String(raw.method)} ${String(raw.path)}`);
    }
    const pattern = raw.path.split("/").map((segment) => (/^\{[a-z]+\}$/.test(segment) ? "[^/]+" : segment)).join("\\/");
    return Object.freeze({ method: raw.method, pattern: new RegExp(`^${pattern}$`) });
  });
  // Longest prefix first, so /workspace/sandboxes/settings could be given its own entry later.
  routes.sort((a, b) => b.prefix.length - a.prefix.length);
  return Object.freeze({ products, routes: Object.freeze(routes), whilePaused: Object.freeze(whilePaused) });
}

export function loadRouteProducts(file = ROUTE_PRODUCTS_PATH) {
  return compileRouteProducts(JSON.parse(fs.readFileSync(file, "utf8")));
}

function lookup(table, path) {
  return table.routes.find(({ prefix }) => path === prefix || path.startsWith(`${prefix}/`))?.products;
}

/**
 * The products a route serves (["platform"] for a platform route), or undefined when none is named.
 * The raw path and its decoded form must name the same products: an encoded spelling of a family
 * (%73 for its first letter, which a server on the way could decode) names none, so it is refused.
 */
export function productsForPath(table, path) {
  let decoded;
  try {
    decoded = decodeURIComponent(path);
  } catch {
    return undefined;
  }
  const raw = lookup(table, path);
  if (raw === undefined || lookup(table, decoded)?.join(",") !== raw.join(",")) return undefined;
  return raw;
}

function claimStates(claims) {
  const products = claims?.products;
  if (products === null || typeof products !== "object" || Array.isArray(products)) return undefined;
  const states = new Map();
  for (const [product, state] of Object.entries(products)) {
    if (!STATES.has(state)) return undefined;
    states.set(product, state);
  }
  return states;
}

/**
 * `{ ok: true, products }` or a refusal `{ status, error, reason }`.
 *
 * @param {{ verified: boolean }} trust whether this gateway verified the key's signature itself
 */
export function decideProduct(table, method, path, claims, { verified }) {
  if (!verified) return { status: 503, error: "verification_unavailable", reason: "unverified_claims" };
  const routeProducts = productsForPath(table, path);
  if (routeProducts === undefined) return { status: 403, error: "route_not_allowed", reason: "no_product" };
  const states = claimStates(claims);
  if (states === undefined) return { status: 404, error: "product_not_admitted", reason: "no_products_claim" };
  if (routeProducts[0] === PLATFORM) {
    return states.size > 0 ? { ok: true, products: [PLATFORM] } : { status: 404, error: "product_not_admitted", reason: "platform" };
  }
  const present = routeProducts.filter((product) => states.has(product));
  if (present.length === 0) return { status: 404, error: "product_not_admitted", reason: routeProducts.join(",") };
  if (present.some((product) => states.get(product) === "active")) return { ok: true, products: present };
  const verb = String(method || "GET").toUpperCase();
  if (READS.has(verb) || table.whilePaused.some((entry) => entry.method === verb && entry.pattern.test(path))) {
    return { ok: true, products: present };
  }
  return { status: 503, error: "product_paused", reason: present.join(","), retryAfterSeconds: 30 };
}
