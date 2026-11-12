// The base URL a copy-ready request shows (the Overview quickstart, a model's
// API tab, the Auto routing example, a fresh key's first request on the Keys
// page).
//
// Requests use the gateway's baseUrl from GatewayContext, which the platform
// builds from AGENTCC_GATEWAY_PUBLIC_URL (agentcc/views/gateway.py): the
// address the Overview Endpoint row shows. They use it only when a reader
// could call it: an http or https URL whose host is not localhost, loopback or
// the placeholder's own host.
//
// - Hosted (Future AGI Cloud, deployment mode "cloud"): that URL, or the
//   placeholder (kind "unavailable", with BASE_URL_UNAVAILABLE beside it) when
//   it is absent or still a local/placeholder default.
// - Self-hosted (mode "oss" or "ee"): that URL when its host is also not an
//   in-cluster name (a single-label host such as a compose or Kubernetes
//   service name, or a host ending in .svc, .cluster.local, .internal or
//   .local). Otherwise a clear placeholder the reader replaces with their
//   gateway's address.
// - An unconfirmed deployment shows the placeholder (kind "unavailable").
// A request is always shown, as production showed it; an address that is not
// confirmed callable is never presented as real.

export const PLACEHOLDER_BASE_URL = "https://YOUR_GATEWAY_URL/v1";

// The URL as a request is built from it: trimmed, no trailing slash. An empty
// string when there is none.
export function trimBaseUrl(baseUrl) {
  return String(baseUrl || "")
    .trim()
    .replace(/\/+$/, "");
}

// Not a web URL, or a local/placeholder default: the placeholder's host,
// localhost, IPv4 loopback (URL parsing turns 127.1 and 2130706433 into
// dotted form) and IPv6 loopback, plain or IPv4-mapped.
function isLocalDefault(parsed, host) {
  return (
    !["http:", "https:"].includes(parsed.protocol) ||
    host === "your_gateway_url" ||
    host === "localhost" ||
    host.endsWith(".localhost") ||
    /^127\./.test(host) ||
    host === "::1" ||
    /^::ffff:7f[0-9a-f]{2}:/.test(host)
  );
}

// An in-cluster name a reader outside the deployment cannot call: a
// single-label host (an IPv6 literal has colons, not dots, and is not one) or
// a host ending in one of these suffixes.
const IN_CLUSTER_SUFFIXES = [".svc", ".cluster.local", ".internal", ".local"];
function isInClusterHost(host) {
  if (!host.includes(".") && !host.includes(":")) return true;
  return IN_CLUSTER_SUFFIXES.some((suffix) => host.endsWith(suffix));
}

// The reported URL, trimmed, when a reader could call it; "" otherwise.
function callableBaseUrl(baseUrl, { allowInCluster }) {
  const url = trimBaseUrl(baseUrl);
  if (!url) return "";
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return "";
  }
  // Compared in lower case, without a trailing dot or IPv6 brackets.
  const host = parsed.hostname
    .toLowerCase()
    .replace(/\.$/, "")
    .replace(/^\[|\]$/g, "");
  if (isLocalDefault(parsed, host)) return "";
  if (!allowInCluster && isInClusterHost(host)) return "";
  return url;
}

/**
 * The base URL for a request example: { kind, url }.
 *  - "configured": the gateway's reported URL, trimmed, when a reader could
 *    call it (hosted or confirmed self-hosted mode).
 *  - "placeholder": confirmed self-hosted mode with no such URL; url is
 *    PLACEHOLDER_BASE_URL.
 *  - "unavailable": unconfirmed mode, or hosted mode with no such URL; url
 *    is PLACEHOLDER_BASE_URL and the page says so (BASE_URL_UNAVAILABLE).
 */
export function snippetBaseUrl(
  baseUrl,
  { hosted, deploymentModeConfirmed = false } = {},
) {
  if (!deploymentModeConfirmed || typeof hosted !== "boolean")
    return { kind: "unavailable", url: PLACEHOLDER_BASE_URL };
  // Hosted keeps the checks it had; a self-hosted address must also be
  // callable from outside its cluster.
  const url = callableBaseUrl(baseUrl, { allowInCluster: hosted });
  if (url) return { kind: "configured", url };
  return hosted
    ? { kind: "unavailable", url: PLACEHOLDER_BASE_URL }
    : { kind: "placeholder", url: PLACEHOLDER_BASE_URL };
}

// What a page says beside a request example built on the placeholder because
// the console has no confirmed public gateway URL to put in it.
export const BASE_URL_UNAVAILABLE =
  "The gateway's public URL isn't available from the console right now, so these requests use the placeholder https://YOUR_GATEWAY_URL/v1. Replace it with your gateway's address before you run them, or reload the page to try again.";

// The same, for a fresh key's Base URL row and its first request.
export const PLACEHOLDER_REQUEST_NOTE =
  "The base URL and request here use the placeholder https://YOUR_GATEWAY_URL/v1. Replace it with your gateway's address before you use them.";
