import { useMemo } from "react";
import { HOST_API } from "src/config-global";
import { useAuthContext } from "src/auth/hooks";

/**
 * @returns {string} - Full WebSocket URL
 */
export const usePromptStreamUrl = () => {
  const { user } = useAuthContext();
  const hostApi = HOST_API;
  const token = user?.accessToken || "";
  const workspaceId =
    typeof window !== "undefined"
      ? window.sessionStorage.getItem("workspaceId")
      : "";

  return useMemo(() => {
    if (!hostApi || !token) return "";

    const isSecure = HOST_API.includes("https");
    const wsHost = HOST_API.replace(/^https?:\/\//, "").replace(/\/+$/, "");
    const protocol = isSecure ? "wss" : "ws";
    const params = new URLSearchParams({ token });
    if (workspaceId) params.set("workspace_id", workspaceId);
    const baseUrl = `${protocol}://${wsHost}/ws/prompt-stream/?${params.toString()}`;
    return baseUrl;
  }, [hostApi, token, workspaceId]);
};
