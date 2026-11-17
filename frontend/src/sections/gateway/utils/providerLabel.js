// The provider a customer screen names for a request, an attempt or a
// ledger charge.
//
// A managed call is
// Future AGI's, so customer screens name its provider "Future AGI".
// The upstream the gateway bought it from shows only in the request
// drawer's technical section (Attempt details) and in the docs' public
// subprocessors list.
//
// The label follows the billing mode (or an attempt's credential type),
// never the provider's name: an organization's own OpenRouter key is a
// provider of its own and keeps its name.

export const MANAGED_PROVIDER_LABEL = "Future AGI";

export function isManagedMode(mode) {
  return String(mode ?? "").toLowerCase() === "managed";
}

// The two billing modes have one name on every screen:
// the Logs cell and drawer, the Filters select and its chip, attempts, Try
// it, MCP tool calls, the catalog's Access, Usage Summary, and the API's
// billing-mode groups (agentcc/services/provider_label.py). The dimension
// itself is "Billing mode". A managed call's provider is still
// MANAGED_PROVIDER_LABEL: that names who served it, this names who pays.
export const BILLING_MODE_LABEL = "Billing mode";
export const MANAGED_MODE_LABEL = "Future AGI credits";
export const OWN_KEY_LABEL = "Own key";
export const BILLING_MODE_LABELS = {
  managed: MANAGED_MODE_LABEL,
  byok: OWN_KEY_LABEL,
};

/** A billing mode's name ("Future AGI credits", "Own key"); unknown modes as given. */
export function billingModeLabel(mode) {
  if (mode === undefined || mode === null || mode === "") return "";
  return BILLING_MODE_LABELS[String(mode).toLowerCase()] || String(mode);
}

/** The provider to show: "Future AGI" for a call on Future AGI credits. */
export function providerLabel(provider, billingMode) {
  if (isManagedMode(billingMode)) return MANAGED_PROVIDER_LABEL;
  return provider || "";
}
