// Whether this gateway enforces an organization config section, as the
// gateway itself reports it: GET /-/config lists, in org_sections_enforced,
// the org sections this node runs the plugin for, and names in
// org_sections_not_enforced the reason it gives for any it does not run (an
// IP ACL with no trusted proxy: "client IP unknown (trusted_proxies not
// set)"). The config action passes both through unchanged. Nothing here
// claims enforcement from anything but that list.

// The sections the gateway reports on, with the label each note names.
export const ENFORCED_SECTION_LABELS = Object.freeze({
  alerting: "Alerting",
  budgets: "Budgets",
  cache: "Cache",
  ip_acl: "IP ACL",
  rate_limiting: "Rate Limiting",
  tool_policy: "Tool Policy",
});

// "enforced" | "not_enforced" | "unknown", or null while the read is pending.
// A read that failed, or a gateway that does not report the list, is unknown.
export function sectionEnforcement(gatewayConfigQuery, section) {
  if (gatewayConfigQuery?.isLoading) return null;
  if (notEnforcedReason(gatewayConfigQuery, section)) return "not_enforced";
  if (
    section === "cache" &&
    gatewayConfigQuery?.data?.cache?.enabled === false
  ) {
    return "not_enforced";
  }
  const reported = gatewayConfigQuery?.data?.org_sections_enforced;
  if (!Array.isArray(reported)) return "unknown";
  return reported.includes(section) ? "enforced" : "not_enforced";
}

// Why enforcement is unknown: "read_failed" when the config read failed and
// left no list (it says nothing about the gateway): the console's read, or
// Django's own read of the gateway (gateway.config_read "failed"). Else
// "not_reported" (the gateway answered without org_sections_enforced). A list
// kept from an earlier read still counts after a failed refresh.
export function enforcementUnknownCause(gatewayConfigQuery) {
  const data = gatewayConfigQuery?.data;
  const listed = Array.isArray(data?.org_sections_enforced);
  const failed =
    gatewayConfigQuery?.isError || data?.gateway?.config_read === "failed";
  if (failed && !listed) return "read_failed";
  return "not_reported";
}

// The gateway's own reason for not enforcing a section, or null when it
// gives none.
export function notEnforcedReason(gatewayConfigQuery, section) {
  if (gatewayConfigQuery?.isLoading) return null;
  const reasons = gatewayConfigQuery?.data?.org_sections_not_enforced;
  const reason =
    reasons && typeof reasons === "object" ? reasons[section] : null;
  return typeof reason === "string" && reason ? reason : null;
}

export function enforcementSentence(section, state, reason, unknownCause) {
  const label = ENFORCED_SECTION_LABELS[section];
  if (state === "unknown" && unknownCause === "read_failed") {
    return `${label}: enforcement unknown. The gateway's settings report couldn't be read.`;
  }
  if (state === "unknown") {
    return `${label}: enforcement unknown. This gateway does not report which organization settings it enforces.`;
  }
  if (section === "cache" || section === "ip_acl") {
    if (reason) return `${label}: Not enforced on this gateway: ${reason}.`;
    return section === "cache"
      ? "Cache: Not enforced on this gateway. Response caching is off on this gateway."
      : `${label}: Not enforced on this gateway. This gateway does not run the ${label} plugin.`;
  }
  if (reason) {
    return `${label}: Not enforced on this gateway: ${reason}. These settings are saved but not applied to requests.`;
  }
  return `${label}: Not enforced on this gateway. This gateway does not run the ${label} plugin, so these settings are saved but not applied to requests.`;
}
