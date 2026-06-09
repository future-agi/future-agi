import PropTypes from "prop-types";
import React, {
  createContext,
  useEffect,
  useRef,
  useState,
  useCallback,
  useMemo,
} from "react";
import { useNavigate } from "react-router";
import { useSafeAuthContext } from "src/auth/hooks/use-auth-context";
import { HOST_API } from "src/config-global";
import logger from "src/utils/logger";

export const WebSocketContext = createContext();

const RECONNECT_INTERVAL = 5000;
const MAX_RECONNECT_DELAY = 30000; // 30 seconds max delay
const HEARTBEAT_INTERVAL = 10000;

export const WebSocketProvider = ({ children }) => {
  const { user, authenticated } = useSafeAuthContext();
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState(null);
  const reconnectAttempts = useRef(0);
  const navigate = useNavigate();

  const socketRef = useRef(null);
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
      reconnectTimeoutRef.current ||
      socketRef.current?.readyState === WebSocket.OPEN ||
      socketRef.current?.readyState === WebSocket.CONNECTING
    ) {
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

  const createWebSocketConnection = useCallback(() => {
    if (!accessToken.current) {
      logger.debug("No access token found, skipping WebSocket connection.");
      return;
    }

    // Close any existing connection before creating a new one
    closeConnection();

    const isSecure = HOST_API.includes("https");
    const wsHost = HOST_API.replace(/^https?:\/\//, "").replace(/\/+$/, "");
    const protocol = isSecure ? "wss" : "ws";
    const wsUrl = `${protocol}://${wsHost}/ws/connect/?token=${accessToken.current}`;

    const socket = new WebSocket(wsUrl);
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
        url: ws?.url ? ws.url.replace(/token=[^&]+/, "token=***") : "unknown",
        protocol: ws?.protocol || "none",
        reconnectAttempts: reconnectAttempts.current,
      };

      // Create a proper Error object with meaningful context
      const wsError = new Error(
        `WebSocket connection failed (state: ${errorContext.readyState}, attempts: ${errorContext.reconnectAttempts})`,
      );
      wsError.name = "WebSocketError";

      logger.error("WebSocket error:", wsError, errorContext);
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
    navigate,
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

  // Consolidated authentication state handling
  useEffect(() => {
    if (authenticated && user?.accessToken) {
      accessToken.current = user?.accessToken;

      if (
        !socketRef.current ||
        socketRef.current.readyState !== WebSocket.OPEN
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
