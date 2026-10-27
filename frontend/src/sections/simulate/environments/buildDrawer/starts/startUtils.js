import { enqueueSnackbar } from "notistack";
import { sourceLabel } from "src/sections/agent-playground/connected/connectedAgentLabels";

/** The final action of a UI-only start: says so, builds nothing. */
export const uiOnlyToast = (what) =>
  enqueueSnackbar(`UI only: ${what} isn't wired to the API yet.`, {
    variant: "info",
  });

const CODE_SOURCES = new Set(["github", "archive"]);

/** A connected agent's mark: code for a repository or upload, else a cloud. */
export const agentIconOf = (agent) =>
  CODE_SOURCES.has(agent?.source) ? "solar:code-linear" : "solar:cloud-linear";

/** "Vapi · v3 · 2 environments" */
export const agentMetaOf = (agent) =>
  [
    agent.source && sourceLabel(agent.source),
    agent.latestLabel || "No version yet",
    agent.environmentCount != null &&
      `${agent.environmentCount} environment${agent.environmentCount === 1 ? "" : "s"}`,
  ]
    .filter(Boolean)
    .join(" · ");

/**
 * "{agent} environment", numbered after the agent's existing environments so
 * it doesn't repeat one. The environment list isn't read for this, so a
 * renamed environment can still collide.
 */
export function envNameFor(agent) {
  const base = `${agent.name.replace(/\s+agent$/i, "")} environment`;
  const taken = agent.environmentCount || 0;
  return taken ? `${base} ${taken + 1}` : base;
}

/** A name as a CLI slug. */
export const slugOf = (name) =>
  name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
