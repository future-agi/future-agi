// The auto-reload authorization a person agrees to before any save with
// auto-reload on (managed-credits/settings/). The terms and their version are
// an exact copy of futureagi/agentcc/auto_reload_terms.py, which the generated
// settings contract also publishes (consent_version's description); a terms
// change bumps the version there, regenerates the contract and updates this
// module together (__tests__/autoReloadConsent.test.jsx fails otherwise).
//
// The server refuses every enabled save without the current version, so the
// form sends it only after the person agrees, again on every enabled save; a
// save that turns auto-reload off never carries it. Nothing here supplies
// consent on anyone's behalf.

export const AUTO_RELOAD_CONSENT_VERSION = "auto-reload-v1";

export const AUTO_RELOAD_CONSENT_TERMS =
  "I authorize Future AGI to charge my saved payment method for the selected " +
  "auto-reload amount whenever my credit balance is at or below the selected " +
  "threshold, subject to configured limits. These charges may occur without " +
  "me being present. I can stop future auto-reloads by disabling auto-reload " +
  "in my credit settings; a payment already initiated may still complete.";

export const CONSENT_TITLE = "Auto-reload authorization";
export const CONSENT_VERSION_LABEL = `Terms version ${AUTO_RELOAD_CONSENT_VERSION}`;
export const CONSENT_CHECKBOX_LABEL =
  "I agree to the auto-reload authorization above";
export const CONSENT_INCOMPLETE_TEXT =
  "Set the reload threshold and amount to see the charge you authorize.";
export const CONSENT_EVERY_SAVE_NOTE =
  "Each save with auto-reload on records your agreement to these terms again.";
export const CONSENT_NOT_ACKNOWLEDGED_ERROR =
  "Agree to the auto-reload authorization before saving with auto-reload on.";
export const CONSENT_OUTDATED_SUFFIX =
  "Reload this page to see the current terms.";
export const USD_NOT_A_NUMBER_ERROR = "Enter amounts in USD as numbers.";
export const SETTINGS_NOT_SAVED_ERROR =
  "The settings were not saved. Try again.";
export const BILLING_UNAVAILABLE_NOTE =
  "Credit purchase is unavailable. You can turn off auto-reload and save without agreeing again.";

// This release runs no automatic reload. The server reports this reason for
// every organization, whatever it has stored, and refuses turning auto-reload
// on with the same code; the form then offers no terms and no agreement.
export const AUTO_RELOAD_UNAVAILABLE_REASON = "auto_reload_unavailable";
export const AUTO_RELOAD_UNAVAILABLE_TEXT =
  "Automatic reloads are not available in this release.";
export const AUTO_RELOAD_NOT_RUNNING_TEXT = `Auto-reload is on in your settings but is not running. ${AUTO_RELOAD_UNAVAILABLE_TEXT}`;
export const AUTO_RELOAD_TURN_OFF_TO_SAVE = `${AUTO_RELOAD_UNAVAILABLE_TEXT} Turn off auto-reload to save your other settings.`;

/** Whether the credit summary says no automatic reload runs in this build. */
export function autoReloadUnavailable(summary) {
  return (
    summary?.auto_reload?.disabled_reason === AUTO_RELOAD_UNAVAILABLE_REASON
  );
}

/** The server's refusals of an enabled save for want of a current agreement. */
export const CONSENT_REFUSAL_CODES = Object.freeze([
  "auto_reload_consent_required",
  "auto_reload_consent_outdated",
  "auto_reload_consent_actor_required",
]);

// Format the same canonical number sent in JSON, not the ledger's rounded
// display figure. Expand exponent notation and pad cents without rounding
// any significant digit, including for inputs the server may later refuse.
function consentUsd(value) {
  const number = usd(value);
  if (number === null) throw new RangeError("A USD amount must be set.");
  const [coefficient, exponent = "0"] = Math.abs(number).toString().split("e");
  const [integer, fraction = ""] = coefficient.split(".");
  const digits = integer + fraction;
  const point = integer.length + Number(exponent);
  const decimal =
    point <= 0
      ? `0.${"0".repeat(-point)}${digits}`
      : `${digits.padEnd(point, "0").slice(0, point)}.${digits.slice(point)}`;
  const [whole, part = ""] = decimal.split(".");
  return `${number < 0 ? "-" : ""}$${whole}.${part.padEnd(2, "0")}`;
}

/** The charge the person authorizes, for the threshold and amount shown. */
export function chosenChargeText(threshold, amount) {
  return `Future AGI will charge ${consentUsd(amount)} to your saved payment method whenever your credit balance is at or below ${consentUsd(threshold)}.`;
}

// A form value as the contract's JSON number: empty is null; anything else
// must be a finite number (the form refuses other input before saving, so a
// throw here is a programming error, never a request).
function usd(value) {
  if (value === "" || value === null || value === undefined) return null;
  const number = Number(value);
  if (!Number.isFinite(number)) {
    throw new RangeError("A USD amount must be a finite number.");
  }
  return number;
}

/**
 * The managed-credits/settings/ body. An enabled body is built only with the
 * current version the person agreed to; a disabled body never carries one,
 * whatever is passed. USD values are JSON numbers, as the contract types them.
 */
export function creditSettingsBody({
  lowBalance,
  enabled,
  threshold,
  amount,
  consentVersion,
}) {
  const lowBalanceUsd = usd(lowBalance);
  if (enabled !== true) {
    return {
      low_balance_threshold_usd: lowBalanceUsd,
      auto_reload: { enabled: false, threshold_usd: null, amount_usd: null },
    };
  }
  if (consentVersion !== AUTO_RELOAD_CONSENT_VERSION) {
    throw new Error("auto-reload consent not acknowledged");
  }
  return {
    low_balance_threshold_usd: lowBalanceUsd,
    auto_reload: {
      enabled: true,
      consent_version: AUTO_RELOAD_CONSENT_VERSION,
      threshold_usd: usd(threshold),
      amount_usd: usd(amount),
    },
  };
}

/** A failed settings save, in the server's own words where it gave any. */
export function settingsErrorText(err) {
  const result = err?.result;
  const text =
    (result &&
      typeof result === "object" &&
      typeof result.message === "string" &&
      result.message) ||
    (typeof result === "string" && result) ||
    (typeof err?.message === "string" && err.message) ||
    SETTINGS_NOT_SAVED_ERROR;
  return err?.code === "auto_reload_consent_outdated"
    ? `${text} ${CONSENT_OUTDATED_SUFFIX}`
    : text;
}
