import { useEffect, useRef, useCallback } from "react";
import { useAuthContext } from "src/auth/hooks";
import { getAccessToken } from "src/auth/context/jwt/utils";
import { HOST_API } from "src/config-global";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import logger from "src/utils/logger";
import useFalconStore from "../store/useFalconStore";

const pendingFalconMessages = [];
let sharedSocket = null;
let sharedWorkspaceId = null;
let activeHookCount = 0;
let reconnectRetries = 0;
let reconnectTimer = null;
let closeTimer = null;
let pingTimer = null;
let connectTimer = null;
let healthTimer = null;
let sharedSocketHealthy = false;
let sharedSocketLastPongAt = 0;

const FALCON_SOCKET_HEALTH_TIMEOUT_MS = 5000;
const FALCON_SOCKET_HEALTH_TTL_MS = 45000;

const sendOnOpenSocket = (payload) => {
  if (
    sharedSocket?.readyState !== WebSocket.OPEN ||
    !isSharedFalconSocketHealthy(Date.now())
  ) {
    return false;
  }
  sharedSocket.send(JSON.stringify(payload));
  return true;
};

export const shouldReplaceSharedFalconSocket = (
  existingWorkspaceId,
  nextWorkspaceId,
) => Boolean(nextWorkspaceId && existingWorkspaceId !== nextWorkspaceId);

export const buildFalconContextPayload = (context, selectedContext) => {
  if (selectedContext && selectedContext !== "auto") {
    return { page: selectedContext };
  }
  return context;
};

export const isFalconSocketPongFresh = (lastPongAt, now = Date.now()) =>
  Boolean(lastPongAt && now - lastPongAt < FALCON_SOCKET_HEALTH_TTL_MS);

export const isSharedFalconSocketHealthy = (now = Date.now()) =>
  Boolean(
    sharedSocketHealthy &&
      isFalconSocketPongFresh(sharedSocketLastPongAt, now),
  );

export const selectFalconSocketToken = (storedToken, userToken) =>
  storedToken || userToken;

export const buildFalconSocketUrl = ({
  hostApi,
  pageProtocol,
  pageHost,
  sameOrigin,
  token,
  workspaceId,
}) => {
  const wsParams = new URLSearchParams({ token });
  if (workspaceId) {
    wsParams.set("workspace_id", workspaceId);
  }

  if (sameOrigin) {
    const protocol = pageProtocol === "https:" ? "wss" : "ws";
    return `${protocol}://${pageHost}/ws/falcon-ai/?${wsParams.toString()}`;
  }

  const isSecure = hostApi.includes("https");
  const wsHost = hostApi.replace(/^https?:\/\//, "").replace(/\/+$/, "");
  const protocol = isSecure ? "wss" : "ws";
  return `${protocol}://${wsHost}/ws/falcon-ai/?${wsParams.toString()}`;
};

/**
 * WebSocket hook for Falcon AI streaming chat.
 * Follows the same reconnection pattern as use-simulation-socket.js.
 *
 * Usage:
 *   const { sendChat, sendStop, sendFeedback } = useFalconSocket();
 */
export const useFalconSocket = () => {
  const { user } = useAuthContext();
  const { currentWorkspaceId } = useWorkspace();
  const attachedRef = useRef(false);

  const flushPendingMessages = useCallback((ws) => {
    while (
      pendingFalconMessages.length > 0 &&
      ws.readyState === WebSocket.OPEN
    ) {
      const payload = pendingFalconMessages.shift();
      ws.send(JSON.stringify(payload));
      if (payload.type === "chat") {
        logger.debug("Falcon WebSocket sent queued chat payload", {
          conversationId: payload.conversation_id,
        });
      }
    }
  }, []);

  const connect = useCallback(() => {
    const token = selectFalconSocketToken(getAccessToken(), user?.accessToken);
    if (!token) {
      logger.debug("Falcon WebSocket skipped: no access token");
      return;
    }

    const workspaceId = currentWorkspaceId || null;

    if (
      sharedSocket &&
      shouldReplaceSharedFalconSocket(sharedWorkspaceId, workspaceId)
    ) {
      sharedSocketHealthy = false;
      sharedSocketLastPongAt = 0;
      sharedSocket.close(1000);
      sharedSocket = null;
    }

    // Don't open a second socket if one is already connecting/open.
    if (
      sharedSocket &&
      (sharedSocket.readyState === WebSocket.CONNECTING ||
        sharedSocket.readyState === WebSocket.OPEN)
    ) {
      return;
    }

    clearTimeout(reconnectTimer);
    const sameOrigin =
      import.meta.env.VITE_FALCON_WS_SAME_ORIGIN === "true";
    const url = buildFalconSocketUrl({
      hostApi: HOST_API,
      pageProtocol: window.location.protocol,
      pageHost: window.location.host,
      sameOrigin,
      token,
      workspaceId,
    });
    const wsHost = sameOrigin
      ? window.location.host
      : HOST_API.replace(/^https?:\/\//, "").replace(/\/+$/, "");
    logger.debug("Falcon WebSocket connecting", {
      host: wsHost,
      hasWorkspace: Boolean(workspaceId),
      sameOrigin,
    });

    const ws = new WebSocket(url);
    sharedSocket = ws;
    sharedWorkspaceId = workspaceId;
    sharedSocketHealthy = false;
    sharedSocketLastPongAt = 0;
    clearTimeout(connectTimer);
    connectTimer = setTimeout(() => {
      if (sharedSocket === ws && ws.readyState === WebSocket.CONNECTING) {
        logger.warn("Falcon WebSocket connect timeout; retrying");
        ws.close();
        sharedSocket = null;
        connect();
      }
    }, 5000);

    const sendHealthPing = () => {
      if (ws.readyState !== WebSocket.OPEN) return;
      try {
        ws.send(JSON.stringify({ type: "ping" }));
      } catch {
        return;
      }
      clearTimeout(healthTimer);
      healthTimer = setTimeout(() => {
        if (
          sharedSocket === ws &&
          ws.readyState === WebSocket.OPEN &&
          !isSharedFalconSocketHealthy(Date.now())
        ) {
          logger.warn("Falcon WebSocket health check timed out; retrying");
          sharedSocketHealthy = false;
          sharedSocketLastPongAt = 0;
          ws.close();
          sharedSocket = null;
          connect();
        }
      }, FALCON_SOCKET_HEALTH_TIMEOUT_MS);
    };

    const resumeActiveStream = () => {
      const store = useFalconStore.getState();
      if (store.currentConversationId && store.isStreaming) {
        ws.send(
          JSON.stringify({
            type: "reconnect",
            conversation_id: store.currentConversationId,
          }),
        );
      }
    };

    ws.onopen = () => {
      clearTimeout(connectTimer);
      reconnectRetries = 0;
      logger.debug("Falcon WebSocket connected");
      sendHealthPing();

      // Keep connection alive — proxies/load balancers drop idle WebSockets after ~100s
      clearInterval(pingTimer);
      pingTimer = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          sendHealthPing();
        }
      }, 30000);
    };

    ws.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data);
        const { type, data } = parsed;

        // Filter: ignore events from a different conversation
        if (data?.conversation_id) {
          const store = useFalconStore.getState();
          if (
            store.currentConversationId &&
            data.conversation_id !== store.currentConversationId
          ) {
            // Event belongs to a different conversation — ignore it
            return;
          }
        }

        // Map backend message_id to frontend's placeholder message
        // Frontend creates "assistant-<timestamp>", backend sends "<uuid>"
        if (data?.message_id) {
          const store = useFalconStore.getState();
          if (
            store.streamingMessageId &&
            store.streamingMessageId !== data.message_id
          ) {
            // Update the placeholder message ID to match backend
            store.updateMessage(store.streamingMessageId, {
              id: data.message_id,
            });
            store.setStreaming(true, data.message_id);
          }
        }

        const store = useFalconStore.getState();
        switch (type) {
          case "pong":
            if (sharedSocket === ws) {
              const wasHealthy = isSharedFalconSocketHealthy(Date.now());
              sharedSocketHealthy = true;
              sharedSocketLastPongAt = Date.now();
              clearTimeout(healthTimer);
              flushPendingMessages(ws);
              if (!wasHealthy) {
                resumeActiveStream();
              }
            }
            break;

          case "text_delta":
            store.appendTextDelta(data.message_id, data.delta);
            break;

          case "tool_call_start":
            store.addToolCall(data.message_id, {
              call_id: data.call_id,
              tool_name: data.tool_name,
              tool_description: data.tool_description,
              params: data.params,
              status: "running",
              step: data.step,
              result_summary: null,
              result_full: null,
            });
            break;

          case "tool_call_result":
            store.updateToolCall(data.message_id, data.call_id, {
              status: data.status,
              result_summary: data.result_summary,
              result_full: data.result_full,
            });
            break;

          case "iteration_start":
            store.updateMessage(data.message_id, {
              currentIteration: data.iteration,
              maxIterations: data.max_iterations,
            });
            break;

          case "completion":
            store.updateMessage(data.message_id, {
              completion_card: data.completion_card,
            });
            break;

          case "done": {
            store.setStreaming(false);
            break;
          }

          case "stopped": {
            // User clicked stop — clear streaming state so they can send new messages
            store.setStreaming(false);
            break;
          }

          case "cancelled": {
            // Agent was cancelled (server-side) — same as stopped
            store.setStreaming(false);
            break;
          }

          case "title_generated": {
            const convId = data.conversation_id;
            const title = data.title;
            if (convId && title) {
              const updated = (store.conversations || []).map((c) =>
                c.id === convId ? { ...c, title } : c,
              );
              store.setConversations(updated);
              document.title = `${title} | Falcon AI`;
            }
            break;
          }

          case "error":
            store.setStreaming(false);
            store.updateMessage(data.message_id, {
              error: data.error || "An error occurred",
            });
            break;

          case "skill_activated":
            if (data.skill) {
              store.setActiveSkill(data.skill);
            }
            break;

          case "reconnect_status":
            if (data.status === "running") {
              // Stream is still active — replayed events will follow, then live events
              store.setStreaming(true, store.streamingMessageId);
            } else if (data.status === "done") {
              // Stream completed while disconnected — events will replay, then done
              // (the "done" event is part of the buffered events)
            } else if (data.status === "none" || data.status === "cancelled") {
              // No active stream — stop any loading indicators
              store.setStreaming(false);
            }
            break;

          case "navigate":
            // Agent wants to navigate the user to a page
            if (data.path) {
              useFalconStore.getState().setPendingNavigation(data.path);
            }
            break;

          case "widget_render": {
            import("src/components/imagine/useImagineStore").then(
              ({ default: useImagineStore }) => {
                const store = useImagineStore.getState();
                const action = data.action || "add";
                if (action === "add" && data.widget) {
                  store.addWidget(data.widget);
                } else if (action === "update" && data.widget) {
                  store.updateWidget(data.widget.id, data.widget);
                } else if (action === "replace_all") {
                  store.replaceAll(
                    data.widgets || (data.widget ? [data.widget] : []),
                  );
                } else if (action === "remove" && data.widget?.id) {
                  store.removeWidget(data.widget.id);
                } else if (data.widget) {
                  store.addWidget(data.widget);
                }
              },
            );
            break;
          }

          default:
            break;
        }
      } catch {
        // ignore non-JSON
      }
    };

    ws.onerror = () => {
      clearTimeout(connectTimer);
      logger.warn("Falcon WebSocket error");
    };

    ws.onclose = (event) => {
      clearTimeout(connectTimer);
      clearTimeout(healthTimer);
      clearInterval(pingTimer);
      if (sharedSocket !== ws) return;
      sharedSocket = null;
      sharedSocketHealthy = false;
      sharedSocketLastPongAt = 0;
      logger.debug("Falcon WebSocket closed", { code: event.code });

      // Auth-related errors - do NOT retry
      if ([4001, 4401, 4403, 1008].includes(event.code)) {
        pendingFalconMessages.length = 0;
        sharedSocketHealthy = false;
        sharedSocketLastPongAt = 0;
        useFalconStore.getState().setStreaming(false);
        return;
      }

      if (activeHookCount > 0) {
        const delay = Math.min(1000 * 2 ** reconnectRetries, 30000);
        reconnectRetries += 1;
        reconnectTimer = setTimeout(connect, delay);
      }
    };
  }, [user?.accessToken, currentWorkspaceId, flushPendingMessages]);

  useEffect(() => {
    if (!attachedRef.current) {
      activeHookCount += 1;
      attachedRef.current = true;
    }
    clearTimeout(closeTimer);
    connect();
    return () => {
      if (attachedRef.current) {
        activeHookCount = Math.max(0, activeHookCount - 1);
        attachedRef.current = false;
      }
      if (activeHookCount === 0) {
        clearTimeout(reconnectTimer);
        closeTimer = setTimeout(() => {
          if (activeHookCount === 0 && sharedSocket) {
            clearInterval(pingTimer);
            clearTimeout(healthTimer);
            sharedSocketHealthy = false;
            sharedSocketLastPongAt = 0;
            sharedSocket.close(1000);
            sharedSocket = null;
          }
        }, 1000);
      }
    };
  }, [connect]);

  const sendChat = useCallback(
    (message, conversationId, context, fileIds) => {
      const store = useFalconStore.getState();
      const selectedCtx = store.selectedContext;
      const activeSkill = store.activeSkill;

      const contextPayload = buildFalconContextPayload(context, selectedCtx);

      const payload = {
        type: "chat",
        message,
        conversation_id: conversationId,
        context: contextPayload,
        file_ids: fileIds || [],
      };

      if (activeSkill) {
        payload.skill_id = activeSkill.id;
      }

      if (sendOnOpenSocket(payload)) {
        logger.debug("Falcon WebSocket sent chat payload", {
          conversationId,
          messageChars: message.length,
        });
        return;
      }

      pendingFalconMessages.push(payload);
      logger.debug("Falcon WebSocket queued chat payload", {
        pending: pendingFalconMessages.length,
      });
      connect();
    },
    [connect],
  );

  const sendActivateSkill = useCallback((skillId) => {
    sendOnOpenSocket({ type: "activate_skill", skill_id: skillId });
  }, []);

  const sendStop = useCallback(() => {
    sendOnOpenSocket({ type: "stop" });
    // Optimistically clear streaming state so user isn't stuck
    useFalconStore.getState().setStreaming(false);
  }, []);

  const sendFeedback = useCallback((messageId, feedback) => {
    sendOnOpenSocket({
      type: "feedback",
      message_id: messageId,
      feedback,
    });
  }, []);

  return { sendChat, sendStop, sendFeedback, sendActivateSkill };
};

export default useFalconSocket;
