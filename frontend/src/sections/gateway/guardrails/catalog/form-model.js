// A check's settings form: the values it starts from, and how they go back
// into the stored rule. Pure functions, shared by the Guardrails page (saves
// one rule) and the org-config editor (edits a draft).
import { isEqual } from "lodash";
import { defaultActionFor } from "./actions";

// The actions every check takes; a check that can rewrite the request it
// reads also takes mask (actions.js actionChoices).
export const ACTIONS = ["block", "warn", "log"];

const STRING_TYPES = new Set(["text", "select", "async-select"]);
const LIST_TYPES = new Set(["multiselect", "async-multiselect"]);

export function parseMultilineList(value) {
  if (Array.isArray(value)) return value;
  if (typeof value !== "string") return [];
  const seen = new Set();
  return value
    .split(/\n|,/)
    .map((item) => item.trim())
    .filter((item) => item && !seen.has(item) && seen.add(item));
}

// "Legal: lawsuit, attorney" -> {name: "Legal", keywords: [...]}. A line
// without a colon is a topic whose one keyword is its name.
export function parseTopicList(value) {
  if (Array.isArray(value)) return value;
  if (typeof value !== "string") return [];
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const colon = line.indexOf(":");
      if (colon < 0) return { name: line, keywords: [line] };
      const name = line.slice(0, colon).trim();
      const keywords = parseMultilineList(line.slice(colon + 1));
      return { name, keywords: keywords.length ? keywords : [name] };
    })
    .filter((topic) => topic.name.length > 0);
}

export function topicListToText(topics) {
  if (!Array.isArray(topics)) return "";
  return topics
    .map((topic) => `${topic.name}: ${(topic.keywords || []).join(", ")}`)
    .join("\n");
}

const isSet = (value) => value !== undefined && value !== null && value !== "";

// A rule's own keys sit beside its config on an editor's check. Its
// execution mode/stage and a check's settings are different namespaces.
// Accept a legacy top-level setting only when it is one of this field's
// options; an explicit invalid inner value stays visible for repair.
export const RULE_OWN_KEYS = new Set(["mode", "stage"]);

/** A field's stored value on a check: beside its config, or in it. */
export function storedFieldValue(field, check) {
  const config = check?.config || {};
  const top = check?.[field.key];
  if (!RULE_OWN_KEYS.has(field.key)) return top ?? config[field.key];
  if (Object.prototype.hasOwnProperty.call(config, field.key))
    return config[field.key];
  return (field.options || []).includes(top) ? top : undefined;
}

export function invalidSelectValue(field, value) {
  // Provider option lists are suggestions, not complete runtime domains.
  // Only explicit closed domains (policy modes and booleans) require repair.
  if (field.type !== "select" || !field.closedOptions) return false;
  // An absent policy stays absent (the runtime owns its default). Explicit
  // null/empty policies are malformed and require deliberate repair.
  if (!isSet(value)) return field.key === "mode" && value !== undefined;
  // MCP Security accepts native booleans as well as the select's strings.
  // Compare the displayed option, but keep the stored type in the form/save.
  const option = typeof value === "boolean" ? String(value) : value;
  return !field.options?.includes(option);
}

/**
 * The threshold the gateway runs an editor's check at. The editors keep it as
 * `confidence_threshold`: normalizeGuardrails puts there the threshold the
 * config push sends for a rule or an entry of the policy sync's checks list
 * (rule-model pushedThreshold). A check of a checks map is forwarded as
 * stored, and the gateway contract reads its confidence_threshold key when it
 * is saved, even as null, else its confidenceThreshold. A null one is sent as
 * none, which the gateway runs at 0; a check with neither runs at `absent`.
 */
export function storedThreshold(check, absent) {
  const stored =
    check?.confidence_threshold !== undefined
      ? check.confidence_threshold
      : check?.confidenceThreshold;
  return Number(stored === undefined ? absent : stored ?? 0);
}

/**
 * The form's starting values for a check (stored rule or a new one).
 * `defaultThreshold` is what the gateway runs a stored check without a
 * threshold at: 0.8 for a rule, 0 for a check of a checks map.
 */
export function formValuesFor(meta, check, actionOf, defaultThreshold = 0.8) {
  const fields = {};
  const storedSecrets = {};
  (meta?.fields || []).forEach((field) => {
    const value = storedFieldValue(field, check);
    if (field.key === "mode" && check) {
      // Keep explicit null/empty and absent policy modes distinct. Only a new
      // rule gets the catalog default; an existing rule needs deliberate repair.
      fields[field.key] = value;
    } else if (field.type === "password") {
      // Write-only: a stored credential is never put back on screen.
      fields[field.key] = "";
      storedSecrets[field.key] = isSet(value);
    } else if (field.type === "multiline-list") {
      fields[field.key] = Array.isArray(value)
        ? value.join("\n")
        : value ?? field.defaultValue ?? "";
    } else if (field.type === "topic-list") {
      fields[field.key] = topicListToText(value);
    } else if (LIST_TYPES.has(field.type)) {
      fields[field.key] = Array.isArray(value)
        ? value.map(String)
        : check
          ? []
          : field.defaultValue || [];
    } else {
      fields[field.key] = isSet(value) ? value : field.defaultValue ?? "";
    }
  });
  return {
    enabled: check ? check.enabled !== false : true,
    action: check ? actionOf(check) : defaultActionFor(),
    threshold: check ? storedThreshold(check, defaultThreshold) : 0.8,
    fields,
    storedSecrets,
  };
}

/**
 * The rule's `config` after the form: the stored config with the form's
 * fields merged in. Keys the form does not know stay; a field the user
 * cleared is removed; a blank credential keeps the stored one. A field the
 * form shows as stored only (`unread`) stays exactly as stored, and a new
 * rule never gets one. For an existing check, `openingFields` is the form's
 * initial display: unchanged inputs keep their original values and absence,
 * even when the display supplies a default or a save would trim text.
 */
export function formToConfig(
  meta,
  values = {},
  storedConfig = {},
  openingFields,
) {
  const config = { ...(storedConfig || {}) };
  (meta?.fields || []).forEach(({ key, type, unread }) => {
    if (unread || (openingFields && isEqual(values[key], openingFields[key])))
      return;
    const value = values[key];
    // A no-edit save must retain both the policy's bytes and field presence,
    // including null, an empty string or an omitted mode. Never trim/delete it
    // or insert a default unless the user selected a different policy.
    if (key === "mode" && value === storedConfig?.[key]) return;
    const put = (next, keep) => {
      if (keep) config[key] = next;
      else delete config[key];
    };
    if (type === "password") {
      if (typeof value === "string" && value.trim()) config[key] = value.trim();
    } else if (type === "multiline-list") {
      const list = parseMultilineList(value);
      put(list, list.length > 0);
    } else if (type === "topic-list") {
      const topics = parseTopicList(value);
      put(topics, topics.length > 0);
    } else if (LIST_TYPES.has(type)) {
      const list = Array.isArray(value) ? value : [];
      put(list, list.length > 0);
    } else if (type === "number") {
      put(Number(value), isSet(value) && !Number.isNaN(Number(value)));
    } else if (STRING_TYPES.has(type) || !type) {
      const text = typeof value === "string" ? value.trim() : value;
      put(text, isSet(text));
    }
  });
  if (meta?.provider && !openingFields) config.provider = meta.provider;
  return config;
}

/**
 * Why a check's form cannot be saved, for the checks that say so: Topic
 * Restriction with both topic lists empty, Script Detection with no
 * allowed script. null when the form names no reason.
 */
export function incompleteReason(meta, values) {
  const fields = values?.fields || {};
  const empty = (meta?.fields || []).find(
    (field) =>
      field.emptyReason &&
      !(Array.isArray(fields[field.key]) && fields[field.key].length > 0),
  );
  if (empty) return empty.emptyReason;
  if (
    meta?.requiresOneOfReason &&
    !meta.requiresOneOf.some((key) => parseTopicList(fields[key]).length > 0)
  ) {
    return meta.requiresOneOfReason;
  }
  return null;
}

/** True when every required field has a value (a stored credential counts). */
export function formIsComplete(meta, values) {
  const fields = values?.fields || {};
  const filled = (field) => {
    if (field.type === "password") {
      return (
        Boolean(fields[field.key]?.trim?.()) ||
        values.storedSecrets?.[field.key]
      );
    }
    const value = fields[field.key];
    if (Array.isArray(value)) return value.length > 0;
    if (field.type === "multiline-list")
      return parseMultilineList(value).length > 0;
    return isSet(typeof value === "string" ? value.trim() : value);
  };
  if (
    (meta?.fields || []).some((field) =>
      invalidSelectValue(field, fields[field.key]),
    )
  )
    return false;
  const required = (meta?.fields || []).filter((field) => field.required);
  if (!required.every(filled)) return false;
  if (meta?.requiresOneOf) {
    // Only topic lists use this today.
    return meta.requiresOneOf.some(
      (key) => parseTopicList(fields[key]).length > 0,
    );
  }
  return true;
}
