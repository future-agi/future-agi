// Which rate class a route draws on (READINESS L03, lane 3.7: the sandbox numbers).
//
// The manifest names one of `read`, `write` and `model-start` for every route, and its class
// lists are byte-identical with EE's copy, so a gateway-only class cannot be added there. This
// table is the one place a route draws on a gateway class instead of its manifest class:
//
//   POST /workspace/sandboxes   sandbox-create (the write bucket AND 30 creates a minute per
//                               workspace; ratelimit.mjs)
//
// It is checked once at load against the manifest the gateway enforces: an entry whose route
// the manifest does not admit, whose manifest class is not `write`, or whose class the limiter
// does not know stops the gateway, so a typo can never silently drop a limit.
import { RATE_CLASSES } from "./ratelimit.mjs";
import { ROUTE_MANIFEST } from "./routes.mjs";

export const ROUTE_RATE_CLASSES = Object.freeze({
  "POST /workspace/sandboxes": "sandbox-create",
});

/** Throws when an override does not name a manifest write route and a known class. */
export function checkRouteRateClasses(manifest = ROUTE_MANIFEST, overrides = ROUTE_RATE_CLASSES, classes = RATE_CLASSES) {
  for (const [id, rateClass] of Object.entries(overrides)) {
    const route = manifest.routes.find((entry) => `${entry.method} ${entry.path}` === id);
    if (route === undefined) throw new Error(`Rate override ${id} names no manifest route.`);
    if (route.rate !== "write") throw new Error(`Rate override ${id} must refine a write route, not ${route.rate}.`);
    if (!Object.hasOwn(classes, rateClass)) throw new Error(`Rate override ${id} names an unknown class ${rateClass}.`);
  }
}

checkRouteRateClasses();

/** The class a matched route's requests are limited by. */
export function rateClassOf(route, overrides = ROUTE_RATE_CLASSES) {
  return overrides[`${route.method} ${route.path}`] ?? route.rate;
}
