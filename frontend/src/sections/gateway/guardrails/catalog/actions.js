import {
  MASK_ACTION_HELP,
  actionOptionsFor,
} from "../../constants/guardrailCheckActions";

// What each action does on this gateway (guardrails/plugin.go). Every check
// blocks, warns or logs; only a check whose type can rewrite the request it
// reads can also mask (constants/guardrailCheckActions.js, from
// api_contracts/gateway/guardrail-check-actions.json). A check on the model's
// answer that blocks refuses the answer with a 403; on a stream, text
// already sent stays sent and the stream ends with the guardrail's error.
export const ACTION_HELP = {
  block: {
    label: "Block",
    help: "Refuse with a 403 guardrail error.",
  },
  warn: {
    label: "Warn",
    help: "Let it through and record a warning on the request.",
  },
  mask: {
    label: "Mask",
    help: MASK_ACTION_HELP,
  },
  log: {
    label: "Log",
    help: "Let it through and record the match.",
  },
};

// Said under the Action of a check whose stored action it cannot take (mask
// on a check that cannot rewrite the request, which an older console offered
// for every check): the gateway runs it as log.
export const RUNS_AS_LOG =
  "This check cannot take this action; the gateway runs it as Log.";

// What each action does once guardrails are turned on in the gateway
// config, said while they are off (no check runs, so nothing is refused).
const WHEN_ON = "When guardrails are on in the gateway config, ";
export const ACTION_HELP_WHEN_OFF = {
  block: `${WHEN_ON}refuse the request with a guardrail error.`,
  warn: `${WHEN_ON}let it through and record a warning on the request.`,
  mask: `${WHEN_ON}mask the match before the request is sent.`,
  log: `${WHEN_ON}let it through and record the match.`,
};

/**
 * What an action does. `enforced` false: the gateway runs no check, so the
 * text says what the action does once guardrails are on, and promises no
 * refusal now.
 */
export function actionHelp(action, { enforced = true } = {}) {
  if (!enforced) return ACTION_HELP_WHEN_OFF[action];
  return ACTION_HELP[action]?.help;
}

/** An action's name as the console shows it. */
export function actionName(action) {
  return ACTION_HELP[action]?.label || action;
}

/**
 * The Action select's options for a check: the actions it takes, and the
 * stored action too when it takes another (listed disabled, labelled with
 * what the gateway runs), so an unchanged save keeps it.
 */
export function actionChoices(checkName, current) {
  return actionOptionsFor(checkName, current).map(({ value, disabled }) => ({
    value,
    disabled,
    label: disabled ? `${actionName(value)} (runs as Log)` : actionName(value),
  }));
}

/** True when the check does not take its stored action (it runs as log). */
export function runsAsLog(checkName, action) {
  return actionChoices(checkName, action).some(
    (choice) => choice.disabled && choice.value === action,
  );
}

/** The action a new check starts on. */
export function defaultActionFor() {
  return "block";
}
