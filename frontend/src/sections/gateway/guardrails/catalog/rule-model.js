// The org config's guardrail section, read the way the gateway runs it.
//
// The section comes in three shapes: a `rules` list (console saves), a
// `checks` map (older editor saves) and a `checks` list (the guardrail-policy
// sync). The editors work on one `checks` map keyed by catalog name; each
// entry keeps its stored rule name in `_originalName`.
import { catalogEntry, needsSetup, scoresOneOnMatch } from "./index";
import { formToConfig, formValuesFor, storedThreshold } from "./form-model";
import { fixedScoreNote } from "./stored-policy";
import { canMask } from "../../constants/guardrailCheckActions";

export {
  ACTIONS,
  formIsComplete,
  formToConfig,
  formValuesFor,
  parseMultilineList,
  parseTopicList,
} from "./form-model";

// Org-config rule name -> catalog name, where they differ.
export const RULE_TO_CATALOG = {
  "pii-detector": "pii-detection",
  "secrets-detector": "secret-detection",
  "injection-detector": "prompt-injection",
};

/**
 * A rule id as the gateway names it (logs, block messages) -> the catalog
 * name the console shows everywhere; an id the catalog does not know stays.
 */
export function ruleLabel(name) {
  return catalogEntry(RULE_TO_CATALOG[name] || name)?.label || name;
}

const isPlainObject = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);

/**
 * The threshold the config push sends for a rule or an entry of the policy
 * sync's checks list: its `threshold`, 0.8 when it has none, and none at all
 * when it is saved as null, which the gateway runs at 0 (config_push.py reads
 * rule.get("threshold", 0.8), and the contract's dump leaves a None out). No
 * other spelling on such an entry is read. The stored value is never
 * rewritten: this is what the editors show and compare against.
 */
export function pushedThreshold(entry) {
  if (entry?.threshold === undefined) return 0.8;
  return entry.threshold ?? 0;
}

/**
 * A stored rule as both guardrail editors read it into their checks map
 * (here and settings/GuardrailConfigTab.jsx), and as useConfigPatch
 * guardrailsForSave compares a draft entry with it.
 */
export function ruleAsCheck(rule) {
  return {
    enabled: rule.enabled !== false,
    action: rule.action || "block",
    confidence_threshold: pushedThreshold(rule),
    stage: rule.stage || rule.phase,
    mode: rule.mode,
    config: rule.config,
    _originalName: rule.name,
  };
}

/**
 * An entry of the policy sync's checks list as both editors read it: the
 * entry as stored, with the threshold the push sends it at as its
 * `confidence_threshold`, the key the editors read.
 */
export function listEntryAsCheck(check) {
  return {
    ...check,
    confidence_threshold: pushedThreshold(check),
    _originalName: check.name,
  };
}

function checksArrayToObject(checks) {
  const normalized = {};
  checks.forEach((check) => {
    if (!isPlainObject(check) || !check.name) return;
    const catalogName = RULE_TO_CATALOG[check.name] || check.name;
    normalized[catalogName] = listEntryAsCheck(check);
  });
  return normalized;
}

const isStoredChecksList = (saved) =>
  Array.isArray(saved?.checks) && saved.checks.length > 0;

/** The section as a `checks` map keyed by catalog name, whatever its shape. */
export function normalizeGuardrails(raw) {
  if (!raw) return { checks: {} };
  if (isPlainObject(raw.checks) && Object.keys(raw.checks).length > 0) {
    return raw;
  }
  if (Array.isArray(raw.checks) && raw.checks.length > 0) {
    return { ...raw, checks: checksArrayToObject(raw.checks) };
  }
  const rules = raw.rules;
  if (!Array.isArray(rules) || rules.length === 0) {
    return { ...raw, checks: isPlainObject(raw.checks) ? raw.checks : {} };
  }
  const checks = { ...(isPlainObject(raw.checks) ? raw.checks : {}) };
  rules.forEach((rule) => {
    if (!rule?.name) return;
    const catalogName = RULE_TO_CATALOG[rule.name] || rule.name;
    checks[catalogName] = ruleAsCheck(rule);
  });
  return { ...raw, checks };
}

/**
 * True when the stored section has a non-empty `checks` list or map: the
 * config push reads those and ignores `rules`, so a single-rule save (which
 * edits `rules`) would not take effect.
 */
export function sectionHasChecks(saved) {
  const checks = saved?.checks;
  if (Array.isArray(checks)) return checks.length > 0;
  return isPlainObject(checks) && Object.keys(checks).length > 0;
}

const STORED_ACTIONS = ["block", "warn", "mask", "log"];

// What the gateway runs a stored check without an action or a threshold at:
// a rule gets block and 0.8 from the config push, a check of a checks map is
// forwarded as stored and runs as log, at 0.
const defaultsFor = (asMap) =>
  asMap ? { action: "log", threshold: 0 } : { action: "block", threshold: 0.8 };

/**
 * The action the gateway applies to a check: block, warn or log, and mask on
 * a check that can rewrite the request it reads (pii-detection). It runs
 * anything else (mask elsewhere included) as log.
 */
export function actionOf(check, { asMap = false, name } = {}) {
  const action = check?.action;
  if (action === undefined || action === null || action === "") {
    return defaultsFor(asMap).action;
  }
  if (action === "mask") {
    return canMask(name || check?._originalName || check?.name)
      ? "mask"
      : "log";
  }
  return ["block", "warn", "log"].includes(action) ? action : "log";
}

/**
 * The action a check is stored with, for its form: a stored mask stays mask
 * (listed disabled where the check cannot take it), so a save that changes
 * nothing keeps it.
 */
export function storedActionOf(check, { asMap = false } = {}) {
  const action = check?.action;
  if (STORED_ACTIONS.includes(action)) return action;
  return actionOf(check, { asMap });
}

/**
 * The threshold the gateway runs an editor's check at (form-model
 * storedThreshold): a rule's or a checks-list entry's pushed threshold, which
 * normalizeGuardrails keeps as confidence_threshold, or a checks-map check's
 * as the gateway contract reads it.
 */
export function thresholdOf(
  check,
  { asMap = false, defaultThreshold = defaultsFor(asMap).threshold } = {},
) {
  return storedThreshold(check, defaultThreshold);
}

const statsByName = (list, key) =>
  new Map((Array.isArray(list) ? list : []).map((item) => [item?.[key], item]));

/**
 * One grid row per rule in the section, in stored order, with its last
 * analytics window (`ruleStats`, guardrail-rules) and feedback accuracy
 * (`feedback`, guardrail-feedback summary). Rules the catalog does not know
 * (webhook or expression checks) are kept as custom rows.
 */
export function ruleRows(
  normalized,
  { ruleStats, feedback, asMap = false } = {},
) {
  const stats = statsByName(ruleStats, "rule");
  const reviews = statsByName(feedback, "check_name");
  return Object.entries(normalized?.checks || {}).map(
    ([catalogName, check]) => {
      const meta = catalogEntry(catalogName);
      const ruleName = check?._originalName || check?.name || catalogName;
      const stat = stats.get(catalogName) || stats.get(ruleName) || {};
      const review = reviews.get(catalogName) || reviews.get(ruleName);
      return {
        id: catalogName,
        ruleName,
        label: meta?.label || catalogName,
        description:
          meta?.description ||
          "A custom check from this organization's config.",
        kind: meta?.kind || "custom",
        stage: meta?.stage || "request",
        enabled: check?.enabled !== false,
        action: actionOf(check, { asMap, name: catalogName }),
        storedAction: storedActionOf(check, { asMap }),
        asMap,
        threshold:
          (meta && "hideConfidence" in meta && meta.hideConfidence) ||
          scoresOneOnMatch(catalogName, check)
            ? null
            : thresholdOf(check, { asMap }),
        // A stored threshold that stops a fixed-score check, said where the
        // threshold is not shown.
        thresholdNote: fixedScoreNote(meta, thresholdOf(check, { asMap })),
        triggers: Number(stat.trigger_count || 0),
        blocked: Number(stat.block_count || 0),
        warned: Number(stat.warn_count || 0),
        accuracy:
          review &&
          review.correct_rate !== undefined &&
          review.correct_rate !== null
            ? Number(review.correct_rate)
            : null,
        needsSetup: needsSetup(meta, check),
        check,
      };
    },
  );
}

// Text fields are a display projection, not a lossless stored representation.
// Only serialize fields whose displayed values actually changed.
function configForEditedFields(meta, form, check, asMap) {
  if (!check) return formToConfig(meta, form.fields || {}, {});
  const shown = formValuesFor(
    meta,
    check,
    (value) => storedActionOf(value, { asMap }),
    asMap ? 0 : 0.8,
  );
  const fields = (meta?.fields || []).filter(
    ({ key }) =>
      Object.prototype.hasOwnProperty.call(form.fields || {}, key) &&
      JSON.stringify(form.fields[key]) !== JSON.stringify(shown.fields[key]),
  );
  if (!fields.length) return check.config ?? {};
  return formToConfig(
    { ...meta, fields },
    form.fields || {},
    check.config ?? {},
  );
}

/**
 * The stored entry a row shows: of the section's checks list when it has one
 * (the push reads that and ignores rules), else of its rules, the last entry
 * whose name is the row's catalog name or an alias of it. normalizeGuardrails
 * keeps that one, the config push sends that one, and update-guardrail edits
 * that one in a checks list (guardrail_policy_checks._list_index).
 */
function storedEntry(saved, catalogName) {
  const entries = isStoredChecksList(saved)
    ? saved.checks
    : Array.isArray(saved?.rules)
      ? saved.rules
      : [];
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    const entry = entries[i];
    if (
      isPlainObject(entry) &&
      entry.name &&
      (RULE_TO_CATALOG[entry.name] || entry.name) === catalogName
    ) {
      return entry;
    }
  }
  return null;
}

/**
 * The update-guardrail body for one rule, built from the stored entry the
 * drawer shows (storedEntry). On a rules list update-guardrail stores the
 * rule as sent (`rules[i] = updated`), so everything the stored rule had is
 * carried over: its `_policy` (where its encrypted credentials live; without
 * it the push cannot resolve the "__encrypted__" placeholders), mode and any
 * config keys the form does not show. On the policy sync's checks list it
 * merges what is sent over the stored entry (edit_in_checks_list), so only
 * the fields the form changed are sent, the threshold as `threshold`, and
 * nothing (null) when the form changed none. No stage is added: the gateway
 * never receives one, and runs an organization's check where its type runs.
 * A rule the section does not hold yet is sent whole, from the form.
 */
export function ruleBodyForSave({ saved, check, meta, catalogName, form }) {
  const base = storedEntry(saved, catalogName);
  const ruleName = base?.name || check?._originalName || check?.name;
  const shown = base
    ? formValuesFor(
        meta,
        check || normalizeGuardrails(saved).checks[catalogName],
        storedActionOf,
      )
    : null;
  const config = formToConfig(
    meta,
    form.fields || {},
    base?.config ?? check?.config ?? {},
    shown?.fields,
  );
  const next = {
    action: form.action,
    threshold: Number(form.threshold),
    enabled: Boolean(form.enabled),
  };
  if (!base) {
    const name = ruleName || catalogName;
    return { name, config: { name, mode: "sync", ...next, config } };
  }
  const edit = {};
  Object.entries(next).forEach(([key, value]) => {
    if (value !== shown[key]) edit[key] = value;
  });
  if (!sameConfig(config, base.config)) edit.config = config;
  if (isStoredChecksList(saved)) {
    return Object.keys(edit).length ? { name: ruleName, config: edit } : null;
  }
  return { name: ruleName, config: { ...base, ...edit } };
}

/** A draft `checks` map entry from the form (the org-config editor's path). */
export function draftCheck(meta, form, existing = {}) {
  const config = configForEditedFields(
    meta,
    form,
    Object.keys(existing).length ? existing : null,
    true,
  );
  const next = {
    ...existing,
    enabled: Boolean(form.enabled),
    action: form.action,
    confidence_threshold: Number(form.threshold),
  };
  if (meta?.provider) next.provider = meta.provider;
  if (Object.keys(config).length > 0 || existing?.config) next.config = config;
  return next;
}

const sameConfig = (a, b) =>
  JSON.stringify(a ?? {}) === JSON.stringify(b ?? {});

/**
 * The update-guardrail body for a check of a section stored as a checks map:
 * update-guardrail edits such a check in the map and takes only enabled,
 * action, confidence_threshold and config on it, so only the keys the form
 * changed are sent, by the map's names, and nothing else the check does
 * changes. A check the map does not have yet is sent whole. Null when the
 * form changed nothing.
 */
export function mapEditForSave({ check, meta, catalogName, form }) {
  const name = check?._originalName || catalogName;
  const opening = check
    ? formValuesFor(meta, check, (c) => storedActionOf(c, { asMap: true }), 0)
    : null;
  const config = formToConfig(
    meta,
    form.fields || {},
    check?.config ?? {},
    opening?.fields,
  );
  const next = {
    enabled: Boolean(form.enabled),
    action: form.action,
    confidence_threshold: Number(form.threshold),
    config,
  };
  if (!check) return { name, config: next };
  const shown = {
    enabled: check.enabled !== false,
    action: storedActionOf(check, { asMap: true }),
    confidence_threshold: thresholdOf(check, { asMap: true }),
  };
  const edit = {};
  ["enabled", "action", "confidence_threshold"].forEach((key) => {
    if (next[key] !== shown[key]) edit[key] = next[key];
  });
  if (!sameConfig(config, check.config)) edit.config = config;
  return Object.keys(edit).length ? { name, config: edit } : null;
}
