import { matchPath } from "react-router";

// Some links carry a one-time code: in the path (verification, invitation and
// shared links) or as a query parameter (sign-in landings and callbacks).
// Analytics report such URLs by their route pattern and without those
// parameters. The router and the address bar are not changed.

const TOKEN_PAGE_PATTERNS = [
  "/auth/jwt/invitation/accept/:uuid/:token",
  "/auth/jwt/invitation/set-password/:uuid/:token",
  "/auth/jwt/verify/:uuid/:token",
  "/shared/:token",
];

// Query parameters that hold sign-in codes or values tied to one sign-in or
// authorization request. Compared in lower case.
const ONE_TIME_PARAMS = new Set([
  "sso_token",
  "access_token",
  "refresh_token",
  "is_new_user",
  "saml_code",
  "oauth_code",
  "onboarding_token",
  "onboarding_gcp_token",
  "code",
  "state",
  "request_id",
  "code_challenge",
  "samlresponse",
  "samlrequest",
  "relaystate",
]);

const BASE = "https://analytics-url.invalid";
const ABSOLUTE = /^[a-z][a-z0-9+.-]*:/i;

export const pageViewPath = (pathname = "") => {
  const pattern = TOKEN_PAGE_PATTERNS.find((candidate) =>
    matchPath({ path: candidate, end: false }, pathname),
  );
  return pattern || pathname;
};

const isOneTimeParam = (name) => ONE_TIME_PARAMS.has(name.toLowerCase());

// A parameter value can itself be a URL or path, e.g. returnTo=/auth/....
const looksLikeUrl = (value) =>
  value.startsWith("/") || /^https?:\/\//i.test(value);

const cleanSearch = (search, depth) => {
  const params = new URLSearchParams(search);
  let changed = false;
  const kept = new URLSearchParams();
  params.forEach((value, name) => {
    if (isOneTimeParam(name)) {
      changed = true;
      return;
    }
    const cleaned =
      depth < 1 && looksLikeUrl(value) ? cleanUrl(value, depth + 1) : value;
    if (cleaned !== value) changed = true;
    kept.append(name, cleaned);
  });
  if (!changed) return search;
  const query = kept.toString();
  return query ? `?${query}` : "";
};

const cleanUrl = (href, depth) => {
  let url;
  try {
    url = new URL(href, BASE);
  } catch {
    // Not a parseable URL: keep only the part before any query or fragment.
    const [beforeQuery] = href.split(/[?#]/, 1);
    return beforeQuery === href ? href : pageViewPath(beforeQuery);
  }

  const pathname = pageViewPath(url.pathname);
  const search = cleanSearch(url.search, depth);
  const fragment = new URLSearchParams(url.hash.slice(1));
  const hash = Array.from(fragment.keys()).some(isOneTimeParam) ? "" : url.hash;

  if (pathname === url.pathname && search === url.search && hash === url.hash) {
    return href;
  }
  const rest = `${pathname}${search}${hash}`;
  if (ABSOLUTE.test(href)) return `${url.origin}${rest}`;
  if (href.startsWith("//")) return `//${url.host}${rest}`;
  return rest;
};

/**
 * Returns `href` as analytics report it: link paths that carry a one-time
 * code are replaced by their route pattern, and one-time query parameters
 * (and a fragment that holds one) are removed. Any other URL, and any value
 * that is not a string, is returned unchanged.
 */
export const analyticsUrl = (href) => {
  if (typeof href !== "string" || href === "") return href;
  return cleanUrl(href, 0);
};
