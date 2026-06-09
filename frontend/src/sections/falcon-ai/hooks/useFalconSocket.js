import { useEffect, useRef, useCallback } from "react";
import { useAuthContext } from "src/auth/hooks";
import { HOST_API } from "src/config-global";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import useFalconStore from "../store/useFalconStore";

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
  const socketRef = useRef(null);
  const retriesRef = useRef(0);
  const timerRef = useRef(null);
  const pingRef = useRef(null);
  const mountedRef = useRef(true);
  // Holds a chat payload sent while the socket was not yet OPEN, so it can be
  // flushed once the connection opens instead of being silently dropped
  // (otherwise the UI streams forever with no reply — TH-4873).
  const pendingChatRef = useRef(null);
  // Resume-after-drop guard: a `reconnect` (which asks the server to replay a
  // stream) must fire ONLY when the socket closed DURING a stream — never on a
  // fresh send. A fresh send also sets isStreaming=true, so an ungated
  // reconnect there gets a `reconnect_status: "none"` reply that clears the
  // live streaming state — dropping the answer or leaving the spinner stuck.
  const droppedDuringStreamRef = useRef(false);
  const reconnectSentRef = useRef(false);

  const {
    appendTextDelta,
    addToolCall,
    updateToolCall,
    updateMessage,
    setStreaming,
    setActiveSkill,
  } = useFalconStore.getState();

  const connect = useCallback(() => {
    const token = user?.accessToken;
    if (!token) return;

    // Don't open a second socket if one is already connecting/open
    if (
      socketRef.current &&
      (socketRef.current.readyState === WebSocket.CONNECTING ||
        socketRef.current.readyState === WebSocket.OPEN)
    ) {
      return;
    }

    const isSecure = HOST_API.includes("https");
    const wsHost = HOST_API.replace(/^https?:\/\//, "").replace(/\/+$/, "");
    const protocol = isSecure ? "wss" : "ws";
    const wsParams = new URLSearchParams({ token });
    if (currentWorkspaceId) {
      wsParams.set("workspace_id", currentWorkspaceId);
    }
    const url = `${protocol}://${wsHost}/ws/falcon-ai/?${wsParams.toString()}`;

    const ws = new WebSocket(url);
    socketRef.current = ws;

    ws.onopen = () => {
      retriesRef.current = 0;

      // Keep connection alive — proxies/load balancers drop idle WebSockets after ~100s
      clearInterval(pingRef.current);
      pingRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "ping" }));
        }
      }, 30000);

      // Flush a chat that was queued while the socket was still connecting
      // (e.g. the first message right after page load, or during a slow
      // handshake). Without this the frame was silently dropped and the UI
      // streamed forever with no reply (TH-4873).
      if (pendingChatRef.current) {
        ws.send(JSON.stringify(pendingChatRef.current));
        pendingChatRef.current = null;
        return;
      }

      // Resume a stream ONLY after a genuine mid-stream drop — never on a fresh
      // send (which also has isStreaming=true). A spurious reconnect there gets
      // a `reconnect_status: "none"` reply that wipes the live streaming state.
      const store = useFalconStore.getState();
      if (
        droppedDuringStreamRef.current &&
        store.currentConversationId &&
        store.isStreaming
      ) {
        ws.send(
          JSON.stringify({
            type: "reconnect",
            conversation_id: store.currentConversationId,
          }),
        );
        reconnectSentRef.current = true;
      }
      droppedDuringStreamRef.current = false;
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
            updateMessage(store.streamingMessageId, { id: data.message_id });
            setStreaming(true, data.message_id);
          }
        }

        switch (type) {
          case "text_delta":
            appendTextDelta(data.message_id, data.delta);
            break;

          case "tool_call_start":
            addToolCall(data.message_id, {
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
            updateToolCall(data.message_id, data.call_id, {
              status: data.status,
              result_summary: data.result_summary,
              result_full: data.result_full,
            });
            break;

          case "iteration_start":
            updateMessage(data.message_id, {
              currentIteration: data.iteration,
              maxIterations: data.max_iterations,
            });
            break;

          case "completion":
            updateMessage(data.message_id, {
              completion_card: data.completion_card,
            });
            break;

          case "done": {
            setStreaming(false);
            break;
          }

          case "stopped": {
            // User clicked stop — clear streaming state so they can send new messages
            setStreaming(false);
            break;
          }

          case "cancelled": {
            // Agent was cancelled (server-side) — same as stopped
            setStreaming(false);
            break;
          }

          case "title_generated": {
            const store = useFalconStore.getState();
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
            setStreaming(false);
            updateMessage(data.message_id, {
              error: data.error || "An error occurred",
            });
            break;

          case "skill_activated":
            if (data.skill) {
              setActiveSkill(data.skill);
            }
            break;

          case "reconnect_status":
            // Defensive: only honor a status we actually solicited (after a real
            // drop). Otherwise a stray/late reconnect_status could clear a live,
            // freshly-sent stream — the exact dropped-answer / stuck-spinner bug.
            if (reconnectSentRef.current) {
              if (data.status === "running") {
                // Stream is still active — replayed events follow, then live events
                setStreaming(
                  true,
                  useFalconStore.getState().streamingMessageId,
                );
              } else if (data.status === "done") {
                // Stream completed while disconnected — events replay, then done
                // (the "done" event is part of the buffered events)
              } else if (
                data.status === "none" ||
                data.status === "cancelled"
              ) {
                // No active stream — stop any loading indicators
                setStreaming(false);
              }
              reconnectSentRef.current = false;
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
      // errors are handled via onclose
    };

    ws.onclose = (event) => {
      clearInterval(pingRef.current);
      if (socketRef.current !== ws) return;
      socketRef.current = null;

      // The active socket dropped mid-stream — mark it so the NEXT onopen
      // resumes via `reconnect`. (A fresh send never closes here, so it won't
      // set this flag and won't trigger the stream-wiping spurious reconnect.)
      if (useFalconStore.getState().isStreaming) {
        droppedDuringStreamRef.current = true;
      }

      // Auth-related errors - do NOT retry
      if ([4001, 4401, 4403, 1008].includes(event.code)) {
        return;
      }

      if (mountedRef.current) {
        const delay = Math.min(1000 * 2 ** retriesRef.current, 30000);
        retriesRef.current += 1;
        timerRef.current = setTimeout(connect, delay);
      }
    };
  }, [
    user?.accessToken,
    currentWorkspaceId,
    appendTextDelta,
    addToolCall,
    updateToolCall,
    updateMessage,
    setStreaming,
    setActiveSkill,
  ]);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      clearTimeout(timerRef.current);
      clearInterval(pingRef.current);
      if (socketRef.current) {
        socketRef.current.close();
        socketRef.current = null;
      }
    };
  }, [connect]);

  const sendChat = useCallback(
    (message, conversationId, context, fileIds) => {
      const store = useFalconStore.getState();
      const selectedCtx = store.selectedContext;
      const activeSkill = store.activeSkill;

      // Build the context payload — override page if user chose a specific context
      const contextPayload =
        selectedCtx && selectedCtx !== "auto"
          ? { ...context, page: selectedCtx }
          : context;

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

      if (
        socketRef.current &&
        socketRef.current.readyState === WebSocket.OPEN
      ) {
        socketRef.current.send(JSON.stringify(payload));
      } else {
        // Socket not open yet (first message after load / slow handshake).
        // Queue it and flush on open instead of silently dropping it, which
        // left the UI streaming forever with no reply (TH-4873).
        pendingChatRef.current = payload;
        connect();
      }
    },
    [connect],
  );

  const sendActivateSkill = useCallback((skillId) => {
    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      socketRef.current.send(
        JSON.stringify({ type: "activate_skill", skill_id: skillId }),
      );
    }
  }, []);

  const sendStop = useCallback(() => {
    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify({ type: "stop" }));
    }
    // Optimistically clear streaming state so user isn't stuck
    useFalconStore.getState().setStreaming(false);
  }, []);

  const sendFeedback = useCallback((messageId, feedback) => {
    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      socketRef.current.send(
        JSON.stringify({
          type: "feedback",
          message_id: messageId,
          feedback,
        }),
      );
    }
  }, []);

  return { sendChat, sendStop, sendFeedback, sendActivateSkill };
};

export default useFalconSocket;
