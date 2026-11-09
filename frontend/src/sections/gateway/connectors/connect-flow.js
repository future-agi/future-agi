// What the Integrations page reads to connect a sign-in (OAuth) app, and the
// one rule for the URLs it follows. The page builds no sign-in URL itself:
// every one comes from the API, and one the page cannot vouch for is
// treated as absent, so its button is not drawn.
//
// The contract (CONNECTORS-PLAN 2.9 and 2.10, CN25; served by M25x, CN11
// and CN24 through CN9's catalog API):
//
// A provider card (catalog `providers[]`) that signs in:
//   credential   { mode: "per_user" | "shared", kind: "oauth", label, help,
//                  help_url }. per_user: each person signs in with their own
//                  account; shared: one account for the organization, signed
//                  in by an Owner or Admin.
//   account_modes  the modes the org may add it in: "user" (per_user) and
//                  "agent" (shared).
//   connect      sent only when this deployment serves the provider's sign-in
//                end to end, as { served: true, own_app? }. Without
//                served === true the provider is not shown at all
//                (catalog-model.js signInServed).
//   connect.own_app  sent when the org may sign in through its own OAuth app
//                (CN11, resolved by CN3): { client_group, redirect_uri,
//                required }. redirect_uri is the callback the org registers
//                with the vendor; required is true when only the org's own
//                app can serve this sign-in.
//
// A server record (an app's `servers[]`), for the user reading the page:
//   credential_mode  "per_user" | "shared"
//   connected    whether this user's calls have an account to use
//   connect_url  where this user goes to sign in: their own account
//                (per_user), or the org's one account (shared, Owner or
//                Admin). Absent when this user may not start a sign-in.
//   accounts     [{ id, scope: "user" | "shared", label, mine, status,
//                disconnect_url? }]: the accounts on the server this user may
//                see. disconnect_url is sent where this user may disconnect it.
//
// Adding a sign-in app (POST apps/{app}/add/ {provider, mode,
// url_variables}) sends no credential; its result carries connect_url, where
// the admin goes next to sign in.

import { HOST_API } from "src/config-global";

export const CONNECT_PREFIX = "/agentcc/mcp/connect/";

// The absolute URL to follow for `url` from the API, or null. It must be a
// path on the API ("/agentcc/mcp/connect/...") or an absolute URL on the
// API's own origin, with no user name or password, that still lies under
// the connect routes once resolved.
export function connectTarget(url, apiBase = HOST_API) {
  if (typeof url !== "string" || url === "") return null;
  let api;
  let resolved;
  try {
    api = new URL(apiBase);
    resolved =
      url.startsWith("/") && !url.startsWith("//")
        ? new URL(url, api.origin)
        : new URL(url);
  } catch {
    return null;
  }
  if (resolved.origin !== api.origin) return null;
  if (resolved.username || resolved.password) return null;
  if (!resolved.pathname.startsWith(CONNECT_PREFIX)) return null;
  return resolved.href;
}

// Sends the browser to a sign-in URL connectTarget or authorizeTarget
// returned. The sign-in runs on the API and comes back to the console when
// it is done.
export function openSignIn(url) {
  window.location.assign(url);
}

// The vendor's sign-in page to follow for the authorize_url a connect start
// answered (POST /agentcc/mcp/connect/{server_id}/start/), or null. It must
// be an absolute URL with no user name or password. https always; plain http
// only on a deployment whose own API is plain http (a local one), so a
// console served over https never sends anyone to a sign-in page over plain
// http. The API chose the page through its own egress policy; this adds no
// host of its own.
export function authorizeTarget(url, apiBase = HOST_API) {
  if (typeof url !== "string" || url === "") return null;
  let api;
  let target;
  try {
    api = new URL(apiBase);
    target = new URL(url);
  } catch {
    return null;
  }
  if (target.username || target.password) return null;
  if (target.protocol === "https:") return target.href;
  if (target.protocol === "http:" && api.protocol === "http:") {
    return target.href;
  }
  return null;
}

// The account modes an org may add a sign-in provider in, each with what it
// means, each person's own account first.
const ACCOUNT_MODE_TEXT = {
  user: "Each person signs in with their own account",
  agent: "One account for the organization, signed in by an Owner or Admin",
};

export function accountModes(provider) {
  const declared = provider?.account_modes || [];
  return ["user", "agent"]
    .filter((mode) => declared.includes(mode))
    .map((mode) => ({ id: mode, label: ACCOUNT_MODE_TEXT[mode] }));
}

// Where this user signs in to an added server's account, or null: the API's
// connect_url, on its own sign-in path, for a provider whose sign-in it
// says it serves. Each person signs in to their own account (per_user); the
// organization's one shared account is an Owner's or Admin's to sign in.
export function serverSignIn(server, provider, canManage, apiBase = HOST_API) {
  if (!server || !provider?.connect || provider.connect.served !== true) {
    return null;
  }
  const target = connectTarget(server.connect_url, apiBase);
  if (!target) return null;
  if (server.credential_mode === "per_user") return target;
  return canManage ? target : null;
}
