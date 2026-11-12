import PropTypes from "prop-types";
import React, {
  createContext,
  useEffect,
  useRef,
  useState,
  useCallback,
  useMemo,
} from "react";
import { useSafeAuthContext } from "src/auth/hooks/use-auth-context";
import { HOST_API } from "src/config-global";
import logger from "src/utils/logger";
import { useSocketResume, useSocketSessionToken, withSocketTicketRetry } from "./socket-recovery";

// CR-07f: session bearers travel only in this JSON POST's Authorization header.
// Never cache a ticket: every socket attempt, including reconnect, needs a new one.
export async function requestWebSocketUrl(token, path, query = {}) {
  if (!token || !["/ws/connect/", "/ws/simulation-updates/", "/ws/prompt-stream/", "/ws/falcon-ai/"].includes(path)) {
    throw new Error("Socket ticket refused");
  }
  return withSocketTicketRetry(token, async () => {
    const base = HOST_API.replace(/\/+$/, "");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(`${base}/accounts/handoff/ws-ticket/`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ path, query }),
        cache: "no-store",
        credentials: "omit",
        redirect: "error",
        signal: controller.signal,
      });
      if (!response.ok) throw Object.assign(new Error("Socket ticket refused"), { status: response.status });
      const { ticket } = await response.json();
      if (typeof ticket !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(ticket)) {
        throw new Error("Socket ticket refused");
      }
      const socketBase = base.replace(/^http:/, "ws:").replace(/^https:/, "wss:");
      return `${socketBase}${path}?ticket=${encodeURIComponent(ticket)}`;
    } finally {
      clearTimeout(timeout);
    }
  });
}

export const WebSocketContext = createContext();

const RECONNECT_INTERVAL = 5000;
const MAX_RECONNECT_DELAY = 30000; // 30 seconds max delay
const MAX_RECONNECT_ATTEMPTS = 10; // Give up after this many consecutive failures
const HEARTBEAT_INTERVAL = 10000;

export const WebSocketProvider = ({ children }) => {
  const { user, authenticated } = useSafeAuthContext();
  const sessionToken = useSocketSessionToken(authenticated ? user?.accessToken : null);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState(null);
  const reconnectAttempts = useRef(0);
  const pendingTicket = useRef(false);
  const retryStopped = useRef(false);

  const socketRef = useRef(null);
  const connectionGeneration = useRef(0);
  const reconnectTimeoutRef = useRef(null);
  const heartbeatIntervalRef = useRef(null);
  const accessToken = useRef(null);
  const createWebSocketConnectionRef = useRef(null);
  const messageListenersRef = useRef(new Set());

  // Store event handlers to properly remove them later
  const eventHandlersRef = useRef({
    open: null,
    message: null,
    error: null,
    close: null,
  });

  const stopHeartbeat = useCallback(() => {
    if (heartbeatIntervalRef.current) {
      clearInterval(heartbeatIntervalRef.current);
      heartbeatIntervalRef.current = null;
    }
  }, []);

  const closeConnection = useCallback(() => {
    connectionGeneration.current += 1;
    pendingTicket.current = false;
    clearTimeout(reconnectTimeoutRef.current);
    reconnectTimeoutRef.current = null;
    stopHeartbeat();

    if (socketRef.current) {
      const socket = socketRef.current;

      // Remove all event listeners
      if (eventHandlersRef.current.open) {
        socket.removeEventListener("open", eventHandlersRef.current.open);
      }
      if (eventHandlersRef.current.message) {
        socket.removeEventListener("message", eventHandlersRef.current.message);
      }
      if (eventHandlersRef.current.error) {
        socket.removeEventListener("error", eventHandlersRef.current.error);
      }
      if (eventHandlersRef.current.close) {
        socket.removeEventListener("close", eventHandlersRef.current.close);
      }

      socket.close();
      socketRef.current = null;
      setIsConnected(false);
    }
  }, [stopHeartbeat]);

  const startHeartbeat = useCallback(() => {
    if (authenticated) {
      stopHeartbeat();
      heartbeatIntervalRef.current = setInterval(() => {
        if (socketRef.current?.readyState === WebSocket.OPEN) {
          try {
            socketRef.current.send(JSON.stringify({ type: "ping" }));
          } catch (e) {
            logger.warn("Heartbeat send failed:", e);
            closeConnection();
          }
        } else if (
          socketRef.current?.readyState !== WebSocket.CONNECTING &&
          !reconnectTimeoutRef.current
        ) {
          logger.warn("Heartbeat failed, websocket not open. Reconnecting...");
          attemptReconnect();
        }
      }, HEARTBEAT_INTERVAL);
    }
  }, [authenticated, closeConnection]);

  const attemptReconnect = useCallback(() => {
    // Don't attempt to reconnect if already trying or if connection is already open
    if (
      !accessToken.current || retryStopped.current || pendingTicket.current ||
      reconnectTimeoutRef.current ||
      socketRef.current?.readyState === WebSocket.OPEN ||
      socketRef.current?.readyState === WebSocket.CONNECTING
    ) {
      return;
    }

    // Reconnection permanently exhausted — stop retrying. The single actionable
    // exception is emitted from errorHandler once this cap is reached; here we
    // just stop the loop so transient drops don't retry forever.
    if (reconnectAttempts.current >= MAX_RECONNECT_ATTEMPTS) {
      logger.addBreadcrumb({
        category: "websocket",
        level: "warning",
        message: `WebSocket reconnection exhausted after ${reconnectAttempts.current} attempts`,
      });
      return;
    }

    // Use exponential backoff with a maximum delay
    const backoffTime = Math.min(
      RECONNECT_INTERVAL * Math.pow(2, reconnectAttempts.current),
      MAX_RECONNECT_DELAY,
    );

    logger.debug(
      `Attempting WebSocket reconnect in ${backoffTime}ms (attempt ${reconnectAttempts.current + 1})...`,
    );

    reconnectTimeoutRef.current = setTimeout(() => {
      reconnectTimeoutRef.current = null;
      reconnectAttempts.current++;
      // Use ref to avoid circular dependency
      createWebSocketConnectionRef.current?.();
    }, backoffTime);
  }, []);

  const createWebSocketConnection = useCallback(async () => {
    if (!accessToken.current || pendingTicket.current || retryStopped.current) return;

    // Close any existing connection before creating a new one
    closeConnection();
    pendingTicket.current = true;

    const generation = connectionGeneration.current;
    const token = accessToken.current;
    let socket;
    try {
      const wsUrl = await requestWebSocketUrl(token, "/ws/connect/");
      if (generation !== connectionGeneration.current || token !== accessToken.current) return;
      socket = new WebSocket(wsUrl);
    } catch (ticketError) {
      if (generation === connectionGeneration.current && token === accessToken.current) {
        pendingTicket.current = false;
        retryStopped.current = ticketError?.retryable === false;
        setError("WebSocket connection error");
        attemptReconnect();
      }
      return;
    } finally {
      if (generation === connectionGeneration.current) pendingTicket.current = false;
    }
    socketRef.current = socket;

    // Create and store event handlers
    const openHandler = () => {
      setIsConnected(true);
      setError(null);
      reconnectAttempts.current = 0; // Reset reconnect attempts on successful connection
      logger.debug("WebSocket connected");
      startHeartbeat();
    };

    const messageHandler = (event) => {
      let message = event.data;
      try {
        message = JSON.parse(event.data);
      } catch {
        // Some websocket producers may send plain text. Forward it unchanged.
      }

      messageListenersRef.current.forEach((listener) => {
        try {
          listener(message);
        } catch (e) {
          logger.error("WebSocket message listener failed", e);
        }
      });
    };

    const errorHandler = () => {
      // WebSocket error events don't provide detailed error information.
      // Extract useful context from the WebSocket state for debugging.
      const ws = socketRef.current;
      const readyStateMap = {
        [WebSocket.CONNECTING]: "CONNECTING",
        [WebSocket.OPEN]: "OPEN",
        [WebSocket.CLOSING]: "CLOSING",
        [WebSocket.CLOSED]: "CLOSED",
      };

      const errorContext = {
        readyState: ws ? readyStateMap[ws.readyState] || ws.readyState : "null",
        url: ws?.url ? ws.url.split("?", 1)[0] : "unknown",
        protocol: ws?.protocol || "none",
        reconnectAttempts: reconnectAttempts.current,
      };

      // Create a proper Error object with meaningful context
      const wsError = new Error(
        `WebSocket connection failed (state: ${errorContext.readyState}, attempts: ${errorContext.reconnectAttempts})`,
      );
      wsError.name = "WebSocketError";

      // Transient WS drops (network blips, navigation, token expiry, proxy
      // idle-timeout) are routine and must NOT create Sentry issues. Record a
      // breadcrumb for context and only capture an exception once reconnection
      // is permanently exhausted (the final attempt).
      if (reconnectAttempts.current >= MAX_RECONNECT_ATTEMPTS) {
        logger.error("WebSocket error:", wsError, errorContext);
      } else {
        logger.addBreadcrumb({
          category: "websocket",
          level: "warning",
          message: wsError.message,
          data: errorContext,
        });
      }
      setError("WebSocket connection error");
      setIsConnected(false);

      if (
        socketRef.current?.readyState !== WebSocket.OPEN &&
        socketRef.current?.readyState !== WebSocket.CONNECTING
      ) {
        attemptReconnect();
      }
    };

    const closeHandler = (event) => {
      setIsConnected(false);
      socketRef.current?.close?.();
      socketRef.current = null;
      stopHeartbeat();

      if (event.code === 1006) {
        logger.debug("WebSocket closed with 1006 - skipping reconnect");
        return;
      }

      logger.debug("WebSocket closed:", event);

      // if (event.code === 4003) {
      //     console.warn('WebSocket closed with 4003 - forcing logout');
      //     setSession(null);
      //     resetUser();
      //     navigate('/auth/jwt/login');
      // } else
      if (authenticated) {
        attemptReconnect();
      }
    };

    // Store handlers for later removal
    eventHandlersRef.current = {
      open: openHandler,
      message: messageHandler,
      error: errorHandler,
      close: closeHandler,
    };

    // Add event listeners
    socket.addEventListener("open", openHandler);
    socket.addEventListener("message", messageHandler);
    socket.addEventListener("error", errorHandler);
    socket.addEventListener("close", closeHandler);
  }, [
    authenticated,
    closeConnection,
    startHeartbeat,
    stopHeartbeat,
    attemptReconnect,
  ]);

  const sendMessage = useCallback(
    (message) => {
      if (socketRef.current?.readyState === WebSocket.OPEN) {
        socketRef.current.send(JSON.stringify(message));
        return true;
      } else {
        logger.warn("Cannot send, WebSocket not open");
        if (
          socketRef.current?.readyState !== WebSocket.CONNECTING &&
          !reconnectTimeoutRef.current
        ) {
          attemptReconnect();
        }
        return false;
      }
    },
    [attemptReconnect],
  );

  const addMessageListener = useCallback((listener) => {
    messageListenersRef.current.add(listener);
    return () => {
      messageListenersRef.current.delete(listener);
    };
  }, []);

  // A new session retires pending work and starts a fresh bounded attempt.
  useEffect(() => {
    accessToken.current = sessionToken;
    reconnectAttempts.current = 0;
    retryStopped.current = false;
    closeConnection();
    if (sessionToken) createWebSocketConnection();
    return closeConnection;
  }, [sessionToken, createWebSocketConnection, closeConnection]);

  useSocketResume(() => {
    if (!accessToken.current || socketRef.current?.readyState === WebSocket.OPEN) return;
    reconnectAttempts.current = 0;
    retryStopped.current = false;
    closeConnection();
    createWebSocketConnection();
  });

  // Network status handling
  useEffect(() => {
    const handleOnline = () => {
      logger.debug("Network reconnected");
      if (
        authenticated &&
        (!socketRef.current || socketRef.current.readyState !== WebSocket.OPEN)
      ) {
        attemptReconnect();
      }
    };

    const handleOffline = () => {
      logger.debug("Network disconnected");
      closeConnection();
    };

    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);

    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
      closeConnection();

      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
    };
  }, [attemptReconnect, closeConnection, authenticated]);

  useEffect(() => {
    createWebSocketConnectionRef.current = createWebSocketConnection;
  }, [createWebSocketConnection]);

  const contextValue = useMemo(
    () => ({
      socket: socketRef.current,
      sendMessage,
      addMessageListener,
      isConnected,
      error,
      closeConnection,
    }),
    [sendMessage, addMessageListener, isConnected, error, closeConnection],
  );

  return (
    <WebSocketContext.Provider value={contextValue}>
      {children}
    </WebSocketContext.Provider>
  );
};

WebSocketProvider.propTypes = {
  children: PropTypes.node.isRequired,
};

export default WebSocketContext;
