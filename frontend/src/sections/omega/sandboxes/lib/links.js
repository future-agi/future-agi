/**
 * Routes for the Sandboxes tab and the pages it links to.
 *
 * The shell lane registers /dashboard/sandboxes and /dashboard/sandboxes/:id/:tab?
 * (paths.dashboard.sandboxes). Runs live in Omega Console, which mirrors the
 * framed EE Console's own routes under /dashboard/console/* (lane
 * console-frame); a run is the Console's chat thread for that conversation.
 */
const ROOT = "/dashboard/sandboxes";

// No "services" tab: EE's customer sandbox API (omega-workspace-sandboxes/v1)
// has no service or port route, so a preview-links tab could only say there is
// nothing to preview. It comes back when the API publishes ports.
export const SANDBOX_TABS = [
  "overview",
  "files",
  "run",
  "snapshots",
  "activity",
];

/**
 * True for a string EE could have issued as a sandbox id (`sbx_…`, `wsbx-…`,
 * `agent-task-…`): one plain path segment. Anything else (an id copied with the
 * page's "…" shortening, stray spaces, `..`) cannot name a sandbox, and EE
 * refuses such a path before any lookup, so the page says "not found" itself
 * instead of passing EE's refusal off as Sandboxes being unreachable.
 */
export function isSandboxId(id) {
  return (
    typeof id === "string" && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/u.test(id)
  );
}

export function sandboxesPath() {
  return ROOT;
}

export function sandboxPath(id, tab) {
  const base = `${ROOT}/${encodeURIComponent(id)}`;
  return tab && tab !== "overview"
    ? `${base}/${encodeURIComponent(tab)}`
    : base;
}

export function runHref(runId) {
  return `/dashboard/console/chat/${encodeURIComponent(runId)}`;
}

export function agentHref(agentId) {
  return `/dashboard/console/agents/${encodeURIComponent(agentId)}`;
}

/** Settings → Sandboxes: a workspace's own sandbox settings (owners and admins). */
export const SANDBOX_SETTINGS_PATH = "/dashboard/settings/sandboxes";
