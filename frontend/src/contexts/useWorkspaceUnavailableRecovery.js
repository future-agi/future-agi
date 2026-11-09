import { useEffect } from "react";
import { WORKSPACE_UNAVAILABLE_EVENT } from "./workspaceUnavailableEvent";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import useFalconStore from "src/sections/falcon-ai/store/useFalconStore";

// The failed mutation is never replayed: the caller must explicitly retry once
// the fresh, access-checked landing is selected. No request credentials cross
// this event boundary, only scope IDs and a local submission correlation ID.
export { WORKSPACE_UNAVAILABLE_EVENT } from "./workspaceUnavailableEvent";

export function useWorkspaceUnavailableRecovery({
  authenticated,
  userId,
  organizationId,
  clearWorkspace,
  applyUserWorkspace,
}) {
  useEffect(() => {
    if (!authenticated) return undefined;
    let active = true;
    let recovering = false;

    const recover = async ({ detail = {} }) => {
      const headers = axios.defaults.headers.common;
      // Responses from a previous workspace/org must not clear the new scope.
      if (
        recovering ||
        (detail.workspaceId &&
          detail.workspaceId !== headers["X-Workspace-Id"]) ||
        (detail.organizationId &&
          detail.organizationId !== headers["X-Organization-Id"])
      )
        return;
      recovering = true;
      const requestOrgId = headers["X-Organization-Id"];
      const canApply = () =>
        active &&
        !axios.defaults.headers.common["X-Workspace-Id"] &&
        axios.defaults.headers.common["X-Organization-Id"] === requestOrgId;
      // Invalidate chat before changing the socket scope, including a failed
      // landing read. The next user send must create a fresh conversation.
      const chat = useFalconStore.getState();
      // Preserve only the correlated refused submission, never old history or
      // scope. ChatInput consumes pendingPrompt as a draft, not a send.
      const pending = chat.pendingDraft;
      const draft =
        pending &&
        pending.id === detail.submissionId &&
        (!pending.isCurrent || pending.isCurrent())
          ? pending
          : null;
      chat.resetAll({ draft });
      clearWorkspace();
      enqueueSnackbar(
        "The selected workspace is unavailable. Select an accessible workspace and retry.",
        { variant: "error", preventDuplicate: true },
      );
      try {
        const response = await axios.get(endpoints.auth.me);
        if (canApply()) {
          applyUserWorkspace(response.data, organizationId);
        }
      } catch {
        // Do not restore a selector known to be stale if the read fails. Account
        // and workspace switching remain available for manual recovery.
        if (canApply()) applyUserWorkspace(null);
      } finally {
        recovering = false;
      }
    };

    window.addEventListener(WORKSPACE_UNAVAILABLE_EVENT, recover);
    return () => {
      active = false;
      window.removeEventListener(WORKSPACE_UNAVAILABLE_EVENT, recover);
    };
  }, [
    authenticated,
    userId,
    organizationId,
    clearWorkspace,
    applyUserWorkspace,
  ]);
}
