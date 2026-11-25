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
import {
  AUTH_REJECTED_CLOSE_CODE,
  MAX_AUTH_FAILURES,
  getCurrentAccessToken,
  refreshRejectedSocketToken,
} from "./socket-auth";

export const WebSocketContext = createContext();

const RECONNECT_INTERVAL = 5000;
const MAX_RECONNECT_DELAY = 30000; // 30 seconds max delay
const MAX_RECONNECT_ATTEMPTS = 10; // Give up after this many consecutive failures
const HEARTBEAT_INTERVAL = 10000;

export const WebSocketProvider = ({ children }) => {
  const { user, authenticated } = useSafeAuthContext();
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState(null);
  const reconnectAttempts = useRef(0);

  const socketRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const heartbeatIntervalRef = useRef(null);
  const accessToken = useRef(null);
  const socketTokenRef = useRef(null);
  const authFailuresRef = useRef(0);
  const refreshingRef = useRef(false);
  const authenticatedRef = useRef(false);
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
    // The server keeps rejecting the token; only a new token reconnects.
    if (authFailuresRef.current >= MAX_AUTH_FAILURES) {
      return;
    }

    // Don't attempt to reconnect if already trying or if connection is already open
    if (
      refreshingRef.current ||
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
      reconnectAttempts.current++;
      // Use ref to avoid circular dependency
      if (createWebSocketConnectionRef.current) {
        createWebSocketConnectionRef.current();
      }
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }, backoffTime);
  }, []);

  // After the server rejects a token, make sure the next attempt carries a
  // current one before reconnecting.
  const refreshTokenThenReconnect = useCallback(
    async (rejectedToken) => {
      if (refreshingRef.current) return;

      refreshingRef.current = true;
      let canRetry;
      try {
        canRetry = await refreshRejectedSocketToken(
          rejectedToken,
          accessToken.current,
        );
      } finally {
        refreshingRef.current = false;
      }
      if (!canRetry) {
        // Nothing to refresh with; a new sign-in brings a new token.
        authFailuresRef.current = MAX_AUTH_FAILURES;
        return;
      }

      if (!authenticatedRef.current) return;
      attemptReconnect();
    },
    [attemptReconnect],
  );

  const createWebSocketConnection = useCallback(() => {
    const token = getCurrentAccessToken(accessToken.current);
    if (!token) {
      logger.debug("No access token found, skipping WebSocket connection.");
      return;
    }

    // Close any existing connection before creating a new one
    closeConnection();

    const isSecure = HOST_API.includes("https");
    const wsHost = HOST_API.replace(/^https?:\/\//, "").replace(/\/+$/, "");
    const protocol = isSecure ? "wss" : "ws";
    const query = new URLSearchParams({ token });
    const wsUrl = `${protocol}://${wsHost}/ws/connect/?${query}`;

    const socket = new WebSocket(wsUrl);
    socketRef.current = socket;
    socketTokenRef.current = token;

    // Create and store event handlers
    const openHandler = () => {
      setIsConnected(true);
      setError(null);
      // Retry counters reset on the first server frame, not here: the server
      // accepts the handshake before it validates the token.
      logger.debug("WebSocket connected");
      startHeartbeat();
    };

    const messageHandler = (event) => {
      // Any server frame (including the heartbeat pong) means the token was
      // accepted.
      reconnectAttempts.current = 0;
      authFailuresRef.current = 0;
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
        url: ws?.url ? ws.url.replace(/token=[^&]+/, "token=***") : "unknown",
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

      if (event.code === AUTH_REJECTED_CLOSE_CODE) {
        authFailuresRef.current += 1;
        if (authFailuresRef.current >= MAX_AUTH_FAILURES) {
          logger.addBreadcrumb({
            category: "websocket",
            level: "warning",
            message: `WebSocket token rejected ${authFailuresRef.current} times; not reconnecting`,
          });
          return;
        }
        if (authenticated) {
          refreshTokenThenReconnect(socketTokenRef.current);
        }
        return;
      }

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
    refreshTokenThenReconnect,
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

  // Consolidated authentication state handling
  useEffect(() => {
    authenticatedRef.current = Boolean(authenticated && user?.accessToken);
    if (authenticated && user?.accessToken) {
      if (accessToken.current !== user?.accessToken) {
        // A new sign-in gets a fresh budget of token rejections.
        authFailuresRef.current = 0;
      }
      accessToken.current = user?.accessToken;

      // A scheduled retry, a refresh in flight or a token the server keeps
      // rejecting owns the next connection; re-renders must not bypass it.
      const retryPending =
        reconnectTimeoutRef.current ||
        refreshingRef.current ||
        authFailuresRef.current >= MAX_AUTH_FAILURES;

      if (
        !retryPending &&
        (!socketRef.current || socketRef.current.readyState !== WebSocket.OPEN)
      ) {
        createWebSocketConnection();
      }
    } else {
      accessToken.current = null;
      closeConnection();
    }
  }, [
    authenticated,
    user?.accessToken,
    createWebSocketConnection,
    closeConnection,
  ]);

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

  // A token refresh still in flight at unmount must not open a socket.
  useEffect(
    () => () => {
      authenticatedRef.current = false;
    },
    [],
  );

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
