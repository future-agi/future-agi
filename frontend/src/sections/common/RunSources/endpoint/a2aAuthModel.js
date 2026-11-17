// A2A agent auth (W6 B4). Pure. The kinds an agent card's
// `securitySchemes` can ask for (A2A 0.3: HTTP bearer / basic, API key in a
// header, OAuth 2 client credentials), each taking its secret from the one
// workspace-secret picker. A secret is always a reference: the request
// shows `•••• (from secret NAME)`, never a value.
import {
  SECRET_MASK,
  missingSecretMessage,
  missingSecrets,
} from "./requestModel";

export const AUTH_KINDS = Object.freeze([
  { id: "none", label: "None" },
  { id: "bearer", label: "Bearer token" },
  { id: "basic", label: "Basic (user and password)" },
  { id: "api_key", label: "API key header" },
  { id: "oauth2", label: "OAuth 2 client credentials" },
]);

const s = (v) => String(v ?? "").trim();

/**
 * A card's auth in one shape. The older `{name, secret}` pair (a header
 * from a secret) reads as Bearer on `Authorization` and as an API key on
 * any other header.
 */
export function authOf(auth) {
  if (!auth || typeof auth !== "object") return { kind: "none" };
  if (auth.kind) return { ...auth };
  const name = s(auth.name);
  const secret = s(auth.secret);
  // A header with no secret sends nothing (the card's untouched default).
  if (!secret) return { kind: "none" };
  return /^authorization$/i.test(name) || !name
    ? { kind: "bearer", secret }
    : { kind: "api_key", header: name, secret };
}

/** The secret names an auth references. */
export function authSecrets(auth) {
  const a = authOf(auth);
  return a.kind === "none" ? [] : [s(a.secret)].filter(Boolean);
}

/** What is missing before the auth can be sent (copy for the card). */
export function authIssues(auth, workspaceNames) {
  const a = authOf(auth);
  if (a.kind === "none") return [];
  const out = [];
  if (!s(a.secret)) out.push("Pick the workspace secret this auth sends.");
  if (a.kind === "api_key" && !s(a.header))
    out.push("Name the header the API key goes in, e.g. X-Api-Key.");
  if (a.kind === "basic" && !s(a.username))
    out.push("Enter the user name for Basic auth.");
  if (a.kind === "oauth2") {
    if (!/^https?:\/\/\S+/.test(s(a.token_url)))
      out.push("Enter the token URL, e.g. https://auth.acme.test/oauth/token.");
    if (!s(a.client_id)) out.push("Enter the OAuth client id.");
  }
  if (workspaceNames) {
    const msg = missingSecretMessage(
      missingSecrets(authSecrets(a), workspaceNames),
    );
    if (msg) out.push(msg);
  }
  return out;
}

/**
 * The header the auth adds, for display and for the span:
 * `{name, value}` with the secret masked, or null for None. OAuth 2 sends
 * the token its exchange answered (`token`: the exchange's header), so with
 * no token yet it sends nothing.
 */
export function authHeaderOf(auth, token = null) {
  const a = authOf(auth);
  const from = `${SECRET_MASK} (from secret ${s(a.secret) || "?"})`;
  if (a.kind === "bearer")
    return { name: "Authorization", value: `Bearer ${from}` };
  if (a.kind === "basic")
    return { name: "Authorization", value: `Basic ${s(a.username)}:${from}` };
  if (a.kind === "api_key")
    return { name: s(a.header) || "X-Api-Key", value: from };
  if (a.kind === "oauth2") return token?.value ? { ...token } : null;
  return null;
}

/** One line for the card and run details: `Bearer · secret ACME_TOKEN`. */
export function authSummary(auth) {
  const a = authOf(auth);
  if (a.kind === "none") return "No auth";
  const label = AUTH_KINDS.find((k) => k.id === a.kind)?.label ?? a.kind;
  const where =
    a.kind === "api_key"
      ? ` in ${s(a.header) || "X-Api-Key"}`
      : a.kind === "oauth2"
        ? `${s(a.token_url) ? ` via ${s(a.token_url)}` : ""}${s(a.client_id) ? ` (client ${s(a.client_id)})` : ""}`
        : "";
  return `${label}${where} · secret ${s(a.secret) || "not picked"}`;
}

/** The fields a kind asks for, in order (the form renders these). */
export function authFieldsOf(kind) {
  if (kind === "api_key") return ["header", "secret"];
  if (kind === "basic") return ["username", "secret"];
  if (kind === "oauth2") return ["token_url", "client_id", "secret"];
  if (kind === "bearer") return ["secret"];
  return [];
}

/** The auth an agent card asks for first (its `securitySchemes`), or null. */
export function cardAuthKind(card) {
  const schemes = Object.values(card?.securitySchemes ?? {});
  const first = schemes[0];
  if (!first) return null;
  if (first.type === "http")
    return first.scheme === "basic" ? "basic" : "bearer";
  if (first.type === "apiKey") return "api_key";
  if (first.type === "oauth2") return "oauth2";
  return null;
}
