// Maps between the parent's URL and the framed child's route.
//
// The parent owns a base path such as /dashboard/studio. Everything after it,
// plus the query string, is the child's route:
//   parent /dashboard/studio/runs/run_1?tab=trace  <->  child /runs/run_1?tab=trace
import { normalizeEmbedPath } from "./protocol";

export const trimBasePath = (basePath) =>
  (basePath || "").replace(/\/+$/, "") || "";

export function childPathFromParent(pathname, search, basePath) {
  const base = trimBasePath(basePath);
  const path = typeof pathname === "string" ? pathname : "/";
  let rest = "/";
  if (base && (path === base || path.startsWith(`${base}/`))) {
    rest = path.slice(base.length) || "/";
  } else if (!base) {
    rest = path || "/";
  }
  const query = typeof search === "string" && search !== "?" ? search : "";
  return normalizeEmbedPath(`${rest}${query}`) || "/";
}

export function parentPathFromChild(basePath, childPath) {
  const base = trimBasePath(basePath);
  const clean = normalizeEmbedPath(childPath) || "/";
  const cut = clean.search(/[?#]/);
  const pathPart = cut === -1 ? clean : clean.slice(0, cut);
  const tail = cut === -1 ? "" : clean.slice(cut);
  const joined = pathPart === "/" ? base || "/" : `${base}${pathPart}`;
  return `${joined}${tail}`;
}

// Builds the frame's first URL from the child's base URL and its first route.
// "hash" children (Studio) read the route from the fragment; "path" children
// (the Console under /omega/console/) from the path and query.
export function buildFrameSrc(src, route, routeMode = "hash") {
  const url = new URL(src, window.location.origin);
  const clean = normalizeEmbedPath(route) || "/";
  if (routeMode === "path") {
    const cut = clean.indexOf("?");
    const pathPart = cut === -1 ? clean : clean.slice(0, cut);
    const query = cut === -1 ? "" : clean.slice(cut + 1);
    url.pathname = `${url.pathname.replace(/\/+$/, "")}${pathPart}`;
    new URLSearchParams(query).forEach((value, key) => {
      if (!url.searchParams.has(key)) url.searchParams.set(key, value);
    });
    url.hash = "";
  } else {
    url.hash = clean === "/" ? "" : clean;
  }
  return url.toString();
}

export const originOf = (src) => {
  try {
    return new URL(src, window.location.origin).origin;
  } catch {
    return null;
  }
};
