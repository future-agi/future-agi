// The only metrics the gateway's alerting plugin records — see `collectMetrics`
// in agentcc-gateway/internal/plugins/alerting/alerting.go. A rule on any other
// metric is accepted and then never evaluated, so every surface that writes
// `alerting.rules` picks its options from here.
export const ALERT_METRIC_OPTIONS = [
  { value: "error_count", label: "Error Count" },
  { value: "request_count", label: "Request Count" },
  { value: "cost_total", label: "Total Cost ($)" },
  { value: "latency_avg", label: "Avg Latency (ms)" },
  { value: "tokens_total", label: "Total Tokens" },
];

export const DEFAULT_ALERT_METRIC = "error_count";

// The only channel types the gateway's alerting plugin can deliver to — see
// the channel switch in the same file. Any other type is accepted and then
// skipped with a log line.
export const ALERT_CHANNEL_TYPE_OPTIONS = [
  { value: "webhook", label: "Webhook" },
  { value: "slack", label: "Slack" },
  { value: "log", label: "Log Only" },
];

// The severities an alert rule can carry. The gateway sends the rule's
// severity with every alert (the webhook payload's `severity`, a line in the
// Slack text) and reads a rule saved without one as warning
// (tenant.DefaultAlertSeverity in agentcc-gateway/internal/tenant).
export const ALERT_SEVERITY_OPTIONS = [
  { value: "critical", label: "Critical" },
  { value: "warning", label: "Warning" },
  { value: "info", label: "Info" },
];

export const DEFAULT_ALERT_SEVERITY = "warning";

// The severity the gateway applies to a stored value: one of the three, else
// the default (the platform does not push an unknown value).
export function effectiveAlertSeverity(severity) {
  const value = typeof severity === "string" ? severity.toLowerCase() : "";
  return ALERT_SEVERITY_OPTIONS.some((o) => o.value === value)
    ? value
    : DEFAULT_ALERT_SEVERITY;
}

// After a rule fires, the gateway waits its cooldown before the rule can fire
// again, while its condition holds. A rule saved without one (or with a value
// that is not a duration) waits this long (tenant.DefaultAlertCooldown).
export const DEFAULT_ALERT_COOLDOWN = "15m";

// The window new rules start with, and the one the gateway uses for a rule
// saved without a window (plugins/alerting getOrgManager).
export const DEFAULT_ALERT_WINDOW = "5m";
export const GATEWAY_EMPTY_ALERT_WINDOW = "1m";

// A duration as the gateway reads it (Go time.ParseDuration): one or more
// number+unit pairs, e.g. 30s, 15m, 1h30m; "0" alone is zero.
const GO_DURATION = /^(?:0|(?:\d+(?:\.\d+)?(?:ns|us|µs|ms|s|m|h))+)$/;

export function isAlertDuration(value) {
  return typeof value === "string" && GO_DURATION.test(value.trim());
}

// Whether a rule runs on this gateway, from what the gateway reports
// (GET /-/config org_sections_enforced, passed through by the config
// action). Active only when the gateway lists alerting; never assumed.
export function alertRuleStatus({ rule, alertingEnabled, sectionsEnforced }) {
  if (rule?.enabled === false) {
    return {
      label: "Disabled",
      color: "default",
      tooltip: "This rule is not sent to the gateway.",
    };
  }
  if (alertingEnabled !== true) {
    return {
      label: "Alerting off",
      color: "default",
      tooltip:
        "Per-org alerting is switched off (Settings > Org Config > Alerting), so the gateway evaluates no rule.",
    };
  }
  if (!Array.isArray(sectionsEnforced)) {
    return {
      label: "Enforcement unknown",
      color: "default",
      tooltip:
        "This gateway does not report which org config sections it enforces, so whether this rule runs is unknown.",
    };
  }
  if (!sectionsEnforced.includes("alerting")) {
    return {
      label: "Not enforced on this gateway",
      color: "warning",
      tooltip:
        "This gateway does not run org alert rules: its alerting plugin is not installed.",
    };
  }
  return {
    label: "Active",
    color: "success",
    tooltip: "This gateway evaluates the rule on every request.",
  };
}
