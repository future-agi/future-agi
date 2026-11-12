import { storedReader } from "./guardrailCheckRequirements";

const hasValue = (value) =>
  value !== undefined &&
  value !== null &&
  !(typeof value === "string" && value.trim() === "");

const URL_FIELDS = new Set(["endpoint", "base_url"]);
const HIDDEN_URL = "saved URL (details hidden)";

// Display copies only. URL fields never fall back to their stored text, even
// for malformed, scheme-relative or control-character input. Accept only an
// explicit HTTP(S) authority and show its host, never userinfo/path/query.
function urlHost(text) {
  const display = text.trim();
  if (!/^https?:\/\/[^/\\\s]/i.test(display)) return HIDDEN_URL;
  try {
    const url = new URL(display);
    return ["http:", "https:"].includes(url.protocol) && url.host
      ? url.host
      : HIDDEN_URL;
  } catch {
    return HIDDEN_URL;
  }
}

function settingText(value) {
  const text = String(value);
  try {
    const url = new URL(text);
    if (["http:", "https:"].includes(url.protocol)) return urlHost(text);
  } catch {
    // A malformed URL-looking value must not be rendered verbatim either.
  }
  if (/https?\s*:|:\/\//i.test(text)) return HIDDEN_URL;
  return text.length > 60 ? `${text.slice(0, 57)}...` : text;
}

export function storedSettings(meta, check) {
  const read = storedReader(check);
  return (meta.fields || []).flatMap((field) => {
    const value = read(field.key);
    if (field.type === "password") {
      if (!field.required && !hasValue(value)) return [];
      return [`${field.label}: ${hasValue(value) ? "saved" : "not saved"}`];
    }
    if (!hasValue(value)) return [];
    if (URL_FIELDS.has(field.key)) {
      return [
        `${field.label}: ${typeof value === "string" ? urlHost(value) : HIDDEN_URL}`,
      ];
    }
    if (Array.isArray(value)) {
      const values = value.filter((v) => hasValue(v) && typeof v !== "object");
      return values.length
        ? [`${field.label}: ${values.map(settingText).join(", ")}`]
        : [];
    }
    if (typeof value === "object") return [];
    return [`${field.label}: ${settingText(value)}`];
  });
}

// Rule.mode is the pipeline's sync/async mode, not the tool policy's mode.
// Read nested settings first; retain support for old flat checks-map fields.
export function storedToolSettings(check) {
  const mode =
    check?.config?.mode ??
    (["allowlist", "denylist", "audit"].includes(check?.mode)
      ? check.mode
      : undefined);
  const tools = check?.config?.tools ?? check?.tools;
  const patterns = Array.isArray(tools)
    ? tools.filter((tool) => typeof tool === "string").join(", ")
    : typeof tools === "string"
      ? tools
      : "";
  return [
    `Mode: ${hasValue(mode) && typeof mode === "string" ? mode : "not saved"}`,
    `Tool Patterns: ${hasValue(patterns) ? patterns : "none saved"}`,
  ];
}

// The entry the config push sends for a name. It builds the gateway's checks
// map entry by entry, so of two stored entries with one name the later one
// is sent, and the editor's normalization keeps that one too. Callers pass
// stored-only partner names, which no rule-name alias maps onto, so the
// stored name is the name the gateway gets.
function pushedEntry(entries, name) {
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    const entry = entries[i];
    if (entry && typeof entry === "object" && entry.name === name) return entry;
  }
  return undefined;
}

// A rule or policy-sync list entry is pushed with confidence_threshold from
// its threshold: 0.8 when it has none, and none at all when it is saved as
// null, which the gateway runs as 0. A key holding undefined is not in the
// JSON the server keeps, so it counts as absent.
function pushedListThreshold(entry) {
  if (entry?.threshold === undefined) return 0.8;
  return entry.threshold ?? 0;
}

// The threshold the gateway runs a stored check at, read from the section as
// the config push reads it, not from the editor's normalized draft: a
// policy-sync list or rules use threshold, a checks map is forwarded as
// stored. A map entry's threshold is its confidence_threshold key when that
// key is saved, even as null, else its confidenceThreshold; a null or absent
// one is sent as none, which the gateway runs as 0.
export function storedCheckThreshold(section, name) {
  const { checks, rules } = section || {};
  if (Array.isArray(checks) && checks.length) {
    return pushedListThreshold(pushedEntry(checks, name));
  }
  if (checks && typeof checks === "object" && Object.keys(checks).length) {
    const check = checks[name];
    const stored =
      check?.confidence_threshold !== undefined
        ? check.confidence_threshold
        : check?.confidenceThreshold;
    return stored ?? 0;
  }
  return pushedListThreshold(
    Array.isArray(rules) ? pushedEntry(rules, name) : undefined,
  );
}

// Whether the gateway runs a stored entry, read as the config push sends its
// `enabled`. A rule or policy-sync list entry is sent with its enabled, or
// true when it has none. One saved as null is sent without enabled (the
// contract dump leaves out None), and the gateway reads a check without
// enabled as off (Go's zero value): it skips the check, and a static rule of
// that name is disabled. A checks map is forwarded as stored, so a map entry
// saved without enabled, or as null, is off too. A key holding undefined is
// not in the JSON the server keeps, so it counts as absent. Any other value
// keeps the reading the Overview gives it: a rule or list entry is on unless
// false, a map entry only when true.
export function storedEntryOn(entry, storedAsMap) {
  if (typeof entry === "boolean") return entry;
  if (!entry || typeof entry !== "object") return false;
  const { enabled } = entry;
  if (storedAsMap) return enabled === true;
  if (enabled === undefined) return true;
  return enabled !== false && enabled !== null;
}

// Whether the gateway runs a stored check, from the section as the config
// push reads it (see storedCheckThreshold): the later entry of a name in a
// policy-sync list or in rules, or the map's entry.
export function storedCheckOn(section, name) {
  const { checks, rules } = section || {};
  if (Array.isArray(checks) && checks.length) {
    return storedEntryOn(pushedEntry(checks, name), false);
  }
  if (checks && typeof checks === "object" && Object.keys(checks).length) {
    return storedEntryOn(checks[name], true);
  }
  return storedEntryOn(
    Array.isArray(rules) ? pushedEntry(rules, name) : undefined,
    false,
  );
}

// The exact entry behind the summary, without defaults or reconstructed fields.
export function storedCheckEntry(section, name) {
  const { checks, rules } = section || {};
  if (Array.isArray(checks) && checks.length) return pushedEntry(checks, name);
  if (checks && typeof checks === "object" && Object.keys(checks).length) return checks[name];
  return Array.isArray(rules) ? pushedEntry(rules, name) : undefined;
}

export const NEVER_TRIGGERS_WARNING =
  "This check never triggers at a threshold of 1 or higher: its score must be above the threshold.";
