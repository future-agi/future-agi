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
// authorization request, removed from every address. Compared in lower case.
const ONE_TIME_PARAMS = new Set([
  "sso_token",
  "access_token",
  "refresh_token",
  "is_new_user",
  "saml_code",
  "oauth_code",
  "onboarding_token",
  "onboarding_gcp_token",
  "code_challenge",
  "samlresponse",
  "samlrequest",
  "relaystate",
]);

const BASE = "https://analytics-url.invalid";

// The router matches decoded paths, so "/%73hared/x" opens /shared/:token.
const decodedPath = (pathname) => {
  try {
    return decodeURIComponent(pathname);
  } catch {
    return pathname;
  }
};

// Path segments in lower case, without empty ones ("/SOS/" -> ["sos"]).
const pathSegments = (pathname) =>
  decodedPath(pathname).toLowerCase().split("/").filter(Boolean);

const endsWith = (segments, ...expected) =>
  segments.length >= expected.length &&
  expected.every(
    (segment, index) =>
      segments[segments.length - expected.length + index] === segment,
  );

const isPath = (segments, ...expected) =>
  segments.length === expected.length &&
  expected.every((segment, index) => segments[index] === segment);

// Parameters that are removed only on the pages that receive them, so the
// same names elsewhere (a request_id filter on the gateway logs, an access
// level in settings) are sent unchanged.
const PAGE_PARAMS = [
  {
    // The support sign-in page reads its session values from ?access=&refresh=.
    matches: (segments) => isPath(segments, "sos"),
    params: ["access", "refresh"],
  },
  {
    // The MCP authorization page and the endpoint that redirects to it.
    matches: (segments) =>
      isPath(segments, "mcp", "authorize") ||
      isPath(segments, "mcp", "oauth", "authorize"),
    params: ["request_id", "code", "state"],
  },
  {
    // Sign-in and connector callbacks: /saml2_auth/...,
    // /integrations/slack/callback/ and
    // /falcon-ai/mcp-connectors/<id>/oauth/callback/.
    matches: (segments) =>
      segments.includes("saml2_auth") ||
      endsWith(segments, "integrations", "slack", "callback") ||
      (segments.includes("mcp-connectors") &&
        endsWith(segments, "oauth", "callback")),
    params: ["code", "state", "c"],
  },
];

const removedParams = (pathname) => {
  const segments = pathSegments(pathname);
  const names = new Set(ONE_TIME_PARAMS);
  PAGE_PARAMS.forEach(({ matches, params }) => {
    if (matches(segments)) params.forEach((name) => names.add(name));
  });
  return names;
};

export const pageViewPath = (pathname = "") => {
  const decoded = decodedPath(pathname);
  const pattern = TOKEN_PAGE_PATTERNS.find(
    (candidate) =>
      matchPath({ path: candidate, end: false }, pathname) ||
      matchPath({ path: candidate, end: false }, decoded),
  );
  return pattern || pathname;
};

// How many levels of URLs inside parameter values (e.g. returnTo=...) and
// hash routes are cleaned. A URL value found deeper than this is removed.
const MAX_DEPTH = 4;

// A parameter value can itself be a URL or path, e.g. returnTo=/auth/....
const looksLikeUrl = (value) =>
  value.startsWith("/") || /^https?:\/\//i.test(value);

// A parameter value as a URL, after up to one more decoding step (values
// that were percent-encoded twice). Null when it is not a URL.
const nestedUrl = (value) => {
  if (looksLikeUrl(value)) return { url: value, encodedAgain: false };
  if (!value.includes("%")) return null;
  try {
    const decoded = decodeURIComponent(value);
    return looksLikeUrl(decoded) ? { url: decoded, encodedAgain: true } : null;
  } catch {
    return null;
  }
};

// Returns `search` ("?a=1&b=2", or a fragment's "a=1&b=2") without the
// parameters in `removed`, with URLs in parameter values cleaned too.
const cleanSearch = (search, removed, depth) => {
  const params = new URLSearchParams(search);
  let changed = false;
  const kept = new URLSearchParams();
  params.forEach((value, name) => {
    if (removed.has(name.toLowerCase())) {
      changed = true;
      return;
    }
    const nested = nestedUrl(value);
    if (!nested) {
      kept.append(name, value);
      return;
    }
    if (depth >= MAX_DEPTH) {
      changed = true;
      return;
    }
    const cleaned = cleanUrl(nested.url, depth + 1);
    if (cleaned === nested.url) {
      kept.append(name, value);
      return;
    }
    changed = true;
    kept.append(
      name,
      nested.encodedAgain ? encodeURIComponent(cleaned) : cleaned,
    );
  });
  if (!changed) return search;
  const query = kept.toString();
  if (search.startsWith("?")) return query ? `?${query}` : "";
  return query;
};

const cleanHash = (hash, removed, depth) => {
  if (!hash) return hash;
  // A hash route ("#/path?query" or "#!/path?query"): cleaned as a path with
  // its own query.
  const route = hash.match(/^#(!?)(\/.*)$/s);
  if (route) {
    if (depth >= MAX_DEPTH) return "";
    return `#${route[1]}${cleanUrl(route[2], depth + 1)}`;
  }
  // A fragment with its own query ("#tab?view=grid"): the query is cleaned.
  // A "?" after the first parameter ("#a=1&next=/x?y=2") is part of a value.
  const queryStart = hash.indexOf("?");
  if (queryStart >= 0 && !/[=&]/.test(hash.slice(1, queryStart))) {
    const query = cleanSearch(hash.slice(queryStart), removed, depth);
    return `${hash.slice(0, queryStart)}${query}`;
  }
  // A fragment of parameters ("#sso_token=..."): removed when it carries one
  // of the parameters, otherwise URLs in its values are cleaned.
  const fragment = hash.slice(1);
  if (
    Array.from(new URLSearchParams(fragment).keys()).some((name) =>
      removed.has(name.toLowerCase()),
    )
  ) {
    return "";
  }
  return fragment.includes("=")
    ? `#${cleanSearch(fragment, removed, depth)}`
    : hash;
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

  const removed = removedParams(url.pathname);
  const pathname = pageViewPath(url.pathname);
  const search = cleanSearch(url.search, removed, depth);
  const hash = cleanHash(url.hash, removed, depth);

  if (pathname === url.pathname && search === url.search && hash === url.hash) {
    return href;
  }
  const rest = `${pathname}${search}${hash}`;
  if (href.startsWith("//")) return `//${url.host}${rest}`;
  if (!href.startsWith("/")) return `${url.origin}${rest}`;
  return rest;
};

// Only absolute URLs with a host (http:, https:, ...) and paths starting with
// "/" are URLs here. Other text, such as a name like "shared/platform", is
// never rewritten.
const isUrlText = (href) => {
  if (href.startsWith("/")) return true;
  try {
    return new URL(href).origin !== "null";
  } catch {
    return false;
  }
};

/**
 * Returns `href` as analytics report it: link paths that carry a one-time
 * code are replaced by their route pattern, and one-time query parameters
 * (and a fragment that holds one) are removed. Any other URL, text that is
 * not a URL, and any value that is not a string, is returned unchanged.
 */
export const analyticsUrl = (href) => {
  if (typeof href !== "string" || !isUrlText(href)) return href;
  return cleanUrl(href, 0);
};
