// Analytics events carry descriptive properties only. Credential-like keys
// (API keys, secrets, tokens, passwords, auth headers, cookies) are dropped at
// any depth before an event is sent, whatever the caller passes.

// A key is split into lower-case words ("livekitApiKey" -> livekit, api, key).
// It is credential-like when its last word is one of these...
const CREDENTIAL_LAST_WORDS = new Set([
  "key",
  "apikey",
  "secret",
  "secrets",
  "token",
  "password",
  "passwd",
  "passphrase",
  "authorization",
  "bearer",
  "cookie",
  "cookies",
  "headers",
  "credential",
  "credentials",
]);

// ...or its last word ends with one of these (e.g. "accesstoken", "privatekey"),
const CREDENTIAL_WORD_SUFFIXES = [
  "token",
  "secret",
  "password",
  "apikey",
  "accesskey",
  "privatekey",
  "secretkey",
];

// ...or any word contains one of these (e.g. "secret_ref", "passwordHash").
const CREDENTIAL_WORD_PARTS = ["secret", "password", "authorization", "cookie"];

const keyWords = (key) =>
  String(key)
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1_$2")
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean);

export const isCredentialLikeKey = (key) => {
  const words = keyWords(key);
  if (words.length === 0) return false;
  const last = words[words.length - 1];
  if (CREDENTIAL_LAST_WORDS.has(last)) return true;
  if (CREDENTIAL_WORD_SUFFIXES.some((suffix) => last.endsWith(suffix))) {
    return true;
  }
  const joined = words.join("_");
  // "api_key_value", "aws_access_key_id", "client_secret_key_ref", ...
  if (
    ["api_key", "access_key", "secret_key", "private_key"].some((name) =>
      joined.includes(name),
    )
  ) {
    return true;
  }
  return words.some((word) =>
    CREDENTIAL_WORD_PARTS.some((part) => word.includes(part)),
  );
};

const isPlainObject = (value) =>
  Object.prototype.toString.call(value) === "[object Object]";

const scrubValue = (value, ancestors) => {
  if (Array.isArray(value)) {
    if (ancestors.has(value)) return undefined;
    ancestors.add(value);
    // Elements keep their positions; a reference back to an enclosing value
    // becomes undefined.
    const result = Array.from(value, (item) => scrubValue(item, ancestors));
    ancestors.delete(value);
    return result;
  }
  if (isPlainObject(value)) {
    if (ancestors.has(value)) return undefined;
    ancestors.add(value);
    const result = {};
    Object.entries(value).forEach(([key, item]) => {
      // A true/false flag such as "password_match" says nothing about the
      // value itself, so it is kept.
      if (isCredentialLikeKey(key) && typeof item !== "boolean") return;
      const scrubbed = scrubValue(item, ancestors);
      if (scrubbed !== undefined || item === undefined) result[key] = scrubbed;
    });
    ancestors.delete(value);
    return result;
  }
  return value;
};

/**
 * Returns a copy of `properties` without credential-like keys, at any depth
 * (nested objects and arrays included). The input is never modified.
 * Values that are not plain objects or arrays are returned as they are.
 */
export const scrubAnalyticsProperties = (properties) =>
  scrubValue(properties, new Set());
