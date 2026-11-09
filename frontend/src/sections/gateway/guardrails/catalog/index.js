// Every check an org can turn on, with what the console needs to know about
// it: kind (built in, Future AGI, partner), the stage the gateway runs it at
// and the settings it needs. Plain data plus small readers.
import { STORED_ONLY_CHECKS } from "../../constants/storedOnlyChecks";
import { BUILT_IN_CHECKS } from "./builtin-checks";
import { PARTNER_CHECKS_A } from "./partner-checks-a";
import { PARTNER_CHECKS_B } from "./partner-checks-b";
import { invalidSelectValue, storedFieldValue } from "./form-model";

const kindOf = (entry) => {
  if (!entry.provider) return "built-in";
  return entry.provider === "futureagi" ? "futureagi" : "partner";
};

export const CATALOG = [
  ...BUILT_IN_CHECKS,
  ...PARTNER_CHECKS_A,
  ...PARTNER_CHECKS_B,
].map((entry) => ({
  ...entry,
  kind: kindOf(entry),
  // Only hallucination-detection and data-leakage-prevention read the
  // model's answer; every other check (partners included) runs on the request.
  stage: entry.stage || "request",
}));

// Checks the console does not offer: no catalog lists them (Add a check, the
// Org config Guardrails tab). Their entries stay so that a check an
// organization already stores keeps its name and settings, and a save keeps
// it unchanged.
export const NOT_OFFERED_CHECKS = new Set(["mcp-security", "tool-permissions"]);

/** The checks a catalog offers. */
export const OFFERED_CATALOG = CATALOG.filter(
  (entry) =>
    !NOT_OFFERED_CHECKS.has(entry.name) && !STORED_ONLY_CHECKS.has(entry.name),
);

const BY_NAME = new Map(CATALOG.map((entry) => [entry.name, entry]));

export function catalogEntry(name) {
  return BY_NAME.get(name) || null;
}

// The checks whose score on a match is always 1.0, so a threshold (a match
// counts when its score is above it) changes nothing below 1.0 and turns the
// check off at 1.0. Topic Restriction and Language Detection score 1.0 on
// every match (agentcc-gateway guardrails/topic, language). Keyword
// Blocklist, PII Detection and Secret Detection score matches divided by
// max_matches, max_entities or max_secrets, capped at 1.0, with a default
// of 1: 1.0 on any match unless that setting is stored above 1. Tool
// Permissions and MCP Security score 1.0 on a match too.
const SCORES_ONE_ON_MATCH = {
  "topic-restriction": null,
  "language-detection": null,
  "keyword-blocklist": "max_matches",
  "pii-detection": "max_entities",
  "secret-detection": "max_secrets",
  "tool-permissions": null,
  "mcp-security": null,
};

/** True when the check scores 1.0 on every match (no threshold applies). */
export function scoresOneOnMatch(name, check) {
  if (!Object.prototype.hasOwnProperty.call(SCORES_ONE_ON_MATCH, name)) {
    return false;
  }
  const key = SCORES_ONE_ON_MATCH[name];
  if (!key) return true;
  const stored = check?.[key] ?? check?.config?.[key];
  const max = Number(stored ?? 1);
  return !(max > 1);
}

export function checkStage(name) {
  return catalogEntry(name)?.stage || "request";
}

export const KIND_LABELS = {
  "built-in": "Built-in",
  futureagi: "Future AGI",
  partner: "Partner",
  custom: "Custom",
};

// Where the gateway runs a check: on the request before the model is called,
// or on the model's answer.
export const STAGE_LABELS = {
  request: "Before LLM",
  response: "After LLM",
};

const hasValue = (value) => {
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "string") return value.trim().length > 0;
  return value !== undefined && value !== null;
};

/**
 * True when the check cannot do anything yet: a required setting (or, for
 * topic restriction, at least one topic list) is missing from its config. A
 * stored credential counts as set even though the read only returns its
 * "__encrypted__" placeholder.
 */
// A partner or Future AGI check calls a service outside the gateway with an
// account the organization configures. The console shows one only when the
// organization's stored config already holds that account: the check is
// stored, nothing it requires is missing, and its credential (a key, a
// secret or the service's endpoint) is set. A stored credential counts even
// though the read returns only its "__encrypted__" placeholder. A built-in
// check needs no account and is always shown.
const isCredentialField = (field) =>
  field.type === "password" || field.key === "endpoint";

export function partnerConfigured(meta, check) {
  if (!meta || !check) return false;
  const config = check.config || {};
  const value = (key) => check[key] ?? config[key];
  const credentials = (meta.fields || []).filter(isCredentialField);
  if (credentials.length === 0) return false;
  return (
    !needsSetup(meta, check) &&
    credentials.some((field) => hasValue(value(field.key)))
  );
}

/** True when a check is shown: built in, or a partner check configured. */
export function isShownCheck(meta, check) {
  if (STORED_ONLY_CHECKS.has(meta?.name))
    return check !== undefined && check !== null;
  if (!meta || meta.kind === "built-in" || !meta.provider) return true;
  return partnerConfigured(meta, check);
}

export function needsSetup(meta, check) {
  if (!meta) return false;
  const config = check?.config || {};
  const value = (key) => check?.[key] ?? config[key];
  const missing = (meta.fields || []).some(
    (field) =>
      (field.required && !hasValue(storedFieldValue(field, check))) ||
      invalidSelectValue(field, storedFieldValue(field, check)),
  );
  if (missing) return true;
  if (meta.requiresOneOf) {
    return !meta.requiresOneOf.some((key) => hasValue(value(key)));
  }
  return false;
}
