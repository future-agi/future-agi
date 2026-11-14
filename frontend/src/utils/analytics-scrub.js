import { analyticsUrl } from "src/utils/analytics-url";

// Analytics events carry descriptive properties only. Credential-like keys
// (API keys, secrets, tokens, passwords, auth headers, cookies) and free-text
// keys (prompts, messages, descriptions) are dropped at any depth before an
// event is sent, whatever the caller passes.

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

// ...or any word contains one of these (e.g. "secret_ref", "passwordHash"),
const CREDENTIAL_WORD_PARTS = ["secret", "password", "authorization", "cookie"];

// ...or any word is one of these (e.g. "tokenValue", "headersValue"), unless
// the last word says the value is a count, size, id, type or name (e.g.
// "input_token_cost", "token_count", "headerName").
const CREDENTIAL_WORDS = new Set([
  "token",
  "apikey",
  "apikeys",
  "bearer",
  "header",
  "headers",
  "credential",
  "credentials",
  "passphrase",
  "passwd",
]);
const DESCRIPTIVE_LAST_WORDS = new Set([
  "count",
  "counts",
  "cost",
  "costs",
  "limit",
  "limits",
  "used",
  "usage",
  "length",
  "size",
  "total",
  "id",
  "ids",
  "type",
  "types",
  "kind",
  "name",
  "names",
]);

// A key is free text when its last word is one of these (e.g. "statement",
// "system_prompt", "previousPrompt", "prompt_template", "initialMessage").
// Ids, counts and types such as "prompt_id" or "message_count" are kept.
const FREE_TEXT_LAST_WORDS = new Set([
  "statement",
  "prompt",
  "prompts",
  "template",
  "templates",
  "message",
  "messages",
  "content",
  "contents",
  "text",
  "texts",
  "instruction",
  "instructions",
  "requirement",
  "requirements",
  "description",
  "desc",
  "notes",
  "query",
  "search",
  "term",
  "pattern",
  "patterns",
  "objective",
]);

// Whole keys that hold free text although their last word does not say so:
// synthetic data instructions and trace filter values.
const FREE_TEXT_KEYS = new Set(["use_case", "property_value", "filter_value"]);

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
  if (
    words.some((word) =>
      CREDENTIAL_WORD_PARTS.some((part) => word.includes(part)),
    )
  ) {
    return true;
  }
  return (
    !DESCRIPTIVE_LAST_WORDS.has(last) &&
    words.some((word) => CREDENTIAL_WORDS.has(word))
  );
};

export const isFreeTextKey = (key) => {
  const words = keyWords(key);
  return (
    words.length > 0 &&
    (FREE_TEXT_LAST_WORDS.has(words[words.length - 1]) ||
      FREE_TEXT_KEYS.has(words.join("_")))
  );
};

// Numbers and true/false flags under these names say nothing about the text
// or credential itself (e.g. "password_match", "prompt": 3), so they are kept.
const isCountOrFlag = (value) =>
  typeof value === "boolean" || typeof value === "number";

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
      if (isFreeTextKey(key) && !isCountOrFlag(item)) return;
      // A true/false flag such as "password_match" says nothing about the
      // value itself, so it is kept.
      if (isCredentialLikeKey(key) && typeof item !== "boolean") return;
      const scrubbed = scrubValue(item, ancestors);
      if (scrubbed !== undefined || item === undefined) result[key] = scrubbed;
    });
    ancestors.delete(value);
    return result;
  }
  // mixpanel-browser sends a URL object as an empty object; send its address
  // the way the page URL is sent.
  if (value instanceof URL) return analyticsUrl(value.href);
  return value;
};

/**
 * Returns a copy of `properties` without credential-like or free-text keys,
 * at any depth (nested objects and arrays included). The input is never
 * modified. URL objects become their address as analyticsUrl reports it;
 * other values that are not plain objects or arrays are returned as they are.
 */
export const scrubAnalyticsProperties = (properties) =>
  scrubValue(properties, new Set());
