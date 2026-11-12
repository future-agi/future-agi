import { useEffect } from "react";
import { WORKSPACE_UNAVAILABLE_EVENT } from "./workspaceUnavailableEvent";
import { holdWorkspaceRefusals } from "./workspaceRefusalHold";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import useFalconStore from "src/sections/falcon-ai/store/useFalconStore";

// The failed mutation is never replayed: the caller must explicitly retry once
// the fresh, access-checked landing is selected. No request credentials cross
// this event boundary, only scope IDs and a local submission correlation ID.
export { WORKSPACE_UNAVAILABLE_EVENT } from "./workspaceUnavailableEvent";

const UNAVAILABLE_MESSAGE =
  "The selected workspace is unavailable. Select an accessible workspace and retry.";
// A stalled landing read still ends in the refusal message, and a later
// refusal can start a new recovery.
const LANDING_READ_TIMEOUT_MS = 10000;
// The screen that made the refused call may toast its text a little after the
// landing line; that text stays held this long.
const LANDED_HOLD_GRACE_MS = 5000;

// Axios applies `timeout` only in its transport adapters, so the cutoff is
// enforced here; the abort also frees a real request.
function readLanding() {
  const controller = new AbortController();
  let timer;
  const cutoff = new Promise((_, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      reject(new Error("The workspace landing read timed out."));
    }, LANDING_READ_TIMEOUT_MS);
  });
  return Promise.race([
    axios.get(endpoints.auth.me, { signal: controller.signal }),
    cutoff,
  ]).finally(() => clearTimeout(timer));
}

function landingNotice(userInfo) {
  if (!userInfo?.default_workspace_id) {
    return "You no longer have access to that workspace. Choose a workspace to continue.";
  }
  const name =
    userInfo.default_workspace_display_name ||
    userInfo.default_workspace_name ||
    "your default workspace";
  return `You no longer have access to that workspace. Switched to ${name}.`;
}

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
      // Started before the first await, so the interceptor that dispatched
      // this event can claim the refusal (workspaceRefusalHold.js).
      const releaseHold = holdWorkspaceRefusals();
      let graceMs = 0;
      let explained = false;
      try {
        const response = await readLanding();
        if (canApply()) {
          applyUserWorkspace(response.data, organizationId);
          graceMs = LANDED_HOLD_GRACE_MS;
          explained = true;
          // The landing already recovered the tab, so say where the user is
          // now (same wording as organization recovery), not the refusal.
          enqueueSnackbar(landingNotice(response.data), {
            variant: "info",
            preventDuplicate: true,
          });
        }
      } catch {
        // Do not restore a selector known to be stale if the read fails. Account
        // and workspace switching remain available for manual recovery.
        if (canApply()) {
          applyUserWorkspace(null);
          explained = true;
          // Release first: the hold would drop this same text.
          releaseHold();
          enqueueSnackbar(UNAVAILABLE_MESSAGE, {
            variant: "error",
            preventDuplicate: true,
          });
        }
      } finally {
        recovering = false;
        // When canApply() was false, another flow owns the state (logout,
        // organization switch, a manual choice): the hook adds no line, and
        // the calling screen's own toast, dropped while this ran, is shown.
        releaseHold(graceMs, { unexplained: !explained });
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
