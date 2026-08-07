import useFalconStore from "src/sections/falcon-ai/store/useFalconStore";
import { resetAgentPlaygroundStore } from "src/sections/agent-playground/store/agent-playground-store";
import { resetWorkflowRunStore } from "src/sections/agent-playground/store/workflow-run-store";
import { resetGlobalVariablesDrawerStore } from "src/sections/agent-playground/store/global-variables-drawer-store";
import { resetTemplateLoadingStore } from "src/sections/agent-playground/store/template-loading-store";
import { useAgentDetailsStore } from "src/sections/agents/store/agentDetailsStore";
import { resetPromptState } from "src/sections/workbench-v2/store/usePromptStore";
import { resetGraphStore } from "src/components/GraphBuilder/store/graphStore";
import { resetAllStates as resetDevelopDetailStates } from "src/sections/develop-detail/states";
import logger from "src/utils/logger";

// Resets the in-memory Zustand stores that hold user-scoped data.
//
// Called from the auth provider's same-tab logout flow so the previous user's
// data does not surface to the next user signing in on the same tab. The
// cross-tab logout path already triggers a full page reload, which drops
// all module-level Zustand state implicitly; only the in-app logout needs
// this explicit reset.
//
// Tokens, sessionStorage organization/workspace, and the react-query cache
// are cleared elsewhere in the logout callback. This helper targets the
// in-memory store surface only.
//
// PR #82 introduced the pattern for useFalconStore. This is the same pattern
// extended to the remaining stores that hold user-scoped data.
export const resetAllUserStores = () => {
  const resets = [
    ["falcon", () => useFalconStore.getState().resetAll()],
    ["agent-playground", resetAgentPlaygroundStore],
    ["workflow-run", resetWorkflowRunStore],
    ["global-variables-drawer", resetGlobalVariablesDrawerStore],
    ["template-loading", resetTemplateLoadingStore],
    [
      "agent-details",
      () => useAgentDetailsStore.getState().resetAgentDetails(),
    ],
    ["prompt", resetPromptState],
    ["graph-builder", resetGraphStore],
    ["develop-detail", resetDevelopDetailStates],
  ];

  for (const [name, reset] of resets) {
    try {
      reset();
    } catch (error) {
      logger.error(`Failed to reset ${name} store on logout`, error);
    }
  }
};
