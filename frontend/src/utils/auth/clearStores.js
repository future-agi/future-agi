import useFalconStore from "src/sections/falcon-ai/store/useFalconStore";
import { resetWorkflowRunStore } from "src/sections/agent-playground/store/workflow-run-store";
import { resetGlobalVariablesDrawerStore } from "src/sections/agent-playground/store/global-variables-drawer-store";
import { resetTemplateLoadingStore } from "src/sections/agent-playground/store/template-loading-store";
import { useAgentDetailsStore } from "src/sections/agents/store/agentDetailsStore";
import { resetPromptState } from "src/sections/workbench-v2/store/usePromptStore";
import { resetUserStore } from "./userStoreResets";
import { resetAllStates as resetDevelopDetailStates } from "src/sections/develop-detail/states";
import logger from "src/utils/logger";
import { resetAllStores } from "src/utils/zustand";

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
  // First every store created through src/utils/zustand, listed below or not,
  // goes back to its initial state, and persisted stores drop what they saved
  // to browser storage. The list below then runs the stores' own clean-ups
  // (Falcon streams, agent-details URL params, the develop summary columns
  // read from localStorage at load), which must win over a reset to the
  // state a store had when its module loaded.
  resetAllStores();

  const resets = [
    ["falcon", () => useFalconStore.getState().resetAll()],
    ["agent-playground", () => resetUserStore("agent-playground")],
    ["workflow-run", resetWorkflowRunStore],
    ["global-variables-drawer", resetGlobalVariablesDrawerStore],
    ["template-loading", resetTemplateLoadingStore],
    [
      "agent-details",
      () => useAgentDetailsStore.getState().resetAgentDetails(),
    ],
    ["prompt", resetPromptState],
    ["graph-builder", () => resetUserStore("graph-builder")],
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
