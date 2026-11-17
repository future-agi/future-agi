// What activating a config version changes, as sentences: one per setting
// the diff reports, "Rate limit: 60 → 120 requests per minute", never the raw
// "+", "-" and "~" lines. A nested value is summarized in the sentence, and
// its full value, every stored secret masked (maskSecrets.maskEntry), is kept
// as a detail line under it.

import { maskEntry } from "./maskSecrets";

// The section names the settings page shows (orgConfigSections.js).
const SECTION_LABELS = {
  guardrails: "Guardrails",
  routing: "Routing and fallbacks",
  cache: "Cache",
  rate_limiting: "Rate limiting",
  budgets: "Budgets",
  cost_tracking: "Custom pricing",
  ip_acl: "IP access list",
  tool_policy: "Tool policy",
  privacy: "Privacy",
  alerting: "Alerting",
  mcp: "MCP",
  model_database: "Model database",
  model_map: "Model map",
  managed_models: "Future AGI credits",
};

const PER_MINUTE_REQUESTS = "requests per minute";
const PER_MINUTE_TOKENS = "tokens per minute";
const limit = (label, unit) => ({ label, unit, zero: "unlimited" });

// Settings with a label of their own, and for a limit its unit; 0 means no
// limit there (RateLimitingConfigTab's "0 = unlimited").
const FIELDS = {
  rate_limiting: {
    enabled: { label: "Rate limiting" },
    global_rpm: limit("Rate limit", PER_MINUTE_REQUESTS),
    global_tpm: limit("Token limit", PER_MINUTE_TOKENS),
    per_key_rpm: limit("Rate limit per key", PER_MINUTE_REQUESTS),
    per_key_tpm: limit("Token limit per key", PER_MINUTE_TOKENS),
    per_model_rpm: limit("Rate limit per model", PER_MINUTE_REQUESTS),
    per_user_rpm: limit("Rate limit per user", PER_MINUTE_REQUESTS),
  },
  cache: {
    enabled: { label: "Response cache" },
    default_ttl: { label: "Cache lifetime" },
  },
  routing: {
    strategy: { label: "Routing strategy" },
    model_fallbacks: { label: "Fallback chains" },
  },
  managed_models: {
    enabled: { label: "Future AGI credits" },
  },
};

const ACRONYMS = {
  acl: "ACL",
  api: "API",
  id: "ID",
  ip: "IP",
  mcp: "MCP",
  rpm: "RPM",
  tpm: "TPM",
  ttl: "TTL",
  url: "URL",
};

// "default_ttl" or "defaultTtl" as "Default TTL".
function humanize(key) {
  const words = String(key)
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .split(/[_\-\s]+/)
    .filter(Boolean)
    .map((word) => ACRONYMS[word.toLowerCase()] || word.toLowerCase());
  if (!words.length) return String(key);
  const [first, ...rest] = words;
  return [first.charAt(0).toUpperCase() + first.slice(1), ...rest].join(" ");
}

export function sectionLabel(name) {
  return SECTION_LABELS[name] || humanize(name);
}

function fieldFor(section, key) {
  return FIELDS[section]?.[key] || { label: humanize(key) };
}

const isPlainObject = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);

const isScalar = (value) =>
  value === null || ["string", "number", "boolean"].includes(typeof value);

const plural = (n, word) =>
  `${n.toLocaleString("en-US")} ${word}${n === 1 ? "" : "s"}`;

// A nested value whose whole shape a sentence cannot carry.
const isComplex = (value) =>
  (isPlainObject(value) && Object.keys(value).length > 0) ||
  (Array.isArray(value) &&
    (value.length > 3 || !value.every((item) => isScalar(item))));

// A value as words; `field` gives a limit's unlimited 0.
function describe(value, field) {
  if (value === null || value === undefined) return "not set";
  if (typeof value === "boolean") return value ? "on" : "off";
  if (typeof value === "number") {
    if (value === 0 && field.zero) return field.zero;
    return value.toLocaleString("en-US");
  }
  if (typeof value === "string") return value === "" ? "empty" : value;
  if (Array.isArray(value)) {
    if (value.length === 0) return "none";
    if (!isComplex(value)) {
      return value.map((item) => describe(item, {})).join(", ");
    }
    return plural(value.length, "item");
  }
  if (isPlainObject(value)) {
    const n = Object.keys(value).length;
    return n === 0 ? "none" : plural(n, "setting");
  }
  return String(value);
}

// A number that is a real amount takes the field's unit.
const hasUnit = (value, field) =>
  Boolean(field.unit) &&
  typeof value === "number" &&
  !(value === 0 && field.zero);

function withUnit(value, field) {
  const words = describe(value, field);
  return hasUnit(value, field) ? `${words} ${field.unit}` : words;
}

const compact = (value) => JSON.stringify(value);

function line(text, details = []) {
  return { text, detail: details };
}

// [{text, detail}] for one section of the diff (GET org-configs/{id}/diff/:
// {added, removed, changed}), in the order added, removed, changed.
export function changeSentences(name, section) {
  const lines = [];
  Object.entries(section?.added || {}).forEach(([key, raw]) => {
    const field = fieldFor(name, key);
    const value = maskEntry(key, raw);
    if (typeof value === "boolean") {
      lines.push(line(`${field.label}: turned ${value ? "on" : "off"}`));
      return;
    }
    lines.push(
      line(
        `${field.label}: set to ${withUnit(value, field)}`,
        isComplex(value) ? [`Now: ${compact(value)}`] : [],
      ),
    );
  });
  Object.entries(section?.removed || {}).forEach(([key, raw]) => {
    const field = fieldFor(name, key);
    const value = maskEntry(key, raw);
    lines.push(
      line(
        `${field.label}: removed (was ${withUnit(value, field)})`,
        isComplex(value) ? [`Was: ${compact(value)}`] : [],
      ),
    );
  });
  Object.entries(section?.changed || {}).forEach(([key, change]) => {
    const field = fieldFor(name, key);
    const before = maskEntry(key, change?.old);
    const after = maskEntry(key, change?.new);
    if (typeof before === "boolean" && typeof after === "boolean") {
      lines.push(line(`${field.label}: turned ${after ? "on" : "off"}`));
      return;
    }
    // "60 → 120 requests per minute": one unit when both sides carry it.
    const shared = hasUnit(before, field) && hasUnit(after, field);
    const text = shared
      ? `${field.label}: ${describe(before, field)} → ${describe(after, field)} ${field.unit}`
      : `${field.label}: ${withUnit(before, field)} → ${withUnit(after, field)}`;
    const details = [];
    if (isComplex(before)) details.push(`Was: ${compact(before)}`);
    if (isComplex(after)) details.push(`Now: ${compact(after)}`);
    lines.push(line(text, details));
  });
  return lines;
}
