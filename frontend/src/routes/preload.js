import { matchRoutes } from "react-router-dom";

// Preload a page's code before the click.
//
// The router registers its dashboard route table here; RouterLink calls
// preloadRoute(href) on hover and focus. Every lazy page found in the matched
// routes' elements (through guards and Suspense wrappers) starts loading, so by
// the time the click lands the chunk is cached and the page skeleton is skipped.

let registeredRoutes = [];
const preloaded = new Set();

export function registerPreloadRoutes(routes) {
  registeredRoutes = routes;
}

function preloadElement(element, depth = 0) {
  if (!element || typeof element !== "object" || depth > 6) return;
  if (Array.isArray(element)) {
    element.forEach((child) => preloadElement(child, depth + 1));
    return;
  }
  element.type?.preload?.();
  preloadElement(element.props?.children, depth + 1);
}

export function preloadRoute(href) {
  if (typeof href !== "string" || !href.startsWith("/")) return;
  const pathname = href.split(/[?#]/)[0];
  if (preloaded.has(pathname)) return;
  const matches = matchRoutes(registeredRoutes, pathname);
  // Remember the path only once something matched, so a hover before the
  // routes register can still preload later.
  if (!matches) return;
  preloaded.add(pathname);
  matches.forEach(({ route }) => preloadElement(route.element));
}
