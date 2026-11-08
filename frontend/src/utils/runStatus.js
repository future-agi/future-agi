// The one status map (Future AGI Design System · Run status). Every status in
// the product maps here to a role, a label and a shape; screens import it and
// never define their own colours. Roles index theme.palette.status.

export const STATUS_ROLES = [
  "success",
  "warning",
  "danger",
  "info",
  "neutral",
  "discovery",
];

// shape: "hollow" draws an outlined dot (waiting), "live" pulses (in progress).
export const RUN_STATUS = {
  queued: { role: "neutral", label: "Queued", shape: "hollow" },
  pending: { role: "neutral", label: "Pending", shape: "hollow" },
  booting: { role: "info", label: "Provisioning", shape: "live" },
  building: { role: "info", label: "Building", shape: "live" },
  running: { role: "info", label: "Running", shape: "live" },
  in_progress: { role: "info", label: "In progress", shape: "live" },
  grading: { role: "info", label: "Grading", shape: "live" },
  syncing: { role: "info", label: "Syncing", shape: "live" },
  passed: { role: "success", label: "Passed" },
  pass: { role: "success", label: "Passed" },
  success: { role: "success", label: "Success" },
  completed: { role: "neutral", label: "Completed" },
  done: { role: "neutral", label: "Done" },
  failed: { role: "danger", label: "Failed" },
  fail: { role: "danger", label: "Failed" },
  build_failed: { role: "danger", label: "Build failed" },
  error: { role: "danger", label: "Error" },
  flaky: { role: "warning", label: "Flaky" },
  needs_input: { role: "warning", label: "Needs input" },
  needs_review: { role: "warning", label: "Needs review" },
  stopped: { role: "neutral", label: "Stopped" },
  cancelled: { role: "neutral", label: "Cancelled" },
  canceled: { role: "neutral", label: "Cancelled" },
  unmeasured: { role: "neutral", label: "Not measured" },
  not_run: { role: "neutral", label: "Not run yet" },
  draft: { role: "neutral", label: "Draft" },
  unset: { role: "neutral", label: "Not set" },
  live: { role: "success", label: "Live" },
  active: { role: "success", label: "Active" },
  sealed: { role: "neutral", label: "Sealed" },
  inactive: { role: "neutral", label: "Inactive" },
  suggested: { role: "discovery", label: "Suggested" },
  new: { role: "discovery", label: "New" },
  resolved: { role: "success", label: "Resolved" },
  connected: { role: "success", label: "Connected" },
  disconnected: { role: "neutral", label: "Disconnected" },
  open: { role: "warning", label: "Open" },
  escalating: { role: "danger", label: "Escalating" },
  acknowledged: { role: "neutral", label: "Acknowledged" },
  for_review: { role: "warning", label: "For review" },
  in_review: { role: "warning", label: "In review" },
  needs_changes: { role: "warning", label: "Needs changes" },
  resubmitted: { role: "info", label: "Resubmitted" },
  skipped: { role: "neutral", label: "Skipped" },
  paused: { role: "warning", label: "Paused" },
  backfilling: { role: "info", label: "Backfilling" },
  timeout: { role: "danger", label: "Timed out" },
  partial: { role: "warning", label: "Partial" },
  expiring: { role: "warning", label: "Expiring" },
  warning: { role: "warning", label: "Warning" },
  processing: { role: "info", label: "Processing" },
  idle: { role: "warning", label: "Idle" },
  rate_limited: { role: "warning", label: "Rate limited" },
  no_new_data: { role: "neutral", label: "No new data" },
  healthy: { role: "success", label: "Healthy" },
  degraded: { role: "warning", label: "Degraded" },
  unreachable: { role: "danger", label: "Unreachable" },
  revoked: { role: "danger", label: "Revoked" },
  expired: { role: "warning", label: "Expired" },
  delivered: { role: "success", label: "Delivered" },
  dead_letter: { role: "neutral", label: "Dead letter" },
};

// camelCase, kebab-case and spaces all become snake_case: "NotStarted",
// "not-started" and "Not started" are the same key.
const key = (v) =>
  v
    ?.toString?.()
    .trim()
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .toLowerCase()
    .replace(/[\s-]+/g, "_") || "";

// Free-form values (API strings, labels) fall back to the closest role.
// Negated and partial forms are checked before success, so "unhealthy",
// "incomplete" or "not_connected" never read as green. They land where the
// map puts their nearest entry: unreachable is danger, partial and open are
// warning, not_run and disconnected are neutral.
function inferRole(k) {
  if (!k) return "neutral";
  if (/fail|error|broken|reject|denied|invalid|unsuccess|unhealth/.test(k))
    return "danger";
  if (/partial|(^|_)(in|un|not_)(complet|resolv|finish)/.test(k))
    return "warning";
  if (
    /(^|_)(not|non|no)_|(^|_)(un|dis)(connect|start|run)/.test(k) ||
    /(^|_)(un|in|non|dis)(pass|success|ok|complete|resolved|healthy|connected)/.test(
      k,
    )
  )
    return "neutral";
  if (/pass|success|ok\b|^ok|complete|resolved|healthy|connected/.test(k))
    return "success";
  if (/warn|flaky|review|attention|degraded|expir/.test(k)) return "warning";
  if (
    /run|progress|process|sync|build|start|(evaluat|edit|finali[sz])ing/.test(k)
  )
    return "info";
  if (/suggest|ai_|new\b/.test(k)) return "discovery";
  return "neutral";
}

/** { role, label, shape } for any status value. */
export function runStatusOf(status, label) {
  const k = key(status);
  const known = RUN_STATUS[k];
  if (known) return { ...known, label: label ?? known.label };
  const text = status?.toString?.() ?? "";
  return {
    role: inferRole(k),
    label:
      label ??
      (text
        ? text.charAt(0).toUpperCase() + text.slice(1).replace(/_/g, " ")
        : ""),
  };
}

/** The palette entry for a role, with a light fallback for bare themes. */
export function statusColors(theme, role) {
  const status = theme.palette.status;
  if (status?.[role]) return status[role];
  const tone = {
    success: "success",
    warning: "warning",
    danger: "error",
    info: "info",
    discovery: "primary",
  }[role];
  const c = tone ? theme.palette[tone] : null;
  return {
    fg: c?.dark || theme.palette.text.secondary,
    bg: c?.lighter || theme.palette.action.hover,
    border: c?.light || theme.palette.divider,
    solid: c?.main || theme.palette.text.disabled,
    onSolid: c?.contrastText || theme.palette.common.white,
  };
}
