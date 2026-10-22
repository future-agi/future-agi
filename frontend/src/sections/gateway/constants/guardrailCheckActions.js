// The actions the gateway takes for an organization's guardrail check, by
// check type (api_contracts/gateway/guardrail-check-actions.json, which the
// gateway's test and checkActions.test.jsx both read): every check blocks,
// warns or logs, and only a check whose type can rewrite the request it reads
// (pii-detection) can also mask, which replaces each match in the request
// with *** and lets the request through. On any other check the gateway runs
// "mask" as log, so it is not offered there.
const CHECK_ACTIONS = ["block", "warn", "log"];
const MASKING_CHECK_ACTIONS = ["block", "warn", "mask", "log"];

// Check types that can mask, by the names the gateway receives.
const MASKING_CHECKS = new Set(["pii-detection"]);

// A section stored as rules names some checks by their rule names.
const RULE_NAME_TO_CHECK = {
  "pii-detector": "pii-detection",
  "secrets-detector": "secret-detection",
  "injection-detector": "prompt-injection",
};

export const checkTypeOf = (name) => RULE_NAME_TO_CHECK[name] || name;

export const canMask = (name) => MASKING_CHECKS.has(checkTypeOf(name));

// The actions a dialog offers for the check named name.
export const actionsForCheck = (name) =>
  canMask(name) ? MASKING_CHECK_ACTIONS : CHECK_ACTIONS;

export const MASK_ACTION_HELP =
  "Replaces each match in the request with *** and lets the request through.";

// The options a dialog lists for a check whose stored action is current: the
// actions the check takes, and the stored action too when it is not one of
// them (mask on a check that cannot rewrite), so the dialog shows it and an
// unchanged save keeps it. Such an action is listed disabled: the gateway
// runs it as log.
export const actionOptionsFor = (name, current) => {
  const offered = actionsForCheck(name);
  const options = offered.map((value) => ({ value, disabled: false }));
  if (current && !offered.includes(current)) {
    options.push({ value: current, disabled: true });
  }
  return options;
};

export const actionLabel = (value) =>
  value ? value.charAt(0).toUpperCase() + value.slice(1) : value;
