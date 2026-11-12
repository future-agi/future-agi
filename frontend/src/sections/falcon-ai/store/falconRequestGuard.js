import axios from "src/utils/axios";
import useFalconStore from "./useFalconStore";

// Capture before starting asynchronous work; check immediately before every
// completion writes state (and before starting a dependent request). IDs alone
// are insufficient: reset/reselect can return to the same conversation ID.
export function captureFalconRequest({ conversation = false } = {}) {
  const { resetGeneration, conversationGeneration, currentConversationId } =
    useFalconStore.getState();
  const headers = axios.defaults.headers.common;
  const workspaceId = headers["X-Workspace-Id"];
  const organizationId = headers["X-Organization-Id"];
  return () => {
    const state = useFalconStore.getState();
    const currentHeaders = axios.defaults.headers.common;
    return (
      state.resetGeneration === resetGeneration &&
      currentHeaders["X-Workspace-Id"] === workspaceId &&
      currentHeaders["X-Organization-Id"] === organizationId &&
      (!conversation ||
        (state.conversationGeneration === conversationGeneration &&
          state.currentConversationId === currentConversationId))
    );
  };
}
