// Whose words a failed request's error message is, as the drawer names it
// above the message: the log's error_source, which the gateway records beside
// error_code. A log without one (stored before it was recorded) gets no
// label, as before.

import { providerLabel } from "../utils/providerLabel";

/** The label above a failed request's error message, or "" for none. */
export function errorSourceLabel(log) {
  const source = log?.error_source;
  if (source === "provider") {
    const billingMode = log.billing?.mode || log.metadata?.billing_mode;
    const name = providerLabel(log.provider, billingMode);
    return name ? `Error from ${name}` : "Error from the provider";
  }
  if (source === "gateway") {
    return Number(log.status_code) >= 500
      ? "Gateway error"
      : "Refused by the gateway";
  }
  if (source === "client") return "Cancelled by the client";
  return "";
}
