const ENTITLEMENT_CODES = new Set([
  "ENTITLEMENT_DENIED",
  "ENTITLEMENT_LIMIT",
  "FREE_TIER_LIMIT",
  "PAYMENT_REQUIRED",
  "ACCOUNT_SUSPENDED",
  "LICENSE_EXPIRED",
  "LICENSE_FEATURE_DENIED",
  "BUDGET_PAUSED",
]);
const FIELDS = {
  name: "monitor name",
  critical_threshold_value: "critical threshold",
  warning_threshold_value: "warning threshold",
  threshold_operator: "threshold operator",
  notification_emails: "notification email",
  project: "project",
  organization: "organization",
  metric: "saved metric",
};

/** Axios errors are normalized to the body plus statusCode, not response.data.
 * Never render server messages here: a 5xx or a response-contract error can
 * arrive AFTER persistence, and a blind retry can create a duplicate monitor.
 */
export function monitorFailureMessage(failure) {
  const status = failure?.statusCode;
  const code = failure?.code || failure?.error?.code;
  if (
    status === 402 ||
    ((status === 403 || status === 400) &&
      (failure?.upgrade_required === true || ENTITLEMENT_CODES.has(code)))
  ) {
    const detail = failure?.error?.detail;
    const atLimit =
      code === "ENTITLEMENT_LIMIT" ||
      code === "FREE_TIER_LIMIT" ||
      (Number.isFinite(detail?.limit) &&
        Number.isFinite(detail?.current_usage) &&
        detail.current_usage >= detail.limit &&
        detail.limit >= 0);
    return atLimit
      ? "Monitor creation was blocked by the organization’s monitor limit. Ask an organization admin to review monitor usage and the organization plan before trying again."
      : "Monitor creation was blocked by organization access or entitlement checks. Ask an organization admin to check the organization plan, monitor limit and billing or license status. Changing the monitor fields will not resolve this.";
  }
  if (status === 403) {
    return "Monitor could not be created. Workspace member access is required. Check your access before retrying.";
  }
  if (
    (status === 400 || status === 422) &&
    failure?.type === "validation_error"
  ) {
    const fields = [
      ...new Set(
        [failure.attr, ...Object.keys(failure.details || {})]
          .map((field) => (Object.hasOwn(FIELDS, field) ? FIELDS[field] : null))
          .filter(Boolean),
      ),
    ];
    return `Monitor could not be created. Check ${fields.length ? fields.join(", ") : "the name, threshold and notification email"}, then retry.`;
  }
  return "Could not confirm monitor creation. The monitor may already exist. Check Monitors in this workspace before creating another; do not retry blindly. If the outcome is still unclear, ask an organization admin for help.";
}
