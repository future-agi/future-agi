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
