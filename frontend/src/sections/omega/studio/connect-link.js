// Reads the links `omega dev` prints, and the server addresses people paste.
//
// omega dev prints the local link, and with --embed-origin (outside a
// terminal) the Future AGI one too:
//   Studio is ready at http://127.0.0.1:4191/#t=<token>  (open this link ...)
//   Open in Future AGI: <origin>/dashboard/studio#connect=<server>&t=<token>
// When its output is not a terminal, the local link reads "#t=<key>", with
// those very characters: only the "Open in Future AGI" line holds the key
// then. That placeholder is refused, never handed to Studio as a key.
// Either link, the whole line it sits on, or a bare server address can be
// pasted on the connect screen. The token is returned to the caller in memory
// only; nothing here stores it.
//
// Studio only ever runs on this computer (omega dev binds loopback), so a
// server must be a loopback address. A link that points anywhere else is
// refused before anything is framed: Future AGI never shows another site
// inside its own page under the Studio name.

const MAX_TOKEN_LENGTH = 4096;
const LOOPBACK_HOST = /^(?:localhost|127(?:\.\d{1,3}){3}|\[::1\])$/i;

export const LINK_PROBLEM = Object.freeze({
  empty: "empty",
  invalid: "invalid",
  notLocal: "not-local",
  placeholderKey: "placeholder-key",
});

export const isLoopbackHostname = (hostname) =>
  typeof hostname === "string" && LOOPBACK_HOST.test(hostname);

// Splits a fragment such as "connect=<url>&t=<token>" the way the intake
// script in index.html does: on "&", at the first "=" of each part.
function fragmentParams(hash) {
  const params = {};
  const body = (hash || "").replace(/^#/, "");
  if (!body) return params;
  for (const part of body.split("&")) {
    const cut = part.indexOf("=");
    const key = cut === -1 ? part : part.slice(0, cut);
    const value = cut === -1 ? "" : part.slice(cut + 1);
    if (key && !(key in params)) params[key] = value;
  }
  return params;
}

const decode = (value) => {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
};

const HAS_SCHEME = /^[a-z][a-z0-9+.-]*:\/\//i;
const withScheme = (text) => (HAS_SCHEME.test(text) ? text : `http://${text}`);

// A pasted line of omega dev's output holds its link among words ("Studio is
// ready at http://127.0.0.1:4191/#t=...  (open this link ...)"). Text with
// spaces is read as its first http(s) link that carries a real key, else its
// first link that carries any (the "#t=<key>" placeholder, or a server
// without a key), else its first link, without the punctuation a sentence puts
// after it. So a pasted listing whose local link reads "#t=<key>" connects
// with its "Open in Future AGI" line. Text with spaces and no link in it is
// not a link.
const LINKS_IN_TEXT = /https?:\/\/\S+/gi;
const CARRIES_KEY = /#(?:.*&)?(?:connect|t)=/;
function linkIn(text) {
  if (!/\s/.test(text)) return text;
  const links = (text.match(LINKS_IN_TEXT) || []).map((link) =>
    link.replace(/[),.;:'">]+$/, ""),
  );
  return (
    links.find(carriesRealKey) ||
    links.find((link) => CARRIES_KEY.test(link)) ||
    links[0] ||
    null
  );
}

// Whether text without a scheme reads as an address at all: a dotted host, a
// port, an IPv6 literal or localhost. "hello" is not one, so it gets "not a
// link from omega dev" rather than "not on this computer".
const looksLikeAddress = (text, parsed) =>
  HAS_SCHEME.test(text) ||
  parsed.port !== "" ||
  parsed.hostname.includes(".") ||
  parsed.hostname.startsWith("[") ||
  isLoopbackHostname(parsed.hostname);

/**
 * A Studio server address: scheme, host, port and path, no query or fragment.
 * Returns { url, origin } or null when it is not an http(s) loopback address.
 */
export function normalizeStudioServer(value) {
  if (typeof value !== "string" || value.trim() === "") return null;
  let parsed;
  try {
    parsed = new URL(withScheme(value.trim()));
  } catch {
    return null;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
  if (parsed.username || parsed.password) return null;
  if (!isLoopbackHostname(parsed.hostname)) return null;
  const path = parsed.pathname.replace(/\/+$/, "");
  return { url: `${parsed.origin}${path}`, origin: parsed.origin };
}

const cleanToken = (raw) => {
  if (raw === undefined || raw === null || raw === "") return null;
  const token = decode(raw);
  if (!token || token.length > MAX_TOKEN_LENGTH) return null;
  return token;
};

// omega dev's "#t=<key>": a key never holds "<" or ">". Read decoded, as a
// parsed URL hands the fragment over as "%3Ckey%3E".
const isPlaceholderKey = (token) => /[<>]/.test(token);

function carriesRealKey(link) {
  const hash = link.indexOf("#");
  if (hash === -1) return false;
  const token = cleanToken(fragmentParams(link.slice(hash)).t);
  return Boolean(token) && !isPlaceholderKey(token);
}

// What a link's key comes to: refused when it is the placeholder, else the
// key or null.
const withKey = (server, raw) => {
  const token = cleanToken(raw);
  if (token && isPlaceholderKey(token)) {
    return { ok: false, problem: LINK_PROBLEM.placeholderKey };
  }
  return { ok: true, ...server, token };
};

/**
 * Reads pasted text: the "Open in Future AGI" link, the local Studio link, or
 * a server address. A link whose key is omega dev's "<key>" placeholder is
 * refused (problem "placeholder-key"): it cannot open Studio.
 *
 * @returns {{ok: true, url: string, origin: string, token: string|null}
 *   | {ok: false, problem: string}}
 */
export function parseStudioLink(text) {
  const pasted = typeof text === "string" ? text.trim() : "";
  if (!pasted) return { ok: false, problem: LINK_PROBLEM.empty };
  const input = linkIn(pasted);
  if (!input) return { ok: false, problem: LINK_PROBLEM.invalid };

  let outer;
  try {
    outer = new URL(withScheme(input));
  } catch {
    return { ok: false, problem: LINK_PROBLEM.invalid };
  }
  if (!looksLikeAddress(input, outer)) {
    return { ok: false, problem: LINK_PROBLEM.invalid };
  }
  const params = fragmentParams(outer.hash);

  // The Future AGI link: the server is in `connect`, the key in `t`.
  if ("connect" in params) {
    const target = decode(params.connect);
    const server = normalizeStudioServer(target || "");
    if (!server) {
      return {
        ok: false,
        problem:
          target && /^https?:\/\//i.test(target)
            ? LINK_PROBLEM.notLocal
            : LINK_PROBLEM.invalid,
      };
    }
    return withKey(server, params.t);
  }

  // The local link (http://127.0.0.1:4191/#t=<token>) or a bare address.
  if (outer.protocol !== "http:" && outer.protocol !== "https:") {
    return { ok: false, problem: LINK_PROBLEM.invalid };
  }
  if (outer.username || outer.password) {
    return { ok: false, problem: LINK_PROBLEM.invalid };
  }
  const server = normalizeStudioServer(
    `${outer.protocol}//${outer.host}${outer.pathname}`,
  );
  if (!server) return { ok: false, problem: LINK_PROBLEM.notLocal };
  return withKey(server, params.t);
}

// "127.0.0.1:4191" for a server URL; the path is kept when there is one.
export function serverDisplayName(url) {
  try {
    const parsed = new URL(url);
    const path = parsed.pathname.replace(/\/+$/, "");
    return `${parsed.host}${path}`;
  } catch {
    return url;
  }
}

// What the frame opens: Studio's own page in embed mode. The token is never
// part of it; it travels over postMessage after the hello.
export const studioFrameSrc = (url) => `${url.replace(/\/+$/, "")}/?embed=1`;

// "Open local Studio": the server's page in a tab of its own, without a key.
export const studioDirectHref = (url) => `${url.replace(/\/+$/, "")}/`;
