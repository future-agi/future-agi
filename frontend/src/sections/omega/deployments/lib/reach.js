// Who can reach a deployment's URL (DEMO-FIX-2 item 1, interim until the
// public endpoint lands: PARITY P09).
//
// EE says it on every deployment (DEP-02): `urlReach` is "deployment-host"
// while the port is published on the host's loopback address only (every
// deployment today), "network" once a reverse proxy gives it a public address;
// `urlNote` is EE's own sentence for it. A reply without `urlReach` (an older
// EE) is judged by the URL: a loopback host reaches nobody else either.
//
// A host-only deployment is labelled "Running on this host", never "Live", and
// its URL is shown as text, never a link: opened from a browser, the address
// is the deployment's own Studio page, which asks for the access token (the
// owner demo check on 2026-10-22 found every "Live" link opening that key
// page, with a 401 behind it). Its "Studio page" is not linked either
// (DEMO-POLISH-3: the round-5c demo check found that link opening the same key
// page, worded for `omega dev`, for a token the person never saw): a
// host-only deployment links nothing on its host (studioPageHref), except
// right beside the access token the New deployment result shows, where that
// key page is one paste away.

const LOOPBACK_HOST = /^(?:localhost|127(?:\.\d{1,3}){3}|\[::1\])$/i;

export const HOST_ONLY_STATUS = "Running on this host";
export const HOST_ONLY_NOTE =
  "Only the machine that runs Omega can reach this address. Calls need the access token.";

export function isHostOnly(deployment) {
  if (!deployment?.url) return false;
  if (deployment.urlReach === "deployment-host") return true;
  if (deployment.urlReach === "network") return false;
  try {
    return LOOPBACK_HOST.test(new URL(deployment.url).hostname);
  } catch {
    return false;
  }
}

/** EE's own sentence when it sent one, else ours. */
export const hostOnlyNote = (deployment) =>
  (typeof deployment?.urlNote === "string" && deployment.urlNote.trim()) ||
  HOST_ONLY_NOTE;

/**
 * Where the "Studio page" link of a deployment goes, or null for no link. None
 * for a deployment only its host can reach (DEMO-POLISH-3), unless
 * `tokenOnScreen`: the caller shows that deployment's access token right beside
 * the link (the New deployment result), so the page's key gate is no dead end.
 * Never with the access token in it, fragment included
 * (OD-21: a fragment lands in the address bar and in history). The page is
 * served by the deployment itself, on another origin, and reads its token only
 * from its own address, so no in-memory handoff can reach it: the link opens
 * the page's own key gate, and OpenInStudio says so beside the link.
 */
export function studioPageHref(deployment, options) {
  if (isHostOnly(deployment) && options?.tokenOnScreen !== true) return null;
  return deployment?.studioUrl || null;
}
