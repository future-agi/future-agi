// Reads the connect link that the first script in index.html took out of the
// address bar. `omega dev` prints a link such as
//   /dashboard/studio#connect=http://127.0.0.1:4190&t=<token>
// and that script removes the fragment before any analytics SDK can read the
// URL, holding it on window.__omegaConnect (non-enumerable, read once).
//
// This module moves it into a closure on first use, so the token lives in one
// place in memory. The token is handed out exactly once; nothing is stored.
//
// A page of this app can also hand a link over in memory (offerOmegaConnect):
// Deployments' "Open in Studio" does, so a deployment's access token never
// travels in a URL, fragment included (OWNER-RULINGS 28, OD-21). Such an offer
// is single-use like any link, honoured for HANDOFF_TTL_MS at most, and only
// on the origin that made it. The token then reaches the framed Studio only
// as embed.key, posted with targetOrigin set to the server's origin
// (bridge.js), so no other origin can read it.
//
// Every link that arrives on an open page, and every offer, starts a new
// generation. A Studio connection records the generation it was opened for and
// takes the token only for that one: a frame still showing an older link keeps
// saying hello until the tab re-renders, and must not take a newer link's key.

export const CONNECT_GLOBAL = "__omegaConnect";
// Fired by the intake script when a link arrives by fragment navigation on a
// page that is already open.
export const CONNECT_EVENT = "omega:connect";
const MAX_TOKEN_LENGTH = 4096;
// How long an in-memory offer waits for the Studio tab to take its token.
export const HANDOFF_TTL_MS = 60_000;

let pending; // undefined until read; then null or { url, origin, token, offer? }
let generation = 0; // which link or offer `pending` came from

if (typeof window !== "undefined") {
  // A newer link replaces whatever was waiting. Registered when this module
  // loads, so it runs before any onOmegaConnect listener reads the new link.
  window.addEventListener(CONNECT_EVENT, () => {
    generation += 1;
    pending = undefined;
  });
}

function readGlobal() {
  if (typeof window === "undefined") return null;
  let value = null;
  try {
    value = window[CONNECT_GLOBAL] ?? null;
    delete window[CONNECT_GLOBAL];
  } catch {
    value = null;
  }
  return value;
}

function normalize(value) {
  if (!value || typeof value !== "object") return null;
  const { url, token } = value;
  if (typeof url !== "string" || typeof token !== "string") return null;
  if (token.length === 0 || token.length > MAX_TOKEN_LENGTH) return null;
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
  // Keep scheme, host, port and path; never a query or fragment from the link.
  const base = `${parsed.origin}${parsed.pathname.replace(/\/+$/, "")}`;
  return { url: base, origin: parsed.origin, token };
}

const ownOrigin = () =>
  typeof window === "undefined" ? null : window.location.origin;

// An offer that has expired, or that another origin made, is dropped whole:
// opening the Studio tab later must not connect to a server nobody asked for.
function current(now = Date.now()) {
  const offer = pending?.offer;
  if (offer && (now > offer.expiresAt || offer.origin !== ownOrigin())) {
    pending = null;
  }
  return pending;
}

function pull() {
  if (pending === undefined) pending = normalize(readGlobal());
  return current();
}

/**
 * Hands a Studio server and its token to the Studio tab in memory, never in a
 * URL; the caller then moves to the tab with a router push. Replaces any link
 * that is waiting. Returns false (and holds nothing) for a value the intake
 * would refuse. `now` and `origin` are for tests.
 */
export function offerOmegaConnect(
  url,
  token,
  { now = Date.now(), origin = ownOrigin() } = {},
) {
  const value = normalize({ url, token });
  if (!value || !origin) return false;
  readGlobal(); // a link still on window is older than this offer
  generation += 1;
  pending = { ...value, offer: { origin, expiresAt: now + HANDOFF_TTL_MS } };
  return true;
}

// The server the link points at, without the token: { url, origin } or null.
export function peekOmegaConnect() {
  const value = pull();
  return value ? { url: value.url, origin: value.origin } : null;
}

// True while a token is waiting to be handed to the frame.
export const hasOmegaConnectToken = () => Boolean(pull()?.token);

// The generation of the link or offer waiting now. A connection records it
// when it is opened and passes it back to takeOmegaConnectToken.
export const omegaConnectGeneration = () => generation;

// Returns the token once, then forgets it. Given a generation, it returns the
// token only while that generation is current, and null once a newer link or
// offer has arrived (without spending the newer token). Called with no
// generation, it takes the current one: for a page that reads one link per
// load. A connection that can outlive a newer link binds its own:
//   const generation = omegaConnectGeneration();
//   getToken = () => takeOmegaConnectToken(generation);
export function takeOmegaConnectToken(forGeneration) {
  if (forGeneration !== undefined && forGeneration !== generation) return null;
  const value = pull();
  if (!value?.token) return null;
  const { token } = value;
  pending = { ...value, token: null };
  return token;
}

// Calls listener({ url, origin } | null) whenever a new link arrives on a page
// that is already open. Returns an unsubscribe function.
export function onOmegaConnect(listener) {
  if (typeof window === "undefined") return () => {};
  const handler = () => listener(peekOmegaConnect());
  window.addEventListener(CONNECT_EVENT, handler);
  return () => window.removeEventListener(CONNECT_EVENT, handler);
}

// Forgets the link entirely (for example after the user disconnects).
export function clearOmegaConnect() {
  readGlobal();
  pending = null;
}

// Test hook: lets unit tests simulate a fresh page load.
export function resetOmegaConnectForTests() {
  pending = undefined;
}
