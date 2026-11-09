// CX-WALL (owner ruling X-1): the route manifest's `credential` field, as W2 compiles it.
//
// A manifest route takes one of three credentials, the same rules Omega EE's W3 and the
// Console's W1 apply to the byte-identical manifest:
//   - absent (`key`): a customer host assertion, one per request (the rest of this gateway);
//   - `channel-mint`: a key route whose 201 answer carries a channel token (EE's `channel.mint`);
//   - `channel`: the token in the `token` query parameter and NOTHING else. A host key is never
//     admitted on it, and the token is never admitted on any other route.
//
// The binding is declared by the paths: a token minted at the conversation's computer channel route
// (conversations/{id}/computer/channel) binds the values of the mint's parent path
// (conversations/{id}/computer), and every
// `channel` route must lie under exactly one mint's parent, so a token for one conversation can never
// name another. No route template is written by hand here.

import { assertTriggerBudgetContract } from "../trigger-budget-contract.mjs";

/** The query parameter a channel route reads its token from (EE's contract, CHANNEL_TOKEN_QUERY). */
export const CHANNEL_TOKEN_QUERY = "token";
/** `cct_` and 43 base64url characters: EE's CHANNEL_TOKEN_PATTERN. */
export const CHANNEL_TOKEN = /^cct_[A-Za-z0-9_-]{43}$/;
const CREDENTIALS = ["channel", "channel-mint", "trigger-signature"];
const PARAM = /^\{([a-zA-Z][a-zA-Z0-9]*)\}$/;

/** One route's credential; strict, so a typo or a credential on an `eeOnly` route fails the compile. */
export function routeCredentialOf(value, surface, id) {
  if (
    value === "trigger-signature" &&
    (surface !== "browser" || id !== "POST /workspace/triggers/{id}/events")
  )
    throw new Error(`Route ${id} cannot use trigger signatures.`);
  if (value === undefined) return "key";
  if (!CREDENTIALS.includes(value))
    throw new Error(`Route ${id} has an unknown credential class.`);
  if (surface !== "browser")
    throw new Error(
      `Route ${id} is in the wrong manifest list for a channel credential.`,
    );
  return value;
}

/** The path a mint binds: its template without the last segment. */
export function channelBindingPrefix(mintPath) {
  return mintPath.slice(0, mintPath.lastIndexOf("/"));
}

/**
 * Whole-manifest rules: every `channel` route lies under exactly one mint's binding prefix, every
 * mint is a POST that binds at least one path parameter and at least one `channel` route.
 */
export function checkChannelBindings(routes) {
  const mints = routes.filter((route) => route.credential === "channel-mint");
  const channels = routes.filter((route) => route.credential === "channel");
  for (const mint of mints) {
    const prefix = channelBindingPrefix(mint.path);
    if (mint.method !== "POST")
      throw new Error(`Channel mint ${mint.path} must be a POST.`);
    if (!/\{[a-zA-Z][a-zA-Z0-9]*\}/u.test(prefix))
      throw new Error(`Channel mint ${mint.path} binds no path parameter.`);
    if (!channels.some((route) => route.path.startsWith(`${prefix}/`)))
      throw new Error(`Channel mint ${mint.path} binds no channel route.`);
  }
  for (const route of channels) {
    const owners = mints.filter((mint) =>
      route.path.startsWith(`${channelBindingPrefix(mint.path)}/`),
    );
    if (owners.length !== 1)
      throw new Error(
        `Channel route ${route.method} ${route.path} must lie under exactly one channel mint.`,
      );
  }
  for (const route of routes) {
    assertTriggerBudgetContract(route);
    // The channel rate class falls back per replica on a store outage; only a channel route may use it.
    if ((route.rate === "channel") !== (route.credential === "channel"))
      throw new Error(
        `Route ${route.method} ${route.path}: the channel rate class is for channel routes only.`,
      );
  }
}

/**
 * The values a request path gives the parameters of `prefix` (a route template's leading segments),
 * decoded, by position. The path already matched a manifest route under `prefix`.
 */
export function boundValues(prefix, path) {
  const template = prefix.split("/");
  const segments = path.split("/");
  const values = {};
  template.forEach((segment, index) => {
    const name = PARAM.exec(segment)?.[1];
    if (name === undefined) return;
    try {
      values[name] = decodeURIComponent(segments[index] ?? "");
    } catch {
      values[name] = "";
    }
  });
  return values;
}

/** The binding prefix a `channel` (or `channel-mint`) route of this manifest falls under. */
export function bindingPrefixFor(manifest, route) {
  const mints = manifest.routes.filter(
    (entry) => entry.credential === "channel-mint",
  );
  if (route.credential === "channel-mint")
    return channelBindingPrefix(route.path);
  const owner = mints.find((mint) =>
    route.path.startsWith(`${channelBindingPrefix(mint.path)}/`),
  );
  return owner === undefined ? undefined : channelBindingPrefix(owner.path);
}
