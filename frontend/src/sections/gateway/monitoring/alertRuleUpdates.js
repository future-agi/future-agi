import { isEqual } from "lodash";

const EDITABLE_FIELDS = new Set([
  "metric", "condition", "threshold", "window", "cooldown", "severity", "channels", "enabled",
]);
const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

/**
 * Build a patch from a fresh config read, never from the grid's cached array.
 * Names are stable identities: renaming, missing rules and ambiguous names are
 * refused. Only fields the operator actually changed are written. A concurrent
 * change to one of those fields needs a new edit; all other latest facts survive.
 *
 * update-config has no general version/If-Match precondition (its revision is
 * budgets-only). This detects pre-read conflicts, not the remaining GET/POST
 * race. Keyed legacy rules can use a target-only merge patch; arrays must be
 * replaced in full, retaining every other raw entry and every unknown field.
 */
export function buildAlertRulePatch(config, original, changes) {
  const fields = Object.keys(changes);
  if (fields.some((field) => !EDITABLE_FIELDS.has(field))) {
    throw new Error("This rule field cannot be edited here.");
  }
  const raw = config?.alerting?.rules;
  if (!original?.name || (!Array.isArray(raw) && !isObject(raw))) {
    throw new Error("Could not read the stored rules. Reload rules before saving.");
  }
  const entries = Array.isArray(raw)
    ? raw.map((value, key) => ({ key, value, name: value?.name }))
    : Object.entries(raw).map(([key, value]) => ({ key, value, name: value?.name ?? key }));
  const matches = entries.filter((entry) => entry.name === original.name);
  if (matches.length !== 1 || !isObject(matches[0].value)) {
    throw new Error(matches.length > 1
      ? "More than one rule has this name. Resolve duplicate names in Settings before editing."
      : "This rule no longer exists. Reload rules and reopen the rule before saving.");
  }
  const { key, value: latest } = matches[0];
  if (fields.some((field) => !isEqual(latest[field], original[field]))) {
    throw new Error("This rule changed since you opened it. Reload rules and reopen it before saving.");
  }
  if (!Array.isArray(raw)) {
    // Null deletes a field in a dict merge; do not resend other map entries,
    // whose unknown null-valued fields must not be interpreted as deletions.
    const patch = Object.fromEntries(fields.map((field) => [field, changes[field] ?? null]));
    return { alerting: { rules: { [key]: patch } } };
  }
  const updated = { ...latest, ...changes };
  fields.forEach((field) => {
    if (changes[field] === undefined) delete updated[field];
  });
  return { alerting: { rules: raw.map((entry, index) => index === key ? updated : entry) } };
}
