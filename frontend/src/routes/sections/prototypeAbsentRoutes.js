// L7b absent-sweep (nav:other-products, settings:pages): the app-shell pages
// outside Datasets + Experiments that the prototype mocks do not serve. In a
// prototype build their nav entries are absent (config-navigation.jsx) and a
// direct or deep link lands on the Datasets list instead of an empty page or
// a fake success from the unmocked catch-all. The real app never reads this
// list (DashboardRoutes checks the static PROTOTYPE_BUILD first). Pure.

/** Where an absent page's link lands in a prototype build. */
export const PROTOTYPE_ABSENT_FALLBACK = "/dashboard/develop";

/** Path patterns (full pathname) of the pages absent in a prototype build. */
export const PROTOTYPE_ABSENT_ROUTES = Object.freeze([
  // Observe / gateway / onboarding products.
  /^\/dashboard\/(error-feed|tasks|users|alerts|dashboards|gateway|get-started)(\/|$)/,
  // Simulate, except Agent Definition.
  /^\/dashboard\/simulate\/(environments|scenarios|personas|test)(\/|$)/,
  // Organization settings the mocks do not serve.
  // L7c g1: Falcon AI Connectors too (no connector can be added there).
  /^\/dashboard\/settings\/(user-management|usage-summary|pricing|billing|mcp-server|org-settings|ee-licenses|integrations|falcon-ai-connectors)(\/|$)/,
  // Per-workspace settings the mocks do not serve.
  /^\/dashboard\/settings\/workspace\/[^/]+\/(members|integrations)(\/|$)/,
]);

/** True when `pathname` is a page absent from a prototype build. */
export function isAbsentInPrototype(pathname) {
  const p = String(pathname ?? "");
  return PROTOTYPE_ABSENT_ROUTES.some((re) => re.test(p));
}
