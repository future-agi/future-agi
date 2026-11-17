// A copy of a config for display with every stored secret masked.
//
// The org config reads already replace each stored org-config secret with a
// write-only placeholder ("__encrypted__" and a fingerprint, the value
// SecretField shows as dots). The console still masks anything that looks
// like a credential, so a value the server does not know is a secret is
// never painted either:
// - a server placeholder is masked wherever it sits;
// - any key that names a credential is masked;
// - every value under a `headers` object is (header values carry tokens);
// - every value under an `auth` object is, except its `type` and `header`
//   (the MCP API-key auth stores its key as plain `key`);
// - a URL keeps its host, but not its password or token-like query values;
// - a URL under `channels` or `sinks` (alert channels, audit sinks) keeps only
//   its host: a Slack-style webhook URL is itself the token.

export const MASK = "••••••••";

// A key names a credential when it ends with one of these words, read as
// snake_case (camelCase and dashes are folded first). "max_tokens" and
// "per_key_rpm" stay readable; "token", "sas_token", "secretAccessKey" and
// "connection_string" are masked.
const SECRET_ENDING =
  /(^|_)(password|passwd|secret|token|credentials?|authorization|bearer|sas|api_?key|access_?key|private_?key|secret_?key|connection_?string|signing_?key)$/;

// Query parameters that carry a credential beyond SECRET_ENDING's words.
const SECRET_QUERY = /^(key|sig|signature|code|auth|pass|pwd)$/;

const AUTH_PARENT = /^(auth|authentication)$/i;
const AUTH_READABLE = /^(type|header|header_?name)$/;
const NOTIFIER_PARENT = /^(channels|sinks)$/i;
const URL_KEY = /^(url|webhook_?url|endpoint)$/;

// The org config reads' write-only placeholder for a stored secret.
const SERVER_MASK = /^__encrypted__/;

const snakeCase = (key) =>
  String(key)
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .replace(/-/g, "_")
    .toLowerCase();

export function isSecretKey(key) {
  return SECRET_ENDING.test(snakeCase(key));
}

const isPlainObject = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);

function maskValue(value) {
  if (value === "" || value === null || value === undefined) return value;
  if (typeof value === "boolean") return value;
  return MASK;
}

// scheme://[userinfo@]host[:port], then the rest.
const URL_SHAPE =
  /^([a-z][a-z0-9+.-]*:\/\/)(?:([^/?#@\s]*)@)?([^/?#\s]*)(.*)$/i;

function maskUserinfo(userinfo) {
  if (userinfo === undefined) return "";
  const colon = userinfo.indexOf(":");
  // user:password keeps the user; a lone user part may itself be a token.
  return colon >= 0 ? `${userinfo.slice(0, colon)}:${MASK}@` : `${MASK}@`;
}

function maskQuery(rest) {
  const q = rest.indexOf("?");
  if (q < 0) return rest;
  const hash = rest.indexOf("#", q);
  const query = rest.slice(q + 1, hash < 0 ? undefined : hash);
  const masked = query
    .split("&")
    .map((pair) => {
      const eq = pair.indexOf("=");
      if (eq < 0) return pair;
      const name = snakeCase(decodeURIComponentSafe(pair.slice(0, eq)));
      return SECRET_ENDING.test(name) || SECRET_QUERY.test(name)
        ? `${pair.slice(0, eq)}=${MASK}`
        : pair;
    })
    .join("&");
  return `${rest.slice(0, q)}?${masked}${hash < 0 ? "" : rest.slice(hash)}`;
}

function decodeURIComponentSafe(text) {
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
}

// A string that is a URL, with its credentials masked; `hostOnly` drops
// everything after the host. Anything else is returned as it is.
export function maskUrl(value, { hostOnly = false } = {}) {
  if (typeof value !== "string") return value;
  const match = value.match(URL_SHAPE);
  if (!match) return value;
  const [, scheme, userinfo, host, rest] = match;
  const origin = `${scheme}${maskUserinfo(userinfo)}${host}`;
  if (hostOnly)
    return rest && rest !== "/" ? `${origin}/${MASK}` : origin + rest;
  return `${origin}${maskQuery(rest)}`;
}

function maskChild(child) {
  return isPlainObject(child) || Array.isArray(child)
    ? maskSecrets(child, { underHeaders: true })
    : maskValue(child);
}

export function maskSecrets(
  value,
  { underHeaders = false, underNotifier = false } = {},
) {
  if (Array.isArray(value)) {
    return value.map((item) =>
      maskSecrets(item, { underHeaders, underNotifier }),
    );
  }
  if (!isPlainObject(value)) {
    if (underHeaders) return maskValue(value);
    if (typeof value === "string" && SERVER_MASK.test(value)) return MASK;
    return maskUrl(value);
  }
  return Object.fromEntries(
    Object.entries(value).map(([key, child]) => {
      if (underHeaders || isSecretKey(key) || /^headers$/i.test(key)) {
        return [key, maskChild(child)];
      }
      if (AUTH_PARENT.test(key) && isPlainObject(child)) {
        return [
          key,
          Object.fromEntries(
            Object.entries(child).map(([name, inner]) => [
              name,
              AUTH_READABLE.test(snakeCase(name))
                ? maskSecrets(inner)
                : maskChild(inner),
            ]),
          ),
        ];
      }
      if (underNotifier && URL_KEY.test(snakeCase(key))) {
        return [key, maskUrl(child, { hostOnly: true })];
      }
      return [
        key,
        maskSecrets(child, {
          underNotifier: underNotifier || NOTIFIER_PARENT.test(key),
        }),
      ];
    }),
  );
}

// One entry of a config section, masked as if it sat under its own key (the
// version diff lists entries one key at a time).
export function maskEntry(key, value) {
  return maskSecrets({ [key]: value })[key];
}
