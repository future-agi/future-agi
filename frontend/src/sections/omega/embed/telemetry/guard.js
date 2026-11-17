// Telemetry guard for pages that frame an Omega surface (Studio, the Console).
//
// The connect token is removed from the address bar by the first script in
// index.html, before any analytics SDK starts. This module is the second line:
// it keeps frame pages out of autocapture and session replay, and scrubs any
// connect fragment or local-server URL from what the SDKs send. It is imported
// by the analytics set-up code, so it must stay free of React and MUI.

import { AUTH_URL_RULES } from "./auth-url-rules";

// Parent routes that hold an Omega frame. Prefix match on path segments.
export const EMBED_FRAME_PATH_PREFIXES = Object.freeze([
  "/dashboard/studio",
  "/dashboard/console",
  "/dashboard/omega/studio",
  "/dashboard/omega/console",
]);

const normalizePathname = (pathname) => {
  if (typeof pathname !== "string" || pathname.length === 0) return "/";
  return pathname.split(/[?#]/, 1)[0].replace(/\/+$/, "") || "/";
};

export function isEmbedFramePath(pathname) {
  const path = normalizePathname(pathname);
  return EMBED_FRAME_PATH_PREFIXES.some(
    (prefix) => path === prefix || path.startsWith(`${prefix}/`),
  );
}

export const currentPathname = () =>
  typeof window === "undefined" ? "/" : window.location.pathname;

export const isOnEmbedFramePath = () => isEmbedFramePath(currentPathname());

const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// For SDK URL lists (PostHog autocapture url_ignorelist, replay blocklists).
export const EMBED_FRAME_URL_PATTERNS = Object.freeze(
  EMBED_FRAME_PATH_PREFIXES.map(
    (prefix) => new RegExp(`${escapeRegExp(prefix)}(?:[/?#]|$)`),
  ),
);

export const EMBED_FRAME_REPLAY_URL_BLOCKLIST = Object.freeze(
  EMBED_FRAME_PATH_PREFIXES.map((prefix) => ({
    url: `${escapeRegExp(prefix)}(?:[/?#]|$)`,
    matching: "regex",
  })),
);

const LOOPBACK_HOST =
  /^(?:localhost|.+\.localhost|127(?:\.\d{1,3}){3}|\[::1\]|::1|0\.0\.0\.0)$/i;

export const isLoopbackHost = (hostname) =>
  typeof hostname === "string" && LOOPBACK_HOST.test(hostname);

const pageOrigin = () =>
  typeof window === "undefined" ? "" : window.location.origin;

// A URL on a server running on this computer that is not this page's own
// origin: a local Studio, a local Console, a probe to either.
export function isLocalServerUrl(value, origin = pageOrigin()) {
  if (typeof value !== "string" || value.length === 0) return false;
  let url;
  try {
    url = new URL(value, origin || undefined);
  } catch {
    return false;
  }
  return isLoopbackHost(url.hostname) && url.origin !== origin;
}

// The marker names neither the parameter nor the server, so a scrubbed payload
// carries no trace of the link at all.
export const REDACTED_CONNECT = "omega-link-redacted";
export const REDACTED_LOCAL_SERVER = "local-server";

// A fragment that holds `connect=` anywhere is removed whole, so
// `#t=<token>&connect=<url>` loses its token just as `#connect=<url>&t=<token>`
// does (the index.html intake reads either order). The look-ahead for
// `connect=` stops at the next fragment mark, raw or encoded, and after 4200
// characters (a token is at most 4096), so each part of a string is scanned
// once however many `#` it holds. Also matches the percent-encoded form a URL
// takes inside another URL.
const CONNECT_FRAGMENT =
  /(#|%23)(?=(?:(?:[^#%\s"'<>]|%(?!23)){0,4200}?(?:&|%26))?connect(?:=|%3D))[^#\s"'<>]*/gi;
// `connect=` in a query or after `&`, and whatever follows it up to the next
// fragment, quote or whitespace (so `&t=<token>` goes with it).
const CONNECT_PARAM = /(#|\?|&|%23|%3F|%26)connect(=|%3D)[^#\s"'<>]*/gi;

// A public receipt's share token (EE replay-share-route.ts signs it as
// `rpl_v1.<payload>.<signature>`, both base64url). A remix link carries it in
// its path (/dashboard/console/remix/<token>), and while the user signs in
// the sign-in page holds the same path in `returnTo=`, which is not a frame
// page, so analytics would see it there. It is removed wherever it appears;
// percent-encoding leaves its characters as they are.
const SHARE_TOKEN = /rpl_v1\.[A-Za-z0-9_-]{1,4096}\.[A-Za-z0-9_-]{16,128}/g;

// Plain {regex, replacement} rules, applied in order. New Relic's obfuscate
// option takes them as they are; redactEmbedSecrets applies the same ones.
export const CONNECT_LINK_RULES = Object.freeze([
  Object.freeze({
    regex: CONNECT_FRAGMENT,
    replacement: `$1${REDACTED_CONNECT}`,
  }),
  Object.freeze({ regex: CONNECT_PARAM, replacement: `$1${REDACTED_CONNECT}` }),
  Object.freeze({ regex: SHARE_TOKEN, replacement: REDACTED_CONNECT }),
  ...AUTH_URL_RULES,
]);

export const LOCAL_SERVER_ORIGIN =
  /\bhttps?(?::|%3A)(?:\/|%2F){2}(?:localhost|127(?:\.\d{1,3}){3}|\[::1\]|%5B%3A%3A1%5D)(?:(?::|%3A)\d{1,5})?/gi;

// Resource spans also carry a scheme-less server.address. Require a port so
// ordinary mentions of localhost/loopback remain intact.
const LOCAL_SERVER_ADDRESS =
  /(?:localhost|127(?:(?:\.|%2E)\d{1,3}){3}|\[::1\]|%5B%3A%3A1%5D)(?::|%3A)\d{1,5}/gi;

// Inspect just the adjacent character (possibly a percent-encoded byte), not
// a decoded copy of the payload. Encoded delimiters must still be boundaries;
// encoded dots or letters must not turn a larger hostname into a match.
const decodeAddressBoundary = (text) =>
  text.replace(/%([\da-f]{2})/gi, (_, hex) =>
    String.fromCharCode(parseInt(hex, 16)),
  );

export function redactEmbedSecrets(text) {
  if (typeof text !== "string" || text.length === 0) return text;
  return CONNECT_LINK_RULES.reduce(
    (out, rule) => out.replace(rule.regex, rule.replacement),
    text,
  )
    .replace(LOCAL_SERVER_ORIGIN, (match) => {
      const origin = pageOrigin();
      return origin && match.toLowerCase() === origin.toLowerCase()
        ? match
        : REDACTED_LOCAL_SERVER;
    })
    .replace(LOCAL_SERVER_ADDRESS, (address, offset, source) => {
      const before = decodeAddressBoundary(
        source.slice(Math.max(0, offset - 3), offset),
      ).slice(-1);
      const end = offset + address.length;
      const after = decodeAddressBoundary(source.slice(end, end + 3)).slice(0, 1);
      if (/[\w.%-]/.test(before + after)) return address;
      const host = typeof window === "undefined" ? "" : window.location.host;
      return host &&
        decodeURIComponent(address).toLowerCase() === host.toLowerCase()
        ? address
        : REDACTED_LOCAL_SERVER;
    });
}

// Default ports disappear during URL normalization, leaving only a hostname
// in server.address. Scrub that field without changing hostname-only prose.
// Use sibling scheme/port attributes to preserve only this page's own origin,
// not a Studio sharing its hostname on a different port or scheme.
const redactServerAddress = (address, data) => {
  const hostname = decodeAddressBoundary(address);
  if (!isLoopbackHost(hostname)) return redactEmbedSecrets(address);
  try {
    const page = new URL(pageOrigin());
    const scheme = data["url.scheme"] || page.protocol.slice(0, -1);
    const host = hostname === "::1" ? "[::1]" : hostname;
    const port = data["server.port"];
    const server = new URL(
      `${scheme}://${host}${port == null ? "" : `:${port}`}`,
    );
    if (server.origin === page.origin) return address;
  } catch {
    // No matching page origin: do not retain a local server's address.
  }
  return REDACTED_LOCAL_SERVER;
};

// Walk plain telemetry data without a depth cutoff that would let credentials
// escape in nested properties. An explicit worklist avoids stack overflow;
// references/cycles are retained and objects needing no change keep identity.
export function scrubEmbedSecrets(value) {
  if (typeof value === "string") return redactEmbedSecrets(value);
  const seen = new WeakMap();
  const nodes = [];
  const nodeFor = (object) => {
    if (!object || typeof object !== "object") return null;
    const proto = Object.getPrototypeOf(object);
    if (!Array.isArray(object) && proto !== Object.prototype && proto !== null)
      return null;
    if (seen.has(object)) return seen.get(object);
    const node = {
      original: object,
      copy: Array.isArray(object)
        ? new Array(object.length)
        : Object.create(proto),
      entries: [],
      parents: [],
      changed: false,
    };
    seen.set(object, node);
    nodes.push(node);
    return node;
  };
  const root = nodeFor(value);
  if (!root) return value;
  for (let index = 0; index < nodes.length; index += 1) {
    const node = nodes[index];
    Object.keys(node.original).forEach((key) => {
      const original = node.original[key];
      const clean =
        typeof original === "string"
          ? key === "server.address"
            ? redactServerAddress(original, node.original)
            : redactEmbedSecrets(original)
          : original;
      const child = nodeFor(clean);
      if (child) child.parents.push(node);
      const cleanKey = redactEmbedSecrets(key);
      if (clean !== original || cleanKey !== key) node.changed = true;
      node.entries.push({ key: cleanKey, clean, child });
    });
  }
  // Propagate changes through every alias, not just the first path to a node.
  const changed = nodes.filter((node) => node.changed);
  for (let index = 0; index < changed.length; index += 1) {
    changed[index].parents.forEach((parent) => {
      if (!parent.changed) {
        parent.changed = true;
        changed.push(parent);
      }
    });
  }
  changed.forEach((node) => {
    node.entries.forEach(({ key, clean, child }) => {
      // defineProperty also treats a JSON "__proto__" key as data, not a setter.
      Object.defineProperty(node.copy, key, {
        value: child?.changed ? child.copy : clean,
        enumerable: true,
        configurable: true,
        writable: true,
      });
    });
  });
  return root.changed ? root.copy : value;
}
