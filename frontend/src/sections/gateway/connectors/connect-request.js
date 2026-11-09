// What the Integrations page sends to connect an app, built only from the
// catalog API's provider card (CN9): the add-from-catalog body, and the body
// for the credential vault (M14, POST /agentcc/mcp/credentials/). Plain
// functions with no imports, so the lane's fixture generator can run them in
// node and replay the bodies against the real views.

// The vault keeps a secret exactly as sent and refuses one with surrounding
// spaces or control characters, or longer than this (mcp_credentials.py).
const MAX_KEY_CHARS = 8192;
// AM-C9's rule for a URL variable value: 1-253 printable ASCII, no space.
const URL_VARIABLE_VALUE = /^[!-~]{1,253}$/;
// A control character (U+0000-U+001F or DEL), which the vault refuses.
const hasControlCharacter = (value) =>
  [...value].some((ch) => ch.charCodeAt(0) < 0x20 || ch.charCodeAt(0) === 0x7f);

// The catalog's sign-in kinds (catalog-model.js isSignInCredential), here
// too so this file keeps no imports.
const SIGN_IN_KINDS = new Set(["oauth", "oauth2", "mcp_oauth"]);
const isSignIn = (credential) => SIGN_IN_KINDS.has(credential?.kind);

// Whether a provider's credential is a key the admin stores now (one shared
// secret for the org); "none" needs nothing, and a sign-in takes no key.
export function needsKey(credential) {
  return credential?.mode === "shared" && !isSignIn(credential);
}

// The body for POST apps/{app}/add/: the provider, the URL variables the
// user filled in, and the key the user typed for a provider that takes one
// (CN-ADD: the add lists the provider's tools with that key and stores it in
// the vault in the same write). An empty field is left out, so the catalog's
// default (if any) applies; a name the provider does not declare is never
// sent. The key is sent exactly as typed, as credential.value; where it goes
// comes from the catalog, never from the request, and a provider that needs
// no key is sent none (the add refuses one). A key provider is added in the
// API's default account mode, one shared account for the org; a sign-in
// provider is added in `mode` ("user": each person's own account, or
// "agent": one for the org), with no credential, which the sign-in brings.
export function addRequestBody(provider, values = {}, key = "", mode = null) {
  const urlVariables = {};
  for (const variable of provider.url_variables || []) {
    const value = values[variable.name];
    if (typeof value === "string" && value !== "") {
      urlVariables[variable.name] = value;
    }
  }
  const body = { provider: provider.id, url_variables: urlVariables };
  if (isSignIn(provider.credential)) {
    if (mode) body.mode = mode;
    return body;
  }
  if (needsKey(provider.credential) && typeof key === "string" && key !== "") {
    body.credential = { value: key };
  }
  return body;
}

// The catalog's pattern, matched whole, as the server's re.fullmatch does.
// A pattern the browser cannot compile is left to the server to check.
function matchesPattern(pattern, value) {
  try {
    return new RegExp(`^(?:${pattern})$`).test(value);
  } catch {
    return true;
  }
}

// Per-field problems with the URL variables the user typed, by variable
// name; empty when the add can be sent. The server checks the same rules.
export function urlVariableProblems(provider, values = {}) {
  const problems = {};
  for (const variable of provider.url_variables || []) {
    const value = values[variable.name] ?? "";
    if (value === "") {
      if (variable.default == null) problems[variable.name] = "Required.";
    } else if (!URL_VARIABLE_VALUE.test(value)) {
      problems[variable.name] =
        "Use printable characters with no space, at most 253.";
    } else if (!matchesPattern(variable.pattern, value)) {
      problems[variable.name] = `Does not match ${variable.pattern}`;
    }
  }
  return problems;
}

// Why a typed key cannot be stored, or null. The vault never trims or
// alters a key, so a pasted line break is reported, not removed.
export function keyProblem(value) {
  if (!value) return "Enter the key.";
  if (value !== value.trim()) {
    return "Remove the spaces or line breaks at the start or end of the key.";
  }
  if (hasControlCharacter(value)) {
    return "Remove the line break or other control character inside the key.";
  }
  if (value.length > MAX_KEY_CHARS) {
    return `A key can be at most ${MAX_KEY_CHARS} characters.`;
  }
  return null;
}

// The vault body for a provider's shared key. A header key goes by its
// header name (none for a bearer token, which the vault puts in
// Authorization); a key in a query parameter or a URL variable goes by
// where it goes and its name. Constant parts are never sent: the gateway
// adds them from the catalog entry the server's catalog_ref names.
export function credentialRequestBody(serverId, credential, value) {
  const body = { server_id: serverId, kind: credential.kind };
  if (credential.in && credential.in !== "header") {
    body.in = credential.in;
    body.name = credential.name;
  } else if (credential.header_name) {
    body.header_name = credential.header_name;
  }
  return { ...body, value, scope: "shared" };
}

// Why a typed OAuth client secret cannot be stored, or null: the vault's own
// rules for a secret (as keyProblem), never trimmed or altered.
export function clientSecretProblem(value) {
  if (!value) return "Enter the client secret.";
  if (value !== value.trim()) {
    return "Remove the spaces or line breaks at the start or end of the secret.";
  }
  if (hasControlCharacter(value)) {
    return "Remove the line break or other control character inside the secret.";
  }
  if (value.length > MAX_KEY_CHARS) {
    return `A secret can be at most ${MAX_KEY_CHARS} characters.`;
  }
  return null;
}

// A refusal's message from the API, never one that repeats what was typed.
export function refusalText(error, typed) {
  const message =
    (typeof error?.message === "string" && error.message) ||
    (typeof error?.detail === "string" && error.detail) ||
    "Something went wrong. Try again.";
  return typed && message.includes(typed)
    ? "The server refused the request."
    : message;
}
