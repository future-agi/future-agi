import { useCallback, useEffect, useRef } from "react";
import { useAuthContext } from "src/auth/hooks";
import { requestWebSocketUrl } from "src/components/websocket/use-socket";
import { useSocketSessionToken } from "src/components/websocket/socket-recovery";
import { SS_KEY_WORKSPACE_ID } from "src/utils/sessionKeys";

const getWorkspaceId = () => typeof window !== "undefined"
  ? window.sessionStorage.getItem(SS_KEY_WORKSPACE_ID) || ""
  : "";

/**
 * Lazy, uncached ticket factory. Await it immediately before EACH socket open:
 * compared versions, row runs, reruns and reconnects must call it separately.
 * Mounting/rendering does not issue a ticket or start its 60-second lifetime.
 * @returns {(signal?: AbortSignal) => Promise<string>}
 */
export const usePromptStreamUrl = () => {
  const { user } = useAuthContext();
  const token = useSocketSessionToken(user?.accessToken) || "";
  const scope = useRef(null);

  useEffect(() => {
    scope.current = { token };
    return () => { scope.current = null; };
  }, [token]);

  return useCallback(async (signal) => {
    const current = scope.current;
    if (!current || signal?.aborted) {
      throw new DOMException("Prompt connection cancelled", "AbortError");
    }
    const workspaceId = getWorkspaceId();
    const query = workspaceId ? { workspace_id: workspaceId } : {};
    const assertCurrent = () => {
      if (signal?.aborted || scope.current !== current || getWorkspaceId() !== workspaceId) {
        throw new DOMException("Prompt connection cancelled", "AbortError");
      }
    };
    // No shared URL or in-flight promise: even parallel calls mint independently.
    try {
      const url = await requestWebSocketUrl(current.token, "/ws/prompt-stream/", query);
      assertCurrent();
      return url;
    } catch (error) {
      // Also suppress fallback/reconnect work when an obsolete mint fails.
      assertCurrent();
      throw error;
    }
  }, []);
};
