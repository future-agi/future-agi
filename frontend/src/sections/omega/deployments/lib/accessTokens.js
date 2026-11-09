// A deployment's access token is shown once, in the reply that minted it (a
// first deploy or a rotation). This page keeps it in a module closure, keyed
// by deployment id, so it can be copied and handed to Studio while this tab is
// open. It is never written to storage, a query cache, a URL path or a log;
// a reload forgets it.

const held = new Map();
const listeners = new Set();

const notify = () => listeners.forEach((listener) => listener());

/** Take the token out of a detail reply; returns the detail without it. */
export function keepAccessToken(detail) {
  if (!detail || typeof detail !== "object") return detail;
  const { accessToken, ...rest } = detail;
  if (typeof accessToken === "string" && accessToken && rest.id) {
    held.set(rest.id, accessToken);
    notify();
  }
  return rest;
}

export const heldAccessToken = (id) => held.get(id) ?? null;

export function forgetAccessToken(id) {
  if (held.delete(id)) notify();
}

export function subscribeAccessTokens(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function resetAccessTokensForTests() {
  held.clear();
  notify();
}
