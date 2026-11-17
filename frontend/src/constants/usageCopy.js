// PROPOSED user-visible copy; the site gate supplies the final wording.
// Keep CREDIT_LIMIT_MESSAGE in lockstep with error_codes.py INSUFFICIENT_CREDITS.
export const ERROR_MESSAGES = {
  RATE_LIMIT_MESSAGE:
    "Rate limit reached. Please try again later or upgrade your plan.",
  CREDIT_LIMIT_MESSAGE:
    "This request could not be completed. Try again later; if it keeps happening, contact your administrator.",
  // Compatibility sentinel for stored results and older backend responses.
  LEGACY_CREDIT_LIMIT_MESSAGE:
    "Insufficient credits. Please recharge your account.",
  SUPPORT_MESSAGE:
    "Required action could not be performed, please validate the input data. In case of query, please reach out to us at support@futureagi.com",
};

// PROPOSED: the existing action opens Billing, not a platform-credit checkout.
export const OPEN_BILLING_LABEL = "Open Billing";

// PROPOSED: valid on hosted and self-host without claiming workspace-admin access.
export const BILLING_NO_ACCESS_MESSAGE =
  "Ask an organization Owner or Admin for access to Billing.";

// PROPOSED: self-host has settings, but no cloud-only pricing route.
// Usage-branch codes also include entitlement and license denials; do not assume a limit.
export const SELF_HOST_USAGE_LIMIT_MESSAGE =
  "This action could not be completed. Contact your administrator.";
export const OPEN_SETTINGS_LABEL = "Open settings";
